"""V/Line's feed rules, for every consumer of Transport Victoria's V/Line feed. Pure Python: NO homeassistant imports.

Moved here in 0.2.38 (backlog 337; Lee 2026-11-13 01:48 CST: "Build and deploy covers PyPI and moving the feed rules into
the library") from Live Track Intercity Rail's sources/vline.py (cfe19bb), when V/Line's commuter routes went to Live
Track Commuter Rail and the feed got two consumers (saw_livetrack.transport_vic, COMMUTER). The rules and every measured
constant are that module's, unchanged; only the timetable is now asked through a small interface (Timetable, below), so
each integration keeps its own copy in its own shape. Every rule MEASURED on capture round 1, 2026-10-18 to 10-21, or
DECIDED by Lee 2026-11-01 and 11-02 (30_Integrations/Live_Track_Intercity_Rail/20_Research/_shared/australia/
AU_CAPTURE1_ANALYSIS_20261101.md, section 13; Intercity SSOT 8.5):

THE POSITION FEED RUNS ON THE TIMETABLE, NOT THE TRAIN. A trip leaves it about a minute after its scheduled arrival
wherever the train is: 45.5% of 1,137 trips were last seen over 1 km short of the terminal, every run forecast 10
minutes or more late among them. Fixes freeze at about 23:58 and running trips leave at 00:15, the same nine trains
losing up to 130 minutes each night. Their trip updates go on.

SO A MISSING ROW IS A VERY STALE FIX (decision 1). A run keeps its last accepted fix and goes on being reported with it,
at that fix's time (Report.held), so a consumer treats the freeze, the 00:15 cut and the timetable's exit alike. What a
consumer then draws is its own: Intercity Rail moves a stale row on at its last speed until the fix is TIMED_AFTER_S old
(decision 2), then times it to its next stop, and a run with no row once its fix is ROW_GONE_TIMED_S old. Scored on 223
to 252 late runs with their rows hidden (bin\\ltir_vl_holdout_variants.txt), metres from the fixes hidden, 5 to 10
minutes on, p50 / p90: timed from 120 s 987 / 4,491, from 600 s 2,469 / 8,282. A run ends when the terminal's forecast
has passed, when the trip is cancelled, or when good answers have not carried it for UPDATES_GONE_S; a failed answer
never counts. Nothing can report on the train once its row has ended, only on these hidden stretches (INFERRED that it
is running, strongly: section 4). The state is in memory: after a restart these trains are gone until their next run.

A FROZEN FEED COUNTS AS NO ROW (Lee 2026-11-02 14:03). The first midnight on the box: a frozen row stood at its next
stop, then raced at 10 minutes (8817, 176 km/h for five minutes). Timing every stale row sooner is wrong: at 300 s a
real stale fix, 300-600 s old, went from p50 1,324 m to 2,406 (bin\\ltir_vl_t_show.txt). A freeze can be told without a
clock, every train stopping at once: when the newest fix in the WHOLE feed is over FROZEN_FEED_S old with
FROZEN_FEED_ROWS or more rows. In 67.6 hours, both midnights and two single polls by day (bin\\ltir_vl_t_feedwide.txt);
every other spell had one row, a lone train or the frozen last Geelong run. Since 0.2.38 each consumer is handed only
its own routes, so the rule reads the whole feed's newest fix from the client (TransportVicClient.feed_newest, the
`whole` argument): a long-distance subset at midnight may hold one train, which the rule would not count.

IDENTITY "<service number>-<day of start date>": 1,151 runs, no collisions; the trip id repeats across dates (377). NOT
the vehicle: 46 trips had two, 50 an empty one, and 3 sat on two trips at once. The service number is the last four
digits of trip_id on 6,988 of 6,988, even toward Melbourne.

NO AGE REFUSAL (decision 3): 89% of fixes reaching 300 s came back. A fix over 300 s old at FIRST sight gets no report. A
train is finished, and gets none, once its terminal's forecast has passed and its fix is over 300 s old: the last
Geelong run stands frozen in the feed until 04:22.

FLIPS (decision 5): a fix over 70 m/s from the last accepted one is refused, and the train goes on from that one: 14
pairs on 4 trips of 1,151, out and back 33 to 254 km with no change of vehicle id, INFERRED to be two units reporting
under one id; the longest such stretch ran 19 minutes (bin\\ltir_vl_flips.txt). Refused fixes that agree with each
other for CONFIRM_S are the truth, or a wrong first fix would hold the right ones off for as long as 70 m/s takes to
cover the distance.

A FIX CAN COME OLDER THAN ONE ALREADY READ (bin\\ltir_vl_older.txt): 1,192 of 111,262, on 633 of 1,151 runs, nearly
always one fix alone, p50 23 s behind, p90 514, max 1,703, with the next fix current again. It says nothing new:
refused, no vote.

NEXT STOP (D5): the timetable plus the update's delay, first stop still ahead, since the updates name no stop ahead on
20.8% of cases. The origin is never next; its delay still counts for the stop after it (next_stop).

A LIVE TRIP ID MAY NOT BE IN THE TIMETABLE (Lee 2026-11-05 21:34, "Build the V/Line fallback"). Found 2026-11-05 with no
new publication since 11-02 (same ETag): the realtime feed named Friday's trips `01-GEL--1-T0-8751` on six lines where
the timetable has `01-GEL--5-T0-8751`; 20 of 33 running trains had no stops, no delay and no estimate
(bin\\ltir_vl_tripids.txt). Such a trip takes the ONE timetable trip with the same line prefix (`01-GEL`) and service
number whose service runs on its start date: 34 of 34 had exactly one, 33 of 34 matching every stop its update names by
stop_id and sequence (bin\\ltir_vl_unknown_trips.txt), and across the publication's first 63 days every line and number
running on a date ran ONE trip (21,835 of 21,835, bin\\ltir_vl_fb_census.txt). None or several: nothing, as before. The
trip update is still matched by the live id; only the stops and the line come from the timetable trip (Run.table_id).
On 2026-11-13 at 07:4xZ, 43 of 43 live trip ids were NOT in the box's 2026-11-07 copy: every train went through it
(Commuter Rail's research note AU_VLINE_COMMUTER_20261113.md section 6).

WHAT A CONSUMER GIVES: its own copy of the timetable behind four names (Timetable, below): whether it holds a trip, the
trips to look a twin up among, whether a trip's service runs on a date, and a trip's stop rows in seconds from its
service day's origin. WHAT IT GETS: VLineRuns.update() -> (reports, skipped): one Report per run to draw, in the order
the rules met them, each with the run's state (its last accepted fix, its timetable trip and rows, its last delays) and
whether its row is gone (held). Left to each consumer: the look, the label, lateness, `stalled`, how a stale fix is drawn.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol

from .gtfs.fleet import metres
from .gtfs.realtime import position_of, vehicle_time
from .gtfs.schedule import next_by_timetable, update_delays
from .transport_vic import TIME_ZONE, route_code  # noqa: F401  (route_code re-exported)
from .transport_vic import newest_fix as feed_newest

MAX_FIRST_AGE_S = 300.0
FINISHED_AGE_S = 300.0
# Fix age at which the last speed gives way to the timed rule (Lee 2026-11-01, decision 2): on 25 gaps of 10-30 minutes
# that came back, p50 2,033 m timed against 9,285 m at the last speed. A consumer's to apply.
TIMED_AFTER_S = 600.0
MAX_FIX_MS = 70.0
CONFIRM_S = 1800.0
# Replay: 341 times an answer missed a running trip and it was back within 2 minutes; never longer. The answers in the
# capture are about 60 s apart.
UPDATES_GONE_S = 180.0
# A run with no row is timed once its fix is this old (decision 1). A consumer's to apply.
ROW_GONE_TIMED_S = 120.0
FROZEN_FEED_S = 180.0
FROZEN_FEED_ROWS = 2
CANCELED = 3


def service_number(trip_id: str) -> str | None:
    """'01-ABY--10-T0-8605' -> '8605'; None when the id does not end in a number."""
    tail = trip_id.rsplit("-", 1)[-1] if trip_id else ""
    return tail if tail.isdigit() else None


def line_prefix(trip_id: str) -> str | None:
    """'01-GEL--1-T0-8751' -> '01-GEL'; None without the '--'."""
    head, sep, _ = (trip_id or "").partition("--")
    return head if sep and head else None


def identity(number: str, start_date: str | None) -> str:
    """'8605', '20261020' -> '8605-20'; the bare number with no usable start date."""
    sd = (start_date or "").strip()
    if len(sd) == 8 and sd.isdigit():
        return f"{number}-{int(sd[6:8])}"
    return number


class Origins:
    """The GTFS service day's origin as an epoch: local noon less 12 hours, the only reading that holds across a
    daylight-saving change. Callable: start_date ('YYYYMMDD') -> epoch, or None for a date that does not parse."""

    def __init__(self, time_zone: str = TIME_ZONE) -> None:
        self.time_zone = time_zone
        self._zone = None
        self._cache: dict[str, float | None] = {}

    def __call__(self, start_date: str) -> float | None:
        if start_date not in self._cache:
            if len(self._cache) > 32:
                self._cache.clear()
            if self._zone is None:
                from zoneinfo import ZoneInfo
                self._zone = ZoneInfo(self.time_zone)
            try:
                day = dt.datetime.strptime(start_date, "%Y%m%d")
                noon = dt.datetime(day.year, day.month, day.day, 12, tzinfo=self._zone)
                self._cache[start_date] = noon.timestamp() - 43200.0
            except ValueError:
                self._cache[start_date] = None
        return self._cache[start_date]


def terminal_forecast(rows: list, delays: Mapping[int, int]) -> float | None:
    """When the last stop's forecast falls: its scheduled arrival plus the last delay known. None without a timetable."""
    if not rows:
        return None
    last = 0
    for seq, *_ in rows:
        if seq in delays:
            last = delays[seq]
    seq, stop, arrival, departure = rows[-1]
    when = arrival if arrival is not None else departure
    return None if when is None else when + last


def next_stop(rows: list, delays: Mapping[int, int], skipped: Iterable[int], now: float):
    """(stop_sequence, stop_id, delay, estimated arrival epoch) of the run's next stop, or None (D5): the timetable plus
    the update's delay, the origin never next, the origin's delay still counting for the stop after it."""
    if not rows:
        return None
    ahead = rows[1:] if len(rows) > 1 else rows
    known = dict(delays)
    if rows[0][0] in known and ahead[0][0] not in known:
        known[ahead[0][0]] = known[rows[0][0]]
    return next_by_timetable(ahead, known, skipped, now)


def feed_frozen(positions: Any, now: float, whole: tuple[float | None, int] | None = None) -> bool:
    """Every dated fix in the feed old at once, with at least FROZEN_FEED_ROWS of them. `whole` is the WHOLE feed's
    (newest, count) where the positions are one consumer's share (TransportVicClient.feed_newest); without it, these
    positions are read."""
    newest, count = whole if whole is not None else feed_newest(positions)
    return newest is not None and count >= FROZEN_FEED_ROWS and now - newest > FROZEN_FEED_S


class Timetable(Protocol):
    """What VLineRuns asks of a consumer's timetable. Duck-typed: any object with these will do."""

    # Changes (is another object) whenever the timetable does: the twin index is rebuilt then.
    token: Any

    def __contains__(self, trip_id: object) -> bool: ...

    def twin_candidates(self) -> Iterable[str]: ...       # the trips a twin is looked up among

    def runs_on(self, trip_id: str, start_date: str) -> bool: ...

    def stop_rows(self, trip_id: str) -> list: ...        # [(seq, stop_id, arrival s or None, departure s or None)]


@dataclass
class Run:
    trip_id: str
    start: str
    number: str
    route_id: str
    lat: float | None = None        # the last accepted fix
    lon: float | None = None
    when: float | None = None
    observed: Any = None
    rows: list = field(default_factory=list)
    delays: dict = field(default_factory=dict)
    skipped: set = field(default_factory=set)
    gone_since: float | None = None  # since when good answers have not carried it
    cand: list = field(default_factory=list)  # [(lat, lon, time)] refused in a row
    table_id: str | None = None      # the timetable's trip: trip_id, or its twin (VLineRuns.table_id)


@dataclass(frozen=True)
class Report:
    """One run to draw this poll: its key (identity), its state, and whether its row is gone or the feed frozen."""

    key: str
    run: Run
    held: bool


_UNSET = object()


class VLineRuns:
    """Per consumer and config entry: each running trip's last accepted fix, its timetable rows and its last delays.

    `origin`: start_date -> the service day's origin epoch (Origins() in Melbourne when None)."""

    def __init__(self, origin: Callable[[str], float | None] | None = None) -> None:
        self.runs: dict[str, Run] = {}
        self._origin = origin if origin is not None else Origins()
        self._twin_src: Any = _UNSET
        self._twin_idx: dict[tuple[str, str], list[str]] = {}

    def twins(self, timetable: Timetable) -> dict[tuple[str, str], list[str]]:
        """(line prefix, service number) -> the timetable's trip ids, built once per copy."""
        token = timetable.token
        if token is not self._twin_src:
            idx: dict[tuple[str, str], list[str]] = {}
            for trip in timetable.twin_candidates():
                prefix, number = line_prefix(trip), service_number(trip)
                if prefix and number:
                    idx.setdefault((prefix, number), []).append(trip)
            self._twin_src, self._twin_idx = token, idx
        return self._twin_idx

    def table_id(self, trip_id: str, start_date: str, timetable: Timetable) -> str | None:
        """The timetable trip for a live trip: itself when the timetable has it, else the ONE trip with its line prefix
        and service number whose service runs on start_date (the module docstring); None when there is none or more
        than one."""
        if trip_id in timetable:
            return trip_id
        prefix, number = line_prefix(trip_id), service_number(trip_id)
        if not prefix or not number:
            return None
        hits = [t for t in self.twins(timetable).get((prefix, number), ())
                if t in timetable and timetable.runs_on(t, start_date)]
        return hits[0] if len(hits) == 1 else None

    def rows(self, trip_id: str, start_date: str, timetable: Timetable) -> list:
        """[(sequence, stop_id, arrival epoch, departure epoch)] for the timetable trip on this start date."""
        table = timetable.stop_rows(trip_id)
        if not table:
            return []
        origin = self._origin(start_date)
        if origin is None:
            return []
        return [(seq, stop, None if a is None else origin + a, None if d is None else origin + d)
                for seq, stop, a, d in table]

    def run_rows(self, run: Run, timetable: Timetable) -> None:
        """Fill a run's timetable rows (and its timetable trip) while it has none."""
        if run.rows:
            return
        run.table_id = self.table_id(run.trip_id, run.start, timetable)
        run.rows = self.rows(run.table_id, run.start, timetable) if run.table_id else []

    @staticmethod
    def note_update(run: Run, update: Any, answered: bool, now: float) -> None:
        if update is not None:
            run.gone_since = None
            delays, skipped = update_delays(update)
            # An update with no stops says nothing about the delay.
            if delays or skipped:
                run.delays, run.skipped = delays, skipped
        elif answered and run.gone_since is None:
            run.gone_since = now

    @staticmethod
    def accept(run: Run, lat: float, lon: float, stamp: float | None, observed: Any) -> bool:
        """Decision 5. True when the run's fix is now this one or stands unchanged by choice: False only when this row
        was refused."""
        if run.lat is None or run.when is None or stamp is None:
            run.lat, run.lon, run.when, run.observed, run.cand = lat, lon, stamp, observed, []
            return True
        if (lat, lon) == (run.lat, run.lon):
            if stamp > run.when:
                run.when, run.observed = stamp, observed
            run.cand = []
            return True
        gap = stamp - run.when
        if gap <= 0:
            return False
        if metres(run.lat, run.lon, lat, lon) / gap <= MAX_FIX_MS:
            run.lat, run.lon, run.when, run.observed, run.cand = lat, lon, stamp, observed, []
            return True
        cand = run.cand
        if not cand or stamp > cand[-1][2]:
            last = cand[-1] if cand else None
            if last is not None and metres(last[0], last[1], lat, lon) / (stamp - last[2]) <= MAX_FIX_MS:
                cand.append((lat, lon, stamp))
            else:
                run.cand = cand = [(lat, lon, stamp)]
            if stamp - cand[0][2] >= CONFIRM_S:
                run.lat, run.lon, run.when, run.observed, run.cand = lat, lon, stamp, observed, []
                return True
        return False

    @staticmethod
    def ended(run: Run, update: Any, now: float) -> bool:
        """A run with no row: whether it stops being reported."""
        if run.when is None:
            return True
        if run.gone_since is not None and now - run.gone_since >= UPDATES_GONE_S:
            return True
        if update is not None and update.trip.schedule_relationship == CANCELED:
            return True
        forecast = terminal_forecast(run.rows, run.delays)
        return forecast is None or forecast < now

    def update(self, positions: Any, updates: Any, now: float, timetable: Timetable,
               whole: tuple[float | None, int] | None = None) -> tuple[list[Report], dict[str, int]]:
        """One poll: the consumer's two FeedMessages -> (reports, skipped counts by reason). `positions` must not be
        None (a consumer checks that the positions arrived; an EMPTY feed is not an outage: the runs are reported on).
        `updates` is None when that file failed: each run keeps its last delays. `whole`: feed_frozen. Blocking on the
        first run of a date (the zone file): run in the executor."""
        answered = updates is not None
        reports: list[Report] = []
        skipped: dict[str, int] = {}

        def skip(reason: str) -> None:
            skipped[reason] = skipped.get(reason, 0) + 1

        by_run: dict[tuple[str, str], Any] = {}
        for ent in getattr(updates, "entity", ()) or ():
            if ent.HasField("trip_update"):
                trip = ent.trip_update.trip
                by_run.setdefault((trip.trip_id.strip(), trip.start_date.strip()), ent.trip_update)

        frozen = feed_frozen(positions, now, whole)
        if frozen:
            skip("feed_frozen")
        seen: set[str] = set()
        for ent in positions.entity:
            if not ent.HasField("vehicle"):
                continue
            v = ent.vehicle
            trip_id = v.trip.trip_id.strip()
            start = v.trip.start_date.strip()
            number = service_number(trip_id)
            if not number:
                skip("no_number")
                continue
            key = identity(number, start)
            if key in seen:
                skip("duplicate")
                continue
            pos = position_of(v)
            if pos is None:
                # Not `seen`: a run already known goes on below.
                skip("no_position")
                continue
            when = vehicle_time(v)
            stamp = when.timestamp() if when is not None else None
            age = None if stamp is None else now - stamp
            run = self.runs.get(key)
            if run is None and age is not None and age > MAX_FIRST_AGE_S:
                skip("stale_first_sight")
                continue
            if run is None:
                run = Run(trip_id, start, number, v.trip.route_id.strip())
            self.run_rows(run, timetable)
            self.note_update(run, by_run.get((trip_id, start)), answered, now)
            forecast = terminal_forecast(run.rows, run.delays)
            if forecast is not None and forecast < now and age is not None and age > FINISHED_AGE_S:
                self.runs.pop(key, None)
                skip("finished")
                continue
            if not self.accept(run, pos[0], pos[1], stamp, when):
                skip("fix_refused")
            self.runs[key] = run
            seen.add(key)
            reports.append(Report(key, run, frozen))

        for key, run in list(self.runs.items()):
            if key in seen:
                continue
            update = by_run.get((run.trip_id, run.start))
            self.note_update(run, update, answered, now)
            self.run_rows(run, timetable)
            if self.ended(run, update, now):
                del self.runs[key]
                skip("ended")
                continue
            skip("held")
            reports.append(Report(key, run, True))
        return reports, skipped
