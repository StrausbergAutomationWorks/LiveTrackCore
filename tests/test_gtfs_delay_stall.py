"""Backlog 275 (Lee 2026-10-04): delay at the next stop for every feed, and
`stalled` for a train in service standing away from every stop.

stop_delays: the update's own delay when the feed sends one (NICTD), else the
predicted time minus the timetable's (Metra). distill: stop_pos and the
three-day sched window. Fleet: delay_min truncated toward zero; stalled."""

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
import saw_livetrack.gtfs as gtfs  # noqa: E402
from saw_livetrack.gtfs import fleet as fleet_mod, schedule  # noqa: E402
from saw_livetrack.gtfs.realtime import Obs  # noqa: E402

CHI = ZoneInfo("America/Chicago")


def at(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=CHI).timestamp()


NOW = at(2026, 10, 2, 19, 45)


def tu_feed(trips, start_date=None):
    fm = feeds.feed()
    for trip, stops in trips.items():
        e = fm.entity.add()
        e.id = f"tu_{trip}"
        e.trip_update.trip.trip_id = trip
        if start_date:
            e.trip_update.trip.start_date = start_date
        for s in stops:
            u = e.trip_update.stop_time_update.add()
            if "seq" in s:
                u.stop_sequence = s["seq"]
            if "stop_id" in s:
                u.stop_id = s["stop_id"]
            if "arr" in s:
                u.arrival.time = int(s["arr"])
            if "delay" in s:
                u.arrival.delay = s["delay"]
    return fm


V = schedule.STATIC_VERSION
# 19:48:00 = 71280 s; 20:00:00 = 72000 s; 24:10:00 = 87000 s
METRA = {"v": V, "tz": "America/Chicago", "stops": {"IRVINGPK": "Irving Park", "JP": "Jefferson Park"},
         "sched": {"UNW669": {"IRVINGPK": 71280, "JP": 72000}, "LATE": {"IRVINGPK": 87000}}}
NICTD = {"v": V, "tz": "America/Chicago", "stops": {"s5": "57th St.", "s7": "Kensington"},
         "stop_times": {"232": {"7": ["s5", "19:48:00"], "8": ["s7", "20:00:00"]}}}


class StopDelaysMetraShape(unittest.TestCase):
    """stop_id and a predicted time, never a delay field."""

    def test_predicted_minus_scheduled(self):
        fm = tu_feed({"UNW669": [{"seq": 3, "stop_id": "IRVINGPK", "arr": at(2026, 10, 2, 19, 55, 30)}]})
        self.assertEqual(schedule.stop_delays(fm, METRA, NOW), {"UNW669": 7 * 60 + 30})

    def test_early_is_negative(self):
        fm = tu_feed({"UNW669": [{"seq": 3, "stop_id": "IRVINGPK", "arr": at(2026, 10, 2, 19, 46)}]})
        self.assertEqual(schedule.stop_delays(fm, METRA, NOW), {"UNW669": -120})

    def test_start_date_decides_the_day(self):
        # Service day 2026-10-01, ran 13 h 7 min late: due 08:55 on the 2nd.
        now = at(2026, 10, 2, 8, 50)
        fm = tu_feed({"UNW669": [{"seq": 3, "stop_id": "IRVINGPK", "arr": at(2026, 10, 2, 8, 55)}]},
                     start_date="20261001")
        self.assertEqual(schedule.stop_delays(fm, METRA, now), {"UNW669": 13 * 3600 + 7 * 60})

    def test_too_far_off_is_dropped_even_with_a_start_date(self):
        now = at(2026, 10, 2, 8, 50)
        fm = tu_feed({"UNW669": [{"seq": 3, "stop_id": "IRVINGPK", "arr": at(2026, 10, 2, 8, 55)}]},
                     start_date="20260930")
        self.assertEqual(schedule.stop_delays(fm, METRA, now), {})

    def test_guessed_day_capped_at_ahead_s(self):
        # No start_date: 4 h 7 min after 19:48 is taken for a wrong guess.
        fm = tu_feed({"UNW669": [{"seq": 3, "stop_id": "IRVINGPK", "arr": at(2026, 10, 2, 23, 55)}]})
        self.assertEqual(schedule.stop_delays(fm, METRA, NOW), {})

    def test_after_midnight_without_a_start_date(self):
        # 24:10 on the service day of the 2nd is 00:10 on the 3rd; predicted 00:13.
        now = at(2026, 10, 3, 0, 5)
        fm = tu_feed({"LATE": [{"seq": 9, "stop_id": "IRVINGPK", "arr": at(2026, 10, 3, 0, 13)}]})
        self.assertEqual(schedule.stop_delays(fm, METRA, now), {"LATE": 180})

    def test_service_day_is_the_one_nearest_the_prediction_not_now(self):
        # Scheduled 00:30, predicted 00:35 on the 3rd, asked at 12:20 on the
        # 2nd: the 2nd's 00:30 is nearer NOW, the 3rd's is the right one.
        static = {**METRA, "sched": {"EARLY": {"IRVINGPK": 1800}}}
        fm = tu_feed({"EARLY": [{"seq": 1, "stop_id": "IRVINGPK", "arr": at(2026, 10, 3, 0, 35)}]})
        self.assertEqual(schedule.stop_delays(fm, static, at(2026, 10, 2, 12, 20)), {"EARLY": 300})

    def test_served_stop_skipped_next_stop_used(self):
        fm = tu_feed({"UNW669": [{"seq": 2, "stop_id": "IRVINGPK", "arr": NOW - 120},
                                 {"seq": 3, "stop_id": "JP", "arr": at(2026, 10, 2, 20, 4)}]})
        self.assertEqual(schedule.stop_delays(fm, METRA, NOW), {"UNW669": 240})

    def test_next_stop_not_in_timetable_is_absent_not_the_one_after(self):
        fm = tu_feed({"UNW669": [{"seq": 3, "stop_id": "NOWHERE", "arr": NOW + 60},
                                 {"seq": 4, "stop_id": "JP", "arr": at(2026, 10, 2, 20, 4)}]})
        self.assertEqual(schedule.stop_delays(fm, METRA, NOW), {})

    def test_trip_not_in_window_is_absent(self):
        fm = tu_feed({"OTHER": [{"seq": 3, "stop_id": "IRVINGPK", "arr": NOW + 60}]})
        self.assertEqual(schedule.stop_delays(fm, METRA, NOW), {})

    def test_no_timetable_or_wrong_version_or_zone_gives_nothing(self):
        fm = tu_feed({"UNW669": [{"seq": 3, "stop_id": "IRVINGPK", "arr": NOW + 60}]})
        self.assertEqual(schedule.stop_delays(fm, None, NOW), {})
        self.assertEqual(schedule.stop_delays(fm, {**METRA, "v": 2}, NOW), {})
        self.assertEqual(schedule.stop_delays(fm, {**METRA, "tz": "Mars/Olympus"}, NOW), {})
        self.assertEqual(schedule.stop_delays(None, METRA, NOW), {})

    def test_sequence_order(self):
        fm = tu_feed({"UNW669": [{"seq": 5, "stop_id": "JP", "arr": at(2026, 10, 2, 20, 9)},
                                 {"seq": 3, "stop_id": "IRVINGPK", "arr": at(2026, 10, 2, 19, 49)}]})
        self.assertEqual(schedule.stop_delays(fm, METRA, NOW), {"UNW669": 60})


class StopDelaysDelayField(unittest.TestCase):
    """A delay field wins over anything computed; it needs no timetable."""

    def test_nictd_sequence_and_delay(self):
        fm = tu_feed({"232": [{"seq": 7, "delay": 660}, {"seq": 8, "delay": 660}]})
        self.assertEqual(schedule.stop_delays(fm, NICTD, NOW), {"232": 660})

    def test_served_stop_by_its_estimate(self):
        fm = tu_feed({"232": [{"seq": 7, "delay": -600}, {"seq": 8, "delay": 120}]})
        self.assertEqual(schedule.stop_delays(fm, NICTD, NOW), {"232": 120})

    def test_field_wins_over_predicted_minus_scheduled(self):
        fm = tu_feed({"UNW669": [{"seq": 3, "stop_id": "IRVINGPK",
                                  "arr": at(2026, 10, 2, 19, 55), "delay": 60}]})
        self.assertEqual(schedule.stop_delays(fm, METRA, NOW), {"UNW669": 60})

    def test_without_a_timetable(self):
        fm = tu_feed({"232": [{"seq": 7, "delay": 300}]})
        self.assertEqual(schedule.stop_delays(fm, None, NOW), {"232": 300})

    def test_zero_is_published(self):
        fm = tu_feed({"232": [{"seq": 7, "delay": 0}]})
        self.assertEqual(schedule.stop_delays(fm, NICTD, NOW), {"232": 0})

    def test_no_trip_id_skipped(self):
        fm = tu_feed({"": [{"seq": 7, "delay": 300}]})
        self.assertEqual(schedule.stop_delays(fm, NICTD, NOW), {})


def _zip(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return b.getvalue()


class DistillV3(unittest.TestCase):
    BASE = {
        "agency.txt": "agency_id, agency_name, agency_timezone\r\nM, Metra, America/Chicago\r\n",
        "stops.txt": "stop_id, stop_name, stop_lat, stop_lon\r\n"
                     "A, Alpha, 41.8812345678, -87.6398765432\r\nB, Beta, , \r\n",
        "trips.txt": "route_id,service_id,trip_id\nR,WK,t_wk\nR,SA,t_sa\nR,SU,t_su\nR,XX,t_x\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                        "WK,1,1,1,1,1,0,0,20260101,20261231\n"
                        "SA,0,0,0,0,0,1,0,20260101,20261231\n"
                        "SU,0,0,0,0,0,0,1,20260101,20261231\n",
        "calendar_dates.txt": "service_id,date,exception_type\nXX,20261004,1\nSA,20261003,2\n",
        "stop_times.txt": "trip_id,stop_id,stop_sequence,arrival_time,departure_time\n"
                          "t_wk,A,1,07:00:30,07:01:00\nt_sa,A,1,08:00:00,08:00:00\n"
                          "t_su,A,1,,09:00:00\nt_x,A,1,24:10:00,24:10:00\n",
    }

    def test_stop_pos_rounded_and_blank_skipped(self):
        d = schedule.distill(_zip(self.BASE), stop_times=False, today=date(2026, 10, 4))
        self.assertEqual(d["stop_pos"], {"A": [41.881235, -87.639877]})
        self.assertEqual(d["v"], 3)

    def test_window_is_yesterday_today_tomorrow(self):
        # 2026-10-04 is a Sunday. Saturday 10-03 is removed for SA by
        # calendar_dates; XX is added on 10-04; WK runs Monday 10-05.
        d = schedule.distill(_zip(self.BASE), stop_times=False, today=date(2026, 10, 4))
        self.assertEqual(d["sched"], {"t_wk": {"A": 25230}, "t_su": {"A": 32400}, "t_x": {"A": 87000}})

    def test_window_moves_with_today(self):
        d = schedule.distill(_zip(self.BASE), stop_times=False, today=date(2026, 10, 1))
        self.assertEqual(set(d["sched"]), {"t_wk"})

    def test_calendar_outside_its_dates_does_not_run(self):
        files = dict(self.BASE)
        files["calendar.txt"] = files["calendar.txt"].replace("SU,0,0,0,0,0,0,1,20260101,20261231",
                                                              "SU,0,0,0,0,0,0,1,20260101,20260930")
        d = schedule.distill(_zip(files), stop_times=False, today=date(2026, 10, 4))
        self.assertNotIn("t_su", d["sched"])

    def test_no_calendar_keeps_every_trip(self):
        files = {k: v for k, v in self.BASE.items() if not k.startswith("calendar")}
        d = schedule.distill(_zip(files), stop_times=False, today=date(2026, 10, 4))
        self.assertEqual(set(d["sched"]), {"t_wk", "t_sa", "t_su", "t_x"})

    def test_no_stop_times_file(self):
        files = {k: v for k, v in self.BASE.items() if k != "stop_times.txt"}
        d = schedule.distill(_zip(files), stop_times=False, today=date(2026, 10, 4))
        self.assertEqual(d["sched"], {})

    def test_today_defaults_to_the_agency_date(self):
        d = schedule.distill(_zip(self.BASE), stop_times=False)
        self.assertIn("sched", d)


T0 = 1_000_000.0
LAT, LON = 41.880, -87.950
FAR = {"v": V, "stop_pos": {"S": [LAT + 0.004, LON]}}     # ~445 m north
NEAR = {"v": V, "stop_pos": {"S": [LAT + 0.002, LON]}}    # ~222 m north


def ob(dlat=0.0, in_service=True, trip="TRIP", train="47"):
    return Obs(vehicle_id="8528", lat=LAT + dlat, lon=LON, observed_at=None,
               in_service=in_service, train=train if in_service else None,
               line="UP-W", trip_id=trip if in_service else None)


class FleetDelay(unittest.TestCase):
    def rec(self, **kw):
        return fleet_mod.Fleet().update([ob()], T0, **kw)["vehicles"]["8528"]

    def test_truncated_toward_zero(self):
        for secs, mins in ((359, 5), (360, 6), (150, 2), (-90, -1), (-30, 0), (0, 0)):
            self.assertEqual(self.rec(trip_delays={"TRIP": secs})["delay_min"], mins, secs)

    def test_per_train_delays_truncated_too(self):
        self.assertEqual(self.rec(delays={"47": 330})["delay_min"], 5)

    def test_trip_delay_wins_over_train_delay(self):
        self.assertEqual(self.rec(trip_delays={"TRIP": 600}, delays={"47": 60})["delay_min"], 10)

    def test_train_delay_when_trip_has_none(self):
        self.assertEqual(self.rec(trip_delays={"OTHER": 600}, delays={"47": 60})["delay_min"], 1)

    def test_absent_without_either(self):
        self.assertNotIn("delay_min", self.rec(trip_delays={"OTHER": 600}))

    def test_never_on_equipment_not_in_service(self):
        r = fleet_mod.Fleet().update([ob(in_service=False)], T0, show_parked=True,
                                     trip_delays={"TRIP": 600}, delays={"47": 600})
        self.assertNotIn("delay_min", r["vehicles"]["8528"])


class Stalled(unittest.TestCase):
    def run_polls(self, polls, static=FAR, f=None):
        f = f or fleet_mod.Fleet()
        out = None
        for t, o in polls:
            out = f.update([o], T0 + t, static=static)["vehicles"].get("8528")
        return out

    def test_held_far_from_every_stop(self):
        r = self.run_polls([(0, ob()), (30, ob()), (60, ob()), (90, ob())])
        self.assertIs(r["stalled"], True)

    def test_not_before_rest_s_from_first_sight(self):
        r = self.run_polls([(0, ob()), (30, ob()), (60, ob()), (89, ob())])
        self.assertNotIn("stalled", r)

    def test_at_a_stop_within_stall_m(self):
        r = self.run_polls([(0, ob()), (90, ob())], static=NEAR)
        self.assertNotIn("stalled", r)

    def test_at_a_stop_stalled_after_platform_hold(self):
        # Lee 2026-10-04: standing at a platform long after it should have
        # left is a problem too; 366 s = twice the longest normal dwell.
        self.assertNotIn("stalled", self.run_polls([(0, ob()), (365, ob())], static=NEAR))
        self.assertIs(self.run_polls([(0, ob()), (366, ob())], static=NEAR)["stalled"], True)

    def test_platform_hold_counts_from_the_last_move(self):
        near_end = {"v": V, "stop_pos": {"S": [LAT + 0.005, LON]}}
        polls = [(0, ob()), (30, ob(0.005)), (395, ob(0.005))]
        self.assertNotIn("stalled", self.run_polls(polls, static=near_end))
        polls.append((396, ob(0.005)))
        self.assertIs(self.run_polls(polls, static=near_end)["stalled"], True)

    def test_platform_hold_settable(self):
        f = fleet_mod.Fleet(platform_hold_s=120)
        self.assertIs(self.run_polls([(0, ob()), (120, ob())], static=NEAR, f=f)["stalled"], True)

    def test_moving_is_not_stalled(self):
        r = self.run_polls([(0, ob()), (30, ob(0.003)), (60, ob(0.006)), (90, ob(0.009))],
                           static={"v": V, "stop_pos": {"S": [LAT - 0.1, LON]}})
        self.assertNotIn("stalled", r)

    def test_held_counts_from_the_last_move(self):
        far = {"v": V, "stop_pos": {"S": [LAT - 0.1, LON]}}
        polls = [(0, ob()), (30, ob(0.003)), (60, ob(0.003)), (100, ob(0.003))]
        self.assertNotIn("stalled", self.run_polls(polls, static=far))
        polls.append((121, ob(0.003)))      # moved at 30; still for more than REST_S
        self.assertIs(self.run_polls(polls, static=far)["stalled"], True)

    def test_exactly_rest_s_after_a_move_is_still_moving(self):
        # Fleet calls a vehicle moving while since-last-move <= REST_S, so at
        # exactly REST_S it is moving, and a moving train is never stalled.
        far = {"v": V, "stop_pos": {"S": [LAT - 0.1, LON]}}
        polls = [(0, ob()), (30, ob(0.003)), (30 + fleet_mod.REST_S, ob(0.003))]
        self.assertNotIn("stalled", self.run_polls(polls, static=far))

    def test_equipment_not_in_service_never(self):
        f = fleet_mod.Fleet()
        r = None
        for t in (0, 30, 60, 90, 120):
            r = f.update([ob(in_service=False)], T0 + t, static=FAR, show_parked=True)["vehicles"]["8528"]
        self.assertNotIn("stalled", r)

    def test_unknown_stops_is_not_far(self):
        for static in (None, {"v": V}, {"v": V, "stop_pos": {}}, {**FAR, "v": 2}):
            r = self.run_polls([(0, ob()), (90, ob())], static=static)
            self.assertNotIn("stalled", r, static)

    def test_one_stop_near_is_enough(self):
        both = {"v": V, "stop_pos": {"FAR": [LAT - 0.1, LON], "NEAR": [LAT + 0.002, LON]}}
        self.assertNotIn("stalled", self.run_polls([(0, ob()), (90, ob())], static=both))

    def test_threshold_is_strict_and_settable(self):
        # NEAR is ~222 m: stalled at 200, not at 300 (the default).
        f = fleet_mod.Fleet(stall_m=200)
        self.assertIs(self.run_polls([(0, ob()), (90, ob())], static=NEAR, f=f)["stalled"], True)

    def test_forgotten_vehicle_starts_over(self):
        f = fleet_mod.Fleet()
        self.run_polls([(0, ob()), (90, ob())], f=f)
        f.update([], T0 + 100)
        f.update([], T0 + 100 + fleet_mod.GRACE_S)          # gone and forgotten
        r = self.run_polls([(400, ob()), (430, ob())], f=f)
        self.assertNotIn("stalled", r)


class Defaults275(unittest.TestCase):
    def test_stall_m(self):
        self.assertEqual(fleet_mod.STALL_M, 300.0)
        self.assertEqual(fleet_mod.Fleet()._stall_m, 300.0)
        self.assertEqual(fleet_mod.PLATFORM_HOLD_S, 366)
        self.assertEqual(fleet_mod.Fleet()._platform_hold_s, 366)
        self.assertIs(gtfs.PLATFORM_HOLD_S, fleet_mod.PLATFORM_HOLD_S)
        self.assertIs(gtfs.STALL_M, fleet_mod.STALL_M)
        self.assertIs(gtfs.stop_delays, schedule.stop_delays)
        self.assertEqual(schedule.STATIC_VERSION, 3)


if __name__ == "__main__":
    unittest.main()
