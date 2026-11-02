"""Shared Transport Victoria client for the Live Track family.

Shared from first use (Lee 2026-10-20 01:09: "I want it hoisted the same way"; backlog 319). Design and every decision:
35_Dependencies/ours/saw-amtraker-client/DESIGN_319_tfnsw_transport_vic_20261102.md. Provider:
35_Dependencies/third_party/transport-victoria/SSOT.md. Pure Python: NO homeassistant imports (05_SHARED_LESSONS.md
B8.1a). One client per Home Assistant (reference/shared_transport_vic.py, B16).

Data: Transport Victoria, CC BY 4.0. Two things:

THE GTFS SCHEDULE, no key: one zip of numbered folders, each a google_transit.zip, each integration asking for its own
(FOLDERS). MEASURED across two publications, 2026-10-16 and 11-02: the folder numbers held, and everything else moved.
* THE URL CAN CHANGE: never compile it. `.../resource/fb152201-.../download/gtfs.zip` answers 404 since the resource id
  became 62370a3a-...; it is read from the portal's CKAN package each time (CKAN_URL). discover.data.vic.gov.au still
  named the dead URL: not that host.
* THE WHOLE FILE IS 184-268 MB: a folder is fetched by HTTP Range. Tail, end of central directory, member's local header,
  member; offsets are read afresh each time; the ETag is checked on every response and the CRC at the end. A 200 to a
  Range request is abandoned at once, never read: that would be the whole file.
* A COPY GOES STALE: 3,031 of the new copy's 6,988 trip ids are in the copy 17 days older; live on 2026-11-02, 40 of 40
  in the new copy, 25 of 40 in the old. The portal says Weekly: checked daily, fetched when the ETag changes.
* A folder goes to disk, never memory: storage_dir/saw_transport_vic_<folder>.zip. V/Line's shapes.txt unpacks to 193 MB and
  is contiguous and ascending, so a consumer can distill it one shape at a time.

THE REALTIME FEEDS, a key per user, header KeyId. Only V/Line's are measured, so only V/Line's are here; a mode is a line
in FEEDS once it has been. No V/Line rule is here either: its positions run on the timetable and stop at midnight
(30_Integrations/Live_Track_Intercity_Rail/20_Research/_shared/australia/AU_CAPTURE1_ANALYSIS_20261101.md section 4), and
that is its one consumer's to handle.

Behind Cloudflare: Python's default User-Agent is refused with HTTP 403, body `error code: 1010`, which looks like a bad
key (05_SHARED_LESSONS.md C13). DEFAULT_USER answered 200 on 2026-11-02. HTTP 500 "(still) no cached message" came 217
times in 2.8 days of the capture: FeedUnavailable, for the consumer to ride out.
"""

from __future__ import annotations

import json
import os
import struct
import threading
import time
import urllib.error
import urllib.request
import zipfile
import zlib

from ._keyed import (AuthRefused, Backoff, FeedUnavailable, KeyedError, KeyRefused, KeyRing,  # noqa: F401
                     RateLimited, fetch)

CONTRACT_VERSION = 1
SHARED_KEY = "saw_transport_vic_v%d" % CONTRACT_VERSION

DATASET_ID = "3f4e292e-7f8a-4ffe-831f-1953be0fe448"
CKAN_URL = "https://opendata.transport.vic.gov.au/api/3/action/package_show?id=" + DATASET_ID
REALTIME_BASE = "https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/"
TERMS_URL = "https://discover.data.vic.gov.au/dataset/gtfs-realtime"
DEFAULT_USER = "StrausbergAutomationWorks/saw-livetrack"
ATTRIBUTION = "Data: Transport Victoria (opendata.transport.vic.gov.au), CC BY 4.0"
TIME_ZONE = "Australia/Melbourne"

INTERCITY = "intercity"
BUS = "bus"
CONSUMERS = (INTERCITY, BUS)

# The outer zip's folder for each consumer: V/Line trains; V/Line coaches.
FOLDERS = {INTERCITY: "1", BUS: "5"}
# The realtime path for each consumer. V/Line's coaches' positions are not measured: none yet.
FEEDS = {INTERCITY: "vline"}

# A 30 s poll (Lee 2026-11-01, decision 12) always reads fresh. Documented limit 20-27 calls a minute, cache 30 s.
FEED_TTL_S = 25.0
STATIC_TTL_S = 24 * 3600.0
STATIC_RETRY_S = 6 * 3600.0
# Folder 5 was 108.8 MB on 2026-10-16.
MAX_FOLDER_BYTES = 128 * 1024 * 1024
_TAIL = 65536
_CHUNK = 1024 * 1024
_EOCD = b"PK\x05\x06"


def _check_consumer(consumer):
    if consumer not in CONSUMERS:
        raise ValueError("unknown consumer %r" % (consumer,))


def _decode(raw, url):
    from .gtfs.realtime import decode
    feed = decode(raw)
    if feed is None:
        raise FeedUnavailable("undecodable body from %s" % url)
    return feed


def parse_package(body):
    """The ZIP resource's url, from a CKAN package_show answer. Raises FeedUnavailable."""
    try:
        resources = json.loads(body)["result"]["resources"]
        urls = [r["url"] for r in resources if str(r.get("format", "")).upper() == "ZIP" and r.get("url")]
    except (ValueError, KeyError, TypeError) as err:
        raise FeedUnavailable("no resources in the package: %s" % type(err).__name__) from None
    if len(urls) != 1:
        raise FeedUnavailable("%d ZIP resources in the package, expected one" % len(urls))
    return urls[0]


def parse_central_directory(tail, tail_start):
    """({member name: (method, crc, compressed size, unpacked size, local header offset)}, (directory offset, size)) from
    the zip's last bytes; the first is None when the directory starts before them. Raises FeedUnavailable on a zip not read
    here (zip64)."""
    e = tail.rfind(_EOCD)
    if e < 0:
        raise FeedUnavailable("no end of central directory in the tail")
    count, size, offset = struct.unpack("<HII", tail[e + 10:e + 20])
    if offset == 0xFFFFFFFF or size == 0xFFFFFFFF or count == 0xFFFF:
        raise FeedUnavailable("zip64 is not read here")
    if offset < tail_start:
        return None, (offset, size)
    cd = tail[offset - tail_start:offset - tail_start + size]
    out, i = {}, 0
    while i + 46 <= len(cd) and cd[i:i + 4] == b"PK\x01\x02":
        method = struct.unpack("<H", cd[i + 10:i + 12])[0]
        crc, csize, usize = struct.unpack("<III", cd[i + 16:i + 28])
        ln, lx, lc = struct.unpack("<HHH", cd[i + 28:i + 34])
        loc = struct.unpack("<I", cd[i + 42:i + 46])[0]
        out[cd[i + 46:i + 46 + ln].decode("utf-8", "replace")] = (method, crc, csize, usize, loc)
        i += 46 + ln + lx + lc
    return out, (offset, size)


def member_for(members, folder):
    """The member holding `folder`, or None: '1/google_transit.zip' for '1'."""
    for name in members:
        if name.strip("/").split("/")[0] == folder and name.endswith(".zip"):
            return name
    return None


class TransportVicClient:
    """Realtime for the consumers with a feed, the Schedule's folder for each consumer.

    `consumers` is {consumer: key or None}. A key is needed only for realtime: for_consumer() raises AuthRefused without
    one. One lock covers fetches. register() and unregister() take a small state lock: they run on the event loop.

    for_consumer(name) -> (positions, trip_updates), GTFS-Realtime FeedMessages as published. trip_updates is None when
    that file failed and the positions did not. A consumer with no realtime feed gets ValueError.

    static_path(name) and static_version(name): the consumer's folder on disk and its ETag. Checked at most daily on its
    own thread; the feeds wait for nothing. A failed fetch keeps the last copy.
    """

    def __init__(self, consumers, user=DEFAULT_USER, timeout=30, opener=None, clock=None, feed_ttl_s=FEED_TTL_S,
                 storage_dir=None, static_timeout=300, background=True):
        if not user or not str(user).strip():
            raise ValueError("a non-empty User-Agent is required")
        self.user = user
        self.timeout = timeout
        self.static_timeout = static_timeout
        self._opener = opener
        self._clock = clock or time.time
        self.feed_ttl_s = float(feed_ttl_s)
        self.storage_dir = storage_dir
        self._background = background
        self._lock = threading.RLock()
        self._state = threading.Lock()
        self._ring = KeyRing(self._clock)
        self._backoff = Backoff(self._clock)
        self._wanted = set()
        self._feeds = {}             # realtime path -> (fetched at, positions, trip updates)
        self._versions = {}          # folder -> etag
        self._static_at = None
        self._static_retry = 0.0
        self._static_loaded = False
        self._loading = False
        self.fetch_count = 0
        self.rate_limit = None
        for consumer, key in dict(consumers).items():
            self.register(consumer, key)

    @property
    def consumers(self):
        with self._state:
            return frozenset(self._wanted)

    def register(self, consumer, key=None):
        """Serve `consumer` from now on. True if it is new. Never waits for a fetch."""
        _check_consumer(consumer)
        if key is not None:
            self._ring.add(consumer, key)
        with self._state:
            if consumer in self._wanted:
                return False
            self._wanted.add(consumer)
            # A folder not yet here makes the next check due.
            if FOLDERS[consumer] not in self._versions:
                self._static_retry = 0.0
            return True

    def unregister(self, consumer):
        """Stop serving `consumer` and forget its key. True if it was registered. Never waits."""
        _check_consumer(consumer)
        self._ring.remove(consumer)
        with self._state:
            if consumer not in self._wanted:
                return False
            self._wanted.discard(consumer)
            return True

    # --- the Schedule -----------------------------------------------------------

    def _folder_paths(self, folder):
        base = os.path.join(self.storage_dir, "saw_transport_vic_%s" % folder)
        return base + ".zip", base + ".json"

    def static_path(self, consumer):
        """The consumer's folder as a zip on disk, or None before one exists."""
        _check_consumer(consumer)
        if self.storage_dir is None:
            return None
        self._load_cached()
        folder = FOLDERS[consumer]
        with self._state:
            have = folder in self._versions
        path = self._folder_paths(folder)[0]
        return path if have and os.path.exists(path) else None

    def static_version(self, consumer):
        """The folder's ETag: it changes when the publication does."""
        _check_consumer(consumer)
        self._load_cached()
        with self._state:
            return self._versions.get(FOLDERS[consumer])

    def _load_cached(self):
        if self.storage_dir is None:
            return
        with self._state:
            if self._static_loaded:
                return
            self._static_loaded = True
        for folder in set(FOLDERS.values()):
            zp, meta = self._folder_paths(folder)
            try:
                with open(meta, encoding="utf-8") as fh:
                    d = json.load(fh)
            except (OSError, ValueError):
                continue
            if os.path.exists(zp) and d.get("etag"):
                with self._state:
                    self._versions[folder] = d["etag"]
                    self._static_at = max(self._static_at or 0.0, float(d.get("checked_at") or 0.0))

    def _static_due(self, now):
        with_all = all(FOLDERS[c] in self._versions for c in self._wanted)
        fresh = with_all and self._static_at is not None and now - self._static_at < STATIC_TTL_S
        return not fresh and now >= self._static_retry

    def refresh_static(self, force=False):
        """Check and if needed fetch the registered consumers' folders on THIS thread; never raises. True when one
        changed."""
        if self.storage_dir is None:
            return False
        self._load_cached()
        with self._state:
            now = self._clock()
            if (not force and not self._static_due(now)) or self._loading:
                return False
            self._loading = True
            self._static_retry = now + STATIC_RETRY_S
            folders = sorted({FOLDERS[c] for c in self._wanted})
        try:
            return self._refresh(folders, now)
        except Exception:  # noqa: BLE001 - keep the last copy; the feeds do not need it
            return False
        finally:
            with self._state:
                self._loading = False

    def _get(self, url, headers=None, method="GET", max_bytes=None):
        self.fetch_count += 1
        return fetch(url, {"User-Agent": self.user, **(headers or {})}, self.timeout, self._opener, method=method,
                     max_bytes=max_bytes)

    def _open_range(self, url, start, end, etag):
        """An open response for bytes start..end. Refuses anything but a 206 with this ETag, unread."""
        self.fetch_count += 1
        req = urllib.request.Request(url, headers={"User-Agent": self.user, "Range": "bytes=%d-%d" % (start, end)})
        open_fn = self._opener or urllib.request.urlopen
        try:
            resp = open_fn(req, timeout=self.static_timeout)
        except Exception as err:  # noqa: BLE001 - transport of any kind
            raise FeedUnavailable("%s from %s" % (type(err).__name__, url)) from None
        status = getattr(resp, "status", None)
        got = resp.headers.get("ETag")
        if status != 206:
            resp.close()
            raise FeedUnavailable("HTTP %s to a Range request; not read" % status)
        if got != etag:
            resp.close()
            raise FeedUnavailable("the file changed during the fetch")
        return resp

    def _range_bytes(self, url, start, end, etag):
        resp = self._open_range(url, start, end, etag)
        try:
            return resp.read(end - start + 2)
        except Exception as err:  # noqa: BLE001
            raise FeedUnavailable("%s from %s" % (type(err).__name__, url)) from None
        finally:
            resp.close()

    def _refresh(self, folders, now):
        url = parse_package(self._get(CKAN_URL, max_bytes=4 * 1024 * 1024)[0])
        _, head = self._get(url, method="HEAD")
        etag = head.get("ETag")
        try:
            size = int(head.get("Content-Length"))
        except (TypeError, ValueError):
            raise FeedUnavailable("no size for the timetable") from None
        if not etag:
            raise FeedUnavailable("no ETag for the timetable")
        with self._state:
            todo = [f for f in folders if self._versions.get(f) != etag or not os.path.exists(self._folder_paths(f)[0])]
        changed = False
        if todo:
            start = max(0, size - _TAIL)
            tail = self._range_bytes(url, start, size - 1, etag)
            members, where = parse_central_directory(tail, start)
            if members is None:
                tail = self._range_bytes(url, where[0], where[0] + where[1] - 1 + 22, etag)
                members, _ = parse_central_directory(tail, where[0])
            for folder in todo:
                self._fetch_folder(url, etag, members, folder, now)
                changed = True
        for folder in folders:
            self._write_meta(self._folder_paths(folder)[1], etag, now)
        with self._state:
            self._static_at = now
        return changed

    def _fetch_folder(self, url, etag, members, folder, now):
        name = member_for(members, folder)
        if name is None:
            raise FeedUnavailable("no folder %s in the timetable" % folder)
        method, crc, csize, usize, off = members[name]
        if csize > MAX_FOLDER_BYTES or usize > MAX_FOLDER_BYTES:
            raise FeedUnavailable("folder %s is over %d bytes" % (folder, MAX_FOLDER_BYTES))
        if method not in (0, 8):
            raise FeedUnavailable("folder %s has compression method %s" % (folder, method))
        lh = self._range_bytes(url, off, off + 29, etag)
        if lh[:4] != b"PK\x03\x04" or len(lh) < 30:
            raise FeedUnavailable("no local header for folder %s" % folder)
        ln, lx = struct.unpack("<HH", lh[26:30])
        start = off + 30 + ln + lx
        zp, _ = self._folder_paths(folder)
        tmp = zp + ".tmp"
        resp = self._open_range(url, start, start + csize - 1, etag)
        try:
            inflate = zlib.decompressobj(-15) if method == 8 else None
            seen, total, got = 0, 0, 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > csize:
                        raise FeedUnavailable("more bytes than the member holds")
                    data = inflate.decompress(chunk) if inflate else chunk
                    seen = zlib.crc32(data, seen)
                    total += len(data)
                    out.write(data)
                if inflate:
                    data = inflate.flush()
                    seen = zlib.crc32(data, seen)
                    total += len(data)
                    out.write(data)
            if seen != crc or total != usize:
                raise FeedUnavailable("folder %s failed its CRC" % folder)
            with zipfile.ZipFile(tmp) as z:
                if not {"routes.txt", "trips.txt", "stop_times.txt", "stops.txt"} <= set(z.namelist()):
                    raise FeedUnavailable("folder %s lacks a required file" % folder)
            os.replace(tmp, zp)
            self._write_meta(self._folder_paths(folder)[1], etag, now)
        except KeyedError:
            raise
        except Exception as err:  # noqa: BLE001 - a bad body of any kind
            raise FeedUnavailable("%s fetching folder %s" % (type(err).__name__, folder)) from None
        finally:
            resp.close()
            if os.path.exists(tmp):
                os.remove(tmp)
        with self._state:
            self._versions[folder] = etag

    @staticmethod
    def _write_meta(meta, etag, now):
        tmp = meta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"etag": etag, "checked_at": now}, fh)
        os.replace(tmp, meta)

    def _ensure_static(self):
        if self.storage_dir is None:
            return
        self._load_cached()
        with self._state:
            due = self._static_due(self._clock()) and not self._loading
        if not due:
            return
        if not self._background:
            self.refresh_static()
            return
        threading.Thread(target=self.refresh_static, name="saw_transport_vic_timetable", daemon=True).start()

    # --- realtime ---------------------------------------------------------------

    def for_consumer(self, consumer, force=False):
        _check_consumer(consumer)
        if consumer not in self.consumers:
            raise ValueError("consumer %r is not registered with this client" % (consumer,))
        path = FEEDS.get(consumer)
        if path is None:
            raise ValueError("consumer %r has no realtime feed" % (consumer,))
        self._ensure_static()
        with self._lock:
            now = self._clock()
            held = self._feeds.get(path)
            if not force and held is not None and now - held[0] < self.feed_ttl_s:
                return held[1], held[2]
            base = REALTIME_BASE + path + "/"
            url = base + "vehicle-positions"
            self._backoff.check(url)
            key = self._ring.active()
            hdr = {"KeyId": key}
            try:
                raw, got = self._get(url, hdr)
            except RateLimited as err:
                self._backoff.hit(err)
                raise
            except KeyRefused:
                self._positions_refused(base + "trip-updates", hdr, key)
                raise
            self._ring.ok()
            self._note_rate_limit(got)
            vp = _decode(raw, url)
            url = base + "trip-updates"
            try:
                tu = _decode(self._get(url, hdr)[0], url)
            except RateLimited as err:
                self._backoff.hit(err)
                tu = None
            except KeyedError:
                tu = None
            self._feeds[path] = (now, vp, tu)
            return vp, tu

    def _positions_refused(self, url, hdr, key):
        """Counts against the key only if the other feed refuses it too (design section 2)."""
        try:
            self._get(url, hdr)
        except KeyRefused:
            if self._ring.refused(key):
                self._ring.active()     # AuthRefused now, when that was the last key
        except KeyedError:
            pass
        else:
            self._ring.ok()

    def _note_rate_limit(self, headers):
        """The source's X-Rate-Limit, for stats() only: [{"window":24,"type":"throttle","remaining":19}, ...]."""
        try:
            self.rate_limit = json.loads(headers.get("X-Rate-Limit"))
        except (AttributeError, TypeError, ValueError):
            self.rate_limit = None

    def stats(self):
        """Low-cardinality state for a diagnostic. Never a key."""
        with self._state:
            versions, loading = dict(self._versions), self._loading
        return {"folders": sorted(versions), "timetable_loading": loading, "fetch_count": self.fetch_count,
                "rate_limit": self.rate_limit, "backing_off": self._clock() < self._backoff.until, **self._ring.state()}
