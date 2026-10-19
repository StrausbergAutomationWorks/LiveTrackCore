"""Tests for saw_livetrack.renfe and its shared registration.

Every case is a MEASURED finding (capture round 1, 2026-10-16..19, Commuter Rail
20_Research/ES_RENFE_CERCANIAS_FEED_20261016.md) or a decision of Lee's.
Run:  python tests/test_renfe.py  -> exit 0
"""
import datetime as _dt
import io
import os
import sys
import urllib.error
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "reference"))

from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import saw_livetrack.renfe as rf  # noqa: E402
import shared_renfe as srf  # noqa: E402

CHECKS = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


# --- trip ids, as seen live -------------------------------------------------
check("trip id: network, train, line", rf.parse_trip_id("3087V23743C5") == ("30", "23743", "C5"))
check("trip id: lower-case line letter", rf.parse_trip_id("1089D27405C4b") == ("10", "27405", "C4b"))
check("trip id: two-letter line", rf.parse_trip_id("5190L28560R2N") == ("51", "28560", "R2N"))
check("trip id: padded", rf.parse_trip_id(" 3087V23743C5  ") == ("30", "23743", "C5"))
check("trip id: SPECIAL has no network", rf.parse_trip_id("SPECIAL_50_95224R4") == (None, "95224", "R4"))
check("trip id: junk", rf.parse_trip_id("VP_C1-26912") == (None, None, None))
check("trip id: None", rf.parse_trip_id(None) == (None, None, None))
check("trip id: not a string", rf.parse_trip_id(3087) == (None, None, None))

# --- who draws what -----------------------------------------------------------
check("bus by route_type 3", rf.consumer_of("3", "41", "C2") == rf.BUS)
check("rail commuter", rf.consumer_of("2", "10", "C5") == rf.COMMUTER)
check("Catalan regional R11", rf.consumer_of("2", "51", "R11") == rf.INTERCITY)
check("Catalan regional R17", rf.consumer_of("2", "51", "R17") == rf.INTERCITY)
check("Catalan regional R12 (not seen live)", rf.consumer_of("2", "51", "R12") == rf.INTERCITY)
check("Rodalies R4 is commuter", rf.consumer_of("2", "51", "R4") == rf.COMMUTER)
check("Rodalies RG1 is commuter", rf.consumer_of("2", "51", "RG1") == rf.COMMUTER)
check("Santander R3 is commuter", rf.consumer_of("2", "62", "R3") == rf.COMMUTER)
check("R11 outside Catalonia is commuter", rf.consumer_of("2", "10", "R11") == rf.COMMUTER)
check("SPECIAL (not in timetable) R4: rail, commuter", rf.consumer_of(None, None, "R4") == rf.COMMUTER)
check("SPECIAL R13: intercity", rf.consumer_of(None, None, "R13") == rf.INTERCITY)
check("a bus not in timetable cannot be known: rail", rf.consumer_of(None, "41", "C2") == rf.COMMUTER)
check("route_type 0 to nobody", rf.consumer_of("0", "10", "C1") is None)
check("route_type empty to nobody", rf.consumer_of("", "10", "C1") is None)
check("networks named", rf.NETWORKS["51"].startswith("Barcelona") and rf.NETWORKS["10"] == "Madrid")
check("consumers", rf.CONSUMERS == ("commuter", "intercity", "bus"))
check("shared key", rf.SHARED_KEY == "saw_renfe_v1")

# --- the timetable --------------------------------------------------------------
TODAY = _dt.date(2026, 10, 19)   # a Monday


def static_zip(extra_dates=b""):
    files = {
        "agency.txt": b"agency_id,agency_name,agency_url,agency_timezone\n1071VC,Renfe Cercanias,https://x,Europe/Madrid\n",
        # padded, as Renfe writes it
        "routes.txt": (b"route_id,route_short_name,route_long_name,route_type,route_color,route_text_color\n"
                       b"10T0001C5  ,C5  ,Mostoles - Humanes   ,2,FECB00,FFFFFF\n"
                       b"41T0025C2  ,C2  ,Aguilas - Lorca      ,3,E93CAC,FFFFFF\n"
                       b"51T0001R11 ,R11 ,Figueres - Sants     ,2,0069AA,FFFFFF\n"),
        "calendar.txt": (b"service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                         b"MON,1,0,0,0,0,0,0,20261019,20261019\n"
                         b"SUN,0,0,0,0,0,0,1,20261018,20261018\n"
                         b"THU,0,0,0,1,0,0,0,20261022,20261022\n"
                         b"OFF,1,0,0,0,0,0,0,20261019,20261019\n"),
        "calendar_dates.txt": b"service_id,date,exception_type\nOFF,20261019,2\nADD,20261020,1\n" + extra_dates,
        "trips.txt": (b"route_id,service_id,trip_id\n"
                      b"10T0001C5  ,MON ,1090L23743C5  \n"
                      b"41T0025C2  ,MON ,4190L84931C2  \n"
                      b"51T0001R11 ,SUN ,5189D77001R11 \n"
                      b"10T0001C5  ,THU ,1093J23743C5  \n"
                      b"10T0001C5  ,OFF ,1090L99999C5  \n"
                      b"10T0001C5  ,ADD ,1091M23744C5  \n"),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


idx = rf.TripIndex.from_zip(static_zip(), today=TODAY)
check("index: today's trip, padded id stripped", idx.route_type("1090L23743C5") == "2")
check("index: lookup strips the asked id", idx.route_type(" 1090L23743C5 ") == "2")
check("index: the bus", idx.route_type("4190L84931C2") == "3")
check("index: yesterday kept", idx.route_type("5189D77001R11") == "2")
check("index: three days out dropped", idx.route_type("1093J23743C5") is None)
check("index: removed by calendar_dates", idx.route_type("1090L99999C5") is None)
check("index: added by calendar_dates (tomorrow)", idx.route_type("1091M23744C5") == "2")
check("index: size", len(idx) == 4)
check("index: day", idx.day == TODAY)

# --- the client, over a fake network -------------------------------------------------
MADRID_A = (40.481907, -3.616286)
MADRID_B = (40.492401, -3.593483)     # 2.2 km away: the C1 19830 return, measured


def vp_feed(rows, ts=1792310000):
    fm = pb.FeedMessage()
    fm.header.gtfs_realtime_version = "2.0"
    fm.header.timestamp = ts
    for i, (eid, trip, lat, lon, vts) in enumerate(rows):
        e = fm.entity.add()
        e.id = eid
        e.vehicle.trip.trip_id = trip
        e.vehicle.position.latitude = lat
        e.vehicle.position.longitude = lon
        e.vehicle.timestamp = vts
    return fm.SerializeToString()


def tu_feed(trips, ts=1792310000):
    fm = pb.FeedMessage()
    fm.header.gtfs_realtime_version = "2.0"
    fm.header.timestamp = ts
    for trip in trips:
        e = fm.entity.add()
        e.id = "TUUPDATE_" + trip
        e.trip_update.trip.trip_id = trip
        s = e.trip_update.stop_time_update.add()
        s.stop_id = "18000"
        s.arrival.delay = 60
    return fm.SerializeToString()


class Resp:
    def __init__(self, body):
        self._b = io.BytesIO(body)

    def read(self, n=-1):
        return self._b.read() if n is None or n < 0 else self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Net:
    """Serves POSITIONS_URL, TRIP_UPDATES_URL and STATIC_URL; counts requests."""

    def __init__(self, vp, tu=None, static=None):
        self.vp, self.tu, self.static = vp, tu, static
        self.calls = []
        self.fail = {}

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        check("user agent sent", req.get_header("User-agent") == rf.DEFAULT_USER)
        if url in self.fail:
            raise self.fail[url]
        body = {rf.POSITIONS_URL: self.vp, rf.TRIP_UPDATES_URL: self.tu,
                rf.STATIC_URL: self.static}[url]
        if body is None:
            raise urllib.error.URLError("down")
        return Resp(body() if callable(body) else body)


class Clock:
    def __init__(self, t=1792310000.0):
        self.t = t

    def __call__(self):
        return self.t


ROWS = [
    ("VP_C5-23743", "1090L23743C5", 40.30, -3.80, 1792309998),
    ("VP_C2-84931", "4190L84931C2", 37.60, -1.70, 1792309998),       # a bus
    ("VP_R11-77001", "5189D77001R11", 41.90, 2.80, 1792309998),       # a regional
    ("VP_R4-95224", "SPECIAL_50_95224R4", 41.72, 1.83, 1792309998),   # not in timetable
    ("VP_C1-00000", "1090L00000C1", 0.0, 0.0, 1792309998),            # null island
    ("VP_C1-26912", "6190L35912C1", 43.27, -1.97, 1792309990),        # older copy
    ("VP_C1-26912", "6190L35912C1", 43.28, -1.96, 1792309998),        # same trip, newer
]
net = Net(vp_feed(ROWS), tu_feed(["1090L23743C5", "4190L84931C2", "5189D77001R11",
                                  "1090L55555C5"]), static_zip())
clock = Clock()
cl = rf.RenfeClient(background=False, opener=net, clock=clock, today=TODAY)
snap = cl.snapshot()
vp_c, tu_c = snap["commuter"]
vp_i, tu_i = snap["intercity"]
vp_b, tu_b = snap["bus"]
trips = lambda fm: sorted(e.vehicle.trip.trip_id for e in fm.entity)  # noqa: E731
check("commuter positions", trips(vp_c) == ["1090L23743C5", "6190L35912C1", "SPECIAL_50_95224R4"])
check("intercity positions: the regional", trips(vp_i) == ["5189D77001R11"])
check("bus positions: the bus", trips(vp_b) == ["4190L84931C2"])
check("null island dropped", cl.dropped_null == 1)
check("duplicate trip kept once", cl.dropped_duplicate == 1)
dup = [e for e in vp_c.entity if e.vehicle.trip.trip_id == "6190L35912C1"][0]
check("duplicate: the newer fix", abs(dup.vehicle.position.latitude - 43.28) < 1e-4)
check("header copied", vp_c.header.timestamp == 1792310000)
tut = lambda fm: sorted(e.trip_update.trip.trip_id for e in fm.entity)  # noqa: E731
check("commuter trip updates (a TU without a position classified too)",
      tut(tu_c) == ["1090L23743C5", "1090L55555C5"])
check("intercity trip updates", tut(tu_i) == ["5189D77001R11"])
check("bus trip updates", tut(tu_b) == ["4190L84931C2"])
check("first snapshot: timetable, positions, trip updates", net.calls == [rf.STATIC_URL, rf.POSITIONS_URL, rf.TRIP_UPDATES_URL])
check("index loaded", cl.stats()["index_trips"] == 4)

# cached within the TTL
n0 = len(net.calls)
cl.for_consumer("commuter")
cl.for_consumer("bus")
check("within the TTL: no request", len(net.calls) == n0)
clock.t += rf.FEED_TTL_S
cl.for_consumer("commuter")
check("after the TTL: positions and trip updates again, not the timetable",
      net.calls[n0:] == [rf.POSITIONS_URL, rf.TRIP_UPDATES_URL])

# registration
c2 = rf.RenfeClient(background=False, opener=Net(vp_feed(ROWS), None, static_zip()), clock=Clock(), consumers=("commuter",),
                    today=TODAY)
try:
    c2.for_consumer("bus")
    check("unregistered consumer refused", False)
except ValueError:
    check("unregistered consumer refused", True)
try:
    c2.for_consumer("metro")
    check("unknown consumer refused", False)
except ValueError:
    check("unknown consumer refused", True)
p, t = c2.for_consumer("commuter")
check("trip updates down: None, positions still served", t is None and len(p.entity) == 3)
check("only registered consumers built", set(c2.snapshot()) == {"commuter"})
check("register new", c2.register("bus") is True)
check("register again", c2.register("bus") is False)
check("a newcomer gets its feed at once", trips(c2.for_consumer("bus")[0]) == ["4190L84931C2"])
check("unregister", c2.unregister("bus") is True and c2.unregister("bus") is False)
try:
    c2.for_consumer("bus")
    check("unregistered again refused", False)
except ValueError:
    check("unregistered again refused", True)
try:
    rf.RenfeClient(user=" ")
    check("empty user refused", False)
except ValueError:
    check("empty user refused", True)
try:
    rf.RenfeClient(consumers=("tram",))
    check("unknown consumer at construction refused", False)
except ValueError:
    check("unknown consumer at construction refused", True)

# positions down -> FeedUnavailable; 429 -> RateLimited
bad = Net(None, None, static_zip())
c3 = rf.RenfeClient(background=False, opener=bad, clock=Clock(), today=TODAY)
try:
    c3.snapshot()
    check("positions down raises FeedUnavailable", False)
except rf.FeedUnavailable:
    check("positions down raises FeedUnavailable", True)
bad.vp = vp_feed(ROWS)
bad.fail[rf.POSITIONS_URL] = urllib.error.HTTPError(rf.POSITIONS_URL, 429, "slow", {}, io.BytesIO(b""))
try:
    c3.snapshot(force=True)
    check("429 raises RateLimited", False)
except rf.RateLimited:
    check("429 raises RateLimited", True)
bad.fail[rf.POSITIONS_URL] = urllib.error.HTTPError(rf.POSITIONS_URL, 503, "x", {}, io.BytesIO(b""))
try:
    c3.snapshot(force=True)
    check("503 raises FeedUnavailable", False)
except rf.FeedUnavailable:
    check("503 raises FeedUnavailable", True)
del bad.fail[rf.POSITIONS_URL]
bad.vp = b"\x08\x96\x01not a feed"
try:
    c3.snapshot(force=True)
    check("undecodable body raises FeedUnavailable", False)
except rf.FeedUnavailable:
    check("undecodable body raises FeedUnavailable", True)

# timetable down: every trip is rail; retried no sooner than an hour
down = Net(vp_feed(ROWS), None, None)
ck = Clock()
c4 = rf.RenfeClient(background=False, opener=down, clock=ck, today=TODAY)
s4 = c4.snapshot()
check("no timetable: the bus is taken as rail", "4190L84931C2" in trips(s4["commuter"][0]))
check("no timetable: bus feed empty", len(s4["bus"][0].entity) == 0)
check("no timetable: index None", c4.stats()["index_trips"] is None)
ck.t += rf.FEED_TTL_S
c4.snapshot()
check("timetable not retried within the hour", down.calls.count(rf.STATIC_URL) == 1)
ck.t += rf.STATIC_RETRY_S
down.static = static_zip()
s4 = c4.snapshot()
check("timetable retried after the hour", down.calls.count(rf.STATIC_URL) == 2)
check("then the bus goes to bus", trips(s4["bus"][0]) == ["4190L84931C2"])
# the timetable too large: a VALID zip one byte over the cap is refused
big = Net(vp_feed(ROWS), None, static_zip())
c5 = rf.RenfeClient(background=False, opener=big, clock=Clock(), today=TODAY)
old = rf.STATIC_MAX_BYTES
rf.STATIC_MAX_BYTES = len(static_zip()) - 1
try:
    check("oversized timetable refused, quietly", c5.refresh_index(force=True) is False and c5.index is None)
finally:
    rf.STATIC_MAX_BYTES = old
check("at the cap it loads", rf.RenfeClient(background=False, opener=big, clock=Clock(), today=TODAY)
      .refresh_index(force=True) is True)
big = Net(vp_feed(ROWS), None, b"x" * 50)
check("a timetable that is not a zip: index stays None",
      rf.RenfeClient(background=False, opener=big, clock=Clock(), today=TODAY).refresh_index(force=True) is False)
# refreshed daily
ck.t += rf.STATIC_TTL_S
c4.snapshot()
check("timetable refreshed after a day", down.calls.count(rf.STATIC_URL) == 3)

# the newer of a trip's two rows wins wherever it stands in the body
first = Net(vp_feed([("VP_C1-26912", "6190L35912C1", 43.28, -1.96, 1792309998),
                     ("VP_C1-26912", "6190L35912C1", 43.27, -1.97, 1792309990)]), None, static_zip())
cfirst = rf.RenfeClient(background=False, opener=first, clock=Clock(), today=TODAY)
pos = cfirst.for_consumer("commuter")[0].entity
check("newer row first in the body: still the newer kept", len(pos) == 1
      and abs(pos[0].vehicle.position.latitude - 43.28) < 1e-4)

# --- returns (A-B-A) ---------------------------------------------------------------
seq = []
cur = {"pos": MADRID_A}
net6 = Net(lambda: vp_feed([("VP_C1-19830", "1090L19830C1", cur["pos"][0], cur["pos"][1], 1)]),
           None, static_zip())
ck6 = Clock()
c6 = rf.RenfeClient(background=False, opener=net6, clock=ck6, today=TODAY)


def step(pos):
    cur["pos"] = pos
    ck6.t += rf.FEED_TTL_S
    e = c6.for_consumer("commuter")[0].entity[0].vehicle.position
    return (round(e.latitude, 4), round(e.longitude, 4))


r4 = lambda p: (round(p[0], 4), round(p[1], 4))  # noqa: E731
check("A published", step(MADRID_A) == r4(MADRID_A))
check("B published (a real move)", step(MADRID_B) == r4(MADRID_B))
check("A again: a return, held at B", step(MADRID_A) == r4(MADRID_B))
check("B: still B", step(MADRID_B) == r4(MADRID_B))
check("A: held again (ping-pong stays held)", step(MADRID_A) == r4(MADRID_B))
check("held counted", c6.held_returns == 2)
check("A twice more in a row: accepted on the third",
      step(MADRID_A) == r4(MADRID_B) and step(MADRID_A) == r4(MADRID_A))
C = (40.50, -3.57)
check("a move onward is published", step(C) == r4(C))
check("returns counted: two in the ping-pong, one before the third", c6.held_returns == 3)
# seen all along: its history is kept however long, so a return after a long stand is held
long_net = Net(None, None, static_zip())
ck_long = Clock()
c_long = rf.RenfeClient(background=False, opener=long_net, clock=ck_long, today=TODAY)


def long_step(pos):
    long_net.vp = vp_feed([("VP_C1-7", "1090L00007C1", pos[0], pos[1], 1)])
    ck_long.t += 600
    e = c_long.snapshot(force=True)["commuter"][0].entity[0].vehicle.position
    return (round(e.latitude, 4), round(e.longitude, 4))


long_step(MADRID_A)
long_step(MADRID_B)
for _ in range(int(rf.FORGET_S / 600) + 3):
    long_step(MADRID_B)
check("a trip seen at every poll keeps its history past FORGET_S: a return still held",
      long_step(MADRID_A) == r4(MADRID_B))
# a small move back is not a return
c7net = Net(None, None, static_zip())
c7 = rf.RenfeClient(background=False, opener=c7net, clock=Clock(), today=TODAY)
P0, P1 = (40.0, -3.0), (40.0005, -3.0)       # 56 m apart
for pos, want in ((P0, P0), (P1, P1), (P0, P0)):
    c7net.vp = vp_feed([("VP_C1-1", "1090L00001C1", pos[0], pos[1], 1)])
    got = c7.snapshot(force=True)["commuter"][0].entity[0].vehicle.position
    check("under %d m back: published as is" % rf.RETURN_MIN_M,
          (round(got.latitude, 4), round(got.longitude, 4)) == r4(want))
# forgotten after FORGET_S
ck6.t += rf.FORGET_S + 1
net6.vp = vp_feed([("VP_C2-1", "1090L00002C2", 41.0, -3.0, 1)])
c6.snapshot(force=True)
check("a trip not seen for FORGET_S is forgotten", "1090L19830C1" not in c6._history)
check("stats keys", set(c6.stats()) == {"index_trips", "index_day", "timetable", "fetch_count", "held_returns",
                                         "dropped_null", "dropped_duplicate", "trips_tracked"})

# --- the timetable in the background (measured: 36 s to download) --------------------
import threading  # noqa: E402

gate = threading.Event()


class SlowNet(Net):
    def __call__(self, req, timeout=None):
        if req.full_url == rf.STATIC_URL:
            gate.wait(10)
        return Net.__call__(self, req, timeout)


slow = SlowNet(vp_feed(ROWS), tu_feed(["1090L23743C5"]), static_zip())
cb = rf.RenfeClient(opener=slow, clock=Clock(), today=TODAY)
check("background timetable: no zip before it loads", cb.static_zip() is None)
sb = cb.snapshot()
check("background timetable: the first snapshot does not wait for it",
      rf.POSITIONS_URL in slow.calls and not gate.is_set())
check("background timetable: while loading, every feed is empty (no bus drawn as a train)",
      all(len(vp.entity) == 0 for vp, _ in sb.values()))
check("background timetable: trip updates empty too", len(sb["commuter"][1].entity) == 0)
check("background timetable: stats say loading", cb.stats()["timetable"] == "loading: feeds empty")
check("background timetable: returns tracked while waiting", len(cb._history) == 5)
cb.snapshot(force=True)
check("background timetable: one load at a time", slow.calls.count(rf.STATIC_URL) <= 1)
check("background timetable: a forced load during it refused", cb.refresh_index(force=True) is False
      and slow.calls.count(rf.STATIC_URL) <= 1)
gate.set()
for th in threading.enumerate():
    if th.name == "saw_renfe_timetable":
        th.join(10)
sb = cb.snapshot(force=True)
check("background timetable: routed once loaded", trips(sb["bus"][0]) == ["4190L84931C2"]
      and len(sb["commuter"][0].entity) == 3)
check("background timetable: stats say loaded", cb.stats()["timetable"] == "loaded")
check("background timetable: the zip kept for consumers", cb.static_zip() == static_zip())
check("background timetable: loaded once", slow.calls.count(rf.STATIC_URL) == 1)
failnet = Net(vp_feed(ROWS), None, None)
cf = rf.RenfeClient(opener=failnet, clock=Clock(), today=TODAY)
cf.snapshot()
for th in threading.enumerate():
    if th.name == "saw_renfe_timetable":
        th.join(10)
sf = cf.snapshot(force=True)
check("background timetable failed: every trip taken as rail", "4190L84931C2" in trips(sf["commuter"][0]))
check("background timetable failed: stats say so", cf.stats()["timetable"] == "failed: every trip taken as rail")
check("a default client loads in the background", rf.RenfeClient()._background is True)

# --- the shared registration -------------------------------------------------------


class Hass:
    def __init__(self):
        self.data = {}


h = Hass()
a = srf.get_shared_renfe_client(h, "commuter")
check("first claim creates the entry", h.data[rf.SHARED_KEY]["client"] is a
      and a.consumers == frozenset({"commuter"}))
b = srf.get_shared_renfe_client(h, "intercity")
check("second integration finds it and registers", b is a and a.consumers == frozenset({"commuter", "intercity"}))
srf.get_shared_renfe_client(h, "commuter")
check("counts per consumer", h.data[rf.SHARED_KEY]["consumers"] == {"commuter": 2, "intercity": 1}
      and h.data[rf.SHARED_KEY]["refcount"] == 3)
srf.release_shared_renfe_client(h, "commuter")
check("one of two commuter claims released: still registered", "commuter" in a.consumers)
srf.release_shared_renfe_client(h, "bus")
check("never claimed: nothing changes", h.data[rf.SHARED_KEY]["refcount"] == 2)
srf.release_shared_renfe_client(h, "intercity")
check("last intercity claim: unregistered", "intercity" not in a.consumers)
srf.release_shared_renfe_client(h, "commuter")
check("last claim of all: entry gone", rf.SHARED_KEY not in h.data)
srf.release_shared_renfe_client(h, "commuter")
check("release with no entry: harmless", rf.SHARED_KEY not in h.data)
h2 = Hass()
h2.data[rf.SHARED_KEY] = {"contract_version": 99, "client": object()}
p2 = srf.get_shared_renfe_client(h2, "commuter")
check("foreign entry: a private client, entry untouched",
      isinstance(p2, rf.RenfeClient) and h2.data[rf.SHARED_KEY]["contract_version"] == 99)
h3 = Hass()
h3.data[rf.SHARED_KEY] = "junk"
check("garbage entry: no raise, private client", isinstance(srf.get_shared_renfe_client(h3, "bus"), rf.RenfeClient)
      and h3.data[rf.SHARED_KEY] == "junk")
srf.release_shared_renfe_client(h3, "bus")
check("release over garbage: harmless", h3.data[rf.SHARED_KEY] == "junk")
for bad_entry in ({"contract_version": rf.CONTRACT_VERSION, "entry_shape": 99, "consumers": {}, "client": a},
                  {"contract_version": rf.CONTRACT_VERSION, "entry_shape": 1, "consumers": [], "client": a},
                  {"contract_version": rf.CONTRACT_VERSION, "entry_shape": 1, "consumers": {}, "client": 1}):
    h4 = Hass()
    h4.data[rf.SHARED_KEY] = bad_entry
    got = srf.get_shared_renfe_client(h4, "commuter")
    check("unusable entry shape: private client", got is not a and h4.data[rf.SHARED_KEY] is bad_entry)

# --- import cost ---------------------------------------------------------------------
import subprocess  # noqa: E402
code = ("import sys; sys.path.insert(0, %r); import saw_livetrack; "
        "print('saw_livetrack.renfe' in sys.modules)" % os.path.join(ROOT, "src"))
out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
check("import saw_livetrack does not load renfe", out == "False")

bad_checks = [n for n, ok in CHECKS if not ok]
for n in bad_checks:
    print("FAIL", n)
print("%d checks, %d failed" % (len(CHECKS), len(bad_checks)))
sys.exit(1 if bad_checks else 0)
