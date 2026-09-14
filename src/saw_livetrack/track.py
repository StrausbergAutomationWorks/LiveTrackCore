"""Fix tracking for the Live Track passenger-rail family.

Produces the `previous_*` segment fields of 05_SHARED_LESSONS.md D2 so a
consumer can animate between two OBSERVED positions (D3c), and nothing else.

WHY THIS IS A SEPARATE MODULE. The transport in client.py is stateless. This
holds one small piece of state per object, and that state is the whole point:

    D3c-iii -- HOLD THE LAST DISTINCT OBSERVATION, NOT THE LAST ONE.

Live Track South Shore emitted the previous_* fields correctly and still
stuttered on the map, because its poll interval and the source's publish
interval aliased: roughly one poll in three returned an observation identical
to the last, which collapsed the segment to zero length. Every individual
omission was correct -- no movement means no course, zero elapsed means no
segment -- so only the SEQUENCE was wrong, and a single entity snapshot looked
like a broken deploy.

Brightline is the worst possible case of this and it is structural, not
accidental. Measured over three captures: the feed tick is 30 s and the
position changes every 60 s, so EXACTLY HALF of all polls return a duplicate,
on every train, every time.
"""

from __future__ import annotations

import math

# --- Generic motion thresholds. Nothing here is Amtraker-specific. --------

# Below this two fixes are the same place. A parked train drifts: measured
# p50 2 m, max 29 m over 31 min, and a 1 m threshold counted that noise as
# movement and manufactured a 1,440 s update interval.
JITTER_M = 50.0

# Fallback displacement bound for fixes that cannot be dated.
SNAP_KM = 25.0

# A feed reset to a terminus is an impossible SPEED, not a fixed distance. A
# flat distance test discards genuine movement: at 125 mph over an observed
# 180 s interval a train legitimately covers 10 km.
MAX_PLAUSIBLE_KMH = 322.0


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km."""
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    h = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(h))


def _default_observed_at(record):
    """Fallback timestamp extractor: an ISO `observed_at` key, or nothing.

    Sources whose timestamp needs interpreting pass their own callable. The
    Amtraker one, for instance, knows that a Predeparture train's timestamp is
    a SCHEDULED DEPARTURE and that one provider's is a feed job clock -- and
    returns None in both cases rather than dating a position with either.
    """
    import datetime as _d
    v = record.get("observed_at")
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        p = _d.datetime.fromisoformat(s)
    except ValueError:
        return None
    return p if p.tzinfo is not None else None

# Below this, two fixes are the same place and the bearing between them is
# noise. D3c-iii: a train standing at a station has no course -- leave the key
# absent rather than emitting a direction invented from jitter.
COURSE_MIN_M = JITTER_M


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing, degrees true, 0-359.9 (D0)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


class _Fix:
    __slots__ = ("lat", "lon", "at")

    def __init__(self, lat, lon, at):
        self.lat, self.lon, self.at = lat, lon, at

    def same_place_as(self, other):
        return haversine_km(self.lat, self.lon, other.lat, other.lon) * 1000.0 < JITTER_M


class FixTracker:
    """Holds the last two DISTINCT fixes per object.

    Feed a train record on every poll. Duplicate polls are absorbed: the
    segment is re-emitted unchanged rather than destroyed.
    """

    def __init__(self, observed_at=None, lat_key="lat", lon_key="lon"):
        """`observed_at` is a callable taking one record and returning an
        aware datetime, or None when the record's timestamp cannot date the
        position. Sources differ enough that this must be supplied rather than
        assumed: one Amtraker provider stamps its whole fleet from a feed job
        clock, and a Predeparture train's timestamp is a SCHEDULED DEPARTURE,
        not an observation. Both must yield None, and only the source knows.
        """
        self._observed_at = observed_at or _default_observed_at
        self._lat_key, self._lon_key = lat_key, lon_key
        self._curr = {}      # key -> _Fix, the newest distinct fix
        self._prev = {}      # key -> _Fix, the one before it
        self.duplicates = 0  # polls that carried nothing new
        self.snaps = 0       # feed resets to a terminus

    def _fix_from(self, record):
        lat = record.get(self._lat_key)
        lon = record.get(self._lon_key)
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            return None
        return _Fix(lat, lon, self._observed_at(record))

    def update(self, key, record):
        """Record an observation. Returns True if it advanced the segment."""
        new = self._fix_from(record)
        if new is None:
            return False

        curr = self._curr.get(key)
        if curr is None:
            self._curr[key] = new
            return True

        # D3c-iii: a duplicate is NOT a previous fix. Absorb it and keep the
        # segment intact -- advancing here is what collapses it to zero length.
        #
        # THE TEST IS POSITION ALONE, NOT POSITION AND TIMESTAMP. A first
        # version required both and caught nothing real: this feed advances
        # lastValTS on every 30 s tick whether or not the train moved, so a
        # genuine duplicate arrives with a NEW timestamp and an unchanged
        # position. Caught by a test 2026-09-14.
        #
        # A same-place observation is the SAME FIX, seen again. It refreshes
        # how recently we heard from the train; it does not create a segment.
        if new.same_place_as(curr):
            self.duplicates += 1
            if new.at is not None:
                curr.at = new.at
            return False

        # The feed resets a finished train's position to its terminus. Neither
        # trainState nor eventCode flags it, so displacement is the only
        # signal. Judged by implied SPEED where the fixes can be dated: a flat
        # distance test discards genuine movement, because at 125 mph over the
        # observed 180 s maximum interval a train legitimately covers 10 km.
        km = haversine_km(curr.lat, curr.lon, new.lat, new.lon)
        snapped = km > SNAP_KM
        if not snapped and curr.at is not None and new.at is not None:
            dt = (new.at - curr.at).total_seconds()
            if dt > 0 and km / (dt / 3600.0) > MAX_PLAUSIBLE_KMH:
                snapped = True
        if snapped:
            self.snaps += 1
            self._prev.pop(key, None)   # the old fix is not on this segment
            self._curr[key] = new
            return True

        self._prev[key] = curr
        self._curr[key] = new
        return True

    def forget(self, key):
        self._prev.pop(key, None)
        self._curr.pop(key, None)

    def segment_fields(self, key):
        """The D2 kinematics keys this object can honestly supply.

        D0: UNAVAILABLE MEANS OMIT THE KEY. Never None, never a sentinel. So
        this returns only the keys that are real, and the caller merges it
        into extra_state_attributes without filtering.
        """
        out = {}
        curr = self._curr.get(key)
        if curr is None:
            return out

        # D2: observed_at is when the POSITION was true, not when we updated.
        # For a BATCH_STAMPED provider the feed timestamp is a job clock, so
        # observed_at() returns None and the key is simply absent.
        if curr.at is not None:
            out["observed_at"] = curr.at.isoformat()

        prev = self._prev.get(key)
        if prev is None:
            # D2: omit previous_* on a first sighting. A consumer must read
            # their absence as "place it and do not move it".
            return out

        out["previous_latitude"] = prev.lat
        out["previous_longitude"] = prev.lon
        if prev.at is not None:
            out["previous_observed_at"] = prev.at.isoformat()

        # D2: course_deg is the direction of ACTUAL TRAVEL, and for a train
        # heading_deg is not reported at all -- so that key stays omitted and
        # a consumer rotates the icon by course as documented.
        #
        # D3c-iii: a bearing between two points a metre apart is noise, and a
        # train standing at a station has no course. This omission is CORRECT
        # and must not be "fixed".
        if haversine_km(prev.lat, prev.lon, curr.lat, curr.lon) * 1000.0 >= COURSE_MIN_M:
            out["course_deg"] = round(
                bearing_deg(prev.lat, prev.lon, curr.lat, curr.lon), 1)
        return out

    def stats(self):
        return {"tracked": len(self._curr), "with_segment": len(self._prev),
                "duplicates_absorbed": self.duplicates, "snaps": self.snaps}
