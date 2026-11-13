"""Tests for Sydney Trains in saw_livetrack.tfnsw (0.2.37), its reference registration and gtfs.realtime.sequence_by_stop.

Every case is a rule of 35_Dependencies/ours/saw-amtraker-client/DESIGN_319_sydneytrains_20261112.md (approved as written
by Lee 2026-11-12, D7: intercity merges Sydney Trains' CTY) or a MEASURED finding of its capture (bin\\nsw319\\rec\\) or
of bin\\saw0237_ids*.txt. The network is a fake. The clock is Friday 2026-11-13 14:20 Sydney, inside the capture.
Run:  python tests/test_tfnsw_sydneytrains.py  -> exit 0
"""
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.error
import zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "reference"))

from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import saw_livetrack.tfnsw as tf  # noqa: E402
from saw_livetrack.gtfs import realtime as rt  # noqa: E402
from saw_livetrack.gtfs import schedule  # noqa: E402
import shared_tfnsw as stf  # noqa: E402

CHECKS = []
KEY_A, KEY_B = "key-AAAA-1234", "key-BBBB-5678"
SYD = ZoneInfo("Australia/Sydney")
NOW = datetime(2026, 11, 13, 14, 20, tzinfo=SYD).timestamp()
MOD = "Thu, 12 Nov 2026 14:01:15 GMT"
TMPS = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def tmpdir():
    d = tempfile.mkdtemp()
    TMPS.append(d)
    return d


# --- the fake network -----------------------------------------------------------

class Resp:
    def __init__(self, body, headers=None):
        self.body, self.headers, self.pos = body, headers or {}, 0

    def read(self, n=None):
        n = len(self.body) if n is None else n
        out = self.body[self.pos:self.pos + n]
        self.pos += len(out)
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(url, code):
    return urllib.error.HTTPError(url, code, "x", {}, None)


class Net:
    """answers: {url: bytes | int status | Exception | [those, taken in turn, the last repeating]}."""

    def __init__(self, answers=None, modified=None, statics=None):
        self.answers = dict(answers or {})
        self.modified = {tf.STATIC_URL: 500, tf.SYDNEY_STATIC_URL: MOD}
        self.modified.update(modified or {})
        self.statics = {tf.SYDNEY_STATIC_URL: syd_zip()}
        self.statics.update(statics or {})
        self.calls = []

    @staticmethod
    def _take(d, k):
        v = d.get(k)
        if isinstance(v, list):
            return v.pop(0) if len(v) > 1 else v[0]
        return v

    def __call__(self, req, timeout=None):
        url, method = req.full_url, req.get_method()
        self.calls.append((method, url, req.get_header("Authorization")))
        if url in (tf.STATIC_URL, tf.SYDNEY_STATIC_URL):
            if method == "HEAD":
                v = self._take(self.modified, url)
                if isinstance(v, Exception):
                    raise v
                if isinstance(v, int):
                    raise http_error(url, v)
                return Resp(b"", {"Last-Modified": v} if v else {})
            ans = self._take(self.statics, url)
        else:
            ans = self._take(self.answers, url)
        if ans is None:
            raise http_error(url, 404)
        if isinstance(ans, Exception):
            raise ans
        if isinstance(ans, int):
            raise http_error(url, ans)
        return Resp(ans)

    def count(self, url, method="GET"):
        return sum(1 for c in self.calls if c[1] == url and c[0] == method)


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


# --- bodies ---------------------------------------------------------------------

ROUTES = (("BMT_1", "NSWTrains"), ("CCN_1a", "NSWTrains"), ("SCO_2", "NSWTrains"), ("CTY_NC1", "NSWTrains"),
          ("IWL_1a", "SydneyTrains"), ("WST_1a", "SydneyTrains"), ("RTTA_REV", "SydneyTrains"),
          ("ZZZ_1", "SydneyTrains"), ("YYY_1", "NSWTrains"))
# Friday 13 Nov 2026 is "today"; Thursday 12th yesterday.
CALENDAR = (("1421.149.100", "0000100", "20261113", "20261113"),     # Friday only
            ("1421.149.200", "0001000", "20261112", "20261112"),     # Thursday only (yesterday)
            ("9.9.9", "0001100", "20261112", "20261113"),            # Thursday and Friday: two dates
            ("8.8.8", "0000010", "20261114", "20261114"))            # Saturday (tomorrow)


def syd_zip(routes=ROUTES, calendar=CALENDAR, drop=()):
    import io
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("routes.txt", "﻿route_id,agency_id,route_type\n" + "".join("%s,%s,2\n" % r for r in routes))
        days = "monday,tuesday,wednesday,thursday,friday,saturday,sunday"
        z.writestr("calendar.txt", "service_id,%s,start_date,end_date\n" % days + "".join(
            "%s,%s,%s,%s\n" % (sid, ",".join(mask), a, b) for sid, mask, a, b in calendar))
        for name in ("trips.txt", "stop_times.txt", "stops.txt", "agency.txt"):
            z.writestr(name, "x\n")
        for name in drop:
            pass
    if drop:
        src = zipfile.ZipFile(io.BytesIO(out.getvalue()))
        out2 = io.BytesIO()
        with zipfile.ZipFile(out2, "w") as z:
            for n in src.namelist():
                if n not in drop:
                    z.writestr(n, src.read(n))
        return out2.getvalue()
    return out.getvalue()


def feed():
    f = pb.FeedMessage()
    f.header.gtfs_realtime_version = "2.0"
    return f


def syd_vehicle(f, trip, route, ts=NOW - 30, label="14:00 Central Station to Lithgow Station ", lat=-33.88, lon=151.20,
                vid="7890.6725"):
    e = f.entity.add()
    e.id = "s" + trip
    v = e.vehicle
    v.trip.trip_id = trip
    if route:
        v.trip.route_id = route
    v.position.latitude = lat
    v.position.longitude = lon
    v.position.bearing = 0.0
    v.vehicle.id = vid
    if label is not None:
        v.vehicle.label = label
    v.stop_id = "Hornsby.Cowan 2 Loc"
    if ts is not None:
        v.timestamp = int(ts)
    return e


def nsw_vehicle(f, trip, route, start="20261113", ts=NOW - 30, lat=-33.70, lon=151.10, bearing=135.0, vid="TE2810"):
    e = f.entity.add()
    e.id = "n" + trip + start
    v = e.vehicle
    v.trip.trip_id = trip
    v.trip.start_date = start
    v.trip.route_id = route
    v.position.latitude = lat
    v.position.longitude = lon
    v.position.bearing = bearing
    v.position.speed = 0.0
    v.vehicle.id = vid
    v.vehicle.label = "nsw " + trip
    v.stop_id = "999"
    v.current_stop_sequence = 7
    v.current_status = 2
    v.timestamp = int(ts)
    return e


def upd(f, trip, route="", start=None, stops=("S1",), delay=60):
    e = f.entity.add()
    e.id = "u" + trip + (start or "")
    t = e.trip_update
    t.trip.trip_id = trip
    if start:
        t.trip.start_date = start
    if route:
        t.trip.route_id = route
    for sid in stops:
        s = t.stop_time_update.add()
        s.stop_sequence = 0
        s.stop_id = sid
        s.arrival.delay = delay
    return e


def ser(f):
    return f.SerializeToString()


def answers(nvp=None, ntu=None, svp=None, stu=None):
    return {tf.POSITIONS_URL: ser(nvp if nvp is not None else feed()), tf.TRIP_UPDATES_URL: ser(ntu if ntu is not None else feed()),
            tf.SYDNEY_POSITIONS_URL: ser(svp if svp is not None else feed()),
            tf.SYDNEY_TRIP_UPDATES_URL: ser(stu if stu is not None else feed())}


def make(consumers, sydney=(), ans=None, storage=True, clock=None, net=None, **kw):
    net = net or Net(ans or answers())
    c = tf.TfnswClient(consumers, opener=net, clock=clock or Clock(), background=False,
                       storage_dir=tmpdir() if storage else None, sydney_trains=sydney, **kw)
    return c, net


def trips(f):
    return sorted(e.vehicle.trip.trip_id if e.HasField("vehicle") else e.trip_update.trip.trip_id for e in f.entity)


def by_trip(f):
    return {(e.vehicle.trip.trip_id if e.HasField("vehicle") else e.trip_update.trip.trip_id): e for e in f.entity}


ALL = {tf.COMMUTER: KEY_A, tf.INTERCITY: KEY_A, tf.TRANSIT: KEY_A}
BOTH = (tf.COMMUTER, tf.INTERCITY)

# ================================================================== constants and pure functions
# The contract stays 1 (Lee 2026-11-12, against the design's 2): Intercity Rail's tests name the key saw_tfnsw_v1.
check("contract 1, key v1", (tf.CONTRACT_VERSION, tf.SHARED_KEY) == (1, "saw_tfnsw_v1"))
check("transit consumer", tf.TRANSIT == "transit" and tf.TRANSIT in tf.CONSUMERS)
check("who may take Sydney Trains", tf.SYDNEY_CONSUMERS == {tf.COMMUTER, tf.INTERCITY, tf.TRANSIT})
check("sydney urls", (tf.SYDNEY_POSITIONS_URL, tf.SYDNEY_TRIP_UPDATES_URL, tf.SYDNEY_STATIC_URL) == (
    "https://api.transport.nsw.gov.au/v2/gtfs/vehiclepos/sydneytrains",
    "https://api.transport.nsw.gov.au/v2/gtfs/realtime/sydneytrains",
    "https://api.transport.nsw.gov.au/v1/gtfs/schedule/sydneytrains"))
check("rule constants", (tf.TIE_S, tf.LAST_FIX_KEEP_S, tf.BEFORE_DEPARTURE_MAX_AGE_S, tf.PREVIOUS_CALENDAR_UNTIL_HOUR)
      == (30.0, 7200.0, 300.0, 8))
check("timetable names", tf.TIMETABLES == ("nswtrains", "sydneytrains") and tf.SYDNEY_STATIC_NAME == "saw_tfnsw_sydneytrains")
check("required members", tf.SYDNEY_REQUIRED == {"routes.txt", "trips.txt", "stop_times.txt", "stops.txt", "calendar.txt"})

for pre in ("CCN", "BMT", "SCO", "SHL", "HUN"):
    check("%s is commuter" % pre, tf.sydney_consumer_of(pre + "_1a") == tf.COMMUTER)
for pre in ("APS", "CMB", "ESI", "IWL", "NSN", "WST", "NTH", "OLY", "T3", "T6"):
    check("%s is transit" % pre, tf.sydney_consumer_of(pre + "_1") == tf.TRANSIT)
check("CTY is intercity (D7)", tf.sydney_consumer_of("CTY_NC1") == tf.INTERCITY)
check("RTTA nobody", tf.sydney_consumer_of("RTTA_REV") is None)
check("RTTA nobody even if the agency says otherwise", tf.sydney_consumer_of("RTTA_REV", {"RTTA_REV": "SydneyTrains"}) is None)
check("no route nobody", tf.sydney_consumer_of("") is None and tf.sydney_consumer_of(None) is None and tf.sydney_consumer_of(7) is None)
check("unknown prefix, no timetable: nobody", tf.sydney_consumer_of("ZZZ_1") is None)
check("unknown prefix, SydneyTrains: transit", tf.sydney_consumer_of("ZZZ_1", {"ZZZ_1": "SydneyTrains"}) == tf.TRANSIT)
check("unknown prefix, NSWTrains: commuter", tf.sydney_consumer_of("YYY_1", {"YYY_1": "NSWTrains"}) == tf.COMMUTER)
check("unknown prefix, other agency: nobody", tf.sydney_consumer_of("YYY_1", {"YYY_1": "Other"}) is None)
check("a listed prefix ignores the agency", tf.sydney_consumer_of("BMT_1", {"BMT_1": "SydneyTrains"}) == tf.COMMUTER)
check("WST suburban, CTY_WST intercity", tf.sydney_consumer_of("WST_1a") == tf.TRANSIT and tf.sydney_consumer_of("CTY_W1") == tf.INTERCITY)
check("padded route id", tf.sydney_consumer_of(" IWL_1a ") == tf.TRANSIT)
check("prefix needs the underscore split", tf.sydney_consumer_of("CTYX_1") is None)

check("run_of either feed", tf.run_of("W665.1421.149.100.D.10.1") == "W665" and tf.run_of(" KN56.131126.16.N.2.1316") == "KN56")
check("run_of nothing", tf.run_of("") == "" and tf.run_of(None) == "")
check("service_of", tf.service_of("W665.1421.149.100.D.10.92") == "1421.149.100")
check("service_of too short", tf.service_of("NonTimetabled.123") is None and tf.service_of("A.1.2") is None)
check("service_of empty field", tf.service_of("A..1.2.3") is None and tf.service_of(None) is None)
check("timetable_of nswtrains field 2", tf.timetable_of("KN56.131126.16.N.2.1316") == tf.NSWTRAINS)
check("timetable_of nswtrains field 3 (North Coast, measured)", tf.timetable_of("NT32.1421.091126.116.X.7.92260223") == tf.NSWTRAINS)
check("timetable_of nswtrains short id", tf.timetable_of("135.081126.64.1750") == tf.NSWTRAINS)
check("timetable_of sydney", tf.timetable_of("W665.1421.149.100.D.10.92") == tf.SYDNEYTRAINS)
check("timetable_of sydney 3-digit field 2", tf.timetable_of("1--A.860.117.124.H.8.1") == tf.SYDNEYTRAINS)
check("timetable_of a 6-digit non-date is no date", tf.timetable_of("AB.320126.991399.2.X") == tf.SYDNEYTRAINS)
check("timetable_of field 2 a date beats fields 2-4", tf.timetable_of("AB.311226.1.2") == tf.NSWTRAINS)
check("timetable_of nothing", tf.timetable_of("NonTimetabled.1") is None and tf.timetable_of("") is None and tf.timetable_of(None) is None)

cal = tf.read_calendar([{"service_id": "s1", "monday": "0", "tuesday": "0", "wednesday": "0", "thursday": "1", "friday": "1",
                         "saturday": "0", "sunday": "0", "start_date": "20261112", "end_date": "20261113"},
                        {"service_id": " ", "monday": "1"}])
check("read_calendar", cal == {"s1": ("20261112", "20261113", (False, False, False, True, True, False, False))})
check("runs_on in range and weekday", tf.runs_on(cal, "s1", date(2026, 11, 13)) and tf.runs_on(cal, "s1", date(2026, 11, 12)))
check("runs_on wrong weekday", not tf.runs_on(cal, "s1", date(2026, 11, 11)))
check("runs_on out of range", not tf.runs_on(cal, "s1", date(2026, 11, 19)) and not tf.runs_on(cal, "s1", date(2026, 11, 5)))
wk = tf.read_calendar([{"service_id": "w", "monday": "1", "tuesday": "1", "wednesday": "1", "thursday": "1", "friday": "0",
                        "saturday": "1", "sunday": "1", "start_date": "20261109", "end_date": "20261115"}])
check("runs_on: a weekday off inside the range", not tf.runs_on(wk, "w", date(2026, 11, 13)) and tf.runs_on(wk, "w", date(2026, 11, 12)))
check("runs_on unknown service, no calendar", not tf.runs_on(cal, "x", date(2026, 11, 13)) and not tf.runs_on(None, "s1", date(2026, 11, 13)))
check("label_minutes", tf.label_minutes("14:25 Central Station to Lithgow Station ") == 865)
check("label_minutes one-digit hour and padding", tf.label_minutes(" 7:05 x") == 425)
check("label_minutes midnight", tf.label_minutes("00:15 Central Station to Hornsby Station ") == 15)
check("label_minutes none", all(tf.label_minutes(x) is None for x in ("", None, "Central", "1425 x", "14:60 x", "14:255 x", 5)))
check("label_minutes past midnight kept", tf.label_minutes("25:10 x") == 1510 and tf.label_minutes("48:00 x") is None)
check("local_day", tf.local_day(NOW) == date(2026, 11, 13))
check("service day epoch is local midnight", tf.service_day_epoch(date(2026, 11, 13)) == datetime(2026, 11, 13, tzinfo=SYD).timestamp())
check("departure today", tf.departure_epoch(date(2026, 11, 13), 14 * 60 + 30, NOW) == datetime(2026, 11, 13, 14, 30, tzinfo=SYD).timestamp())
just_after_midnight = datetime(2026, 11, 14, 0, 5, tzinfo=SYD).timestamp()
check("00:15 on yesterday's service is tonight", tf.departure_epoch(date(2026, 11, 13), 15, just_after_midnight)
      == datetime(2026, 11, 14, 0, 15, tzinfo=SYD).timestamp())
check("23:50 departed 40 min ago stays yesterday", tf.departure_epoch(date(2026, 11, 13), 23 * 60 + 50,
      datetime(2026, 11, 14, 0, 30, tzinfo=SYD).timestamp()) == datetime(2026, 11, 13, 23, 50, tzinfo=SYD).timestamp())
scal = tf.read_calendar([dict(zip(("service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                                   "start_date", "end_date"), (sid,) + tuple(mask) + (a, b))) for sid, mask, a, b in CALENDAR])
check("service_dates today", tf.service_dates(scal, "1421.149.100", NOW) == [date(2026, 11, 13)])
check("service_dates yesterday", tf.service_dates(scal, "1421.149.200", NOW) == [date(2026, 11, 12)])
check("service_dates tomorrow", tf.service_dates(scal, "8.8.8", NOW) == [date(2026, 11, 14)])
check("service_dates two", tf.service_dates(scal, "9.9.9", NOW) == [date(2026, 11, 12), date(2026, 11, 13)])
check("service_dates none", tf.service_dates(scal, "7.7.7", NOW) == [])
prev = {"old": ("20261112", "20261112", (False, False, False, True, False, False, False))}
until = datetime(2026, 11, 14, 8, 0, tzinfo=SYD).timestamp()
check("previous answers for yesterday before 08:00", tf.service_dates(scal, "old", NOW, prev, until) == [date(2026, 11, 12)])
check("previous not after", tf.service_dates(scal, "old", until + 1, prev, until) == [])
prev_today = {"p": ("20261113", "20261113", (False, False, False, False, True, False, False))}
check("previous never answers for today", tf.service_dates(scal, "p", NOW, prev_today, until) == [])
check("next_morning before 08:00 is today's", tf.next_morning(datetime(2026, 11, 13, 1, 1, tzinfo=SYD).timestamp())
      == datetime(2026, 11, 13, 8, 0, tzinfo=SYD).timestamp())
check("next_morning after 08:00 is tomorrow's", tf.next_morning(NOW) == datetime(2026, 11, 14, 8, 0, tzinfo=SYD).timestamp())
check("next_morning at 08:00 exactly is tomorrow's", tf.next_morning(datetime(2026, 11, 13, 8, 0, tzinfo=SYD).timestamp())
      == datetime(2026, 11, 14, 8, 0, tzinfo=SYD).timestamp())

# ================================================================== who asks for Sydney Trains, and the calls it costs
c, net = make({tf.INTERCITY: KEY_A})
c.for_consumer(tf.INTERCITY)
check("no consumer asked: nswtrains only", net.count(tf.POSITIONS_URL) == 1 and net.count(tf.TRIP_UPDATES_URL) == 1
      and net.count(tf.SYDNEY_POSITIONS_URL) == 0 and net.count(tf.SYDNEY_TRIP_UPDATES_URL) == 0)
check("no consumer asked: Sydney's timetable never asked", net.count(tf.SYDNEY_STATIC_URL, "HEAD") == 0)
c, net = make({tf.TRANSIT: KEY_A})
c.for_consumer(tf.TRANSIT)
check("transit only: Sydney Trains only", net.count(tf.SYDNEY_POSITIONS_URL) == 1 and net.count(tf.SYDNEY_TRIP_UPDATES_URL) == 1
      and net.count(tf.POSITIONS_URL) == 0 and net.count(tf.TRIP_UPDATES_URL) == 0)
check("transit only: nswtrains' timetable never asked", net.count(tf.STATIC_URL, "HEAD") == 0 and net.count(tf.SYDNEY_STATIC_URL, "HEAD") == 1)
check("transit always takes Sydney Trains", c.sydney_consumers == {tf.TRANSIT})
c, net = make({tf.COMMUTER: KEY_A}, sydney=(tf.COMMUTER,))
c.for_consumer(tf.COMMUTER)
check("commuter asked: four calls", [net.count(u) for u in (tf.POSITIONS_URL, tf.TRIP_UPDATES_URL, tf.SYDNEY_POSITIONS_URL,
                                                            tf.SYDNEY_TRIP_UPDATES_URL)] == [1, 1, 1, 1])
c.for_consumer(tf.COMMUTER)
check("inside the TTL: no new calls", net.count(tf.SYDNEY_POSITIONS_URL) == 1 and net.count(tf.POSITIONS_URL) == 1)
check("bus cannot ask", raises(ValueError, lambda: tf.TfnswClient({tf.BUS: KEY_A}, sydney_trains=(tf.BUS,))))
check("bus cannot ask later", raises(ValueError, lambda: make({tf.BUS: KEY_A})[0].register(tf.BUS, KEY_A, sydney_trains=True)))
check("asking for a consumer not given", raises(ValueError, lambda: tf.TfnswClient({tf.BUS: KEY_A}, sydney_trains=(tf.COMMUTER,))))
check("asking for an unknown consumer", raises(ValueError, lambda: tf.TfnswClient({tf.BUS: KEY_A}, sydney_trains=("tram",))))
check("bus cannot be turned on", raises(ValueError, lambda: make({tf.BUS: KEY_A})[0].set_sydney_trains(tf.BUS, True)))

# an old consumer's feed does not change when Sydney Trains is polled for another (the reason for asking)
nvp, ntu = feed(), feed()
nsw_vehicle(nvp, "BM12.131126.16.N.2.1", "4T.T.BMT")
nsw_vehicle(nvp, "NT32.1421.091126.116.X.7.9", "4T.T.NT32", vid="XP2001")
upd(ntu, "BM12.131126.16.N.2.1", "4T.T.BMT", "20261113")
upd(ntu, "NT32.1421.091126.116.X.7.9", "4T.T.NT32", "20261113")
svp, stu = feed(), feed()
syd_vehicle(svp, "BM12.1421.149.100.D.10.1", "BMT_1")
syd_vehicle(svp, "BM99.1421.149.100.D.10.1", "BMT_1")
syd_vehicle(svp, "NT31.1421.149.100.X.7.1", "CTY_NC1")
syd_vehicle(svp, "W665.1421.149.100.D.10.1", "IWL_1a")
upd(stu, "BM99.1421.149.100.D.10.1", "BMT_1")
upd(stu, "W665.1421.149.100.D.10.1", "IWL_1a")
old_c, _ = make({tf.COMMUTER: KEY_A, tf.INTERCITY: KEY_A}, ans=answers(nvp, ntu))
new_c, _ = make({tf.COMMUTER: KEY_A, tf.INTERCITY: KEY_A, tf.TRANSIT: KEY_A}, ans=answers(nvp, ntu, svp, stu))
for cons in (tf.COMMUTER, tf.INTERCITY):
    a, b = old_c.for_consumer(cons), new_c.for_consumer(cons)
    check(cons + " not asking: byte-identical to nswtrains alone", ser(a[0]) == ser(b[0]) and ser(a[1]) == ser(b[1]))
check("not asking: no Sydney Trains run in it", "BM99.1421.149.100.D.10.1" not in trips(new_c.for_consumer(tf.COMMUTER)[0]))

c, net = make({tf.COMMUTER: KEY_A, tf.TRANSIT: KEY_A}, ans=answers(nvp, ntu, svp, stu))
check("before asking", "BM99.1421.149.100.D.10.1" not in trips(c.for_consumer(tf.COMMUTER)[0]))
check("register returns False for a known consumer that now asks", c.register(tf.COMMUTER, KEY_A, sydney_trains=True) is False)
check("asking later takes effect at once (no TTL wait)", "BM99.1421.149.100.D.10.1" in trips(c.for_consumer(tf.COMMUTER)[0]))
check("sydney_consumers", c.sydney_consumers == {tf.COMMUTER, tf.TRANSIT})
check("register without asking does not turn it off", c.register(tf.COMMUTER, KEY_B) is False and tf.COMMUTER in c.sydney_consumers)
c.set_sydney_trains(tf.COMMUTER, False)
check("turned off: back to nswtrains alone at once", "BM99.1421.149.100.D.10.1" not in trips(c.for_consumer(tf.COMMUTER)[0]))
c.set_sydney_trains(tf.TRANSIT, False)
check("transit cannot be turned off", tf.TRANSIT in c.sydney_consumers)
c.register(tf.INTERCITY, KEY_A, sydney_trains=True)
c.unregister(tf.INTERCITY)
check("unregister forgets the asking", tf.INTERCITY not in c.sydney_consumers and c.register(tf.INTERCITY, KEY_A) is True
      and tf.INTERCITY not in c.sydney_consumers)

# ================================================================== routing, dating, cleaning
svp = feed()
syd_vehicle(svp, "A1.1421.149.100.D.1.1", "BMT_1")
syd_vehicle(svp, "A2.1421.149.100.D.1.1", "CCN_1a")
syd_vehicle(svp, "A3.1421.149.100.D.1.1", "CTY_NC1")
syd_vehicle(svp, "A4.1421.149.100.D.1.1", "IWL_1a")
syd_vehicle(svp, "A5.1421.149.100.D.1.1", "RTTA_REV")
syd_vehicle(svp, "A6.1421.149.100.D.1.1", "")
syd_vehicle(svp, "NonTimetabled.4711", "")
syd_vehicle(svp, "A8.1421.149.100.D.1.1", "ZZZ_1")
syd_vehicle(svp, "A9.1421.149.100.D.1.1", "YYY_1")
syd_vehicle(svp, "B1.1421.149.200.D.1.1", "IWL_1a")                             # yesterday's service
syd_vehicle(svp, "B2.9.9.9.D.1.1", "IWL_1a", label="14:00 Central to Penrith")   # two dates, label decides
syd_vehicle(svp, "B3.9.9.9.D.1.1", "IWL_1a", label=None)                        # two dates, no label
syd_vehicle(svp, "B4.7.7.7.D.1.1", "IWL_1a")                                    # service in no calendar
e = svp.entity.add()
e.id = "nopos"
e.vehicle.trip.trip_id = "B5.1421.149.100.D.1.1"
e.vehicle.trip.route_id = "IWL_1a"
svp.entity.add().id = "empty"
stu = feed()
upd(stu, "A4.1421.149.100.D.1.1", "IWL_1a")
upd(stu, "B1.1421.149.200.D.1.1", "")                 # owner and day from its position
upd(stu, "C1.1421.149.200.D.1.1", "IWL_1a")           # no position: dated by the calendar
upd(stu, "C2.9.9.9.D.1.1", "IWL_1a")                  # no position, two dates: undated
upd(stu, "C3.1421.149.100.D.1.1", "RTTA_REV")
upd(stu, "", "IWL_1a")
c, net = make(ALL, sydney=BOTH, ans=answers(svp=svp, stu=stu))
cp, cu = c.for_consumer(tf.COMMUTER)
ip, iu = c.for_consumer(tf.INTERCITY)
tp, tu_ = c.for_consumer(tf.TRANSIT)
check("commuter: its lines, an NSWTrains route by agency", trips(cp) == ["A1.1421.149.100.D.1.1", "A2.1421.149.100.D.1.1",
                                                                         "A9.1421.149.100.D.1.1"])
check("intercity: CTY (D7)", trips(ip) == ["A3.1421.149.100.D.1.1"])
check("transit: suburban, a SydneyTrains route by agency, the dated and undated",
      trips(tp) == ["A4.1421.149.100.D.1.1", "A8.1421.149.100.D.1.1", "B1.1421.149.200.D.1.1", "B2.9.9.9.D.1.1",
                    "B3.9.9.9.D.1.1", "B4.7.7.7.D.1.1"])
everyone = trips(cp) + trips(ip) + trips(tp)
check("RTTA, no route, NonTimetabled, no position: nobody", not any(t.startswith(("A5", "A6", "NonTimetabled", "B5")) for t in everyone))
check("each vehicle to one consumer", len(everyone) == len(set(everyone)))
t = by_trip(tp)
check("start_date set from the calendar", t["A4.1421.149.100.D.1.1"].vehicle.trip.start_date == "20261113")
check("yesterday's service dated yesterday", t["B1.1421.149.200.D.1.1"].vehicle.trip.start_date == "20261112")
check("two dates: the label's departure nearer now", t["B2.9.9.9.D.1.1"].vehicle.trip.start_date == "20261113")
check("two dates, no label: left unset", not t["B3.9.9.9.D.1.1"].vehicle.trip.HasField("start_date"))
check("service in no calendar: left unset", not t["B4.7.7.7.D.1.1"].vehicle.trip.HasField("start_date"))
check("undated counted", c.stats()["undated"] == 2)
v = t["A4.1421.149.100.D.1.1"].vehicle
check("Sydney Trains cleaned: stop_id, sequence, status", not v.HasField("stop_id") and not v.HasField("current_stop_sequence")
      and not v.HasField("current_status"))
check("Sydney Trains cleaned: bearing and speed", not v.position.HasField("bearing") and not v.position.HasField("speed"))
check("position, vehicle and label kept", round(v.position.latitude, 2) == -33.88 and v.vehicle.id == "7890.6725"
      and v.vehicle.label.startswith("14:00"))
check("the source feed is untouched", svp.entity[0].vehicle.stop_id == "Hornsby.Cowan 2 Loc")
u = by_trip(tu_)
check("transit updates: its own, by route and by position", sorted(u) == ["A4.1421.149.100.D.1.1", "B1.1421.149.200.D.1.1",
                                                                         "C1.1421.149.200.D.1.1", "C2.9.9.9.D.1.1"])
check("update dated from its position", u["B1.1421.149.200.D.1.1"].trip_update.trip.start_date == "20261112")
check("update with no position dated by the calendar", u["C1.1421.149.200.D.1.1"].trip_update.trip.start_date == "20261112")
check("update with no position, two dates: unset", not u["C2.9.9.9.D.1.1"].trip_update.trip.HasField("start_date"))
check("updates keep their stops", len(u["A4.1421.149.100.D.1.1"].trip_update.stop_time_update) == 1)
check("headers carried", tp.header.gtfs_realtime_version == "2.0" and tu_.header.gtfs_realtime_version == "2.0")

c, net = make(ALL, sydney=BOTH, ans=answers(svp=svp, stu=stu), storage=False)
cp = c.for_consumer(tf.COMMUTER)[0]
tp = c.for_consumer(tf.TRANSIT)[0]
check("no timetable: an unlisted prefix goes to nobody", "A9.1421.149.100.D.1.1" not in trips(cp) and "A8.1421.149.100.D.1.1" not in trips(tp))
check("no timetable: nothing dated", not any(e.vehicle.trip.HasField("start_date") for e in tp.entity))

# ================================================================== S1, before departure
S1 = "14:30 Central Station to Lithgow Station "     # ahead of NOW (14:20)
svp = feed()
syd_vehicle(svp, "P1.1421.149.100.D.1.1", "IWL_1a", ts=NOW - 299, label=S1)
syd_vehicle(svp, "P2.1421.149.100.D.1.1", "IWL_1a", ts=NOW - 301, label=S1)
syd_vehicle(svp, "P3.1421.149.100.D.1.1", "IWL_1a", ts=NOW - 300, label=S1)
syd_vehicle(svp, "P4.1421.149.100.D.1.1", "IWL_1a", ts=None, label=S1)
syd_vehicle(svp, "P5.1421.149.100.D.1.1", "IWL_1a", ts=NOW - 3000)                       # departed 14:00
syd_vehicle(svp, "P6.1421.149.100.D.1.1", "IWL_1a", ts=NOW - 3000, label=None)           # no label
syd_vehicle(svp, "P7.7.7.7.D.1.1", "IWL_1a", ts=NOW - 3000, label=S1)                    # undated
syd_vehicle(svp, "P8.1421.149.100.D.1.1", "BMT_1", ts=NOW - 3000, label=S1)              # commuter too
syd_vehicle(svp, "P9.1421.149.100.D.1.1", "IWL_1a", ts=NOW - 3000, label="14:20 Central to Penrith")   # departs now
stu = feed()
upd(stu, "P2.1421.149.100.D.1.1", "IWL_1a")
c, net = make(ALL, sydney=BOTH, ans=answers(svp=svp, stu=stu))
tp, tu_ = c.for_consumer(tf.TRANSIT)
got = trips(tp)
check("S1: a 299 s fix before departure kept", "P1.1421.149.100.D.1.1" in got)
check("S1: 300 s exactly kept", "P3.1421.149.100.D.1.1" in got)
check("S1: 301 s dropped", "P2.1421.149.100.D.1.1" not in got)
check("S1: no fix time before departure dropped", "P4.1421.149.100.D.1.1" not in got)
check("S1: departed, an old fix is the integration's (S2)", "P5.1421.149.100.D.1.1" in got)
check("S1: no label, not applied", "P6.1421.149.100.D.1.1" in got)
check("S1: undated, not applied", "P7.7.7.7.D.1.1" in got)
check("S1: at the departure minute it has departed", "P9.1421.149.100.D.1.1" in got)
check("S1: for commuter too", "P8.1421.149.100.D.1.1" not in trips(c.for_consumer(tf.COMMUTER)[0]))
check("S1 counted", c.stats()["before_departure_dropped"] == 3)
check("S1: the dropped run's update kept (Live Track Stations)", "P2.1421.149.100.D.1.1" in trips(tu_))

# ================================================================== one entity per run-day
def merged(nvp, ntu, svp, stu, clock=None, client=None):
    if client is None:
        client, _ = make(ALL, sydney=BOTH, ans=answers(nvp, ntu, svp, stu), clock=clock)
    else:
        client._opener.answers.update(answers(nvp, ntu, svp, stu))
    return client, client.for_consumer(tf.COMMUTER, force=True)


NSW_T, SYD_T = "BM12.131126.16.N.2.1", "BM12.1421.149.100.D.10.1"
for label, tn, ts_, want in (("Sydney 40 s newer: Sydney's fix", NOW - 60, NOW - 20, "syd"),
                             ("Sydney 30 s newer: a tie, nswtrains'", NOW - 60, NOW - 30, "nsw"),
                             ("Sydney 31 s newer: Sydney's", NOW - 61, NOW - 30, "syd"),
                             ("nswtrains newer: nswtrains'", NOW - 20, NOW - 200, "nsw"),
                             ("same time: nswtrains'", NOW - 20, NOW - 20, "nsw")):
    nvp, svp = feed(), feed()
    nsw_vehicle(nvp, NSW_T, "4T.T.BMT", ts=tn, lat=-33.70)
    syd_vehicle(svp, SYD_T, "BMT_1", ts=ts_, lat=-33.50)
    _, (p, _u) = merged(nvp, feed(), svp, feed())
    check(label + ": one entity", len(p.entity) == 1)
    e = p.entity[0].vehicle
    check(label, round(e.position.latitude, 2) == (-33.50 if want == "syd" else -33.70)
          and e.timestamp == int(ts_ if want == "syd" else tn))
    check(label + ": nswtrains' trip and vehicle", e.trip.trip_id == NSW_T and e.trip.start_date == "20261113"
          and e.vehicle.id == "TE2810")
    check(label + ": bearing only from nswtrains", e.position.HasField("bearing") == (want == "nsw"))
    check(label + ": cleaned", not e.HasField("stop_id") and not e.position.HasField("speed"))

nvp, svp = feed(), feed()
nsw_vehicle(nvp, "BM12.121126.16.N.2.1", "4T.T.BMT", start="20261112")
syd_vehicle(svp, SYD_T, "BMT_1")
_, (p, _u) = merged(nvp, feed(), svp, feed())
check("different days of one run: two entities", trips(p) == ["BM12.121126.16.N.2.1", SYD_T])

svp = feed()
syd_vehicle(svp, "BM77.1421.149.100.D.10.1", "BMT_1", vid="7000.1")
_, (p, _u) = merged(feed(), feed(), svp, feed())
e = p.entity[0].vehicle
check("Sydney Trains only: its trip, dated, its vehicle", e.trip.trip_id == "BM77.1421.149.100.D.10.1"
      and e.trip.start_date == "20261113" and e.vehicle.id == "7000.1")

ntu, svp = feed(), feed()
upd(ntu, NSW_T, "4T.T.BMT", "20261113")
syd_vehicle(svp, SYD_T, "BMT_1", vid="7000.2")
_, (p, u) = merged(feed(), ntu, svp, feed())
e = p.entity[0].vehicle
check("nswtrains has the run in its updates only: its trip, Sydney's vehicle and fix",
      e.trip.trip_id == NSW_T and e.trip.route_id == "4T.T.BMT" and e.vehicle.id == "7000.2" and round(e.position.latitude, 2) == -33.88)

nvp, svp = feed(), feed()
nsw_vehicle(nvp, NSW_T, "4T.T.BMT")
syd_vehicle(svp, "BM12.7.7.7.D.10.1", "BMT_1", ts=NOW + 10)           # undated: merged into nswtrains' only day
_, (p, _u) = merged(nvp, feed(), svp, feed())
check("undated Sydney run joins nswtrains' one day", len(p.entity) == 1 and p.entity[0].vehicle.trip.trip_id == NSW_T
      and p.entity[0].vehicle.timestamp == int(NOW + 10))
nvp, svp = feed(), feed()
nsw_vehicle(nvp, NSW_T, "4T.T.BMT")
nsw_vehicle(nvp, "BM12.141126.16.N.2.1", "4T.T.BMT", start="20261114")
syd_vehicle(svp, "BM12.7.7.7.D.10.1", "BMT_1", ts=NOW + 10)
_, (p, _u) = merged(nvp, feed(), svp, feed())
check("undated Sydney run, nswtrains on two days: its own entity", len(p.entity) == 3)
svp = feed()
syd_vehicle(svp, "B2.9.9.9.D.1.1", "BMT_1", label="00:15 Central Station to Lithgow Station ")
_, (p, _u) = merged(feed(), feed(), svp, feed(), clock=Clock(datetime(2026, 11, 13, 0, 5, tzinfo=SYD).timestamp()))
check("two dates: the label's clock time on each date (the documented limit after midnight)",
      p.entity[0].vehicle.trip.start_date == "20261113")

svp = feed()
syd_vehicle(svp, "BM55.1421.149.100.D.10.1", "BMT_1", ts=NOW - 100, lat=-33.1)
syd_vehicle(svp, "BM55.1421.149.100.D.11.2", "BMT_1", ts=NOW - 10, lat=-33.2)
_, (p, _u) = merged(feed(), feed(), svp, feed())
check("a run twice in Sydney Trains (none measured): the fresher", len(p.entity) == 1 and round(p.entity[0].vehicle.position.latitude, 2) == -33.2)

# never older than the last fix handed out
clk = Clock()
nvp, svp = feed(), feed()
nsw_vehicle(nvp, NSW_T, "4T.T.BMT", ts=NOW - 100, lat=-33.70)
syd_vehicle(svp, SYD_T, "BMT_1", ts=NOW - 20, lat=-33.50)
cl, (p, _u) = merged(nvp, feed(), svp, feed(), clock=clk)
check("read 1: Sydney's newer fix", p.entity[0].vehicle.timestamp == int(NOW - 20))
clk.t += 60
nvp = feed()
nsw_vehicle(nvp, NSW_T, "4T.T.BMT", ts=NOW - 90, lat=-33.71)
_, (p, _u) = merged(nvp, feed(), feed(), feed(), client=cl)
e = p.entity[0].vehicle
check("read 2: an older fix is not handed out; the last one is",
      e.timestamp == int(NOW - 20) and round(e.position.latitude, 2) == -33.50 and e.trip.trip_id == NSW_T)
check("kept_last_fix counted", cl.stats()["kept_last_fix"] == 1 and cl.stats()["runs_remembered"] == 1)
clk.t += 60
nvp = feed()
nsw_vehicle(nvp, NSW_T, "4T.T.BMT", ts=NOW + 100, lat=-33.72)
_, (p, _u) = merged(nvp, feed(), feed(), feed(), client=cl)
check("read 3: a newer fix wins again", p.entity[0].vehicle.timestamp == int(NOW + 100))
clk.t += tf.LAST_FIX_KEEP_S + 1
_, (p, _u) = merged(feed(), feed(), feed(), feed(), client=cl)
check("forgotten after LAST_FIX_KEEP_S unseen", cl.stats()["runs_remembered"] == 0 and len(p.entity) == 0)
nvp = feed()
nsw_vehicle(nvp, NSW_T, "4T.T.BMT", ts=NOW - 500)
_, (p, _u) = merged(nvp, feed(), feed(), feed(), client=cl)
check("after forgetting, an older fix is handed out", p.entity[0].vehicle.timestamp == int(NOW - 500))

# CTY merges into intercity (D7)
nvp, ntu, svp, stu = feed(), feed(), feed(), feed()
nsw_vehicle(nvp, "NT32.1421.091126.116.X.7.9", "4T.T.NT32", ts=NOW - 100, vid="XP2001", lat=-31.0)
syd_vehicle(svp, "NT32.1421.149.100.X.7.1", "CTY_NC1", ts=NOW - 10, lat=-31.5)
syd_vehicle(svp, "WY65.1421.149.100.X.7.1", "CTY_S1", ts=NOW - 10, lat=-34.5)
upd(ntu, "NT32.1421.091126.116.X.7.9", "4T.T.NT32", "20261113")
upd(stu, "NT32.1421.149.100.X.7.1", "CTY_NC1")
upd(stu, "WY65.1421.149.100.X.7.1", "CTY_S1")
c, _ = make(ALL, sydney=BOTH, ans=answers(nvp, ntu, svp, stu))
ip, iu = c.for_consumer(tf.INTERCITY)
d = by_trip(ip)
check("D7: one intercity entity per CTY run", sorted(d) == ["NT32.1421.091126.116.X.7.9", "WY65.1421.149.100.X.7.1"])
check("D7: the fresher Sydney fix under nswtrains' trip and vehicle",
      round(d["NT32.1421.091126.116.X.7.9"].vehicle.position.latitude, 1) == -31.5 and d["NT32.1421.091126.116.X.7.9"].vehicle.vehicle.id == "XP2001")
check("D7: a run only Sydney Trains carries is drawn (NT31, WY65 measured)", d["WY65.1421.149.100.X.7.1"].vehicle.trip.start_date == "20261113")
check("D7: updates nswtrains' for its run, Sydney's for the other", trips(iu) == ["NT32.1421.091126.116.X.7.9", "WY65.1421.149.100.X.7.1"])
check("D7: CTY never to commuter or transit", not any("NT32" in x or "WY65" in x for x in trips(c.for_consumer(tf.COMMUTER)[0]) + trips(c.for_consumer(tf.TRANSIT)[0])))
c, _ = make(ALL, sydney=(tf.COMMUTER,), ans=answers(nvp, ntu, svp, stu))
check("intercity not asking: no Sydney CTY", trips(c.for_consumer(tf.INTERCITY)[0]) == ["NT32.1421.091126.116.X.7.9"])

# ================================================================== merged trip updates
nvp, ntu, svp, stu = feed(), feed(), feed(), feed()
nsw_vehicle(nvp, NSW_T, "4T.T.BMT")
syd_vehicle(svp, SYD_T, "BMT_1")
upd(ntu, NSW_T, "4T.T.BMT", "20261113", stops=("N1",))
upd(stu, SYD_T, "BMT_1", stops=("S1",))                                   # nswtrains has this run: dropped
syd_vehicle(svp, "BM20.1421.149.100.D.10.1", "BMT_1")
upd(stu, "BM20.1421.149.100.D.10.1", "BMT_1")                             # Sydney only: kept, dated
upd(ntu, "BM30.131126.16.N.2.1", "4T.T.BMT", "20261113")                  # nswtrains updates only
upd(ntu, "BM30.131126.16.N.2.1", "4T.T.BMT", "20261114")                  # tomorrow's run of that trip
syd_vehicle(svp, "BM30.1421.149.100.D.10.1", "BMT_1")                     # positioned by Sydney only
upd(stu, "BM30.1421.149.100.D.10.1", "BMT_1")                             # nswtrains has it in updates: dropped
upd(stu, "BM40.1421.149.100.D.10.1", "BMT_1")                             # no position anywhere: kept
upd(stu, "BM50.7.7.7.D.10.1", "BMT_1")                                    # undated, run unknown to nswtrains: kept
upd(ntu, "BM60.131126.16.N.2.1", "4T.T.BMT", "20261113")
upd(stu, "BM60.7.7.7.D.10.1", "BMT_1")                                    # undated, nswtrains has the run: dropped
c, _ = make(ALL, sydney=BOTH, ans=answers(nvp, ntu, svp, stu))
p, u = c.for_consumer(tf.COMMUTER)
ut = [(e.trip_update.trip.trip_id, e.trip_update.trip.start_date) for e in u.entity]
check("merged updates", sorted(ut) == sorted([(NSW_T, "20261113"), ("BM30.131126.16.N.2.1", "20261113"),
                                               ("BM60.131126.16.N.2.1", "20261113"),
                                               ("BM20.1421.149.100.D.10.1", "20261113"), ("BM40.1421.149.100.D.10.1", "20261113"),
                                               ("BM50.7.7.7.D.10.1", "")]))
check("nswtrains' update for its run, not Sydney's", by_trip(u)[NSW_T].trip_update.stop_time_update[0].stop_id == "N1")
check("a run positioned by Sydney under nswtrains' trip keeps only that day's update",
      ("BM30.131126.16.N.2.1", "20261114") not in ut and c.stats()["dropped_other_runs"] >= 1)
check("positions: the BM30 entity carries nswtrains' trip", "BM30.131126.16.N.2.1" in trips(p))
check("Sydney updates yielded counted", c.stats()["sydney_updates_yielded"] == 3)

# ================================================================== failures
nvp, ntu, svp, stu = feed(), feed(), feed(), feed()
nsw_vehicle(nvp, NSW_T, "4T.T.BMT")
nsw_vehicle(nvp, "NT32.1421.091126.116.X.7.9", "4T.T.NT32", vid="XP2001")
syd_vehicle(svp, "BM20.1421.149.100.D.10.1", "BMT_1")
syd_vehicle(svp, "W665.1421.149.100.D.10.1", "IWL_1a")
upd(ntu, NSW_T, "4T.T.BMT", "20261113")
upd(stu, "W665.1421.149.100.D.10.1", "IWL_1a")
good = answers(nvp, ntu, svp, stu)

a = dict(good)
a[tf.SYDNEY_POSITIONS_URL] = 500
c, net = make({tf.COMMUTER: KEY_A, tf.INTERCITY: KEY_A, tf.TRANSIT: KEY_A}, sydney=(tf.COMMUTER,), ans=a)
check("Sydney down: transit raises FeedUnavailable", raises(tf.FeedUnavailable, lambda: c.for_consumer(tf.TRANSIT)))
check("Sydney down: commuter asking gets nswtrains'", trips(c.for_consumer(tf.COMMUTER)[0]) == [NSW_T])
check("Sydney down: intercity not asking unaffected", trips(c.for_consumer(tf.INTERCITY)[0]) == ["NT32.1421.091126.116.X.7.9"])
check("Sydney down: one round of calls for all three inside the TTL", net.count(tf.SYDNEY_POSITIONS_URL) == 1 and net.count(tf.POSITIONS_URL) == 1)
check("Sydney down: its updates not asked", net.count(tf.SYDNEY_TRIP_UPDATES_URL) == 0)

a = dict(good)
a[tf.POSITIONS_URL] = 500
c, net = make({tf.COMMUTER: KEY_A, tf.INTERCITY: KEY_A, tf.TRANSIT: KEY_A}, sydney=(tf.COMMUTER,), ans=a)
check("nswtrains down: intercity not asking raises", raises(tf.FeedUnavailable, lambda: c.for_consumer(tf.INTERCITY)))
p, u = c.for_consumer(tf.COMMUTER)
check("nswtrains down: commuter asking gets Sydney's", trips(p) == ["BM20.1421.149.100.D.10.1"] and trips(u) == [])
check("nswtrains down: transit served", trips(c.for_consumer(tf.TRANSIT)[0]) == ["W665.1421.149.100.D.10.1"])

a = dict(good)
a[tf.POSITIONS_URL] = 500
a[tf.SYDNEY_POSITIONS_URL] = 500
c, net = make({tf.COMMUTER: KEY_A, tf.TRANSIT: KEY_A}, sydney=(tf.COMMUTER,), ans=a)
check("both down: raises", raises(tf.FeedUnavailable, lambda: c.for_consumer(tf.TRANSIT)))
check("both down: nothing cached, asked again", raises(tf.FeedUnavailable, lambda: c.for_consumer(tf.COMMUTER))
      and net.count(tf.SYDNEY_POSITIONS_URL) == 2)
c, net = make({tf.TRANSIT: KEY_A}, ans=a)
check("transit only, Sydney down: raises", raises(tf.FeedUnavailable, lambda: c.for_consumer(tf.TRANSIT)))

a = dict(good)
a[tf.SYDNEY_TRIP_UPDATES_URL] = 500
c, net = make({tf.COMMUTER: KEY_A, tf.TRANSIT: KEY_A}, sydney=(tf.COMMUTER,), ans=a)
check("Sydney updates down: transit's updates None", c.for_consumer(tf.TRANSIT)[1] is None
      and trips(c.for_consumer(tf.TRANSIT)[0]) == ["W665.1421.149.100.D.10.1"])
check("Sydney updates down: commuter's from nswtrains", trips(c.for_consumer(tf.COMMUTER)[1]) == [NSW_T])
a[tf.TRIP_UPDATES_URL] = 500
c, net = make({tf.COMMUTER: KEY_A, tf.TRANSIT: KEY_A}, sydney=(tf.COMMUTER,), ans=a)
check("both updates down: commuter's updates None", c.for_consumer(tf.COMMUTER)[1] is None)
a = dict(good)
a[tf.TRIP_UPDATES_URL] = 500
c, net = make({tf.COMMUTER: KEY_A, tf.TRANSIT: KEY_A}, sydney=(tf.COMMUTER,), ans=a)
check("nswtrains updates down: commuter's from Sydney Trains", trips(c.for_consumer(tf.COMMUTER)[1]) == [])
a = dict(good)
a[tf.SYDNEY_POSITIONS_URL] = 429
clk = Clock()
c, net = make({tf.COMMUTER: KEY_A, tf.TRANSIT: KEY_A}, sydney=(tf.COMMUTER,), ans=a, clock=clk)
check("Sydney 429: transit RateLimited", raises(tf.RateLimited, lambda: c.for_consumer(tf.TRANSIT)))
check("Sydney 429: backing off", c.stats()["backing_off"] is True)
clk.t += 60
n_before = len(net.calls)
check("Sydney 429: the next round asks nothing and raises", raises(tf.RateLimited, lambda: c.for_consumer(tf.TRANSIT))
      and len(net.calls) == n_before)
a = dict(good)
a[tf.SYDNEY_POSITIONS_URL] = 401
c, net = make({tf.TRANSIT: KEY_A}, ans=a)
check("Sydney 401: KeyRefused", raises(tf.KeyRefused, lambda: c.for_consumer(tf.TRANSIT)))
check("Sydney 401: the refusal is checked on Sydney's own updates", net.count(tf.SYDNEY_TRIP_UPDATES_URL) == 1
      and net.count(tf.TRIP_UPDATES_URL) == 0)
check("the key on every Sydney request", all(x[2] == "apikey " + KEY_A for x in net.calls if "sydneytrains" in x[1]))

# ================================================================== the Sydney Trains timetable
tmp = tmpdir()
net = Net(good)
clk = Clock()
c = tf.TfnswClient({tf.TRANSIT: KEY_A}, opener=net, clock=clk, background=False, storage_dir=tmp)
check("no timetable yet", c.static_path(tf.SYDNEYTRAINS) is None and c.static_version(tf.SYDNEYTRAINS) is None)
c.for_consumer(tf.TRANSIT)
zp = os.path.join(tmp, "saw_tfnsw_sydneytrains.zip")
check("downloaded on first poll: HEAD then GET", net.count(tf.SYDNEY_STATIC_URL, "HEAD") == 1 and net.count(tf.SYDNEY_STATIC_URL) == 1)
check("on disk under a neutral name", c.static_path(tf.SYDNEYTRAINS) == zp and os.path.exists(zp))
check("version", c.static_version(tf.SYDNEYTRAINS) == MOD)
check("no temp, meta beside it", sorted(os.listdir(tmp)) == ["saw_tfnsw_sydneytrains.json", "saw_tfnsw_sydneytrains.zip"])
check("stats", c.stats()["sydney_timetable"] == MOD and c.stats()["sydney_timetable_routes"] == len(ROUTES))
check("nswtrains' path unaffected", c.static_path() is None and c.static_version(tf.NSWTRAINS) is None)
check("unknown timetable refused", raises(ValueError, lambda: c.static_path("metro")) and raises(ValueError, lambda: c.static_version("x"))
      and raises(ValueError, lambda: c.refresh_static(timetable="x")))
check("inside a day: nothing asked", c.refresh_static(timetable=tf.SYDNEYTRAINS) is False and net.count(tf.SYDNEY_STATIC_URL, "HEAD") == 1)
check("forced, unchanged: HEAD only", c.refresh_static(force=True, timetable=tf.SYDNEYTRAINS) is False
      and net.count(tf.SYDNEY_STATIC_URL, "HEAD") == 2 and net.count(tf.SYDNEY_STATIC_URL) == 1)
before = open(zp, "rb").read()
net.modified[tf.SYDNEY_STATIC_URL] = "Fri, 13 Nov 2026 14:01:15 GMT"
for label, bad in (("a failed download", 500), ("a broken zip", b"not a zip"), ("calendar.txt missing", syd_zip(drop=("calendar.txt",)))):
    net.statics[tf.SYDNEY_STATIC_URL] = bad
    check(label + ": False", c.refresh_static(force=True, timetable=tf.SYDNEYTRAINS) is False)
    check(label + ": the copy kept", open(zp, "rb").read() == before and c.static_version(tf.SYDNEYTRAINS) == MOD)
    check(label + ": no temp", ".tmp" not in "".join(os.listdir(tmp)))
net.statics[tf.SYDNEY_STATIC_URL] = syd_zip(routes=ROUTES[:3])
check("changed: downloaded", c.refresh_static(force=True, timetable=tf.SYDNEYTRAINS) is True
      and c.static_version(tf.SYDNEYTRAINS) == "Fri, 13 Nov 2026 14:01:15 GMT" and c.stats()["sydney_timetable_routes"] == 3)

# the previous calendar for yesterday, until 08:00 Sydney (design #13)
tmp = tmpdir()
early = datetime(2026, 11, 14, 2, 0, tzinfo=SYD).timestamp()          # Saturday 02:00: Friday's trips still run
svp = feed()
syd_vehicle(svp, "Y1.1421.149.100.D.1.1", "IWL_1a", ts=early - 30, label="23:40 Central Station to Penrith Station ")
clk = Clock(early)
net = Net(answers(svp=svp))
c = tf.TfnswClient({tf.TRANSIT: KEY_A}, opener=net, clock=clk, background=False, storage_dir=tmp)
check("Friday's run dated Friday after midnight", c.for_consumer(tf.TRANSIT)[0].entity[0].vehicle.trip.start_date == "20261113")
saturday = (("8.8.8", "0000010", "20261114", "20261114"),)
net.modified[tf.SYDNEY_STATIC_URL] = "Fri, 13 Nov 2026 14:01:15 GMT"
net.statics[tf.SYDNEY_STATIC_URL] = syd_zip(calendar=saturday)          # published 01:01: Friday's services gone
check("new publication taken", c.refresh_static(force=True, timetable=tf.SYDNEYTRAINS) is True)
check("the previous calendar still dates Friday's run", c.for_consumer(tf.TRANSIT, force=True)[0].entity[0].vehicle.trip.start_date == "20261113")
c2 = tf.TfnswClient({tf.TRANSIT: KEY_A}, opener=net, clock=clk, background=False, storage_dir=tmp)
check("a restart keeps the previous calendar", c2.for_consumer(tf.TRANSIT)[0].entity[0].vehicle.trip.start_date == "20261113")
clk.t = datetime(2026, 11, 14, 8, 0, 1, tzinfo=SYD).timestamp()
check("after 08:00 it no longer answers", not c.for_consumer(tf.TRANSIT, force=True)[0].entity[0].vehicle.trip.HasField("start_date"))
c3 = tf.TfnswClient({tf.TRANSIT: KEY_A}, opener=net, clock=clk, background=False, storage_dir=tmp)
check("nor after a restart", not c3.for_consumer(tf.TRANSIT)[0].entity[0].vehicle.trip.HasField("start_date"))

tmp = tmpdir()
net = Net(good)
c = tf.TfnswClient({tf.TRANSIT: KEY_A}, opener=net, clock=Clock(), background=False, storage_dir=tmp)
net.statics[tf.SYDNEY_STATIC_URL] = 500
c.for_consumer(tf.TRANSIT)
check("a failed first download: feeds served, nothing on disk", c.static_version(tf.SYDNEYTRAINS) is None
      and trips(c.for_consumer(tf.TRANSIT)[0]) == ["W665.1421.149.100.D.10.1"])
check("no storage_dir: no Sydney timetable", make({tf.TRANSIT: KEY_A}, storage=False)[0].static_path(tf.SYDNEYTRAINS) is None
      and make({tf.TRANSIT: KEY_A}, storage=False)[0].refresh_static(timetable=tf.SYDNEYTRAINS) is False)


# ================================================================== the reference registration
class Config:
    def __init__(self, base):
        self.base = base

    def path(self, name):
        return os.path.join(self.base, name)


class Hass:
    def __init__(self):
        self.data = {}
        self.config = Config(tmpdir())


REF = os.path.join(ROOT, "reference", "shared_tfnsw.py")
# The integrations' copies are byte-exact with this file (Commuter Rail's test_nsw); 0.2.37 leaves it as 0.2.23 wrote it.
check("ref: unchanged since 0.2.23", hashlib.sha256(open(REF, "rb").read().replace(b"\r\n", b"\n")).hexdigest()
      == "21c3db52078e191aa8667f17c6d069d661adb16090607970221ca80b145465cb")
h = Hass()
c1 = stf.get_shared_tfnsw_client(h, tf.COMMUTER, KEY_A)
check("ref: registered, not asking", c1.consumers == {tf.COMMUTER} and c1.sydney_consumers == frozenset())
c1.set_sydney_trains(tf.COMMUTER, True)
check("ref: the integration asks after claiming", c1.sydney_consumers == {tf.COMMUTER})
c2 = stf.get_shared_tfnsw_client(h, tf.COMMUTER, KEY_A)          # a second config entry of the same integration
c2.set_sydney_trains(tf.COMMUTER, True)
check("ref: a second claim leaves it asking", c1 is c2 and c1.sydney_consumers == {tf.COMMUTER}
      and h.data[tf.SHARED_KEY]["consumers"] == {tf.COMMUTER: 2})
stf.release_shared_tfnsw_client(h, tf.COMMUTER)
check("ref: one claim released: still asking", tf.COMMUTER in c1.sydney_consumers and tf.COMMUTER in c1.consumers)
stf.get_shared_tfnsw_client(h, tf.TRANSIT, KEY_B)
check("ref: transit takes Sydney Trains unasked", tf.TRANSIT in c1.sydney_consumers)
stf.release_shared_tfnsw_client(h, tf.COMMUTER)
check("ref: the last claim unregisters and forgets the asking", tf.COMMUTER not in c1.consumers
      and tf.COMMUTER not in c1.sydney_consumers)
c3 = stf.get_shared_tfnsw_client(h, tf.COMMUTER, KEY_A)
check("ref: claimed again, not asking until it asks again", c3 is c1 and tf.COMMUTER not in c1.sydney_consumers)
stf.release_shared_tfnsw_client(h, tf.COMMUTER)
stf.release_shared_tfnsw_client(h, tf.TRANSIT)
check("ref: the last claim of all removes the entry", tf.SHARED_KEY not in h.data)
h = Hass()
other = tf.TfnswClient({tf.INTERCITY: KEY_A})
foreign = {"contract_version": 99, "entry_shape": stf._ENTRY_SHAPE, "client": other, "consumers": {}}
h.data[tf.SHARED_KEY] = foreign
priv = stf.get_shared_tfnsw_client(h, tf.COMMUTER, KEY_A)
priv.set_sydney_trains(tf.COMMUTER, True)
check("ref: a foreign entry: a private client, which asks the same way", priv is not other
      and priv.sydney_consumers == {tf.COMMUTER} and h.data[tf.SHARED_KEY] is foreign and other.sydney_consumers == frozenset())
check("ref: storage under .storage", priv.storage_dir == h.config.path(".storage"))

# ================================================================== gtfs.realtime.sequence_by_stop
rows = [(1, "A"), (2, "B"), (3, "C"), (4, "B"), (5, "D")]
f = feed()
upd(f, "T1", stops=("B", "C", "B", "D"))
tu1 = f.entity[0].trip_update
check("sequence_by_stop: all placed", rt.sequence_by_stop(tu1, rows) == 4)
check("sequence_by_stop: a stop visited twice gets each visit", [s.stop_sequence for s in tu1.stop_time_update] == [2, 3, 4, 5])
f = feed()
upd(f, "T1b", stops=("B", "B"))
check("sequence_by_stop: a stop twice in a row is two visits", rt.sequence_by_stop(f.entity[0].trip_update, rows) == 2
      and [s.stop_sequence for s in f.entity[0].trip_update.stop_time_update] == [2, 4])
f = feed()
upd(f, "T2", stops=("C", "X", "A", "D"))
tu2 = f.entity[0].trip_update
check("sequence_by_stop: unknown and behind removed", rt.sequence_by_stop(tu2, rows) == 2
      and [(s.stop_id, s.stop_sequence) for s in tu2.stop_time_update] == [("C", 3), ("D", 5)])
f = feed()
upd(f, "T3", stops=("A",))
f.entity[0].trip_update.stop_time_update.add().arrival.delay = 5          # no stop_id
check("sequence_by_stop: a stop update with no stop_id removed", rt.sequence_by_stop(f.entity[0].trip_update, rows) == 1
      and len(f.entity[0].trip_update.stop_time_update) == 1)
check("sequence_by_stop: padded rows, string sequences", rt.sequence_by_stop(feed().entity.add().trip_update, []) == 0)
f = feed()
upd(f, "T4", stops=("B",), delay=120)
rt.sequence_by_stop(f.entity[0].trip_update, [("1", " A "), ("2", "B ")])
check("sequence_by_stop: then update_delays reads it", schedule.update_delays(f.entity[0].trip_update) == ({2: 120}, set()))
check("sequence_by_stop: exported through gtfs.realtime only", hasattr(rt, "sequence_by_stop"))

# ================================================================== stats never carry a key
c, _ = make(ALL, sydney=BOTH, ans=good)
c.for_consumer(tf.COMMUTER)
st = c.stats()
check("stats fields", {"sydney_timetable", "sydney_consumers", "before_departure_dropped", "kept_last_fix", "undated",
                       "sydney_updates_yielded", "runs_remembered"} <= set(st) and st["sydney_consumers"] == ["commuter", "intercity", "transit"])
check("stats carry no key", KEY_A not in repr(st))

for d in TMPS:
    shutil.rmtree(d, ignore_errors=True)
failed = [n for n, ok in CHECKS if not ok]
for n in failed:
    print("FAIL", n)
print("%d checks, %d failed" % (len(CHECKS), len(failed)))
sys.exit(1 if failed else 0)
