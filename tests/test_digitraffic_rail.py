"""Tests for saw_livetrack.digitraffic_rail and its shared registration.

Every case is a MEASURED finding (capture round 1, 2026-10-11..13) or a
decision of Lee's. Run:  python tests/test_digitraffic_rail.py  -> exit 0
"""
import datetime as _dt
import gzip
import io
import json
import os
import sys
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "reference"))

import saw_livetrack.digitraffic_rail as dr  # noqa: E402
import shared_digitraffic_rail as sdr  # noqa: E402

CHECKS = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


UTC = _dt.timezone.utc

# --- two real positions, as captured 2026-10-11 08:39:22Z, plus junk -------
LOCS = [
    {"accuracy": 0, "isGpsLocation": True,
     "location": {"type": "Point", "coordinates": [29.77802, 62.580419]},
     "speed": 51, "trainNumber": 1, "departureDate": "2026-10-11",
     "timestamp": "2026-10-11T08:39:19.000Z"},
    {"accuracy": 0, "isGpsLocation": True,
     "location": {"type": "Point", "coordinates": [25.475093, 60.777581]},
     "speed": 122, "trainNumber": 3, "departureDate": "2026-10-11",
     "timestamp": "2026-10-11T08:38:44.000Z"},
    {"trainNumber": 7, "departureDate": "2026-10-11"},                 # no location
    {"trainNumber": 9, "departureDate": "2026-10-11",
     "location": {"coordinates": [25.0, 95.0]}},                        # impossible lat
    "junk",
]


def row(kind, station, sched, actual=None, diff=None, stop=True, commercial=True,
        estimate=None, cancelled=False):
    r = {"type": kind, "stationShortCode": station, "scheduledTime": sched,
         "trainStopping": stop, "cancelled": cancelled}
    if commercial:
        r["commercialStop"] = True
    if actual:
        r["actualTime"] = actual
    if diff is not None:
        r["differenceInMinutes"] = diff
    if estimate:
        r["liveEstimateTime"] = estimate
    return r


def train(number, category, ttype, version=100, line="", rows=None, date="2026-10-11",
          operator="vr", cancelled=False):
    return {"trainNumber": number, "departureDate": date, "trainCategory": category,
            "trainType": ttype, "commuterLineID": line, "operatorShortCode": operator,
            "version": version, "cancelled": cancelled, "timeTableRows": rows or []}


# --- parse_locations -------------------------------------------------------
locs = dr.parse_locations(LOCS)
check("real positions parsed; entries with no or impossible location skipped", len(locs) == 2)
check("GeoJSON order: coordinates are [lon, lat]",
      locs[0]["lat"] == 62.580419 and locs[0]["lon"] == 29.77802)
check("key is (number, departureDate) as strings", locs[0]["key"] == ("1", "2026-10-11"))
check("fix time parsed as aware UTC",
      locs[0]["observed_at"] == _dt.datetime(2026, 10, 11, 8, 39, 19, tzinfo=UTC))
check("speed kept in km/h", locs[1]["speed_kmh"] == 122)
check("no bearing is invented (the feed has none)", "course_deg" not in locs[0] and "bearing" not in locs[0])
check("a non-list payload is an empty answer, not a crash", dr.parse_locations({"x": 1}) == [])

# --- who owns what (D1; Lee 2026-10-11 03:38 and 2026-10-14 03:23) --------
check("Long-distance -> Intercity Rail", dr.consumer_of("Long-distance") == dr.INTERCITY)
check("Commuter -> Commuter Rail", dr.consumer_of("Commuter") == dr.COMMUTER)
for cat in ("Cargo", "Locomotive", "Shunting", "On-track machines", "Test drive"):
    check("%s -> Freight Trains" % cat, dr.consumer_of(cat) == dr.FREIGHT)
check("unknown category (train not listed) -> nobody", dr.consumer_of(None) is None)
# Lee 2026-10-14 16:25: MV, empty-stock passenger trains, to Freight Trains
check("MV (Long-distance in Digitraffic) -> Freight Trains", dr.consumer_of("Long-distance", "MV") == dr.FREIGHT)
check("the other long-distance types stay Intercity",
      all(dr.consumer_of("Long-distance", t) == dr.INTERCITY for t in ("IC", "S", "H", "HDM", "PYO")))
check("a type rule never claims an unlisted train", dr.consumer_of(None, "MV") is None)
check("a type that is not a string is ignored", dr.consumer_of("Long-distance", ["MV"]) == dr.INTERCITY)

# --- TrainTable: version merge, prune --------------------------------------
tab = dr.TrainTable()
tab.update([train(1, "Long-distance", "S", version=100)])
tab.update([train(1, "Long-distance", "IC", version=90)])
check("an OLDER version does not roll a train back", tab.get(("1", "2026-10-11"))["trainType"] == "S")
tab.update([train(1, "Long-distance", "IC", version=110)])
check("a newer version replaces it", tab.get(("1", "2026-10-11"))["trainType"] == "IC")
check("table version is the highest seen", tab.version == 110)
tab.update([train(5, "Cargo", "T", date="2026-10-09", version=5)])
tab.prune(_dt.date(2026, 10, 11))
check("prune drops trains older than yesterday", tab.get(("5", "2026-10-09")) is None)
check("prune keeps today's", tab.get(("1", "2026-10-11")) is not None)

# --- next_stop: passing points, commercial stops, rounded delay ------------
rows = [
    row("DEPARTURE", "JNS", "2026-10-11T06:00:00.000Z", "2026-10-11T06:00:44.000Z", 1),
    row("ARRIVAL", "PLT", "2026-10-11T06:00:30.000Z", stop=False, commercial=False),   # passing point
    row("ARRIVAL", "XYZ", "2026-10-11T06:10:00.000Z", commercial=False),                # operational stop
    row("ARRIVAL", "KUO", "2026-10-11T06:30:00.000Z", diff=4, estimate="2026-10-11T06:34:00.000Z"),
]
t1 = train(1, "Long-distance", "S", rows=rows)
check("a passing point is never the next stop", dr.next_stop(t1)[0] == "XYZ")
st, eta, delay = dr.next_stop(t1, commercial_only=True)
check("passengers: the next COMMERCIAL stop", st == "KUO")
check("ETA is the live estimate when there is one", eta == _dt.datetime(2026, 10, 11, 6, 34, tzinfo=UTC))
check("delay is the next stop's estimate", delay == 4)
check("operational stop with no estimate falls back to the last actual delay", dr.next_stop(t1)[2] == 1)
done = train(2, "Cargo", "T", rows=[row("ARRIVAL", "A", "2026-10-11T05:00:00.000Z", "2026-10-11T05:02:00.000Z", 2)])
check("a finished train has no next stop", dr.next_stop(done) is None)
cancelled_row = train(3, "Long-distance", "IC", rows=[row("ARRIVAL", "B", "2026-10-11T07:00:00.000Z", cancelled=True),
                                                      row("ARRIVAL", "C", "2026-10-11T07:30:00.000Z")])
check("a cancelled stop is skipped", dr.next_stop(cancelled_row)[0] == "C")

# --- join / split ------------------------------------------------------------
tab2 = dr.TrainTable()
tab2.update([train(1, "Long-distance", "S"), train(3, "Commuter", "HL", line="R")])
recs = dr.join(locs, tab2)
check("join carries category, type and commuter line",
      recs[1]["category"] == "Commuter" and recs[1]["commuter_line"] == "R")
loc_cargo = dict(locs[0]); loc_cargo["key"] = ("50000", "2026-10-11")
loc_unlisted = dict(locs[0]); loc_unlisted["key"] = ("777", "2026-10-11")
tab2.update([train(50000, "Cargo", "T", operator="ferfi")])
parts = dr.split(dr.join(locs + [loc_cargo, loc_unlisted], tab2))
check("split: one long-distance, one commuter, one freight",
      [len(parts[c]) for c in dr.CONSUMERS] == [1, 1, 1])
check("an unlisted train is drawn by nobody", sum(len(v) for v in parts.values()) == 3)
check("freight keeps its operator", parts[dr.FREIGHT][0]["operator"] == "ferfi")
tab3 = dr.TrainTable()
tab3.update([train(10395, "Long-distance", "MV")])
loc_mv = dict(locs[0]); loc_mv["key"] = ("10395", "2026-10-11")
check("join routes an MV train to Freight Trains", dr.join([loc_mv], tab3)[0]["consumer"] == dr.FREIGHT)

# --- transport: headers, gzip, errors (05_SHARED_LESSONS.md C5) ------------
class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


SENT = []


def opener_for(answers):
    def opener(req, timeout=None):
        SENT.append(req)
        path = req.full_url.split("/api/v1/", 1)[1]
        body = answers(path)
        if isinstance(body, Exception):
            raise body
        return Resp(gzip.compress(json.dumps(body).encode()))
    return opener


dr._request("train-locations/latest", "SAW-test/1.0", 5, opener_for(lambda p: LOCS))
hdr = {k.lower(): v for k, v in SENT[-1].header_items()}
check("Accept-Encoding: gzip is sent (406 without it)", hdr.get("accept-encoding") == "gzip")
check("Digitraffic-User is sent", hdr.get("digitraffic-user") == "SAW-test/1.0")
check("a gzipped body is decoded", len(dr._request("x", "u", 5, opener_for(lambda p: LOCS))) == 5)


def http_error(code, body):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body))


try:
    dr._request("x", "u", 5, opener_for(lambda p: http_error(406, b"Use of gzip compression is required")))
    check("406 raises", False)
except dr.FeedUnavailable as e:
    check("406 raises FeedUnavailable carrying the server's BODY", "gzip compression is required" in str(e))
try:
    dr._request("x", "u", 5, opener_for(lambda p: http_error(429, b"")))
    check("429 raises", False)
except dr.RateLimited:
    check("429 raises RateLimited, not 'no trains'", True)
try:
    dr.DigitrafficRailClient(user=" ")
    check("blank user refused", False)
except ValueError:
    check("a blank Digitraffic-User is refused", True)

# --- the client: first load by date, then ?version=, and TTLs --------------
NOW = [_dt.datetime(2026, 10, 11, 22, 30, tzinfo=UTC)]
CALLS = []


def answers(path):
    CALLS.append(path)
    if path.startswith("trains/"):
        d = path.split("/")[1]
        return [train(1, "Long-distance", "S", date=d, version=200)] if d == "2026-10-11" else []
    if path.startswith("trains?version="):
        return [train(50000, "Cargo", "T", version=201)]
    if path == "train-locations/latest":
        return LOCS
    return []


cl = dr.DigitrafficRailClient(user="SAW-test/1.0", opener=opener_for(answers), clock=lambda: NOW[0])
snap = cl.snapshot()
check("first load asks for yesterday, today and tomorrow by date",
      [p for p in CALLS if p.startswith("trains/")] == ["trains/2026-10-10", "trains/2026-10-11", "trains/2026-10-12"])
check("first snapshot routes the S train to Intercity", len(snap[dr.INTERCITY]) == 1)
n = len(CALLS)
cl.snapshot(); cl.for_consumer(dr.COMMUTER); cl.for_consumer(dr.FREIGHT)
check("three consumers inside the TTL cost NO further requests", len(CALLS) == n)
NOW[0] += _dt.timedelta(seconds=61)
cl.snapshot()
check("after the TTL the table is asked incrementally with the version",
      "trains?version=200" in CALLS)
check("positions re-fetched after their TTL", CALLS.count("train-locations/latest") == 2)
try:
    cl.for_consumer("ships")
    check("unknown consumer refused", False)
except ValueError:
    check("an unknown consumer name is refused", True)

# --- 0.2.7: each consumer registers what it draws ----------------------------
# Measured 2026-10-14 (bin\vr_mem.py): a client keeping every category held
# about 138 MB. Now it keeps only the registered consumers' trains.
NOW2 = [_dt.datetime(2026, 10, 11, 22, 30, tzinfo=UTC)]
CALLS2 = []


def answers2(path):
    CALLS2.append(path)
    if path.startswith("trains/"):
        d = path.split("/")[1]
        if d != "2026-10-11":
            return []
        return [train(1, "Long-distance", "S", version=200), train(3, "Commuter", "HL", line="R", version=200),
                train(50000, "Cargo", "T", version=200), train(10395, "Long-distance", "MV", version=200),
                train(12980, "Long-distance", "V", version=200)]
    if path.startswith("trains?version="):
        # Train 1 becomes a cargo run (a category change); a new cargo train.
        return [train(1, "Cargo", "T", version=205), train(50001, "Cargo", "T", version=206)]
    if path == "train-locations/latest":
        return LOCS
    return []


cl2 = dr.DigitrafficRailClient(user="u", opener=opener_for(answers2), clock=lambda: NOW2[0],
                               consumers=(dr.INTERCITY,))
check("a client registered for one consumer says so", cl2.consumers == frozenset({dr.INTERCITY}))
cl2.snapshot()
keys = set(cl2.table.trains)
check("only the registered consumer's trains are kept", keys == {("1", "2026-10-11")})
check("V (varikkojuna, depot train; Lee 2026-10-14) is not kept for intercity", ("12980", "2026-10-11") not in keys)
check("MV is not kept for intercity", ("10395", "2026-10-11") not in keys)
try:
    cl2.for_consumer(dr.FREIGHT)
    check("an unregistered consumer is refused", False)
except ValueError:
    check("for_consumer refuses a consumer not registered", True)
check("register returns True for a newcomer", cl2.register(dr.FREIGHT) is True)
check("register is idempotent", cl2.register(dr.FREIGHT) is False)
n = len(CALLS2)
cl2.snapshot()
check("a consumer joining a loaded client reloads the day lists once",
      [p for p in CALLS2[n:] if p.startswith("trains/")] == ["trains/2026-10-10", "trains/2026-10-11", "trains/2026-10-12"])
check("after the reload the newcomer's trains are there, V and MV with them",
      {("50000", "2026-10-11"), ("10395", "2026-10-11"), ("12980", "2026-10-11")} <= set(cl2.table.trains))
check("the commuter train is still not kept", ("3", "2026-10-11") not in cl2.table.trains)
n = len(CALLS2)
cl2.snapshot()
check("the reload happens once, then the TTL holds", len(CALLS2) == n)
check("unregister returns True", cl2.unregister(dr.FREIGHT) is True)
cl2.snapshot()
check("a consumer leaving takes its trains at the next refresh, without a request",
      set(cl2.table.trains) == {("1", "2026-10-11")} and len(CALLS2) == n)
check("unregister is idempotent", cl2.unregister(dr.FREIGHT) is False)
NOW2[0] += _dt.timedelta(seconds=61)
cl2.snapshot()
check("a train whose category changes to one nobody wants is dropped", ("1", "2026-10-11") not in cl2.table.trains)
check("an unwanted train still advances the version, so ?version= moves on", cl2.table.version == 206)
recs2 = dr.join(dr.parse_locations(LOCS), cl2.table)
check("join carries each record's train", all("train" in r for r in recs2))
tabj = dr.TrainTable(); tabj.update([train(1, "Long-distance", "S")])
check("the record's train is the table's own", dr.join(locs, tabj)[0]["train"] is tabj.get(("1", "2026-10-11")))
for bad in ("ships", None):
    try:
        cl2.register(bad)
        check("register refuses %r" % (bad,), False)
    except ValueError:
        check("register refuses an unknown consumer %r" % (bad,), True)
try:
    dr.DigitrafficRailClient(user="u", consumers=("ships",))
    check("unknown consumer at construction refused", False)
except ValueError:
    check("an unknown consumer at construction is refused", True)
check("a standalone client keeps every consumer by default",
      dr.DigitrafficRailClient(user="u").consumers == frozenset(dr.CONSUMERS))

# --- 0.2.7: the guard. One fetch at a time; register never waits on it ------
import threading  # noqa: E402
import time as _time  # noqa: E402

GATE = threading.Event()
CALLS3 = []


def slow(path):
    CALLS3.append(path)
    GATE.wait(5)
    return answers2(path) if not path.startswith("trains?") else []


cl3 = dr.DigitrafficRailClient(user="u", opener=opener_for(slow), clock=lambda: NOW2[0],
                               consumers=(dr.INTERCITY, dr.COMMUTER))
out3 = {}
t_a = threading.Thread(target=lambda: out3.setdefault("a", cl3.for_consumer(dr.INTERCITY)))
t_b = threading.Thread(target=lambda: out3.setdefault("b", cl3.for_consumer(dr.COMMUTER)))
t_a.start(); _time.sleep(0.1); t_b.start(); _time.sleep(0.1)
t0 = _time.monotonic()
cl3.register(dr.FREIGHT)
cl3.unregister(dr.FREIGHT)
waited = _time.monotonic() - t0
GATE.set()
t_a.join(10); t_b.join(10)
check("register and unregister do not wait for a fetch in progress (event loop)", waited < 0.05)
check("two consumers asking at once cost ONE set of requests (3 dates + positions)",
      sorted(CALLS3) == sorted(["trains/2026-10-10", "trains/2026-10-11", "trains/2026-10-12", "train-locations/latest"]))
check("both got their trains", len(out3.get("a", [])) == 1 and len(out3.get("b", [])) == 1)

GATE4 = threading.Event()
CALLS4 = []


def slow4(path):
    CALLS4.append(path)
    if path == "trains/2026-10-10":
        GATE4.wait(5)
    return answers2(path) if not path.startswith("trains?") else []


cl4 = dr.DigitrafficRailClient(user="u", opener=opener_for(slow4), clock=lambda: NOW2[0],
                               consumers=(dr.INTERCITY,))
t4 = threading.Thread(target=cl4.snapshot)
t4.start(); _time.sleep(0.1)
cl4.register(dr.COMMUTER)          # during the FIRST load
GATE4.set(); t4.join(10)
check("a consumer registered during the first load is not in it", ("3", "2026-10-11") not in cl4.table.trains)
n4 = len(CALLS4)
cl4.snapshot()
check("...and the next refresh loads it, inside the TTL",
      ("3", "2026-10-11") in cl4.table.trains
      and [p for p in CALLS4[n4:] if p.startswith("trains/")] == ["trains/2026-10-10", "trains/2026-10-11", "trains/2026-10-12"])

check("V -> Freight Trains (Lee 2026-10-14 21:55)", dr.consumer_of("Long-distance", "V") == dr.FREIGHT)

# --- shared registration (B8.1a, B16), 0.2.7 --------------------------------
class FakeHass:
    def __init__(self):
        self.data = {}


h = FakeHass()
a = sdr.get_shared_rail_client(h, dr.INTERCITY)
check("the first integration creates the client with ITS consumer only", a.consumers == frozenset({dr.INTERCITY}))
check("with the client's own Digitraffic-User (Lee 2026-10-14 21:10)", a.user == dr.DEFAULT_USER)
b = sdr.get_shared_rail_client(h, dr.COMMUTER)
c = sdr.get_shared_rail_client(h, dr.COMMUTER)
check("three claims, ONE rail client", a is b is c)
check("the second integration snaps in with what it draws",
      a.consumers == frozenset({dr.INTERCITY, dr.COMMUTER}))
check("refcount 3, counted per consumer",
      h.data[dr.SHARED_KEY]["refcount"] == 3 and h.data[dr.SHARED_KEY]["consumers"] == {dr.INTERCITY: 1, dr.COMMUTER: 2})
check("the shared key is SAW-neutral and versioned (contract 2 in 0.2.7)", dr.SHARED_KEY == "saw_digitraffic_rail_v2")
sdr.release_shared_rail_client(h, dr.COMMUTER)
check("one of two commuter claims released: commuter stays", dr.COMMUTER in a.consumers)
sdr.release_shared_rail_client(h, dr.FREIGHT)
check("releasing a consumer never claimed changes nothing", h.data[dr.SHARED_KEY]["refcount"] == 2)
sdr.release_shared_rail_client(h, dr.COMMUTER)
check("its last claim unregisters it", a.consumers == frozenset({dr.INTERCITY}))
sdr.release_shared_rail_client(h, dr.INTERCITY)
check("entry removed with the last release", dr.SHARED_KEY not in h.data)
try:
    sdr.get_shared_rail_client(FakeHass(), "ships")
    check("unknown consumer refused by the reference", False)
except ValueError:
    check("an unknown consumer is refused before anything is stored", True)
h2 = FakeHass()
foreign = {"contract_version": 99, "entry_shape": 2, "client": dr.DigitrafficRailClient(user="u"), "refcount": 1,
           "consumers": {}}
h2.data[dr.SHARED_KEY] = foreign
priv = sdr.get_shared_rail_client(h2, dr.INTERCITY)
check("a foreign contract version -> a PRIVATE client, its own consumer only",
      priv is not foreign["client"] and priv.consumers == frozenset({dr.INTERCITY}))
check("the foreign entry is not overwritten", h2.data[dr.SHARED_KEY] is foreign)
old_shape = {"contract_version": dr.CONTRACT_VERSION, "entry_shape": 1, "client": dr.DigitrafficRailClient(user="u"),
             "refcount": 1}
h4 = FakeHass(); h4.data[dr.SHARED_KEY] = old_shape
check("an entry of the old shape -> a private client, entry untouched",
      sdr.get_shared_rail_client(h4, dr.INTERCITY) is not old_shape["client"] and h4.data[dr.SHARED_KEY] is old_shape)
shape1 = {"contract_version": dr.CONTRACT_VERSION, "entry_shape": 1, "client": dr.DigitrafficRailClient(user="u"),
          "refcount": 1, "consumers": {dr.INTERCITY: 1}}
h6 = FakeHass(); h6.data[dr.SHARED_KEY] = shape1
check("an entry of shape 1 WITH consumer counts is still foreign",
      sdr.get_shared_rail_client(h6, dr.INTERCITY) is not shape1["client"] and shape1["refcount"] == 1)
no_counts = {"contract_version": dr.CONTRACT_VERSION, "entry_shape": 2, "client": dr.DigitrafficRailClient(user="u"),
             "refcount": 1}
h5 = FakeHass(); h5.data[dr.SHARED_KEY] = no_counts
check("an entry with no consumer counts -> a private client, entry untouched",
      sdr.get_shared_rail_client(h5, dr.INTERCITY) is not no_counts["client"] and h5.data[dr.SHARED_KEY] is no_counts)
h3 = FakeHass(); h3.data[dr.SHARED_KEY] = "junk"
check("garbage at the key -> private client, garbage untouched",
      isinstance(sdr.get_shared_rail_client(h3, dr.INTERCITY), dr.DigitrafficRailClient) and h3.data[dr.SHARED_KEY] == "junk")

failed = [n for n, ok in CHECKS if not ok]
print("checks: %d   passed: %d   failed: %d" % (len(CHECKS), len(CHECKS) - len(failed), len(failed)))
for n in failed:
    print("  FAIL: %s" % n)
sys.exit(1 if failed else 0)
