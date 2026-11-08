"""saw_livetrack.entur: Norway's shared client. Offline: a fake opener stands in for Entur."""

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from google.transit import gtfs_realtime_pb2 as rt  # noqa: E402

from saw_livetrack import entur  # noqa: E402

T0 = 1794086700


def vehicle(tid, rid, sd, lat, lon, mode="RAIL", public="__route__", vid=None, ts=float(T0)):
    """One GraphQL vehicle; sd YYYYMMDD or None (no datedServiceJourney)."""
    local = tid.split(":")[-1]
    return {"vehicleId": vid if vid is not None else "v" + local[-6:], "mode": mode,
            "line": {"lineRef": rid, "publicCode": rid.rsplit(":", 1)[-1] if public == "__route__" else public},
            "serviceJourney": {"id": tid},
            "datedServiceJourney": {"id": "%s:DatedServiceJourney:%s_%s-%s-%s" % (
                tid[:3], local, sd[2:4], sd[4:6], sd[6:8])} if sd else None,
            "lastUpdatedEpochSecond": ts, "location": {"latitude": lat, "longitude": lon}}


def vp_feed(rows):
    """A GraphQL vehicles answer from rows of vehicle() arguments."""
    return json.dumps({"data": {"vehicles": [vehicle(*r) if isinstance(r, tuple) else r for r in rows]}}).encode()


def tu_feed(rows):
    """rows: (trip_id, route_id, start_date, delay)"""
    fm = rt.FeedMessage()
    fm.header.gtfs_realtime_version = "2.0"
    fm.header.timestamp = T0
    for i, (tid, rid, sd, delay) in enumerate(rows):
        e = fm.entity.add()
        e.id = str(i)
        e.trip_update.trip.trip_id = tid
        e.trip_update.trip.route_id = rid
        e.trip_update.trip.start_date = sd
        s = e.trip_update.stop_time_update.add()
        s.stop_sequence = 2
        s.stop_id = "NSR:Quay:1"
        s.arrival.delay = delay
    return fm.SerializeToString()


def timetable(routes, extra_files=True):
    """A zip with routes.txt (route_id, short, type, color) and the other required files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("routes.txt", "route_id,route_short_name,route_type,route_color\n" +
                   "".join("%s,%s,%s,%s\n" % r for r in routes))
        if extra_files:
            for n in ("trips.txt", "stop_times.txt", "stops.txt"):
                z.writestr(n, "x\n")
    return buf.getvalue()


class Resp:
    def __init__(self, body, headers=None):
        self._b = io.BytesIO(body)
        self.headers = headers or {}

    def read(self, n=-1):
        return self._b.read() if n is None or n < 0 else self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    """url -> body, or an int HTTP status, or a callable. The GraphQL POST is keyed "gql:<codespace>". Records
    (method, url, headers)."""

    def __init__(self, routes, headers=None):
        self.routes = routes
        self.headers = headers or {}
        self.calls = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        if req.get_method() == "POST":
            self.posted = json.loads(req.data.decode())
            url = "gql:" + self.posted["query"].split('codespaceId: "')[1].split('"')[0]
        self.calls.append((req.get_method(), url, dict(req.header_items())))
        got = self.routes.get(url)
        if callable(got):
            got = got()
        if isinstance(got, int):
            raise urllib.error.HTTPError(url, got, "status", {}, None)
        if got is None:
            raise urllib.error.URLError("no route for " + url)
        return Resp(b"" if req.get_method() == "HEAD" else got, self.headers.get(url, {}))


VP_VYG = "gql:VYG"
TU_VYG = entur.TRIP_UPDATES_URL % "VYG"
VP_GOA = "gql:GOA"
TU_GOA = entur.TRIP_UPDATES_URL % "GOA"
ST_GOA = entur.STATIC_URL % "goa"

UUID_BUS = "VYG:ServiceJourney:33d5b4ad-6caa-4b6d-ac56-fc523e6b96ae-336-2026-11-07-0"
VYG_ROWS = [
    ("VYG:ServiceJourney:1672_443368-R", "VYG:Line:R13", "20261107", 60.24, 11.20),
    ("VYG:ServiceJourney:64-BRG_444368-R", "VYG:Line:F4", "20261107", 59.91, 10.69),
    ("VYG:ServiceJourney:2150_1-R", "VYG:Line:L1", "20261107", 59.90, 10.70),
    # A bus GraphQL calls RAIL: the trip id says bus.
    ("VYG:ServiceJourney:BUS-336_855501-R", "VYG:Line:RE10", "20261107", 60.75, 11.19),
    (UUID_BUS, "VYG:Line:RE10", None, 60.8, 11.1, "BUS"),
    ("VYG:ServiceJourney:9_9-R", "VYG:Line:X", "20261107", 60.0, 10.0),
    ("VYG:ServiceJourney:8_8-R", "VYG:Line:R12", "20261107", 60.0, 10.0, "TRAM"),
]
VYG_TU = [
    ("VYG:ServiceJourney:1672_443368-R", "VYG:Line:R13", "2026-11-07", 69),
    ("VYG:ServiceJourney:1672_443368-R", "VYG:Line:R13", "2026-11-08", 0),
    ("VYG:ServiceJourney:1110-MOS_876301-R", "VYG:Line:R21", "2026-11-08", 0),
    ("VYG:ServiceJourney:2150_1-R", "VYG:Line:L1", "2026-11-07", 30),
]


class TestRules(unittest.TestCase):
    def test_letters(self):
        self.assertEqual(entur.letters("RE10"), "RE")
        self.assertEqual(entur.letters("R13x"), "R")
        self.assertEqual(entur.letters("RX11"), "RX")
        self.assertEqual(entur.letters("F4"), "F")
        self.assertEqual(entur.letters("L2x"), "L")
        self.assertEqual(entur.letters("FLY1"), "FLY")
        self.assertIsNone(entur.letters("50"))
        self.assertIsNone(entur.letters(""))
        self.assertIsNone(entur.letters(None))
        self.assertIsNone(entur.letters("R"))

    def test_line_from_route_id(self):
        self.assertEqual(entur.line_from_route_id("VYG:Line:R13"), "R13")
        self.assertEqual(entur.line_from_route_id("VYG:Line:RE10"), "RE10")
        self.assertIsNone(entur.line_from_route_id("GOA:Line:50"))
        self.assertIsNone(entur.line_from_route_id(None))

    def test_ids(self):
        self.assertEqual(entur.codespace_of("VYG:ServiceJourney:1672_443368-R"), "VYG")
        self.assertIsNone(entur.codespace_of("1672"))
        self.assertIsNone(entur.codespace_of("vyg:ServiceJourney:1"))
        self.assertEqual(entur.local_id("GOA:ServiceJourney:715-LOD_68400-R"), "715-LOD_68400-R")
        self.assertEqual(entur.local_id("plain"), "plain")

    def test_bus(self):
        self.assertTrue(entur.is_bus("VYG:ServiceJourney:BUS-336_855501-R"))
        self.assertTrue(entur.is_bus("GOA:ServiceJourney:B704-DT_72896-R"))
        self.assertTrue(entur.is_bus(UUID_BUS))
        self.assertFalse(entur.is_bus("VYG:ServiceJourney:1672_443368-R"))
        self.assertFalse(entur.is_bus("VYG:ServiceJourney:398-138_690446-R"))
        self.assertFalse(entur.is_bus("VYG:ServiceJourney:BRG-1_1-R"))
        self.assertFalse(entur.is_bus("FLT:ServiceJourney:1-2614-3802-20261107"))
        self.assertFalse(entur.is_bus(None))

    def test_operating_date(self):
        self.assertEqual(entur.operating_date("VYG:DatedServiceJourney:1033_ASR-KVG_26-11-07"), "20261107")
        self.assertEqual(entur.operating_date("GOA:DatedServiceJourney:715_OSL-STV_26-11-08", "73-02"), "20261108")
        self.assertEqual(entur.operating_date(None, "1672-2026-11-07"), "20261107")
        self.assertEqual(entur.operating_date("x", "72-04"), "")
        self.assertEqual(entur.operating_date(None, None), "")
        # Flytoget: no datedServiceJourney, the unit as vehicle id, the date at the trip id's end (0.2.28).
        self.assertEqual(entur.operating_date(None, "71-08", "FLT:ServiceJourney:1-2614-3724-20261108"), "20261108")
        self.assertEqual(entur.operating_date(None, None, "1-2614-3724-20261231"), "20261231")
        self.assertEqual(entur.operating_date("VYG:DatedServiceJourney:1_A-B_26-11-07", None,
                                              "FLT:ServiceJourney:1-2614-3724-20261108"), "20261107")
        self.assertEqual(entur.operating_date(None, "1672-2026-11-07", "x-20261108"), "20261107")
        self.assertEqual(entur.operating_date(None, None, "FLT:ServiceJourney:1-2614-3724-20261340"), "")
        self.assertEqual(entur.operating_date(None, None, "FLT:ServiceJourney:1-2614-3724-20261132"), "")
        self.assertEqual(entur.operating_date(None, None, "VYG:ServiceJourney:1672_443368-R"), "")
        self.assertEqual(entur.operating_date(None, None, UUID_BUS), "")
        self.assertEqual(entur.operating_date(None, None, "x-2026110"), "")
        self.assertEqual(entur.operating_date(None, None, "FLT:ServiceJourney:1-20261108-3724"), "")   # the END only
        self.assertEqual(entur.operating_date(None, None, 20261108), "")

    def test_consumer_of(self):
        c = entur.consumer_of
        self.assertEqual(c("VYG", "F4", "VYG:ServiceJourney:64_1-R"), entur.INTERCITY)
        self.assertEqual(c("VYG", "RE10", "x"), entur.INTERCITY)
        self.assertEqual(c("VYG", "RX20", "x"), entur.INTERCITY)
        self.assertEqual(c("VYG", "R13x", "x"), entur.INTERCITY)
        self.assertEqual(c("GOA", "F5", "x", "RAIL"), entur.INTERCITY)
        self.assertEqual(c("VYG", "L1", "x"), entur.COMMUTER)
        self.assertEqual(c("GOA", "L5", "x"), entur.COMMUTER)
        self.assertEqual(c("FLT", "FLY1", "x"), entur.COMMUTER)
        self.assertEqual(c("FLT", None, "x"), entur.COMMUTER)
        self.assertEqual(c("VYG", "RE10", "VYG:ServiceJourney:BUS-1_1-R"), entur.BUS)
        self.assertEqual(c("VYG", "RE10", "VYG:ServiceJourney:1_1-R", "BUS"), entur.BUS)
        self.assertEqual(c("FLT", "FLY1", "FLT:ServiceJourney:B1-x"), entur.BUS)
        self.assertIsNone(c("VYG", "R12", "x", "TRAM"))
        self.assertIsNone(c("VYG", None, "x"))
        self.assertIsNone(c("GOA", "50", "x"))
        self.assertIsNone(c("VYG", "FLY1", "x"))

    def test_ymd(self):
        self.assertEqual(entur.ymd("2026-11-08"), "20261108")
        self.assertEqual(entur.ymd("20261108"), "20261108")
        self.assertEqual(entur.ymd(""), "")
        self.assertEqual(entur.ymd("2026-11-0x"), "2026-11-0x")
        self.assertEqual(entur.ymd(None), "")

    def test_codespace_checked(self):
        # SJ Nord, positionless, may be claimed since 0.2.29: for its SIRI ET only (TestEtClient).
        entur.EnturClient([(entur.INTERCITY, "SJN")])
        with self.assertRaises(ValueError):
            entur.EnturClient([(entur.INTERCITY, "XYZ")])
        with self.assertRaises(ValueError):
            entur.EnturClient([("freight", "VYG")])
        with self.assertRaises(ValueError):
            entur.EnturClient(user=" ")


class TestVehiclesFeed(unittest.TestCase):
    def test_conversion(self):
        answer = json.loads(vp_feed([
            ("VYG:ServiceJourney:1672_443368-R", "VYG:Line:R13", "20261107", 60.24, 11.20, "RAIL", "R13",
             "1672-2026-11-07", 1794086681.0),
            ("VYG:ServiceJourney:5_5-R", "VYG:Line:R13", None, 60.0, 11.0, "RAIL", "R13", "5-2026-11-06"),
            ("VYG:ServiceJourney:6_6-R", "VYG:Line:R13", None, 0, 0),
            ("VYG:ServiceJourney:7_7-R", "VYG:Line:R13", None, 60.0, 11.0, "RAIL", None, "72-04", None),
            {"vehicleId": "x", "mode": "RAIL", "serviceJourney": None, "location": {"latitude": 60, "longitude": 10}},
            {"vehicleId": "y", "mode": "RAIL", "serviceJourney": {"id": "VYG:ServiceJourney:9_9-R"}, "location": None},
            "not a vehicle",
        ]).decode())
        answer["data"]["vehicles"][0]["bearing"] = 102.3
        fm, meta = entur.vehicles_feed(answer, T0 + 5)
        self.assertEqual(fm.header.timestamp, T0 + 5)
        self.assertEqual(len(fm.entity), 3)
        a, b, c = (e.vehicle for e in fm.entity)
        self.assertEqual(a.trip.trip_id, "VYG:ServiceJourney:1672_443368-R")
        self.assertEqual(a.trip.route_id, "VYG:Line:R13")
        self.assertEqual(a.trip.start_date, "20261107")
        self.assertEqual(a.vehicle.id, "1672-2026-11-07")
        self.assertEqual(a.timestamp, 1794086681)
        self.assertAlmostEqual(a.position.latitude, 60.24, places=5)
        self.assertFalse(a.position.HasField("bearing"))          # a chord, never passed on
        self.assertEqual(b.trip.start_date, "20261106")          # from the vehicle id
        self.assertFalse(c.trip.HasField("start_date"))
        self.assertFalse(c.HasField("timestamp"))
        self.assertEqual(meta, [("RAIL", "R13"), ("RAIL", "R13"), ("RAIL", None)])

    def test_flytoget_run_date(self):
        row = {"vehicleId": "71-08", "mode": "RAIL", "line": {"lineRef": "FLT:Line:FLY1", "publicCode": "FLY1"},
               "serviceJourney": {"id": "FLT:ServiceJourney:1-2614-3724-20261108"}, "datedServiceJourney": None,
               "lastUpdatedEpochSecond": float(T0), "location": {"latitude": 59.9, "longitude": 10.5}}
        fm, _ = entur.vehicles_feed({"data": {"vehicles": [row]}}, T0)
        self.assertEqual(fm.entity[0].vehicle.trip.start_date, "20261108")
        self.assertEqual(fm.entity[0].vehicle.vehicle.id, "71-08")

    def test_fix_time_from_text(self):
        v = vehicle("VYG:ServiceJourney:1672_443368-R", "VYG:Line:R13", "20261107", 60.24, 11.20, ts=None)
        v["lastUpdated"] = "2026-11-07T21:21:15.925Z"
        w = dict(v, lastUpdated="2026-11-07T22:21:15+01:00")
        x = dict(v, lastUpdated="not a time")
        y = dict(v, lastUpdated="2026-11-07T21:21:15")          # no zone: not guessed
        fm, _ = entur.vehicles_feed({"data": {"vehicles": [v, w, x, y]}}, T0)
        stamps = [e.vehicle.timestamp if e.vehicle.HasField("timestamp") else None for e in fm.entity]
        self.assertEqual(stamps, [1794086475, 1794086475, None, None])   # floored, never ahead

    def test_bad_answers(self):
        for bad in ([], {"errors": [{"message": "nope"}]}, {"data": None}, {"data": {"vehicles": None}}):
            with self.assertRaises(entur.FeedUnavailable):
                entur.vehicles_feed(bad, T0)
        fm, meta = entur.vehicles_feed({"data": {"vehicles": []}}, T0)
        self.assertEqual((len(fm.entity), meta), (0, []))


class TestSnapshot(unittest.TestCase):
    def client(self, routes, claims, **kw):
        op = Opener(routes)
        clock = kw.pop("clock", lambda: float(T0))
        return entur.EnturClient(claims, opener=op, clock=clock, **kw), op

    def test_routing_and_runs(self):
        c, op = self.client({VP_VYG: vp_feed(VYG_ROWS), TU_VYG: tu_feed(VYG_TU)},
                            [(entur.INTERCITY, "VYG"), (entur.COMMUTER, "VYG"), (entur.BUS, "VYG")])
        vp, tu = c.for_consumer(entur.INTERCITY, "VYG")
        self.assertEqual([e.vehicle.trip.trip_id.split(":")[-1] for e in vp.entity],
                         ["1672_443368-R", "64-BRG_444368-R"])
        self.assertEqual(vp.entity[0].vehicle.trip.start_date, "20261107")
        # Today's run of 1672 only; 1110 has no vehicle, so every run of it is kept; L1 is commuter's.
        self.assertEqual([(e.trip_update.trip.trip_id.split(":")[-1], e.trip_update.trip.start_date)
                          for e in tu.entity],
                         [("1672_443368-R", "20261107"), ("1110-MOS_876301-R", "20261108")])
        self.assertEqual(c.dropped_other_runs, 1)
        cvp, ctu = c.for_consumer(entur.COMMUTER, "VYG")
        self.assertEqual([e.vehicle.trip.trip_id.split(":")[-1] for e in cvp.entity], ["2150_1-R"])
        self.assertEqual([e.trip_update.trip.start_date for e in ctu.entity], ["20261107"])
        bvp, _ = c.for_consumer(entur.BUS, "VYG")
        self.assertEqual([e.vehicle.trip.trip_id for e in bvp.entity],
                         ["VYG:ServiceJourney:BUS-336_855501-R", UUID_BUS])
        self.assertEqual(c.unrouted, 2)                           # line X; and the tram
        self.assertEqual(vp.header.timestamp, T0)

    def test_flytoget_trip_updates_kept(self):
        tid = "FLT:ServiceJourney:1-2614-3724-20261108"
        row = {"vehicleId": "71-08", "mode": "RAIL", "line": {"lineRef": "FLT:Line:FLY1", "publicCode": "FLY1"},
               "serviceJourney": {"id": tid}, "datedServiceJourney": None,
               "lastUpdatedEpochSecond": float(T0), "location": {"latitude": 59.9, "longitude": 10.5}}
        vp_flt, tu_flt = "gql:FLT", entur.TRIP_UPDATES_URL % "FLT"
        c, _ = self.client({vp_flt: json.dumps({"data": {"vehicles": [row]}}).encode(),
                            tu_flt: tu_feed([(tid, "FLT:Line:FLY1", "20261108", 60),
                                             ("FLT:ServiceJourney:1-2614-3720-20261109", "FLT:Line:FLY1",
                                              "20261109", 0)])},
                           [(entur.COMMUTER, "FLT")])
        vp, tu = c.for_consumer(entur.COMMUTER, "FLT")
        self.assertEqual(len(vp.entity), 1)
        self.assertEqual([e.trip_update.trip.trip_id for e in tu.entity],
                         [tid, "FLT:ServiceJourney:1-2614-3720-20261109"])
        self.assertEqual(c.dropped_other_runs, 0)

    def test_public_code_routes_without_timetable(self):
        rows = [("GOA:ServiceJourney:715-LOD_68400-R", "GOA:Line:50", "20261107", 58.52, 5.80, "RAIL", "F5"),
                ("GOA:ServiceJourney:3069_63130-R", "GOA:Line:59", "20261107", 58.92, 5.73, "RAIL", "L5"),
                ("GOA:ServiceJourney:717-LOD_65461-R", "GOA:Line:50", "20261107", 58.1, 7.9, "RAIL", None)]
        c, _ = self.client({VP_GOA: vp_feed(rows), TU_GOA: tu_feed([])},
                           [(entur.INTERCITY, "GOA"), (entur.COMMUTER, "GOA")])
        vp, _ = c.for_consumer(entur.INTERCITY, "GOA")
        self.assertEqual([e.vehicle.trip.trip_id.split(":")[-1] for e in vp.entity], ["715-LOD_68400-R"])
        cvp, _ = c.for_consumer(entur.COMMUTER, "GOA")
        self.assertEqual(len(cvp.entity), 1)
        self.assertEqual(c.unrouted, 1)                           # no public code, no timetable yet

    def test_headers_query_and_one_fetch_per_tick(self):
        c, op = self.client({VP_VYG: vp_feed(VYG_ROWS), TU_VYG: tu_feed(VYG_TU)},
                            [(entur.INTERCITY, "VYG"), (entur.COMMUTER, "VYG")])
        c.for_consumer(entur.INTERCITY, "VYG")
        c.for_consumer(entur.COMMUTER, "VYG")
        self.assertEqual([(m, u) for m, u, _ in op.calls], [("POST", VP_VYG), ("GET", TU_VYG)])
        self.assertEqual(op.posted, {"query": entur.VEHICLES_QUERY % "VYG"})
        for _, _, h in op.calls:
            self.assertEqual(h.get("Et-client-name"), entur.CLIENT_NAME)
            self.assertEqual(h.get("User-agent"), entur.DEFAULT_USER)
        self.assertEqual(op.calls[0][2].get("Content-type"), "application/json")

    def test_ttl(self):
        now = [float(T0)]
        c, op = self.client({VP_VYG: vp_feed(VYG_ROWS), TU_VYG: tu_feed(VYG_TU)}, [(entur.INTERCITY, "VYG")],
                            clock=lambda: now[0])
        c.snapshot("VYG")
        now[0] += entur.FEED_TTL_S - 1
        c.snapshot("VYG")
        self.assertEqual(len(op.calls), 2)
        now[0] += 2
        c.snapshot("VYG")
        self.assertEqual(len(op.calls), 4)

    def test_only_registered_codespace_and_consumer(self):
        c, op = self.client({VP_VYG: vp_feed(VYG_ROWS), TU_VYG: tu_feed(VYG_TU)}, [(entur.INTERCITY, "VYG")])
        with self.assertRaises(ValueError):
            c.for_consumer(entur.COMMUTER, "VYG")
        with self.assertRaises(ValueError):
            c.for_consumer(entur.INTERCITY, "GOA")
        self.assertEqual(c.codespaces(), frozenset({"VYG"}))
        c.for_consumer(entur.INTERCITY, "VYG")
        self.assertTrue(c.register(entur.COMMUTER, "VYG"))
        self.assertFalse(c.register(entur.COMMUTER, "VYG"))
        # Registered after the snapshot, inside its TTL: it is built again for it.
        n = c.fetch_count
        cvp, _ = c.for_consumer(entur.COMMUTER, "VYG")
        self.assertEqual(len(cvp.entity), 1)
        self.assertEqual(c.fetch_count, n + 2)
        self.assertTrue(c.unregister(entur.COMMUTER, "VYG"))
        self.assertFalse(c.unregister(entur.COMMUTER, "VYG"))
        self.assertEqual(c.claims, frozenset({(entur.INTERCITY, "VYG")}))

    def test_updates_fail_positions_do_not(self):
        c, _ = self.client({VP_VYG: vp_feed(VYG_ROWS), TU_VYG: 503}, [(entur.INTERCITY, "VYG")])
        vp, tu = c.for_consumer(entur.INTERCITY, "VYG")
        self.assertEqual(len(vp.entity), 2)
        self.assertIsNone(tu)

    def test_positions_fail(self):
        for body, err in ((503, entur.FeedUnavailable), (429, entur.RateLimited),
                          (b"\xff\xfe not json", entur.FeedUnavailable),
                          (json.dumps({"errors": [{"message": "x"}]}).encode(), entur.FeedUnavailable)):
            c, _ = self.client({VP_VYG: body, TU_VYG: tu_feed(VYG_TU)}, [(entur.INTERCITY, "VYG")])
            with self.assertRaises(err):
                c.for_consumer(entur.INTERCITY, "VYG")

    def test_oversized_answer_refused(self):
        c, _ = self.client({VP_VYG: b" " * (entur.VEHICLES_MAX_BYTES + 1)}, [(entur.INTERCITY, "VYG")])
        with self.assertRaises(entur.FeedUnavailable):
            c.for_consumer(entur.INTERCITY, "VYG")

    def test_train_wins_a_shared_trip_id(self):
        tid = "VYG:ServiceJourney:1672_443368-R"
        rows = [(tid, "VYG:Line:R13", "20261107", 60.0, 11.0, "BUS", "R13"),
                (tid, "VYG:Line:R13", "20261107", 60.1, 11.1, "RAIL", "R13")]
        c, _ = self.client({VP_VYG: vp_feed(rows), TU_VYG: tu_feed([(tid, "VYG:Line:R13", "2026-11-07", 5)])},
                           [(entur.INTERCITY, "VYG")])
        vp, tu = c.for_consumer(entur.INTERCITY, "VYG")
        self.assertEqual(len(vp.entity), 1)
        self.assertEqual(len(tu.entity), 1)
        # The train first and the bus after: the train still owns the trip's update.
        c, _ = self.client({VP_VYG: vp_feed(rows[::-1]),
                            TU_VYG: tu_feed([(tid, "VYG:Line:R13", "2026-11-07", 5)])},
                           [(entur.INTERCITY, "VYG"), (entur.BUS, "VYG")])
        vp, tu = c.for_consumer(entur.INTERCITY, "VYG")
        self.assertEqual((len(vp.entity), len(tu.entity)), (1, 1))
        self.assertEqual(len(c.for_consumer(entur.BUS, "VYG")[1].entity), 0)

    def test_update_follows_its_vehicle_not_its_route(self):
        # The vehicle's public code says F5; the update's route id says nothing (no timetable yet).
        tid = "GOA:ServiceJourney:715-LOD_68400-R"
        rows = [(tid, "GOA:Line:50", "20261107", 58.5, 5.8, "RAIL", "F5")]
        c, _ = self.client({VP_GOA: vp_feed(rows), TU_GOA: tu_feed([(tid, "GOA:Line:50", "2026-11-07", 5)])},
                           [(entur.INTERCITY, "GOA")])
        _, tu = c.for_consumer(entur.INTERCITY, "GOA")
        self.assertEqual(len(tu.entity), 1)

    def test_a_public_code_that_is_no_line_is_not_trusted(self):
        rows = [("VYG:ServiceJourney:1672_443368-R", "VYG:Line:R13", "20261107", 60.0, 11.0, "RAIL", "50")]
        c, _ = self.client({VP_VYG: vp_feed(rows), TU_VYG: tu_feed([])}, [(entur.INTERCITY, "VYG")])
        vp, _ = c.for_consumer(entur.INTERCITY, "VYG")
        self.assertEqual(len(vp.entity), 1)                       # the route id's R13 decides
        self.assertEqual(c.unrouted, 0)


class TestEdges(unittest.TestCase):
    def test_letters_whole_code_only(self):
        self.assertIsNone(entur.letters("RE10-2"))
        self.assertIsNone(entur.letters("R13xy"))

    def test_route_colour_upper(self):
        path = os.path.join(tempfile.mkdtemp(), "t.zip")
        with open(path, "wb") as fh:
            fh.write(timetable([("VYG:Line:R13", "R13", "100", "df2027")]))
        self.assertEqual(entur.route_table(path)["VYG:Line:R13"]["color"], "DF2027")

    def test_get_429_is_rate_limited(self):
        with self.assertRaises(entur.RateLimited):
            entur._get("https://x/u", "ua", 5, Opener({"https://x/u": 429}))
        with self.assertRaises(entur.FeedUnavailable):
            entur._get("https://x/u", "ua", 5, Opener({"https://x/u": 503}))
        self.assertEqual(entur._get("https://x/u", "ua", 5, Opener({"https://x/u": b"ok"}))[0], b"ok")

    def test_post_size_cap(self):
        body = b"[1,2,3,4,5]"                                     # 11 bytes of good JSON
        with self.assertRaises(entur.FeedUnavailable):
            entur._post_json("https://x/u", {}, "ua", 5, lambda req, timeout=None: Resp(body), max_bytes=10)
        self.assertEqual(entur._post_json("https://x/u", {}, "ua", 5, lambda req, timeout=None: Resp(body), max_bytes=11),
                         [1, 2, 3, 4, 5])

    def test_undecodable_trip_updates_raise(self):
        with self.assertRaises(entur.FeedUnavailable):
            entur._decode(b"\xff\xff\xff not protobuf", "u")

    def test_fix_time_edges(self):
        self.assertIsNone(entur._fix_time({"lastUpdatedEpochSecond": 0}))
        self.assertIsNone(entur._fix_time({"lastUpdatedEpochSecond": True}))
        self.assertEqual(entur._fix_time({"lastUpdatedEpochSecond": 1794086681.9}), 1794086681)   # floored

    def test_static_size_cap(self):
        d = tempfile.mkdtemp()
        st = entur.STATIC_URL % "goa"
        c = entur.EnturClient([(entur.INTERCITY, "GOA")], opener=Opener({st: timetable(GOA_TT)}, {st: {"Last-Modified": "v1"}}),
                              clock=lambda: float(T0), storage_dir=d, background=False)
        old = entur.STATIC_MAX_BYTES
        entur.STATIC_MAX_BYTES = 64
        try:
            self.assertFalse(c.refresh_static("GOA", force=True))
        finally:
            entur.STATIC_MAX_BYTES = old
        self.assertIsNone(c.static_path("GOA"))
        self.assertFalse(os.path.exists(os.path.join(d, "saw_entur_goa.zip.tmp")))

    def test_a_zip_without_its_version_is_not_served(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "saw_entur_goa.zip"), "wb") as fh:
            fh.write(timetable(GOA_TT))
        c = entur.EnturClient([(entur.INTERCITY, "GOA")], opener=Opener({}), storage_dir=d, background=False)
        self.assertIsNone(c.static_path("GOA"))


GOA_TT = [("GOA:Line:50", "F5", "100", "0054A6"), ("GOA:Line:59", "L5", "100", "8DC63F"),
          ("GOA:Line:53", "R50", "100", "DF2027")]


class TestTimetable(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def client(self, routes, claims, headers=None, **kw):
        op = Opener(routes, headers)
        return entur.EnturClient(claims, opener=op, clock=lambda: float(T0), storage_dir=self.dir,
                                 background=False, **kw), op

    def test_route_from_timetable_when_no_public_code(self):
        rows = [("GOA:ServiceJourney:715-LOD_68400-R", "GOA:Line:50", "20261107", 58.52, 5.80, "RAIL", None),
                ("GOA:ServiceJourney:3069_63130-R", "GOA:Line:59", "20261107", 58.92, 5.73, "RAIL", None)]
        routes = {VP_GOA: vp_feed(rows), TU_GOA: tu_feed([("GOA:ServiceJourney:715-LOD_68400-R", "GOA:Line:50",
                                                            "2026-11-07", -6)]), ST_GOA: 503}
        c, _ = self.client(routes, [(entur.INTERCITY, "GOA"), (entur.COMMUTER, "GOA")])
        vp, _ = c.for_consumer(entur.INTERCITY, "GOA")
        self.assertEqual(len(vp.entity), 0)                  # GOA:Line:50 says nothing without a timetable
        self.assertEqual(c.unrouted, 2)
        routes[ST_GOA] = timetable(GOA_TT)
        c._static_retry["GOA"] = 0.0
        self.assertTrue(c.refresh_static("GOA", force=True))
        vp, tu = c.for_consumer(entur.INTERCITY, "GOA", force=True)
        self.assertEqual([e.vehicle.trip.trip_id.split(":")[-1] for e in vp.entity], ["715-LOD_68400-R"])
        self.assertEqual(tu.entity[0].trip_update.trip.start_date, "20261107")
        cvp, _ = c.for_consumer(entur.COMMUTER, "GOA")
        self.assertEqual([e.vehicle.trip.trip_id.split(":")[-1] for e in cvp.entity], ["3069_63130-R"])
        self.assertEqual(c.routes("GOA")["GOA:Line:50"], {"line": "F5", "type": "100", "color": "0054A6"})

    def test_disk_version_and_reload(self):
        routes = {ST_GOA: timetable(GOA_TT)}
        heads = {ST_GOA: {"Last-Modified": "Fri, 06 Nov 2026 23:27:32 GMT"}}
        c, op = self.client(routes, [(entur.INTERCITY, "GOA")], headers=heads)
        self.assertIsNone(c.static_path("GOA"))
        self.assertTrue(c.refresh_static("GOA", force=True))
        path = c.static_path("GOA")
        self.assertEqual(os.path.basename(path), "saw_entur_goa.zip")
        self.assertEqual(c.static_version("GOA"), "Fri, 06 Nov 2026 23:27:32 GMT")
        with open(os.path.join(self.dir, "saw_entur_goa.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["version"], "Fri, 06 Nov 2026 23:27:32 GMT")
        # The same Last-Modified: a HEAD only.
        n = len(op.calls)
        self.assertFalse(c.refresh_static("GOA", force=True))
        self.assertEqual([m for m, _, _ in op.calls[n:]], ["HEAD"])
        # A new client finds the copy on disk, with no request.
        c2, op2 = self.client({}, [(entur.INTERCITY, "GOA")])
        self.assertEqual(c2.static_version("GOA"), "Fri, 06 Nov 2026 23:27:32 GMT")
        self.assertEqual(len(c2.routes("GOA")), 3)
        self.assertEqual(op2.calls, [])

    def test_bad_zip_keeps_last_copy(self):
        routes = {ST_GOA: timetable(GOA_TT)}
        heads = {ST_GOA: {"Last-Modified": "v1"}}
        c, _ = self.client(routes, [(entur.INTERCITY, "GOA")], headers=heads)
        c.refresh_static("GOA", force=True)
        routes[ST_GOA] = timetable(GOA_TT, extra_files=False)
        heads[ST_GOA] = {"Last-Modified": "v2"}
        self.assertFalse(c.refresh_static("GOA", force=True))
        self.assertEqual(c.static_version("GOA"), "v1")
        self.assertEqual(len(c.routes("GOA")), 3)
        self.assertFalse(os.path.exists(c.static_path("GOA") + ".tmp"))
        routes[ST_GOA] = b"not a zip"
        self.assertFalse(c.refresh_static("GOA", force=True))
        self.assertEqual(c.static_version("GOA"), "v1")

    def test_due_daily_and_retry(self):
        now = [float(T0)]
        routes = {ST_GOA: timetable(GOA_TT)}
        heads = {ST_GOA: {"Last-Modified": "v1"}}
        op = Opener(routes, heads)
        c = entur.EnturClient([(entur.INTERCITY, "GOA")], opener=op, clock=lambda: now[0], storage_dir=self.dir,
                              background=False)
        self.assertTrue(c.refresh_static("GOA"))
        n = len(op.calls)
        now[0] += entur.STATIC_TTL_S - 10
        self.assertFalse(c.refresh_static("GOA"))
        self.assertEqual(len(op.calls), n)
        now[0] += 20
        c.refresh_static("GOA")
        self.assertEqual(len(op.calls), n + 1)                # due again: a HEAD

    def test_failed_download_retried_after_an_hour(self):
        now = [float(T0)]
        routes = {ST_GOA: 503}
        op = Opener(routes)
        c = entur.EnturClient([(entur.INTERCITY, "GOA")], opener=op, clock=lambda: now[0], storage_dir=self.dir,
                              background=False)
        self.assertFalse(c.refresh_static("GOA"))
        n = len(op.calls)
        now[0] += entur.STATIC_RETRY_S - 1
        self.assertFalse(c.refresh_static("GOA"))
        self.assertEqual(len(op.calls), n)
        now[0] += 2
        c.refresh_static("GOA")
        self.assertEqual(len(op.calls), n + 1)

    def test_no_storage_no_timetable(self):
        c = entur.EnturClient([(entur.INTERCITY, "VYG")], opener=Opener({}))
        self.assertIsNone(c.static_path("VYG"))
        self.assertIsNone(c.static_version("VYG"))
        self.assertEqual(c.routes("VYG"), {})
        self.assertFalse(c.refresh_static("VYG"))

    def test_stats(self):
        c, _ = self.client({ST_GOA: timetable(GOA_TT)}, [(entur.INTERCITY, "GOA")],
                           headers={ST_GOA: {"Last-Modified": "v1"}})
        c.refresh_static("GOA", force=True)
        s = c.stats()
        self.assertEqual(s["timetables"], {"GOA": "v1"})
        self.assertEqual(s["timetable_routes"], {"GOA": 3})


class TestTunnels(unittest.TestCase):
    OSLO_S = (59.91093, 10.75328)
    NATIONALTHEATRET = (59.91539, 10.72813)
    SKOYEN = (59.92235, 10.67883)
    LYSAKER = (59.91309, 10.63774)
    SANDVIKA = (59.89290, 10.52575)
    DRAMMEN = (59.74010, 10.20524)
    ASKER = (59.83334, 10.43449)

    def row(self, tid):
        return next(t for t in entur.TUNNELS if t[0] == tid)

    def test_table_shape(self):
        self.assertEqual(len(entur.TUNNELS), 14)
        self.assertEqual(len({t[0] for t in entur.TUNNELS}), 14)
        for t in entur.TUNNELS:
            self.assertGreater(entur.metres(t[1], t[2]), 3000.0)
            self.assertGreater(t[3], 0)

    def test_oslo_s_westbound(self):
        t = entur.tunnel_at((59.91168, 10.75219), self.OSLO_S, self.NATIONALTHEATRET)
        self.assertEqual(t[0], "oslotunnelen_w")

    def test_come_out_is_not_sent_back(self):
        # A train standing at Oslo S having come EAST through Oslotunnelen: east's comeback, west's way in, going east.
        self.assertIsNone(entur.tunnel_at((59.91059, 10.75485), self.NATIONALTHEATRET, self.OSLO_S))

    def test_direction_decides(self):
        # Drammen to Oslo S is going east: the westbound Lysaker-Sandvika way in near Lysaker is not it.
        self.assertIsNone(entur.tunnel_at((59.91486, 10.62438), self.DRAMMEN, self.OSLO_S))
        self.assertEqual(entur.tunnel_at((59.91486, 10.62438), self.LYSAKER, self.SANDVIKA)[0], "lysaker_sandvika_w")

    def test_inside_counts(self):
        r = self.row("romeriksporten_e")
        inside = (r[1][0] + 0.3 * (r[2][0] - r[1][0]), r[1][1] + 0.3 * (r[2][1] - r[1][1]))
        self.assertGreater(entur.metres(inside, r[1]), entur.TUNNEL_ENTRY_M)
        self.assertEqual(entur.tunnel_at(inside, self.OSLO_S, (59.95322, 11.04468))[0], "romeriksporten_e")

    def test_far_and_missing(self):
        self.assertIsNone(entur.tunnel_at((60.39, 5.33), (60.39, 5.33), (60.42, 5.46)))
        self.assertIsNone(entur.tunnel_at((59.91168, 10.75219), None, self.NATIONALTHEATRET))
        self.assertIsNone(entur.tunnel_at((59.91168, 10.75219), self.OSLO_S, None))

    def test_entry_edge(self):
        r = self.row("lieraasen_w")
        k = entur.TUNNEL_ENTRY_M / entur.metres(r[1], r[2])
        just_in = (r[1][0] - 0.9 * k * (r[2][0] - r[1][0]), r[1][1] - 0.9 * k * (r[2][1] - r[1][1]))
        just_out = (r[1][0] - 1.2 * k * (r[2][0] - r[1][0]), r[1][1] - 1.2 * k * (r[2][1] - r[1][1]))
        self.assertEqual(entur.tunnel_at(just_in, self.ASKER, self.DRAMMEN)[0], "lieraasen_w")
        self.assertIsNone(entur.tunnel_at(just_out, self.ASKER, self.DRAMMEN))

    def test_nearest_way_in(self):
        a = ("a", (59.0, 10.0), (59.05, 10.0), 100.0, 1)
        b = ("b", (59.003, 10.0), (59.06, 10.0), 100.0, 1)
        self.assertEqual(entur.tunnel_at((59.0029, 10.0), (58.9, 10.0), (59.2, 10.0), (a, b))[0], "b")
        self.assertEqual(entur.tunnel_at((59.0001, 10.0), (58.9, 10.0), (59.2, 10.0), (b, a))[0], "a")

    def test_time(self):
        r = self.row("romeriksporten_e")
        self.assertAlmostEqual(entur.tunnel_time(r[1], r), 331.0)
        half = ((r[1][0] + r[2][0]) / 2, (r[1][1] + r[2][1]) / 2)
        self.assertAlmostEqual(entur.tunnel_time(half, r), 331.0 * entur.metres(half, r[2]) / entur.metres(r[1], r[2]))
        before = (r[1][0] - 0.002, r[1][1] - 0.01)
        self.assertAlmostEqual(entur.tunnel_time(before, r), 331.0)
        self.assertEqual(entur.tunnel_time((1.0, 1.0), ("z", (1.0, 1.0), (1.0, 1.0), 50.0, 1)), 50.0)

    def test_metres(self):
        self.assertAlmostEqual(entur.metres((60.0, 10.0), (60.0, 10.0)), 0.0)
        self.assertAlmostEqual(entur.metres((60.0, 10.0), (60.01, 10.0)), 1111.95, places=0)
        self.assertAlmostEqual(entur.metres((60.0, 10.0), (60.0, 10.02)), 1111.95, places=0)


# --------------------------------------------------------------------------
# SIRI ET (0.2.29): SJ Nord, which publishes no positions
# --------------------------------------------------------------------------

ET_URL_BNR = entur.ET_URL % "BNR"


def siri_body(*journeys):
    """A Bane NOR-shaped SIRI ET body, as captured 2026-11-07/08: DatedVehicleJourneyRef directly on the journey."""
    def call(tag, order, quay, name, **times):
        t = "".join(f"<{k}>{v}</{k}>" for k, v in times.items())
        return (f"<{tag}><StopPointRef>{quay}</StopPointRef><Order>{order}</Order><StopPointName>{name}</StopPointName>"
                f"{t}</{tag}>")
    out = []
    for line, dvj, recorded, estimated, extra in journeys:
        out.append(f"<EstimatedVehicleJourney><LineRef>{line}</LineRef><DatedVehicleJourneyRef>{dvj}"
                   f"</DatedVehicleJourneyRef>{extra}<RecordedCalls>{''.join(call('RecordedCall', *c[:3], **c[3]) for c in recorded)}"
                   f"</RecordedCalls><EstimatedCalls>{''.join(call('EstimatedCall', *c[:3], **c[3]) for c in estimated)}"
                   f"</EstimatedCalls></EstimatedVehicleJourney>")
    return ('<?xml version="1.0" encoding="UTF-8"?><Siri xmlns="http://www.siri.org.uk/siri" version="2.0">'
            "<ServiceDelivery><EstimatedTimetableDelivery><EstimatedJourneyVersionFrame>" + "".join(out) +
            "</EstimatedJourneyVersionFrame></EstimatedTimetableDelivery></ServiceDelivery></Siri>").encode("utf-8")


SJN_453 = ("SJN:Line:R70", "453:2026-11-07",
           [(2, "NSR:Quay:618", "Kvål", {"AimedArrivalTime": "2026-11-07T20:41:30+01:00",
                                           "ActualArrivalTime": "2026-11-07T20:40:42.904040342+01:00"}),
            (1, "NSR:Quay:664", "Ler", {"AimedDepartureTime": "2026-11-07T20:37:00+01:00",
                                         "ActualDepartureTime": "2026-11-07T20:37:41+01:00"})],
           [(3, "NSR:Quay:111911", "Melhus", {"AimedArrivalTime": "2026-11-07T20:47:00+01:00",
                                               "ExpectedArrivalTime": "2026-11-07T20:47:30+01:00",
                                               "Cancellation": "false"}),
            (4, "NSR:Quay:999", "Heimdal", {"AimedArrivalTime": "2026-11-07T20:55:00+01:00",
                                             "Cancellation": "true"})], "<VehicleRef>453</VehicleRef>")
SJN_L1 = ("SJN:Line:L9", "9001:2026-11-08", [], [], "")
SJV_F1 = ("SJV:Line:F1", "40:2026-11-07", [], [(1, "NSR:Quay:1", "Oslo S", {"AimedDepartureTime":
                                                                             "2026-11-07T20:00:00+01:00"})], "")
SJN_CANCELLED = ("SJN:Line:F6", "405:2026-11-08", [], [(1, "NSR:Quay:2", "Oslo S", {})],
                 "<Cancellation>true</Cancellation>")
SJN_NO_LINE = ("SJN:Line:-", "7777:2026-11-08", [], [], "")
SJN_NO_NUMBER = ("SJN:Line:R70", "x:2026-11-08", [], [], "")


class TestSiriEt(unittest.TestCase):
    def test_time(self):
        self.assertEqual(entur.siri_time("2026-11-07T20:37:41+01:00"), 1794080261.0)
        self.assertAlmostEqual(entur.siri_time("2026-11-07T22:34:05.904040342+01:00"), 1794087245.904040, places=5)
        self.assertEqual(entur.siri_time("2026-11-07T19:37:41Z"), 1794080261.0)
        for bad in (None, "", "  ", "2026-11-07T20:37:41", "soon", 5):
            self.assertIsNone(entur.siri_time(bad), bad)

    def test_journeys(self):
        js = entur.et_journeys(siri_body(SJN_453, SJV_F1, SJN_CANCELLED, SJN_NO_LINE, SJN_NO_NUMBER), "SJN")
        self.assertEqual([j["id"] for j in js], ["453:2026-11-07", "405:2026-11-08", "7777:2026-11-08"])
        j = js[0]
        self.assertEqual((j["number"], j["date"], j["line"], j["cancelled"]), ("453", "20261107", "R70", False))
        self.assertEqual([c["order"] for c in j["calls"]], [1, 2, 3, 4])    # by Order, whatever the body's order
        ler, kval, melhus, heimdal = j["calls"]
        self.assertIs(heimdal["cancelled"], True)
        self.assertEqual((ler["stop"], ler["name"], ler["recorded"]), ("NSR:Quay:664", "Ler", True))
        self.assertEqual(ler["actual_departure"], 1794080261.0)
        self.assertIsNone(ler["expected_departure"])
        self.assertEqual(kval["name"], "Kvål")
        self.assertAlmostEqual(kval["actual_arrival"], 1794080442.904040, places=5)
        self.assertEqual((melhus["recorded"], melhus["cancelled"], melhus["expected_arrival"]),
                         (False, False, 1794080850.0))
        self.assertEqual(set(entur.ET_TIMES) <= set(melhus), True)
        self.assertIs(js[1]["cancelled"], True)
        self.assertIsNone(js[2]["line"])
        self.assertEqual(entur.et_journeys(siri_body(SJV_F1), "SJV")[0]["number"], "40")

    def test_vehicle_ref_when_the_id_has_no_number(self):
        body = siri_body(("SJN:Line:R70", "x:2026-11-08", [], [], "<VehicleRef>0461</VehicleRef>"))
        j, = entur.et_journeys(body, "SJN")
        self.assertEqual((j["number"], j["id"]), ("461", "x:2026-11-08"))

    def test_not_xml(self):
        with self.assertRaises(entur.FeedUnavailable):
            entur.et_journeys(b"<Siri", "SJN")


class TestEtClient(unittest.TestCase):
    def client(self, body=None, clock=None, claims=((entur.INTERCITY, "SJN"),)):
        op = Opener({ET_URL_BNR: siri_body(SJN_453, SJN_NO_LINE, SJN_L1) if body is None else body})
        return entur.EnturClient(list(claims), opener=op, clock=clock or (lambda: float(T0))), op

    def test_sjn_is_known_for_et_only(self):
        c, _ = self.client()
        self.assertIn((entur.INTERCITY, "SJN"), c.claims)
        with self.assertRaises(ValueError):
            c.for_consumer(entur.INTERCITY, "SJN")          # no positions
        with self.assertRaises(ValueError):
            c.snapshot("SJN")
        with self.assertRaises(ValueError):
            c.et("VYG")                                     # no ET dataset
        with self.assertRaises(ValueError):
            c.register(entur.INTERCITY, "XXX")
        self.assertTrue(c.register(entur.COMMUTER, "SJN"))
        self.assertTrue(c.unregister(entur.COMMUTER, "SJN"))

    def test_routed_by_line_letter_and_read_once_a_minute(self):
        now = [float(T0)]
        c, op = self.client(clock=lambda: now[0])
        js = c.et_for_consumer(entur.INTERCITY, "SJN")
        # The journey with no line goes to nobody; the L line to the commuter consumer, not registered here.
        self.assertEqual([j["id"] for j in js], ["453:2026-11-07"])
        self.assertEqual(c.unrouted, 1)
        self.assertEqual(op.calls[0][1], ET_URL_BNR)
        headers = {k.lower(): v for k, v in op.calls[0][2].items()}
        self.assertEqual(headers["et-client-name"], entur.CLIENT_NAME)
        now[0] += 59           # Entur's SIRI REST: 4 requests a minute; one a minute here
        c.et_for_consumer(entur.INTERCITY, "SJN")
        self.assertEqual(len(op.calls), 1)
        now[0] += 1
        c.et_for_consumer(entur.INTERCITY, "SJN")
        self.assertEqual(len(op.calls), 2)
        c.et_for_consumer(entur.INTERCITY, "SJN", force=True)
        self.assertEqual(len(op.calls), 3)

    def test_unregistered_consumer_refused(self):
        c, _ = self.client()
        with self.assertRaises(ValueError):
            c.et_for_consumer(entur.COMMUTER, "SJN")

    def test_registered_after_a_read_gets_one(self):
        c, op = self.client(claims=((entur.INTERCITY, "SJN"),))
        c.et("SJN")
        c.register(entur.COMMUTER, "SJN")
        self.assertEqual([j["id"] for j in c.et_for_consumer(entur.COMMUTER, "SJN")], ["9001:2026-11-08"])
        self.assertEqual(len(op.calls), 2)

    def test_failures_raise_and_keep_nothing(self):
        for status, err in ((429, entur.RateLimited), (503, entur.FeedUnavailable)):
            c = entur.EnturClient([(entur.INTERCITY, "SJN")], opener=Opener({ET_URL_BNR: status}),
                                  clock=lambda: float(T0))
            with self.assertRaises(err):
                c.et_for_consumer(entur.INTERCITY, "SJN")
        c, op = self.client(body=b"not xml")
        with self.assertRaises(entur.FeedUnavailable):
            c.et_for_consumer(entur.INTERCITY, "SJN")
        op.routes[ET_URL_BNR] = siri_body(SJN_453)
        self.assertEqual(len(c.et_for_consumer(entur.INTERCITY, "SJN")), 1)     # asked again at once

    def test_too_big(self):
        # A good body, too big: refused before it is read.
        c, _ = self.client(body=siri_body(SJN_453) + b" " * entur.ET_MAX_BYTES)
        with self.assertRaises(entur.FeedUnavailable):
            c.et("SJN")


if __name__ == "__main__":
    unittest.main()
