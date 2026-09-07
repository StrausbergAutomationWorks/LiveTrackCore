"""Shared Amtraker API client for the Live Track family.

Consumed by Live Track Amtrak, Live Track VIA Rail and Live Track Brightline.
Pure Python: NO homeassistant imports, so it can ship as a PyPI package that
each integration declares in manifest.json requirements (05_SHARED_LESSONS.md
section B8.1a).

Data: Amtraker (https://amtraker.com), ODC-By v1.0.

Every rule enforced here was MEASURED. See 05_SHARED_LESSONS.md section B8.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import urllib.error
import urllib.request

# Contract version stamped into the shared hass.data key. Bump ONLY when the
# shape a coordinator publishes changes. B8.1a: a consumer finding a
# coordinator at a version it does not understand must build its own rather
# than misuse a foreign object.
CONTRACT_VERSION = 1
SHARED_KEY = "saw_amtraker_feed_v%d" % CONTRACT_VERSION

BASE = "https://api-v3.amtraker.com/v3"

# B8: a User-Agent is MANDATORY. Requests without one are blocked server-side.
DEFAULT_UA = "SAW-LiveTrack/0.1 (+https://github.com/StrausbergAutomationWorks)"

PROVIDERS = ("Amtrak", "Via", "Brightline")

# --- Measured artefact thresholds. B8, Brightline SSOT section 4.3 ----------

# A parked train drifts: measured p50 2 m, max 29 m over 31 min. A 1 m
# "did it move" test counts noise as movement and manufactured a 1,440 s
# update interval.
JITTER_M = 50.0

# TERMINAL SNAP: on completing a run the feed resets a train's position to the
# terminus before dropping it. Measured at 313 km in one 30 s tick, an implied
# 11,670 mph. trainState stayed "Active" and eventCode stayed unchanged, so
# NEITHER FIELD FLAGS IT. Displacement is the only available signal.
#
# A snap is an impossible SPEED, not a fixed distance. CAUGHT BY A TEST
# 2026-09-06: a flat 5 km threshold would have discarded GENUINE movement,
# because at 125 mph over the observed 180 s maximum interval a train
# legitimately covers 10 km. Fastest scheduled service on this feed is Acela
# at 150 mph (241 km/h); 322 km/h leaves headroom while still rejecting a
# 37,560 km/h reset by two orders of magnitude.
MAX_PLAUSIBLE_KMH = 322.0

# Distance fallback for when the two fixes cannot be dated -- which is exactly
# the BATCH_STAMPED case where the snap was observed. 25 km is ~2.5x the
# largest legitimate displacement seen and still 12x below the measured 313 km.
SNAP_KM = 25.0

# Two clocks, measured with ZERO variance (n=225 and n=74). B8.3.
FEED_TICK_S = 30.0
POSITION_INTERVAL_S = 60.0

# Providers whose `velocity` is never populated. B8.4: measured 0 of 9 for
# Brightline while trains covered 76-133 km at highway speed.
VELOCITY_UNPOPULATED = frozenset({"Brightline"})

# Providers whose lastValTS is a fleet-wide feed refresh clock rather than a
# per-train observation time. B8.3 - cannot serve as `last_seen`.
BATCH_STAMPED = frozenset({"Brightline"})


class AmtrakerError(Exception):
    """Base error for this client."""


class RateLimited(AmtrakerError):
    """HTTP 429. NOT 'no data' and NOT a hard negative -- back off and retry.

    01_ENVIRONMENT.md: letting a non-200 count as absence once deleted two
    working sensor sites from a build plan.
    """


class FeedUnavailable(AmtrakerError):
    """Transport or decode failure. Distinct from 'looked and found nothing',
    which is an empty result rather than an exception (C2)."""


# --------------------------------------------------------------------------
# Timestamps
# --------------------------------------------------------------------------

def parse_ts(value):
    """Parse a feed timestamp to an aware datetime, or None.

    B8.4: THREE formats occur in this one field, and VIA is inconsistent with
    itself. Measured over 381 instances:
        Amtrak      offset, no millis  '2026-09-06T20:41:41-07:00'
        Brightline  Z + millis         '2026-09-07T02:00:08.539Z'
        VIA         Z (x48) AND Z+millis (x76)
    Never assume a provider is uniform.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def observed_at(train):
    """When this train's position was true, or None if unknowable.

    B8.3: for BATCH_STAMPED providers lastValTS is a FEED REFRESH CLOCK -- it
    advances every 30 s while the position sits still, so it cannot date the
    position. D0 then applies: omit rather than approximate.

    B8: for Amtrak, lastValTS on a `Predeparture` train carries a SCHEDULED
    FUTURE DEPARTURE, not an observation. Measured negative ages of -1,774 s,
    -274 s and -94 s. Branch on trainState before trusting it.
    """
    if train.get("provider") in BATCH_STAMPED:
        return None
    if train.get("trainState") == "Predeparture":
        return None
    return parse_ts(train.get("lastValTS"))


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km."""
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    h = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(h))


def classify_movement(prev, curr):
    """Classify displacement between two fixes of the SAME train.

    Returns one of "snap", "still", "moved", or "unknown".

    B8 / Brightline SSOT 4.3: both artefacts are invisible to trainState and
    eventCode, so a consumer that trusts those fields ships both bugs.

    A snap is judged by IMPLIED SPEED where the fixes can be dated, and only
    falls back to raw distance where they cannot. A flat distance test throws
    away real movement -- see MAX_PLAUSIBLE_KMH.
    """
    for rec in (prev, curr):
        if not isinstance(rec.get("lat"), (int, float)):
            return "unknown"
        if not isinstance(rec.get("lon"), (int, float)):
            return "unknown"
    km = haversine_km(prev["lat"], prev["lon"], curr["lat"], curr["lon"])

    t0, t1 = observed_at(prev), observed_at(curr)
    if t0 is not None and t1 is not None:
        dt = (t1 - t0).total_seconds()
        if dt > 0:
            if km / (dt / 3600.0) > MAX_PLAUSIBLE_KMH:
                return "snap"
        elif km * 1000.0 >= JITTER_M:
            # Movement with no forward time cannot be real.
            return "snap"
    elif km > SNAP_KM:
        return "snap"

    if km * 1000.0 < JITTER_M:
        return "still"
    return "moved"


def speed_kmh(train):
    """Reported speed in km/h, or None when the field is not real.

    B8.4: `velocity` is documented in MPH and is genuinely populated for
    Amtrak (220 of 248) and VIA (44 of 124). For Brightline it read 0.0 on
    every one of 9 instances while four trains covered 76-133 km at 44-77 mph
    -- the field is UNPOPULATED, not "stationary" (D3b-ii-a).

    D0: omit rather than approximate. Returning None means the caller omits
    the key; returning 0.0 would assert the train is stopped.
    """
    if train.get("provider") in VELOCITY_UNPOPULATED:
        return None
    value = train.get("velocity")
    if not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return value * 1.609344


def derived_speed_kmh(prev, curr):
    """Speed from two position fixes, or None.

    WARNING -- READ BEFORE USING. This is an ESTIMATE and it is a 60-second
    AVERAGE, not an instantaneous speed. It is intended as a DISPLAYED value,
    labelled as derived.

    It must NEVER feed `max_extrapolation_s` or any dead reckoning: D3b-i
    forbids deriving speed from consecutive positions and then extrapolating
    on it, however good the number looks.

    Returns None for snaps, for stationary jitter, and for missing timestamps,
    because each of those produces a fictional speed.
    """
    kind = classify_movement(prev, curr)
    if kind in ("snap", "unknown"):
        return None
    t0, t1 = observed_at(prev), observed_at(curr)
    if t0 is None or t1 is None:
        return None
    dt = (t1 - t0).total_seconds()
    if dt <= 0:
        return None
    if kind == "still":
        return 0.0
    km = haversine_km(prev["lat"], prev["lon"], curr["lat"], curr["lon"])
    return km / (dt / 3600.0)


def course_deg(train):
    """Always None. Present so nobody adds it later thinking it was missed.

    B8.2: the feed's `heading` is one of eight OCTANT STRINGS. Converting "SW"
    to 225.0 invents 22.5 degrees of precision the feed never carried, and D0
    says omit rather than approximate. Use heading_octant() instead.
    """
    return None


def heading_octant(train):
    """The raw octant string, or None.

    D4 display helper. All eight values occur (measured n=198), so it is real
    data rather than a constant -- but it is the DISPLAY form, which inverts
    D5's usual 'integrations emit physics, consumers derive display'.
    """
    value = train.get("heading")
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    if value in ("N", "NE", "E", "SE", "S", "SW", "W", "NW"):
        return value
    return None


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------

def _request(path, user_agent, timeout, opener=None):
    url = "%s/%s" % (BASE, path)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as err:
        if err.code == 429:
            raise RateLimited("HTTP 429 from %s" % path) from err
        raise FeedUnavailable("HTTP %s from %s" % (err.code, path)) from err
    except Exception as err:  # noqa: BLE001 - transport of any kind
        raise FeedUnavailable("%s from %s" % (type(err).__name__, path)) from err
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as err:  # noqa: BLE001
        raise FeedUnavailable("undecodable body from %s" % path) from err


def _flatten(payload):
    """Normalise a trains payload to a list.

    B8: a bare number or a finished train returns `[]` -- a LIST, not the
    documented keyed object. Branch on TYPE, not on emptiness.
    """
    if isinstance(payload, list):
        return []
    if not isinstance(payload, dict):
        return []
    out = []
    for value in payload.values():
        if isinstance(value, list):
            out.extend(x for x in value if isinstance(x, dict))
    return out


class AmtrakerClient:
    """Fetches the Amtraker feed once and serves every provider from it.

    B8.1: /v3/trains takes NO parameters and returns every provider, so N
    consumers make N identical system-wide requests unless the FEED is cached.
    Same shape as 05 section B7 and 03_BACKLOG.md item 104. Documentation does
    not fix it; a shared cache does.

    Measured payload sizes:
        /v3/trains          ~1,150,000 bytes   whole fleet
        /v3/stale               ~6,500 bytes   per-train timeSince
        /v3/trains/<id>         ~2,500 bytes   one train
    """

    def __init__(self, user_agent=DEFAULT_UA, ttl_s=FEED_TICK_S, timeout=30,
                 opener=None, clock=None):
        if not user_agent or not str(user_agent).strip():
            # B8: the server blocks requests with no User-Agent. Fail loudly
            # here rather than puzzling over empty results later.
            raise ValueError("a non-empty User-Agent is required")
        self.user_agent = user_agent
        self.ttl_s = float(ttl_s)
        self.timeout = timeout
        self._opener = opener
        self._clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self._cached = None
        self._cached_at = None
        self.fetch_count = 0
        self.serve_count = 0

    def _fresh(self):
        if self._cached_at is None:
            return False
        return (self._clock() - self._cached_at).total_seconds() < self.ttl_s

    def fetch_fleet(self, force=False):
        """All trains, cached for ttl_s. Default TTL is the measured 30 s feed
        tick, so polling faster than the feed changes costs nothing upstream."""
        self.serve_count += 1
        if not force and self._fresh():
            return self._cached
        payload = _request("trains", self.user_agent, self.timeout, self._opener)
        self._cached = _flatten(payload)
        self._cached_at = self._clock()
        self.fetch_count += 1
        return self._cached

    def trains(self, provider=None, force=False):
        """Trains, optionally filtered to one provider.

        B8.4: the providers do NOT share a data contract -- velocity is real
        for two and always-zero for the third, last_seen has a source for two
        and none for the third. Filtering is where three integrations diverge.
        """
        if provider is not None and provider not in PROVIDERS:
            raise ValueError("unknown provider %r" % (provider,))
        rows = self.fetch_fleet(force=force)
        if provider is None:
            return list(rows)
        return [t for t in rows if t.get("provider") == provider]

    def stale(self):
        """/v3/stale: per-train timeSince for every active train, ~180x
        cheaper than the fleet feed. Use it to DETECT update events."""
        return _request("stale", self.user_agent, self.timeout, self._opener)

    def train(self, train_id):
        """One train, ~2,500 bytes. Accepts 'b5756' or 'b5756-6'."""
        return _flatten(
            _request("trains/%s" % train_id, self.user_agent, self.timeout,
                     self._opener))
