"""Tests for saw_livetrack.transperth and its shared registration.

Every case is a MEASURED finding of the PTA's zip (35_Dependencies/third_party/transperth/SSOT.md, 2026-11-13: the
agencies and route_types, stops.txt's spaced header, parent stations, times past 24:00, no daylight saving) or a
decision of Lee's (Transperth's trains to Commuter Rail, 2026-11-13).
Run:  python tests/test_transperth.py  -> exit 0
"""
import datetime as dt
import io
import os
import shutil
import sys
import tempfile
import urllib.error
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "reference"))

import saw_livetrack.transperth as tp  # noqa: E402
import shared_transperth as stp  # noqa: E402

CHECKS = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


# --- who draws what -------------------------------------------------------------
check("Transperth rail to commuter", tp.consumer_of("WES-RAI", "Transperth", "2") == tp.COMMUTER)
check("Transwa rail to intercity", tp.consumer_of("TRA-TRA", "Transwa", "2") == tp.INTERCITY)
check("other rail to nobody", tp.consumer_of("XXX", "Transperth", "2") is None)
check("Transperth bus to transit", tp.consumer_of("SWA-JOO", "Transperth", "3") == tp.TRANSIT)
check("Transperth ferry to transit", tp.consumer_of("PWT-FRY", "Transperth", "4") == tp.TRANSIT)
check("other ferry to nobody", tp.consumer_of("ABC", "Somebody", "4") is None)
check("Transwa coach to bus", tp.consumer_of("TRA-TRA", "Transwa", "3") == tp.BUS)
check("town bus to bus", tp.consumer_of("STR-BBY", "TransBunbury", "3") == tp.BUS)
check("bus with no agency to nobody", tp.consumer_of("", "", "3") is None)
check("padded values read", tp.consumer_of(" WES-RAI ", " Transperth ", " 2 ") == tp.COMMUTER)
check("unknown type to nobody", tp.consumer_of("WES-RAI", "Transperth", "0") is None)
check("not strings to nobody", tp.consumer_of(None, None, 2) is None)
check("consumers", tp.CONSUMERS == ("commuter", "intercity", "transit", "bus"))
check("no daylight saving: UTC+8", tp.TZ.utcoffset(None) == dt.timedelta(hours=8))
check("attribution is the licence's statement", "available free of charge from www.transperth.wa.gov.au"
      in tp.ATTRIBUTION and "Transperth " not in tp.ATTRIBUTION.replace("www.transperth", ""))


# --- a zip shaped like the PTA's -------------------------------------------------
def make_zip(drop=None, extra_trip=True):
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
                      "WES-RAI,Transperth,http://x,Australia/Perth\nTRA-TRA,Transwa,http://y,Australia/Perth\n"
                      "SWA-JOO,Transperth,http://x,Australia/Perth\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type,route_color\n"
                      "WES-RAI-1865,WES-RAI,,Mandurah Line,2,cb6015\nTRA-TRA-137,TRA-TRA,,Australind,2,CF4520\n"
                      "SWA-JOO-1,SWA-JOO,460,,3,\n",
        "trips.txt": "route_id,service_id,trip_id,direction_id,trip_headsign,shape_id\n"
                     "WES-RAI-1865,WD,T1,1,Mandurah Stn,S1\nWES-RAI-1865,WD,T2,0,Perth Underground Stn,S1\n"
                     "WES-RAI-1865,WD,TLATE,1,Mandurah Stn,S1\nWES-RAI-1865,WD,TNOSHAPE,1,Mandurah Stn,\n"
                     "TRA-TRA-137,WD,X1,1,Bunbury,S9\nSWA-JOO-1,WD,B1,0,Joondalup,S8\n"
                     + ("WES-RAI-1865,SAT,TSAT,1,Mandurah Stn,S1\n" if extra_trip else ""),
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type\n"
                          "T1,08:00:00,08:00:00,99602,1,0,1\nT1,08:10:00,08:11:00,99662,2,0,0\n"
                          "T1,08:30:00,08:30:00,99732,3,1,0\n"
                          "T2,09:00:00,09:00:00,99731,1,0,1\nT2,09:30:00,,99601,2,1,0\n"
                          "TLATE,25:10:00,25:10:00,99602,1,0,1\nTLATE,25:40:00,25:40:00,99732,2,1,0\n"
                          "TNOSHAPE,08:00:00,08:00:00,99602,1,0,1\nTNOSHAPE,08:30:00,08:30:00,99732,2,1,0\n"
                          "X1,06:00:00,06:00:00,95022,1,0,1\nX1,08:30:00,08:30:00,95499,2,1,0\n"
                          "B1,07:00:00,07:00:00,10001,1,0,0\nB1,07:20:00,07:20:00,10002,2,0,0\n"
                          "TSAT,08:00:00,08:00:00,99602,1,0,1\nTSAT,08:30:00,08:30:00,99732,2,1,0\n",
        # The PTA's header carries a space after every comma (MEASURED 2026-11-13).
        "stops.txt": "location_type, parent_station, stop_id, stop_code, stop_name, stop_lat, stop_lon\n"
                     "1,,64,,Perth Underground Stn,-31.9516,115.8582\n"
                     "0,64,99602,,Perth Underground Stn Platform 2,-31.951595,115.858186\n"
                     "0,64,99601,,Perth Underground Stn Platform 1,-31.951569,115.858099\n"
                     "0,,99662,, Murdoch Stn Platform 2 ,-32.0669,115.8414\n"
                     "0,,99731,,Mandurah Stn Platform 1,-32.5242,115.7432\n"
                     "0,,99732,,Mandurah Stn Platform 2,-32.5243,115.7433\n"
                     "0,,95022,,East Perth,-31.95,115.87\n0,,95499,,Bunbury,-33.36,115.66\n"
                     "0,,10001,,Bus A,-31.7,115.7\n0,,10002,,Bus B,-31.71,115.71\n",
        "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
                      "S1,-31.95,115.86,1\nS1,-32.52,115.74,2\nS9,-31.95,115.87,1\nS9,-33.36,115.66,2\n"
                      "S8,-31.7,115.7,1\nS8,-31.71,115.71,2\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                        "WD,1,1,1,1,1,0,0,20261110,20270208\nSAT,0,0,0,0,0,1,0,20261110,20270208\n",
        "calendar_dates.txt": "service_id,date,exception_type\nWD,20261117,2\nSAT,20261117,1\n",
    }
    if drop:
        files.pop(drop)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


RAW = make_zip()
check("route_ids commuter", tp.route_ids(RAW, tp.COMMUTER) == {"WES-RAI-1865"})
check("route_ids intercity", tp.route_ids(RAW, tp.INTERCITY) == {"TRA-TRA-137"})
check("route_ids transit", tp.route_ids(RAW, tp.TRANSIT) == {"SWA-JOO-1"})
try:
    tp.route_ids(RAW, "freight")
    check("unknown consumer refused", False)
except ValueError:
    check("unknown consumer refused", True)

CUT = tp.cut(RAW, tp.COMMUTER)
cz = zipfile.ZipFile(io.BytesIO(CUT))
stops_head = cz.read("stops.txt").decode().splitlines()[0]
check("cut: stops header stripped", stops_head == "location_type,parent_station,stop_id,stop_code,stop_name,stop_lat,"
      "stop_lon")
trips_cut = cz.read("trips.txt").decode()
check("cut: commuter trips only", "T1," in trips_cut and "X1," not in trips_cut and "B1," not in trips_cut)
st_cut = cz.read("stop_times.txt").decode()
check("cut: stop times of kept trips only", "T1," in st_cut and "X1," not in st_cut and "B1," not in st_cut)
stops_cut = cz.read("stops.txt").decode()
check("cut: used stops and their parent kept", "99602" in stops_cut and ",64," in stops_cut and "\n1,,64," in stops_cut
      and "95022" not in stops_cut and "10001" not in stops_cut)
check("cut: cells stripped", ",Murdoch Stn Platform 2," in stops_cut and ", Murdoch" not in stops_cut)
check("cut: shapes of kept trips only", "S1," in cz.read("shapes.txt").decode()
      and "S9," not in cz.read("shapes.txt").decode())
check("cut: routes and agency", "Mandurah Line" in cz.read("routes.txt").decode()
      and "Australind" not in cz.read("routes.txt").decode()
      and "WES-RAI" in cz.read("agency.txt").decode() and "Transwa" not in cz.read("agency.txt").decode())
check("cut: calendars of kept services", "WD," in cz.read("calendar.txt").decode()
      and "SAT," in cz.read("calendar.txt").decode())

MON = dt.date(2026, 11, 16)
T = tp.read_timetable(CUT, tp.COMMUTER, MON)
check("table: commuter trips with a shape", set(T["trips"]) == {"T1", "T2", "TLATE", "TSAT"})
check("table: trip row", T["trips"]["T1"] == ["WES-RAI-1865", "WD", 8 * 3600, 8 * 3600 + 1800, "Mandurah Stn", "1"])
check("table: past midnight in seconds", T["trips"]["TLATE"][2] == 25 * 3600 + 600)
check("table: calls in order", T["calls"]["T1"] == [["99602", 28800, 28800], ["99662", 29400, 29460],
                                                    ["99732", 30600, 30600]])
check("table: platform named by its parent", T["stop_names"]["99602"] == "Perth Underground Stn")
check("table: stop positions", T["stops"]["99602"] == [-31.951595, 115.858186])
check("table: route name and colour", T["routes"]["WES-RAI-1865"] == ["Mandurah Line", "#CB6015"])
check("table: weekday dates, the removed Tuesday out", "2026-11-16" in T["dates"]["WD"]
      and "2026-11-17" not in T["dates"]["WD"] and "2026-11-15" not in T["dates"]["WD"])
check("table: an added date in", "2026-11-17" in T["dates"]["SAT"] and "2026-11-21" in T["dates"]["SAT"])
check("table: window yesterday to six days on", min(T["dates"]["WD"]) >= "2026-11-15"
      and max(T["dates"]["WD"]) <= "2026-11-22")
check("table: a last call with no departure takes its arrival", T["trips"]["T2"][3] == 9 * 3600 + 1800)
check("table: yesterday is in the window", "2026-11-16" in tp.read_timetable(CUT, tp.COMMUTER,
                                                                             MON + dt.timedelta(days=1))["dates"]["WD"])
check("table from the whole zip equals the cut's", tp.read_timetable(RAW, tp.COMMUTER, MON)["trips"] == T["trips"])

# --- time -------------------------------------------------------------------------
base = tp.service_base(MON)
check("service base is local midnight in Perth", base == dt.datetime(2026, 11, 16, tzinfo=tp.TZ).timestamp())
check("local day in Perth", tp.local_day(dt.datetime(2026, 11, 16, 17, 0, tzinfo=dt.timezone.utc).timestamp())
      == dt.date(2026, 11, 17))


def at(h, m=0, day=MON):
    return tp.service_base(day) + h * 3600 + m * 60


def runs(now):
    rs, sk = tp.scheduled_runs(T, now)
    return {r.trip_id: r for r in rs}, sk


r, _ = runs(at(7, 50))
check("before the pre-departure window: not out", "T1" not in r)
r, _ = runs(at(7, 56))
check("at its origin 4 min before it leaves: standing at 0", "T1" in r and (r["T1"].state, r["T1"].index)
      == ("standing", 0))
check("run key is trip and day of month", r["T1"].key == "T1-16" and r["T1"].code == "WES-RAI-1865")
check("no delay ever", r["T1"].delay is None)
r, _ = runs(at(8, 5))
check("between calls: running from 0", (r["T1"].state, r["T1"].index) == ("running", 0))
r, _ = runs(at(8, 10, ) + 30)
check("between arrival and departure: standing at 1", (r["T1"].state, r["T1"].index) == ("standing", 1))
r, _ = runs(at(8, 31))
check("just arrived at the end: standing at the last call", (r["T1"].state, r["T1"].index) == ("standing", 2))
r, _ = runs(at(8, 33))
check("past FINISHED_S after its end: gone", "T1" not in r)
r, _ = runs(at(8, 5, MON + dt.timedelta(days=1)))
check("removed date: not run", "T1" not in r)
r, _ = runs(at(8, 5, MON + dt.timedelta(days=1)))
check("added date: run", "TSAT" in r)
r, _ = runs(at(1, 20, MON + dt.timedelta(days=1)))
check("yesterday's trip after midnight runs, keyed by its service day", "TLATE" in r and r["TLATE"].key == "TLATE-16"
      and r["TLATE"].state == "running")
r, sk = tp.scheduled_runs({}, at(8))
check("no timetable: nothing, counted", r == [] and sk == {"no_timetable": 1})
T2 = dict(T, calls=dict(T["calls"], T1=[["99602", None, None], ["99732", 30600, 30600]]))
r, sk = tp.scheduled_runs(T2, at(8, 5))
check("a trip with fewer than two timed calls: skipped as no_times",
      "T1" not in {x.trip_id for x in r} and sk.get("no_times") == 1)


# --- the client --------------------------------------------------------------------
class Resp:
    def __init__(self, body=b"", headers=None, error=None):
        self.body, self.headers, self.error = io.BytesIO(body), headers or {}, error

    def __enter__(self):
        if self.error:
            raise self.error
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body.read(n)


class Opener:
    def __init__(self):
        self.version = "Thu, 12 Nov 2026 00:16:38 GMT"
        self.body = RAW
        self.calls = []
        self.fail = None

    def __call__(self, req, timeout):
        self.calls.append(req.get_method())
        if self.fail:
            raise self.fail
        if req.get_method() == "HEAD":
            return Resp(headers={"Last-Modified": self.version})
        return Resp(self.body)


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


tmp = tempfile.mkdtemp()
try:
    op, clk = Opener(), Clock()
    c = tp.TransperthClient((tp.COMMUTER,), opener=op, clock=clk, storage_dir=tmp, background=False)
    check("first static_path downloads", c.static_path() is not None and op.calls == ["HEAD", "GET"])
    check("version is Last-Modified", c.static_version() == op.version)
    with open(c.static_path(), "rb") as fh:
        check("the zip on disk is the body", fh.read() == RAW)
    c.static_path()
    check("checked within the day: no request", op.calls == ["HEAD", "GET"])
    clk.t += tp.STATIC_TTL_S + 1
    c.static_path()
    check("next day, same Last-Modified: a HEAD only", op.calls == ["HEAD", "GET", "HEAD"] and c.downloads == 1)
    clk.t += tp.STATIC_TTL_S + 1
    op.version = "Fri, 20 Nov 2026 00:00:00 GMT"
    c.static_path()
    check("changed Last-Modified: downloaded again", c.downloads == 2 and c.static_version() == op.version)
    # A fresh client on the same folder finds the copy and its version without a request.
    op2 = Opener()
    c2 = tp.TransperthClient((tp.COMMUTER,), opener=op2, clock=clk, storage_dir=tmp, background=False)
    check("a new client finds the copy on disk", c2.static_version() == op.version and c2.static_path()
          and op2.calls == [])
    # A broken zip never replaces the good one.
    clk.t += tp.STATIC_TTL_S + 1
    op.version, op.body = "Sat, 21 Nov 2026 00:00:00 GMT", make_zip(drop="shapes.txt")
    c.refresh_static()
    with open(c.static_path(), "rb") as fh:
        check("a zip lacking a required file is refused, the old copy kept", fh.read() == RAW
              and c.static_version() == "Fri, 20 Nov 2026 00:00:00 GMT" and c.last_error == "FeedUnavailable")
    check("no temp file left", not any(n.endswith(".tmp") for n in os.listdir(tmp)))
    # Failure: retried after STATIC_RETRY_S, not before.
    clk.t += 60
    n = len(op.calls)
    c.refresh_static()
    check("a failed check is not retried within the hour", len(op.calls) == n)
    op.fail = urllib.error.HTTPError(tp.STATIC_URL, 503, "x", {}, None)
    clk.t += tp.STATIC_RETRY_S + 1
    check("HTTP error: False, copy kept", c.refresh_static() is False and c.static_path() is not None
          and c.last_error == "FeedUnavailable")
    big = tp.TransperthClient((tp.COMMUTER,), opener=Opener(), clock=Clock(), storage_dir=tempfile.mkdtemp(),
                              background=False)
    old_max = tp.STATIC_MAX_BYTES
    tp.STATIC_MAX_BYTES = 100
    try:
        check("over the size limit: refused", big.static_path() is None and big.last_error == "FeedUnavailable")
    finally:
        tp.STATIC_MAX_BYTES = old_max
        shutil.rmtree(big.storage_dir, ignore_errors=True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

none = tp.TransperthClient((tp.COMMUTER,), opener=Opener(), background=False)
check("no storage_dir: no timetable", none.static_path() is None and none.refresh_static() is False)
check("register new", none.register(tp.TRANSIT) is True and none.register(tp.TRANSIT) is False)
check("unregister", none.unregister(tp.TRANSIT) is True and none.unregister(tp.TRANSIT) is False)
try:
    tp.TransperthClient((tp.COMMUTER,), user=" ")
    check("blank User-Agent refused", False)
except ValueError:
    check("blank User-Agent refused", True)
check("stats", set(none.stats()) == {"timetable_version", "consumers", "fetch_count", "downloads", "last_error"})


# --- the shared registration -------------------------------------------------------
class Config:
    def path(self, *parts):
        return os.path.join(tempfile.gettempdir(), *parts)


class Hass:
    def __init__(self):
        self.data = {}
        self.config = Config()


h = Hass()
a = stp.get_shared_transperth_client(h, "commuter")
check("first claim creates the entry", h.data[tp.SHARED_KEY]["client"] is a and a.consumers == frozenset({"commuter"})
      and a.storage_dir.endswith(".storage"))
b = stp.get_shared_transperth_client(h, "intercity")
check("second integration finds it and registers", b is a and a.consumers == frozenset({"commuter", "intercity"}))
stp.get_shared_transperth_client(h, "commuter")
check("counts per consumer", h.data[tp.SHARED_KEY]["claims"] == {"commuter": 2, "intercity": 1}
      and h.data[tp.SHARED_KEY]["refcount"] == 3)
stp.release_shared_transperth_client(h, "commuter")
check("one of two commuter claims released: still registered", "commuter" in a.consumers)
stp.release_shared_transperth_client(h, "bus")
check("never claimed: nothing changes", h.data[tp.SHARED_KEY]["refcount"] == 2)
stp.release_shared_transperth_client(h, "commuter")
check("last commuter claim: unregistered", "commuter" not in a.consumers)
stp.release_shared_transperth_client(h, "intercity")
check("last claim of all: entry gone", tp.SHARED_KEY not in h.data)
other = tp.TransperthClient(())
h.data[tp.SHARED_KEY] = {"contract_version": 99, "entry_shape": 1, "claims": {}, "client": other, "refcount": 1}
c = stp.get_shared_transperth_client(h, "commuter")
check("another contract version, same shape: private client", c is not other and "commuter" not in other.consumers)
h.data[tp.SHARED_KEY] = {"contract_version": 99}
c = stp.get_shared_transperth_client(h, "commuter")
check("foreign entry: private client, entry untouched", isinstance(c, tp.TransperthClient)
      and h.data[tp.SHARED_KEY] == {"contract_version": 99})
check("no homeassistant import in the module",
      not any(ln.lstrip().startswith(("import homeassistant", "from homeassistant"))
              for ln in open(tp.__file__, encoding="utf-8")))

bad_checks = [n for n, ok in CHECKS if not ok]
for n in bad_checks:
    print("FAIL", n)
print("%d checks, %d failed" % (len(CHECKS), len(bad_checks)))
sys.exit(1 if bad_checks else 0)
