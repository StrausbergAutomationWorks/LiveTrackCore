"""aisstream.io: ONE shared client per Home Assistant instance, for every SAW consumer (backlog 315, 2026-10-30).

Live Track Ships reads AREAS (every vessel inside its boxes); Live Track SALT reads a VESSEL LIST (the
EnforcementVessels MMSIs, nationwide). aisstream gives each connection ONE subscription, a new one REPLACES the
old, and an MMSI filter binds every box of it (measured 2026-10-30, 35_Dependencies/third_party/aisstream/SSOT.md
section 4). So the two needs cannot share a connection, and this client holds AT MOST TWO PER API KEY (Lee
2026-10-30 03:13):
  * AREA: the union of every area consumer's boxes, unfiltered;
  * VESSELS: the union of every list consumer's MMSIs (at most 200) and of their boxes.
Each observation goes to the consumers whose boxes hold it (and, for a list consumer, whose list names it),
re-labelled with that consumer's domain. A change of consumers REPLACES the subscription on the open socket,
paced REPLACE_PACE_S apart (a faster update closes the connection). The last consumer of a connection closes it.

CLAIMS (0.2.20, backlog 315 task 5): each consumer says which vessels it draws (claim) and another leaves them
alone (claimed_by_others), as on saw_livetrack.adsb_poll: Live Track Ships draws a listed vessel itself with the
list's look, so Live Track SALT skips it. One vessel, one marker.
! A VESSELS CONSUMER WITH NO MMSI IS REFUSED (0.2.20): an empty FiltersShipMMSI would be no filter at all.

! THE PARSER IS MOVED VERBATIM from Live Track Ships' sources/aisstream.py at ccebaee: its constants, _clean,
_parse_time and the methods of Parser. The connection keeps that file's heartbeat, backoff with jitter, binary
frames and refusal handling; what is new is the sharing around it. Static messages feed ONE StaticCache per
instance (self.statics), which Live Track Ships loads from and saves to disk.
! No Home Assistant import and no aiohttp import at module level: the session and the task factory come from the
consumer.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import random
import time
from dataclasses import replace
from typing import Any, Callable

from .ais import KNOTS_TO_KMH, Observation, StaticCache, rot_to_dps

_LOGGER = logging.getLogger(__name__)

STORE_KEY = "saw_aisstream"
AREA = "area"
VESSELS = "vessels"
MAX_MMSIS = 200             # FiltersShipMMSI, per subscription (docs, 2026-10-30)
REPLACE_PACE_S = 1.1        # updates at most 1/s per connection; a faster one closes it
WORLD = [[-90.0, -180.0], [90.0, 180.0]]

URL = "wss://stream.aisstream.io/v0/stream"

# ⚠ aiohttp compress=15 negotiates permessage-deflate. Verified two ways: the
# handshake returned permessage-deflate;server_no_context_takeover;
# client_no_context_takeover AND the server reported CompressionEnabled: true.
COMPRESS_WBITS = 15

SUBSCRIBE_WITHIN_S = 3
MIN_SECONDS_BETWEEN_SUBSCRIBES = 1.0

# ⚠ A SOCKET CAN DIE WITHOUT CLOSING. Measured 2026-10-20: the Madrid entry's
# connection stopped carrying data at 15:58:06Z and stayed ESTABLISHED in the
# kernel - 0 packets in 20 s by tcpdump - for 8.5 hours, while a fresh
# connection with the same key and box got 222 messages a minute. With
# heartbeat=None the read loop waited forever: no error, no reconnect, no log.
# aiohttp sends a ping after HEARTBEAT_S with nothing received and drops the
# socket if no pong comes within half of it, so a dead socket is noticed in
# at most 45 s. aisstream answers pings (measured the same night: pong in
# 0.28-0.29 s on a box over dry land, so no data frame was masking it).
# ⚠ Do NOT replace this with an idle-data timeout: quiet water legitimately
# sends nothing for minutes. SSOT.md item 23.
HEARTBEAT_S = 30.0

# ⚠ Reconnect with backoff AND JITTER. Without jitter, every instance that
# lost the same upstream returns in lockstep.
BACKOFF_START_S = 2.0
BACKOFF_MAX_S = 300.0

POSITION_TYPES = frozenset({
    "PositionReport", "StandardClassBPositionReport",
    "ExtendedClassBPositionReport", "LongRangeAisBroadcastMessage",
})
STATIC_TYPES = frozenset({"ShipStaticData", "StaticDataReport"})

# ⚠ AIS encodes "unavailable" IN BAND. An unmapped sentinel is
# indistinguishable from a measurement downstream. B3 in 05_SHARED_LESSONS.md.
COG_UNAVAILABLE = 360.0
HEADING_UNAVAILABLE = 511
SOG_UNAVAILABLE = 102.3
# ⚠ Rate of turn is decoded by `rot_to_dps` in base.py and has NO sentinel
# constant here, deliberately. Four private copies of that list held three
# different values - SSOT.md items 18 and 20. Do not add one back.
NAV_STATUS_UNDEFINED = 15
SHIP_TYPE_UNAVAILABLE = 0

# ⚠ A record longer than any ship ever built is a bad record, not a big ship.
MAX_PLAUSIBLE_LENGTH_M = 500.0

# Static data is broadcast every ~6 minutes and arrives for a small minority of
# vessels at any moment - 13 of 114 in three minutes. Cache it generously.
# ⛔ STATIC_CACHE_MAX (5000, then a wholesale clear) is GONE since 2026-09-26:
# the join table is statics.StaticCache, kept across restarts, shared by every
# aisstream entry and evicted by last heard. SSOT item 262.


def _clean(value, unavailable):
    if value is None:
        return None
    if isinstance(unavailable, tuple):
        return None if value in unavailable else value
    return None if value == unavailable else value


def _parse_time(raw):
    """⚠ time_utc is a Go timestamp with NANOSECOND precision, e.g.
    '2026-09-02 22:05:43.933000000 +0000 UTC'. Python's %f accepts at most SIX
    fractional digits, so strptime FAILS on it unmodified. Truncate first.
    This cost a broken analysis script before it was caught."""
    if not raw:
        return None
    try:
        s = raw.split(" +")[0].strip()
        if "." in s:
            head, frac = s.split(".", 1)
            s = f"{head}.{frac[:6]}"
        return _dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f").replace(
            tzinfo=_dt.timezone.utc)
    except (ValueError, AttributeError):
        return None


class Parser:
    """aisstream message -> Observation, or None. Static messages go into the shared StaticCache."""

    def __init__(self, statics: StaticCache | None = None, domain: str = "aisstream"):
        self._static: StaticCache = statics if statics is not None else StaticCache()
        self.domain = domain

    # -- normalisation -----------------------------------------------------
    @staticmethod
    def _meta(obj):
        """⚠ THE PUBLISHED DOCS ARE WRONG ABOUT CASING. The example shows
        Latitude/Longitude in MetaData; the live API sends `latitude`,
        `longitude`, `time_utc` LOWERCASE beside `MMSI` and `ShipName`
        UPPERCASE. Inside `Message` it is Latitude/Longitude capitalised.
        Mixed casing in one payload."""
        return obj.get("MetaData") or {}

    def _remember_static(self, mt, body, mmsi):
        """⚠ TWO different static messages, and Class B splits into parts.
        Class A sends ShipStaticData (Msg 5) every ~6 min and it is reliable.
        Class B sends StaticDataReport (Msg 24) where Part A is the NAME and
        Part B is the TYPE - and units transmit Part A far more reliably.
        Measured: 42% of Class B sent static, only 1 in 5 of those carried a
        usable type. Type-driven features must degrade to unknown."""
        # ⛔ This used to clear the whole dict past 5,000 MMSIs, and every
        # learned type with it. The cache evicts by last heard (item 262).
        if mt == "ShipStaticData":
            fields = {
                "name": (body.get("Name") or "").strip() or None,
                "type": _clean(body.get("Type"), SHIP_TYPE_UNAVAILABLE),
                "call_sign": (body.get("CallSign") or "").strip() or None,
                "destination": (body.get("Destination") or "").strip() or None,
                "imo": body.get("ImoNumber") or None,
                "draught_m": body.get("MaximumStaticDraught") or None,
            }
            fields.update(self._dimensions(body.get("Dimension") or {}))
            self._static.update(mmsi, fields)
        elif mt == "StaticDataReport":
            ra = body.get("ReportA") or {}
            if ra.get("Valid"):
                self._static.set_name(mmsi, (ra.get("Name") or "").strip())
            rb = body.get("ReportB") or {}
            # ⚠ ReportB.Valid is false with all-zero dimensions on real
            # traffic. PRESENCE OF A MESSAGE IS NOT PRESENCE OF ITS PAYLOAD.
            if rb.get("Valid"):
                fields = {}
                t = _clean(rb.get("ShipType", rb.get("Type")),
                           SHIP_TYPE_UNAVAILABLE)
                if t is not None:
                    fields["type"] = t
                fields.update(self._dimensions(rb.get("Dimension") or {}))
                cs = (rb.get("CallSign") or "").strip()
                if cs:
                    fields["call_sign"] = cs
                self._static.update(mmsi, fields)

    @staticmethod
    def _dimensions(dim):
        a = dim.get("A") or 0
        b = dim.get("B") or 0
        c = dim.get("C") or 0
        d = dim.get("D") or 0
        length = float(a + b) if (a + b) else None
        beam = float(c + d) if (c + d) else None
        # ⚠ A+B is the length of the whole UNIT. SEA EAGLE II reports type 52
        # (tug) at 164 m - an articulated tug-barge broadcasting the pair, not
        # a 164 m tug. Deriving a size class from this misclassifies ATBs.
        if length and length > MAX_PLAUSIBLE_LENGTH_M:
            return {}
        return {"length_m": length, "beam_m": beam}

    def _to_observation(self, obj):
        mt = obj.get("MessageType")
        meta = self._meta(obj)
        mmsi = meta.get("MMSI")
        if mmsi is None:
            return None
        # ⚠ MMSI_String is a JSON NUMBER, not a string. The field name lies.
        mmsi = str(mmsi)
        body = (obj.get("Message") or {}).get(mt) or {}

        if mt in STATIC_TYPES:
            self._remember_static(mt, body, mmsi)
            return None
        if mt not in POSITION_TYPES:
            # ⚠ Anything else is IGNORED, never fatal. AtoN and base stations
            # have never arrived from this source, but if they start they must
            # not be treated as vessels - a buoy is a fixed object.
            return None

        lat, lon = meta.get("latitude"), meta.get("longitude")
        if lat is None or lon is None:
            return None

        sog = _clean(body.get("Sog"), SOG_UNAVAILABLE)
        # ⚠ ShipName is space-padded INCONSISTENTLY - 'SEADOG IV           '
        # padded to 20, plain 'ODYSSEY' at length 7. And operators MANGLE names
        # to fit: 'ODYSSEY CHICAGO_IVER' is real data with a literal 0x5F, not
        # corruption. Strip it; NEVER try to repair it.
        name = (meta.get("ShipName") or "").strip() or None
        # ⚠ The name goes in so a record read back from disk under a
        # reassigned MMSI is refused (statics.StaticCache.lookup).
        rec = self._static.lookup(mmsi, name)
        static = ({k: getattr(rec, k) for k in rec.__slots__}
                  if rec is not None else {})

        return Observation(
            identity=mmsi,
            source=self.domain,
            observed_at=_parse_time(meta.get("time_utc"))
            or _dt.datetime.now(_dt.timezone.utc),
            latitude=lat,
            longitude=lon,
            course_deg=_clean(body.get("Cog"), COG_UNAVAILABLE),
            heading_deg=_clean(body.get("TrueHeading"), HEADING_UNAVAILABLE),
            speed_kmh=None if sog is None else sog * KNOTS_TO_KMH,
            rate_of_turn_dps=rot_to_dps(body.get("RateOfTurn")),
            name=name,
            ship_type=static.get("type"),
            call_sign=static.get("call_sign"),
            # ⚠ Destination is UNCONSTRAINED FREE TEXT - 'USCHI><USCHI',
            # 'TWO HARBORS CH 74', 'TORONTO@ON'. Operator-typed, no code table.
            # DO NOT build a resolver; the airport pipeline has nothing to bite
            # on here.
            destination=static.get("destination"),
            imo=static.get("imo"),
            length_m=static.get("length_m"),
            beam_m=static.get("beam_m"),
            draught_m=static.get("draught_m"),
            nav_status=_clean(body.get("NavigationalStatus"),
                              NAV_STATUS_UNDEFINED),
            position_source="observed",
            # ⚠ max_extrapolation_s IS DELIBERATELY ABSENT. No field, no
            # motion. D3a in 05_SHARED_LESSONS.md.
            max_extrapolation_s=None,
            raw_message_type=mt,
        )


def _norm_boxes(boxes) -> list:
    out = []
    for b in boxes or []:
        (a, c), (d, e) = b
        out.append([[float(a), float(c)], [float(d), float(e)]])
    return out


def _inside(boxes, lat, lon) -> bool:
    for (a, b), (c, d) in boxes:
        if min(a, c) <= lat <= max(a, c) and min(b, d) <= lon <= max(b, d):
            return True
    return False


def _norm_mmsis(mmsis) -> frozenset:
    out = set()
    for m in mmsis or ():
        s = str(m).strip()
        if len(s) == 9 and s.isdigit():
            out.add(s)
    return frozenset(out)


def _refuse_empty(consumer, mmsis) -> None:
    """! NEVER AN EMPTY FILTER (0.2.20): aisstream would read an empty FiltersShipMMSI as no filter at all, the whole
    world unfiltered (inferred, not measured). A list consumer with nothing to ask for removes itself instead."""
    if not mmsis:
        raise ValueError(f"aisstream consumer {consumer}: a VESSELS subscription needs at least one MMSI")


class _Consumer:
    def __init__(self, name, kind, key, boxes, mmsis, deliver, domain):
        self.name, self.kind, self.key = name, kind, key
        self.boxes, self.mmsis = boxes, mmsis
        self.deliver, self.domain = deliver, domain
        self.delivered = 0

    def wants(self, obs: Observation) -> bool:
        if obs.latitude is None or obs.longitude is None:
            return False
        if self.kind == VESSELS and obs.identity not in self.mmsis:
            return False
        return _inside(self.boxes, obs.latitude, obs.longitude)


class _Conn:
    """One websocket for one (API key, kind). Reconnects with backoff and jitter; replaces its subscription when the
    consumers change."""

    def __init__(self, client: "Client", key: str, kind: str, session):
        self.client, self.key, self.kind, self._session = client, key, kind, session
        self.parser = Parser(client.statics)
        self._closing = False
        self._changed = asyncio.Event()
        self._last_sub: float | None = None
        self.task = None
        self.compression_enabled: bool | None = None
        self.subscriptions_sent = 0
        self.connects = 0
        self.messages = 0
        self.status = "starting"

    def changed(self) -> None:
        self._changed.set()

    async def _pace(self) -> None:
        if self._last_sub is not None:
            wait = self._last_sub + REPLACE_PACE_S - self.client._clock()
            if wait > 0:
                await self.client._sleep(wait)

    async def _send_subscription(self, ws) -> bool:
        payload = self.client._payload(self.key, self.kind)
        if payload is None:
            return False
        await ws.send_str(json.dumps(payload))
        self._last_sub = self.client._clock()
        self.subscriptions_sent += 1
        return True

    async def _resubscriber(self, ws) -> None:
        """Replaces the subscription when the consumers change. A send that fails (the socket is going) ends it
        quietly: the reader sees the close and the next connection subscribes afresh."""
        try:
            while True:
                await self._changed.wait()
                await self._pace()
                self._changed.clear()
                await self._send_subscription(ws)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                   # noqa: BLE001
            _LOGGER.debug("aisstream subscription not replaced: %s", exc)

    async def run(self, aiohttp=None) -> None:
        """Run until stopped, reconnecting with backoff and jitter."""
        if aiohttp is None:
            import aiohttp
        backoff = BACKOFF_START_S
        while not self._closing:
            try:
                async for obs in self._one_connection(aiohttp):
                    backoff = BACKOFF_START_S      # reset only on real data
                    self.client._dispatch(self, obs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:               # noqa: BLE001
                _LOGGER.warning("aisstream connection failed: %s", exc)
            if self._closing:
                return
            # ! JITTER. Without it every instance that lost the same upstream
            # returns in lockstep and the service sees a thundering herd.
            wait = min(BACKOFF_MAX_S, backoff) * (0.5 + random.random())
            self.status = "reconnecting"
            await self.client._sleep(wait)
            backoff = min(BACKOFF_MAX_S, backoff * 2)

    async def _one_connection(self, aiohttp):
        self.connects += 1
        self._changed.clear()
        self._last_sub = None
        async with self._session.ws_connect(
                URL, compress=COMPRESS_WBITS, max_msg_size=0,
                heartbeat=HEARTBEAT_S) as ws:
            # ! WITHIN 3 SECONDS or the socket closes. Send it first.
            if not await self._send_subscription(ws):
                return
            self.status = "connected"
            resub = asyncio.ensure_future(self._resubscriber(ws))
            try:
                async for msg in ws:
                    # ! BINARY, ALWAYS (Ships: 133 of 133 frames, zero TEXT).
                    if msg.type is aiohttp.WSMsgType.BINARY:
                        payload = msg.data.decode("utf-8", errors="replace")
                    elif msg.type is aiohttp.WSMsgType.TEXT:
                        payload = msg.data          # tolerated, never observed
                    elif msg.type is aiohttp.WSMsgType.ERROR:
                        # ! A missed pong arrives HERE. WARNING: a socket that
                        # died silently must leave a line in a default log.
                        if not self._closing:
                            _LOGGER.warning(
                                "aisstream connection lost (%s); reconnecting",
                                ws.exception() or msg.data)
                        return
                    elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                      aiohttp.WSMsgType.CLOSING):
                        _LOGGER.info("aisstream closed the connection: %s",
                                     ws.close_code)
                        return
                    else:
                        continue
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    # ! A refusal (bad key, too many connections) is a frame with
                    # "error" and NO MessageType; silent, it looks like empty water.
                    if "error" in obj and "MessageType" not in obj:
                        text = str(obj.get("error"))[:200]
                        if self.key:
                            text = text.replace(self.key, "<api key>")
                        _LOGGER.warning("aisstream refused the subscription: %s",
                                        text)
                        self.status = "refused"
                        continue
                    if obj.get("MessageType") == "SubscriptionConfirmation":
                        inner = obj.get("Message") or {}
                        self.compression_enabled = inner.get("CompressionEnabled")
                        self.status = "subscribed"
                        continue
                    self.messages += 1
                    obs = self.parser._to_observation(obj)
                    if obs is not None:
                        yield obs
            finally:
                resub.cancel()

    async def stop(self) -> None:
        self._closing = True
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except BaseException:                  # noqa: BLE001
                pass


class Client:
    """The instance's one aisstream client."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, sleep=asyncio.sleep):
        self._clock = clock
        self._sleep = sleep
        self._aiohttp = None        # the aiohttp module, imported on first connect; tests pass a stand-in
        self.statics = StaticCache()
        self._consumers: dict[str, _Consumer] = {}
        self._conns: dict[tuple, _Conn] = {}
        self._claims: dict[str, frozenset] = {}     # consumer -> MMSIs it draws (0.2.20)

    # --- consumers ------------------------------------------------------------
    def add_area(self, consumer: str, session, api_key: str, boxes, deliver: Callable[[Observation], Any],
                 domain: str, create_task=None) -> None:
        """Every vessel inside boxes ([[lat, lon], [lat, lon]] pairs), unfiltered."""
        self._add(_Consumer(consumer, AREA, api_key, _norm_boxes(boxes), frozenset(), deliver, domain),
                  session, create_task)

    def add_vessels(self, consumer: str, session, api_key: str, mmsis, deliver: Callable[[Observation], Any],
                    domain: str, boxes=None, create_task=None) -> None:
        """Only these MMSIs, inside boxes (the whole world when none are given). A list with no valid MMSI is
        refused (ValueError): an empty filter would be none."""
        self._add(_Consumer(consumer, VESSELS, api_key, _norm_boxes(boxes or [WORLD]), _norm_mmsis(mmsis),
                            deliver, domain), session, create_task)

    def update(self, consumer: str, boxes=None, mmsis=None) -> None:
        """New boxes or a new list for a consumer: its connection replaces its subscription."""
        c = self._consumers[consumer]
        new_boxes = _norm_boxes(boxes) if boxes is not None else c.boxes
        new_mmsis = _norm_mmsis(mmsis) if mmsis is not None else c.mmsis
        if c.kind == VESSELS:
            _refuse_empty(consumer, new_mmsis)
            self._check_cap(c.key, consumer, new_mmsis)
        c.boxes, c.mmsis = new_boxes, new_mmsis
        conn = self._conns.get((c.key, c.kind))
        if conn is not None:
            conn.changed()

    async def remove(self, consumer: str) -> None:
        """A consumer unloading. The last one on a connection closes it. Its claims go too."""
        self._claims.pop(consumer, None)
        c = self._consumers.pop(consumer, None)
        if c is None:
            return
        conn = self._conns.get((c.key, c.kind))
        if conn is None:
            return
        if any(o.key == c.key and o.kind == c.kind for o in self._consumers.values()):
            conn.changed()
            return
        del self._conns[(c.key, c.kind)]
        await conn.stop()

    # --- claims (0.2.20) ------------------------------------------------------
    def claim(self, consumer: str, mmsis) -> None:
        """What this consumer draws now; another consumer skips these. An empty set claims nothing."""
        self._claims[consumer] = _norm_mmsis(mmsis)

    def claimed_by_others(self, consumer: str) -> frozenset:
        out: set = set()
        for who, m in self._claims.items():
            if who != consumer:
                out |= m
        return frozenset(out)

    def release(self, consumer: str) -> None:
        """A consumer's claims go (remove() does this too)."""
        self._claims.pop(consumer, None)

    def _check_cap(self, key, consumer, mmsis) -> None:
        union = set(mmsis)
        for o in self._consumers.values():
            if o.key == key and o.kind == VESSELS and o.name != consumer:
                union |= o.mmsis
        if len(union) > MAX_MMSIS:
            raise ValueError(f"aisstream takes at most {MAX_MMSIS} MMSIs per subscription; "
                             f"this would make {len(union)}")

    def _add(self, c: _Consumer, session, create_task) -> None:
        if c.name in self._consumers:
            raise ValueError(f"consumer {c.name} already added")
        if c.kind == VESSELS:
            _refuse_empty(c.name, c.mmsis)
            self._check_cap(c.key, c.name, c.mmsis)
        self._consumers[c.name] = c
        conn = self._conns.get((c.key, c.kind))
        if conn is not None:
            conn.changed()
            return
        conn = self._conns[(c.key, c.kind)] = _Conn(self, c.key, c.kind, session)
        coro = conn.run(self._aiohttp)
        name = f"saw_aisstream_{c.kind}"
        conn.task = create_task(coro, name) if create_task else asyncio.get_running_loop().create_task(coro, name=name)

    # --- what goes on the wire ------------------------------------------------
    def _payload(self, key: str, kind: str) -> dict | None:
        mine = [c for c in self._consumers.values() if c.key == key and c.kind == kind]
        if not mine:
            return None
        boxes: list = []
        for c in mine:
            for b in c.boxes:
                if b not in boxes:
                    boxes.append(b)
        sub: dict = {"APIKey": key, "BoundingBoxes": boxes}
        if kind == VESSELS:
            sub["FiltersShipMMSI"] = sorted(set().union(*(c.mmsis for c in mine)))
        return sub

    def _dispatch(self, conn: _Conn, obs: Observation) -> None:
        for c in list(self._consumers.values()):
            if c.key != conn.key or c.kind != conn.kind or not c.wants(obs):
                continue
            o = obs if obs.source == c.domain else replace(obs, source=c.domain)
            try:
                c.deliver(o)
                c.delivered += 1
            except Exception:                      # noqa: BLE001
                _LOGGER.exception("aisstream consumer %s failed", c.name)

    def status(self) -> dict:
        """Low-cardinality state for a sensor attribute. Never the key."""
        return {"connections": [{"kind": k[1], "status": c.status, "connects": c.connects,
                                  "subscriptions_sent": c.subscriptions_sent} for k, c in self._conns.items()],
                "consumers": sorted(self._consumers), "statics": len(self.statics),
                "claims": {k: len(v) for k, v in sorted(self._claims.items())}}


def shared(store: dict) -> Client:
    """The instance's one client, made on first use. store is the consumer's hass.data."""
    client = store.get(STORE_KEY)
    if client is None:
        client = store[STORE_KEY] = Client()
    return client
