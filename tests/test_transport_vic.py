"""Tests for saw_livetrack.transport_vic and its shared registration.

Every case is a MEASURED finding (35_Dependencies/third_party/transport-victoria/SSOT.md, capture round 1, the two publications
of 2026-10-16 and 11-02) or a decision of Lee's (35_Dependencies/ours/saw-amtraker-client/DESIGN_319_tfnsw_transport_vic_20261102.md).
The network is a fake that honours Range the way the portal does.
Run:  python tests/test_transport_vic.py  -> exit 0
"""
import functools
import io
import json
import logging
import os
import struct
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
import saw_livetrack.transport_vic as tv  # noqa: E402
import shared_transport_vic as stv  # noqa: E402

CHECKS = []
KEY_A, KEY_B = "vic-AAAA-1234", "vic-BBBB-5678"
FILE_URL = "https://opendata.transport.vic.gov.au/dataset/3f4e/resource/62370a3a/download/gtfs.zip"
VP_URL = tv.REALTIME_BASE + "vline/vehicle-positions"
TU_URL = tv.REALTIME_BASE + "vline/trip-updates"
RATE = '[{"window":24,"type":"throttle","remaining":19},{"window":24,"type":"throttle","remaining":254}]'


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


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@functools.lru_cache(maxsize=None)  # a zip holds the time it was made: one tag, one set of bytes
def folder_zip(tag="a", drop=()):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("routes.txt", "trips.txt", "stop_times.txt", "stops.txt"):
            if name not in drop:
                z.writestr(name, "x,%s\n" % tag)
    return out.getvalue()


def outer_zip(folders=None, method=zipfile.ZIP_STORED, padding=0):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", method) as z:
        for n, body in (folders or {"1": folder_zip("one"), "5": folder_zip("five"), "2": folder_zip("two")}).items():
            info = zipfile.ZipInfo("%s/google_transit.zip" % n)
            info.compress_type = method
            info.extra = b"\x99\x99\x04\x00abcd"     # local header and directory both carry it
            info.comment = b"a comment"
            z.writestr(info, body)
        for i in range(padding):
            z.writestr("pad/%05d-a-long-name-to-fill-the-central-directory.txt" % i, "")
    return out.getvalue()


def package(url=FILE_URL, fmt="ZIP"):
    return json.dumps({"result": {"resources": [{"format": "PDF", "url": "https://x/doc.pdf"}, {"format": fmt, "url": url}]}}).encode()


class Resp:
    def __init__(self, body, headers=None, status=200):
        self.body, self.headers, self.status, self.pos, self.reads, self.closed = body, headers or {}, status, 0, 0, False

    def read(self, n=None):
        self.reads += 1
        n = len(self.body) - self.pos if n is None else n
        out = self.body[self.pos:self.pos + n]
        self.pos += len(out)
        return out

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Net:
    """The portal: CKAN, the file by HEAD and Range; the realtime gateway."""

    def __init__(self, file=None, etag='"e1"', vp=b"", tu=b""):
        self.file, self.etag, self.vp, self.tu = file if file is not None else outer_zip(), etag, vp, tu
        self.pkg = package()
        self.calls = []
        self.rate = RATE
        self.ignore_range = False
        self.etag_after = None      # (n range requests, new etag)
        self.ranges = 0
        self.last = None
        self.corrupt = None         # flips a byte of the response to a Range starting past this offset
        self.head_length = True
        self.overflow = 0           # bytes sent past what a Range asked for

    @staticmethod
    def _answer(v, url, headers=None):
        if isinstance(v, list):
            v = v.pop(0) if len(v) > 1 else v[0]
        if isinstance(v, Exception):
            raise v
        if isinstance(v, int):
            raise urllib.error.HTTPError(url, v, "x", {}, None)
        return Resp(v, headers)

    def __call__(self, req, timeout=None):
        url, method = req.full_url, req.get_method()
        self.calls.append((method, url, req.get_header("Range"), req.get_header("Keyid"), req.get_header("User-agent")))
        if url == tv.CKAN_URL:
            return self._answer(self.pkg, url)
        if url == VP_URL:
            return self._answer(self.vp, url, {"X-Rate-Limit": self.rate})
        if url == TU_URL:
            return self._answer(self.tu, url)
        if url != FILE_URL:
            raise urllib.error.HTTPError(url, 404, "x", {}, None)
        if method == "HEAD":
            h = {"ETag": self.etag}
            if self.head_length:
                h["Content-Length"] = str(len(self.file))
            if isinstance(self.etag, Exception):
                raise self.etag
            return Resp(b"", h)
        rng = req.get_header("Range")
        self.ranges += 1
        etag = self.etag
        if self.etag_after and self.ranges > self.etag_after[0]:
            etag = self.etag_after[1]
        if self.ignore_range or rng is None:
            self.last = Resp(self.file, {"ETag": etag}, 200)
            return self.last
        a, b = rng.split("=")[1].split("-")
        body = self.file[int(a):int(b) + 1] + b"\0" * self.overflow
        if self.corrupt is not None and int(a) > self.corrupt and len(body) > 200:
            body = body[:100] + bytes([body[100] ^ 0xFF]) + body[101:]
        self.last = Resp(body, {"ETag": etag}, 206)
        return self.last

    def count(self, url, method="GET"):
        return sum(1 for c in self.calls if c[1] == url and c[0] == method)


def client(net, consumers=None, **kw):
    kw.setdefault("clock", Clock())
    kw.setdefault("background", False)
    return tv.TransportVicClient(consumers if consumers is not None else {tv.INTERCITY: KEY_A}, opener=net, **kw)


def rt_bodies():
    vp, tu = pb.FeedMessage(), pb.FeedMessage()
    for f in (vp, tu):
        f.header.gtfs_realtime_version = "2.0"
    e = vp.entity.add()
    e.id = "1"
    e.vehicle.trip.trip_id = "8821"
    e.vehicle.position.latitude = -37.8
    e.vehicle.position.longitude = 144.9
    e = tu.entity.add()
    e.id = "2"
    e.trip_update.trip.trip_id = "8821"
    return vp.SerializeToString(), tu.SerializeToString()


vp, tu = rt_bodies()

# --- constants -------------------------------------------------------------------
check("constants", (tv.SHARED_KEY, tv.CONSUMERS) == ("saw_transport_vic_v1", ("intercity", "bus")))
check("folders held across two publications", tv.FOLDERS == {"intercity": "1", "bus": "5"})
check("realtime: V/Line's only", tv.FEEDS == {"intercity": "vline"})
check("the portal, never the catalogue", tv.CKAN_URL == "https://opendata.transport.vic.gov.au/api/3/action/package_show?id=3f4e292e-7f8a-4ffe-831f-1953be0fe448")
check("realtime url", VP_URL == "https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/vline/vehicle-positions")
check("ttl under the 30 s poll", tv.FEED_TTL_S == 25.0)
check("cap", tv.MAX_FOLDER_BYTES == 128 * 1024 * 1024)
check("user agent past Cloudflare", tv.DEFAULT_USER == "StrausbergAutomationWorks/saw-livetrack")
check("attribution", "Transport Victoria" in tv.ATTRIBUTION and "CC BY 4.0" in tv.ATTRIBUTION)

# --- parsing ---------------------------------------------------------------------
check("package: the ZIP resource", tv.parse_package(package()) == FILE_URL)
check("package: format any case", tv.parse_package(package(fmt="zip")) == FILE_URL)
check("package: no ZIP", raises(tv.FeedUnavailable, lambda: tv.parse_package(package(fmt="CSV"))))
two = json.dumps({"result": {"resources": [{"format": "ZIP", "url": "a"}, {"format": "ZIP", "url": "b"}]}}).encode()
check("package: two ZIPs refused rather than guessed", raises(tv.FeedUnavailable, lambda: tv.parse_package(two)))
check("package: not JSON", raises(tv.FeedUnavailable, lambda: tv.parse_package(b"<html>")))
check("package: another shape", raises(tv.FeedUnavailable, lambda: tv.parse_package(b'{"result": []}')))

raw = outer_zip()
members, where = tv.parse_central_directory(raw, 0)
check("directory: members", set(members) == {"1/google_transit.zip", "5/google_transit.zip", "2/google_transit.zip"})
m = members["5/google_transit.zip"]
check("directory: sizes, offset, crc", m[0] == 0 and m[2] == len(folder_zip("five")) and m[3] == m[2] and raw[m[4]:m[4] + 4] == b"PK\x03\x04")
check("directory: where it is", raw[where[0]:where[0] + 4] == b"PK\x01\x02")
check("directory: an extra field and a comment read past", members["2/google_transit.zip"][2] == len(folder_zip("two")))
check("directory: after the tail's start", tv.parse_central_directory(raw[10:], 10)[0] == members)
check("directory: before the tail", tv.parse_central_directory(raw[-30:], len(raw) - 30)[0] is None)
check("directory: no end record", raises(tv.FeedUnavailable, lambda: tv.parse_central_directory(b"junk", 0)))
z64 = b"PK\x05\x06" + b"\0" * 6 + struct.pack("<HII", 3, 100, 0xFFFFFFFF) + b"\0\0"
check("directory: zip64 refused", raises(tv.FeedUnavailable, lambda: tv.parse_central_directory(z64, 0)))
check("member_for", tv.member_for(members, "1") == "1/google_transit.zip" and tv.member_for(members, "9") is None)
check("member_for: 1 is not 10", tv.member_for({"10/google_transit.zip": 0}, "1") is None)
check("member_for: only a zip", tv.member_for({"1/readme.txt": 0}, "1") is None)

# --- the folder --------------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    net, clk = Net(), Clock()
    c = client(net, {tv.INTERCITY: None}, storage_dir=tmp, clock=clk)
    check("nothing yet", c.static_path(tv.INTERCITY) is None and c.static_version(tv.INTERCITY) is None)
    check("fetched", c.refresh_static() is True)
    zp = os.path.join(tmp, "saw_transport_vic_1.zip")
    check("a neutral name", c.static_path(tv.INTERCITY) == zp)
    check("the right folder, byte for byte", open(zp, "rb").read() == folder_zip("one"))
    check("version is the ETag", c.static_version(tv.INTERCITY) == '"e1"')
    check("the other folder not fetched", c.static_path(tv.BUS) is None and not os.path.exists(os.path.join(tmp, "saw_transport_vic_5.zip")))
    check("no whole-file GET", all(x[2] is not None for x in net.calls if x[1] == FILE_URL and x[0] == "GET"))
    check("the URL comes from the portal", net.count(tv.CKAN_URL) == 1)
    check("user agent on every request", all(x[4] == tv.DEFAULT_USER for x in net.calls))
    check("no key on the timetable", all(x[3] is None for x in net.calls))
    check("no temp", ".tmp" not in "".join(os.listdir(tmp)))
    n = len(net.calls)
    check("inside a day: nothing asked", c.refresh_static() is False and len(net.calls) == n)
    clk.t += tv.STATIC_TTL_S + 1
    check("a day on, unchanged: no Range request", c.refresh_static() is False and net.ranges == 3 + 0 and net.count(tv.CKAN_URL) == 2 and net.count(FILE_URL, "HEAD") == 2)
    # a new publication at a new address
    new_url = FILE_URL.replace("62370a3a", "aaaaaaaa")
    clk.t += tv.STATIC_TTL_S + 1
    net.pkg = package(FILE_URL)
    net.file, net.etag = outer_zip({"1": folder_zip("newer"), "5": folder_zip("five")}), '"e2"'
    check("a new ETag fetches", c.refresh_static() is True)
    check("the new folder", open(zp, "rb").read() == folder_zip("newer") and c.static_version(tv.INTERCITY) == '"e2"')
    # a restart finds it
    c2 = client(net, {tv.INTERCITY: None}, storage_dir=tmp, clock=clk)
    check("a restart finds it", c2.static_path(tv.INTERCITY) == zp and c2.static_version(tv.INTERCITY) == '"e2"')
    n = len(net.calls)
    check("a restart does not ask again", c2.refresh_static() is False and len(net.calls) == n)
    # a consumer joins: its folder comes on the next check, not tomorrow
    c.register(tv.BUS)
    check("a new consumer's folder: fetched next check", c.refresh_static() is True and open(c.static_path(tv.BUS), "rb").read() == folder_zip("five"))
    check("its version", c.static_version(tv.BUS) == '"e2"')

with tempfile.TemporaryDirectory() as tmp:
    # a folder gone from disk comes back, though the ETag has not changed
    net, clk = Net(), Clock()
    c = client(net, {tv.INTERCITY: None}, storage_dir=tmp, clock=clk)
    c.refresh_static()
    os.remove(os.path.join(tmp, "saw_transport_vic_1.zip"))
    check("a deleted folder: no path", c.static_path(tv.INTERCITY) is None)
    clk.t += tv.STATIC_TTL_S + 1
    check("a deleted folder is fetched again", c.refresh_static() is True and c.static_path(tv.INTERCITY) is not None)

with tempfile.TemporaryDirectory() as tmp:
    # the portal's address moves
    net = Net()
    moved = FILE_URL + "?moved"
    net.pkg = package(moved)
    c = client(net, {tv.INTERCITY: None}, storage_dir=tmp)
    c.refresh_static()
    check("a compiled address is never used", net.count(FILE_URL, "HEAD") == 0 and any(x[1] == moved for x in net.calls))

with tempfile.TemporaryDirectory() as tmp:
    # deflated member
    net = Net(outer_zip(method=zipfile.ZIP_DEFLATED))
    c = client(net, {tv.BUS: None}, storage_dir=tmp)
    check("a deflated member inflates", c.refresh_static() is True and open(c.static_path(tv.BUS), "rb").read() == folder_zip("five"))

with tempfile.TemporaryDirectory() as tmp:
    # central directory bigger than the tail
    net = Net(outer_zip(padding=1200))
    c = client(net, {tv.INTERCITY: None}, storage_dir=tmp)
    check("a directory before the tail: read in a second request", c.refresh_static() is True and open(c.static_path(tv.INTERCITY), "rb").read() == folder_zip("one"))
    first = next(x[2] for x in net.calls if x[1] == FILE_URL and x[0] == "GET")
    check("the tail, not the whole file", first == "bytes=%d-%d" % (len(net.file) - tv._TAIL, len(net.file) - 1) and len(net.file) > tv._TAIL)

with tempfile.TemporaryDirectory() as tmp:
    # one folder fetched and the next failing: the first is known after a restart
    net = Net(outer_zip({"1": folder_zip("one")}))
    c = client(net, {tv.INTERCITY: None, tv.BUS: None}, storage_dir=tmp)
    check("the second folder missing: False", c.refresh_static() is False)
    c2 = client(net, {tv.INTERCITY: None, tv.BUS: None}, storage_dir=tmp)
    check("the first folder survives a restart", c2.static_version(tv.INTERCITY) == '"e1"' and c2.static_path(tv.BUS) is None)
    check("the missing folder raises its own error", raises(tv.FeedUnavailable, lambda: c._fetch_folder(FILE_URL, '"e1"', {}, "5", 0)))

with tempfile.TemporaryDirectory() as tmp:
    # both folders
    net = Net()
    c = client(net, {tv.INTERCITY: None, tv.BUS: None}, storage_dir=tmp)
    check("both", c.refresh_static() is True and c.stats()["folders"] == ["1", "5"])
    check("stats holds no url or key", "http" not in json.dumps(c.stats(), default=str))


def failing(label, setup, tail_ok=True):
    """A failed fetch: False, no raise, the last copy kept, no temp, and a day's retry rule intact."""
    with tempfile.TemporaryDirectory() as tmp:
        net, clk = Net(), Clock()
        c = client(net, {tv.INTERCITY: None}, storage_dir=tmp, clock=clk)
        c.refresh_static()
        zp = c.static_path(tv.INTERCITY)
        before = open(zp, "rb").read()
        clk.t += tv.STATIC_TTL_S + 1
        net.etag = '"e2"'
        net.ranges = 0
        setup(net)
        check(label + ": False", c.refresh_static() is False)
        check(label + ": the copy kept", open(zp, "rb").read() == before and c.static_version(tv.INTERCITY) == '"e1"')
        check(label + ": no temp", ".tmp" not in "".join(os.listdir(tmp)))
        return net


def set_ignore(n):
    n.ignore_range = True


net = failing("a 200 to a Range request", set_ignore)
check("a 200 to a Range request: abandoned unread", net.last.reads == 0 and net.last.closed)
failing("the ETag changes mid-fetch", lambda n: setattr(n, "etag_after", (1, '"e3"')))
failing("the ETag changes at the member", lambda n: setattr(n, "etag_after", (2, '"e3"')))
failing("a corrupt member", lambda n: setattr(n, "corrupt", 0))
failing("CKAN down", lambda n: setattr(n, "pkg", 500))
failing("CKAN's answer over its cap", lambda n: setattr(n, "pkg", package() + b" " * (4 * 1024 * 1024 + 1)))
net = failing("a compression method not read here", lambda n: setattr(n, "file", outer_zip({"1": folder_zip("x")}, method=zipfile.ZIP_BZIP2)))
check("a compression method not read here: stopped at the directory", net.ranges == 1)
net = failing("more bytes than the member holds", lambda n: setattr(n, "overflow", 5 * 1024 * 1024))
check("more bytes than the member holds: not read to the end", net.last.pos < len(net.last.body))
failing("CKAN not JSON", lambda n: setattr(n, "pkg", b"<html>"))
net = failing("HEAD with no size", lambda n: setattr(n, "head_length", False))
check("HEAD with no size: no Range asked", net.ranges == 0)


def broken_local(n):
    off = tv.parse_central_directory(n.file, 0)[0]["1/google_transit.zip"][4]
    n.file = n.file[:off] + b"XXXX" + n.file[off + 4:]


net = failing("a broken local header", broken_local)
check("a broken local header: stopped before the member", net.ranges == 2)
failing("HEAD with no ETag", lambda n: setattr(n, "etag", ""))
failing("no such folder", lambda n: setattr(n, "file", outer_zip({"5": folder_zip("five")})))
failing("a folder lacking a file", lambda n: setattr(n, "file", outer_zip({"1": folder_zip("x", drop=("stops.txt",))})))
failing("a folder not a zip", lambda n: setattr(n, "file", outer_zip({"1": b"not a zip"})))
keep = tv.MAX_FOLDER_BYTES
failing("over the cap", lambda n: setattr(tv, "MAX_FOLDER_BYTES", 10))
tv.MAX_FOLDER_BYTES = keep

with tempfile.TemporaryDirectory() as tmp:
    # the etag of a tail that answered a different file
    net = Net()
    net.etag_after = (0, '"other"')
    c = client(net, {tv.INTERCITY: None}, storage_dir=tmp)
    check("a tail of another ETag: nothing fetched", c.refresh_static() is False and c.static_path(tv.INTERCITY) is None)
    check("and the rest not asked", net.ranges == 1)

with tempfile.TemporaryDirectory() as tmp:
    # retry rule
    net, clk = Net(), Clock()
    net.pkg = 500
    c = client(net, {tv.INTERCITY: KEY_A}, storage_dir=tmp, clock=clk)
    net.vp, net.tu = vp, tu
    c.for_consumer(tv.INTERCITY)
    check("failed: asked once", net.count(tv.CKAN_URL) == 1)
    clk.t += 60
    c.for_consumer(tv.INTERCITY)
    check("not retried at once", net.count(tv.CKAN_URL) == 1)
    clk.t += tv.STATIC_RETRY_S
    c.for_consumer(tv.INTERCITY)
    check("retried after six hours", net.count(tv.CKAN_URL) == 2)

check("no storage_dir", client(Net(), {tv.BUS: None}).refresh_static() is False and client(Net(), {tv.BUS: None}).static_path(tv.BUS) is None)
check("unknown consumer's path", raises(ValueError, lambda: client(Net()).static_path("tram")))
check("unknown consumer's version", raises(ValueError, lambda: client(Net()).static_version("tram")))

with tempfile.TemporaryDirectory() as tmp:
    # the feed waits for nothing
    gate, entered = threading.Event(), threading.Event()
    net = Net(vp=vp, tu=tu)

    def opener(req, timeout=None):
        if req.full_url == tv.CKAN_URL:
            entered.set()
            gate.wait(5)
        return net(req, timeout)

    c = tv.TransportVicClient({tv.INTERCITY: KEY_A}, opener=opener, clock=Clock(), storage_dir=tmp)
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("feeds", c.for_consumer(tv.INTERCITY)), daemon=True)
    th.start()
    th.join(3)
    check("the feed does not wait for the timetable", "feeds" in out and entered.is_set())
    check("no second load while one runs", c.refresh_static(force=True) is False and net.count(tv.CKAN_URL) == 0)
    gate.set()
    for t in threading.enumerate():
        if t.name == "saw_transport_vic_timetable":
            t.join(5)
    check("the background load finishes", c.static_version(tv.INTERCITY) == '"e1"')

# --- realtime ----------------------------------------------------------------------
net, clk = Net(vp=vp, tu=tu), Clock()
c = client(net, clock=clk)
p, u = c.for_consumer(tv.INTERCITY)
check("positions", p.entity[0].vehicle.trip.trip_id == "8821")
check("updates", u.entity[0].trip_update.trip.trip_id == "8821")
check("header KeyId", net.calls[0][3] == KEY_A and net.calls[1][3] == KEY_A)
check("rate limit read into stats", c.stats()["rate_limit"][1]["remaining"] == 254)
clk.t += 24
check("inside the ttl: same", c.for_consumer(tv.INTERCITY)[0] is p and net.count(VP_URL) == 1)
clk.t += 2
c.for_consumer(tv.INTERCITY)
check("past the ttl", net.count(VP_URL) == 2 and net.count(TU_URL) == 2)
c.for_consumer(tv.INTERCITY, force=True)
check("force", net.count(VP_URL) == 3)
net.rate = "not json"
c.for_consumer(tv.INTERCITY, force=True)
check("a rate header not understood: none", c.stats()["rate_limit"] is None)

check("a consumer with no realtime", raises(ValueError, lambda: client(Net(), {tv.BUS: None}).for_consumer(tv.BUS)))
check("not registered", raises(ValueError, lambda: client(Net()).for_consumer(tv.BUS)))
check("not registered, though it has a feed", raises(ValueError, lambda: client(Net(vp=vp, tu=tu), {tv.BUS: KEY_A}).for_consumer(tv.INTERCITY)))
check("unknown", raises(ValueError, lambda: client(Net()).for_consumer("tram")))
check("realtime with no key: AuthRefused", raises(tv.AuthRefused, lambda: client(Net(vp=vp, tu=tu), {tv.INTERCITY: None}).for_consumer(tv.INTERCITY)))
check("no key: no request", (lambda n: (raises(tv.AuthRefused, lambda: client(n, {tv.INTERCITY: None}).for_consumer(tv.INTERCITY)), n.calls == [])[1])(Net(vp=vp, tu=tu)))
check("empty user refused", raises(ValueError, lambda: tv.TransportVicClient({tv.BUS: None}, user=" ")))
check("unknown consumer refused", raises(ValueError, lambda: tv.TransportVicClient({"tram": None})))

check("500 (still no cached message) is FeedUnavailable", raises(tv.FeedUnavailable, lambda: client(Net(vp=500, tu=tu)).for_consumer(tv.INTERCITY)))
check("a transport error", raises(tv.FeedUnavailable, lambda: client(Net(vp=OSError("x"), tu=tu)).for_consumer(tv.INTERCITY)))
check("undecodable", raises(tv.FeedUnavailable, lambda: client(Net(vp=b"\xff\xff\xff", tu=tu)).for_consumer(tv.INTERCITY)))
for name, bad in (("500", 500), ("a transport error", OSError("x")), ("401", 401), ("undecodable", b"\xff\xff\xff")):
    p, u = client(Net(vp=vp, tu=bad)).for_consumer(tv.INTERCITY)
    check("trip updates %s: positions served" % name, len(p.entity) == 1 and u is None)

net, clk = Net(vp=[429, vp], tu=tu), Clock()
c = client(net, clock=clk)
check("429 is RateLimited", raises(tv.RateLimited, lambda: c.for_consumer(tv.INTERCITY)))
check("no request while backing off", raises(tv.RateLimited, lambda: c.for_consumer(tv.INTERCITY)) and net.count(VP_URL) == 1 and c.stats()["backing_off"])
clk.t += keyed.BACKOFF_S + 1
check("backoff ends", len(c.for_consumer(tv.INTERCITY)[0].entity) == 1)
net, clk = Net(vp=vp, tu=429), Clock()
c = client(net, clock=clk)
check("updates 429: positions served", c.for_consumer(tv.INTERCITY)[1] is None)
clk.t += 60
check("updates 429 backs off the next poll", raises(tv.RateLimited, lambda: c.for_consumer(tv.INTERCITY)))

# keys
net, clk = Net(vp=vp, tu=tu), Clock()
c = client(net, {tv.INTERCITY: KEY_A}, clock=clk)
c.register(tv.BUS, KEY_B)
c.for_consumer(tv.INTERCITY)
check("the oldest key", net.calls[-1][3] == KEY_A)
c.register(tv.BUS)
check("a key-less registration keeps the key", c.stats()["keys"] == 2)
c.unregister(tv.BUS)
check("unregister removes its key", c.stats()["keys"] == 1)
check("register and unregister report", c.register(tv.BUS) is True and c.register(tv.BUS) is False and c.unregister(tv.BUS) is True and c.unregister(tv.BUS) is False)
check("register unknown", raises(ValueError, lambda: c.register("tram")))
check("unregister unknown", raises(ValueError, lambda: c.unregister("tram")))
check("consumers", c.consumers == {tv.INTERCITY})

net, clk = Net(vp=401, tu=401), Clock()
c = client(net, {tv.INTERCITY: KEY_A, tv.BUS: KEY_B}, clock=clk)
check("a 401 is KeyRefused", raises(tv.KeyRefused, lambda: c.for_consumer(tv.INTERCITY, force=True)))
clk.t += 599
raises(tv.KeyRefused, lambda: c.for_consumer(tv.INTERCITY, force=True))
check("under ten minutes: kept", c.stats()["keys_refused"] == 0)
clk.t += 2
raises(tv.KeyRefused, lambda: c.for_consumer(tv.INTERCITY, force=True))
check("ten minutes: given up", c.stats()["keys_refused"] == 1)
raises(tv.KeyRefused, lambda: c.for_consumer(tv.INTERCITY, force=True))
check("the next key", net.calls[-1][3] == KEY_B)
clk.t += 601
check("none left: AuthRefused", raises(tv.AuthRefused, lambda: c.for_consumer(tv.INTERCITY, force=True)))
c.register(tv.INTERCITY, "a-new-key-9999")
net.vp, net.tu = vp, tu
check("a reauth works", len(c.for_consumer(tv.INTERCITY, force=True)[0].entity) == 1)

for label, u_ans in (("the other feed answers", tu), ("the other feed down", 500)):
    net, clk = Net(vp=401, tu=u_ans), Clock()
    c = client(net, clock=clk)
    every = True
    for _ in range(20):
        clk.t += 60
        every = every and raises(tv.KeyRefused, lambda: c.for_consumer(tv.INTERCITY, force=True))
    check("positions refusing, %s: no verdict" % label, c.stats()["keys_refused"] == 0)
    check("positions refusing, %s: still KeyRefused" % label, every)

net, clk = Net(vp=[401, vp, 401], tu=401), Clock()
c = client(net, clock=clk)
raises(tv.KeyRefused, lambda: c.for_consumer(tv.INTERCITY, force=True))
clk.t += 300
c.for_consumer(tv.INTERCITY, force=True)
clk.t += 350
raises(tv.KeyRefused, lambda: c.for_consumer(tv.INTERCITY, force=True))
check("the positions answering restarts the ten minutes", c.stats()["keys_refused"] == 0)

net, clk = Net(vp=401, tu=[401, tu, 401]), Clock()
c = client(net, clock=clk)
raises(tv.KeyRefused, lambda: c.for_consumer(tv.INTERCITY, force=True))
clk.t += 300
raises(tv.KeyRefused, lambda: c.for_consumer(tv.INTERCITY, force=True))
clk.t += 350
raises(tv.KeyRefused, lambda: c.for_consumer(tv.INTERCITY, force=True))
check("the updates answering restarts the ten minutes", c.stats()["keys_refused"] == 0)
net, clk = Net(vp=vp, tu=401), Clock()
c = client(net, clock=clk)
for _ in range(20):
    clk.t += 60
    c.for_consumer(tv.INTERCITY, force=True)
check("updates alone refusing: never given up", c.stats()["keys_refused"] == 0)

net, clk = Net(vp=[401, 401, vp, 401, 401], tu=[401, tu, 401, 401]), Clock()
c = client(net, clock=clk)
raises(tv.KeyRefused, lambda: c.for_consumer(tv.INTERCITY, force=True))
clk.t += 400
raises(tv.FeedUnavailable, lambda: c.for_consumer(tv.INTERCITY, force=True))
clk.t += 100
c.for_consumer(tv.INTERCITY, force=True)
clk.t += 400
raises(tv.FeedUnavailable, lambda: c.for_consumer(tv.INTERCITY, force=True))
clk.t += 100
check("a success restarts the ten minutes", not isinstance(caught(lambda: c.for_consumer(tv.INTERCITY, force=True)), tv.AuthRefused))

msg = str(caught(lambda: client(Net(vp=401, tu=401)).for_consumer(tv.INTERCITY)))
check("no key in a refusal", KEY_A not in msg and "401" in msg)
msg = str(caught(lambda: client(Net(vp=OSError("boom " + KEY_A), tu=tu)).for_consumer(tv.INTERCITY)))
check("no key from a transport error's text", KEY_A not in msg)
check("no key in stats", KEY_A not in json.dumps(client(Net()).stats(), default=str))

# register never waits for a fetch
gate, entered = threading.Event(), threading.Event()


def slow(req, timeout=None):
    entered.set()
    gate.wait(5)
    return Resp(vp)


c = client(slow)
th = threading.Thread(target=lambda: caught(lambda: c.for_consumer(tv.INTERCITY)), daemon=True)
th.start()
entered.wait(5)
done = threading.Event()
th2 = threading.Thread(target=lambda: (c.register(tv.BUS, KEY_B), c.unregister(tv.BUS), done.set()), daemon=True)
th2.start()
check("register does not wait on a fetch", done.wait(3))
gate.set()
th.join(5)


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
c1 = stv.get_shared_transport_vic_client(h, tv.INTERCITY, KEY_A)
c2 = stv.get_shared_transport_vic_client(h, tv.BUS)
c3 = stv.get_shared_transport_vic_client(h, tv.BUS)
check("one client", c1 is c2 is c3)
check("storage under .storage", c1.storage_dir == h.config.path(".storage"))
entry = h.data[tv.SHARED_KEY]
check("entry", entry["refcount"] == 3 and entry["consumers"] == {tv.INTERCITY: 1, tv.BUS: 2} and entry["contract_version"] == tv.CONTRACT_VERSION)
stv.release_shared_transport_vic_client(h, tv.BUS)
check("a claim left: registered", tv.BUS in c1.consumers and entry["refcount"] == 2)
stv.release_shared_transport_vic_client(h, tv.BUS)
check("its last claim unregisters", tv.BUS not in c1.consumers and tv.BUS not in entry["consumers"])
stv.release_shared_transport_vic_client(h, tv.BUS)
check("releasing what is not claimed changes nothing", entry["refcount"] == 1)
stv.release_shared_transport_vic_client(h, tv.INTERCITY)
check("the last claim removes the entry", tv.SHARED_KEY not in h.data)
stv.release_shared_transport_vic_client(h, tv.INTERCITY)

logging.disable(logging.CRITICAL)
for label, foreign in (("another contract", {"contract_version": 99, "entry_shape": stv._ENTRY_SHAPE, "client": c1, "consumers": {}}),
                       ("another shape", {"contract_version": tv.CONTRACT_VERSION, "entry_shape": 99, "client": c1, "consumers": {}}),
                       ("a foreign client", {"contract_version": tv.CONTRACT_VERSION, "entry_shape": stv._ENTRY_SHAPE, "client": object(), "consumers": {}}),
                       ("no consumers", {"contract_version": tv.CONTRACT_VERSION, "entry_shape": stv._ENTRY_SHAPE, "client": c1}),
                       ("not a dict", "junk")):
    h = Hass()
    h.data[tv.SHARED_KEY] = foreign
    priv = stv.get_shared_transport_vic_client(h, tv.BUS, KEY_A)
    check(label + ": private client, entry untouched", isinstance(priv, tv.TransportVicClient) and h.data[tv.SHARED_KEY] is foreign and priv is not c1)
    check(label + ": it serves its consumer", priv.consumers == {tv.BUS})
    stv.release_shared_transport_vic_client(h, tv.BUS)
    check(label + ": release leaves it alone", h.data[tv.SHARED_KEY] is foreign)
logging.disable(logging.NOTSET)

# --- import cost ---------------------------------------------------------------------
code = ("import sys; sys.path.insert(0, %r); import saw_livetrack; "
        "print('saw_livetrack.transport_vic' in sys.modules)" % os.path.join(ROOT, "src"))
out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
check("import saw_livetrack does not load transport_vic", out == "False")
code = ("import sys; sys.path.insert(0, %r); import saw_livetrack.transport_vic; "
        "print(any(m.startswith('homeassistant') for m in sys.modules), 'google.transit' in sys.modules)" % os.path.join(ROOT, "src"))
out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
check("importing it loads no Home Assistant and no bindings", out == "False False")

bad_checks = [n for n, ok in CHECKS if not ok]
for n in bad_checks:
    print("FAIL", n)
print("%d checks, %d failed" % (len(CHECKS), len(bad_checks)))
sys.exit(1 if bad_checks else 0)
