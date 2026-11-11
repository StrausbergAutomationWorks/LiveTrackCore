"""Tests for saw_livetrack.ns and its shared registration (the Netherlands).

Every case is a MEASURED finding (30_Integrations/Live_Track_Intercity_Rail/20_Research/_shared/netherlands/
NS_VIRTUAL_TRAIN_20261110.md, section 4) or a decision of Lee's (the same note, 4.6 and 4.8). The fixtures are one NS
vehicle read of 2026-11-11 09:30Z, NS's SpoorKaart and OVapi's timetable of that morning (cut to the trains in the
read) and OVapi's train updates of 09:05Z (cut likewise). The network is a fake.
Run:  python tests/test_ns.py  -> exit 0
"""
import gzip
import io
import json
import os
import sys
import tempfile
import time
import urllib.error
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "reference"))

from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import saw_livetrack._keyed as keyed  # noqa: E402
import saw_livetrack.ns as ns  # noqa: E402
import shared_ns as sns  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
T0 = 1794389454.0
CHECKS = []
KEY_A, KEY_B = "key-AAAA-1234", "key-BBBB-5678"


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


def gz(name):
    with gzip.open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


VEHICLE = gz("ns_vehicle_20261111T0930Z.json.gz")
SPOOR = json.loads(gz("ns_spoorkaart_20261111.json.gz"))
CUT = json.loads(gz("ns_timetable_cut_20261111.json.gz"))
UPDATES = gz("ns_trainupdates_cut_20261111T0905Z.pb.gz")


# --- the fake network -------------------------------------------------------------------------------------------

class Resp:
    def __init__(self, body, headers=None):
        self.body, self.headers, self.status, self.pos = body, headers or {}, 200, 0

    def read(self, n=None):
        n = len(self.body) - self.pos if n is None else n
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
    """URL prefix -> body, Resp or an exception; records every request's url and headers."""

    def __init__(self, routes):
        self.routes, self.calls = dict(routes), []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append((url, dict(req.header_items())))
        for prefix, what in self.routes.items():
            if url.startswith(prefix):
                what = what(req) if callable(what) and not isinstance(what, type) else what
                if isinstance(what, Exception):
                    raise what
                if isinstance(what, Resp):
                    what.pos = 0
                    return what
                return Resp(what)
        raise http_error(url, 404)

    def count(self, prefix):
        return sum(1 for u, _ in self.calls if u.startswith(prefix))


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def vehicle_body(rows):
    return json.dumps({"payload": {"treinen": rows}}).encode()


# --- parse_vehicles ---------------------------------------------------------------------------------------------

rows = ns.parse_vehicles(VEHICLE)
types = {r["type"] for r in rows}
check("vehicle: every record of the 09:30Z read has a number, a position and one of IC/SPR/ARR",
      len(rows) == 302 and types <= {"IC", "SPR", "ARR"})
check("vehicle: speed in km/h and heading in degrees pass through",
      all(r["speed_kmh"] is not None and 0 <= r["heading"] < 360 for r in rows))
odd = ns.parse_vehicles(vehicle_body([
    {"treinNummer": 1, "lat": 52.0, "lng": 5.0, "type": "ic", "richting": 370, "snelheid": 10},
    {"treinNummer": 1, "lat": 52.1, "lng": 5.1, "type": "IC"},
    {"treinNummer": 2, "lat": 0, "lng": 0, "type": "SPR"},
    {"lat": 52.0, "lng": 5.0, "type": "SPR"},
    {"ritId": "3", "lat": "52.2", "lng": "5.2", "type": "ARR", "horizontaleNauwkeurigheid": 2333}]))
check("vehicle: a number twice keeps the first; 0,0 and no number are left out; ritId stands in",
      [r["number"] for r in odd] == ["1", "3"] and odd[0]["type"] == "IC" and odd[0]["heading"] == 10)
check("vehicle: horizontaleNauwkeurigheid is never carried (NS note 4.5: not metres of error)",
      all("horizontaleNauwkeurigheid" not in r and "accuracy" not in str(r) for r in odd))
check("vehicle: a body that is not the answer raises FeedUnavailable",
      raises(ns.FeedUnavailable, lambda: ns.parse_vehicles(b"<html>")) and
      raises(ns.FeedUnavailable, lambda: ns.parse_vehicles(b'{"payload": {}}')))

# --- SpoorKaart --------------------------------------------------------------------------------------------------

geo = {"payload": {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"from": "ut", "to": "ac"},
     "geometry": {"type": "LineString", "coordinates": [[5.11, 52.09], [5.0, 52.2], [4.98, 52.28]]}},
    {"type": "Feature", "properties": {"from": "ac", "to": "ut"},
     "geometry": {"type": "LineString", "coordinates": [[4.98, 52.28], [5.11, 52.09]]}},
    {"type": "Feature", "properties": {"from": "ac", "to": "bkl"},
     "geometry": {"type": "LineString", "coordinates": [[4.98, 52.28], [4.99, 52.17]]}},
    {"type": "Feature", "properties": {"from": "x", "to": "x"},
     "geometry": {"type": "LineString", "coordinates": [[1, 1], [2, 2]]}}]}}
sp = ns.parse_spoorkaart(json.dumps(geo).encode())
check("spoorkaart: codes upper case; a station's place is the end of its lines",
      sp["stations"]["UT"] == [52.09, 5.11] and sp["stations"]["AC"] == [52.28, 4.98])
check("spoorkaart: a pair drawn twice keeps the shorter; a line from a station to itself is dropped",
      set(sp["pairs"]) == {"AC|UT", "AC|BKL"} and len(sp["pairs"]["AC|UT"][1]) == 4)
enc = sp["pairs"]["AC|UT"][1]
check("spoorkaart: points run from the lower code to the higher, 1e-5 degrees, first absolute then differences",
      enc[:2] == [5228000, 498000] and enc[2] == 5209000 - 5228000)
check("spoorkaart: no lines raises", raises(ns.FeedUnavailable, lambda: ns.parse_spoorkaart(b'{"payload": {"features": []}}')))
rev = ns.parse_spoorkaart({"payload": {"features": [
    {"properties": {"from": "zz", "to": "aa"}, "geometry": {"coordinates": [[6.0, 53.0], [5.5, 52.5], [5.0, 52.0]]}},
    {"properties": {"from": "aa", "to": "zz"}, "geometry": {"coordinates": [[5.0, 52.0], [7.0, 54.0], [6.0, 53.0]]}}]}})
check("spoorkaart: a line drawn from the higher code is stored from the lower; the shorter of two kept though first",
      rev["pairs"]["AA|ZZ"][1][:2] == [5200000, 500000] and len(rev["pairs"]["AA|ZZ"][1]) == 6
      and rev["pairs"]["AA|ZZ"][1][2:4] == [50000, 50000])
real = ns.Graph(SPOOR)
check("spoorkaart fixture: 759 lines measured, every one between two stations",
      len(SPOOR["pairs"]) >= 700 and all("|" in k for k in SPOOR["pairs"]))
check("graph: Utrecht to Amsterdam Centraal runs through Amsterdam Amstel, not across country",
      (lambda p: p is not None and p[0] == "UT" and p[-1] == "ASD" and "ASA" in p)(real.path("UT", "ASD")))
check("graph: a path is not believed past PATH_FACTOR times the straight line",
      ns.Graph({"stations": {"A": [52, 5], "B": [52, 5.01], "C": [53, 5]},
                "pairs": {"A|C": [120000, []], "B|C": [120000, []]}}).path("A", "B") is None)
check("graph: nearest station within 800 m, else None",
      real.nearest(52.0894, 5.1101) == "UT" and real.nearest(52.0, 3.0) is None)
check("graph: route joins consecutive stops through the stations between",
      real.route(["UT", "ASD"])[0] == "UT" and len(real.route(["UT", "ASD"])) > 3)

# --- distill -----------------------------------------------------------------------------------------------------


def tiny_zip(path):
    files = {
        "agency.txt": "agency_id,agency_name\nNS,NS\nBN,Blauwnet\nQB,Qbuzz bus\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\n"
                      "r1,NS,Intercity,A <-> B,2\nr2,BN,IC23,Zwolle <-> Enschede,2\nr3,QB,5,Bus,3\n",
        "trips.txt": "route_id,service_id,trip_id,trip_headsign,trip_short_name\n"
                     "r1,s1,t1,B,100\nr2,s1,t2,Enschede,7900\nr3,s1,t3,X,5\nr1,s2,t4,B,101\n",
        "calendar_dates.txt": "service_id,date,exception_type\ns1,20261111,1\ns1,20261112,1\ns1,20261112,2\n"
                              "s2,20261111,1\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type\n"
                          "t1,10:00:00,10:00:00,p1,1,0,0\nt1,10:05:00,10:05:00,p9,2,1,1\nt1,24:10:00,24:11:00,p2,3,0,0\n"
                          "\"t3\",10:00:00,10:00:00,b1,1,0,0\n\"t3\",10:05:00,10:05:00,b2,2,0,0\n"
                          "\"t2\",11:00:00,11:00:00,p2,1,0,0\nt2,11:30:00,11:30:00,p1,2,0,0\n"
                          "t4,12:00:00,12:00:00,p1,1,0,0\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon,parent_station\n"
                     "p1,Alpha,52.09,5.11,area:1\np1b,Alpha,52.0901,5.1102,area:1\np2,Beta,52.28,4.98,\n"
                     "p9,Pass,52.2,5.0,\nb1,Bus1,52,5,\nb2,Bus2,52,5,\n",
    }
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)


with tempfile.TemporaryDirectory() as tmp:
    zp = os.path.join(tmp, "g.zip")
    tiny_zip(zp)
    dd = ns.distill(zp, "v1")
check("distill: rail only (route_type 2), trips with fewer than two calls dropped",
      set(dd["trips"]) == {"t1", "t2"} and "r3" not in dd["routes"])
check("distill: a quoted row of a bus trip is not taken for rail", set(dd["times"]) == {"t1", "t2"})
check("distill: a call neither picked up at nor set down at is no call; times past midnight kept",
      dd["times"]["t1"] == [["p1", 36000, 36000], ["p2", 87000, 87060]])
check("distill: a quoted row is read", dd["times"]["t2"][0] == ["p2", 39600, 39600])
check("distill: calendar_dates adds and removes", dd["services"]["s1"] == ["20261111"])
check("distill: a used stop's siblings in its stop area are kept (updates name platforms)",
      "p1b" in dd["stops"] and dd["stops"]["p1"][3] == "area:1")
check("distill: refuses a zip without the files", raises(ns.FeedUnavailable, lambda: ns.distill(
    (lambda p: (zipfile.ZipFile(p, "w").close(), p)[1])(os.path.join(tempfile.gettempdir(), "ns_empty.zip")))))

# --- the timetable -----------------------------------------------------------------------------------------------

tz = ns.zone()
td = ns.TimetableData(CUT, ns.Graph(SPOOR))
m1126 = td.trip_for("1126", T0, tz)
check("timetable: train 1126 at 09:30Z runs a trip of 2026-11-11 (NS note 4.2: trip_short_name = treinNummer)",
      m1126 is not None and m1126[1] == "20261111")
p1126 = td.path(m1126[0])
check("timetable: 1126's path is Eindhoven to Den Haag Centraal through Tilburg, Breda, Rotterdam, Delft",
      p1126[0] == "EHV" and p1126[-1] == "GVC" and all(c in p1126 for c in ("TB", "BD", "RTD", "DT")))
check("timetable: the fix is near its trip's path", td.near_path(m1126[0], 52.0689, 4.3214))
check("timetable: a fix 100 km away is not that trip (NS note 4.2: one IC 94 km from its number's shape)",
      not td.near_path(m1126[0], 53.2, 6.56))
calls = td.calls(m1126[0], m1126[2])
check("timetable: calls carry station codes and names, aimed times as epochs",
      calls[0]["code"] == "EHV" and calls[0]["name"] and calls[0]["aimed_departure"] > 1.79e9)
m17927 = td.trip_for("17927", T0, tz)
check("company: Blauwnet's IC23 is Keolis's (BN agency names the brand; route decides)",
      td.company(m17927[0])[:2] == ("keolis", "Blauwnet"))
m8038 = td.trip_for("8038", T0, tz)
check("company: Blauwnet's RS20 is Arriva's", td.company(m8038[0])[:2] == ("arriva", "Blauwnet"))
check("company: NS and R-net's Sprinter are NS's",
      td.company(m1126[0])[0] == "ns" and td.company(td.trip_for("8726", T0, tz)[0])[0] == "ns")
check("company: the EU mirror's company agencies are kept",
      ns.AGENCIES["IFF:BLAUWNET_K"][0] == "keolis" and ns.AGENCIES["IFF:RRREIS_A"][0] == "arriva")
small = ns.TimetableData(dd)
day = ns.service_base(time.gmtime(0) and __import__("datetime").date(2026, 11, 11), tz)
check("timetable: a trip after midnight is found on the day before",
      small.trip_for("100", day + 24 * 3600 + 5 * 60, tz) is not None
      and small.trip_for("100", day + 24 * 3600 + 5 * 60, tz)[1] == "20261111")
check("timetable: a number not running is None", small.trip_for("101", day + 5 * 86400, tz) is None)
check("timetable: outside its span (30 min before its first departure) it is no run",
      small.trip_for("100", day + 8 * 3600, tz) is None and small.trip_for("100", day + 9 * 3600 + 40 * 60, tz))
check("timetable: version must match", raises(ValueError, lambda: ns.TimetableData({"v": 99})))

# --- updates and the next call ------------------------------------------------------------------------------------

ups = ns.parse_updates(UPDATES)
check("updates: OVapi's trip ids are the timetable's (cut to the fixture's trips)",
      len(ups) > 100 and all(t in CUT["trips"] for t in ups))
fm = pb.FeedMessage()
fm.header.gtfs_realtime_version = "2.0"
e = fm.entity.add(id="1")
e.trip_update.trip.trip_id = "t1"
u = e.trip_update.stop_time_update.add(stop_id="p1b")
u.departure.time = int(day + 36000 + 240)
u = e.trip_update.stop_time_update.add(stop_id="p2")
u.arrival.time = int(day + 87000 + 240)
small_ups = ns.parse_updates(fm.SerializeToString())
c1 = small.calls("t1", day)
ns.apply_updates(c1, small_ups["t1"], small.stop_index("t1"), small.stops)
check("updates: a platform's update joins its call through the stop area",
      c1[0].get("expected_departure") == day + 36000 + 240)
idx, eta, delay = ns.next_call(c1, day + 36300)
check("next call: never the origin; the delay is the next call's own",
      idx == 1 and delay == 240 and eta == day + 87240)
c2 = small.calls("t1", day)
c2[0]["expected_departure"] = day + 36000 + 180
idx, eta, delay = ns.next_call(c2, day + 36300)
check("next call: with no word for it, aimed plus the delay last known", idx == 1 and delay == 180
      and eta == day + 87000 + 180)
check("next call: past the last call, none", ns.next_call(c2, day + 90000)[0] is None)
check("updates: an undecodable body raises", raises(ns.FeedUnavailable, lambda: ns.parse_updates(b"\xff\x00garbage")))

# --- the client --------------------------------------------------------------------------------------------------


def client(net, clock, tmp=None, consumers=None):
    return ns.NsClient(consumers or {"intercity": KEY_A, "commuter": KEY_A}, opener=net, clock=clock,
                       storage_dir=tmp, background=False)


SPOOR_BODY = json.dumps(geo).encode()
with tempfile.TemporaryDirectory() as tmp:
    # Seed the disk as a box that has run before: SpoorKaart and the timetable cut, both fresh.
    sp_real = dict(SPOOR)
    sp_real["fetched_at"] = T0
    with open(os.path.join(tmp, ns.SPOORKAART_NAME), "w", encoding="utf-8") as fh:
        json.dump(sp_real, fh)
    with gzip.open(os.path.join(tmp, ns.TIMETABLE_NAME + ".json.gz"), "wt", encoding="utf-8") as fh:
        json.dump(CUT, fh)
    with open(os.path.join(tmp, ns.TIMETABLE_NAME + ".meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"etag": '"x"', "checked_at": T0, "version": CUT["version"]}, fh)
    # 8038 runs Blauwnet's RS20 (Arriva's by the route); DVS saying "Blauwnet K" must win.
    carrier = json.dumps([{"ritnummer": 17927, "vervoerder": "Blauwnet K"},
                          {"ritnummer": 8038, "vervoerder": "Blauwnet K"}]).encode()
    net = Net({ns.VEHICLE_URL: VEHICLE, ns.CARRIER_URL: carrier, ns.TRAIN_UPDATES_URL: UPDATES})
    clk = Clock(T0)
    c = client(net, clk, tmp)
    snap = c.snapshot()
    ic, spr = snap["intercity"], snap["commuter"]
    check("client: IC and ARR to intercity, SPR to commuter (Lee 03:00)",
          {t["type"] for t in ic} == {"IC", "ARR"} and {t["type"] for t in spr} == {"SPR"})
    check("client: NS's own trains are operator ns", all(t["operator"] == "ns" for t in ic + spr if t["type"] != "ARR"))
    by = {t["number"]: t for t in ic + spr}
    check("client: the company of a regional train from DVS's vervoerder, over the route (8038 Blauwnet K: keolis)",
          by["17927"]["operator"] == "keolis" and by["17927"]["carrier"] == "Blauwnet K"
          and by["8038"]["operator"] == "keolis")
    check("client: one batched DVS read for the regional numbers", net.count(ns.CARRIER_URL) == 1)
    check("client: option B, the first sight is stamped with the poll time, flagged first_seen",
          by["1126"]["observed_at"] == T0 and by["1126"]["observed_at_source"] == "first_seen")
    check("client: a matched train carries its trip, calls and path",
          by["1126"]["trip_id"] and by["1126"]["calls"] and by["1126"]["path"][0] == "EHV")
    check("client: delays from the updates (read on first use, never beside the timetable's download)",
          net.count(ns.TRAIN_UPDATES_URL) == 1 and any(t.get("updated") for t in ic))
    check("client: no timetable or SpoorKaart download when both on disk are fresh",
          net.count(ns.TIMETABLE_URL) == 0 and net.count(ns.SPOORKAART_URL) == 0)
    check("client: the key travels in Ocp-Apim-Subscription-Key",
          any(h.get("Ocp-apim-subscription-key") == KEY_A for u, h in net.calls if u.startswith(ns.VEHICLE_URL)))
    check("client: OVapi is never sent the key",
          all("Ocp-apim-subscription-key" not in h for u, h in net.calls if u.startswith("https://gtfs.ovapi.nl")))
    n_vehicle = net.count(ns.VEHICLE_URL)
    clk.t = T0 + 5
    c.snapshot()
    check("client: within FEED_TTL_S one read serves every consumer", net.count(ns.VEHICLE_URL) == n_vehicle)
    # Moved: one train changes position, another does not.
    rows2 = json.loads(VEHICLE)
    tr = rows2["payload"]["treinen"]
    moved = next(r for r in tr if str(r["treinNummer"]) == "1126")
    moved["lat"] += 0.001
    far = next(r for r in tr if str(r["treinNummer"]) == "1137")
    far["lat"], far["lng"] = 53.2, 6.56
    net.routes[ns.VEHICLE_URL] = json.dumps(rows2).encode()
    clk.t = T0 + 12
    snap2 = c.snapshot()
    by2 = {t["number"]: t for t in snap2["intercity"] + snap2["commuter"]}
    check("client: a CHANGED position is stamped with the poll that first saw it",
          by2["1126"]["observed_at"] == T0 + 12)
    check("client: an unchanged position keeps its first-seen time, never re-stamped",
          by2["8038"]["observed_at"] == T0)
    check("client: a fix far from its number's trip is not that trip (published at its fix, counted)",
          "trip_id" not in by2["1137"] and by2["1137"]["operator"] == "ns" and c.counts.get("trip_far_from_fix"))
    n_up = net.count(ns.TRAIN_UPDATES_URL)
    c._busy.add("timetable")
    c.refresh_updates(force=True)
    c._busy.discard("timetable")
    check("client: the updates are never read beside the timetable's download", net.count(ns.TRAIN_UPDATES_URL) == n_up)
    check("client: for_consumer(operator) filters", {t["operator"] for t in c.for_consumer("intercity", "keolis")} ==
          {"keolis"})
    st = c.stats()
    check("client: stats count, never a key", KEY_A not in json.dumps(st) and st["timetable_trips"] == len(CUT["trips"]))
    check("client: static_version names both the track and the timetable", c.static_version().startswith("ns1.1:"))
    check("client: stations named from the timetable", c.stations()["UT"]["name"].startswith("Utrecht"))
    check("client: pair_lines are SpoorKaart's encoded lines", "ASA|ASD" in c.pair_lines() or len(c.pair_lines()) > 700)

with tempfile.TemporaryDirectory() as tmp:
    # A fresh box: SpoorKaart and the timetable are fetched; the timetable conditional the second time.
    zbuf = io.BytesIO()
    zp = os.path.join(tmp, "src.zip")
    tiny_zip(zp)
    zbody = open(zp, "rb").read()
    state = {"cond": None}

    def tt_answer(req):
        state["cond"] = req.get_header("If-none-match")
        if state["cond"]:
            return http_error(req.full_url, 304)
        return Resp(zbody, {"ETag": '"e1"', "Last-Modified": "Wed, 11 Nov 2026 03:38:51 GMT"})
    net = Net({ns.VEHICLE_URL: VEHICLE, ns.SPOORKAART_URL: SPOOR_BODY, ns.TIMETABLE_URL: tt_answer,
               ns.TRAIN_UPDATES_URL: UPDATES, ns.CARRIER_URL: b"[]"})
    clk = Clock(T0)
    c = client(net, clk, tmp)
    c.snapshot()
    check("fresh box: SpoorKaart fetched with the key and kept on disk",
          net.count(ns.SPOORKAART_URL) == 1 and os.path.exists(os.path.join(tmp, ns.SPOORKAART_NAME)))
    check("fresh box: the timetable downloaded, distilled to disk, the zip deleted",
          net.count(ns.TIMETABLE_URL) == 1 and os.path.exists(os.path.join(tmp, ns.TIMETABLE_NAME + ".json.gz"))
          and not os.path.exists(os.path.join(tmp, ns.TIMETABLE_NAME + ".zip.tmp")))
    check("fresh box: the updates wait while the timetable downloads (one server, one budget)",
          net.count(ns.TRAIN_UPDATES_URL) == 0)
    clk.t = T0 + ns.TIMETABLE_TTL_S + 1
    c.refresh_timetable()
    check("timetable: checked again after a week, conditionally; 304 keeps the copy",
          state["cond"] == '"e1"' and c._tt is not None and net.count(ns.TIMETABLE_URL) == 2)
    net.routes[ns.TRAIN_UPDATES_URL] = http_error(ns.TRAIN_UPDATES_URL, 429)
    clk.t += 400
    c.refresh_updates(force=True)
    calls_before = net.count(ns.TRAIN_UPDATES_URL)
    clk.t += 60
    c.refresh_updates(force=True)
    check("updates: HTTP 429 backs OVapi off (BACKOFF_S)", net.count(ns.TRAIN_UPDATES_URL) == calls_before)
    check("updates: the backoff also holds the timetable (one server)",
          raises(keyed.RateLimited, lambda: c._ov_backoff.check(ns.TIMETABLE_URL)))

# Keys: refused for REFUSED_S -> AuthRefused; a 429 on NS backs off NS only.
net = Net({ns.VEHICLE_URL: http_error(ns.VEHICLE_URL, 401)})
clk = Clock(T0)
c = client(net, clk, None, {"intercity": KEY_A})
check("keys: one 401 is not a verdict", raises(keyed.KeyRefused, lambda: c.snapshot(force=True)))
clk.t += keyed.REFUSED_S + 1
err = None
try:
    c.snapshot(force=True)
except Exception as exc:  # noqa: BLE001
    err = exc
check("keys: refused for REFUSED_S with no success: AuthRefused", isinstance(err, keyed.AuthRefused))
check("keys: never in an error message", KEY_A not in str(err))
net = Net({ns.VEHICLE_URL: http_error(ns.VEHICLE_URL, 429)})
c = client(net, Clock(T0), None, {"intercity": KEY_A})
check("rate: HTTP 429 raises RateLimited", raises(keyed.RateLimited, lambda: c.snapshot(force=True)))
check("rate: and the next poll does not ask", raises(keyed.RateLimited, lambda: c.snapshot(force=True))
      and net.count(ns.VEHICLE_URL) == 1)
check("check_key: 401 is invalid_key, 500 cannot_connect, a good answer None",
      ns.check_key("k", opener=Net({ns.VEHICLE_URL: http_error(ns.VEHICLE_URL, 401)})) == "invalid_key"
      and ns.check_key("k", opener=Net({ns.VEHICLE_URL: http_error(ns.VEHICLE_URL, 500)})) == "cannot_connect"
      and ns.check_key("k", opener=Net({ns.VEHICLE_URL: VEHICLE})) is None)
check("client: an unknown consumer is refused", raises(ValueError, lambda: ns.NsClient({"transit": KEY_A})))


# --- the shared registration ---------------------------------------------------------------------------------------

class Hass:
    def __init__(self):
        self.data = {}
        self.config = self

    def path(self, *p):
        return os.path.join(tempfile.gettempdir(), *p)


h = Hass()
a = sns.get_shared_ns_client(h, "intercity", KEY_A)
b = sns.get_shared_ns_client(h, "intercity", KEY_A)
cc = sns.get_shared_ns_client(h, "commuter", KEY_B)
check("shared: one client for every entry and consumer", a is b is cc and h.data[ns.SHARED_KEY]["refcount"] == 3)
check("shared: both consumers registered", a.consumers == {"intercity", "commuter"})
sns.release_shared_ns_client(h, "intercity")
check("shared: a consumer stays while it has a claim", "intercity" in a.consumers)
sns.release_shared_ns_client(h, "intercity")
check("shared: its last claim unregisters it", a.consumers == {"commuter"})
sns.release_shared_ns_client(h, "commuter")
check("shared: the last claim of all removes the entry", ns.SHARED_KEY not in h.data)
h.data[ns.SHARED_KEY] = {"contract_version": 99}
p = sns.get_shared_ns_client(h, "commuter", KEY_A)
check("shared: a foreign entry is left alone; a private client is used",
      isinstance(p, ns.NsClient) and h.data[ns.SHARED_KEY] == {"contract_version": 99})
other = ns.NsClient({"intercity": KEY_A})
h.data[ns.SHARED_KEY] = {"contract_version": 99, "entry_shape": 1, "client": other, "refcount": 1,
                         "consumers": {"intercity": 1}}
p = sns.get_shared_ns_client(h, "commuter", KEY_A)
check("shared: an entry of another contract version is not used, even when shaped like ours",
      p is not other and h.data[ns.SHARED_KEY]["refcount"] == 1)

failed = [n for n, ok in CHECKS if not ok]
for n, ok in CHECKS:
    print(("ok   " if ok else "FAIL ") + n)
print("%d checks, %d failed" % (len(CHECKS), len(failed)))
sys.exit(1 if failed else 0)
