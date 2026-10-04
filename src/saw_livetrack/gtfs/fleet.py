"""Moved here from Live Track Commuter Rail (fleet.py, commit df972df) under
backlog item 274: Live Track Intercity Rail is its second consumer.
No Home Assistant imports. Storage, coordinators, entities and config
flows stay in each integration.

One operator's fleet across polls: what to publish, and each vehicle's record.

No Home Assistant imports, so all of it is tested without HA. The coordinator
fetches and parses; this decides.

Three pieces of per-vehicle state, each for its own reason:

1. FixTracker (saw_livetrack.track, 05_SHARED_LESSONS.md B8.5) holds the last
   two DISTINCT fixes, so a duplicate poll re-emits the segment instead of
   collapsing it (06_MAP_CONTRACT.md D3c-iii), and drops a fix after a jump no
   train could make.
2. A motion anchor: the position where the vehicle last moved more than
   JITTER_M. From it come "is it moving now" (REST_S) and, for a vehicle that
   is not in service, "show it at all" (HOLD_S). 06_MAP_CONTRACT.md D8:
   appear on the first displacement, leave only after sustained stillness.
3. The course of its latest move, published as course_deg while moving and
   held as the arrow's bearing at rest (06 D4).

! FixTracker KEEPS a vehicle's last segment, course included, through every
duplicate poll, so its course_deg never disappears at a platform. Rest is
decided HERE, from the anchor, and at rest course_deg is not published. Live
Track Brightline met the same trap (its coordinator, "standing train went on
publishing its approach course as a measurement").

! COLD START. All of this is memory only; the recorder excludes geo_location,
so there is no history to consult (backlog 198). After a restart a vehicle
that is not in service stays hidden until it moves, which is the rule anyway,
so the restart does not flash ~150 parked vehicles onto the map.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable, Iterable

from ..track import FixTracker

from .realtime import Obs
from .schedule import STATIC_VERSION, iso_utc, line_name

# --- Motion (06_MAP_CONTRACT.md D8) -------------------------------------
# Moved / not moved. A parked vehicle's reported position wanders: Brightline
# p50 2 m, max 29 m; Metra's parked fleet max 37 m over 120 s. 50 m is above
# both, and it is the same threshold saw_livetrack's FixTracker uses to tell a
# new fix from the same one seen again.
JITTER_M = 50.0

# A vehicle that is NOT in service is shown from its first displacement over
# JITTER_M until it has been still this long. D8: appear on the first move,
# disappear only after sustained stillness, never on the first quiet sample.
# A crawl through a signal must not blink a train off the map. ~10 min is
# backlog 198's figure; a choice, not a measurement.
HOLD_S = 600

# A vehicle that has not moved for this long is AT REST: course_deg is
# omitted (D3c-iii) and the arrow is held (D4). A choice, not a measurement:
# long enough that a feed which updates positions less often than we poll
# does not make the course flicker, short enough that a train standing at a
# platform stops claiming a direction of travel within a couple of polls.
REST_S = 90

# A vehicle missing from the feed keeps its entity this long (unavailable
# meanwhile) before the entity is removed. Brightline measured trains leaving
# the feed and coming back 65 s later while still running.
GRACE_S = 180

# Longest gap still published as one segment. Longer means the vehicle stood,
# or dropped out and came back, and a straight slide across that gap would be
# invention. Carried over from Live Track South Shore.
MAX_SEGMENT_S = 600

# A train in service standing farther than this from every stop in the
# timetable is `stalled` (backlog 275; a platform dwell is normal). MEASURED
# 2026-10-04 on the 2026-10-02 PM peak (bin\ltcr275_measure.txt): of 56
# Metra dwells of REST_S or more, 39 lay within 200 m of a stop, two at
# 241 m (New Lenox) and 255 m (University Park), then nothing until 350 m;
# everything beyond was a terminal layover or a hold between stations.
# 300 m sits in that gap (Lee 2026-10-04: 300, not the 200 first proposed).
STALL_M = 300.0


def metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlam = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def _obs_time(record: dict) -> datetime | None:
    return record.get("observed_at")


class Fleet:
    """Per config entry. Feed it every poll; it returns what to publish."""

    def __init__(
        self,
        styling: Callable[[Obs], dict[str, Any]] | None = None,
        *,
        jitter_m: float = JITTER_M,
        hold_s: float = HOLD_S,
        rest_s: float = REST_S,
        grace_s: float = GRACE_S,
        max_segment_s: float = MAX_SEGMENT_S,
        stall_m: float = STALL_M,
    ) -> None:
        self._styling = styling
        self._jitter_m, self._hold_s, self._rest_s = jitter_m, hold_s, rest_s
        self._grace_s, self._max_segment_s = grace_s, max_segment_s
        self._stall_m = stall_m
        self._tracker = FixTracker(observed_at=_obs_time)
        self._fixed: set[str] = set()                 # seen by the tracker
        self._anchor: dict[str, tuple[float, float]] = {}
        self._last_moved: dict[str, float] = {}
        self._course: dict[str, float] = {}           # course of the latest move
        self._known: set[str] = set()                 # in the feed, or in grace
        self._missing: dict[str, float] = {}          # vid -> first poll missed
        self._published: set[str] = set()
        self._first_seen: dict[str, float] = {}       # since when still, before any move

    # -- motion ----------------------------------------------------------
    def _moved(self, obs: Obs, now: float) -> bool:
        """True when this poll put the vehicle more than jitter_m from where
        it last moved. The anchor only moves on such a displacement, so a
        parked vehicle's wander (max 37 m measured on Metra) never counts."""
        here = (obs.lat, obs.lon)
        anchor = self._anchor.get(obs.vehicle_id)
        if anchor is None:
            self._anchor[obs.vehicle_id] = here
            return False
        if metres(anchor[0], anchor[1], here[0], here[1]) > self._jitter_m:
            self._anchor[obs.vehicle_id] = here
            self._last_moved[obs.vehicle_id] = now
            return True
        return False

    def _far_from_stops(self, obs: Obs, static: dict[str, Any] | None) -> bool:
        """True only when the timetable's stop coordinates are known and every
        stop is farther than stall_m. Unknown is never 'far'."""
        if not static or static.get("v") != STATIC_VERSION:
            return False
        pos = static.get("stop_pos") or {}
        if not pos:
            return False
        return all(metres(obs.lat, obs.lon, p[0], p[1]) > self._stall_m
                   for p in pos.values())

    def _since_moved(self, vid: str, now: float) -> float | None:
        t = self._last_moved.get(vid)
        return None if t is None else now - t

    # -- bookkeeping -----------------------------------------------------
    def forget(self, vid: str) -> None:
        self._tracker.forget(vid)
        for store in (self._anchor, self._last_moved, self._course, self._missing,
                      self._first_seen):
            store.pop(vid, None)
        self._fixed.discard(vid)
        self._known.discard(vid)
        self._published.discard(vid)

    def stats(self) -> dict[str, int]:
        return {"known": len(self._known), "published": len(self._published),
                "moving_ever": len(self._last_moved), **self._tracker.stats()}

    # -- the poll --------------------------------------------------------
    def update(
        self,
        observations: Iterable[Obs],
        now: float,
        *,
        show_parked: bool = False,
        delays: dict[str, int] | None = None,
        next_stops: dict[str, tuple[str, float | None]] | None = None,
        static: dict[str, Any] | None = None,
        trip_delays: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        vehicles: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        trains: set[str] = set()
        non_revenue = 0

        for obs in observations:
            vid = obs.vehicle_id
            if vid in seen:          # the same unit twice in one feed: first wins
                continue
            seen.add(vid)
            self._known.add(vid)
            self._missing.pop(vid, None)
            self._first_seen.setdefault(vid, now)

            had_fix = vid in self._fixed
            advanced = self._tracker.update(
                vid, {"lat": obs.lat, "lon": obs.lon, "observed_at": obs.observed_at})
            self._fixed.add(vid)
            seg = self._tracker.segment_fields(vid)
            jumped = advanced and had_fix and "previous_latitude" not in seg
            if jumped:
                # The tracker dropped a fix no train could have travelled
                # from; a course held from before it describes somewhere else.
                self._course.pop(vid, None)

            if self._moved(obs, now):
                course = obs.reported_course
                if course is None:
                    course = seg.get("course_deg")
                if course is not None:
                    self._course[vid] = round(float(course) % 360.0, 1)

            if obs.in_service:
                trains.add(obs.train or vid)
            else:
                non_revenue += 1

            since = self._since_moved(vid, now)
            visible = (obs.in_service or show_parked
                       or (since is not None and since <= self._hold_s))
            if not visible:
                continue
            moving = since is not None and since <= self._rest_s
            # Held: still for rest_s, counted from its last move, or from first
            # sight when it has not moved since (a restart forgets moves).
            still_since = self._last_moved.get(vid, self._first_seen[vid])
            held = not moving and now - still_since >= self._rest_s
            vehicles[vid] = self._record(obs, seg, moving, jumped, delays, next_stops, static,
                                         held=held, trip_delays=trip_delays)

        departed: list[str] = []
        # Published before, still in the feed, now hidden: parked long enough.
        for vid in self._published & seen:
            if vid not in vehicles:
                departed.append(vid)
        # Not in this feed at all: wait out the grace, then forget everything.
        in_grace: set[str] = set()
        for vid in list(self._known - seen):
            first = self._missing.setdefault(vid, now)
            if now - first >= self._grace_s:
                if vid in self._published:
                    departed.append(vid)
                self.forget(vid)
            elif vid in self._published:
                in_grace.add(vid)

        self._published = set(vehicles) | in_grace
        return {
            "vehicles": vehicles,
            "departed": sorted(departed),
            "trains_in_service": len(trains),
            "non_revenue": non_revenue,
            "vehicles_in_feed": len(seen),
        }

    def _record(self, obs: Obs, seg: dict, moving: bool, jumped: bool,
                delays: dict[str, int] | None,
                next_stops: dict[str, tuple[str, float | None]] | None = None,
                static: dict[str, Any] | None = None, *, held: bool = False,
                trip_delays: dict[str, int] | None = None) -> dict[str, Any]:
        vid = obs.vehicle_id
        if obs.in_service:
            label = obs.train or vid
        else:
            # 06_MAP_CONTRACT.md D8: never the bare unit number, which shares
            # a range with train numbers in the same feed.
            label = f"NIS {vid}"
        rec: dict[str, Any] = {
            "vehicle_id": vid,
            "latitude": obs.lat,
            "longitude": obs.lon,
            "in_service": obs.in_service,
            "marker_label": label,
        }
        if obs.train:
            rec["train"] = obs.train
        if obs.line:
            rec["line"] = obs.line

        # D2: observed_at is when the POSITION was true. last_seen is the
        # same instant: the vehicle's own timestamp is the last we heard.
        observed = seg.get("observed_at")
        if observed is not None:
            rec["observed_at"] = observed
            rec["last_seen"] = observed
        # The segment only with BOTH ends dated and a plausible gap: D2 says
        # observed_at must be present wherever previous_* is, and that
        # segment_duration_s = observed_at - previous_observed_at.
        prev_at = seg.get("previous_observed_at")
        if observed is not None and prev_at is not None:
            gap = (datetime.fromisoformat(observed)
                   - datetime.fromisoformat(prev_at)).total_seconds()
            if 0 < gap <= self._max_segment_s:
                rec["previous_latitude"] = seg["previous_latitude"]
                rec["previous_longitude"] = seg["previous_longitude"]
                rec["previous_observed_at"] = prev_at
                rec["segment_duration_s"] = int(gap)
        if jumped:
            # 06_MAP_CONTRACT.md D4a: only a JSON true, only on this record.
            rec["position_jump"] = True

        course = self._course.get(vid)
        if course is not None:
            if moving:
                rec["course_deg"] = course
                rec["icon_rotation_deg"] = course
                rec["icon_rotation_basis"] = "course"
            else:
                # At rest course_deg is omitted (D3c-iii); the arrow keeps the
                # last good bearing as a display value (D4).
                rec["icon_rotation_deg"] = course
                rec["icon_rotation_basis"] = "held"

        if obs.in_service:
            # By trip from stop_delays (one rule for every feed), else the
            # adapter's own by train number.
            d = None
            if trip_delays and obs.trip_id and obs.trip_id in trip_delays:
                d = trip_delays[obs.trip_id]
            elif delays and obs.train in delays:
                d = delays[obs.train]
            if d is not None:
                # Whole minutes toward zero (Lee 2026-10-04): 5:59 reads 5, so
                # delay_min >= 6 is late by Metra's on-time standard (on time =
                # within 5:59 of schedule). 06_MAP_CONTRACT.md D4c.
                rec["delay_min"] = int(d / 60)
            # Standing away from any stop (backlog 275, D4c): physics only;
            # whether it is also late is the consumer's call.
            if held and self._far_from_stops(obs, static):
                rec["stalled"] = True
        # Next station and when (schedule.py). Only in service, only when the
        # trip named its next stop; the time only when there is one
        # (06_MAP_CONTRACT.md D4b). Absent, never empty (D0).
        nxt = next_stops.get(obs.trip_id) if (obs.in_service and next_stops and obs.trip_id) else None
        if nxt is not None:
            rec["next_stop"] = nxt[0]
            if nxt[1] is not None:
                rec["arrival_estimated_at"] = iso_utc(nxt[1])
        # The line's NAME for the hover box (Lee 2026-10-03); only in
        # service: equipment out of service runs no line.
        if obs.in_service:
            name = line_name(static, obs.line, obs.trip_id)
            if name:
                rec["line_name"] = name
        if self._styling is not None:
            rec.update(self._styling(obs))
        return rec
