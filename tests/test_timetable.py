"""saw_livetrack.timetable (0.2.34): where a train is by its calls' times alone. Hoisted from Live Track Intercity
Rail's sources/entur_et.py (SJ Nord); these pin the rules its docstring states."""

import datetime as dt
import os
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from saw_livetrack import timetable as tt  # noqa: E402

UTC = dt.timezone.utc


def call(aa=None, ad=None, **kw):
    c = {"aimed_arrival": aa, "aimed_departure": ad}
    c.update(kw)
    return c


class ServiceBase(unittest.TestCase):
    def test_noon_less_twelve_hours(self):
        dub = ZoneInfo("Europe/Dublin")
        self.assertEqual(tt.service_base(dt.date(2026, 11, 9), dub), dt.datetime(2026, 11, 9, tzinfo=UTC).timestamp())
        # Summer time: the service day starts at 23:00 UTC the day before.
        self.assertEqual(tt.service_base(dt.date(2026, 7, 1), dub), dt.datetime(2026, 6, 30, 23, tzinfo=UTC).timestamp())
        # The day the clocks go back (2026-10-25, at 01:00 UTC): noon is GMT, so the zero is 00:00 UTC, which was
        # 01:00 Irish summer time (not local midnight: GTFS's own definition).
        self.assertEqual(tt.service_base(dt.date(2026, 10, 25), dub),
                         dt.datetime(2026, 10, 25, 0, tzinfo=UTC).timestamp())


class CallTimes(unittest.TestCase):
    def test_aimed_with_the_known_delay_carried(self):
        cs = [call(None, 1000, expected_departure=1120), call(2000, 2060), call(3000, None)]
        out = tt.call_times(cs)
        self.assertEqual([(a, d, k) for _, a, d, k in out], [(1120, 1120, False), (2120, 2180, False),
                                                             (3120, 3120, False)])

    def test_actual_beats_expected_and_moves_the_delay(self):
        cs = [call(None, 1000), call(2000, 2060, expected_arrival=2300, actual_arrival=2200), call(3000, None)]
        out = tt.call_times(cs)
        self.assertEqual(out[1][1], 2200)
        self.assertTrue(out[1][3])
        self.assertEqual(out[2][1], 3200)      # 200 s late carried from the actual arrival

    def test_run_floor_refuses_an_impossible_arrival(self):
        # Scheduled 1000 s for the stretch; arriving 200 s after leaving is under RUN_FLOOR (0.3).
        cs = [call(None, 1000), call(2000, 2060, expected_arrival=1200)]
        out = tt.call_times(cs)
        self.assertEqual(out[1][1], 2000)
        self.assertFalse(out[1][3])
        cs = [call(None, 1000), call(2000, 2060, expected_arrival=1301)]   # 301 s: just over 0.3
        self.assertEqual(tt.call_times(cs)[1][1], 1301)

    def test_run_floor_measures_the_scheduled_stretch(self):
        # Left 500 s late (expected departure 1500 against 1000); the stretch is scheduled 1000 s (aimed departure to
        # aimed arrival), so an arrival under 300 s after the actual departure is refused: it takes aimed + 500.
        cs = [call(None, 1000, expected_departure=1500), call(2000, 2060, expected_arrival=1790)]
        self.assertEqual(tt.call_times(cs)[1][1], 2500)
        cs = [call(None, 1000, expected_departure=1500), call(2000, 2060, expected_arrival=1800)]
        self.assertEqual(tt.call_times(cs)[1][1], 1800)

    def test_cancelled_and_timeless_calls_are_left_out(self):
        cs = [call(None, 1000), call(1500, 1560, cancelled=True), call(None, None), call(2000, None)]
        self.assertEqual([c["aimed_arrival"] for c, *_ in tt.call_times(cs)], [None, 2000])

    def test_departure_never_before_arrival(self):
        cs = [call(None, 1000), call(2000, 2060, expected_arrival=2300)]
        _, arr, dep, _ = tt.call_times(cs)[1]
        self.assertEqual((arr, dep), (2300, 2300))


class Place(unittest.TestCase):
    times = [({}, 1000, 1000, False), ({}, 2000, 2060, False), ({}, 3000, 3000, False)]

    def test_states(self):
        p = tt.place
        self.assertIsNone(p(self.times, 1000 - tt.PRE_DEPARTURE_S - 1))
        self.assertEqual(p(self.times, 1000 - tt.PRE_DEPARTURE_S), ("standing", 0))
        self.assertEqual(p(self.times, 1500), ("running", 0))
        self.assertEqual(p(self.times, 2000), ("standing", 1))     # at the arrival time: arrived
        self.assertEqual(p(self.times, 2030), ("standing", 1))
        self.assertEqual(p(self.times, 2061), ("running", 1))
        self.assertEqual(p(self.times, 3000 + tt.FINISHED_S), ("standing", 2))
        self.assertIsNone(p(self.times, 3000 + tt.FINISHED_S + 1))
        self.assertIsNone(p([], 1500))

    def test_parameters(self):
        self.assertEqual(tt.place(self.times, 900, pre_departure_s=100), ("standing", 0))
        self.assertIsNone(tt.place(self.times, 899, pre_departure_s=100))
        self.assertIsNone(tt.place(self.times, 3011, finished_s=10))


if __name__ == "__main__":
    unittest.main()
