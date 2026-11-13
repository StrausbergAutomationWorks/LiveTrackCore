"""Tests for saw_livetrack.tfnsw and its shared registration.

Every case is a MEASURED finding (capture round 1, 2026-10-18 to 10-21,
30_Integrations/Live_Track_Intercity_Rail/20_Research/_shared/australia/AU_CAPTURE1_ANALYSIS_20261101.md) or a decision of
Lee's (35_Dependencies/ours/saw-amtraker-client/DESIGN_319_tfnsw_transport_vic_20261102.md). The network is a fake.
Run:  python tests/test_tfnsw.py  -> exit 0
"""
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import urllib.error
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "reference"))

from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import saw_livetrack._keyed as keyed  # noqa: E402
import saw_livetrack.tfnsw as tf  # noqa: E402
import shared_tfnsw as stf  # noqa: E402

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


def caught(fn):
    try:
        fn()
    except Exception as err:  # noqa: BLE001
        return err
    return None


# --- the fake network -----------------------------------------------------------

class Resp:
    def __init__(self, body, headers=None):
        self.body, self.headers, self.status, self.pos = body, headers or {}, 200, 0

    def read(self, n=None):
        n = len(self.body) if n is None else n
        out = self.body[self.pos:self.pos + n]
        self.pos += len(out)
        return out

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(url, code, retry_after=None):
    return urllib.error.HTTPError(url, code, "x", {"Retry-After": retry_after} if retry_after else {}, None)


class Net:
    """One answer per URL: bytes, an int status, or an exception; a list is taken in turn, the last one repeating."""

    def __init__(self, vp=b"", tu=b"", static=None, modified="Mon, 02 Nov 2026 01:00:00 GMT"):
        self.vp, self.tu, self.static, self.modified = vp, tu, static, modified
        self.calls = []

    def _take(self, name):
        v = getattr(self, name)
        if isinstance(v, list):
            return v.pop(0) if len(v) > 1 else v[0]
        return v

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append((req.get_method(), url, req.get_header("Authorization"), req.get_header("User-agent")))
        if url == tf.STATIC_URL:
            if req.get_method() == "HEAD":
                v = self._take("modified")
                if isinstance(v, Exception):
                    raise v
                if isinstance(v, int):
                    raise http_error(url, v)
                return Resp(b"", {"Last-Modified": v})
            answer = self._take("static")
        else:
            answer = self._take("vp" if url == tf.POSITIONS_URL else "tu")
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            raise http_error(url, answer)
        return Resp(answer)

    def count(self, url, method="GET"):
        return sum(1 for c in self.calls if c[1] == url and c[0] == method)


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def feed():
    f = pb.FeedMessage()
    f.header.gtfs_realtime_version = "2.0"
    return f


def vehicle(body, trip, route, start="20261020", lat=-33.8, lon=151.2, ent=None):
    e = body.entity.add()
    e.id = ent or ((trip or "") + start)
    v = e.vehicle
    if trip is not None:
        v.trip.trip_id = trip
        v.trip.start_date = start
    if route:
        v.trip.route_id = route
    if lat is not None:
        v.position.latitude = lat
        v.position.longitude = lon
        v.position.bearing = 90.0
        v.position.speed = 0.0
    v.vehicle.id = "V" + (trip or "")
    v.vehicle.label = "label " + (trip or "")
    v.stop_id = "999"
    v.current_stop_sequence = 7
    v.current_status = 2
    v.timestamp = 1_800_000_000
    return e


def update(body, trip, route, start="20261020", seq=1):
    e = body.entity.add()
    e.id = "u" + trip + start
    t = e.trip_update
    t.trip.trip_id = trip
    t.trip.start_date = start
    if route:
        t.trip.route_id = route
    s = t.stop_time_update.add()
    s.stop_sequence = seq
    s.stop_id = "S%d" % seq
    s.arrival.delay = 60
    return e


def bodies():
    vp, tu = feed(), feed()
    vehicle(vp, "NT32", "4T.T.NT")
    vehicle(vp, "BM1", "4T.T.BMT")
    vehicle(vp, "HU1", "4T.T.HUN")
    vehicle(vp, "CO1", "4T.C.818")
    vehicle(vp, "XX1", "9X.Y")
    update(tu, "NT32", "4T.T.NT")
    update(tu, "NT32", "4T.T.NT", start="20261021")     # tomorrow's run of the same id
    update(tu, "BM1", "")                               # by the position's owner, no route_id
    update(tu, "NP9", "4T.T.NT", start="20261020")      # no position: every date stays
    update(tu, "NP9", "4T.T.NT", start="20261021")
    update(tu, "CO9", "4T.C.1")
    update(tu, "XX2", "9X.Y")
    return vp.SerializeToString(), tu.SerializeToString()


def client(net, consumers=None, **kw):
    kw.setdefault("clock", Clock())
    kw.setdefault("background", False)
    return tf.TfnswClient(consumers if consumers is not None else {tf.INTERCITY: KEY_A}, opener=net, **kw)


# --- who draws what ----------------------------------------------------------------
check("other 4T.T. is intercity", tf.consumer_of("4T.T.NT") == tf.INTERCITY)
check("a new 4T.T. line is intercity until the static says", tf.consumer_of("4T.T.ZZZ") == tf.INTERCITY)
for line in ("BMT", "HUN", "SCO", "SHL"):
    check("4T.T.%s is commuter" % line, tf.consumer_of("4T.T." + line) == tf.COMMUTER)
check("4T.C. is bus", tf.consumer_of("4T.C.818") == tf.BUS)
check("anything else nobody", tf.consumer_of("9X.Y") is None)
check("empty nobody", tf.consumer_of("") is None)
check("None nobody", tf.consumer_of(None) is None)
check("a number nobody", tf.consumer_of(42) is None)
check("padded route id read", tf.consumer_of(" 4T.C.1 ") == tf.BUS)
check("static route type wins: 100", tf.consumer_of("4T.T.ZZZ", {"4T.T.ZZZ": "100"}) == tf.COMMUTER)
check("static route type wins: 106", tf.consumer_of("4T.T.BMT", {"4T.T.BMT": "106"}) == tf.INTERCITY)
check("static 204", tf.consumer_of("4T.T.NT", {"4T.T.NT": "204"}) == tf.BUS)
check("static 205", tf.consumer_of("4T.T.NT", {"4T.T.NT": "205"}) == tf.BUS)
check("static other type: nobody", tf.consumer_of("4T.T.NT", {"4T.T.NT": "3"}) is None)
check("route not in static falls to the prefix", tf.consumer_of("4T.C.818", {"4T.T.NT": "106"}) == tf.BUS)
# 0.2.37 (Sydney Trains): the `transit` consumer; the contract stays 1 (Lee 2026-11-12, against the design's 2).
check("constants", (tf.SHARED_KEY, tf.CONSUMERS) == ("saw_tfnsw_v1", ("intercity", "commuter", "bus", "transit")))
check("attribution, D4", tf.ATTRIBUTION == "Data: Transport for NSW (opendata.transport.nsw.gov.au), CC BY 4.0")
check("urls", (tf.POSITIONS_URL, tf.TRIP_UPDATES_URL, tf.STATIC_URL) == (
    "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/nswtrains",
    "https://api.transport.nsw.gov.au/v1/gtfs/realtime/nswtrains",
    "https://api.transport.nsw.gov.au/v1/gtfs/schedule/nswtrains"))
check("feed ttl under the 60 s poll", tf.FEED_TTL_S == 55.0)
check("user agent", tf.DEFAULT_USER == "StrausbergAutomationWorks/saw-livetrack")

# --- construction ----------------------------------------------------------------------
check("empty user refused", raises(ValueError, lambda: tf.TfnswClient({tf.BUS: KEY_A}, user=" ")))
check("unknown consumer refused", raises(ValueError, lambda: tf.TfnswClient({"tram": KEY_A})))
check("empty key refused", raises(ValueError, lambda: tf.TfnswClient({tf.BUS: " "})))
check("no key refused", raises(ValueError, lambda: tf.TfnswClient({tf.BUS: None})))

# --- one feed, split -----------------------------------------------------------------
vp, tu = bodies()
net = Net(vp, tu)
c = client(net, {tf.INTERCITY: KEY_A, tf.COMMUTER: KEY_B, tf.BUS: KEY_A})
ip, iu = c.for_consumer(tf.INTERCITY)
cp, cu = c.for_consumer(tf.COMMUTER)
bp, bu = c.for_consumer(tf.BUS)
trips = lambda f: sorted(e.vehicle.trip.trip_id for e in f.entity)
runs = lambda f: sorted((e.trip_update.trip.trip_id, e.trip_update.trip.start_date) for e in f.entity)
check("intercity positions", trips(ip) == ["NT32"])
check("commuter positions", trips(cp) == ["BM1", "HU1"])
check("bus positions", trips(bp) == ["CO1"])
check("one fetch of each for three consumers", net.count(tf.POSITIONS_URL) == 1 and net.count(tf.TRIP_UPDATES_URL) == 1)
check("header carried over", ip.header.gtfs_realtime_version == "2.0" and iu.header.gtfs_realtime_version == "2.0")
check("the running run only; a trip with no position keeps every date", runs(iu) == [
    ("NP9", "20261020"), ("NP9", "20261021"), ("NT32", "20261020")])
check("an update with no route_id follows its position's owner", runs(cu) == [("BM1", "20261020")])
check("an update with no position goes by route_id", runs(bu) == [("CO9", "20261020")])
check("stats counts the dropped run", c.stats()["dropped_other_runs"] == 1)
check("positions: stop cleared", not ip.entity[0].vehicle.HasField("stop_id"))
check("positions: sequence cleared", not ip.entity[0].vehicle.HasField("current_stop_sequence"))
check("positions: status cleared", not ip.entity[0].vehicle.HasField("current_status"))
check("positions: speed cleared", not ip.entity[0].vehicle.position.HasField("speed"))
v0 = ip.entity[0].vehicle
check("positions: the rest kept", (round(v0.position.latitude, 1), round(v0.position.longitude, 1), v0.position.bearing,
                                   v0.vehicle.id, v0.vehicle.label, v0.timestamp, v0.trip.start_date, v0.trip.route_id) ==
      (-33.8, 151.2, 90.0, "VNT32", "label NT32", 1_800_000_000, "20261020", "4T.T.NT"))
check("all consumers cleaned", not cp.entity[0].vehicle.HasField("stop_id") and not bp.entity[0].vehicle.HasField("stop_id"))

# a trip with a position on both dates keeps both; an owner other than the update's route
f = feed()
vehicle(f, "T1", "4T.T.NT", start="20261020")
vehicle(f, "T1", "4T.T.NT", start="20261021", ent="other")
vehicle(f, "T2", "4T.T.BMT")
g = feed()
update(g, "T1", "4T.T.NT", start="20261020")
update(g, "T1", "4T.T.NT", start="20261021")
update(g, "T2", "4T.T.NT")      # its position says commuter
net = Net(f.SerializeToString(), g.SerializeToString())
c = client(net, {tf.INTERCITY: KEY_A, tf.COMMUTER: KEY_A})
check("a position on both dates keeps both", runs(c.for_consumer(tf.INTERCITY)[1]) == [("T1", "20261020"), ("T1", "20261021")])
check("the position's owner beats the update's route", runs(c.for_consumer(tf.COMMUTER)[1]) == [("T2", "20261020")])

f = feed()
vehicle(f, None, "4T.T.NT")
vehicle(f, "  ", "4T.T.NT", ent="blank")
vehicle(f, "NOPOS", "4T.T.NT", lat=None)
vehicle(f, "OK", "4T.T.NT")
c = client(Net(f.SerializeToString(), feed().SerializeToString()))
check("no trip, a blank trip, no position: dropped", trips(c.for_consumer(tf.INTERCITY)[0]) == ["OK"])

# --- registering ---------------------------------------------------------------------
vp, tu = bodies()
net = Net(vp, tu)
c = client(net)
check("only the registered consumer is served", raises(ValueError, lambda: c.for_consumer(tf.BUS)))
check("unknown consumer", raises(ValueError, lambda: c.for_consumer("tram")))
c.for_consumer(tf.INTERCITY)
check("register reports new", c.register(tf.BUS, KEY_B) is True)
check("register reports known", c.register(tf.BUS, KEY_B) is False)
check("consumers", c.consumers == {tf.INTERCITY, tf.BUS})
check("registered after a snapshot: served", trips(c.for_consumer(tf.BUS)[0]) == ["CO1"])
check("unregister reports", c.unregister(tf.BUS) is True and c.unregister(tf.BUS) is False)
check("register unknown refused", raises(ValueError, lambda: c.register("tram", KEY_A)))
check("unregister unknown refused", raises(ValueError, lambda: c.unregister("tram")))
check("unregistered: refused", raises(ValueError, lambda: c.for_consumer(tf.BUS)))

# register never waits for a fetch in progress
gate, entered = threading.Event(), threading.Event()


def slow(req, timeout=None):
    entered.set()
    gate.wait(5)
    return Resp(bodies()[0])


c = client(slow)
th = threading.Thread(target=lambda: c.for_consumer(tf.INTERCITY), daemon=True)
th.start()
entered.wait(5)
done = threading.Event()
th2 = threading.Thread(target=lambda: (c.register(tf.BUS, KEY_B), c.unregister(tf.BUS), done.set()), daemon=True)
th2.start()
check("register does not wait on a fetch", done.wait(3))
gate.set()
th.join(5)

# --- ttl ---------------------------------------------------------------------------------
vp, tu = bodies()
net, clk = Net(vp, tu), Clock()
c = client(net, clock=clk)
first = c.for_consumer(tf.INTERCITY)
clk.t += 54
check("inside the ttl: same feeds", c.for_consumer(tf.INTERCITY) is first and net.count(tf.POSITIONS_URL) == 1)
clk.t += 2
c.for_consumer(tf.INTERCITY)
check("past the ttl: fetched", net.count(tf.POSITIONS_URL) == 2 and net.count(tf.TRIP_UPDATES_URL) == 2)
c.for_consumer(tf.INTERCITY, force=True)
check("force fetches", net.count(tf.POSITIONS_URL) == 3)

# --- failures -----------------------------------------------------------------------------
vp, tu = bodies()
net, clk = Net([429, vp], tu), Clock()
c = client(net, clock=clk)
check("429 is RateLimited", raises(tf.RateLimited, lambda: c.for_consumer(tf.INTERCITY)))
check("backing off in stats", c.stats()["backing_off"] is True)
check("no request while backing off", raises(tf.RateLimited, lambda: c.for_consumer(tf.INTERCITY)) and net.count(tf.POSITIONS_URL) == 1)
clk.t += keyed.BACKOFF_S + 1
check("backoff ends", trips(c.for_consumer(tf.INTERCITY)[0]) == ["NT32"] and c.stats()["backing_off"] is False)

net, clk = Net(http_error(tf.POSITIONS_URL, 429, "900"), tu), Clock()
c = client(net, clock=clk)
raises(tf.RateLimited, lambda: c.for_consumer(tf.INTERCITY))
clk.t += keyed.BACKOFF_S + 1
check("Retry-After longer than the backoff is kept", raises(tf.RateLimited, lambda: c.for_consumer(tf.INTERCITY)) and net.count(tf.POSITIONS_URL) == 1)

net, clk = Net(http_error(tf.POSITIONS_URL, 429, "1"), tu), Clock()
c = client(net, clock=clk)
raises(tf.RateLimited, lambda: c.for_consumer(tf.INTERCITY))
clk.t += 2
raises(tf.RateLimited, lambda: c.for_consumer(tf.INTERCITY))
check("a short Retry-After does not shorten it", net.count(tf.POSITIONS_URL) == 1)

check("500 is FeedUnavailable", raises(tf.FeedUnavailable, lambda: client(Net(500, tu)).for_consumer(tf.INTERCITY)))
check("a transport error is FeedUnavailable", raises(tf.FeedUnavailable, lambda: client(Net(OSError("x"), tu)).for_consumer(tf.INTERCITY)))
check("undecodable positions are FeedUnavailable", raises(tf.FeedUnavailable, lambda: client(Net(b"\xff\xff\xff", tu)).for_consumer(tf.INTERCITY)))
check("an empty feed is no error", list(client(Net(feed().SerializeToString(), tu)).for_consumer(tf.INTERCITY)[0].entity) == [])

for name, bad in (("500", 500), ("a transport error", OSError("x")), ("401", 401), ("undecodable", b"\xff\xff\xff")):
    p, u = client(Net(vp, bad)).for_consumer(tf.INTERCITY)
    check("trip updates %s: positions still served" % name, trips(p) == ["NT32"] and u is None)
net, clk = Net(vp, 429), Clock()
c = client(net, clock=clk)
p, u = c.for_consumer(tf.INTERCITY)
check("trip updates 429: positions served, no updates", trips(p) == ["NT32"] and u is None)
clk.t += 60
check("trip updates 429 backs off the next poll", raises(tf.RateLimited, lambda: c.for_consumer(tf.INTERCITY)))

# --- keys ------------------------------------------------------------------------------------
vp, tu = bodies()
net = Net(vp, tu)
c = client(net, {tf.INTERCITY: KEY_A})
c.for_consumer(tf.INTERCITY)
method, url, auth, ua = net.calls[0]
check("header apikey", auth == "apikey " + KEY_A)
check("header user agent", ua == tf.DEFAULT_USER)
c.register(tf.COMMUTER, KEY_B)
c.for_consumer(tf.COMMUTER, force=True)
check("polls with the oldest key", net.calls[-1][2] == "apikey " + KEY_A)
c.unregister(tf.INTERCITY)
c.for_consumer(tf.COMMUTER, force=True)
check("unloading a consumer removes its key", net.calls[-1][2] == "apikey " + KEY_B)
c.register(tf.COMMUTER, KEY_A)
c.for_consumer(tf.COMMUTER, force=True)
check("registering again replaces the key", net.calls[-1][2] == "apikey " + KEY_A)

# refused by both feeds, ten minutes
net, clk = Net(401, 401), Clock()
c = client(net, {tf.INTERCITY: KEY_A, tf.COMMUTER: KEY_B}, clock=clk)
check("a 401 is KeyRefused, and a FeedUnavailable", issubclass(tf.KeyRefused, tf.FeedUnavailable) and raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True)))
check("first key still in use", net.calls[0][2] == "apikey " + KEY_A)
clk.t += 300
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
clk.t += 299
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
check("under ten minutes: not given up", c.stats()["keys_refused"] == 0)
clk.t += 2
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
check("ten minutes: given up", c.stats()["keys_refused"] == 1)
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
check("moves to the next key", net.calls[-1][2] == "apikey " + KEY_B)
check("the next key gets its own ten minutes", c.stats()["keys_refused"] == 1)
clk.t += 601
check("the last key gone: AuthRefused", raises(tf.AuthRefused, lambda: c.for_consumer(tf.INTERCITY, force=True)))
check("AuthRefused from then on", raises(tf.AuthRefused, lambda: c.for_consumer(tf.INTERCITY, force=True)))
c.register(tf.INTERCITY, "a-new-key-9999")
net.vp, net.tu = vp, tu
check("a reauth's new key works", trips(c.for_consumer(tf.INTERCITY, force=True)[0]) == ["NT32"])

# 403 counts as 401
net, clk = Net(403, 403), Clock()
c = client(net, clock=clk)
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
clk.t += 601
check("403 counts too", raises(tf.AuthRefused, lambda: c.for_consumer(tf.INTERCITY, force=True)))

# a success between restarts the ten minutes
net, clk = Net([401, 401, vp, 401, 401], [401, tu, tu, 401, 401]), Clock()
c = client(net, clock=clk)
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
clk.t += 400
raises(tf.FeedUnavailable, lambda: c.for_consumer(tf.INTERCITY, force=True))
clk.t += 100
c.for_consumer(tf.INTERCITY, force=True)
clk.t += 400
raises(tf.FeedUnavailable, lambda: c.for_consumer(tf.INTERCITY, force=True))
clk.t += 100
check("a success restarts the ten minutes", not isinstance(caught(lambda: c.for_consumer(tf.INTERCITY, force=True)), tf.AuthRefused))

# the positions answering restarts them, with the updates refusing throughout
net, clk = Net([401, vp, 401], 401), Clock()
c = client(net, clock=clk)
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
clk.t += 300
c.for_consumer(tf.INTERCITY, force=True)
clk.t += 350
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
check("the positions answering restarts the ten minutes", c.stats()["keys_refused"] == 0)

# the updates answering restarts them, with the positions refusing throughout
net, clk = Net(401, [401, tu, 401]), Clock()
c = client(net, clock=clk)
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
clk.t += 300
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
clk.t += 350
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
check("the updates answering restarts the ten minutes", c.stats()["keys_refused"] == 0)

# the same key given again, after the provider put it right
net, clk = Net(401, 401), Clock()
c = client(net, clock=clk)
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
clk.t += 601
raises(tf.AuthRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
c.register(tf.INTERCITY, KEY_A)
net.vp, net.tu = vp, tu
check("the same key given again is tried again", trips(c.for_consumer(tf.INTERCITY, force=True)[0]) == ["NT32"])

# only the updates refuse: measured on 2026-10-20, no verdict
net, clk = Net(vp, 401), Clock()
c = client(net, clock=clk)
for _ in range(20):
    clk.t += 60
    c.for_consumer(tf.INTERCITY, force=True)
check("updates alone refusing: never given up", c.stats()["keys_refused"] == 0)

# only the positions refuse: the other feed vouches for the key
net, clk = Net(401, tu), Clock()
c = client(net, clock=clk)
for _ in range(20):
    clk.t += 60
    raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
check("positions alone refusing: never given up", c.stats()["keys_refused"] == 0)

# only the positions refuse and the other feed is down: says nothing
net, clk = Net(401, 500), Clock()
c = client(net, clock=clk)
every = True
for _ in range(20):
    clk.t += 60
    every = every and raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
check("positions refusing, updates down: no verdict", c.stats()["keys_refused"] == 0)
check("positions refusing, updates down: still KeyRefused", every)

# a reauth on a key part way through its ten minutes starts them again
net, clk = Net(401, 401), Clock()
c = client(net, clock=clk)
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
clk.t += 590
c.register(tf.INTERCITY, "a-new-key-9999")
clk.t += 20
raises(tf.KeyRefused, lambda: c.for_consumer(tf.INTERCITY, force=True))
check("a new key starts its own ten minutes", c.stats()["keys_refused"] == 0)

# the key never shows
net = Net(401, 401)
c = client(net)
msg = str(caught(lambda: c.for_consumer(tf.INTERCITY, force=True)))
check("no key in a refusal", KEY_A not in msg and "401" in msg)
msg = str(caught(lambda: client(Net(OSError("boom " + KEY_A), tu)).for_consumer(tf.INTERCITY)))
check("no key from a transport error's text", KEY_A not in msg and "OSError" in msg)
check("no key in stats", KEY_A not in json.dumps(c.stats(), default=str))
check("stats counts keys", c.stats()["keys"] == 1)
check("two consumers on one key is one key", client(Net(vp, tu), {tf.INTERCITY: KEY_A, tf.BUS: KEY_A}).stats()["keys"] == 1)


# --- the timetable --------------------------------------------------------------------------
def static_zip(routes=("4T.T.NT,106", "4T.T.ZZZ,100"), drop=()):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("routes.txt", "﻿route_id,route_type\n" + "\n".join(routes) + "\n")
        for name in ("trips.txt", "stop_times.txt", "stops.txt"):
            if name not in drop:
                z.writestr(name, "x\n")
    return out.getvalue()


vp, tu = bodies()
with tempfile.TemporaryDirectory() as tmp:
    zp = os.path.join(tmp, "saw_tfnsw_nswtrains.zip")
    net, clk = Net(vp, tu, static=static_zip()), Clock()
    c = client(net, storage_dir=tmp, clock=clk)
    check("no timetable yet", c.static_path() is None and c.static_version() is None)
    check("first check downloads", c.refresh_static() is True and net.count(tf.STATIC_URL) == 1 and net.count(tf.STATIC_URL, "HEAD") == 1)
    check("HEAD goes first", net.calls[0][0] == "HEAD")
    check("key on the static", net.calls[1][2] == "apikey " + KEY_A)
    check("on disk under a neutral name", c.static_path() == zp and os.path.exists(zp))
    check("version is Last-Modified", c.static_version() == net.modified)
    check("no temp left", sorted(os.listdir(tmp)) == ["saw_tfnsw_nswtrains.json", "saw_tfnsw_nswtrains.zip"])
    check("stats routes", c.stats()["timetable_routes"] == 2)
    check("inside a day: nothing asked", c.refresh_static() is False and net.count(tf.STATIC_URL, "HEAD") == 1)
    check("force asks inside the day", c.refresh_static(force=True) is False and net.count(tf.STATIC_URL, "HEAD") == 2)
    net.calls = [x for x in net.calls if x[0] != "HEAD"] + [("HEAD", tf.STATIC_URL, None, None)]
    clk.t += tf.STATIC_TTL_S + 1
    check("a day on, unchanged: HEAD only", c.refresh_static() is False and net.count(tf.STATIC_URL, "HEAD") == 2 and net.count(tf.STATIC_URL) == 1)
    clk.t += 3600
    check("a checked day counts: nothing asked", c.refresh_static() is False and net.count(tf.STATIC_URL, "HEAD") == 2)
    clk.t += tf.STATIC_TTL_S
    net.modified = "Tue, 03 Nov 2026 01:00:00 GMT"
    net.static = static_zip(("4T.T.NT,204",))
    check("changed: downloaded", c.refresh_static() is True and c.static_version() == net.modified)
    p, _ = c.for_consumer(tf.INTERCITY)
    check("the static's route type now wins", trips(p) == [])
    clk.t += tf.STATIC_TTL_S + 1
    net.modified = "Wed, 04 Nov 2026 01:00:00 GMT"
    before = open(zp, "rb").read()
    for label, bad in (("a failed download", 500), ("a broken zip", b"not a zip"), ("a required file missing", static_zip(drop=("stops.txt",)))):
        net.static = bad
        c._static_retry = 0.0
        c._static_at = None
        check(label + ": False, never raises", c.refresh_static(force=True) is False)
        check(label + ": the copy kept", open(zp, "rb").read() == before and c.static_version() != net.modified)
        check(label + ": no temp", ".tmp" not in "".join(os.listdir(tmp)))
    net.static = OSError("boom " + KEY_A)
    check("a transport error on the download: False", c.refresh_static(force=True) is False)
    keep_max = tf.STATIC_MAX_BYTES
    tf.STATIC_MAX_BYTES = 10
    net.static = static_zip()
    check("over the cap: False", c.refresh_static(force=True) is False and open(zp, "rb").read() == before)
    tf.STATIC_MAX_BYTES = keep_max

    # a restart finds it, with the day's check still counted
    net2, clk2 = Net(vp, tu, static=static_zip(), modified="Tue, 03 Nov 2026 01:00:00 GMT"), Clock(clk.t)
    c2 = client(net2, storage_dir=tmp, clock=clk2)
    check("a restart finds the copy", c2.static_path() == zp and c2.static_version() == "Tue, 03 Nov 2026 01:00:00 GMT")
    trips_after = trips(c2.for_consumer(tf.INTERCITY)[0])
    check("a restart reads the route types", trips_after == [])
    net2.calls.clear()
    c3 = client(net2, storage_dir=tmp, clock=clk2)
    check("a restart does not ask again inside the day", c3.refresh_static() is False and net2.calls == [])

with tempfile.TemporaryDirectory() as tmp:
    # retried after an hour, not at once
    net, clk = Net(vp, tu, static=500), Clock()
    c = client(net, storage_dir=tmp, clock=clk)
    c.for_consumer(tf.INTERCITY)
    check("failed: asked once", net.count(tf.STATIC_URL) == 1)
    clk.t += 120
    c.for_consumer(tf.INTERCITY)
    check("not retried at once", net.count(tf.STATIC_URL) == 1)
    clk.t += tf.STATIC_RETRY_S
    c.for_consumer(tf.INTERCITY)
    check("retried after an hour", net.count(tf.STATIC_URL) == 2)

with tempfile.TemporaryDirectory() as tmp:
    # no Last-Modified: downloaded, and nothing to compare
    net = Net(vp, tu, static=static_zip(), modified="")
    c = client(net, storage_dir=tmp)
    check("no Last-Modified still downloads", c.refresh_static() is True)

check("no storage_dir: no timetable", client(Net(vp, tu)).static_path() is None and client(Net(vp, tu)).refresh_static() is False)

with tempfile.TemporaryDirectory() as tmp:
    # the feed waits for nothing: a slow timetable on its own thread
    gate, entered = threading.Event(), threading.Event()
    net = Net(vp, tu, static=static_zip())

    def opener(req, timeout=None):
        if req.full_url == tf.STATIC_URL:
            entered.set()
            gate.wait(5)
        return net(req, timeout)

    c = tf.TfnswClient({tf.INTERCITY: KEY_A}, opener=opener, clock=Clock(), storage_dir=tmp)
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("feeds", c.for_consumer(tf.INTERCITY)), daemon=True)
    th.start()
    th.join(3)
    check("the feed does not wait for the timetable", "feeds" in out and entered.is_set())
    check("no second load while one runs", c.refresh_static(force=True) is False and net.calls == [x for x in net.calls if x[1] != tf.STATIC_URL])
    gate.set()
    for t in threading.enumerate():
        if t.name == "saw_tfnsw_timetable":
            t.join(5)
    check("the background load finishes", c.static_version() == net.modified)


# --- the reference registration ---------------------------------------------------------------
class Config:
    def __init__(self, base):
        self.base = base

    def path(self, name):
        return os.path.join(self.base, name)


class Hass:
    def __init__(self):
        self.data = {}
        self.config = Config(tempfile.gettempdir())


h = Hass()
c1 = stf.get_shared_tfnsw_client(h, tf.INTERCITY, KEY_A)
c2 = stf.get_shared_tfnsw_client(h, tf.BUS, KEY_B)
c3 = stf.get_shared_tfnsw_client(h, tf.BUS, KEY_B)
check("one client", c1 is c2 is c3)
check("storage under .storage", c1.storage_dir == h.config.path(".storage"))
entry = h.data[tf.SHARED_KEY]
check("entry", entry["refcount"] == 3 and entry["consumers"] == {tf.INTERCITY: 1, tf.BUS: 2} and entry["contract_version"] == tf.CONTRACT_VERSION)
check("registered", c1.consumers == {tf.INTERCITY, tf.BUS})
stf.release_shared_tfnsw_client(h, tf.BUS)
check("a claim left: still registered", tf.BUS in c1.consumers and entry["refcount"] == 2)
stf.release_shared_tfnsw_client(h, tf.COMMUTER)
check("releasing what was never claimed changes nothing", entry["refcount"] == 2 and tf.COMMUTER not in entry["consumers"])
stf.release_shared_tfnsw_client(h, tf.BUS)
check("its last claim unregisters", tf.BUS not in c1.consumers and tf.BUS not in entry["consumers"] and c1.stats()["keys"] == 1)
stf.release_shared_tfnsw_client(h, tf.INTERCITY)
check("the last claim of all removes the entry", tf.SHARED_KEY not in h.data)
stf.release_shared_tfnsw_client(h, tf.INTERCITY)
check("releasing with no entry is nothing", tf.SHARED_KEY not in h.data)

logging.disable(logging.CRITICAL)
for label, foreign in (("another contract", {"contract_version": 99, "entry_shape": stf._ENTRY_SHAPE, "client": c1, "consumers": {}}),
                       ("another shape", {"contract_version": tf.CONTRACT_VERSION, "entry_shape": 99, "client": c1, "consumers": {}}),
                       ("a foreign client", {"contract_version": tf.CONTRACT_VERSION, "entry_shape": stf._ENTRY_SHAPE, "client": object(), "consumers": {}}),
                       ("no consumers", {"contract_version": tf.CONTRACT_VERSION, "entry_shape": stf._ENTRY_SHAPE, "client": c1}),
                       ("not a dict", "junk")):
    h = Hass()
    h.data[tf.SHARED_KEY] = foreign
    priv = stf.get_shared_tfnsw_client(h, tf.BUS, KEY_A)
    check(label + ": private client, entry untouched", isinstance(priv, tf.TfnswClient) and h.data[tf.SHARED_KEY] is foreign and priv is not c1)
    check(label + ": private client serves its consumer", priv.consumers == {tf.BUS})
    stf.release_shared_tfnsw_client(h, tf.BUS)
    check(label + ": release leaves it alone", h.data[tf.SHARED_KEY] is foreign)
logging.disable(logging.NOTSET)

# --- import cost ---------------------------------------------------------------------
code = ("import sys; sys.path.insert(0, %r); import saw_livetrack; "
        "print('saw_livetrack.tfnsw' in sys.modules, 'saw_livetrack._keyed' in sys.modules)" % os.path.join(ROOT, "src"))
out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
check("import saw_livetrack does not load tfnsw", out == "False False")
code = ("import sys; sys.path.insert(0, %r); import saw_livetrack.tfnsw; "
        "print(any(m.startswith('homeassistant') for m in sys.modules), 'google.transit' in sys.modules)" % os.path.join(ROOT, "src"))
out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
check("importing tfnsw loads no Home Assistant and no bindings", out == "False False")

bad_checks = [n for n, ok in CHECKS if not ok]
for n in bad_checks:
    print("FAIL", n)
print("%d checks, %d failed" % (len(CHECKS), len(bad_checks)))
sys.exit(1 if bad_checks else 0)
