"""Shared Transport for NSW client (the `nswtrains` feed) for the Live Track family.

Shared from first use (Lee 2026-10-20 01:02: "these feeds are all shared and should be a shared client from the
start"; backlog 319). Design and every decision: 35_Dependencies/ours/saw-amtraker-client/
DESIGN_319_tfnsw_transport_vic_20261102.md. Provider: 35_Dependencies/third_party/tfnsw/SSOT.md. Pure Python: NO
homeassistant imports (05_SHARED_LESSONS.md B8.1a). One poller per Home Assistant (reference/shared_tfnsw.py, B16).

Data: Transport for NSW, CC BY 4.0, attributed as the source. Each user brings their own key. Three endpoints:
    vehiclepos/nswtrains    GTFS-Realtime positions, a new fix about once a minute
    realtime/nswtrains      GTFS-Realtime trip updates
    schedule/nswtrains      the GTFS timetable, about 40 MB. It takes NO Range and sends no ETag: HEAD first, then whole

THE FEED CARRIES THREE INTEGRATIONS' VEHICLES, so each goes to ONE consumer (06_MAP_CONTRACT.md D1):

    intercity   route_type 106, 4T.T.<other>                   regional trains (XPT, Xplorer): Live Track Intercity Rail
    commuter    route_type 100, 4T.T.BMT, HUN, SCO, SHL        NSW's "Intercity" lines' outer ends: Live Track Commuter Rail
    bus         route_type 204, 205, 4T.C.<n>                  NSW TrainLink coaches: Live Track Intercity Buses

Routing needs no timetable (unlike Renfe's): prefix and static agree on all 336 routes, and the prefix also places a route
in no static (4T.C.818, 139 rows). Once a timetable is loaded its route_type wins.

Every rule MEASURED on capture round 1, 2026-10-18 to 10-21, 232,829 rows
(30_Integrations/Live_Track_Intercity_Rail/20_Research/_shared/australia/AU_CAPTURE1_ANALYSIS_20261101.md):

FEED QUIRKS handled here once, for every consumer:
* THE POSITION'S STOP IS NOT THE NEXT STOP. stop_id is the stop the train last LEFT (96% of 35,028 regional-train fixes;
  never ahead of the nearest stop on 100, 106 and 205, 257 of 13,901 on 204), under current_status "in transit to", and
  current_stop_sequence is another numbering from the timetable's. Cleared (Lee 2026-11-02, D2).
* SPEED IS 0.0 on all 232,829 rows, moving or not. Cleared, so nothing publishes it as a speed.
* ONE TRIP ID RUNS ON CONSECUTIVE DATES, and its updates overlap: 474 of 675 sampled bodies hold a trip id under two
  start dates, and anything keyed by trip_id alone lets tomorrow's run overwrite today's (schedule.next_stops named a stop
  over 5 km behind on 94 of 5,431 updates; 1 when cut). A consumer's trip updates hold, for a trip with a position, only
  the run or runs with a position.
* entity.id is no identity: 221 to 337 per trip.
Not needed, measured: 0,0 (none), a trip twice in a body (none), no bearing (none).
`time` of 0 with `delay` set is already handled by schedule._update_time; skipped stops are the consumer's to drop
(gtfs.realtime.drop_skipped); REPLACEMENT stays as sent.
"""

from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import zipfile

from ._keyed import (AuthRefused, Backoff, FeedUnavailable, KeyedError, KeyRefused, KeyRing,  # noqa: F401
                     RateLimited, fetch)

CONTRACT_VERSION = 1
SHARED_KEY = "saw_tfnsw_v%d" % CONTRACT_VERSION

BASE = "https://api.transport.nsw.gov.au/v1/gtfs/"
POSITIONS_URL = BASE + "vehiclepos/nswtrains"
TRIP_UPDATES_URL = BASE + "realtime/nswtrains"
STATIC_URL = BASE + "schedule/nswtrains"
TERMS_URL = "https://opendata.transport.nsw.gov.au/datalicence"
DEFAULT_USER = "StrausbergAutomationWorks/saw-livetrack"
# Text only, no logo on a map marker (Lee 2026-11-02, D4).
ATTRIBUTION = "Data: Transport for NSW (opendata.transport.nsw.gov.au), CC BY 4.0"
TIME_ZONE = "Australia/Sydney"

INTERCITY = "intercity"
COMMUTER = "commuter"
BUS = "bus"
CONSUMERS = (INTERCITY, COMMUTER, BUS)

BY_ROUTE_TYPE = {"106": INTERCITY, "100": COMMUTER, "204": BUS, "205": BUS}
COMMUTER_LINES = frozenset({"BMT", "HUN", "SCO", "SHL"})

# A new fix about once a minute: a 60 s poll (Lee 2026-11-01, decision 12) always reads fresh.
FEED_TTL_S = 55.0
STATIC_TTL_S = 24 * 3600.0
STATIC_RETRY_S = 3600.0
STATIC_MAX_BYTES = 128 * 1024 * 1024
STATIC_NAME = "saw_tfnsw_nswtrains"
_CHUNK = 1024 * 1024


def _check_consumer(consumer):
    if consumer not in CONSUMERS:
        raise ValueError("unknown consumer %r" % (consumer,))


def consumer_of(route_id, route_types=None):
    """Who draws a route: INTERCITY, COMMUTER, BUS, or None. `route_types` is {route_id: route_type} from a loaded
    timetable, and wins where it knows the route."""
    rid = (route_id or "").strip() if isinstance(route_id, str) else ""
    known = (route_types or {}).get(rid)
    if known is not None:
        return BY_ROUTE_TYPE.get(known)
    if rid.startswith("4T.C."):
        return BUS
    if rid.startswith("4T.T."):
        return COMMUTER if rid[5:] in COMMUTER_LINES else INTERCITY
    return None


def _decode(raw, url):
    from .gtfs.realtime import decode
    feed = decode(raw)
    if feed is None:
        raise FeedUnavailable("undecodable body from %s" % url)
    return feed


def _new_feed(src):
    out = type(src)()
    out.header.CopyFrom(src.header)
    return out


def _scrub(vehicle):
    """The fields this feed gets wrong, gone (module docstring)."""
    vehicle.ClearField("stop_id")
    vehicle.ClearField("current_stop_sequence")
    vehicle.ClearField("current_status")
    if vehicle.HasField("position"):
        vehicle.position.ClearField("speed")


def _route_types(path):
    """{route_id: route_type} from a timetable zip on disk."""
    with zipfile.ZipFile(path) as z:
        with z.open("routes.txt") as fh:
            return {(r.get("route_id") or "").strip(): (r.get("route_type") or "").strip()
                    for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
                    if (r.get("route_id") or "").strip()}


class TfnswClient:
    """Polls the feed ONCE for every consumer (B16: budget the IP).

    `consumers` is {consumer: key}. The shared registration (reference/shared_tfnsw.py) starts with the first
    integration's and register()s each that joins. Only registered consumers get feeds built.

    for_consumer(name) -> (positions, trip_updates): two GTFS-Realtime FeedMessages holding only that consumer's vehicles
    and trips, cleaned (module docstring). trip_updates is None when that file failed and the positions did not.

    THE TIMETABLE is kept on disk, not in memory: storage_dir/saw_tfnsw_nswtrains.zip, about 40 MB, read by each consumer
    when static_version() changes. Checked with a HEAD at most daily, on its own thread; the feeds wait for nothing. A
    failed download keeps the last copy. With no storage_dir there is no timetable, and routing is by prefix alone.
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
        for consumer, key in dict(consumers).items():
            _check_consumer(consumer)
            self._ring.add(consumer, key)
            self._wanted.add(consumer)
        self._feeds = None
        self._fetched_at = None
        self._types = None
        self._version = None
        self._static_at = None
        self._static_retry = 0.0
        self._static_loaded = False
        self._loading = False
        self.fetch_count = 0
        self.dropped_other_runs = 0

    @property
    def consumers(self):
        with self._state:
            return frozenset(self._wanted)

    def register(self, consumer, key):
        """Serve `consumer` from now on, with its key. True if it is new. Never waits for a fetch."""
        _check_consumer(consumer)
        self._ring.add(consumer, key)
        with self._state:
            if consumer in self._wanted:
                return False
            self._wanted.add(consumer)
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

    # --- the timetable --------------------------------------------------------

    def _paths(self):
        base = os.path.join(self.storage_dir, STATIC_NAME)
        return base + ".zip", base + ".json"

    def static_path(self):
        """The timetable zip on disk, or None before one exists."""
        if self.storage_dir is None:
            return None
        self._load_cached()
        with self._state:
            have = self._version is not None
        path = self._paths()[0]
        return path if have and os.path.exists(path) else None

    def static_version(self):
        """Changes when the timetable does (its Last-Modified). A consumer distills again when it changes."""
        self._load_cached()
        with self._state:
            return self._version

    def _load_cached(self):
        if self.storage_dir is None:
            return
        with self._state:
            if self._static_loaded:
                return
            self._static_loaded = True
        zp, meta = self._paths()
        try:
            with open(meta, encoding="utf-8") as fh:
                d = json.load(fh)
            types = _route_types(zp)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            return
        with self._state:
            self._version, self._static_at, self._types = d.get("version"), d.get("checked_at"), types

    def _static_due(self, now):
        fresh = self._static_at is not None and now - self._static_at < STATIC_TTL_S
        return not fresh and now >= self._static_retry

    def refresh_static(self, force=False):
        """Check and if needed download the timetable on THIS thread; never raises. True when it changed."""
        if self.storage_dir is None:
            return False
        self._load_cached()
        with self._state:
            now = self._clock()
            if (not force and not self._static_due(now)) or self._loading:
                return False
            self._loading = True
            self._static_retry = now + STATIC_RETRY_S
        try:
            return self._refresh(now)
        except Exception:  # noqa: BLE001 - keep the last copy; the feeds do not need it
            return False
        finally:
            with self._state:
                self._loading = False

    def _headers(self, key):
        return {"User-Agent": self.user, "Authorization": "apikey " + key}

    def _refresh(self, now):
        key = self._ring.active()
        self.fetch_count += 1
        _, got = fetch(STATIC_URL, self._headers(key), self.timeout, self._opener, method="HEAD")
        version = got.get("Last-Modified") or None
        zp, meta = self._paths()
        with self._state:
            current = self._version
        if version is not None and version == current and os.path.exists(zp):
            self._write_meta(meta, version, now)
            with self._state:
                self._static_at = now
            return False
        self.fetch_count += 1
        tmp = zp + ".tmp"
        try:
            self._download(key, tmp)
            with zipfile.ZipFile(tmp) as z:
                names = set(z.namelist())
            if not {"routes.txt", "trips.txt", "stop_times.txt", "stops.txt"} <= names:
                raise FeedUnavailable("timetable zip lacks a required file")
            types = _route_types(tmp)
            os.replace(tmp, zp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self._write_meta(meta, version, now)
        with self._state:
            self._version, self._static_at, self._types = version, now, types
        return True

    def _download(self, key, tmp):
        req = urllib.request.Request(STATIC_URL, headers=self._headers(key))
        open_fn = self._opener or urllib.request.urlopen
        total = 0
        try:
            with open_fn(req, timeout=self.static_timeout) as resp, open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > STATIC_MAX_BYTES:
                        raise FeedUnavailable("more than %d bytes from %s" % (STATIC_MAX_BYTES, STATIC_URL))
                    out.write(chunk)
        except KeyedError:
            raise
        except Exception as err:  # noqa: BLE001 - never with the headers in the message
            raise FeedUnavailable("%s from %s" % (type(err).__name__, STATIC_URL)) from None

    @staticmethod
    def _write_meta(meta, version, now):
        tmp = meta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": version, "checked_at": now}, fh)
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
        threading.Thread(target=self.refresh_static, name="saw_tfnsw_timetable", daemon=True).start()

    # --- the feed ---------------------------------------------------------------

    def _fetch_positions(self):
        self._backoff.check(POSITIONS_URL)
        key = self._ring.active()
        self.fetch_count += 1
        try:
            raw, _ = fetch(POSITIONS_URL, self._headers(key), self.timeout, self._opener)
        except RateLimited as err:
            self._backoff.hit(err)
            raise
        except KeyRefused:
            self._positions_refused(key)
            raise
        self._ring.ok()
        return _decode(raw, POSITIONS_URL), key

    def _positions_refused(self, key):
        """Counts against the key only if the other feed refuses it too (design section 2)."""
        self.fetch_count += 1
        try:
            fetch(TRIP_UPDATES_URL, self._headers(key), self.timeout, self._opener)
        except KeyRefused:
            if self._ring.refused(key):
                self._ring.active()     # AuthRefused now, when that was the last key
        except KeyedError:
            pass
        else:
            self._ring.ok()

    def _fetch_updates(self, key):
        self.fetch_count += 1
        try:
            raw, _ = fetch(TRIP_UPDATES_URL, self._headers(key), self.timeout, self._opener)
            return _decode(raw, TRIP_UPDATES_URL)
        except RateLimited as err:
            self._backoff.hit(err)
            return None
        except KeyedError:
            # Measured: this endpoint alone answered 401 four times with the key good. Not counted against the key.
            return None

    def snapshot(self, force=False):
        """{consumer: (positions, trip_updates)} for the registered consumers."""
        self._ensure_static()
        with self._lock:
            now = self._clock()
            if (not force and self._feeds is not None and self._fetched_at is not None
                    and now - self._fetched_at < self.feed_ttl_s):
                return self._feeds
            vp, key = self._fetch_positions()
            tu = self._fetch_updates(key)
            with self._state:
                wanted = frozenset(self._wanted)
                types = self._types
            out = {c: (_new_feed(vp), _new_feed(tu) if tu is not None else None) for c in wanted}
            owner, runs = {}, {}
            for ent in vp.entity:
                if not ent.HasField("vehicle"):
                    continue
                v = ent.vehicle
                if not v.HasField("trip") or not v.trip.trip_id.strip() or not v.HasField("position"):
                    continue
                c = consumer_of(v.trip.route_id, types)
                tid = v.trip.trip_id.strip()
                owner[tid] = c
                runs.setdefault(tid, set()).add(v.trip.start_date)
                if c in out:
                    e = out[c][0].entity.add()
                    e.CopyFrom(ent)
                    _scrub(e.vehicle)
            if tu is not None:
                for ent in tu.entity:
                    if not ent.HasField("trip_update"):
                        continue
                    trip = ent.trip_update.trip
                    tid = trip.trip_id.strip()
                    c = owner[tid] if tid in owner else consumer_of(trip.route_id, types)
                    if c not in out:
                        continue
                    if tid in runs and trip.start_date not in runs[tid]:
                        self.dropped_other_runs += 1
                        continue
                    out[c][1].entity.add().CopyFrom(ent)
            self._feeds = out
            self._fetched_at = now
            return out

    def for_consumer(self, consumer, force=False):
        _check_consumer(consumer)
        if consumer not in self.consumers:
            raise ValueError("consumer %r is not registered with this client" % (consumer,))
        feeds = self.snapshot(force=force)
        if consumer not in feeds:
            # Registered after this snapshot was built.
            feeds = self.snapshot(force=True)
        return feeds[consumer]

    def stats(self):
        """Low-cardinality state for a diagnostic. Never a key."""
        with self._state:
            version, types, loading = self._version, self._types, self._loading
        return {"timetable": version, "timetable_routes": len(types) if types else None, "timetable_loading": loading,
                "fetch_count": self.fetch_count, "dropped_other_runs": self.dropped_other_runs,
                "backing_off": self._clock() < self._backoff.until, **self._ring.state()}
