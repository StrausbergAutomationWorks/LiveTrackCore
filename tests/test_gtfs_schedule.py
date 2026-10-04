"""Ported from Live Track Commuter Rail tests/test_schedule.py (df972df), backlog 274.

Next station and ETA (schedule.py).
Feed shapes follow what each feed was measured to carry, 2026-10-02 19:45."""

import io
import unittest
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo
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
from saw_livetrack.gtfs import schedule  # noqa: E402

CHI = ZoneInfo("America/Chicago")


def at(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=CHI).timestamp()


NOW = at(2026, 10, 2, 19, 45)


def tu_feed(trips):
    """trips: {trip_id: [dict(stop_id=, seq=, arr=, dep=, delay=)], ...}"""
    fm = feeds.feed()
    for trip, stops in trips.items():
        e = fm.entity.add()
        e.id = f"tu_{trip}"
        e.trip_update.trip.trip_id = trip
        for s in stops:
            u = e.trip_update.stop_time_update.add()
            if "seq" in s:
                u.stop_sequence = s["seq"]
            if "stop_id" in s:
                u.stop_id = s["stop_id"]
            if "arr" in s:
                u.arrival.time = int(s["arr"])
            if "dep" in s:
                u.departure.time = int(s["dep"])
            if "delay" in s:
                u.arrival.delay = s["delay"]
    return fm


METRA_STATIC = {"v": schedule.STATIC_VERSION, "tz": "America/Chicago",
                "stops": {"IRVINGPK": "Irving Park", "JEFFERSONP": "Jefferson Park"}}
NICTD_STATIC = {"v": schedule.STATIC_VERSION, "tz": "America/Chicago",
                "stops": {"s5": "57th St. (Hyde Park)", "s7": "Kensington", "s1": "Millennium Station"},
                "stop_times": {"232": {"7": ["s5", "19:48:00"], "8": ["s7", "20:00:00"]},
                               "999": {"3": ["s1", "24:10:00"]}}}


class MetraShape(unittest.TestCase):
    """stop_id and an absolute arrival time on the stop update."""

    def test_first_upcoming_stop_named_from_stops_txt(self):
        fm = tu_feed({"UP-NW_UNW669_V2_C": [
            {"seq": 2, "stop_id": "JEFFERSONP", "arr": NOW - 120},   # served
            {"seq": 3, "stop_id": "IRVINGPK", "arr": NOW + 240},
            {"seq": 4, "stop_id": "JEFFERSONP", "arr": NOW + 600}]})
        got = schedule.next_stops(fm, METRA_STATIC, NOW)
        self.assertEqual(got, {"UP-NW_UNW669_V2_C": ("Irving Park", NOW + 240)})

    def test_a_stop_just_reached_is_still_next(self):
        fm = tu_feed({"t": [{"seq": 1, "stop_id": "IRVINGPK", "arr": NOW - 20}]})
        self.assertEqual(schedule.next_stops(fm, METRA_STATIC, NOW)["t"][0], "Irving Park")

    def test_departure_time_when_no_arrival(self):
        fm = tu_feed({"t": [{"seq": 1, "stop_id": "IRVINGPK", "dep": NOW + 60}]})
        self.assertEqual(schedule.next_stops(fm, METRA_STATIC, NOW)["t"], ("Irving Park", NOW + 60))

    def test_unknown_next_stop_shows_nothing_not_the_one_after(self):
        fm = tu_feed({"t": [{"seq": 1, "stop_id": "NOWHERE", "arr": NOW + 60},
                            {"seq": 2, "stop_id": "IRVINGPK", "arr": NOW + 300}]})
        self.assertEqual(schedule.next_stops(fm, METRA_STATIC, NOW), {})

    def test_hours_ahead_keeps_the_station_not_the_time(self):
        # Lee 2026-10-03: a station with no time is fine. A time hours out
        # is a wrong date, so it is dropped; the station is not.
        fm = tu_feed({"t": [{"seq": 1, "stop_id": "IRVINGPK", "arr": NOW + 4 * 3600}]})
        self.assertEqual(schedule.next_stops(fm, METRA_STATIC, NOW), {"t": ("Irving Park", None)})

    def test_named_stop_with_no_time_is_published_without_one(self):
        # The 2026-10-02 probe: 4 of 40 Metra trip updates named the next
        # stop and carried no time.
        fm = tu_feed({"t": [{"seq": 3, "stop_id": "IRVINGPK"},
                            {"seq": 4, "stop_id": "JEFFERSONP", "arr": NOW + 600}]})
        self.assertEqual(schedule.next_stops(fm, METRA_STATIC, NOW), {"t": ("Irving Park", None)})

    def test_no_name_and_no_time_says_nothing(self):
        fm = tu_feed({"t": [{"seq": 3, "stop_id": "NOWHERE"},
                            {"seq": 4, "stop_id": "IRVINGPK", "arr": NOW + 600}]})
        self.assertEqual(schedule.next_stops(fm, METRA_STATIC, NOW), {})

    def test_sequence_order_not_feed_order(self):
        fm = tu_feed({"t": [{"seq": 5, "stop_id": "JEFFERSONP", "arr": NOW + 600},
                            {"seq": 3, "stop_id": "IRVINGPK", "arr": NOW + 240}]})
        self.assertEqual(schedule.next_stops(fm, METRA_STATIC, NOW)["t"][0], "Irving Park")


class NictdShape(unittest.TestCase):
    """Only stop_sequence and a delay: the timetable names the stop and the time."""

    def test_scheduled_plus_delay(self):
        # Measured 2026-10-02: train 232, seq 7 -> 57th St., 19:48 scheduled,
        # 660 s late -> 19:59.
        fm = tu_feed({"232": [{"seq": 7, "delay": 660}, {"seq": 8, "delay": 660}]})
        got = schedule.next_stops(fm, NICTD_STATIC, NOW)
        self.assertEqual(got, {"232": ("57th St. (Hyde Park)", at(2026, 10, 2, 19, 59))})

    def test_served_stop_skipped_by_its_estimate(self):
        fm = tu_feed({"232": [{"seq": 7, "delay": -600}, {"seq": 8, "delay": 0}]})
        got = schedule.next_stops(fm, NICTD_STATIC, NOW)
        self.assertEqual(got["232"], ("Kensington", at(2026, 10, 2, 20, 0)))

    def test_no_delay_station_without_estimate(self):
        fm = tu_feed({"232": [{"seq": 7}]})
        self.assertEqual(schedule.next_stops(fm, NICTD_STATIC, NOW),
                         {"232": ("57th St. (Hyde Park)", None)})

    def test_trip_missing_from_timetable(self):
        fm = tu_feed({"555": [{"seq": 7, "delay": 0}]})
        self.assertEqual(schedule.next_stops(fm, NICTD_STATIC, NOW), {})

    def test_after_midnight_time_on_the_right_day(self):
        # 24:10:00 on the service day of 2026-10-02 is 00:10 on 2026-10-03.
        now = at(2026, 10, 3, 0, 5)
        fm = tu_feed({"999": [{"seq": 3, "delay": 0}]})
        self.assertEqual(schedule.next_stops(fm, NICTD_STATIC, now)["999"][1], at(2026, 10, 3, 0, 10))

    def test_an_unresolvable_first_update_says_nothing(self):
        # seq 6 is not in the timetable: which stop is next is unknown, so
        # seq 7 must not be shown as if it were.
        fm = tu_feed({"232": [{"seq": 6, "delay": 0}, {"seq": 7, "delay": 0}]})
        self.assertEqual(schedule.next_stops(fm, NICTD_STATIC, NOW), {})

    def test_start_date_is_honoured_when_the_guess_would_differ(self):
        # Service day 2026-10-01, 13 h late: due 08:48 on the 2nd. Without
        # the start date the nearest scheduled 19:48 is the 2nd's, and the
        # estimate would land on the 3rd.
        now = at(2026, 10, 2, 8, 45)
        fm = tu_feed({"232": [{"seq": 7, "delay": 13 * 3600}]})
        fm.entity[0].trip_update.trip.start_date = "20261001"
        self.assertEqual(schedule.next_stops(fm, NICTD_STATIC, now)["232"][1], at(2026, 10, 2, 8, 48))

    def test_start_date_wins_over_the_guess(self):
        fm = tu_feed({"232": [{"seq": 7, "delay": 0}]})
        fm.entity[0].trip_update.trip.start_date = "20261002"
        self.assertEqual(schedule.next_stops(fm, NICTD_STATIC, NOW)["232"][1], at(2026, 10, 2, 19, 48))


class Guards(unittest.TestCase):
    def test_no_timetable_no_feed_wrong_version(self):
        fm = tu_feed({"t": [{"seq": 1, "stop_id": "IRVINGPK", "arr": NOW + 60}]})
        self.assertEqual(schedule.next_stops(fm, None, NOW), {})
        self.assertEqual(schedule.next_stops(None, METRA_STATIC, NOW), {})
        self.assertEqual(schedule.next_stops(fm, {**METRA_STATIC, "v": 0}, NOW), {})

    def test_unknown_timezone_is_silent(self):
        fm = tu_feed({"232": [{"seq": 7, "delay": 0}]})
        self.assertEqual(schedule.next_stops(fm, {**NICTD_STATIC, "tz": "Mars/Olympus"}, NOW), {})

    def test_iso_utc(self):
        self.assertEqual(schedule.iso_utc(at(2026, 10, 2, 19, 59)), "2026-10-03T00:59:00+00:00")


def _zip(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return b.getvalue()


class Distill(unittest.TestCase):
    FILES = {
        # Metra's spacing after commas, in headers and values (B5).
        "agency.txt": "agency_id, agency_name, agency_timezone\r\nMETRA, Metra, America/Chicago\r\n",
        "stops.txt": "stop_id, stop_name, stop_lat\r\nIRVINGPK, Irving Park, 41.95\r\n",
        "stop_times.txt": "trip_id,stop_id,stop_sequence,arrival_time,departure_time\n"
                          "\"232\",s5,7,19:48:00,19:48:00\n",
    }

    def test_stops_stripped_and_tz(self):
        d = schedule.distill(_zip(self.FILES), stop_times=False)
        self.assertEqual(d["stops"], {"IRVINGPK": "Irving Park"})
        self.assertEqual(d["tz"], "America/Chicago")
        self.assertEqual(d["v"], schedule.STATIC_VERSION)
        self.assertNotIn("stop_times", d)

    def test_stop_times_only_when_asked(self):
        d = schedule.distill(_zip(self.FILES), stop_times=True)
        self.assertEqual(d["stop_times"], {"232": {"7": ["s5", "19:48:00"]}})

    def test_no_routes_or_trips_file_still_distils(self):
        d = schedule.distill(_zip(self.FILES), stop_times=True)
        self.assertEqual(d["routes"], {})
        self.assertEqual(d["trip_routes"], {})
        self.assertEqual(d["stops"], {"IRVINGPK": "Irving Park"})

    def test_route_names_long_then_short_whitespace_squeezed(self):
        files = {**self.FILES,
                 "routes.txt": "route_id, route_short_name, route_long_name\r\n"
                               "MD-N, MD-N, Milwaukee North\r\n"
                               "mo_co, MC, Monon  Corridor\r\n"
                               "X, Short Only, \r\n",
                 "trips.txt": "route_id,service_id,trip_id\nmo_co,a,1601\nso_shore,a,232\n"}
        d = schedule.distill(_zip(files), stop_times=True)
        self.assertEqual(d["routes"], {"MD-N": "Milwaukee North", "mo_co": "Monon Corridor",
                                       "X": "Short Only"})
        self.assertEqual(d["trip_routes"], {"1601": "mo_co", "232": "so_shore"})
        self.assertNotIn("trip_routes", schedule.distill(_zip(files), stop_times=False))


class LineName(unittest.TestCase):
    STATIC = {"v": schedule.STATIC_VERSION,
              "routes": {"UP-NW": "Union Pacific Northwest", "so_shore": "Lakeshore Corridor",
                         "mo_co": "Monon Corridor"},
              "trip_routes": {"232": "so_shore", "1601": "mo_co"}}

    def test_by_line_when_the_line_is_a_route_id(self):
        self.assertEqual(schedule.line_name(self.STATIC, "UP-NW", "UP-NW_UNW669_V2_C"),
                         "Union Pacific Northwest")

    def test_by_trip_when_the_line_is_ours(self):
        self.assertEqual(schedule.line_name(self.STATIC, "lakeshore", "232"), "Lakeshore Corridor")
        self.assertEqual(schedule.line_name(self.STATIC, "monon", " 1601 "), "Monon Corridor")

    def test_none_when_unknown_or_no_timetable(self):
        self.assertIsNone(schedule.line_name(self.STATIC, "BNSF", None))
        self.assertIsNone(schedule.line_name(self.STATIC, None, "9999"))
        self.assertIsNone(schedule.line_name(None, "UP-NW", None))
        self.assertIsNone(schedule.line_name({**self.STATIC, "v": 1}, "UP-NW", None))


if __name__ == "__main__":
    unittest.main()
