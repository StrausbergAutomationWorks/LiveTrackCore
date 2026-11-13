"""Shared Adelaide Metro client (South Australia) for the Live Track family.

Hoisted at birth, 2026-11-13 (Lee's rule of 2026-10-20: a feed already known to carry several integrations' vehicles
gets a shared client before its first use; 35_Dependencies/README.md). Pure Python: NO homeassistant imports
(05_SHARED_LESSONS.md B8.1a). One poller per Home Assistant through the shared-coordinator pattern
(reference/shared_adelaide_metro.py, B16).

Data: Adelaide Metro - Department for Infrastructure and Transport, South Australia, CC BY 4.0 (the developer page,
https://www.adelaidemetro.com.au/developer-info, read 2026-11-13 by Lee: the content and data feeds under CC BY 4.0,
attribution requested as "Adelaide Metro - Department for Infrastructure and Transport, South Australia"; the piping
shrike emblem and trademarks excepted). Keyless. Record: 35_Dependencies/third_party/adelaide-metro/SSOT.md.

    /realtime/vehicle_positions   GTFS-Realtime positions, every mode, documented every 15 s
    /realtime/trip_updates        GTFS-Realtime predictions (arrival.time, never a delay), every 60 s
    /static/latest/version.txt    the timetable's version number ("quickly determine if the timetable has changed")
    /static/latest/google_transit.zip  the timetable, every mode, about 15 MB

THE FEED CARRIES EVERY MODE, so each vehicle goes to ONE consumer (06_MAP_CONTRACT.md D1), by its route's route_type
in the timetable (the positions and the updates carry route_id on every row, MEASURED 2026-11-02 and 2026-11-13):

    commuter   2 rail (Belair, Gawler, Noarlunga/Seaford/Flinders, Outer Harbor/Grange/Port Dock/Osborne lines)
               -> Live Track Commuter Rail (Lee 2026-11-13: "Go")
    transit    0 tram, 3 metro bus, 712 school bus, 4 ferry -> Live Track Transit (school bus and ferry: Lee 2026-11-02)
    bus        701 regional bus and coach -> Live Track Intercity Buses (proposed)

Any other route_type, and a route the timetable does not know, goes to nobody. Until the FIRST timetable attempt ends,
every consumer gets an empty feed (a bus is never drawn as a train); without a timetable, nothing is classified.

FEED QUIRKS handled here once, for every consumer: 0,0 dropped; a vehicle twice in one body kept once (the newer fix); a
row without a trip or a route dropped. Positions are GPS with bearing and speed (MEASURED 2026-11-13 07:3x local: 17
trains, fix age 9-32 s), so nothing else is cleaned here.
"""

from __future__ import annotations

import csv
import io
import threading
import time
import urllib.error
import urllib.request
import zipfile

CONTRACT_VERSION = 1
SHARED_KEY = "saw_adelaide_metro_v%d" % CONTRACT_VERSION

BASE = "https://gtfs.adelaidemetro.com.au/v1"
POSITIONS_URL = BASE + "/realtime/vehicle_positions"
TRIP_UPDATES_URL = BASE + "/realtime/trip_updates"
STATIC_URL = BASE + "/static/latest/google_transit.zip"
VERSION_URL = BASE + "/static/latest/version.txt"
TERMS_URL = "https://www.adelaidemetro.com.au/developer-info"
DEFAULT_USER = "StrausbergAutomationWorks/saw-livetrack"
# The wording the Government of South Australia requests (the developer page), and the licence.
ATTRIBUTION = "Data: Adelaide Metro – Department for Infrastructure and Transport, South Australia, CC BY 4.0"
TIME_ZONE = "Australia/Adelaide"

COMMUTER = "commuter"
TRANSIT = "transit"
BUS = "bus"
CONSUMERS = (COMMUTER, TRANSIT, BUS)

RAIL_TYPES = frozenset({"2"})
TRANSIT_TYPES = frozenset({"0", "3", "4", "712"})
BUS_TYPES = frozenset({"701"})

FEED_TTL_S = 15.0
# version.txt is read at most this often; the zip is downloaded only when the version changed (or none is held).
VERSION_CHECK_S = 3600.0
STATIC_RETRY_S = 3600.0
STATIC_MAX_BYTES = 64 * 1024 * 1024


class AdelaideMetroError(Exception):
    """Base error for this client."""


class RateLimited(AdelaideMetroError):
    """HTTP 429. Back off; NOT 'no vehicles'."""


class FeedUnavailable(AdelaideMetroError):
    """Transport, HTTP or decode failure. Distinct from an empty answer (C2)."""


def _check_consumer(consumer):
    if consumer not in CONSUMERS:
        raise ValueError("unknown consumer %r" % (consumer,))


def consumer_of(route_type):
    """Who draws a vehicle on a route of this route_type: COMMUTER, TRANSIT, BUS, or None."""
    rt = (route_type or "").strip() if isinstance(route_type, str) else ""
    if rt in RAIL_TYPES:
        return COMMUTER
    if rt in TRANSIT_TYPES:
        return TRANSIT
    if rt in BUS_TYPES:
        return BUS
    return None


def route_types(raw):
    """{route_id: route_type} from the timetable zip's routes.txt (bytes)."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    out = {}
    with z.open("routes.txt") as fh:
        for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")):
            rid = (r.get("route_id") or "").strip()
            if rid:
                out[rid] = (r.get("route_type") or "").strip()
    return out


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------

def _get(url, user, timeout, opener=None, max_bytes=None):
    req = urllib.request.Request(url, headers={"User-Agent": user})
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(req, timeout=timeout) as response:
            raw = response.read() if max_bytes is None else response.read(max_bytes + 1)
    except urllib.error.HTTPError as err:
        if err.code == 429:
            raise RateLimited("HTTP 429 from %s" % url) from err
        raise FeedUnavailable("HTTP %s from %s" % (err.code, url)) from err
    except Exception as err:  # noqa: BLE001 - transport of any kind
        raise FeedUnavailable("%s from %s" % (type(err).__name__, url)) from err
    if max_bytes is not None and len(raw) > max_bytes:
        raise FeedUnavailable("more than %d bytes from %s" % (max_bytes, url))
    return raw


def _decode(raw, url):
    from .gtfs.realtime import decode
    feed = decode(raw)
    if feed is None:
        raise FeedUnavailable("undecodable body from %s" % url)
    return feed


def _new_feed(src):
    """An empty FeedMessage carrying `src`'s header."""
    out = type(src)()
    out.header.CopyFrom(src.header)
    return out


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------

class AdelaideMetroClient:
    """Polls Adelaide Metro ONCE for every consumer (B16: budget the IP).

    `consumers` are the consumers this client serves (default: all three, for a standalone client); the shared
    registration (reference/shared_adelaide_metro.py) starts with the first integration's and register()s each that
    joins. Only registered consumers get feeds built; for_consumer() refuses one that is not.

    for_consumer(name) -> (positions, trip_updates): two GTFS-Realtime FeedMessages holding only that consumer's
    vehicles and trips. trip_updates is None when that file failed and the positions did not.

    THE TIMETABLE tells the modes apart. With background=True (the default) it loads on its own thread, first at the
    first snapshot; afterwards version.txt is read at most every VERSION_CHECK_S and the zip downloaded again only when
    the version changed. The zip is kept (static_zip(), static_version()) so a consumer that distills it does not
    download it again, and knows when to distill it again.
    """

    def __init__(self, user=DEFAULT_USER, timeout=30, opener=None, clock=None, feed_ttl_s=FEED_TTL_S,
                 consumers=CONSUMERS, static_timeout=180, background=True):
        if not user or not str(user).strip():
            raise ValueError("a non-empty User-Agent is required")
        self.user = user
        self.timeout = timeout
        self.static_timeout = static_timeout
        self._opener = opener
        self._clock = clock or time.time
        self.feed_ttl_s = float(feed_ttl_s)
        self._background = background
        for c in consumers:
            _check_consumer(c)
        self._lock = threading.RLock()
        self._state = threading.Lock()
        self._wanted = set(consumers)
        self.index = None            # {route_id: route_type}
        self.version = None
        self._raw = None
        self._checked_at = None      # last version.txt read (or zip load)
        self._retry_at = 0.0
        self._tried = False          # the first attempt has ended
        self._loading = False
        self._fetched_at = None
        self._feeds = None
        self.fetch_count = 0
        self.dropped_null = 0
        self.dropped_duplicate = 0
        self.dropped_untripped = 0
        self.unclassified = 0

    @property
    def consumers(self):
        with self._state:
            return frozenset(self._wanted)

    def register(self, consumer):
        """Serve `consumer` from now on. True if it is new. Never waits for a fetch."""
        _check_consumer(consumer)
        with self._state:
            if consumer in self._wanted:
                return False
            self._wanted.add(consumer)
            return True

    def unregister(self, consumer):
        """Stop serving `consumer`. True if it was registered. Never waits."""
        _check_consumer(consumer)
        with self._state:
            if consumer not in self._wanted:
                return False
            self._wanted.discard(consumer)
            return True

    # --- the timetable ------------------------------------------------------

    def _static_due(self, now):
        fresh = self._checked_at is not None and now - self._checked_at < VERSION_CHECK_S
        return not fresh and now >= self._retry_at

    def refresh_static(self, force=False):
        """Read version.txt and, when it changed (or no timetable is held), the zip, on THIS thread; never raises.
        True when a new timetable was loaded."""
        with self._state:
            now = self._clock()
            if not force and not self._static_due(now):
                return False
            if self._loading:
                return False
            self._loading = True
            self._retry_at = now + STATIC_RETRY_S
        loaded = False
        try:
            self.fetch_count += 1
            version = _get(VERSION_URL, self.user, self.timeout, self._opener,
                           max_bytes=1024).decode("utf-8", "replace").strip() or None
            with self._state:
                # version is set only together with the index, so an equal version means a timetable is held.
                same = version is not None and version == self.version
            if same:
                with self._state:
                    self._checked_at = now
            else:
                self.fetch_count += 1
                raw = _get(STATIC_URL, self.user, self.static_timeout, self._opener, max_bytes=STATIC_MAX_BYTES)
                index = route_types(raw)
                with self._state:
                    self.index, self._raw, self.version, self._checked_at = index, raw, version, now
                loaded = True
        except Exception:  # noqa: BLE001 - keep the last timetable; retried after STATIC_RETRY_S
            loaded = False
        finally:
            with self._state:
                self._loading = False
                self._tried = True
        return loaded

    def _ensure_static(self):
        with self._state:
            due = self._static_due(self._clock())
        if not due:
            return
        if not self._background:
            self.refresh_static()
            return
        threading.Thread(target=self.refresh_static, name="saw_adelaide_metro_timetable", daemon=True).start()

    def static_zip(self):
        """The timetable zip the index was built from (bytes), or None before one has loaded."""
        with self._state:
            return self._raw

    def static_version(self):
        """The version.txt of the timetable held, or None. A consumer distills the zip again when this changes."""
        with self._state:
            return self.version

    # --- the feed -----------------------------------------------------------

    def _clean(self, feed):
        """[(entity, vehicle id, trip id, route id)]: 0,0 and rows without a trip or route dropped, one per vehicle
        (the newer fix)."""
        best = {}
        for ent in feed.entity:
            if not ent.HasField("vehicle"):
                continue
            v = ent.vehicle
            if not v.HasField("position"):
                continue
            lat, lon = round(float(v.position.latitude), 6), round(float(v.position.longitude), 6)
            if lat == 0.0 and lon == 0.0:
                self.dropped_null += 1
                continue
            trip = v.trip.trip_id.strip() if v.HasField("trip") else ""
            route = v.trip.route_id.strip() if v.HasField("trip") else ""
            if not trip or not route:
                self.dropped_untripped += 1
                continue
            vid = v.vehicle.id.strip() if v.HasField("vehicle") else ""
            key = vid or trip
            ts = int(v.timestamp) if v.HasField("timestamp") else 0
            prev = best.get(key)
            if prev is not None:
                self.dropped_duplicate += 1
                if ts <= prev[1]:
                    continue
            best[key] = (ent, ts, trip, route)
        return [(ent, key, trip, route) for key, (ent, _, trip, route) in best.items()]

    def snapshot(self, force=False):
        """{consumer: (positions, trip_updates)} for the registered consumers."""
        self._ensure_static()
        with self._lock:
            now = self._clock()
            if (not force and self._feeds is not None and self._fetched_at is not None
                    and now - self._fetched_at < self.feed_ttl_s):
                return self._feeds
            self.fetch_count += 1
            vp = _decode(_get(POSITIONS_URL, self.user, self.timeout, self._opener), POSITIONS_URL)
            try:
                self.fetch_count += 1
                tu = _decode(_get(TRIP_UPDATES_URL, self.user, self.timeout, self._opener), TRIP_UPDATES_URL)
            except AdelaideMetroError:
                tu = None
            with self._state:
                wanted = frozenset(self._wanted)
                index = self.index
            out = {c: (_new_feed(vp), _new_feed(tu) if tu is not None else None) for c in wanted}
            if index is not None:
                owner = {}
                for ent, _, trip, route in self._clean(vp):
                    c = consumer_of(index.get(route))
                    owner[trip] = c
                    if c is None:
                        self.unclassified += 1
                    if c in out:
                        out[c][0].entity.add().CopyFrom(ent)
                if tu is not None:
                    for ent in tu.entity:
                        if not ent.HasField("trip_update"):
                            continue
                        t = ent.trip_update.trip
                        route = t.route_id.strip()
                        c = consumer_of(index.get(route)) if route else owner.get(t.trip_id.strip())
                        if c in out and out[c][1] is not None:
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
        with self._state:
            index, tried, loading, version = self.index, self._tried, self._loading, self.version
        return {"routes": len(index) if index is not None else None,
                "timetable_version": version,
                "timetable": ("loaded" if index is not None else
                              "failed: nothing classified" if tried else
                              "loading: feeds empty" if loading else "not started"),
                "fetch_count": self.fetch_count, "dropped_null": self.dropped_null,
                "dropped_duplicate": self.dropped_duplicate, "dropped_untripped": self.dropped_untripped,
                "unclassified": self.unclassified}
