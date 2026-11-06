"""Tests for what backlog 319 added to saw_livetrack.gtfs: drop_skipped, update_delays, next_by_timetable, and the filter
moved here from Live Track Commuter Rail. Run:  python -m unittest tests.test_gtfs_timetable_next
"""
import datetime as dt
import io
import os
import sys
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

from saw_livetrack import gtfs  # noqa: E402
from saw_livetrack.gtfs import filter as gf  # noqa: E402
from saw_livetrack.gtfs.realtime import drop_skipped  # noqa: E402
from saw_livetrack.gtfs.schedule import next_by_timetable, update_delays  # noqa: E402


def trip_update(*stops):
    """Each stop: (sequence, relationship, arrival delay, departure delay); None for an absent field."""
    f = pb.FeedMessage()
    u = f.entity.add().trip_update
    u.trip.trip_id = "T"
    for seq, rel, arr, dep in stops:
        s = u.stop_time_update.add()
        if seq is not None:
            s.stop_sequence = seq
        s.schedule_relationship = rel
        if arr is not None:
            s.arrival.delay = arr
        if dep is not None:
            s.departure.delay = dep
    return f, u


class DropSkipped(unittest.TestCase):
    def test_removes_only_skipped(self):
        f, u = trip_update((1, 0, 0, None), (2, 1, 60, None), (3, 2, None, None), (4, 1, 60, None), (5, 0, 0, None))
        self.assertEqual(drop_skipped(f), 2)
        self.assertEqual([s.stop_sequence for s in u.stop_time_update], [1, 3, 5])

    def test_adjacent_skipped(self):
        f, u = trip_update((1, 1, 0, None), (2, 1, 0, None), (3, 1, 0, None))
        self.assertEqual(drop_skipped(f), 3)
        self.assertEqual(len(u.stop_time_update), 0)

    def test_none_and_vehicles(self):
        self.assertEqual(drop_skipped(None), 0)
        f = pb.FeedMessage()
        f.entity.add().vehicle.trip.trip_id = "V"
        self.assertEqual(drop_skipped(f), 0)

    def test_constant_is_the_enum(self):
        self.assertEqual(gtfs.SKIPPED, pb.TripUpdate.StopTimeUpdate.SKIPPED)
        self.assertIs(gtfs.drop_skipped, drop_skipped)


class UpdateDelays(unittest.TestCase):
    def test_arrival_then_departure(self):
        _, u = trip_update((1, 0, 30, 90), (2, 0, None, 120), (3, 0, None, None))
        self.assertEqual(update_delays(u), ({1: 30, 2: 120}, set()))

    def test_skipped(self):
        _, u = trip_update((1, 1, 60, None), (2, 0, 0, None))
        self.assertEqual(update_delays(u), ({1: 60, 2: 0}, {1}))

    def test_no_sequence_ignored(self):
        _, u = trip_update((None, 1, 60, None), (2, 0, 5, None))
        self.assertEqual(update_delays(u), ({2: 5}, set()))

    def test_exported(self):
        self.assertIs(gtfs.update_delays, update_delays)
        self.assertIs(gtfs.next_by_timetable, next_by_timetable)


# Stops at 1000/1010, 2000/2010, 3000/3010, 4000 (no departure at the terminal).
ROWS = [(1, "A", None, 1010), (2, "B", 2000, 2010), (3, "C", 3000, 3010), (4, "D", 4000, None)]


class NextByTimetable(unittest.TestCase):
    def test_before_the_first(self):
        self.assertEqual(next_by_timetable(ROWS, {}, set(), 500), (1, "A", 0, 1010))

    def test_on_schedule(self):
        self.assertEqual(next_by_timetable(ROWS, {}, set(), 1500), (2, "B", 0, 2000))

    def test_still_at_the_stop_until_it_leaves(self):
        self.assertEqual(next_by_timetable(ROWS, {}, set(), 2005)[0], 2)
        self.assertEqual(next_by_timetable(ROWS, {}, set(), 2009)[0], 2)

    def test_at_the_departure_it_has_gone(self):
        self.assertEqual(next_by_timetable(ROWS, {}, set(), 2010)[0], 3)

    def test_after_the_last(self):
        self.assertIsNone(next_by_timetable(ROWS, {}, set(), 4000))

    def test_delay_holds_the_stop(self):
        self.assertEqual(next_by_timetable(ROWS, {2: 600}, set(), 2500), (2, "B", 600, 2600))

    def test_delay_carried_forward(self):
        self.assertEqual(next_by_timetable(ROWS, {1: 300}, set(), 3200), (3, "C", 300, 3300))

    def test_last_known_delay_replaces(self):
        self.assertEqual(next_by_timetable(ROWS, {1: 900, 2: 60}, set(), 3000), (3, "C", 60, 3060))

    def test_no_delay_before_the_first_known(self):
        self.assertEqual(next_by_timetable(ROWS, {3: 600}, set(), 1500), (2, "B", 0, 2000))

    def test_early(self):
        self.assertEqual(next_by_timetable(ROWS, {2: -30}, set(), 1975), (2, "B", -30, 1970))

    def test_skipped_never_next(self):
        self.assertEqual(next_by_timetable(ROWS, {}, {2}, 1500), (3, "C", 0, 3000))

    def test_a_skipped_stops_delay_still_carries(self):
        self.assertEqual(next_by_timetable(ROWS, {2: 400}, {2}, 3200), (3, "C", 400, 3400))

    def test_all_skipped(self):
        self.assertIsNone(next_by_timetable(ROWS, {}, {1, 2, 3, 4}, 0))

    def test_terminal_by_arrival(self):
        self.assertEqual(next_by_timetable(ROWS, {4: 60}, set(), 4030), (4, "D", 60, 4060))

    def test_first_stop_eta_from_departure(self):
        self.assertEqual(next_by_timetable(ROWS, {1: 10}, set(), 900)[3], 1020)

    def test_a_row_with_no_times_passed_over(self):
        rows = [(1, "A", None, None), (2, "B", 2000, 2010)]
        self.assertEqual(next_by_timetable(rows, {}, set(), 0)[0], 2)

    def test_no_rows(self):
        self.assertIsNone(next_by_timetable([], {}, set(), 0))


def zip_of(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n, t in files.items():
            z.writestr(n, t)
    return out.getvalue()


class Filter(unittest.TestCase):
    """The hoist itself: the behaviour that made it worth moving. Commuter Rail's tests cover the rest until it switches."""

    FILES = {
        "routes.txt": "route_id,route_type\nR1,106\nC1,204\n",
        "trips.txt": "route_id,service_id,trip_id,shape_id\nR1,S,T1,X1\nC1,S,T2,X2\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\nT1,08:00:00,08:00:00,A,1\nT2,09:00:00,09:00:00,B,1\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\nA,Aa,-33,151\nB,Bb,-34,150\n",
        "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\nX1,-33,151,1\nX2,-34,150,1\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nS,1,1,1,1,1,1,1,20260101,20271231\n",
    }

    def read(self, raw, name):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return z.read(name).decode()

    def test_keep_route_types(self):
        raw = gf.keep_route_types(zip_of(self.FILES), ("106",))
        self.assertIn("R1", self.read(raw, "routes.txt"))
        self.assertNotIn("C1", self.read(raw, "routes.txt"))
        self.assertNotIn("T2", self.read(raw, "stop_times.txt"))

    def test_keep_service_window(self):
        raw = gf.keep_service_window(zip_of(self.FILES), today=dt.date(2026, 11, 2))
        self.assertIn("T1", self.read(raw, "trips.txt"))

    # --- services_on: a calendar_dates removal is for its own day (fixed in 0.2.26) ---------
    CAL = ("service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
           "S,1,1,1,1,1,1,1,20260101,20271231\n"
           "MON,1,0,0,0,0,0,0,20260101,20271231\n")
    WINDOW = [dt.date(2026, 11, 1), dt.date(2026, 11, 2), dt.date(2026, 11, 3)]   # Sunday to Tuesday

    def on(self, dates, cal=CAL):
        files = {"calendar.txt": cal} if cal is not None else {}
        if dates is not None:
            files["calendar_dates.txt"] = "service_id,date,exception_type\n" + dates
        with zipfile.ZipFile(io.BytesIO(zip_of(files))) as z:
            return gf.services_on(z, self.WINDOW)

    def test_removal_on_the_last_day_keeps_the_others(self):
        # Daily, removed on Tuesday only: it still runs Sunday and Monday. The old code dropped it.
        self.assertIn("S", self.on("S,20261103,2\n"))

    def test_removal_on_the_middle_day_keeps_the_others(self):
        self.assertIn("S", self.on("S,20261102,2\n"))

    def test_removal_on_every_day_drops_it(self):
        self.assertNotIn("S", self.on("S,20261101,2\nS,20261102,2\nS,20261103,2\n"))

    def test_removal_on_its_only_day_drops_it(self):
        # MON runs Monday only; removed that Monday, it runs on none of the three days.
        self.assertNotIn("MON", self.on("MON,20261102,2\n"))

    def test_removal_on_a_day_it_does_not_run_changes_nothing(self):
        self.assertIn("MON", self.on("MON,20261103,2\n"))

    def test_added_on_one_day(self):
        got = self.on("X,20261103,1\nY,20261104,1\n")
        self.assertIn("X", got)
        self.assertNotIn("Y", got)

    def test_added_and_removed_on_different_days(self):
        # Only calendar_dates: added Sunday, removed Tuesday. It runs Sunday.
        self.assertIn("X", self.on("X,20261101,1\nX,20261103,2\n", cal=None))

    def test_no_calendar_at_all(self):
        self.assertIsNone(self.on(None, cal=None))

    def test_padded_rows(self):
        # Renfe pads every field (05_SHARED_LESSONS.md B5).
        self.assertIn("S", self.on("S  ,20261103  ,2  \n"))

    def test_window_keeps_a_trip_removed_on_its_last_day(self):
        files = dict(self.FILES)
        files["calendar_dates.txt"] = "service_id,date,exception_type\nS,20261103,2\n"
        raw = gf.keep_service_window(zip_of(files), today=dt.date(2026, 11, 2))
        self.assertIn("T1", self.read(raw, "trips.txt"))

    def test_window_drops_a_trip_removed_on_all_three_days(self):
        files = dict(self.FILES)
        files["calendar_dates.txt"] = ("service_id,date,exception_type\n"
                                       "S,20261101,2\nS,20261102,2\nS,20261103,2\n")
        raw = gf.keep_service_window(zip_of(files), today=dt.date(2026, 11, 2))
        self.assertNotIn("T1", self.read(raw, "trips.txt"))

    def test_not_imported_with_gtfs(self):
        code = ("import sys; sys.path.insert(0, %r); import saw_livetrack.gtfs; "
                "print('saw_livetrack.gtfs.filter' in sys.modules)" % os.path.join(ROOT, "src"))
        import subprocess
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "False")


if __name__ == "__main__":
    unittest.main()
