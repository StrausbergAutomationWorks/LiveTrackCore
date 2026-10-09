"""Metra's GTFS static change (Developer Change Notice, received 2026-10-08):
the same timetable in today's form and in the notice's future form must give
the same stations, times, delays and next stops, each keyed by its own trip ids.

Future form, per the notice: no space after commas; trip_id
BNSF_BN1200_V4_A -> BNSF_1200_8_2009_5682524; service_id A1 -> 8_2009; shape_id
BNSF_IB_1 -> 1685230199; block_id populated; stop_times.txt loses
center_boarding, south_boarding, bikes_allowed and notice; pickup_type and
drop_off_type may be 3; feed_info.txt added; transfers.txt, notes.txt,
note_links.txt and trip_flexfields.txt added, possibly header-only.
Synthetic files only: no operator data is committed."""

import io
import os
import sys
import unittest
import zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import google.transit.gtfs_realtime_pb2  # noqa: F401
except ImportError:  # the `gtfs` extra is not installed
    raise unittest.SkipTest("needs saw-livetrack[gtfs]")

import gtfs_feeds as feeds  # noqa: E402
from saw_livetrack import gtfs  # noqa: E402
from saw_livetrack.gtfs import schedule  # noqa: E402

CHI = ZoneInfo("America/Chicago")
TODAY = date(2026, 10, 8)          # a Thursday


def at(h, mi):
    return datetime(2026, 10, 8, h, mi, tzinfo=CHI).timestamp()


def _zip(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return b.getvalue()


OLD_TRIP, NEW_TRIP = "BNSF_BN1200_V4_A", "BNSF_1200_8_2009_5682524"

OLD = {  # today's form: a space after every comma, headers and values (B5)
    "agency.txt": "agency_id, agency_name, agency_url, agency_timezone\r\n"
                  "METRA, Metra, https://metrarail.com, America/Chicago\r\n",
    "calendar.txt": "service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date\r\n"
                    "A1, 1, 1, 1, 1, 1, 0, 0, 20250101, 20271231\r\n",
    "calendar_dates.txt": "service_id, date, exception_type\r\n",
    "routes.txt": "route_id, route_short_name, route_long_name, route_color\r\n"
                  "BNSF, BNSF, Burlington Northern, 29C233\r\n",
    "trips.txt": "route_id, service_id, trip_id, trip_headsign, block_id, shape_id, direction_id\r\n"
                 f"BNSF, A1, {OLD_TRIP}, Chicago Union Station, , BNSF_IB_1, 1\r\n",
    "stop_times.txt": "trip_id, arrival_time, departure_time, stop_id, stop_sequence, pickup_type, drop_off_type, "
                      "center_boarding, south_boarding, bikes_allowed, notice\r\n"
                      f"{OLD_TRIP}, 07:00:00, 07:00:00, AURORA, 1, 0, 0, 0, 0, 1, 0\r\n"
                      f"{OLD_TRIP}, 07:08:00, 07:08:00, ROUTE59, 3, 0, 0, 0, 0, 1, 0\r\n",
    "stops.txt": "stop_id, stop_name, stop_lat, stop_lon\r\n"
                 "AURORA, Aurora, 41.76, -88.31\r\nROUTE59, Route 59, 41.78, -88.21\r\n",
    "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\r\n"
                  "BNSF_IB_1,41.76,-88.31,1\r\nBNSF_IB_1,41.78,-88.21,2\r\n",
}

NEW = {  # the notice's future form
    "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\r\n"
                  "METRA,Metra,https://metrarail.com,America/Chicago\r\n",
    "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\r\n"
                    "8_2009,1,1,1,1,1,0,0,20260608,20270530\r\n",
    "calendar_dates.txt": "service_id,date,exception_type\r\n",
    "routes.txt": "route_id,route_short_name,route_long_name,route_color\r\n"
                  "BNSF,BNSF,Burlington Northern,29C233\r\n",
    "trips.txt": "route_id,service_id,trip_id,trip_headsign,block_id,shape_id,direction_id\r\n"
                 f"BNSF,8_2009,{NEW_TRIP},Chicago Union Station,5682524,1685230199,1\r\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type\r\n"
                      f"{NEW_TRIP},07:00:00,07:00:00,AURORA,1,0,0\r\n"
                      f"{NEW_TRIP},07:08:00,07:08:00,ROUTE59,3,3,3\r\n",
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\r\n"
                 "AURORA,Aurora,41.76,-88.31\r\nROUTE59,Route 59,41.78,-88.21\r\n",
    "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\r\n"
                  "1685230199,41.76,-88.31,1\r\n1685230199,41.78,-88.21,2\r\n",
    "fare_attributes.txt": "fare_id,price,currency_type,payment_method,transfers,agency_id\r\n"
                           "1,3.75,USD,0,0,METRA\r\n",
    "feed_info.txt": "feed_publisher_name,feed_publisher_url,feed_lang,feed_start_date,feed_end_date,feed_version\r\n"
                     "Metra,http://www.metrarail.com/,EN,20260608,20270530,08-JUN-2026--30-MAY-2027\r\n",
    "transfers.txt": "from_stop_id,to_stop_id,transfer_type,min_transfer_time,from_route_id,to_route_id,"
                     "from_trip_id,to_trip_id\r\n",
    "notes.txt": "note_id,label,description\r\n",
    "note_links.txt": "note_id,trip_ids,stop_ids,stop_sequence\r\n",
    # Not header-only: an extension we do not read must not stop the import.
    "trip_flexfields.txt": "trip_id,field_code,field_type,value\r\n"
                           f'"{NEW_TRIP}","Construction","INTEGER","10"\r\n',
}


def tu(trip, stop_id, arr):
    fm = feeds.feed()
    e = fm.entity.add()
    e.id = "tu"
    e.trip_update.trip.trip_id = trip
    u = e.trip_update.stop_time_update.add()
    u.stop_sequence = 3
    u.stop_id = stop_id
    u.arrival.time = int(arr)
    return fm


class BothForms(unittest.TestCase):

    def setUp(self):
        self.old = schedule.distill(_zip(OLD), stop_times=False, today=TODAY)
        self.new = schedule.distill(_zip(NEW), stop_times=False, today=TODAY)

    def test_same_stations_coordinates_lines_and_zone(self):
        for k in ("stops", "stop_pos", "routes", "tz"):
            self.assertEqual(self.old[k], self.new[k], k)
        self.assertEqual(self.new["stops"], {"AURORA": "Aurora", "ROUTE59": "Route 59"})

    def test_same_times_each_under_its_own_trip_id(self):
        want = {"AURORA": 25200, "ROUTE59": 25680}
        self.assertEqual(self.old["sched"], {OLD_TRIP: want})
        # service_id 8_2009 joins trips to calendar as an opaque string
        self.assertEqual(self.new["sched"], {NEW_TRIP: want})

    def test_same_delay_and_next_stop(self):
        now, arr = at(7, 15), at(7, 18)          # Route 59 due 07:08, 10 min late
        for st, trip in ((self.old, OLD_TRIP), (self.new, NEW_TRIP)):
            fm = tu(trip, "ROUTE59", arr)
            self.assertEqual(schedule.stop_delays(fm, st, now), {trip: 600})
            self.assertEqual(schedule.next_stops(fm, st, now), {trip: ("Route 59", arr)})

    def test_new_trip_ids_against_the_old_timetable_lose_only_the_delay(self):
        # The cutover window: the realtime feed has switched, the cached
        # timetable has not. The station is still named (Metra's update names
        # it); the delay is gone, which is what trip_coverage exists to catch.
        now, arr = at(7, 15), at(7, 18)
        fm = tu(NEW_TRIP, "ROUTE59", arr)
        self.assertEqual(schedule.stop_delays(fm, self.old, now), {})
        self.assertEqual(schedule.next_stops(fm, self.old, now), {NEW_TRIP: ("Route 59", arr)})
        self.assertEqual(schedule.trip_coverage(fm, self.old), (0, 1))
        self.assertEqual(schedule.trip_coverage(fm, self.new), (1, 1))

    def test_feed_info(self):
        self.assertEqual(self.new["feed_version"], "08-JUN-2026--30-MAY-2027")
        self.assertEqual((self.new["feed_start"], self.new["feed_end"]), ("20260608", "20270530"))
        for k in ("feed_version", "feed_start", "feed_end"):
            self.assertIsNone(self.old[k], k)

    def test_feed_info_first_row(self):
        # GTFS allows one row; were there two, the first is the dataset's.
        files = {**NEW, "feed_info.txt": "feed_version,feed_start_date\r\nA,20260608\r\nB,20270101\r\n"}
        d = schedule.distill(_zip(files), stop_times=False, today=TODAY)
        self.assertEqual((d["feed_version"], d["feed_start"]), ("A", "20260608"))

    def test_header_only_feed_info_is_none_not_an_error(self):
        files = {**NEW, "feed_info.txt": "feed_publisher_name,feed_version\r\n"}
        self.assertIsNone(schedule.distill(_zip(files), stop_times=False, today=TODAY)["feed_version"])

    def test_stop_times_mode_reads_the_new_form_too(self):
        d = schedule.distill(_zip(NEW), stop_times=True, today=TODAY)
        self.assertEqual(d["stop_times"], {NEW_TRIP: {"1": ["AURORA", "07:00:00"], "3": ["ROUTE59", "07:08:00"]}})
        self.assertEqual(d["trip_routes"], {NEW_TRIP: "BNSF"})

    def test_cache_format_is_4(self):
        self.assertEqual(schedule.STATIC_VERSION, 4)
        self.assertEqual(self.new["v"], 4)


class Identifiers(unittest.TestCase):
    """Preserved exactly: never truncated, rounded or case-normalised."""

    def test_leading_zeros_and_case_keep_trips_apart(self):
        files = {**NEW, "trips.txt": "route_id,service_id,trip_id\r\nBNSF,8_2009,0012\r\nBNSF,8_2009,12\r\n"
                                    "BNSF,8_2009,bnsf_12\r\nBNSF,8_2009,BNSF_12\r\n",
                 "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\r\n"
                                   "0012,07:00:00,07:00:00,AURORA,1\r\n12,08:00:00,08:00:00,AURORA,1\r\n"
                                   "bnsf_12,09:00:00,09:00:00,AURORA,1\r\nBNSF_12,10:00:00,10:00:00,AURORA,1\r\n"}
        s = schedule.distill(_zip(files), stop_times=False, today=TODAY)["sched"]
        self.assertEqual(s, {"0012": {"AURORA": 25200}, "12": {"AURORA": 28800},
                             "bnsf_12": {"AURORA": 32400}, "BNSF_12": {"AURORA": 36000}})


class TripCoverage(unittest.TestCase):
    STATIC = {"v": schedule.STATIC_VERSION, "sched": {"a": {}, "b": {}}, "stop_times": {"515": {}}}

    def feed(self, trips):
        fm = feeds.feed()
        for t in trips:
            e = fm.entity.add()
            e.id = "tu_" + t
            e.trip_update.trip.trip_id = t
        feeds.vehicle(fm, "v1", vid="1")       # a vehicle entity is not a trip update
        return fm

    def test_counts_sched_and_stop_times(self):
        self.assertEqual(schedule.trip_coverage(self.feed(["a", " b ", "515", "zz"]), self.STATIC), (3, 4))

    def test_trip_updates_without_a_trip_id_are_not_counted(self):
        self.assertEqual(schedule.trip_coverage(self.feed(["", "a"]), self.STATIC), (1, 1))

    def test_guards(self):
        fm = self.feed(["a"])
        self.assertEqual(schedule.trip_coverage(None, self.STATIC), (0, 0))
        self.assertEqual(schedule.trip_coverage(fm, None), (0, 0))
        self.assertEqual(schedule.trip_coverage(fm, {**self.STATIC, "v": 3}), (0, 0))

    def test_exported(self):
        self.assertIs(gtfs.trip_coverage, schedule.trip_coverage)


if __name__ == "__main__":
    unittest.main()
