"""Shared Renfe client (Spain) for the Live Track family.

Hoisted at birth, 2026-10-19 (Lee: "keep in mind this will be elevated and read by
other integrations if the feed also contains other categories"). Pure Python: NO
homeassistant imports (05_SHARED_LESSONS.md B8.1a). One poller per Home Assistant
through the shared-coordinator pattern (reference/shared_renfe.py, B16).

Data: Renfe (data.renfe.com), CC BY 4.0. Keyless. Three files:
    vehicle_positions.pb   GTFS-Realtime positions, every network, ~20 s
    trip_updates.pb        GTFS-Realtime: the next stop, its time and the delay
    fomento_transit.zip    the GTFS timetable, every network, ~16 MB

Every rule here was MEASURED on capture round 1 (Fri 2026-10-16 23:50 to Mon 10-19
08:43 Spain time; 6,090 polls, 1,402,064 rows, 7,710 trips):
30_Integrations/Live_Track_Commuter_Rail/20_Research/ES_RENFE_CERCANIAS_FEED_20261016.md.

THE FEED CARRIES MORE THAN COMMUTER TRAINS, so each vehicle goes to ONE consumer
(06_MAP_CONTRACT.md D1: one owner per vehicle):

    commuter    rail; every line except REGIONAL_LINES     Live Track Commuter Rail
    intercity   rail; Catalonia's regional lines             Live Track Intercity Rail
                (Rodalies de Catalunya's "Regionals"
                R11-R17), as V/Line's regional trains
    bus         route_type 3 in Renfe's own timetable:       a bus integration
                rail-replacement and feeder buses
                (329 of 7,710 trips, 4.3%: Murcia C2
                Lorca - Aguilas, San Sebastian C1 Irun -
                Hernani, Santander C2, Valencia C2/C3/C5)

A trip the timetable does not know (Barcelona's `SPECIAL_50_...` trips) is taken as
rail and routed by its line. A route_type other than 2 or 3 goes to nobody.

THE TRIP ID IS THE IDENTITY. It reads NNDDLtttttLINE: network code (10 Madrid ... 51
Barcelona, NETWORKS), a day number and weekday letter, the train number, the line
(3087V23743C5: Seville, train 23743, line C5). entity.id is NOT an identity: built as
VP_<line>-<number>, the same id carried trains in different networks (VP_C1-26912 was
San Sebastian's 6188S35912C1 and Santander's 6288S26912C1); 3,086 of 4,182 entity ids
carried more than one trip, and one appeared twice in a body 890 times.

FEED QUIRKS handled here once, for every consumer:
* A trip twice in one body: kept once, the newer fix.
* 0,0: 1,554 rows, interleaved with real positions. Dropped.
* A RETURN: positions are modelled onto the timetable's shape (p50, p90 and p99 all
  0 m from it), stand still (p50 79 s) and jump (p50 785 m). 7.6% of moves of 200 m
  or more go back to where the train was two positions earlier (A-B-A; Madrid
  12.9%). Such a return is held at the last position, until it repeats RETURN_ACCEPT
  times in a row (then it is where the train is).
* No bearing and no speed on any row. vehicle.timestamp is the batch time (0-8 s
  before the header), not a GPS time.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import math
import re
import threading
import time
import urllib.error
import urllib.request
import zipfile

CONTRACT_VERSION = 1
SHARED_KEY = "saw_renfe_v%d" % CONTRACT_VERSION

POSITIONS_URL = "https://gtfsrt.renfe.com/vehicle_positions.pb"
TRIP_UPDATES_URL = "https://gtfsrt.renfe.com/trip_updates.pb"
STATIC_URL = "https://ssl.renfe.com/ftransit/Fichero_CER_FOMENTO/fomento_transit.zip"
TERMS_URL = "https://data.renfe.com/"
DEFAULT_USER = "StrausbergAutomationWorks/saw-livetrack"
ATTRIBUTION = "Data: Renfe (data.renfe.com), CC BY 4.0"
TIME_ZONE = "Europe/Madrid"

COMMUTER = "commuter"
INTERCITY = "intercity"
BUS = "bus"
CONSUMERS = (COMMUTER, INTERCITY, BUS)

# Network code (the trip id's first two digits, the same code that opens every
# route_id in the timetable: 133,481 of 133,481 trips) -> name.
NETWORKS = {
    "10": "Madrid", "20": "Asturias", "30": "Seville", "31": "Cadiz", "32": "Malaga",
    "40": "Valencia", "41": "Murcia/Alicante", "45": "Cartagena", "46": "Ferrol",
    "47": "Leon", "51": "Barcelona (Rodalies)", "60": "Bilbao", "61": "San Sebastian",
    "62": "Santander", "70": "Zaragoza", "90": "Cercedilla-Cotos",
}
# Rodalies de Catalunya's regional lines. Seen live: R11, R13, R14, R15, R16, R17
# (Figueres, Lleida, Tortosa, Caspe, Salou ... to Barcelona). R12 included: the same
# family, not seen in the capture.
REGIONAL_NETWORKS = frozenset({"51"})
REGIONAL_LINES = frozenset({"R11", "R12", "R13", "R14", "R15", "R16", "R17"})

FEED_TTL_S = 20.0
STATIC_TTL_S = 24 * 3600.0
STATIC_RETRY_S = 3600.0
STATIC_MAX_BYTES = 64 * 1024 * 1024
# A return is a move back to within RETURN_M of the position two before, after a
# move of at least RETURN_MIN_M; held until it repeats RETURN_ACCEPT times in a row.
RETURN_M = 1.0
RETURN_MIN_M = 200.0
RETURN_ACCEPT = 3
# A trip not seen for this long is forgotten (its return history).
FORGET_S = 1800.0

_TRIP_RE = re.compile(r"^(\d\d)\d\d[A-Z](\d+)([A-Za-z][A-Za-z0-9]*)$")
_SPECIAL_RE = re.compile(r"^SPECIAL_\d+_(\d+)([A-Za-z][A-Za-z0-9]*)$")


class RenfeError(Exception):
    """Base error for this client."""


class RateLimited(RenfeError):
    """HTTP 429. Back off; NOT 'no trains'."""


class FeedUnavailable(RenfeError):
    """Transport, HTTP or decode failure. Distinct from an empty answer (C2)."""


def _check_consumer(consumer):
    if consumer not in CONSUMERS:
        raise ValueError("unknown consumer %r" % (consumer,))


def parse_trip_id(trip_id):
    """(network code or None, train number or None, line or None).

    '3087V23743C5' -> ('30', '23743', 'C5'); 'SPECIAL_50_95224R4' -> (None, '95224',
    'R4'); anything else -> (None, None, None)."""
    t = (trip_id or "").strip() if isinstance(trip_id, str) else ""
    m = _TRIP_RE.match(t)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = _SPECIAL_RE.match(t)
    if m:
        return None, m.group(1), m.group(2)
    return None, None, None


def consumer_of(route_type, network, line):
    """Who draws a vehicle: COMMUTER, INTERCITY, BUS, or None.

    route_type from Renfe's timetable for the trip ("2" rail, "3" bus), or None when
    the trip is not in it (taken as rail)."""
    if route_type == "3":
        return BUS
    if route_type not in (None, "2"):
        return None
    if line in REGIONAL_LINES and network in REGIONAL_NETWORKS | {None}:
        return INTERCITY
    return COMMUTER


def _metres(a, b):
    k = 111195.0
    return math.hypot((b[0] - a[0]) * k, (b[1] - a[1]) * k * math.cos(math.radians(a[0])))


# --------------------------------------------------------------------------
# The timetable: which trips are buses
# --------------------------------------------------------------------------

def _rows(z, name):
    if name not in z.namelist():
        return
    with z.open(name) as fh:
        for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")):
            # Renfe pads every field with spaces (05_SHARED_LESSONS.md B5).
            yield {(k or "").strip(): (v or "").strip() for k, v in r.items() if k}


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _today(tz_name):
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo(tz_name)).date()
    except Exception:  # noqa: BLE001 - no zone database (install the gtfs extra)
        return _dt.datetime.now(_dt.timezone.utc).date()


class TripIndex:
    """{trip_id: route_type} for the trips running the day before, on, and after
    `today` (Spain's date). Built from the timetable zip; about 12,000 trips."""

    def __init__(self, route_types, day=None):
        self._types = dict(route_types)
        self.day = day

    def __len__(self):
        return len(self._types)

    def route_type(self, trip_id):
        return self._types.get((trip_id or "").strip())

    @classmethod
    def from_zip(cls, raw, today=None):
        z = zipfile.ZipFile(io.BytesIO(raw))
        if today is None:
            agency = next(_rows(z, "agency.txt"), None) or {}
            today = _today(agency.get("agency_timezone") or TIME_ZONE)
        days = [today + _dt.timedelta(days=d) for d in (-1, 0, 1)]
        cal = list(_rows(z, "calendar.txt"))
        dates = list(_rows(z, "calendar_dates.txt"))
        on = set()
        for day in days:
            wd, ymd = _WEEKDAYS[day.weekday()], day.strftime("%Y%m%d")
            on |= {r["service_id"] for r in cal if r.get("service_id") and r.get(wd) == "1"
                   and r.get("start_date", "") <= ymd <= r.get("end_date", "")}
            for r in dates:
                if r.get("date") == ymd and r.get("service_id"):
                    if r.get("exception_type") == "1":
                        on.add(r["service_id"])
                    elif r.get("exception_type") == "2":
                        on.discard(r["service_id"])
        rtype = {r["route_id"]: r.get("route_type", "") for r in _rows(z, "routes.txt")
                 if r.get("route_id")}
        out = {}
        for r in _rows(z, "trips.txt"):
            tid = r.get("trip_id")
            if tid and (not (cal or dates) or r.get("service_id") in on):
                out[tid] = rtype.get(r.get("route_id", ""), "")
        return cls(out, today)


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------

def _get(url, user, timeout, opener=None, max_bytes=None):
    req = urllib.request.Request(url, headers={"User-Agent": user})
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(req, timeout=timeout) as response:
            raw = response.read() if max_bytes is None else response.read(max_bytes + 1)
    except urllib.error.HTTPError as err:
        if err.code == 429:
            raise RateLimited("HTTP 429 from %s" % url) from err
        raise FeedUnavailable("HTTP %s from %s" % (err.code, url)) from err
    except Exception as err:  # noqa: BLE001 - transport of any kind
        raise FeedUnavailable("%s from %s" % (type(err).__name__, url)) from err
    if max_bytes is not None and len(raw) > max_bytes:
        raise FeedUnavailable("more than %d bytes from %s" % (max_bytes, url))
    return raw


def _decode(raw, url):
    from .gtfs.realtime import decode
    feed = decode(raw)
    if feed is None:
        raise FeedUnavailable("undecodable body from %s" % url)
    return feed


def _new_feed(src):
    """An empty FeedMessage carrying `src`'s header."""
    out = type(src)()
    out.header.CopyFrom(src.header)
    return out


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------

class RenfeClient:
    """Polls Renfe ONCE for every consumer (B16: budget the IP).

    `consumers` are the consumers this client serves (default: all three, for a
    standalone client); the shared registration (reference/shared_renfe.py) starts
    with the first integration's and register()s each that joins. Only registered
    consumers get feeds built; for_consumer() refuses one that is not.

    One lock covers the positions fetch and the state, so consumers on different
    executor threads make one set of requests between them. register() and
    unregister() take only a small state lock: they run on Home Assistant's event
    loop.

    for_consumer(name) -> (positions, trip_updates): two GTFS-Realtime FeedMessages
    holding only that consumer's vehicles and trips, positions cleaned (module
    docstring). trip_updates is None when that file failed and the positions did not.

    THE TIMETABLE tells the buses from the trains. MEASURED 2026-10-19
    (bin\\es_renfe_index_time.txt): the 16 MB zip takes 36 s to download, 1 s to
    read. With background=True (the default) it loads on its own thread, first at
    the first snapshot and then daily, and the feeds wait for nothing: until the
    FIRST attempt ends every consumer gets an empty feed (a bus is never drawn as a
    train); if that attempt fails, every trip is taken as rail until a later one
    succeeds (STATIC_RETRY_S). The zip is kept (static_zip()) so a consumer that
    distills it does not download it again.
    """

    def __init__(self, user=DEFAULT_USER, timeout=30, opener=None, clock=None,
                 feed_ttl_s=FEED_TTL_S, consumers=CONSUMERS, static_timeout=180,
                 today=None, background=True):
        if not user or not str(user).strip():
            raise ValueError("a non-empty User-Agent is required")
        self.user = user
        self.timeout = timeout
        self.static_timeout = static_timeout
        self._opener = opener
        self._clock = clock or time.time
        self.feed_ttl_s = float(feed_ttl_s)
        self._today = today          # tests: the timetable's date
        self._background = background
        for c in consumers:
            _check_consumer(c)
        self._lock = threading.RLock()
        self._state = threading.Lock()
        self._wanted = set(consumers)
        self.index = None
        self._raw = None
        self._index_at = None
        self._index_retry = 0.0
        self._index_tried = False    # the first attempt has ended
        self._loading = False
        self._fetched_at = None
        self._feeds = None
        self._history = {}           # trip -> [older, last, returns in a row, seen at]
        self.fetch_count = 0
        self.held_returns = 0
        self.dropped_null = 0
        self.dropped_duplicate = 0

    @property
    def consumers(self):
        with self._state:
            return frozenset(self._wanted)

    def register(self, consumer):
        """Serve `consumer` from now on. True if it is new. Never waits for a fetch."""
        _check_consumer(consumer)
        with self._state:
            if consumer in self._wanted:
                return False
            self._wanted.add(consumer)
            # Its feeds are built at its first for_consumer() (the snapshot fallback).
            return True

    def unregister(self, consumer):
        """Stop serving `consumer`. True if it was registered. Never waits."""
        _check_consumer(consumer)
        with self._state:
            if consumer not in self._wanted:
                return False
            self._wanted.discard(consumer)
            return True

    # --- the timetable ------------------------------------------------------

    def _index_due(self, now):
        # A load in progress set _index_retry an hour ahead: not due again meanwhile.
        fresh = self._index_at is not None and now - self._index_at < STATIC_TTL_S
        return not fresh and now >= self._index_retry

    def refresh_index(self, force=False):
        """Load or refresh the trip index on THIS thread; never raises. True when it
        changed. The download is made outside the feed lock."""
        with self._state:
            now = self._clock()
            if not force and not self._index_due(now):
                return False
            if self._loading:
                return False
            self._loading = True
            self._index_retry = now + STATIC_RETRY_S
        ok = False
        try:
            self.fetch_count += 1
            raw = _get(STATIC_URL, self.user, self.static_timeout, self._opener,
                       max_bytes=STATIC_MAX_BYTES)
            index = TripIndex.from_zip(raw, today=self._today)
            with self._state:
                self.index, self._raw, self._index_at = index, raw, now
            ok = True
        except Exception:  # noqa: BLE001 - keep the last index; rail assumed without one
            ok = False
        finally:
            with self._state:
                self._loading = False
                self._index_tried = True
        return ok

    def _ensure_index(self):
        """Start a load when one is due: on a thread, or here when background=False."""
        with self._state:
            due = self._index_due(self._clock())
        if not due:
            return
        if not self._background:
            self.refresh_index()
            return
        threading.Thread(target=self.refresh_index, name="saw_renfe_timetable",
                         daemon=True).start()

    def static_zip(self):
        """The timetable zip the index was built from (bytes), or None before one has
        loaded. A consumer distilling Renfe's timetable reads it here instead of
        downloading it again."""
        with self._state:
            return self._raw

    # --- the feed -----------------------------------------------------------

    def _clean(self, feed, now):
        """The positions entities to publish: 0,0 dropped, one per trip (the newer
        fix), returns held. Returns [(entity, trip_id, held position or None)]."""
        best = {}
        for ent in feed.entity:
            if not ent.HasField("vehicle"):
                continue
            v = ent.vehicle
            if not v.HasField("trip") or not v.trip.trip_id.strip():
                continue
            if not v.HasField("position"):
                continue
            lat, lon = round(float(v.position.latitude), 6), round(float(v.position.longitude), 6)
            if lat == 0.0 and lon == 0.0:
                self.dropped_null += 1
                continue
            tid = v.trip.trip_id.strip()
            ts = int(v.timestamp) if v.HasField("timestamp") else 0
            prev = best.get(tid)
            if prev is not None:
                self.dropped_duplicate += 1
                if ts <= prev[1]:
                    continue
            best[tid] = (ent, ts, (lat, lon))
        out = []
        for tid, (ent, _, pos) in best.items():
            out.append((ent, tid, self._hold(tid, pos, now)))
        for tid in [t for t, h in self._history.items() if now - h[3] > FORGET_S]:
            del self._history[tid]
        return out

    def _hold(self, tid, pos, now):
        """The position to publish instead of `pos` when it is a return, else None."""
        h = self._history.get(tid)
        if h is None:
            self._history[tid] = [None, pos, 0, now]
            return None
        older, last = h[0], h[1]
        h[3] = now
        if _metres(last, pos) < RETURN_M:
            h[2] = 0
            return None
        if (older is not None and _metres(older, pos) < RETURN_M
                and _metres(older, last) >= RETURN_MIN_M):
            h[2] += 1
            if h[2] < RETURN_ACCEPT:
                self.held_returns += 1
                return last
            # It stays there: that is where the train is.
        h[0], h[1], h[2] = last, pos, 0
        return None

    def _classify(self, trip_id, index):
        network, _, line = parse_trip_id(trip_id)
        rt = index.route_type(trip_id) if index is not None else None
        return consumer_of(rt, network, line)

    def snapshot(self, force=False):
        """{consumer: (positions, trip_updates)} for the registered consumers."""
        self._ensure_index()
        with self._lock:
            now = self._clock()
            if (not force and self._feeds is not None and self._fetched_at is not None
                    and now - self._fetched_at < self.feed_ttl_s):
                return self._feeds
            self.fetch_count += 1
            vp = _decode(_get(POSITIONS_URL, self.user, self.timeout, self._opener),
                         POSITIONS_URL)
            try:
                self.fetch_count += 1
                tu = _decode(_get(TRIP_UPDATES_URL, self.user, self.timeout, self._opener),
                             TRIP_UPDATES_URL)
            except RenfeError:
                tu = None
            with self._state:
                wanted = frozenset(self._wanted)
                index, waiting = self.index, (self.index is None and not self._index_tried)
            out = {c: (_new_feed(vp), _new_feed(tu) if tu is not None else None)
                   for c in wanted}
            # The returns are tracked even while waiting, so the first published fix
            # already has its history.
            cleaned = self._clean(vp, now)
            if not waiting:
                owner = {}
                for ent, tid, held in cleaned:
                    c = self._classify(tid, index)
                    owner[tid] = c
                    if c not in out:
                        continue
                    e = out[c][0].entity.add()
                    e.CopyFrom(ent)
                    if held is not None:
                        e.vehicle.position.latitude = held[0]
                        e.vehicle.position.longitude = held[1]
                if tu is not None:
                    for ent in tu.entity:
                        if not ent.HasField("trip_update"):
                            continue
                        tid = ent.trip_update.trip.trip_id.strip()
                        c = owner.get(tid) or self._classify(tid, index)
                        if c in out and out[c][1] is not None:
                            out[c][1].entity.add().CopyFrom(ent)
            self._feeds = out
            self._fetched_at = now
            return out

    def for_consumer(self, consumer, force=False):
        _check_consumer(consumer)
        if consumer not in self.consumers:
            raise ValueError("consumer %r is not registered with this client" % (consumer,))
        feeds = self.snapshot(force=force)
        if consumer not in feeds:
            # Registered after this snapshot was built.
            feeds = self.snapshot(force=True)
        return feeds[consumer]

    def stats(self):
        with self._state:
            index, tried, loading = self.index, self._index_tried, self._loading
        return {"index_trips": len(index) if index is not None else None,
                "index_day": str(index.day) if index is not None else None,
                "timetable": ("loaded" if index is not None else
                              "failed: every trip taken as rail" if tried else
                              "loading: feeds empty" if loading else "not started"),
                "fetch_count": self.fetch_count, "held_returns": self.held_returns,
                "dropped_null": self.dropped_null,
                "dropped_duplicate": self.dropped_duplicate,
                "trips_tracked": len(self._history)}
