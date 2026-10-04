"""saw_livetrack.gtfs.realtime: the two field readers and decode (backlog 274).
The readers' rules came from measured feeds: proto2 returns a default for an
unset field, so presence is HasField, never truthiness."""
import subprocess
import unittest
from datetime import datetime, timezone
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import google.transit.gtfs_realtime_pb2  # noqa: F401
except ImportError:  # the `gtfs` extra is not installed
    raise unittest.SkipTest("needs saw-livetrack[gtfs]")

import gtfs_feeds as feeds  # noqa: E402
from saw_livetrack.gtfs import realtime  # noqa: E402


def one(**kw):
    fm = feeds.feed()
    feeds.vehicle(fm, "e1", vid="1", **kw)
    return fm.entity[0].vehicle


class VehicleTime(unittest.TestCase):
    def test_set(self):
        self.assertEqual(realtime.vehicle_time(one(ts=feeds.T0)),
                         datetime.fromtimestamp(feeds.T0, tz=timezone.utc))

    def test_aware_utc(self):
        self.assertIs(realtime.vehicle_time(one(ts=feeds.T0)).tzinfo, timezone.utc)

    def test_unset_and_zero_are_none(self):
        self.assertIsNone(realtime.vehicle_time(one(ts=None)))
        self.assertIsNone(realtime.vehicle_time(one(ts=0)))


class PositionOf(unittest.TestCase):
    def test_six_decimals(self):
        lat, lon = realtime.position_of(one(lat=41.860511779785156, lon=-87.95))
        self.assertEqual(lat, round(lat, 6))
        self.assertAlmostEqual(lat, 41.860512, places=5)

    def test_unset_is_none(self):
        self.assertIsNone(realtime.position_of(one(position=False)))

    def test_null_island_is_none(self):
        self.assertIsNone(realtime.position_of(one(lat=0.0, lon=0.0)))


class Decode(unittest.TestCase):
    def test_round_trip(self):
        fm = feeds.feed()
        feeds.vehicle(fm, "e1", vid="8461", label="669", route="UP-NW")
        got = realtime.decode(fm.SerializeToString())
        self.assertEqual(got.SerializeToString(), fm.SerializeToString())

    def test_junk_is_none(self):
        self.assertIsNone(realtime.decode(b"\x00\x01 not a feed"))


class Obs(unittest.TestCase):
    def test_frozen(self):
        o = realtime.Obs(vehicle_id="1", lat=1.0, lon=2.0, observed_at=None, in_service=False)
        with self.assertRaises(Exception):
            o.lat = 3.0


class OptIn(unittest.TestCase):
    def test_top_level_import_does_not_load_gtfs(self):
        # Live Track Amtrak and VIA Rail import saw_livetrack and must not
        # pull in the GTFS module or need its extra.
        code = ("import sys; sys.path.insert(0, %r); import saw_livetrack; "
                "print('saw_livetrack.gtfs' in sys.modules, 'google.protobuf' in sys.modules)"
                % os.path.join(ROOT, "src"))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.split()
        self.assertEqual(out, ["False", "False"])


if __name__ == "__main__":
    unittest.main()
