"""saw_livetrack.zones (0.2.5): time zones from a coordinate (tzfpy, decision A, 2026-10-11).

The places are the ones the evaluation measured
(35_Dependencies/third_party/tzfpy/EVAL_time_zone_options_20261010.md): the
railroads Live Track draws, the stops Amtrak's GTFS puts on the wrong clock,
and borders where a coarse method fails."""
import builtins
import sys
import unittest
from datetime import datetime, timezone

from saw_livetrack import zones

JAN = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
JUL = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


class ZoneAt(unittest.TestCase):
    def test_places_by_name(self):
        for lat, lon, want in [
            (41.8787, -87.6403, "America/Chicago"),        # Chicago Union Station
            (41.9001, -87.9551, "America/Chicago"),        # Villa Park
            (43.6453, -79.3806, "America/Toronto"),        # Toronto Union
            (45.4167, -75.6514, "America/Toronto"),        # Ottawa
            (26.7024, -80.0539, "America/New_York"),       # Brightline West Palm Beach
            (-27.4654, 153.0197, "Australia/Brisbane"),    # Roma Street
            (60.1719, 24.9414, "Europe/Helsinki"),         # Helsinki central
            (3.1340, 101.6869, "Asia/Kuala_Lumpur"),       # KL Sentral
            (40.0076, -105.2659, "America/Denver"),        # Boulder
        ]:
            with self.subTest(lat=lat, lon=lon):
                self.assertEqual(zones.zone_at(lat, lon), want)

    def test_where_amtraks_own_feed_is_wrong(self):
        # Amtrak's GTFS: all Arizona on Denver time, the Gulf Coast on New York time.
        self.assertEqual(zones.zone_at(35.1983, -111.6513), "America/Phoenix")   # Flagstaff
        self.assertEqual(zones.zone_at(32.1253, -110.9631), "America/Phoenix")   # Tucson
        self.assertEqual(zones.utc_offset_min(JUL, 30.3960, -88.8853), -300)      # Biloxi: Central, CDT
        self.assertEqual(zones.utc_offset_min(JAN, 30.6954, -88.0399), -360)      # Mobile: Central, CST

    def test_borders_a_coarse_grid_gets_wrong(self):
        self.assertEqual(zones.utc_offset_min(JAN, 41.6020, -87.3370), -360)  # Gary: Central
        self.assertEqual(zones.utc_offset_min(JAN, 41.7036, -86.3128), -300)  # South Bend airport: Eastern

    def test_latitude_first(self):
        # tzfpy itself takes longitude first; zone_at must not pass them through as given.
        self.assertEqual(zones.zone_at(41.8787, -87.6403), "America/Chicago")
        self.assertNotEqual(zones.zone_at(-87.6403, 41.8787), "America/Chicago")

    def test_open_sea_is_a_nautical_zone(self):
        name = zones.zone_at(30.0, -40.0)
        self.assertTrue(name and name.startswith("Etc/GMT"), name)

    def test_not_a_coordinate(self):
        for lat, lon in [(None, 0), (0, None), ("x", 1), (float("nan"), 0), (0, float("inf")),
                         (90.5, 0), (-91, 0), (0, 180.5), (0, -181), (True, 1), (1, False), ([], 1)]:
            with self.subTest(lat=lat, lon=lon):
                self.assertIsNone(zones.zone_at(lat, lon))

    def test_our_own_check_not_tzfpys(self):
        # tzfpy 2.1.0 also answers "" off the globe; the refusal must not depend on that
        # (mutation run 2026-10-11: three range mutants survived through it).
        for lat, lon in [(90.5, 0), (-91, 0), (0, 180.5), (0, -181), (0, float("inf")), (float("-inf"), 0),
                         (float("nan"), 0), (0, float("nan"))]:
            with self.subTest(lat=lat, lon=lon):
                self.assertIsNone(zones._coord(lat, lon))
        self.assertEqual(zones._coord("41.5", -87), (41.5, -87.0))
        self.assertEqual(zones._coord(90, -180), (90.0, -180.0))

    def test_numbers_as_strings_and_edges(self):
        self.assertEqual(zones.zone_at("41.8787", "-87.6403"), "America/Chicago")
        self.assertIsNotNone(zones.zone_at(90, 180))
        self.assertIsNotNone(zones.zone_at(-90, -180))


class LocalTime(unittest.TestCase):
    def test_daylight_saving_follows_the_place(self):
        # Phoenix keeps no daylight saving; Denver does.
        self.assertEqual(zones.utc_offset_min(JAN, 33.4484, -112.0740), -420)
        self.assertEqual(zones.utc_offset_min(JUL, 33.4484, -112.0740), -420)
        self.assertEqual(zones.utc_offset_min(JAN, 39.7392, -104.9903), -420)
        self.assertEqual(zones.utc_offset_min(JUL, 39.7392, -104.9903), -360)
        self.assertEqual(zones.utc_offset_min(JAN, -27.4654, 153.0197), 600)   # Brisbane, no DST
        self.assertEqual(zones.utc_offset_min(JAN, 60.1719, 24.9414), 120)     # Helsinki winter

    def test_local_time_is_the_same_instant(self):
        t = zones.local_time(JUL, 41.8787, -87.6403)
        self.assertEqual(t, JUL)
        self.assertEqual((t.hour, t.utcoffset().total_seconds()), (7, -18000))
        self.assertEqual(t.tzinfo.key, "America/Chicago")

    def test_naive_is_refused(self):
        with self.assertRaises(ValueError):
            zones.local_time(datetime(2026, 7, 15, 12, 0), 41.8787, -87.6403)

    def test_not_a_coordinate_is_none(self):
        self.assertIsNone(zones.local_time(JUL, None, 1))
        self.assertIsNone(zones.utc_offset_min(JUL, 95, 1))

    def test_a_zone_zoneinfo_cannot_load_is_none(self):
        real = zones.zone_at
        zones.zone_at = lambda lat, lon: "Not/AZone"
        try:
            self.assertIsNone(zones.local_time(JUL, 1, 1))
        finally:
            zones.zone_at = real


class DataAndAttribution(unittest.TestCase):
    def test_data_version_and_odbl_notice(self):
        self.assertRegex(zones.DATA_VERSION(), r"^\d{4}[a-z]$")
        self.assertIn("ODbL", zones.ATTRIBUTION)
        self.assertIn("Timezone Boundary Builder", zones.ATTRIBUTION)
        self.assertIn("ODbL", zones.__doc__)

    def test_without_tzfpy_it_says_so(self):
        real_import = builtins.__import__

        def no_tzfpy(name, *a, **k):
            if name == "tzfpy" or name.startswith("tzfpy."):
                raise ImportError("no tzfpy")
            return real_import(name, *a, **k)

        saved = sys.modules.pop("tzfpy", None)
        builtins.__import__ = no_tzfpy
        try:
            with self.assertRaises(ImportError):
                zones.zone_at(41.9, -87.6)
        finally:
            builtins.__import__ = real_import
            if saved is not None:
                sys.modules["tzfpy"] = saved

    def test_not_imported_by_the_package(self):
        import saw_livetrack
        self.assertNotIn("zones", vars(saw_livetrack).get("__all__", []))
        self.assertFalse(hasattr(saw_livetrack, "zone_at"))


if __name__ == "__main__":
    unittest.main()
