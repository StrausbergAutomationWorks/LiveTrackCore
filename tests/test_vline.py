"""Tests for saw_livetrack.vline: V/Line's feed rules, moved from Live Track Intercity Rail's sources/vline.py in 0.2.38
(backlog 337). Every case is a measured finding or a decision of Lee's quoted in the module docstring; Intercity Rail's
tests/test_vline_source.py runs the same rules through its adapter. Run: python -m unittest tests.test_vline
"""

import datetime as dt
import os
import sys
import unittest
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

from saw_livetrack import vline as V  # noqa: E402

MEL = ZoneInfo("Australia/Melbourne")
START = "20261020"
ORIGIN = dt.datetime(2026, 10, 20, 12, tzinfo=MEL).timestamp() - 43200.0
TRIP = "01-ABY--10-T0-8605"
ROUTE = "aus:vic:vic-01-ABY:"
# at 10:00, 11:00-11:14, 12:00, 13:00, seconds from the origin
ROWS = [(1, "A", 36000, 36000), (2, "B", 39600, 40440), (3, "C", 43200, 43200), (4, "D", 46800, 46800)]
T = lambda h, m=0, s=0: ORIGIN + h * 3600 + m * 60 + s  # noqa: E731
A, B = (-37.8, 145.0), (-37.0, 145.0)


class TT:
    """A consumer's timetable behind V.Timetable's four names."""

    def __init__(self, tables, runs=None):
        self.tables, self.runs, self.token = dict(tables), dict(runs or {}), object()
        self.candidate_reads = 0

    def __contains__(self, trip):
        return trip in self.tables

    def twin_candidates(self):
        self.candidate_reads += 1
        return list(self.tables)

    def runs_on(self, trip, start):
        return start in self.runs.get(trip, ())

    def stop_rows(self, trip):
        return self.tables.get(trip, [])


def tt():
    return TT({TRIP: ROWS}, {TRIP: {START}})


def positions(*vehicles):
    """Each: (trip, start, lat, lon, time[, route])."""
    f = pb.FeedMessage()
    f.header.gtfs_realtime_version = "2.0"
    for i, row in enumerate(vehicles):
        trip, start, lat, lon, ts = row[:5]
        e = f.entity.add()
        e.id = str(i)
        e.vehicle.trip.trip_id = trip
        e.vehicle.trip.start_date = start
        e.vehicle.trip.route_id = row[5] if len(row) > 5 else ROUTE
        if lat is not None:
            e.vehicle.position.latitude = lat
            e.vehicle.position.longitude = lon
        if ts is not None:
            e.vehicle.timestamp = int(ts)
    return f


def updates(*stops, trip=TRIP, start=START, relationship=None):
    """Each stop: (sequence, relationship, arrival delay)."""
    f = pb.FeedMessage()
    f.header.gtfs_realtime_version = "2.0"
    e = f.entity.add()
    e.id = "u"
    u = e.trip_update
    u.trip.trip_id = trip
    u.trip.start_date = start
    if relationship is not None:
        u.trip.schedule_relationship = relationship
    for seq, rel, delay in stops:
        s = u.stop_time_update.add()
        s.stop_sequence = seq
        if rel:
            s.schedule_relationship = rel
        if delay is not None:
            s.arrival.delay = delay
    return f


def empty():
    f = pb.FeedMessage()
    f.header.gtfs_realtime_version = "2.0"
    return f


class Constants(unittest.TestCase):
    def test_measured_values_unchanged_by_the_move(self):
        self.assertEqual((V.MAX_FIRST_AGE_S, V.FINISHED_AGE_S, V.TIMED_AFTER_S, V.MAX_FIX_MS, V.CONFIRM_S,
                          V.UPDATES_GONE_S, V.ROW_GONE_TIMED_S, V.FROZEN_FEED_S, V.FROZEN_FEED_ROWS, V.CANCELED),
                         (300.0, 300.0, 600.0, 70.0, 1800.0, 180.0, 120.0, 180.0, 2, 3))

    def test_melbourne(self):
        self.assertEqual(V.TIME_ZONE, "Australia/Melbourne")


class Helpers(unittest.TestCase):
    def test_service_number(self):
        self.assertEqual(V.service_number(TRIP), "8605")
        for bad in ("", "01-ABY--10-T0-", "01-ABY--10-T0-86x5", None):
            self.assertIsNone(V.service_number(bad), bad)

    def test_line_prefix(self):
        self.assertEqual(V.line_prefix("01-GEL--1-T0-8751"), "01-GEL")
        for bad in ("8751", "", "--1-T0-8751", None):
            self.assertIsNone(V.line_prefix(bad), bad)

    def test_identity(self):
        self.assertEqual(V.identity("8605", "20261020"), "8605-20")
        self.assertEqual(V.identity("8605", "20261003"), "8605-3")
        for bad in ("", None, "2026102", "2026-10-20", " 20261020x"):
            self.assertEqual(V.identity("8605", bad), "8605", bad)
        self.assertEqual(V.identity("8605", " 20261020 "), "8605-20")

    def test_route_code(self):
        self.assertEqual(V.route_code(ROUTE), "ABY")


class OriginsTest(unittest.TestCase):
    def test_noon_less_twelve_hours(self):
        o = V.Origins()
        self.assertEqual(o(START), ORIGIN)
        # Victoria went to daylight time on 2026-10-04 at 02:00: midnight's reading would be an hour out.
        dst = dt.datetime(2026, 10, 4, 12, tzinfo=MEL).timestamp() - 43200.0
        self.assertEqual(o("20261004"), dst)
        self.assertNotEqual(dst, dt.datetime(2026, 10, 4, 0, tzinfo=MEL).timestamp())

    def test_another_zone(self):
        perth = V.Origins("Australia/Perth")
        self.assertEqual(perth(START) - ORIGIN, 3 * 3600)

    def test_bad_dates(self):
        o = V.Origins()
        self.assertIsNone(o("bad"))
        self.assertIsNone(o("20261340"))

    def test_cache_bounded(self):
        o = V.Origins()
        for d in range(1, 29):
            o("202610%02d" % d)
        for d in range(1, 8):
            o("202611%02d" % d)
        self.assertLessEqual(len(o._cache), 33)
        self.assertEqual(o(START), ORIGIN)


class Forecast(unittest.TestCase):
    def rows(self):
        return [(s, stop, None if a is None else ORIGIN + a, None if d is None else ORIGIN + d) for s, stop, a, d in ROWS]

    def test_none_without_a_timetable(self):
        self.assertIsNone(V.terminal_forecast([], {}))

    def test_last_delay_known_carries_to_the_terminal(self):
        self.assertEqual(V.terminal_forecast(self.rows(), {2: 120, 3: 300}), T(13) + 300)
        self.assertEqual(V.terminal_forecast(self.rows(), {}), T(13))

    def test_arrival_before_departure(self):
        rows = self.rows()[:-1] + [(4, "D", T(13), T(13, 10))]
        self.assertEqual(V.terminal_forecast(rows, {4: 60}), T(13) + 60)

    def test_departure_when_no_arrival(self):
        rows = self.rows()[:-1] + [(4, "D", None, T(13, 5))]
        self.assertEqual(V.terminal_forecast(rows, {}), T(13, 5))
        self.assertIsNone(V.terminal_forecast(self.rows()[:-1] + [(4, "D", None, None)], {}))


class NextStop(unittest.TestCase):
    def rows(self):
        return [(s, stop, ORIGIN + a, ORIGIN + d) for s, stop, a, d in ROWS]

    def test_the_origin_is_never_next(self):
        self.assertEqual(V.next_stop(self.rows(), {}, set(), T(9, 50))[:2], (2, "B"))

    def test_the_origins_delay_counts_for_the_stop_after(self):
        nxt = V.next_stop(self.rows(), {1: 600}, set(), T(10, 5))
        self.assertEqual((nxt[0], nxt[2], nxt[3]), (2, 600, T(11) + 600))

    def test_its_own_delay_wins(self):
        self.assertEqual(V.next_stop(self.rows(), {1: 600, 2: 60}, set(), T(10, 5))[2], 60)

    def test_a_skipped_stop_is_never_next(self):
        self.assertEqual(V.next_stop(self.rows(), {}, {2}, T(10, 30))[1], "C")

    def test_departure_decides_passed(self):
        # At B 11:00-11:14: still next at 11:10, passed at 11:15.
        self.assertEqual(V.next_stop(self.rows(), {}, set(), T(11, 10))[1], "B")
        self.assertEqual(V.next_stop(self.rows(), {}, set(), T(11, 15))[1], "C")

    def test_after_the_end_and_without_rows(self):
        self.assertIsNone(V.next_stop(self.rows(), {}, set(), T(13, 1)))
        self.assertIsNone(V.next_stop([], {}, set(), T(10)))

    def test_one_row(self):
        one = [(1, "A", T(10), T(10))]
        self.assertEqual(V.next_stop(one, {}, set(), T(9))[1], "A")


class Frozen(unittest.TestCase):
    def test_computed_from_the_positions(self):
        f = positions((TRIP, START, *A, T(10, 30)), ("01-GEL--1-T0-8751", START, *B, T(10, 29)))
        self.assertFalse(V.feed_frozen(f, T(10, 30) + 180))
        self.assertTrue(V.feed_frozen(f, T(10, 30) + 181))

    def test_one_row_is_a_lone_train(self):
        f = positions((TRIP, START, *A, T(10, 30)))
        self.assertFalse(V.feed_frozen(f, T(11)))
        self.assertFalse(V.feed_frozen(empty(), T(11)))

    def test_undated_and_unplaced_rows_do_not_count(self):
        f = positions((TRIP, START, *A, T(10, 30)), ("01-GEL--1-T0-8751", START, *B, None),
                      ("01-BAT--1-T0-8101", START, None, None, T(10, 30)))
        self.assertEqual(V.feed_newest(f), (T(10, 30), 1))
        self.assertFalse(V.feed_frozen(f, T(11)))

    def test_the_whole_feed_wins(self):
        share = positions((TRIP, START, *A, T(10, 30)))
        self.assertTrue(V.feed_frozen(share, T(11), whole=(T(10, 30), 9)))
        fresh = positions((TRIP, START, *A, T(10, 59)), ("01-GEL--1-T0-8751", START, *B, T(10, 59)))
        self.assertFalse(V.feed_frozen(fresh, T(11, 30), whole=(T(11, 29), 9)))
        self.assertFalse(V.feed_frozen(share, T(11), whole=(None, 0)))
        self.assertFalse(V.feed_frozen(share, T(11), whole=(T(10, 30), 1)))


class Accept(unittest.TestCase):
    def run_(self):
        return V.Run(TRIP, START, "8605", ROUTE)

    def test_first_fix(self):
        r = self.run_()
        self.assertTrue(V.VLineRuns.accept(r, *A, T(10), "o"))
        self.assertEqual((r.lat, r.lon, r.when, r.observed), (*A, T(10), "o"))

    def test_same_place_newer_time(self):
        r = self.run_()
        V.VLineRuns.accept(r, *A, T(10), "o")
        self.assertTrue(V.VLineRuns.accept(r, *A, T(10, 1), "p"))
        self.assertEqual((r.when, r.observed), (T(10, 1), "p"))
        self.assertTrue(V.VLineRuns.accept(r, *A, T(10, 0, 30), "q"))
        self.assertEqual(r.when, T(10, 1))

    def test_older_fix_refused_no_vote(self):
        r = self.run_()
        V.VLineRuns.accept(r, *A, T(10, 5), "o")
        self.assertFalse(V.VLineRuns.accept(r, -37.79, 145.0, T(10, 4), "x"))
        self.assertEqual((r.when, r.cand), (T(10, 5), []))

    def test_a_flip_is_refused_and_confirmed_after_confirm_s(self):
        r = self.run_()
        V.VLineRuns.accept(r, *A, T(10), "o")
        # 89 km in 60 s: refused
        self.assertFalse(V.VLineRuns.accept(r, *B, T(10, 1), "x"))
        self.assertEqual((r.lat, len(r.cand)), (A[0], 1))
        self.assertFalse(V.VLineRuns.accept(r, *B, T(10, 20), "y"))
        self.assertEqual(len(r.cand), 2)
        self.assertTrue(V.VLineRuns.accept(r, *B, T(10, 31), "z"))
        self.assertEqual((r.lat, r.when, r.cand), (B[0], T(10, 31), []))

    def test_candidates_that_disagree_start_again(self):
        r = self.run_()
        V.VLineRuns.accept(r, *A, T(10), "o")
        V.VLineRuns.accept(r, *B, T(10, 1), "x")
        self.assertFalse(V.VLineRuns.accept(r, -36.0, 145.0, T(10, 2), "y"))
        self.assertEqual(r.cand, [(-36.0, 145.0, T(10, 2))])

    def test_within_70_ms_accepted(self):
        r = self.run_()
        V.VLineRuns.accept(r, *A, T(10), "o")
        # 0.03 deg of latitude, ~3.3 km in 60 s: 55.6 m/s
        self.assertTrue(V.VLineRuns.accept(r, -37.77, 145.0, T(10, 1), "p"))
        self.assertEqual(r.lat, -37.77)

    def test_no_time_takes_the_fix(self):
        r = self.run_()
        V.VLineRuns.accept(r, *A, T(10), "o")
        self.assertTrue(V.VLineRuns.accept(r, *B, None, None))
        self.assertEqual((r.lat, r.when), (B[0], None))


class Update(unittest.TestCase):
    def setUp(self):
        self.r = V.VLineRuns()
        self.tt = tt()

    def test_a_run_is_reported_with_its_timetable(self):
        reps, skipped = self.r.update(positions((TRIP, START, *A, T(10, 30))), updates((2, 0, 120)), T(10, 30, 20), self.tt)
        self.assertEqual(skipped, {})
        self.assertEqual([(x.key, x.held) for x in reps], [("8605-20", False)])
        run = reps[0].run
        self.assertEqual((run.table_id, run.rows[0], run.delays, run.number), (TRIP, (1, "A", T(10), T(10)), {2: 120}, "8605"))

    def test_stale_at_first_sight(self):
        reps, skipped = self.r.update(positions((TRIP, START, *A, T(10))), None, T(10) + 301, self.tt)
        self.assertEqual((reps, skipped, self.r.runs), ([], {"stale_first_sight": 1}, {}))
        reps, _ = self.r.update(positions((TRIP, START, *A, T(10))), None, T(10) + 300, self.tt)
        self.assertEqual(len(reps), 1)

    def test_a_missing_row_is_a_stale_fix(self):
        self.r.update(positions((TRIP, START, *A, T(10, 30))), updates((2, 0, 0)), T(10, 30), self.tt)
        reps, skipped = self.r.update(empty(), updates((2, 0, 0)), T(10, 40), self.tt)
        self.assertEqual(skipped, {"held": 1})
        self.assertEqual([(x.key, x.held, x.run.when, round(x.run.lat, 4)) for x in reps], [("8605-20", True, T(10, 30), A[0])])

    def test_ends_when_the_terminals_forecast_passes(self):
        self.r.update(positions((TRIP, START, *A, T(12, 50))), updates((4, 0, 300)), T(12, 50), self.tt)
        self.assertEqual(len(self.r.update(empty(), updates((4, 0, 300)), T(13, 4), self.tt)[0]), 1)
        reps, skipped = self.r.update(empty(), updates((4, 0, 300)), T(13, 6), self.tt)
        self.assertEqual((reps, skipped, self.r.runs), ([], {"ended": 1}, {}))

    def test_ends_when_cancelled(self):
        self.r.update(positions((TRIP, START, *A, T(10, 30))), updates((2, 0, 0)), T(10, 30), self.tt)
        reps, skipped = self.r.update(empty(), updates(relationship=V.CANCELED), T(10, 31), self.tt)
        self.assertEqual((reps, skipped), ([], {"ended": 1}))

    def test_ends_when_good_answers_stop_carrying_it(self):
        self.r.update(positions((TRIP, START, *A, T(10, 30))), updates((2, 0, 0)), T(10, 30), self.tt)
        other = updates(trip="01-GEL--1-T0-8751")
        self.assertEqual(len(self.r.update(empty(), other, T(10, 31), self.tt)[0]), 1)
        self.assertEqual(self.r.runs["8605-20"].gone_since, T(10, 31))
        self.assertEqual(len(self.r.update(empty(), other, T(10, 31) + 179, self.tt)[0]), 1)
        self.assertEqual(self.r.update(empty(), other, T(10, 31) + 180, self.tt)[1], {"ended": 1})

    def test_a_failed_answer_never_counts(self):
        self.r.update(positions((TRIP, START, *A, T(10, 30))), updates((2, 0, 60)), T(10, 30), self.tt)
        for k in range(10):
            reps, _ = self.r.update(empty(), None, T(10, 31 + k), self.tt)
        self.assertEqual(len(reps), 1)
        self.assertIsNone(self.r.runs["8605-20"].gone_since)
        self.assertEqual(self.r.runs["8605-20"].delays, {2: 60})

    def test_an_update_with_no_stops_keeps_the_delays(self):
        self.r.update(positions((TRIP, START, *A, T(10, 30))), updates((2, 0, 60)), T(10, 30), self.tt)
        self.r.update(positions((TRIP, START, *A, T(10, 31))), updates(), T(10, 31), self.tt)
        self.assertEqual(self.r.runs["8605-20"].delays, {2: 60})

    def test_finished(self):
        self.r.update(positions((TRIP, START, *A, T(12, 50))), None, T(12, 50), self.tt)
        # Past the terminal's forecast (13:00) with a fix 180 s old: still drawn.
        reps, skipped = self.r.update(positions((TRIP, START, *A, T(12, 58))), None, T(13, 1), self.tt)
        self.assertEqual(len(reps), 1)
        reps, skipped = self.r.update(positions((TRIP, START, *A, T(12, 58))), None, T(12, 58) + 300, self.tt)
        self.assertEqual(len(reps), 1)
        reps, skipped = self.r.update(positions((TRIP, START, *A, T(12, 58))), None, T(12, 58) + 301, self.tt)
        self.assertEqual((reps, skipped, self.r.runs), ([], {"finished": 1}, {}))

    def test_a_flip_is_counted_and_the_run_goes_on(self):
        self.r.update(positions((TRIP, START, *A, T(10, 30))), None, T(10, 30), self.tt)
        reps, skipped = self.r.update(positions((TRIP, START, *B, T(10, 31))), None, T(10, 31), self.tt)
        self.assertEqual(skipped, {"fix_refused": 1})
        self.assertEqual((round(reps[0].run.lat, 4), reps[0].held), (A[0], False))

    def test_a_frozen_feed_holds_every_report(self):
        f = positions((TRIP, START, *A, T(10, 30)))
        reps, skipped = self.r.update(f, None, T(10, 30, 5), self.tt, whole=(T(10, 26), 5))
        self.assertEqual((skipped, reps[0].held), ({"feed_frozen": 1}, True))
        reps, skipped = self.r.update(f, None, T(10, 30, 5), self.tt, whole=(T(10, 30), 5))
        self.assertEqual((skipped, reps[0].held), ({}, False))

    def test_rows_that_name_no_run(self):
        f = positions(("01-ABY--10-T0-", START, *A, T(10, 30)), (TRIP, START, *A, T(10, 30)),
                      (TRIP, START, *B, T(10, 30)), ("01-ABY--10-T0-8606", START, None, None, T(10, 30)))
        reps, skipped = self.r.update(f, None, T(10, 30), self.tt)
        self.assertEqual(skipped, {"no_number": 1, "duplicate": 1, "no_position": 1})
        self.assertEqual([x.key for x in reps], ["8605-20"])

    def test_no_position_for_a_known_run_holds_it(self):
        self.r.update(positions((TRIP, START, *A, T(10, 30))), None, T(10, 30), self.tt)
        reps, skipped = self.r.update(positions((TRIP, START, None, None, T(10, 31))), None, T(10, 31), self.tt)
        self.assertEqual(skipped, {"no_position": 1, "held": 1})
        self.assertEqual([(x.key, x.held) for x in reps], [("8605-20", True)])

    def test_report_order_rows_then_held(self):
        g = "01-GEL--1-T0-8751"
        two = TT({TRIP: ROWS, g: ROWS}, {TRIP: {START}, g: {START}})
        self.r.update(positions((g, START, *A, T(10, 30)), (TRIP, START, *A, T(10, 30))), None, T(10, 30), two)
        reps, _ = self.r.update(positions((TRIP, START, *A, T(10, 31))), None, T(10, 31), two)
        self.assertEqual([(x.key, x.held) for x in reps], [("8605-20", False), ("8751-20", True)])

    def test_an_unknown_trip_without_a_twin(self):
        reps, _ = self.r.update(positions(("01-GEL--1-T0-8751", START, *A, T(10, 30))), None, T(10, 30), self.tt)
        self.assertEqual((reps[0].run.table_id, reps[0].run.rows), (None, []))


class Fallback(unittest.TestCase):
    LIVE = "01-ABY--1-T0-8605"
    TWIN = "01-ABY--5-T0-8605"

    def test_the_one_twin_running_that_day(self):
        t = TT({self.TWIN: ROWS, "01-ABY--6-T0-8605": ROWS, "01-GEL--5-T0-8605": ROWS},
               {self.TWIN: {"20261020"}, "01-ABY--6-T0-8605": {"20261024"}, "01-GEL--5-T0-8605": {"20261020"}})
        r = V.VLineRuns()
        self.assertEqual(r.table_id(self.LIVE, "20261020", t), self.TWIN)
        self.assertEqual(r.table_id(self.LIVE, "20261024", t), "01-ABY--6-T0-8605")
        self.assertIsNone(r.table_id(self.LIVE, "20261021", t))

    def test_itself_first(self):
        t = TT({self.LIVE: ROWS, self.TWIN: ROWS}, {self.TWIN: {START}})
        self.assertEqual(V.VLineRuns().table_id(self.LIVE, START, t), self.LIVE)

    def test_two_twins_running_is_none(self):
        t = TT({self.TWIN: ROWS, "01-ABY--7-T0-8605": ROWS}, {self.TWIN: {START}, "01-ABY--7-T0-8605": {START}})
        self.assertIsNone(V.VLineRuns().table_id(self.LIVE, START, t))

    def test_no_prefix_or_number_is_none(self):
        t = TT({self.TWIN: ROWS}, {self.TWIN: {START}})
        for bad in ("8605", "01-ABY--1-T0-", ""):
            self.assertIsNone(V.VLineRuns().table_id(bad, START, t), bad)

    def test_the_index_is_built_once_per_timetable(self):
        t = TT({self.TWIN: ROWS}, {self.TWIN: {START}})
        r = V.VLineRuns()
        r.table_id(self.LIVE, START, t)
        r.table_id(self.LIVE, START, t)
        self.assertEqual(t.candidate_reads, 1)
        t.token = object()
        r.table_id(self.LIVE, START, t)
        self.assertEqual(t.candidate_reads, 2)

    def test_the_run_takes_the_twins_rows_and_keeps_its_live_id(self):
        t = TT({self.TWIN: ROWS}, {self.TWIN: {START}})
        r = V.VLineRuns()
        reps, _ = r.update(positions((self.LIVE, START, *A, T(10, 30))), updates((2, 0, 90), trip=self.LIVE), T(10, 30), t)
        run = reps[0].run
        self.assertEqual((run.trip_id, run.table_id, len(run.rows), run.delays), (self.LIVE, self.TWIN, 4, {2: 90}))

    def test_rows_on_a_bad_date_or_unknown_trip(self):
        r = V.VLineRuns()
        self.assertEqual(r.rows(TRIP, "bad", tt()), [])
        self.assertEqual(r.rows("nope", START, tt()), [])

    def test_a_given_origin_is_used(self):
        r = V.VLineRuns(origin=lambda d: 1000.0)
        self.assertEqual(r.rows(TRIP, START, tt())[0], (1, "A", 37000.0, 37000.0))


if __name__ == "__main__":
    unittest.main()
