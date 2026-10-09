"""Off-rail refusal (Live Track Commuter Rail SSOT 7.10, Lee 2026-10-08):
saw_livetrack.rail.RailIndex, and Fleet's `on_rail` check - a train in service
whose fix is not on rail is held at its last fix that was, for at most
REFUSED_HOLD_S, then hidden; one never seen on rail is not published."""

import math
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from saw_livetrack import gtfs  # noqa: E402
from saw_livetrack.gtfs import fleet as fleet_mod  # noqa: E402
from saw_livetrack.gtfs.realtime import Obs  # noqa: E402
from saw_livetrack.rail import RailIndex  # noqa: E402

LAT0, LON0 = 41.9, -87.9
M_LAT = 1 / 111195.0                                  # degrees per metre north
M_LON = 1 / (111195.0 * math.cos(math.radians(LAT0)))  # degrees per metre east

# A north-south track along LON0, 10 km long.
TRACK = [[(LAT0 - 0.05, LON0), (LAT0 + 0.05, LON0)]]


class Index(unittest.TestCase):

    def setUp(self):
        self.idx = RailIndex(TRACK)

    def test_distance_east_of_the_track(self):
        for m in (0, 50, 150, 299):
            d = self.idx.distance_m(LAT0, LON0 + m * M_LON, 300)
            self.assertAlmostEqual(d, m, delta=1.0)

    def test_beyond_the_limit_is_none(self):
        self.assertIsNone(self.idx.distance_m(LAT0, LON0 + 301 * M_LON, 300))
        self.assertFalse(self.idx.near(LAT0, LON0 + 2580 * M_LON, 300))
        self.assertTrue(self.idx.near(LAT0, LON0 + 200 * M_LON, 300))

    def test_past_the_end_of_a_line_counts_from_its_end(self):
        end = LAT0 + 0.05
        self.assertAlmostEqual(self.idx.distance_m(end + 100 * M_LAT, LON0, 300), 100, delta=1.0)
        self.assertIsNone(self.idx.distance_m(end + 400 * M_LAT, LON0, 300))

    def test_a_long_segment_is_found_from_every_cell_it_crosses(self):
        # one 10 km segment: the point beside its middle is far from both ends
        self.assertTrue(self.idx.near(LAT0 + 0.02, LON0 + 100 * M_LON, 300))

    def test_the_nearest_of_several_lines(self):
        idx = RailIndex(TRACK + [[(LAT0, LON0 + 1000 * M_LON), (LAT0 + 0.01, LON0 + 1000 * M_LON)]])
        self.assertAlmostEqual(idx.distance_m(LAT0, LON0 + 900 * M_LON, 300), 100, delta=1.0)

    def test_a_wide_search_reaches_beyond_neighbouring_cells(self):
        self.assertAlmostEqual(self.idx.distance_m(LAT0, LON0 + 2500 * M_LON, 3000), 2500, delta=2.0)
        end = LAT0 + 0.05
        self.assertAlmostEqual(self.idx.distance_m(end + 2500 * M_LAT, LON0, 3000), 2500, delta=2.0)

    def test_empty_and_degenerate_lines(self):
        idx = RailIndex([[(LAT0, LON0)], []])
        self.assertEqual(len(idx), 0)
        self.assertIsNone(idx.distance_m(LAT0, LON0, 300))
        self.assertEqual(len(RailIndex(TRACK)), 1)


T0 = datetime(2026, 10, 8, 22, 0, tzinfo=timezone.utc)


def ob(m_east=0.0, m_north=0.0, t=0, vid="8426", ins=True, train="835"):
    return Obs(vid, round(LAT0 + m_north * M_LAT, 6), round(LON0 + m_east * M_LON, 6),
               T0 + timedelta(seconds=t), ins, train=train if ins else None,
               line="UP-N" if ins else None, trip_id="UP-N_UN835_V2_A" if ins else None)


IDX = RailIndex(TRACK)


def on_rail(lat, lon):
    return IDX.near(lat, lon, 300)


class Refusal(unittest.TestCase):

    def setUp(self):
        self.f = gtfs.Fleet(on_rail=on_rail)

    def poll(self, *obs, t):
        return self.f.update(list(obs), 1_000_000.0 + t)

    def test_on_rail_publishes_as_before(self):
        r = self.poll(ob(m_east=20, t=0), t=0)
        self.assertEqual(r["vehicles"]["8426"]["longitude"], round(LON0 + 20 * M_LON, 6))
        self.assertEqual(r["off_rail"], 0)

    def test_off_rail_is_held_at_the_last_fix_on_rail(self):
        self.poll(ob(m_north=0, t=0), t=0)
        self.poll(ob(m_north=500, t=30), t=30)
        rec = self.poll(ob(m_east=600, m_north=900, t=60), t=60)["vehicles"]["8426"]
        self.assertEqual((rec["latitude"], rec["longitude"]), (round(LAT0 + 500 * M_LAT, 6), LON0))
        self.assertEqual(rec["observed_at"], (T0 + timedelta(seconds=30)).isoformat())
        self.assertEqual(rec["marker_label"], "835")

    def test_the_count(self):
        self.poll(ob(t=0), t=0)
        self.assertEqual(self.poll(ob(m_east=600, t=30), t=30)["off_rail"], 1)

    def test_held_for_the_hold_then_hidden(self):
        self.poll(ob(t=0), t=0)
        hold = fleet_mod.REFUSED_HOLD_S
        r = self.poll(ob(m_east=600, t=hold), t=hold)
        self.assertIn("8426", r["vehicles"])
        r = self.poll(ob(m_east=600, t=hold + 1), t=hold + 1)
        self.assertNotIn("8426", r["vehicles"])
        self.assertIn("8426", r["departed"])
        self.assertEqual(r["trains_in_service"], 1)        # still counted

    def test_the_hold_runs_from_the_last_fix_on_rail(self):
        hold = fleet_mod.REFUSED_HOLD_S
        self.poll(ob(t=0), t=0)
        self.poll(ob(m_north=300, t=hold - 10), t=hold - 10)
        r = self.poll(ob(m_east=600, t=2 * hold - 20), t=2 * hold - 20)
        self.assertIn("8426", r["vehicles"])

    def test_never_seen_on_rail_is_not_published(self):
        r = self.poll(ob(m_east=2580, t=0), t=0)
        self.assertEqual(r["vehicles"], {})
        self.assertEqual(r["trains_in_service"], 1)

    def test_back_on_rail_is_published_where_it_is(self):
        self.poll(ob(t=0), t=0)
        self.poll(ob(m_east=600, t=30), t=30)
        rec = self.poll(ob(m_north=2000, t=60), t=60)["vehicles"]["8426"]
        self.assertEqual(rec["latitude"], round(LAT0 + 2000 * M_LAT, 6))

    def test_a_refused_train_is_never_called_stalled(self):
        # standing far from every stop for long: stalled, unless the fix is refused
        static = {"v": gtfs.STATIC_VERSION, "stop_pos": {"FAR": [LAT0 + 0.04, LON0]}}
        self.f.update([ob(t=0)], 1_000_000.0, static=static)
        r = self.f.update([ob(t=200)], 1_000_200.0, static=static)
        self.assertTrue(r["vehicles"]["8426"].get("stalled"))
        r = self.f.update([ob(m_east=600, t=400)], 1_000_400.0, static=static)
        self.assertNotIn("stalled", r["vehicles"]["8426"])

    def test_equipment_not_in_service_is_not_checked(self):
        self.poll(ob(vid="9001", ins=False, m_east=2000, t=0), t=0)
        r = self.poll(ob(vid="9001", ins=False, m_east=2000, m_north=200, t=30), t=30)
        self.assertIn("9001", r["vehicles"])
        self.assertEqual(r["off_rail"], 0)

    def test_no_check_without_rail(self):
        f = gtfs.Fleet()
        self.assertIsNone(f.on_rail)
        r = f.update([ob(m_east=2580, t=0)], 1_000_000.0)
        self.assertIn("8426", r["vehicles"])
        self.assertEqual(r["off_rail"], 0)

    def test_the_check_can_be_set_after_construction(self):
        f = gtfs.Fleet()
        f.on_rail = on_rail
        self.assertEqual(f.update([ob(m_east=2580, t=0)], 1_000_000.0)["vehicles"], {})

    def test_forget_drops_the_last_good_fix(self):
        self.poll(ob(t=0), t=0)
        self.f.forget("8426")
        self.assertEqual(self.poll(ob(m_east=600, t=30), t=30)["vehicles"], {})

    def test_a_shorter_hold(self):
        f = gtfs.Fleet(on_rail=on_rail, refused_hold_s=60)
        f.update([ob(t=0)], 1_000_000.0)
        self.assertIn("8426", f.update([ob(m_east=600, t=60)], 1_000_060.0)["vehicles"])
        self.assertNotIn("8426", f.update([ob(m_east=600, t=61)], 1_000_061.0)["vehicles"])

    def test_hold_default_and_export(self):
        self.assertEqual(fleet_mod.REFUSED_HOLD_S, 600)
        self.assertIs(gtfs.REFUSED_HOLD_S, fleet_mod.REFUSED_HOLD_S)
        self.assertEqual(gtfs.Fleet()._refused_hold_s, 600)


if __name__ == "__main__":
    unittest.main()
