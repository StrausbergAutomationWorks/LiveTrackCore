"""Tests for saw_livetrack.adelaide_metro and its shared registration.

Every case is a MEASURED finding (35_Dependencies/third_party/adelaide-metro/SSOT.md, 2026-11-02 and 2026-11-13) or a
decision of Lee's (the consumers: trains to Commuter Rail, school buses and the ferry to Transit).
Run:  python tests/test_adelaide_metro.py  -> exit 0
"""
import io
import os
import sys
import urllib.error
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "reference"))

from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import saw_livetrack.adelaide_metro as am  # noqa: E402
import shared_adelaide_metro as sam  # noqa: E402

CHECKS = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


# --- who draws what -------------------------------------------------------------
check("rail to commuter", am.consumer_of("2") == am.COMMUTER)
check("tram to transit", am.consumer_of("0") == am.TRANSIT)
check("metro bus to transit", am.consumer_of("3") == am.TRANSIT)
check("school bus 712 to transit (Lee 2026-11-02)", am.consumer_of("712") == am.TRANSIT)
check("ferry to transit (Lee 2026-11-02)", am.consumer_of("4") == am.TRANSIT)
check("regional bus 701 to bus", am.consumer_of("701") == am.BUS)
check("padded type read", am.consumer_of(" 2 ") == am.COMMUTER)
check("unknown type to nobody", am.consumer_of("1") is None and am.consumer_of("700") is None)
check("None to nobody", am.consumer_of(None) is None and am.consumer_of("") is None)
check("not a string to nobody", am.consumer_of(2) is None)
check("consumers", am.CONSUMERS == ("commuter", "transit", "bus"))
check("shared key", am.SHARED_KEY == "saw_adelaide_metro_v1")
check("credit as South Australia asks", "Adelaide Metro – Department for Infrastructure and Transport, South Australia"
      in am.ATTRIBUTION and "CC BY 4.0" in am.ATTRIBUTION)
check("terms: the developer page", am.TERMS_URL == "https://www.adelaidemetro.com.au/developer-info")


def static_zip(extra_route=b""):
    files = {"routes.txt": (b"route_id,agency_id,route_short_name,route_long_name,route_type,route_color\n"
                            b"SEAFRD,2,SEAFRD,Seaford to City,2,FE5000\n"
                            b" BEL ,2,BEL,Belair to City,2,509E2F\n"
                            b"GLNELG,1,GLNELG,Glenelg Tram,0,DB0032\n"
                            b"100,3,100,Arndale to Marion,3,FFFFFF\n"
                            b"S1,3,S1,School bus,712,AAAAAA\n"
                            b"LINK1,4,LINK1,Mount Gambier,701,90489A\n"
                            b"KI,5,KI,Kangaroo Island ferry,4,0000AA\n"
                            b"ODD,5,ODD,Unknown,7,000000\n" + extra_route)}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


rt = am.route_types(static_zip())
check("routes read, ids stripped", rt["SEAFRD"] == "2" and rt["BEL"] == "2" and rt["LINK1"] == "701")
check("routes: every row", len(rt) == 8)

# --- the client, over a fake network --------------------------------------------


def vp_feed(rows, ts=1794603700):
    fm = pb.FeedMessage()
    fm.header.gtfs_realtime_version = "2.0"
    fm.header.timestamp = ts
    for vid, trip, route, lat, lon, vts in rows:
        e = fm.entity.add()
        e.id = vid or trip or "x"
        if trip is not None:
            e.vehicle.trip.trip_id = trip
        if route is not None:
            e.vehicle.trip.route_id = route
        if vid:
            e.vehicle.vehicle.id = vid
            e.vehicle.vehicle.label = vid
        e.vehicle.position.latitude = lat
        e.vehicle.position.longitude = lon
        e.vehicle.position.bearing = 178.0
        e.vehicle.timestamp = vts
    return fm.SerializeToString()


def tu_feed(rows, ts=1794603700):
    fm = pb.FeedMessage()
    fm.header.gtfs_realtime_version = "2.0"
    fm.header.timestamp = ts
    for trip, route in rows:
        e = fm.entity.add()
        e.id = "TU" + trip
        e.trip_update.trip.trip_id = trip
        if route is not None:
            e.trip_update.trip.route_id = route
        s = e.trip_update.stop_time_update.add()
        s.stop_sequence = 999
        s.stop_id = "6665"
        s.arrival.time = 1794603900
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
    def __init__(self, vp, tu=None, static=None, version=b"1718\n"):
        self.body = {am.POSITIONS_URL: vp, am.TRIP_UPDATES_URL: tu, am.STATIC_URL: static, am.VERSION_URL: version}
        self.calls = []
        self.fail = {}

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        check("user agent sent", req.get_header("User-agent") == am.DEFAULT_USER)
        if url in self.fail:
            raise self.fail[url]
        body = self.body[url]
        if body is None:
            raise urllib.error.URLError("down")
        return Resp(body() if callable(body) else body)


class Clock:
    def __init__(self, t=1794603700.0):
        self.t = t

    def __call__(self):
        return self.t


ROWS = [
    ("3011", "924822", "OUTHA", -34.85, 138.50, 1794603686),     # route unknown to this timetable: nobody
    ("4006", "684256", "SEAFRD", -35.10, 138.48, 1794603688),    # a train
    ("3026", "683827", "BEL", -34.97, 138.62, 1794603690),       # a train, padded route in routes.txt
    ("101", "T1", "GLNELG", -34.95, 138.55, 1794603690),         # a tram
    ("1005", "B1", "100", -34.90, 138.60, 1794603690),           # a metro bus
    ("2001", "L1", "LINK1", -37.80, 140.78, 1794603690),         # a regional coach
    ("2002", "S9", "S1", -34.80, 138.70, 1794603690),            # a school bus
    ("3000", "Z1", "ODD", -34.80, 138.70, 1794603690),           # route_type 7: nobody
    ("4999", "N1", "SEAFRD", 0.0, 0.0, 1794603690),              # null island
    ("4006", "684256", "SEAFRD", -35.11, 138.47, 1794603680),    # same vehicle, OLDER: dropped
    ("4007", "", "SEAFRD", -35.0, 138.5, 1794603690),            # no trip
    ("4008", "684300", "", -35.0, 138.5, 1794603690),            # no route
    ("3026", "683900", "BEL", -34.96, 138.61, 1794603600),       # same vehicle under another trip, older: dropped
    ("4010", "684400", "SEAFRD", 0.0, 138.5, 1794603690),        # latitude 0 alone is not null island
]
net = Net(vp_feed(ROWS), tu_feed([("684256", "SEAFRD"), ("683827", "BEL"), ("T1", "GLNELG"),
                                  ("B1", None), ("ZZ", None), ("L1", "LINK1"), ("777", "SEAFRD")]), static_zip())
clock = Clock()
cl = am.AdelaideMetroClient(background=False, opener=net, clock=clock)
snap = cl.snapshot()
vids = lambda fm: sorted(e.vehicle.vehicle.id for e in fm.entity)  # noqa: E731
tus = lambda fm: sorted(e.trip_update.trip.trip_id for e in fm.entity)  # noqa: E731
vp_c, tu_c = snap["commuter"]
vp_t, tu_t = snap["transit"]
vp_b, tu_b = snap["bus"]
check("commuter: the trains", vids(vp_c) == ["3026", "4006", "4010"])
check("commuter: the newer fix of a vehicle twice in a body",
      abs([e for e in vp_c.entity if e.vehicle.vehicle.id == "4006"][0].vehicle.position.latitude + 35.10) < 1e-4)
check("transit: tram, metro bus, school bus", vids(vp_t) == ["1005", "101", "2002"])
check("bus: the regional coach", vids(vp_b) == ["2001"])
check("a route the timetable lacks and route_type 7 go to nobody", cl.unclassified == 2)
check("null island dropped", cl.dropped_null == 1)
check("duplicate vehicle kept once, by vehicle not trip", cl.dropped_duplicate == 2)
check("rows without a trip or a route dropped", cl.dropped_untripped == 2)
check("commuter trip updates by route, a trip with no vehicle too", tus(tu_c) == ["683827", "684256", "777"])
check("an update without a route goes where its vehicle went", tus(tu_t) == ["B1", "T1"])
check("an update with neither goes nowhere", "ZZ" not in tus(tu_c) + tus(tu_t) + tus(tu_b))
check("bus trip updates", tus(tu_b) == ["L1"])
check("the header is kept", vp_c.header.timestamp == 1794603700 and tu_c.header.timestamp == 1794603700)
check("the bearing survives", vp_c.entity[0].vehicle.position.bearing == 178.0)
check("timetable version held", cl.static_version() == "1718" and cl.static_zip() == static_zip())
check("stats", cl.stats()["timetable"] == "loaded" and cl.stats()["routes"] == 8
      and cl.stats()["timetable_version"] == "1718")

# within FEED_TTL_S: no new request
n = len(net.calls)
clock.t += am.FEED_TTL_S - 1
cl.snapshot()
check("within the TTL: served from memory", len(net.calls) == n)
clock.t += 2
cl.snapshot()
check("past the TTL: positions and updates read again", net.calls[n:] == [am.POSITIONS_URL, am.TRIP_UPDATES_URL])

# version.txt: read hourly, the zip only when it changed
n = len(net.calls)
clock.t = 1794603700.0 + am.VERSION_CHECK_S + 1
cl.snapshot(force=True)
check("an hour on, same version: version.txt only, no zip", am.VERSION_URL in net.calls[n:]
      and am.STATIC_URL not in net.calls[n:])
net.body[am.VERSION_URL] = b"1719"
net.body[am.STATIC_URL] = static_zip(b"NEW,2,NEW,New line,2,000000\n")
n = len(net.calls)
clock.t += am.VERSION_CHECK_S + 1
cl.snapshot(force=True)
check("a new version: the zip read again", am.STATIC_URL in net.calls[n:] and cl.static_version() == "1719"
      and cl.index.get("NEW") == "2")
check("refresh_static is quiet when not due", cl.refresh_static() is False)

# trip updates down: positions still served
net.fail[am.TRIP_UPDATES_URL] = urllib.error.HTTPError(am.TRIP_UPDATES_URL, 503, "x", {}, None)
clock.t += am.FEED_TTL_S + 1
p, t = cl.for_consumer("commuter")
check("trip updates down: positions, no updates", vids(p) == ["3026", "4006", "4010"] and t is None)
del net.fail[am.TRIP_UPDATES_URL]
# positions down: an error, never an empty answer (C2)
net.fail[am.POSITIONS_URL] = urllib.error.URLError("down")
clock.t += am.FEED_TTL_S + 1
try:
    cl.for_consumer("commuter")
    check("positions down raises", False)
except am.FeedUnavailable:
    check("positions down raises", True)
net.fail[am.POSITIONS_URL] = urllib.error.HTTPError(am.POSITIONS_URL, 429, "x", {}, None)
try:
    cl.for_consumer("commuter", force=True)
    check("429 is RateLimited", False)
except am.RateLimited:
    check("429 is RateLimited", True)
del net.fail[am.POSITIONS_URL]

# no timetable: nothing classified, a bus never drawn as a train
bad = Net(vp_feed(ROWS), tu_feed([("684256", "SEAFRD")]), None)
c2 = am.AdelaideMetroClient(background=False, opener=bad, clock=Clock())
p, t = c2.for_consumer("commuter")
check("no timetable: empty feeds", len(p.entity) == 0 and len(t.entity) == 0)
check("no timetable: said so", c2.stats()["timetable"] == "failed: nothing classified")
check("no timetable: nothing counted as unclassified", c2.stats()["unclassified"] == 0)
# a timetable too large is refused
big = Net(vp_feed(ROWS), None, b"x" * 10)
old = am.STATIC_MAX_BYTES
am.STATIC_MAX_BYTES = 5
c3 = am.AdelaideMetroClient(background=False, opener=big, clock=Clock())
c3.snapshot()
am.STATIC_MAX_BYTES = old
check("a zip over the limit is refused", c3.index is None)
try:
    am._get(am.STATIC_URL, am.DEFAULT_USER, 5, opener=lambda req, timeout=None: Resp(b"x" * 10), max_bytes=5)
    check("a body over max_bytes raises", False)
except am.FeedUnavailable:
    check("a body over max_bytes raises", True)
check("a body within max_bytes is returned", am._get(am.STATIC_URL, am.DEFAULT_USER, 5,
      opener=lambda req, timeout=None: Resp(b"x" * 5), max_bytes=5) == b"xxxxx")

# consumers
c4 = am.AdelaideMetroClient(background=False, opener=net, clock=Clock(), consumers=("commuter",))
check("only registered consumers get feeds", set(c4.snapshot()) == {"commuter"})
try:
    c4.for_consumer("transit")
    check("unregistered consumer refused", False)
except ValueError:
    check("unregistered consumer refused", True)
check("register is new once", c4.register("transit") is True and c4.register("transit") is False)
check("registered later: served at once", len(c4.for_consumer("transit")[0].entity) == 3)
check("unregister", c4.unregister("transit") is True and c4.unregister("transit") is False)
try:
    am.AdelaideMetroClient(consumers=("trams",))
    check("unknown consumer refused", False)
except ValueError:
    check("unknown consumer refused", True)
try:
    am.AdelaideMetroClient(user=" ")
    check("empty user agent refused", False)
except ValueError:
    check("empty user agent refused", True)

# --- the shared registration --------------------------------------------------------


class Hass:
    def __init__(self):
        self.data = {}


h = Hass()
a = sam.get_shared_adelaide_metro_client(h, "commuter")
check("first claim creates the entry", h.data[am.SHARED_KEY]["client"] is a and a.consumers == frozenset({"commuter"}))
b = sam.get_shared_adelaide_metro_client(h, "transit")
check("second integration finds it and registers", b is a and a.consumers == frozenset({"commuter", "transit"}))
sam.get_shared_adelaide_metro_client(h, "commuter")
check("counts per consumer", h.data[am.SHARED_KEY]["consumers"] == {"commuter": 2, "transit": 1}
      and h.data[am.SHARED_KEY]["refcount"] == 3)
sam.release_shared_adelaide_metro_client(h, "commuter")
check("one of two commuter claims released: still registered", "commuter" in a.consumers)
sam.release_shared_adelaide_metro_client(h, "bus")
check("never claimed: nothing changes", h.data[am.SHARED_KEY]["refcount"] == 2)
sam.release_shared_adelaide_metro_client(h, "commuter")
check("last commuter claim: unregistered", "commuter" not in a.consumers)
sam.release_shared_adelaide_metro_client(h, "transit")
check("last claim of all: entry gone", am.SHARED_KEY not in h.data)
h.data[am.SHARED_KEY] = {"contract_version": 99}
c = sam.get_shared_adelaide_metro_client(h, "commuter")
check("foreign entry: private client, entry untouched", isinstance(c, am.AdelaideMetroClient)
      and h.data[am.SHARED_KEY] == {"contract_version": 99})
check("no homeassistant import in the module", not any(ln.lstrip().startswith(("import homeassistant", "from homeassistant")) for ln in open(am.__file__, encoding="utf-8")))

bad_checks = [n for n, ok in CHECKS if not ok]
for n in bad_checks:
    print("FAIL", n)
print("%d checks, %d failed" % (len(CHECKS), len(bad_checks)))
sys.exit(1 if bad_checks else 0)
