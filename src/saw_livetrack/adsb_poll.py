"""ONE adsb.fi poll per Home Assistant instance, for every SAW aircraft integration (backlog 297, 0.2.13).

Live Track Aircraft and Live Track Enforcement Aircraft both read ADS-B from adsb.fi, from the one
public IP of the house, and adsb.fi's limit is counted per IP (10_SEED/05_SHARED_LESSONS.md B16).
So both ask through this module and neither imports the other.

! THE HEX REQUEST AND THE CIRCLE REQUEST STAY SEPARATE REQUESTS, each on its own cadence (Lee
2026-10-24 18:03). Shared means:
  * ONE UNION HEX LIST A MINUTE. Every consumer names the hexes it wants (set_hexes); one round asks
    adsb.fi /v2/icao/ for the union, cut at MAX_PER_REQUEST, and each consumer reads back its own.
    Measured 2026-10-22: 994 hexes in one request, HTTP 200 in 1.3 s, 6.4 kB back.
  * AN IDENTICAL CIRCLE IS ASKED ONCE. A circle asked again within CIRCLE_SHARE_S, by anyone, is
    answered from that request; two asking at once wait for one request.
  * ONE 429 BACKOFF, per source name, for every consumer: the limit is the house's, not an
    integration's. And every request to one source is spaced PACE_S apart (adsb.fi documents one
    request a second), so two consumers polling in phase do not collide.
  * CLAIMS. Each consumer says which hexes it draws (claim); another can skip them
    (claimed_by_others), so one aircraft is one marker (06_MAP_CONTRACT.md D1).
! THE PARSER, THE BACKOFF AND THE CIRCLE FETCH MOVED VERBATIM from Live Track Aircraft's
sources/adsb.py and sources/base.py at 62064b6, and the hex round from its watch.py; what is new is the
sharing around them.
! NEVER adsb.lol FOR THE HEX POLL (B16). A circle's sources are the caller's: Live Track Aircraft
keeps adsb.fi then adsb.lol, Live Track Enforcement Aircraft asks adsb.fi only.
! No Home Assistant import and no aiohttp import: the session comes from the consumer, used through
session.get(url, headers=...) and resp.status / resp.headers / resp.json(content_type=None).
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)

STORE_KEY = "saw_adsb_poll"
USER_AGENT = "saw-livetrack (+https://github.com/StrausbergAutomationWorks/LiveTrackCore)"
REQUEST_TIMEOUT_S = 20

# --- sources ----------------------------------------------------------------
FI = "adsb.fi"
LOL = "adsb.lol"
# ! v3, NOT v2, for the circle: adsb.fi calls v2/lat/lon/dist deprecated. v3 answers in adsb.lol's
# shape ("ac", `now` in MILLISECONDS); the parser reads both.
CIRCLE_URLS = {
    FI: "https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{nm}",
    LOL: "https://api.adsb.lol/v2/point/{lat}/{lon}/{nm}",
}
FI_ONLY = (FI,)
FI_THEN_LOL = (FI, LOL)
HEX_URL = "https://opendata.adsb.fi/api/v2/icao/"

# --- cadences ---------------------------------------------------------------
HEX_INTERVAL_S = 60
# The union answer is dropped after this long without a fresh one, so a failing round does not
# freeze aircraft on the map.
HEX_STALE_S = 3 * HEX_INTERVAL_S
MAX_PER_REQUEST = 994
PACE_S = 1.1               # adsb.fi: 1 request a second, from this IP
CIRCLE_SHARE_S = 10.0      # under the 15 s poll floor, so one consumer never reuses its own answer
# ! A COVERED CIRCLE COSTS NO REQUEST (0.2.14, Lee 2026-10-24 23:58 and 2026-10-25 00:25): a consumer's
# circle lying wholly inside another consumer's circle is answered from that consumer's last answer,
# filtered by distance, while it is at most this old, whatever the two source lists. Over the 15 s poll
# floor, so a neighbour polling every 15 s is always fresh enough; a neighbour polling slower, or
# failing, is not, and the circle is asked itself.
COVER_FRESH_S = 20.0

# --- backoff (backlog 244) ----------------------------------------------------
BACKOFF_FIRST_S = 60.0
BACKOFF_MAX_S = 600.0
RETRY_AFTER_MAX_S = 900.0

# ! A SHORT ANSWER IS NOT A NEGATIVE RESULT. Measured 2026-09-25: on the edges of its 429 cycle
# adsb.lol answered HTTP 200 with 38 aircraft, then with 0, where ~200 were overhead a poll earlier.
SHORT_GUARD_MIN = 20
SHORT_FRACTION = 0.5

KT_TO_KMH = 1.852
FT_TO_M = 0.3048
FPM_TO_MS = 0.00508
STATIONARY_KT = 40.0


class SourceError(Exception):
    """A source could not answer. The next source should be tried."""


@dataclass(slots=True)
class Observation:
    """One aircraft, as one source saw it, in canonical units (05_SHARED_LESSONS.md D0).

    ! Every optional field defaults to None and is OMITTED downstream rather than emitted as a
    sentinel. identity is the ICAO 24-bit address, lower case, never a callsign.
    ! heading_deg and course_deg are NOT the same field (D2): heading is where it POINTS, course
    where it is GOING.
    """

    identity: str
    latitude: float
    longitude: float
    # When the POSITION was true, not when we fetched it (D2).
    observed_at: int
    heading_deg: float | None = None
    course_deg: float | None = None
    speed_kmh: float | None = None
    altitude_m: float | None = None
    vertical_rate_ms: float | None = None
    callsign: str | None = None
    type_code: str | None = None
    emitter_category: str | None = None
    squawk: str | None = None
    registration: str | None = None
    seen_pos_s: float | None = None
    # The feed's own database flags (readsb dbFlags: military 1, interesting 2, PIA 4, LADD 8) and
    # its owner/operator text, from the aggregator's database, not from the aircraft.
    db_flags: int | None = None
    own_op: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_moving(self) -> bool:
        if self.speed_kmh is None:
            return False
        return self.speed_kmh > STATIONARY_KT * KT_TO_KMH


def _f(value: Any) -> float | None:
    """A finite float, or None. Strings and sentinels become None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def parse_record(record: dict[str, Any], now: float) -> Observation | None:
    """One feed record to an Observation, or None if it is not for us."""
    hex_id = (record.get("hex") or "").strip().lower()
    if not hex_id:
        return None
    # ! SKIP TIS-B: a '~' prefix marks a non-ICAO address, a ground station rebroadcasting a radar
    # target (1.1% of records measured 2026-09-10, no callsign, no category, seen_pos 34-50 s).
    if hex_id.startswith("~"):
        return None
    lat, lon = _f(record.get("lat")), _f(record.get("lon"))
    if lat is None or lon is None:
        return None
    # ! SKIP GROUND OBJECTS: alt_baro is the STRING "ground" on the surface, airport vehicles included.
    alt_raw = record.get("alt_baro")
    if isinstance(alt_raw, str):
        if alt_raw.strip().lower() == "ground":
            return None
        alt_raw = None
    # How stale the source says this position is; `now` is the SERVER's clock.
    seen_pos = _f(record.get("seen_pos"))
    observed_at = round(now - seen_pos) if seen_pos is not None else round(now)
    gs = _f(record.get("gs"))
    alt = _f(alt_raw)
    rate = _f(record.get("baro_rate"))
    flags = record.get("dbFlags")
    return Observation(
        identity=hex_id,
        latitude=lat,
        longitude=lon,
        observed_at=observed_at,
        course_deg=_f(record.get("track")),
        heading_deg=_f(record.get("true_heading")),
        speed_kmh=None if gs is None else round(gs * KT_TO_KMH, 1),
        altitude_m=None if alt is None else round(alt * FT_TO_M, 1),
        vertical_rate_ms=None if rate is None else round(rate * FPM_TO_MS, 2),
        callsign=(record.get("flight") or "").strip() or None,
        type_code=(record.get("t") or "").strip() or None,
        emitter_category=(record.get("category") or "").strip() or None,
        squawk=(record.get("squawk") or "").strip() or None,
        registration=(record.get("r") or "").strip() or None,
        seen_pos_s=seen_pos,
        # ! dbFlags is OMITTED when 0 (measured 2026-10-22). An int only: a string or a bool is not
        # a flag word.
        db_flags=flags if isinstance(flags, int) and not isinstance(flags, bool) else None,
        own_op=(str(record.get("ownOp") or "").strip() or None),
    )


def feed_clock(payload: dict[str, Any], now: float) -> float:
    """The answer's own `now` in seconds, else ours.

    ! THE TWO SOURCES USE DIFFERENT UNITS FOR THE SAME FIELD (measured 2026-09-11): adsb.lol and
    adsb.fi v3 send milliseconds, adsb.fi v2 seconds. Sniff the magnitude.
    """
    feed_now = _f(payload.get("now"))
    if feed_now and feed_now > 1e11:
        feed_now = feed_now / 1000.0
    return feed_now if feed_now else now


def parse_payload(payload: dict[str, Any], now: float) -> dict[str, Observation]:
    """An answer's records, on the feed's own clock; a hex carried twice keeps the later record."""
    base = feed_clock(payload, now)
    out: dict[str, Observation] = {}
    for record in payload.get("ac") or payload.get("aircraft") or []:
        if isinstance(record, dict):
            obs = parse_record(record, base)
            if obs is not None:
                out[obs.identity] = obs
    return out


def retry_after_s(value: str | None, wall_now: float) -> float | None:
    """Retry-After as seconds from now, clamped; None if absent or unreadable (RFC 9110: seconds or a date)."""
    if not value:
        return None
    value = value.strip()
    try:
        secs = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        secs = when.timestamp() - wall_now
    if secs != secs or abs(secs) == float("inf"):
        return None
    return max(1.0, min(RETRY_AFTER_MAX_S, secs))


class Backoff:
    """Per-source skip windows after HTTP 429. Decisions on the monotonic clock; the wall clock only shown."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._wall = wall
        self._until: dict[str, float] = {}
        self._until_wall: dict[str, float] = {}
        self._strikes: dict[str, int] = {}

    def remaining(self, name: str) -> float | None:
        until = self._until.get(name)
        if until is None:
            return None
        left = until - self._clock()
        return left if left > 0 else None

    def refused(self, name: str, retry_after: float | None) -> tuple[float, int]:
        """Record a 429. Returns (skip seconds, consecutive refusals)."""
        strikes = self._strikes.get(name, 0) + 1
        self._strikes[name] = strikes
        delay = retry_after if retry_after is not None else min(
            BACKOFF_MAX_S, BACKOFF_FIRST_S * 2 ** (strikes - 1))
        self._until[name] = self._clock() + delay
        self._until_wall[name] = self._wall() + delay
        return delay, strikes

    def answered(self, name: str) -> int:
        """Record a 200. Returns the refusals it ended (0 if none)."""
        self._until.pop(name, None)
        self._until_wall.pop(name, None)
        return self._strikes.pop(name, 0)

    def status(self) -> dict[str, str]:
        """{source: resume time, UTC ISO} for every source now skipped; one value per episode."""
        return {
            name: datetime.fromtimestamp(self._until_wall[name], timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z")
            for name in self._until if self.remaining(name) is not None
        }


def short_floor(previous_count: int | None) -> int | None:
    """The fewest aircraft a believable answer may carry, or None for no guard."""
    if not previous_count or previous_count < SHORT_GUARD_MIN:
        return None
    return int(previous_count * SHORT_FRACTION)


def chunks(hexes: list[str], size: int = MAX_PER_REQUEST) -> list[list[str]]:
    return [hexes[i:i + size] for i in range(0, len(hexes), size)]


def km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (haversine, mean Earth radius)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, h)))


def _norm(hexes: Iterable[str] | None) -> frozenset[str]:
    out = set()
    for h in hexes or ():
        if isinstance(h, str):
            h = h.strip().lower()
            if len(h) == 6 and all(c in "0123456789abcdef" for c in h):
                out.add(h)
    return frozenset(out)


class Poll:
    """The instance's one poller: the union hex round, shared circles, one backoff, the claims."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 backoff: Backoff | None = None,
                 sleep: Callable[[float], Any] = asyncio.sleep,
                 user_agent: str = USER_AGENT) -> None:
        self._clock = clock
        self._sleep = sleep
        self.backoff = Backoff(clock=clock) if backoff is None else backoff
        self.user_agent = user_agent
        self._hexes: dict[str, frozenset[str]] = {}
        self._claims: dict[str, frozenset[str]] = {}
        self._hex_lock = asyncio.Lock()
        self._hex_next = 0.0
        self._hex_at: float | None = None
        self._hex_obs: dict[str, Observation] = {}
        self.hex_status = "idle"
        self._pace_locks: dict[str, asyncio.Lock] = {}
        self._last_start: dict[str, float] = {}
        self._circles: dict[tuple, list] = {}
        # Each consumer's last circle answer: (time, lat, lon, radius_km ASKED - the nm radius in km,
        # source, observations). What a covered circle is answered from.
        self._answers: dict[str, tuple] = {}
        self.requests: dict[str, int] = {}      # by source, since start: what the IP spent
        self.covered_answers = 0                # circles answered from another consumer's answer
        self.circle_how: dict[str, str] = {}    # consumer -> "covered" or "asked", its last circle

    # --- consumers ------------------------------------------------------------
    def set_hexes(self, consumer: str, hexes: Iterable[str] | None) -> None:
        """The hexes this consumer wants in the union round. An empty set removes it."""
        h = _norm(hexes)
        if h:
            self._hexes[consumer] = h
        else:
            self._hexes.pop(consumer, None)

    def union(self) -> list[str]:
        out: set[str] = set()
        for h in self._hexes.values():
            out |= h
        return sorted(out)

    def claim(self, consumer: str, hexes: Iterable[str] | None) -> None:
        """What this consumer draws now; another consumer skips these."""
        self._claims[consumer] = _norm(hexes)

    def claimed_by_others(self, consumer: str) -> frozenset[str]:
        out: set[str] = set()
        for who, h in self._claims.items():
            if who != consumer:
                out |= h
        return frozenset(out)

    def release(self, consumer: str) -> None:
        """A consumer unloading: its hexes and claims go."""
        self._hexes.pop(consumer, None)
        self._claims.pop(consumer, None)
        self._answers.pop(consumer, None)
        self.circle_how.pop(consumer, None)

    # --- one request ----------------------------------------------------------
    async def _pace(self, source: str) -> None:
        lock = self._pace_locks.setdefault(source, asyncio.Lock())
        async with lock:
            last = self._last_start.get(source)
            if last is not None:
                wait = last + PACE_S - self._clock()
                if wait > 0:
                    await self._sleep(wait)
            self._last_start[source] = self._clock()

    async def get_json(self, session, source: str, url: str, user_agent: str | None = None) -> Any:
        """One GET through the shared pacing and backoff. Raises SourceError for anything but a 200.

        ! A non-200 is never a negative RESULT; 429 means slow down, not "no aircraft".
        """
        left = self.backoff.remaining(source)
        if left is not None:
            raise SourceError(f"{source} backing off, {left:.0f} s left")
        await self._pace(source)
        headers = {"User-Agent": user_agent or self.user_agent, "Accept": "application/json"}
        self.requests[source] = self.requests.get(source, 0) + 1
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_S):
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        if resp.status == 429:
                            header = resp.headers.get("Retry-After")
                            delay, strikes = self.backoff.refused(
                                source, retry_after_s(header, time.time()))
                            _LOGGER.log(logging.WARNING if strikes > 1 else logging.INFO,
                                        "%s rate-limited (HTTP 429, %s in a row); skipping it for %.0f s (%s)",
                                        source, strikes, delay,
                                        f"Retry-After {header!r}" if header else "no Retry-After")
                        raise SourceError(f"{source} returned HTTP {resp.status}")
                    payload = await resp.json(content_type=None)
        except SourceError:
            raise
        except Exception as err:  # noqa: BLE001 - every fault is this source failing
            raise SourceError(f"{source} unreachable: {err!r}") from err
        ended = self.backoff.answered(source)
        if ended:
            _LOGGER.info("%s answering again after %s refusal(s)", source, ended)
        return payload

    # --- the circle -----------------------------------------------------------
    async def _fetch_circle(self, session, lat: float, lon: float, nm: int, now: float,
                            sources: tuple[str, ...], min_expected: int | None,
                            user_agent: str | None) -> tuple[str, list[Observation]]:
        """The first source that answers fully and is not backing off (Live Track Aircraft's fetch()).

        ! min_expected (short_floor of the last good count): a 200 with fewer aircraft is SHORT and
        the next source is tried. If every source answered and all were short, the largest is
        returned; if one was short and another could not answer, that is a failure.
        """
        failures: list[str] = []
        shorts: list[tuple[str, list[Observation]]] = []
        unanswered = 0
        for name in sources:
            url = CIRCLE_URLS[name].format(lat=round(lat, 6), lon=round(lon, 6), nm=nm)
            try:
                payload = await self.get_json(session, name, url, user_agent)
            except SourceError as err:
                failures.append(str(err))
                unanswered += 1
                _LOGGER.info("%s; trying the next source", err)
                continue
            result = list(parse_payload(payload if isinstance(payload, dict) else {}, now).values())
            if min_expected is not None and len(result) < min_expected:
                msg = f"{name} answered short: {len(result)} aircraft, expected at least {min_expected}"
                failures.append(msg)
                shorts.append((name, result))
                _LOGGER.info("%s; trying the next source", msg)
                continue
            return name, result
        if shorts and not unanswered:
            return max(shorts, key=lambda s: len(s[1]))
        raise SourceError("; ".join(failures) or "no sources configured")

    def covered(self, consumer: str, latitude: float, longitude: float,
                radius_km: float) -> tuple[str, list[Observation]] | None:
        """(source, observations) for a circle wholly inside ANOTHER consumer's fresh answer, else None.

        ! Wholly inside means the distance between the centres plus this radius is at most the radius
        that consumer actually ASKED (its nm radius in km, not the km it configured: rounding to whole
        nm can shrink it by up to 0.93 km). A consumer's own answer never covers it. The freshest
        covering answer wins; its source is passed on, so a consumer can credit the feed that answered.
        """
        t = self._clock()
        best = None
        for who, (at, lat, lon, asked_km, source, obs) in self._answers.items():
            if who == consumer or t - at > COVER_FRESH_S:
                continue
            if km_between(latitude, longitude, lat, lon) + radius_km <= asked_km:
                if best is None or at > best[0]:
                    best = (at, source, obs)
        if best is None:
            return None
        self.covered_answers += 1
        return best[1], [o for o in best[2]
                         if km_between(latitude, longitude, o.latitude, o.longitude) <= radius_km]

    async def circle(self, session, latitude: float, longitude: float, radius_km: float, now: float,
                     sources: tuple[str, ...] = FI_THEN_LOL, min_expected: int | None = None,
                     user_agent: str | None = None,
                     consumer: str | None = None) -> tuple[str, list[Observation]]:
        """Every aircraft in the circle: (source, observations). Raises SourceError.

        ! A COVERED CIRCLE COSTS NO REQUEST (0.2.14): with `consumer` given, a circle wholly inside
        another consumer's fresh answer is answered from it (covered()); otherwise asked, and the
        answer kept as this consumer's, for whoever lies inside it.
        ! AN IDENTICAL CIRCLE IS ASKED ONCE: the same centre (to 4 decimals, ~11 m), radius in nm and
        sources within CIRCLE_SHARE_S, by any consumer, gets that answer or that failure.
        """
        if consumer is not None:
            hit = self.covered(consumer, latitude, longitude, radius_km)
            if hit is not None:
                self.circle_how[consumer] = "covered"
                return hit
            self.circle_how[consumer] = "asked"
        res = await self._circle_shared(session, latitude, longitude, radius_km, now, sources,
                                        min_expected, user_agent)
        if consumer is not None:
            nm = max(1, min(250, round(radius_km / 1.852)))
            self._answers[consumer] = (self._clock(), latitude, longitude, nm * 1.852, res[0], res[1])
        return res

    async def _circle_shared(self, session, latitude: float, longitude: float, radius_km: float,
                             now: float, sources: tuple[str, ...], min_expected: int | None,
                             user_agent: str | None) -> tuple[str, list[Observation]]:
        nm = max(1, min(250, round(radius_km / 1.852)))
        key = (round(latitude, 4), round(longitude, 4), nm, tuple(sources))
        t = self._clock()
        for k in [k for k, v in self._circles.items() if t - v[0] > 60.0 and v[1].done()]:
            del self._circles[k]
        hit = self._circles.get(key)
        if hit is not None and (not hit[1].done() or t - hit[0] < CIRCLE_SHARE_S):
            return await asyncio.shield(hit[1])
        fut = asyncio.get_running_loop().create_future()
        self._circles[key] = [t, fut]
        try:
            res = await self._fetch_circle(session, latitude, longitude, nm, now, tuple(sources),
                                           min_expected, user_agent)
        except BaseException as err:  # noqa: BLE001 - handed to whoever shares it, then re-raised
            if isinstance(err, asyncio.CancelledError):
                fut.cancel()
                self._circles.pop(key, None)
                raise
            fut.set_exception(err)
            fut.exception()   # retrieved: a failure nobody else waited for is not "never retrieved"
            raise
        fut.set_result(res)
        return res

    # --- the union hex round --------------------------------------------------
    def hex_due(self) -> bool:
        return self._clock() >= self._hex_next

    async def hex_round(self, session, now: float) -> str:
        """Ask for the union once a minute; a call before it is due returns at once.

        ! A failure keeps the last good answer until it is stale: a non-200 is never "none of them is
        flying". Returns the round's status.
        """
        async with self._hex_lock:
            if not self.hex_due():
                return self.hex_status
            self._hex_next = self._clock() + HEX_INTERVAL_S
            hexes = self.union()
            if not hexes:
                self.hex_status = "idle"
                return self.hex_status
            left = self.backoff.remaining(FI)
            if left is not None:
                self.hex_status = f"waiting: {FI} backing off"
                return self.hex_status
            found: dict[str, Observation] = {}
            for part in chunks([h.upper() for h in hexes]):
                try:
                    payload = await self.get_json(session, FI, HEX_URL + ",".join(part))
                except SourceError as err:
                    self.hex_status = f"failed: {err}"
                    return self.hex_status
                found.update(parse_payload(payload if isinstance(payload, dict) else {}, now))
            self._hex_obs = found
            self._hex_at = self._clock()
            self.hex_status = "ok"
            return self.hex_status

    def hex_observations(self, consumer: str) -> list[Observation]:
        """This consumer's hexes from the last answer, or nothing once it is HEX_STALE_S old."""
        if self._hex_at is None or self._clock() - self._hex_at > HEX_STALE_S:
            return []
        want = self._hexes.get(consumer, frozenset())
        return [o for h, o in self._hex_obs.items() if h in want]

    def status(self) -> dict[str, Any]:
        """Low-cardinality state for a sensor attribute."""
        return {"hex_status": self.hex_status, "hexes": len(self.union()),
                "consumers": sorted(set(self._hexes) | set(self._claims)),
                "backoff": self.backoff.status()}


def shared(store: dict) -> Poll:
    """The instance's one poller, made on first use. store is the consumer's hass.data."""
    poll = store.get(STORE_KEY)
    if poll is None:
        poll = store[STORE_KEY] = Poll()
    return poll
