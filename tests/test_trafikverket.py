"""saw_livetrack.trafikverket: Sweden's shared client. Offline: a fake server stands in for Trafikverket, answering
the XML queries the client sends as the real API does (shapes MEASURED 2026-11-08/09, bin\\se_trv_capture.py)."""

import datetime as dt
import io
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from saw_livetrack import trafikverket as tv  # noqa: E402

T0 = 1794200000.0           # 2026-11-09T05:33:20Z
KEY = "a" * 32


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone(dt.timedelta(hours=1))).isoformat(timespec="milliseconds")


def pos(number, date, lat, lon, ts, speed=None, bearing=90, deleted=False):
    p = {"Train": {"AdvertisedTrainNumber": number, "JourneyPlanDepartureDate": date + "T00:00:00.000+01:00",
                   "OperationalTrainNumber": number}, "Position": {"WGS84": "POINT (%s %s)" % (lon, lat)},
         "Bearing": bearing, "TimeStamp": iso(ts), "Deleted": deleted}
    if speed is not None:
        p["Speed"] = speed
    return p


def ann(aid, number, date, sig, act, t, product="SJ Snabbtåg", kind="Tåg", operator="SJ", public=True,
        est=None, actual=None, canceled=False, modified=T0, deleted=False):
    r = {"ActivityId": aid, "AdvertisedTrainIdent": number, "ScheduledDepartureDateTime": date + "T00:00:00.000+01:00",
         "ActivityType": act, "LocationSignature": sig, "AdvertisedTimeAtLocation": iso(t), "Operator": operator,
         "TypeOfTraffic": [{"Code": "x", "Description": kind}], "Advertised": public, "Canceled": canceled,
         "Deleted": deleted, "ModifiedTime": iso(modified)}
    if product:
        r["ProductInformation"] = [{"Code": "p", "Description": product}]
    if est is not None:
        r["EstimatedTimeAtLocation"] = iso(est)
    if actual is not None:
        r["TimeAtLocation"] = iso(actual)
    return r


class Resp:
    def __init__(self, body):
        self._b = body

    def read(self, n=-1):
        return self._b if n < 0 else self._b[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Server:
    """Answers TrainPosition (with changeid), TrainAnnouncement (IN idents; ModifiedTime filter) and TrainStation."""

    def __init__(self):
        self.positions = []          # full set
        self.delta = None            # positions answered to a non-zero changeid
        self.rows = []
        self.stations = [{"LocationSignature": "Cst", "AdvertisedLocationName": "Stockholm C",
                          "Geometry": {"WGS84": "POINT (18.0582 59.3303)"}, "CountryCode": "SE"},
                         {"LocationSignature": "G", "AdvertisedLocationName": "Göteborg C",
                          "Geometry": {"WGS84": "POINT (11.9733 57.7089)"}, "CountryCode": "SE"},
                         {"LocationSignature": "X", "AdvertisedLocationName": "Nowhere", "Geometry": {}}]
        self.requests = []
        self.fail = None             # an exception to raise on the next request
        self.status = None
        self.changeid = 100

    def __call__(self, req, timeout=None):
        body = req.data.decode("utf-8")
        self.requests.append(body)
        if self.fail is not None:
            e, self.fail = self.fail, None
            raise e
        obj = re.search(r'objecttype="(\w+)"', body).group(1)
        if obj == "TrainPosition":
            cid = re.search(r'changeid="([^"]*)"', body).group(1)
            items = self.positions if cid == "0" or self.delta is None else self.delta
            self.changeid += 1
            res = {"TrainPosition": items, "INFO": {"LASTCHANGEID": str(self.changeid)}}
        elif obj == "TrainAnnouncement":
            idents = set(re.search(r'name="AdvertisedTrainIdent" value="([^"]*)"', body).group(1).split(","))
            m = re.search(r'name="ModifiedTime" value="([^"]*)"', body)
            since = dt.datetime.fromisoformat(m.group(1)).timestamp() if m else None
            items = [r for r in self.rows if r["AdvertisedTrainIdent"] in idents
                     and (since is None or dt.datetime.fromisoformat(r["ModifiedTime"]).timestamp() > since)]
            res = {"TrainAnnouncement": items}
        else:
            res = {"TrainStation": self.stations}
        return Resp(json.dumps({"RESPONSE": {"RESULT": [res]}}).encode())


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def client(server, clock=None, claims=None, storage=None):
    return tv.TrafikverketClient(claims or {(tv.INTERCITY, "sj"): KEY}, opener=server, clock=clock or Clock(),
                                 storage_dir=storage)


def sj_run(number="525", date="2026-11-09", start=T0 - 1800, product="SJ Snabbtåg", operator="SJ", kind="Tåg",
           modified=T0 - 60):
    return [ann(number + "a", number, date, "Cst", "Avgang", start, product, kind, operator, modified=modified),
            ann(number + "b", number, date, "Söc", "Ankomst", start + 900, product, kind, operator, modified=modified),
            ann(number + "c", number, date, "Söc", "Avgang", start + 960, product, kind, operator, modified=modified),
            ann(number + "d", number, date, "G", "Ankomst", start + 10000, product, kind, operator, modified=modified)]


class Routing(unittest.TestCase):
    def r(self, product, kind="Tåg", operator="SJ"):
        return tv.route_of([ann("1", "1", "2026-11-09", "Cst", "Avgang", T0, product, kind, operator)])

    def test_every_census_brand(self):
        for product, brand in tv.BRANDS.items():
            if brand == "sl_pendeltag":
                continue
            self.assertEqual(self.r(product), (tv.INTERCITY, brand), product)

    def test_traffic_types(self):
        self.assertEqual(self.r("Västtågen", "Pendeltåg"), (tv.COMMUTER, "vasttagen"))
        self.assertEqual(self.r("SL Pendeltåg", "Pendeltåg", "SLL"), (tv.COMMUTER, "sl_pendeltag"))
        self.assertEqual(self.r("Västtågen", "Buss"), (tv.TRANSIT, "vasttagen"))
        self.assertIsNone(self.r("Västtågen", "Spårvagn"))
        self.assertIsNone(self.r("SL Pendeltåg", "Tåg", "SLL"))
        self.assertIsNone(self.r("Okänd", "Buss", "XYZ"))
        self.assertIsNone(self.r("Okänd", "Pendeltåg", "XYZ"))

    def test_arlanda_express(self):
        self.assertEqual(self.r(None, "Tåg", "ATRAIN"), (tv.COMMUTER, tv.ARLANDA_EXPRESS))

    def test_norway_owns_vy(self):
        self.assertIsNone(self.r("Vy Snabbtåg", operator="VY"))
        self.assertIsNone(self.r("Vy", operator="VY"))
        self.assertIsNone(self.r(None, operator="VY"))
        # Vy's products are Norway's whoever is named operator.
        self.assertIsNone(self.r("Vy Snabbtåg", operator="SJ"))
        self.assertIsNone(self.r("Vy", operator="SJ"))
        # Värmlandstrafik is run by VY and is not Norway's.
        self.assertEqual(self.r("VTAB", operator="VY"), (tv.INTERCITY, "varmlandstrafik"))

    def test_operator_fallback(self):
        self.assertEqual(self.r("SJ Charter"), (tv.INTERCITY, "sj"))
        self.assertEqual(self.r("RDC EuroNight"), (tv.INTERCITY, "sj"))
        self.assertEqual(self.r("Abonnerat tåg", operator="TÅGAB"), (tv.INTERCITY, "tagab"))
        self.assertEqual(self.r(None, operator="MTRX"), (tv.INTERCITY, "vr_snabbtag"))
        self.assertEqual(self.r(None, operator="SNÄLL"), (tv.INTERCITY, "snalltaget"))
        self.assertIsNone(self.r("Okänd", operator="ARRIVA"))
        self.assertIsNone(self.r(None, operator=None))

    def test_inlandsbanan(self):
        self.assertEqual(self.r("Inlandsbanan", operator="IBAB"), (tv.INTERCITY, "inlandsbanan"))
        self.assertEqual(self.r("inlandsbanan sommar", operator="IBAB"), (tv.INTERCITY, "inlandsbanan"))
        self.assertFalse(tv.is_inlandsbanan("Inland"))
        self.assertFalse(tv.is_inlandsbanan(None))

    def test_empty(self):
        self.assertIsNone(tv.route_of([]))


class Fields(unittest.TestCase):
    def test_time(self):
        self.assertEqual(tv.parse_time("2026-11-09T07:12:00.000+01:00"),
                         dt.datetime(2026, 11, 9, 6, 12, tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(tv.parse_time("2026-11-09T06:12:00Z"),
                         dt.datetime(2026, 11, 9, 6, 12, tzinfo=dt.timezone.utc).timestamp())
        for bad in (None, "", " ", "x", 5):
            self.assertIsNone(tv.parse_time(bad))

    def test_point(self):
        self.assertEqual(tv.parse_point("POINT (18.0582 59.3303)"), (59.3303, 18.0582))
        self.assertEqual(tv.parse_point("POINT(18.12345678 59.1)"), (59.1, 18.123457))
        for bad in (None, "", "POINT ()", "POINT (0 0)", "POINT (18 95)", "POINT (190 59)", "LINESTRING (1 2)"):
            self.assertIsNone(tv.parse_point(bad), bad)

    def test_run_key_and_midnight(self):
        self.assertEqual(tv.run_key("525", "2026-11-09T00:00:00.000+01:00"), "525|2026-11-09")
        self.assertEqual(tv.run_key(" 525 ", "2026-11-09"), "525|2026-11-09")
        self.assertEqual(tv.local_midnight(dt.date(2026, 11, 9)), "2026-11-09T00:00:00+01:00")
        self.assertEqual(tv.local_midnight(dt.date(2026, 7, 1)), "2026-07-01T00:00:00+02:00")

    def test_position_record(self):
        r = tv.position_record(pos("525", "2026-11-09", 59.0, 17.0, T0, speed=120, bearing=270))
        self.assertEqual((r["key"], r["lat"], r["lon"], r["speed"], r["bearing"], r["ts"]),
                         ("525|2026-11-09", 59.0, 17.0, 120.0, 270.0, T0))
        self.assertIsNone(tv.position_record(pos("525", "2026-11-09", 59.0, 17.0, T0))["speed"])
        p = pos("525", "2026-11-09", 59.0, 17.0, T0)
        p["Speed"], p["Bearing"] = True, True
        r = tv.position_record(p)
        self.assertIsNone(r["speed"])
        self.assertIsNone(r["bearing"])
        for broken in ({"Train": {}}, pos("", "2026-11-09", 59, 17, T0), pos("525", "", 59, 17, T0),
                       pos("525", "2026-1-9xx", 59, 17, T0),
                       {**pos("525", "2026-11-09", 59, 17, T0), "Position": {}},
                       {**pos("525", "2026-11-09", 59, 17, T0), "TimeStamp": None}):
            self.assertIsNone(tv.position_record(broken))

    def test_call_record(self):
        c = tv.call_record(ann("i", "525", "2026-11-09", " Cst ", "Avgang", T0, est=T0 + 60, actual=T0 + 30,
                               public=False, canceled=True))
        self.assertEqual(c, {"id": "i", "sig": "Cst", "activity": "Avgang", "advertised": T0, "estimated": T0 + 60,
                             "actual": T0 + 30, "public": False, "canceled": True})
        c = tv.call_record({"ActivityId": "j"})
        self.assertTrue(c["public"])
        self.assertFalse(c["canceled"])

    def test_trips(self):
        calls = [{"advertised": T0}, {"advertised": T0 + 600}, {"advertised": None},
                 {"advertised": T0 + 600 + tv.TRIP_GAP_S}, {"advertised": T0 + 700 + tv.TRIP_GAP_S}]
        trips = tv.split_trips(calls)
        self.assertEqual([len(t) for t in trips], [3, 2])
        self.assertEqual(len(tv.split_trips(calls, gap_s=tv.TRIP_GAP_S + 1)), 1)
        self.assertEqual(tv.split_trips([]), [])
        self.assertIs(tv.trip_of(calls, T0 + 300)[0], calls[0])
        self.assertIs(tv.trip_of(calls, T0 + 650 + tv.TRIP_GAP_S)[0], calls[3])
        # Inside both padded spans, nearer the second's own: the second.
        later = T0 + 600 + tv.TRIP_GAP_S - tv.TRIP_BEFORE_S + 10
        self.assertIs(tv.trip_of(calls, later)[0], calls[3])
        # Just outside the second's padded span, inside the first's: the first, though the second is nearer.
        self.assertIs(tv.trip_of(calls, later - 20)[0], calls[0])
        self.assertIs(tv.trip_of(calls, T0 + 600 + tv.TRIP_GAP_S)[0], calls[3])     # the second's own first time
        self.assertIs(tv.trip_of(calls, T0 - tv.TRIP_BEFORE_S - 3600)[0], calls[0])  # held by neither: nearest
        self.assertEqual(tv.trip_of([{"advertised": None}], T0), [])
        self.assertEqual(tv.trip_of([], T0), [])


class Post(unittest.TestCase):
    def call(self, opener):
        return tv.post(KEY, "<QUERY/>", opener=opener)

    def test_errors(self):
        def raise_(e):
            def f(req, timeout=None):
                raise e
            return f
        hdr = {"Retry-After": "120"}
        with self.assertRaises(tv.RateLimited) as cm:
            self.call(raise_(urllib.error.HTTPError(tv.URL, 429, "x", hdr, None)))
        self.assertEqual(cm.exception.retry_after, 120.0)
        with self.assertRaises(tv.RateLimited) as cm:
            self.call(raise_(urllib.error.HTTPError(tv.URL, 429, "x", {}, None)))
        self.assertIsNone(cm.exception.retry_after)
        for code in (401, 403):
            with self.assertRaises(tv.KeyRefused):
                self.call(raise_(urllib.error.HTTPError(tv.URL, code, "x", {}, None)))
        with self.assertRaises(tv.FeedUnavailable) as cm:
            self.call(raise_(urllib.error.HTTPError(tv.URL, 500, "x", {}, None)))
        self.assertNotIsInstance(cm.exception, tv.KeyRefused)
        with self.assertRaises(tv.FeedUnavailable):
            self.call(raise_(OSError("boom")))
        for body in (b"not json", b"{}", b'{"RESPONSE": {"RESULT": []}}', b'{"RESPONSE": {"RESULT": ["x"]}}'):
            with self.assertRaises(tv.FeedUnavailable):
                self.call(lambda req, timeout=None, b=body: Resp(b))
        with self.assertRaises(tv.FeedUnavailable) as cm:
            self.call(lambda req, timeout=None: Resp(b'{"RESPONSE": {"RESULT": [{"ERROR": {"MESSAGE": "bad"}}]}}'))
        self.assertIn("bad", str(cm.exception))
        with self.assertRaises(tv.FeedUnavailable):
            self.call(lambda req, timeout=None: Resp(b'{"RESPONSE": {"RESULT": [{"ERROR": "x"}]}}'))
        big = b'{"RESPONSE": {"RESULT": [{"A": "' + b"x" * 40 + b'"}]}}'
        with self.assertRaises(tv.FeedUnavailable) as cm:
            tv.post(KEY, "<QUERY/>", opener=lambda req, timeout=None: Resp(big), max_bytes=len(big) - 1)
        self.assertIn("more than", str(cm.exception))
        self.assertEqual(tv.post(KEY, "<QUERY/>", opener=lambda req, timeout=None: Resp(big), max_bytes=len(big)),
                         {"A": "x" * 40})

    def test_the_key_is_in_the_body_and_never_in_a_message(self):
        seen = []

        def opener(req, timeout=None):
            seen.append(req)
            raise urllib.error.HTTPError(tv.URL, 401, "x", {}, None)
        with self.assertRaises(tv.KeyRefused) as cm:
            self.call(opener)
        self.assertNotIn(KEY, str(cm.exception))
        req = seen[0]
        self.assertIn('authenticationkey="%s"' % KEY, req.data.decode())
        self.assertNotIn(KEY, json.dumps(dict(req.header_items())))
        self.assertEqual(req.get_method(), "POST")
        # The key is quoted as an XML attribute.
        tv.post('a"b', "<Q/>", opener=lambda req, timeout=None: seen.append(req) or Resp(
            b'{"RESPONSE": {"RESULT": [{}]}}'))
        self.assertIn('authenticationkey=\'a"b\'', seen[-1].data.decode())

    def test_ok(self):
        self.assertEqual(self.call(lambda req, timeout=None: Resp(b'{"RESPONSE": {"RESULT": [{"A": 1}]}}')), {"A": 1})

    def test_check_key(self):
        def raise_(code):
            def f(req, timeout=None):
                raise urllib.error.HTTPError(tv.URL, code, "x", {}, None)
            return f
        self.assertEqual(tv.check_key(KEY, opener=raise_(401)), "invalid_key")
        self.assertEqual(tv.check_key(KEY, opener=raise_(500)), "cannot_connect")
        self.assertEqual(tv.check_key(KEY, opener=raise_(429)), "cannot_connect")
        seen = []
        self.assertIsNone(tv.check_key(KEY, opener=lambda req, timeout=None: seen.append(req) or Resp(
            b'{"RESPONSE": {"RESULT": [{}]}}')))
        self.assertIn('limit="1"', seen[0].data.decode())


class ClientCase(unittest.TestCase):
    def setUp(self):
        self.s = Server()
        self.clock = Clock()
        self.s.positions = [pos("525", "2026-11-09", 59.0, 17.0, T0 - 20, speed=150),
                            pos("1050", "2026-11-09", 55.6, 13.0, T0 - 15)]
        self.s.rows = sj_run() + sj_run("1050", product="Öresundståg", operator="ARRIVA")
        self.c = client(self.s, self.clock)

    def test_claims(self):
        with self.assertRaises(ValueError):
            tv.TrafikverketClient({("bus", "sj"): KEY})
        with self.assertRaises(ValueError):
            tv.TrafikverketClient({(tv.INTERCITY, ""): KEY})
        with self.assertRaises(ValueError):
            tv.TrafikverketClient({"sj": KEY})
        with self.assertRaises(ValueError):
            tv.TrafikverketClient({(tv.INTERCITY, "sj"): ""})
        with self.assertRaises(ValueError):
            tv.TrafikverketClient({}, user=" ")
        self.assertTrue(self.c.register(tv.INTERCITY, "oresundstag", KEY))
        self.assertFalse(self.c.register(tv.INTERCITY, "oresundstag", KEY))
        self.assertTrue(self.c.unregister(tv.INTERCITY, "oresundstag"))
        self.assertFalse(self.c.unregister(tv.INTERCITY, "oresundstag"))
        with self.assertRaises(ValueError):
            self.c.for_consumer(tv.INTERCITY, "oresundstag")

    def test_first_poll_joins_and_routes(self):
        got = self.c.for_consumer(tv.INTERCITY, "sj")
        self.assertEqual(len(got), 1)
        r = got[0]
        self.assertEqual((r["key"], r["number"], r["date"], r["brand"], r["product"], r["operator"]),
                         ("525|2026-11-09", "525", "2026-11-09", "sj", "SJ Snabbtåg", "SJ"))
        self.assertEqual(r["position"]["speed"], 150.0)
        self.assertEqual([c["sig"] for c in r["calls"]], ["Cst", "Söc", "Söc", "G"])
        self.assertEqual([c["activity"] for c in r["calls"]][1:3], ["Ankomst", "Avgang"])
        q = self.s.requests
        self.assertIn('changeid="0"', q[0])
        self.assertIn("$dateadd(-0.00:15:00)", q[0])
        self.assertIn('name="AdvertisedTrainIdent" value="1050,525"', q[1])
        self.assertIn('value="2026-11-09T00:00:00+01:00"', q[1])
        self.assertIn('value="2026-11-10T00:00:00+01:00"', q[1])
        # The changes read: the claimed run only, from the newest ModifiedTime less the overlap.
        self.assertIn('name="AdvertisedTrainIdent" value="525"', q[2])
        since = dt.datetime.fromtimestamp(T0 - 60 - tv.MODIFIED_OVERLAP_S, dt.timezone.utc).isoformat()
        self.assertIn('name="ModifiedTime" value="%s"' % since, q[2])
        self.assertEqual(self.c.stats()["runs_by_route"], {"intercity:sj": 1, "intercity:oresundstag": 1})

    def test_ttl_and_deltas(self):
        self.c.for_consumer(tv.INTERCITY, "sj")
        n = len(self.s.requests)
        self.c.for_consumer(tv.INTERCITY, "sj")
        self.assertEqual(len(self.s.requests), n)            # within the TTL: no read
        self.clock.t += tv.FEED_TTL_S
        self.s.delta = [pos("525", "2026-11-09", 59.1, 17.1, T0 + 5)]
        got = self.c.for_consumer(tv.INTERCITY, "sj")
        self.assertIn('changeid="101"', self.s.requests[n])
        self.assertEqual(got[0]["position"]["lat"], 59.1)
        # An older fix never replaces a newer one.
        self.clock.t += tv.FEED_TTL_S
        self.s.delta = [pos("525", "2026-11-09", 58.0, 16.0, T0 - 100)]
        self.assertEqual(self.c.for_consumer(tv.INTERCITY, "sj")[0]["position"]["lat"], 59.1)
        # Deleted.
        self.clock.t += tv.FEED_TTL_S
        self.s.delta = [pos("525", "2026-11-09", 0, 0, T0, deleted=True)]
        self.assertEqual(self.c.for_consumer(tv.INTERCITY, "sj"), [])
        self.assertEqual(self.c.stats()["positions"], 1)

    def test_force(self):
        self.c.for_consumer(tv.INTERCITY, "sj")
        n = len(self.s.requests)
        self.c.for_consumer(tv.INTERCITY, "sj", force=True)
        self.assertGreater(len(self.s.requests), n)

    def test_a_failed_read_resets_the_changeid(self):
        self.c.poll()
        self.clock.t += tv.FEED_TTL_S
        self.s.fail = urllib.error.HTTPError(tv.URL, 500, "x", {}, None)
        with self.assertRaises(tv.FeedUnavailable):
            self.c.poll()
        self.c.poll()
        last = [q for q in self.s.requests if "TrainPosition" in q][-1]
        self.assertIn('changeid="0"', last)

    def test_old_fixes_go(self):
        self.c.poll()
        self.clock.t = T0 - 20 + tv.POSITION_MAX_AGE_S + 1
        self.s.delta = []
        self.c.poll()
        self.assertEqual(self.c.stats()["positions"], 1)     # 525 aged out; 1050's fix 5 s newer stays
        self.clock.t += 6
        self.c.poll(force=True)
        self.assertEqual(self.c.stats()["positions"], 0)

    def test_changes_merge(self):
        self.c.poll()
        self.clock.t += tv.FEED_TTL_S
        self.s.delta = []
        self.s.rows.append(ann("525b", "525", "2026-11-09", "Söc", "Ankomst", T0 - 900, est=T0 - 600, modified=T0 + 10))
        self.s.rows.append(ann("525d", "525", "2026-11-09", "G", "Ankomst", T0 + 8200, deleted=True, modified=T0 + 10))
        got = self.c.for_consumer(tv.INTERCITY, "sj")
        calls = got[0]["calls"]
        self.assertEqual([c["sig"] for c in calls], ["Cst", "Söc", "Söc"])
        self.assertEqual(calls[1]["estimated"], T0 - 600)
        # The cursor moved to the newest ModifiedTime answered.
        self.clock.t += tv.FEED_TTL_S
        self.c.poll()
        since = dt.datetime.fromtimestamp(T0 + 10 - tv.MODIFIED_OVERLAP_S, dt.timezone.utc).isoformat()
        self.assertIn(since, self.s.requests[-1])

    def test_unannounced_and_unrouted(self):
        self.s.positions.append(pos("7869", "2026-11-09", 59.6, 17.9, T0 - 5))
        self.s.positions.append(pos("88", "2026-11-09", 57.7, 12.0, T0 - 5))
        self.s.rows += sj_run("88", product="Vy Snabbtåg", operator="VY")
        self.c.poll()
        st = self.c.stats()
        self.assertEqual(st["unannounced"], 1)
        self.assertEqual(st["unrouted"], {"Vy Snabbtåg": 1})
        n = len(self.s.requests)
        self.clock.t += tv.FEED_TTL_S
        self.s.delta = []
        self.c.poll()
        self.assertFalse(any("7869" in q for q in self.s.requests[n:]))        # not asked again yet
        self.clock.t = T0 + tv.UNANNOUNCED_RETRY_S
        self.c.poll()
        self.assertTrue(any("7869" in q and 'name="ModifiedTime"' not in q for q in self.s.requests[n:]))

    def test_new_runs_are_spread(self):
        self.s.positions = [pos(str(3000 + i), "2026-11-09", 59, 17, T0) for i in range(tv.NEW_RUN_BATCH
                                                                                         * tv.NEW_RUN_QUERIES + 7)]
        self.c.poll()
        reads = [q for q in self.s.requests if "TrainAnnouncement" in q and 'name="ModifiedTime"' not in q]
        self.assertEqual(len(reads), tv.NEW_RUN_QUERIES)
        self.assertEqual(self.c.stats()["unannounced"], tv.NEW_RUN_BATCH * tv.NEW_RUN_QUERIES)
        self.clock.t += tv.FEED_TTL_S
        self.s.delta = []
        self.c.poll()
        reads = [q for q in self.s.requests if "TrainAnnouncement" in q and 'name="ModifiedTime"' not in q]
        self.assertEqual(len(reads), tv.NEW_RUN_QUERIES + 1)

    def test_rows_in_order_arrivals_first_and_deleted_dropped(self):
        rows = sj_run()
        same = [ann("s1", "525", "2026-11-09", "Söc", "Avgang", T0 - 500), ann("s2", "525", "2026-11-09", "Söc",
                                                                               "Ankomst", T0 - 500)]
        self.s.rows = [rows[0]] + same + [rows[3], ann("zz", "525", "2026-11-09", "Nk", "Ankomst", T0, deleted=True,
                                                            modified=T0 - 10000)]     # older than the changes read
        got = self.c.for_consumer(tv.INTERCITY, "sj")
        self.assertEqual([(c["sig"], c["activity"]) for c in got[0]["calls"]],
                         [("Cst", "Avgang"), ("Söc", "Ankomst"), ("Söc", "Avgang"), ("G", "Ankomst")])

    def test_a_positioned_run_is_never_forgotten(self):
        self.c.poll()
        self.clock.t = T0 + tv.FORGET_S + 20000
        self.s.delta = [pos("525", "2026-11-09", 59.1, 17.1, self.clock.t - 5)]
        self.c.poll()
        self.assertEqual(len(self.c.for_consumer(tv.INTERCITY, "sj")), 1)

    def test_two_trips_one_key(self):
        self.s.positions = [pos("1165", "2026-11-09", 56.16, 15.58, T0 + 16 * 3600)]
        morning = [ann("m1", "1165", "2026-11-09", "Mc", "Avgang", T0 - 5 * 3600, "Öresundståg", operator="ARRIVA"),
                   ann("m2", "1165", "2026-11-09", "Hie", "Ankomst", T0 - 5 * 3600 + 400, "Öresundståg",
                       operator="ARRIVA")]
        evening = [ann("e1", "1165", "2026-11-09", "Ck", "Avgang", T0 + 16 * 3600, "Öresundståg", operator="ARRIVA"),
                   ann("e2", "1165", "2026-11-09", "Hm", "Ankomst", T0 + 18 * 3600, "Öresundståg", operator="ARRIVA")]
        self.s.rows = morning + evening
        self.clock.t = T0 + 16 * 3600 + 10
        c = client(self.s, self.clock, {(tv.INTERCITY, "oresundstag"): KEY})
        got = c.for_consumer(tv.INTERCITY, "oresundstag")
        self.assertEqual([x["sig"] for x in got[0]["calls"]], ["Ck", "Hm"])

    def test_forget(self):
        self.c.poll()
        self.clock.t = T0 + 9200
        self.s.delta = []
        self.c.poll()
        self.assertEqual(self.c.stats()["positions"], 0)
        self.assertEqual(self.c.stats()["runs"], 2)      # last row T0 + 8200: within FORGET_S
        self.clock.t = T0 - 1800 + 10000 + tv.FORGET_S + 1
        self.c.poll()
        self.assertEqual(self.c.stats()["runs"], 0)

    def test_stats_never_hold_a_key(self):
        self.c.poll()
        self.assertNotIn(KEY, json.dumps(self.c.stats()))
        self.assertEqual(self.c.stats()["keys"], 1)

    def test_refused_key_gives_up_after_the_window(self):
        self.s.fail = urllib.error.HTTPError(tv.URL, 401, "x", {}, None)
        with self.assertRaises(tv.KeyRefused):
            self.c.poll()
        self.clock.t += 10
        self.c.poll()                                      # works: the run of refusals ends
        self.clock.t += tv.FEED_TTL_S
        self.s.fail = urllib.error.HTTPError(tv.URL, 401, "x", {}, None)
        with self.assertRaises(tv.KeyRefused):
            self.c.poll()
        self.clock.t += 700
        self.s.fail = urllib.error.HTTPError(tv.URL, 401, "x", {}, None)
        with self.assertRaises(tv.AuthRefused):
            self.c.poll()

    def test_429_backs_off(self):
        self.s.fail = urllib.error.HTTPError(tv.URL, 429, "x", {}, None)
        with self.assertRaises(tv.RateLimited):
            self.c.poll()
        n = len(self.s.requests)
        self.clock.t += 60
        with self.assertRaises(tv.RateLimited):
            self.c.poll()
        self.assertEqual(len(self.s.requests), n)
        self.clock.t += tv.BACKOFF_S if hasattr(tv, "BACKOFF_S") else 300
        self.c.poll()
        self.assertTrue(self.c.stats()["changeid"])

    def test_stations(self):
        with tempfile.TemporaryDirectory() as d:
            c = client(self.s, self.clock, storage=d)
            self.assertIsNone(c.stations_version())
            self.assertEqual(c.stations(fetch=False), {})
            st = c.stations()
            self.assertEqual(sorted(st), ["Cst", "G"])
            self.assertEqual(st["G"], {"name": "Göteborg C", "lat": 57.7089, "lon": 11.9733, "country": "SE"})
            self.assertEqual(c.stations_version(), int(T0))
            n = len(self.s.requests)
            c.stations()
            self.assertEqual(len(self.s.requests), n)       # fresh: not read again
            self.clock.t += tv.STATIONS_RETRY_S
            c.stations()
            self.assertEqual(len(self.s.requests), n)       # past the retry wait, still within the day
            # A new client finds the disk copy.
            c2 = client(self.s, self.clock, storage=d)
            self.assertEqual(sorted(c2.stations(fetch=False)), ["Cst", "G"])
            self.assertEqual(c2.stations_version(), int(T0))
            # A day on: read again; a failure keeps the copy and waits STATIONS_RETRY_S.
            self.clock.t += tv.STATIONS_TTL_S
            self.s.fail = urllib.error.HTTPError(tv.URL, 500, "x", {}, None)
            self.assertEqual(sorted(c2.stations()), ["Cst", "G"])
            n = len(self.s.requests)
            c2.stations()
            self.assertEqual(len(self.s.requests), n)
            self.clock.t += tv.STATIONS_RETRY_S
            self.s.stations = self.s.stations[:1]
            self.assertEqual(sorted(c2.stations()), ["Cst"])
            # An empty answer keeps what was there.
            self.clock.t += tv.STATIONS_TTL_S
            self.s.stations = []
            self.assertEqual(sorted(c2.stations()), ["Cst"])
        c3 = client(self.s, self.clock)
        self.assertEqual(c3.stations(fetch=False), {})


if __name__ == "__main__":
    unittest.main()
