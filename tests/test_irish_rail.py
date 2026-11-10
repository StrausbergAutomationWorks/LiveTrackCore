"""saw_livetrack.irish_rail: parsers on real answers of 2026-11-09 (tests/fixtures/irish_rail_*), the client's
fallback, cache, backoff, stations and timetable, with a fake opener. No network."""

import datetime as dt
import email.message
import gzip
import io
import os
import sys
import tempfile
import unittest
import urllib.error
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from saw_livetrack import irish_rail as ir  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with gzip.open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


HACON = fixture("irish_rail_hacon_20261109T0800Z.xml.gz")
CURRENT = fixture("irish_rail_current_20261109T0800Z.xml.gz")
STATIONS = fixture("irish_rail_stations_20261109.xml.gz")
EMPTY = (b'<?xml version="1.0" encoding="utf-8"?>\r\n<ArrayOfObjHaconPositions xmlns:xsi="http://www.w3.org/2001/'
         b'XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns="http://api.irishrail.ie/realtime/" />')


class Resp:
    def __init__(self, body, headers=None):
        self._b = io.BytesIO(body)
        self.headers = email.message.Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    """{(method, url): body bytes | int status | Exception}; records every call."""

    def __init__(self, routes, headers=None):
        self.routes, self.headers, self.calls = dict(routes), headers or {}, []

    def __call__(self, req, timeout=None):
        key = (req.get_method(), req.full_url)
        self.calls.append(key)
        got = self.routes.get(key, 404)
        if isinstance(got, Exception):
            raise got
        if isinstance(got, int):
            raise urllib.error.HTTPError(req.full_url, got, "x", email.message.Message(), None)
        return Resp(got, self.headers.get(key))


class Clock:
    def __init__(self, t=1_762_675_240.0):
        self.t = t

    def __call__(self):
        return self.t


def gtfs_zip(files=("routes.txt", "trips.txt", "stop_times.txt", "stops.txt", "shapes.txt")):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in files:
            z.writestr(f, "a\n1\n")
    return buf.getvalue()


class Codes(unittest.TestCase):
    def test_prefix_routing(self):
        self.assertEqual(ir.consumer_of("A700"), ir.INTERCITY)
        self.assertEqual(ir.consumer_of("B702"), ir.INTERCITY)
        self.assertEqual(ir.consumer_of("P508"), ir.COMMUTER)
        self.assertEqual(ir.consumer_of("d507"), ir.COMMUTER)
        self.assertEqual(ir.consumer_of("E805"), ir.TRANSIT)
        self.assertEqual(ir.consumer_of("BUS1"), ir.TRANSIT)
        for bad in ("X100", "", None, "A", "700", 7):
            self.assertIsNone(ir.consumer_of(bad), bad)

    def test_number(self):
        self.assertEqual(ir.number_of("A700"), "700")
        self.assertEqual(ir.number_of("b702"), "702")
        self.assertIsNone(ir.number_of("A"))
        self.assertIsNone(ir.number_of(None))


class Parsers(unittest.TestCase):
    def test_hacon_fixture(self):
        trains = ir.parse_hacon(HACON)
        self.assertGreater(len(trains), 50)
        p508 = next(t for t in trains if t["code"] == "P508")
        self.assertEqual(p508["status"], "N")
        self.assertEqual(p508["date"], "2026-11-09")
        self.assertEqual(p508["origin"], "COBH")
        self.assertEqual(p508["last_location_type"], "E")
        self.assertEqual(p508["next_location"], "RBROK")
        self.assertEqual(p508["delay_s"], 0)
        # 08:00 Irish time in November is 08:00 UTC.
        self.assertEqual(p508["origin_time"], dt.datetime(2026, 11, 9, 8, 0, tzinfo=dt.timezone.utc).timestamp())
        self.assertIsNone(p508["scheduled_arrival"])          # 01/01/1900
        self.assertIsNone(p508["message"])
        for t in trains:
            self.assertIsNotNone(ir.consumer_of(t["code"]), t["code"])
            self.assertTrue((t["lat"] is None) == (t["lon"] is None))
            if t["lat"] is not None:
                self.assertTrue(51 < t["lat"] < 55.5 and -10.5 < t["lon"] < -5.5, t)
        self.assertTrue(any(t["status"] == "R" and t["delay_s"] for t in trains))

    def test_zero_position_is_none(self):
        body = HACON.replace(b"<TrainLatitude>51.8491</TrainLatitude>", b"<TrainLatitude>0</TrainLatitude>", 1)
        body = body.replace(b"<TrainLongitude>-8.29956</TrainLongitude>", b"<TrainLongitude>0</TrainLongitude>", 1)
        p508 = next(t for t in ir.parse_hacon(body) if t["code"] == "P508")
        self.assertIsNone(p508["lat"])
        self.assertIsNone(p508["lon"])

    def test_current_fixture(self):
        trains = ir.parse_current(CURRENT)
        self.assertGreater(len(trains), 50)
        late = [t for t in trains if t["delay_s"] is not None]
        self.assertTrue(late)
        for t in late:
            self.assertEqual(t["delay_s"] % 60, 0)
        withmsg = next(t for t in trains if t["code"] == "P508")
        self.assertIn("\n", withmsg["message"])
        self.assertIsNone(withmsg["last_location"])

    def test_late_parse(self):
        body = CURRENT.replace(b"Expected Departure 08:00</PublicMessage>",
                               b"Expected Departure 08:00 (12 mins late)</PublicMessage>", 1)
        first = ir.parse_current(body)[0]
        self.assertEqual(first["delay_s"], 720)
        body = CURRENT.replace(b"Expected Departure 08:00</PublicMessage>",
                               b"(-3 mins late)</PublicMessage>", 1)
        self.assertEqual(ir.parse_current(body)[0]["delay_s"], -180)

    def test_stations(self):
        st = ir.parse_stations(STATIONS)
        self.assertEqual(len(st), 171)
        self.assertEqual(sum(1 for s in st if s["lat"] is None), 4)
        bfst = next(s for s in st if s["code"] == "BFSTC")
        self.assertEqual(bfst["name"], "Belfast")

    def test_empty_and_bad(self):
        self.assertEqual(ir.parse_hacon(EMPTY), [])
        with self.assertRaises(ir.FeedUnavailable):
            ir.parse_hacon(b"<html>maintenance</html")
        with self.assertRaises(ir.FeedUnavailable):
            ir.parse_hacon(b"<html/>")

    def test_times(self):
        self.assertIsNone(ir.parse_time("01/01/1900 00:00:00"))
        self.assertIsNone(ir.parse_time(""))
        self.assertIsNone(ir.parse_time("2026-11-09 08:00"))
        # Summer: Irish Summer Time is UTC+1.
        self.assertEqual(ir.parse_time("01/07/2026 08:00:00"),
                         dt.datetime(2026, 7, 1, 7, 0, tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(ir.parse_date("09 Nov 2026"), "2026-11-09")
        self.assertIsNone(ir.parse_date("2026-11-09"))


class ZoneFallback(unittest.TestCase):
    """Without the IANA database the module keeps Ireland's own rule: it agrees with Europe/Dublin every hour of
    2026 and 2027 (UTC hours mapped to wall time and back)."""

    def test_agrees_with_zoneinfo(self):
        try:
            from zoneinfo import ZoneInfo
            real = ZoneInfo(ir.TIME_ZONE)
        except Exception:  # noqa: BLE001
            self.skipTest("no IANA database to compare with")
        mine = ir._IrishTime()
        t = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        bad = 0
        while t.year < 2028:
            wall = t.astimezone(real).replace(tzinfo=None)
            if wall.replace(tzinfo=mine).timestamp() != t.timestamp() and not (wall.month == 10 and wall.hour == 1):
                bad += 1
            t += dt.timedelta(hours=1)
        self.assertEqual(bad, 0)
        # The repeated October hour reads as summer time (the docstring's choice), and 02:00 that day is winter.
        self.assertEqual(dt.datetime(2026, 10, 25, 1, 30, tzinfo=mine).utcoffset(), dt.timedelta(hours=1))
        self.assertEqual(dt.datetime(2026, 10, 25, 2, 0, tzinfo=mine).utcoffset(), dt.timedelta(0))
        self.assertEqual(ir._last_sunday(2026, 3), dt.date(2026, 3, 29))
        self.assertEqual(ir._last_sunday(2026, 10), dt.date(2026, 10, 25))
        self.assertEqual(ir._last_sunday(2026, 12), dt.date(2026, 12, 27))


class Client(unittest.TestCase):
    def test_hacon_then_cache(self):
        clock = Clock()
        op = Opener({("GET", ir.HACON_URL): HACON})
        c = ir.IrishRailClient([ir.INTERCITY], opener=op, clock=clock)
        a = c.for_consumer(ir.INTERCITY)
        self.assertEqual(c.source, "hacon")
        self.assertTrue(a and all(t["code"][0] in "AB" for t in a))
        clock.t += 30
        c.for_consumer(ir.COMMUTER)
        self.assertEqual(len(op.calls), 1)          # one read serves every consumer within the TTL
        clock.t += 30
        c.for_consumer(ir.INTERCITY)
        self.assertEqual(len(op.calls), 2)

    def test_fallback_to_current(self):
        op = Opener({("GET", ir.HACON_URL): 500, ("GET", ir.CURRENT_URL): CURRENT})
        c = ir.IrishRailClient(opener=op, clock=Clock())
        got = c.for_consumer(ir.TRANSIT)
        self.assertEqual(c.source, "current")
        self.assertEqual(c.hacon_failures, 1)
        self.assertTrue(got and all(t["code"][0] == "E" for t in got))
        op2 = Opener({("GET", ir.HACON_URL): b"<oops", ("GET", ir.CURRENT_URL): CURRENT})
        c2 = ir.IrishRailClient(opener=op2, clock=Clock())
        c2.poll()
        self.assertEqual(c2.source, "current")

    def test_both_fail_raises_not_empty(self):
        op = Opener({("GET", ir.HACON_URL): 503, ("GET", ir.CURRENT_URL): OSError("down")})
        c = ir.IrishRailClient(opener=op, clock=Clock())
        with self.assertRaises(ir.FeedUnavailable):
            c.poll()

    def test_429_backs_off(self):
        clock = Clock()
        op = Opener({("GET", ir.HACON_URL): 429})
        c = ir.IrishRailClient(opener=op, clock=clock)
        with self.assertRaises(ir.RateLimited):
            c.poll()
        with self.assertRaises(ir.RateLimited):
            c.poll(force=True)
        self.assertEqual(len(op.calls), 1)
        clock.t += 301
        op.routes[("GET", ir.HACON_URL)] = HACON
        self.assertTrue(c.poll(force=True))

    def test_construction_reads_no_certificate_file(self):
        # The shared registration builds the client on Home Assistant's event loop (0.2.33).
        real = ir._ssl_context
        def boom():
            raise AssertionError("TLS context built in the constructor")
        ir._ssl_context = boom
        try:
            c = ir.IrishRailClient([ir.INTERCITY])
            self.assertIsNone(c._opener_made)
        finally:
            ir._ssl_context = real
        self.assertTrue(callable(c._opener))
        self.assertIs(c._opener, c._opener)

    def test_register(self):
        c = ir.IrishRailClient(opener=Opener({}), clock=Clock())
        self.assertTrue(c.register(ir.INTERCITY))
        self.assertFalse(c.register(ir.INTERCITY))
        self.assertTrue(c.unregister(ir.INTERCITY))
        self.assertFalse(c.unregister(ir.INTERCITY))
        with self.assertRaises(ValueError):
            c.register("freight")
        with self.assertRaises(ValueError):
            ir.IrishRailClient(user=" ", opener=Opener({}))

    def test_stations_cached_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            clock = Clock()
            op = Opener({("GET", ir.STATIONS_URL): STATIONS})
            c = ir.IrishRailClient(opener=op, clock=clock, storage_dir=d)
            self.assertEqual(len(c.stations()), 171)
            self.assertTrue(os.path.exists(os.path.join(d, ir.STATIONS_NAME)))
            c.stations()
            self.assertEqual(len(op.calls), 1)
            # A fresh client reads the disk; a week later a failed read keeps it.
            op2 = Opener({("GET", ir.STATIONS_URL): 500})
            c2 = ir.IrishRailClient(opener=op2, clock=Clock(clock.t + 8 * 86400), storage_dir=d)
            self.assertEqual(len(c2.stations()), 171)
            self.assertEqual(len(op2.calls), 1)

    def test_timetable(self):
        with tempfile.TemporaryDirectory() as d:
            clock = Clock()
            lm = "Sun, 08 Nov 2026 23:06:09 GMT"
            op = Opener({("HEAD", ir.STATIC_URL): b"", ("GET", ir.STATIC_URL): gtfs_zip()},
                        headers={("HEAD", ir.STATIC_URL): {"Last-Modified": lm}})
            c = ir.IrishRailClient(opener=op, clock=clock, storage_dir=d, background=False)
            self.assertIsNone(c.static_path())
            self.assertTrue(c.refresh_static())
            self.assertEqual(c.static_version(), lm)
            self.assertTrue(os.path.exists(c.static_path()))
            clock.t += 86401
            self.assertFalse(c.refresh_static())        # same Last-Modified: HEAD only
            self.assertEqual(op.calls.count(("GET", ir.STATIC_URL)), 1)
            # A new version whose zip lacks shapes is refused; the last copy stays.
            clock.t += 86401
            op.headers[("HEAD", ir.STATIC_URL)] = {"Last-Modified": "Mon, 09 Nov 2026 23:06:09 GMT"}
            op.routes[("GET", ir.STATIC_URL)] = gtfs_zip(("routes.txt", "trips.txt", "stop_times.txt", "stops.txt"))
            self.assertFalse(c.refresh_static())
            self.assertEqual(c.static_version(), lm)
            # A new client finds the copy on disk.
            c3 = ir.IrishRailClient(opener=Opener({}), clock=clock, storage_dir=d)
            self.assertEqual(c3.static_version(), lm)

    def test_poll_starts_the_timetable_check(self):
        with tempfile.TemporaryDirectory() as d:
            op = Opener({("GET", ir.HACON_URL): EMPTY, ("HEAD", ir.STATIC_URL): b"", ("GET", ir.STATIC_URL): gtfs_zip()},
                        headers={("HEAD", ir.STATIC_URL): {"Last-Modified": "x"}})
            c = ir.IrishRailClient(opener=op, clock=Clock(), storage_dir=d, background=False)
            self.assertEqual(c.poll(), [])
            self.assertEqual(c.static_version(), "x")
            self.assertIn("timetable", c.stats())


if __name__ == "__main__":
    unittest.main()
