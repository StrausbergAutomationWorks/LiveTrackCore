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

# --- shared registration (B8.1a, B16) ----------------------------------------
class FakeHass:
    def __init__(self):
        self.data = {}


h = FakeHass()
a, b, c = (sdr.get_shared_rail_client(h, "u") for _ in range(3))
check("three integrations receive ONE rail client", a is b is c)
check("refcount 3", h.data[dr.SHARED_KEY]["refcount"] == 3)
check("the shared key is SAW-neutral and versioned", dr.SHARED_KEY == "saw_digitraffic_rail_v1")
for _ in range(3):
    sdr.release_shared_rail_client(h)
check("entry removed with the last release", dr.SHARED_KEY not in h.data)
h2 = FakeHass()
foreign = {"contract_version": 99, "entry_shape": 1, "client": dr.DigitrafficRailClient(user="u"), "refcount": 1}
h2.data[dr.SHARED_KEY] = foreign
check("a foreign contract version -> a PRIVATE client", sdr.get_shared_rail_client(h2, "u") is not foreign["client"])
check("the foreign entry is not overwritten", h2.data[dr.SHARED_KEY] is foreign)
h3 = FakeHass(); h3.data[dr.SHARED_KEY] = "junk"
check("garbage at the key -> private client, garbage untouched",
      isinstance(sdr.get_shared_rail_client(h3, "u"), dr.DigitrafficRailClient) and h3.data[dr.SHARED_KEY] == "junk")

failed = [n for n, ok in CHECKS if not ok]
print("checks: %d   passed: %d   failed: %d" % (len(CHECKS), len(CHECKS) - len(failed), len(failed)))
for n in failed:
    print("  FAIL: %s" % n)
sys.exit(1 if failed else 0)
