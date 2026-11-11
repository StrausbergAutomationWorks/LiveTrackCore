"""saw_livetrack.irish_rail's timetable-driven runs (0.2.34): read_timetable, join_stations, RunTracker. The capture
is Live Track Intercity Rail's of 2026-11-09 (fixtures irish_rail_geometry_20261109.json.gz, its timetable distilled
for that day, and irish_rail_hacon_20261109.json.gz, every Hacon read 02:48Z-13:59Z); the counts pinned here are the
ones Intercity's code gives on it (bin\\saw0234_prove.py: 665 polls, 15,188 trains drawn, 247 early reports)."""

import datetime as dt
import gzip
import io
import json
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from saw_livetrack import irish_rail as ir  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
GEO = json.loads(gzip.open(os.path.join(FIX, "irish_rail_geometry_20261109.json.gz")).read())
POLLS = json.loads(gzip.open(os.path.join(FIX, "irish_rail_hacon_20261109.json.gz")).read())
TABLE = {"trips": {k: [v[0], v[1], v[3], v[4]] for k, v in GEO["ir_trips"].items()},
         "calls": {k: [[r[1], r[2], r[3]] for r in GEO["trip_stops"][k]] for k in GEO["ir_trips"]},
         "dates": GEO["ir_dates"]}
UTC = dt.timezone.utc


def at(h, m=0):
    return dt.datetime(2026, 11, 9, h, m, tzinfo=UTC).timestamp()


def gtfs_zip():
    """A small Irish Rail-shaped zip: P100 (commuter, two stops then a pass-through and a third), A200 (intercity),
    D300 without a shape, P400 with one call, a parent station, calendar and calendar_dates."""
    files = {
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon,parent_station\n"
                     "CON,Connolly,53.3531,-6.2461,\nCON1,Connolly Platform 1,53.3532,-6.2462,CON\n"
                     "DRO,Drogheda (MacBride),53.7120,-6.3350,\nSKS,Skerries,53.5826,-6.1086,\nX,,,,\n",
        "trips.txt": "route_id,service_id,trip_id,trip_short_name,shape_id\n"
                     "R,WK,t1,P100,s1\nR,WK,t2,A200,s1\nR,WK,t3,D300,\nR,SAT,t4,P400,s1\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type\n"
                          "t1,08:00:00,08:01:00,SKS,2,0,0\nt1,07:30:00,07:30:00,DRO,1,0,0\n"
                          "t1,08:10:00,08:10:00,X,3,1,1\nt1,25:05:00,25:05:00,CON1,4,0,0\n"
                          "t2,09:00:00,09:00:00,CON1,1,0,0\nt2,10:00:00,10:00:00,DRO,2,0,0\n"
                          "t3,09:00:00,09:00:00,CON1,1,0,0\nt3,10:00:00,10:00:00,DRO,2,0,0\n"
                          "t4,09:00:00,09:00:00,CON1,1,0,0\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                        "WK,1,1,1,1,1,0,0,20261101,20261130\nSAT,0,0,0,0,0,1,0,20261101,20261130\n",
        "calendar_dates.txt": "service_id,date,exception_type\nWK,20261110,2\nWK,20261115,1\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


class ReadTimetable(unittest.TestCase):
    def test_small_zip(self):
        t = ir.read_timetable(gtfs_zip(), ir.COMMUTER, dt.date(2026, 11, 9))
        self.assertEqual(list(t["trips"]), ["t1"])                     # A200 intercity; D300 no shape; P400 one call
        self.assertEqual(t["trips"]["t1"], ["P100", "WK", 7 * 3600 + 1800, 25 * 3600 + 300])
        # By stop_sequence; the pass-through row (pickup and drop-off 1) dropped; past midnight kept.
        self.assertEqual(t["calls"]["t1"], [["DRO", 27000, 27000], ["SKS", 28800, 28860], ["CON1", 90300, 90300]])
        # Yesterday (Sun 8th) to six days on (Sun 15th): weekdays, less the 10th, plus the 15th.
        self.assertEqual(t["dates"], {"WK": ["2026-11-09", "2026-11-11", "2026-11-12", "2026-11-13",
                                              "2026-11-15"]})
        self.assertEqual(t["stop_names"]["CON1"], "Connolly")         # the parent station's name
        self.assertNotIn("X", t["stops"])
        # From Tuesday the 10th the window starts at Monday the 9th: yesterday is in it.
        self.assertEqual(ir.read_timetable(gtfs_zip(), ir.COMMUTER, dt.date(2026, 11, 10))["dates"]["WK"][0],
                         "2026-11-09")
        i = ir.read_timetable(gtfs_zip(), ir.INTERCITY, dt.date(2026, 11, 9))
        self.assertEqual(list(i["trips"]), ["t2"])
        with self.assertRaises(ValueError):
            ir.read_timetable(gtfs_zip(), "freight", dt.date(2026, 11, 9))


class JoinStations(unittest.TestCase):
    def test_by_name_prefix_and_distance(self):
        stops = {"CON": [53.3531, -6.2461], "DRO": [53.7120, -6.3350], "HSTON": [53.3464, -6.2928]}
        names = {"CON": "Connolly", "DRO": "Drogheda (MacBride)", "HSTON": "Dublin Heuston"}
        api = [{"code": "CNLLY", "name": "Dublin Connolly", "lat": 0.0, "lon": 0.0},        # "Dublin" dropped
               {"code": "DGHDA", "name": "Drogheda", "lat": None, "lon": None},             # prefix
               {"code": "HSTN", "name": "Heuston Station", "lat": 53.3466, "lon": -6.2930},  # by distance
               {"code": "FAR", "name": "Nowhere", "lat": 52.0, "lon": -8.0},
               {"code": "", "name": "Connolly", "lat": None, "lon": None}]
        self.assertEqual(ir.join_stations(api, stops, names), {"CNLLY": "CON", "DGHDA": "DRO", "HSTN": "HSTON"})

    def test_capture_join(self):
        self.assertEqual(len(GEO["ir_stations"]), 162)


class Tracker(unittest.TestCase):
    def test_whole_capture(self):
        tr, drawn, early_max = ir.RunTracker(), 0, 0
        for t, trains in POLLS:
            runs, skipped = tr.step(trains, t, TABLE, GEO["ir_stations"])
            drawn += len(runs)
            self.assertEqual(len({r.key for r in runs}), len(runs))
            for r in runs:
                self.assertIn(r.state, ("standing", "running"))
                self.assertTrue(0 <= r.index < len(r.times))
        self.assertEqual(len(POLLS), 665)
        self.assertEqual(drawn, 15188)
        self.assertEqual(tr.early, 247)

    def test_no_timetable(self):
        self.assertEqual(ir.RunTracker().step([], at(8), {}, {}), ([], {"no_timetable": 1}))

    def test_genuine(self):
        day, start, end = dt.date(2026, 11, 9), at(8), at(10)
        g = ir.RunTracker.genuine
        rep = {"status": "R", "origin_time": start + 60, "delay_s": 120}
        self.assertTrue(g(rep, day, start, end, at(9)))
        self.assertFalse(g(dict(rep, origin_time=start + ir.MATCH_S + 1), day, start, end, at(9)))
        self.assertFalse(g(dict(rep, status="N"), day, start, end, at(9)))
        # A ghost: listed after its span plus its delay plus GHOST_S (1800 s, pinned).
        self.assertFalse(g(rep, day, start, end, end + 120 + 1801))
        self.assertTrue(g(rep, day, start, end, end + 120 + 1800))
        self.assertFalse(g(dict(rep, origin_time=start + 121), day, start, end, at(9)))   # MATCH_S 120, pinned
        # No origin time (the documented call): matched by date.
        self.assertTrue(g({"status": "R", "date": "2026-11-09"}, day, start, end, at(9)))
        self.assertFalse(g({"status": "R", "date": "2026-11-08"}, day, start, end, at(9)))

    def one_trip(self):
        tid = next(k for k, v in TABLE["trips"].items() if "2026-11-09" in TABLE["dates"][v[1]]
                   and len(TABLE["calls"][k]) >= 4 and v[2] > 8 * 3600)
        return tid, TABLE["trips"][tid]

    def test_delay_kept_terminated_and_floor(self):
        tid, (code, sid, first, last) = self.one_trip()
        base = ir.service_base(dt.date(2026, 11, 9))
        start = base + first
        table = {"trips": {tid: TABLE["trips"][tid]}, "calls": {tid: TABLE["calls"][tid]}, "dates": TABLE["dates"]}
        tr = ir.RunTracker()
        rep = {"code": code, "status": "R", "origin_time": start, "delay_s": 300}
        runs, _ = tr.step([rep], start + 60, table, {})
        self.assertEqual(runs[0].delay, 300)
        runs, _ = tr.step([], start + 120, table, {})                 # not listed: the delay is kept
        self.assertEqual(runs[0].delay, 300)
        # A report more than EARLY_S early is not believed.
        tr2 = ir.RunTracker()
        runs, _ = tr2.step([dict(rep, delay_s=-ir.EARLY_S - 1)], start + 60, table, {})
        self.assertIsNone(runs[0].delay)
        self.assertEqual(tr2.early, 1)
        # The floor: on time and still at its origin by the timetable, but reported departed from (D) or arrived at
        # (A) the third call: drawn there, not at the origin.
        third = TABLE["calls"][tid][2][0]
        # No delay reported (delay_s None), so no call carries an expected time and `known` comes from the floor alone.
        on_time = dict(rep, delay_s=None, last_location="Z")
        for kind, want in (("D", ("running", 2)), ("A", ("standing", 2))):
            runs, _ = ir.RunTracker().step([dict(on_time, last_location_type=kind)], start - 200, table, {"Z": third})
            self.assertEqual((runs[0].state, runs[0].index), want, kind)
            # An arrival report makes that call's arrival the railroad's word (`known`), which the consumer's
            # published delay rests on; a departure report does not.
            self.assertEqual(runs[0].times[2][3], kind == "A", kind)
        runs, _ = ir.RunTracker().step([dict(on_time, last_location="NOWHERE")], start - 200, table, {"Z": third})
        self.assertEqual((runs[0].state, runs[0].index), ("standing", 0))
        # T ends the run.
        runs, skipped = tr.step([dict(rep, status="T")], start + 180, table, {})
        self.assertEqual(runs, [])
        self.assertEqual(skipped, {"terminated": 1})

    def test_past_midnight_and_duplicates(self):
        calls = [["A", 23 * 3600 + 1800, 23 * 3600 + 1800], ["B", 25 * 3600 + 300, 25 * 3600 + 300]]
        table = {"trips": {"t": ["P1", "WK", 23 * 3600 + 1800, 25 * 3600 + 300]}, "calls": {"t": calls},
                 "dates": {"WK": ["2026-11-08"]}}
        # Yesterday's run (Sunday 8th), at 00:30 on Monday the 9th: drawn, keyed by its own day.
        runs, _ = ir.RunTracker().step([], at(0, 30), table, {})
        self.assertEqual([(r.key, r.state) for r in runs], [("P1-8", "running")])
        # Two trips with the same code on the same day: one run, the other counted.
        table = {"trips": {"t": ["P1", "WK", 8 * 3600, 9 * 3600], "u": ["P1", "WK", 8 * 3600, 9 * 3600]},
                 "calls": {"t": [["A", 8 * 3600, 8 * 3600], ["B", 9 * 3600, 9 * 3600]],
                           "u": [["A", 8 * 3600, 8 * 3600], ["B", 9 * 3600, 9 * 3600]]},
                 "dates": {"WK": ["2026-11-09"]}}
        runs, skipped = ir.RunTracker().step([], at(8, 30), table, {})
        self.assertEqual([r.trip_id for r in runs], ["t"])
        self.assertEqual(skipped, {"duplicate": 1})

    def test_ghost_counted(self):
        tid, (code, sid, first, last) = self.one_trip()
        table = {"trips": {tid: TABLE["trips"][tid]}, "calls": {tid: TABLE["calls"][tid]}, "dates": TABLE["dates"]}
        tr = ir.RunTracker()
        runs, skipped = tr.step([{"code": "A999", "status": "R", "origin_time": 0}], at(0, 1), table, {})
        self.assertEqual(skipped, {"ghost_or_unmatched": 1})
        self.assertEqual(tr.ghosts, 1)


if __name__ == "__main__":
    unittest.main()
