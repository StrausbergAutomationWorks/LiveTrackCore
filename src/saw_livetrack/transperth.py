"""Shared Transperth / PTA Western Australia timetable client for the Live Track family (0.2.40).

Hoisted at first use, 2026-11-13 (Lee's rule of 2026-10-20: a feed already known to carry several integrations'
vehicles gets a shared client before its first use; 35_Dependencies/README.md). Pure Python: NO homeassistant imports
(05_SHARED_LESSONS.md B8.1a). One copy of the timetable per Home Assistant through the shared-coordinator pattern
(reference/shared_transperth.py, B16). Record: 35_Dependencies/third_party/transperth/SSOT.md.

PERTH PUBLISHES NO REALTIME (the Spatial Data Access page, read 2026-11-13). The one dataset is the static GTFS, one zip
for Transperth (trains, buses, ferry), Transwa (regional trains and coaches) and the regional town buses. So every
vehicle is drawn from its timetable (scheduled_runs below), as Live Track Commuter Rail draws Cork's trains.

Terms: the PTA License Agreement (not CC BY): use, reproduce, redistribute; whoever passes the data on must state that it
"is available free of charge from www.transperth.wa.gov.au" (ATTRIBUTION, verbatim in substance); no PTA trademark is
used in association with the data (so nothing here names an entry after one). Keyless.

THE ZIP (MEASURED 2026-11-13: 26.9 MB, Last-Modified and an ETag; shapes.txt 78 MB and stop_times.txt 76 MB unpacked).
Kept on disk (.storage/saw_transperth_gtfs.zip), checked daily with a HEAD, downloaded again only when Last-Modified
changes. Quirks handled here once, for every consumer: stops.txt's header carries a space after each comma (cut() writes
every file's header and cells stripped); no trip_short_name (a run is keyed by its trip); times past 24:00; Western
Australia has no daylight saving (UTC+8 all year, TZ).

WHO DRAWS WHAT (consumer_of: the route's agency and route_type; agency.txt and routes.txt):

    commuter   Transperth's trains (agency WES-RAI, route_type 2) -> Live Track Commuter Rail (Lee 2026-11-13)
    intercity  Transwa's trains (TRA-TRA, 2) -> Live Track Intercity Rail (candidate)
    transit    Transperth's buses (3) and ferry (4) -> Live Track Transit (candidate)
    bus        Transwa's coaches and the regional town buses (3) -> not decided

TIMETABLE-DRIVEN RUNS (scheduled_runs): every trip of the consumer the timetable runs yesterday or today, its calls'
aimed times placed with saw_livetrack.timetable (call_times, place): standing at its origin from PRE_DEPARTURE_S before
it leaves, standing at a call between its arrival and departure, running between calls, kept at its last call
FINISHED_S after arriving. No delay is ever known.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import zipfile

from . import timetable as _tt

CONTRACT_VERSION = 1
SHARED_KEY = "saw_transperth_v%d" % CONTRACT_VERSION

STATIC_URL = "https://www.transperth.wa.gov.au/TimetablePDFs/GoogleTransit/Production/google_transit.zip"
TERMS_URL = "https://www.transperth.wa.gov.au/About/Spatial-Data-Access"
DEFAULT_USER = "StrausbergAutomationWorks/saw-livetrack"
# The statement the PTA License Agreement requires of whoever passes the data on.
ATTRIBUTION = "Timetable data available free of charge from www.transperth.wa.gov.au"
# Western Australia: UTC+8 all year (no daylight saving since 2009).
TZ = dt.timezone(dt.timedelta(hours=8))

COMMUTER = "commuter"
INTERCITY = "intercity"
TRANSIT = "transit"
BUS = "bus"
CONSUMERS = (COMMUTER, INTERCITY, TRANSIT, BUS)

RAIL_AGENCY = "WES-RAI"          # Transperth's trains
REGIONAL_AGENCY = "TRA-TRA"      # Transwa
CITY_NAME = "Transperth"         # agency_name of every Transperth contract (19 agency ids, measured 2026-11-13)

STATIC_TTL_S = 24 * 3600.0
STATIC_RETRY_S = 3600.0
STATIC_MAX_BYTES = 96 * 1024 * 1024
STATIC_NAME = "saw_transperth_gtfs"
REQUIRED = frozenset({"agency.txt", "routes.txt", "trips.txt", "stop_times.txt", "stops.txt", "shapes.txt"})
_CHUNK = 1024 * 1024

PRE_DEPARTURE_S = 300.0
FINISHED_S = 120.0
TIMETABLE_VERSION = 1
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class TransperthError(Exception):
    """Base error for this client."""


class FeedUnavailable(TransperthError):
    """Transport, HTTP or file failure. Distinct from an empty timetable (C2)."""


def _check_consumer(consumer):
    if consumer not in CONSUMERS:
        raise ValueError("unknown consumer %r" % (consumer,))


def consumer_of(agency_id, agency_name, route_type):
    """Who draws a route: COMMUTER, INTERCITY, TRANSIT, BUS or None (module docstring)."""
    def s(v):
        return v.strip() if isinstance(v, str) else ""
    aid, name, rt = s(agency_id), s(agency_name), s(route_type)
    if rt == "2":
        return COMMUTER if aid == RAIL_AGENCY else INTERCITY if aid == REGIONAL_AGENCY else None
    if rt == "4":
        return TRANSIT if name == CITY_NAME else None
    if rt == "3":
        return TRANSIT if name == CITY_NAME else BUS if aid else None
    return None


# --------------------------------------------------------------------------
# Reading the zip
# --------------------------------------------------------------------------

def _rows(z, name):
    """Stripped dict rows of `name` (header names and cells), nothing when absent."""
    if name not in z.namelist():
        return
    with z.open(name) as fh:
        rd = csv.reader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline=""))
        try:
            head = [h.strip() for h in next(rd)]
        except StopIteration:
            return
        for row in rd:
            yield dict(zip(head, (c.strip() for c in row)))


def route_ids(raw_or_zip, consumer):
    """{route_id} of `consumer`'s routes."""
    _check_consumer(consumer)
    z = raw_or_zip if isinstance(raw_or_zip, zipfile.ZipFile) else zipfile.ZipFile(io.BytesIO(raw_or_zip))
    agencies = {r.get("agency_id", ""): r.get("agency_name", "") for r in _rows(z, "agency.txt")}
    out = set()
    for r in _rows(z, "routes.txt"):
        aid = r.get("agency_id", "")
        if r.get("route_id") and consumer_of(aid, agencies.get(aid, ""), r.get("route_type")) == consumer:
            out.add(r["route_id"])
    return out


def _write(out, name, head, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(head)
    w.writerows(rows)
    out.writestr(name, buf.getvalue())


def _cut_file(z, out, name, col, allowed, collect=None):
    """Copy `name` stripped, keeping rows whose `col` is in `allowed` (every row when allowed is None); the values of
    `collect` in the kept rows."""
    got = set()
    if name not in z.namelist():
        return got
    with z.open(name) as fh:
        rd = csv.reader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline=""))
        try:
            head = [h.strip() for h in next(rd)]
        except StopIteration:
            out.writestr(name, b"")
            return got
        i = head.index(col) if col in head else None
        j = head.index(collect) if collect in head else None
        kept = []
        for row in rd:
            row = [c.strip() for c in row]
            if allowed is not None and (i is None or len(row) <= i or row[i] not in allowed):
                continue
            kept.append(row)
            if j is not None and len(row) > j and row[j]:
                got.add(row[j])
    _write(out, name, head, kept)
    return got


def cut(raw, consumer):
    """The zip with only `consumer`'s routes, their trips, stop times, stops (and parent stations), shapes and
    services; every file's header and cells stripped (stops.txt's spaced header). Blocking: reads the 76 MB
    stop_times.txt once."""
    _check_consumer(consumer)
    z = zipfile.ZipFile(io.BytesIO(raw))
    routes = route_ids(z, consumer)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        agencies = _cut_file(z, out, "routes.txt", "route_id", routes, "agency_id")
        _cut_file(z, out, "agency.txt", "agency_id", agencies)
        trips, shapes, services = set(), set(), set()
        for r in _rows(z, "trips.txt"):
            if r.get("route_id") in routes and r.get("trip_id"):
                trips.add(r["trip_id"])
                if r.get("shape_id"):
                    shapes.add(r["shape_id"])
                if r.get("service_id"):
                    services.add(r["service_id"])
        _cut_file(z, out, "trips.txt", "trip_id", trips)
        used = _cut_file(z, out, "stop_times.txt", "trip_id", trips, "stop_id")
        parents = {r["parent_station"] for r in _rows(z, "stops.txt")
                   if r.get("stop_id") in used and r.get("parent_station")}
        _cut_file(z, out, "stops.txt", "stop_id", used | parents)
        _cut_file(z, out, "shapes.txt", "shape_id", shapes)
        _cut_file(z, out, "calendar.txt", "service_id", services)
        _cut_file(z, out, "calendar_dates.txt", "service_id", services)
    return buf.getvalue()


def local_day(now):
    """The Western Australian date at epoch `now`."""
    return dt.datetime.fromtimestamp(now, TZ).date()


def service_base(day):
    """The GTFS service day's zero in Perth (saw_livetrack.timetable.service_base)."""
    return _tt.service_base(day, TZ)


def _gtfs_seconds(text):
    """'25:10:00' -> 90600; None when empty or not a time."""
    try:
        h, m, s = (text or "").strip().split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except ValueError:
        return None


def _service_days(z, dates):
    """service_id -> the ISO dates among `dates` it runs on: calendar.txt, then calendar_dates.txt (1 adds, 2 removes)."""
    days = {}
    for r in _rows(z, "calendar.txt"):
        try:
            start = dt.datetime.strptime(r.get("start_date", ""), "%Y%m%d").date()
            end = dt.datetime.strptime(r.get("end_date", ""), "%Y%m%d").date()
        except ValueError:
            continue
        days[r.get("service_id", "")] = {d.isoformat() for d in dates
                                         if start <= d <= end and r.get(_WEEKDAYS[d.weekday()]) == "1"}
    wanted = {d.strftime("%Y%m%d"): d.isoformat() for d in dates}
    for r in _rows(z, "calendar_dates.txt"):
        iso = wanted.get(r.get("date", ""))
        if iso is None:
            continue
        sid, kind = r.get("service_id", ""), r.get("exception_type", "")
        if kind == "1":
            days.setdefault(sid, set()).add(iso)
        elif kind == "2" and sid in days:
            days[sid].discard(iso)
    return days


def read_timetable(raw, consumer, today):
    """The zip (whole or cut) -> what scheduled_runs needs for `consumer`, JSON-ready:

        stops       stop_id -> [lat, lon]
        stop_names  stop_id -> name (the parent station's where there is one; " Stn" kept, as Transperth prints it)
        routes      route_id -> [long name (else short), colour "#RRGGBB" or ""]
        trips       trip_id -> [route_id, service_id, first departure s, last arrival s, headsign, direction_id]
        calls       trip_id -> [[stop_id, arrival s, departure s], ...] by stop_sequence
        dates       service_id -> [ISO dates it runs], yesterday to six days on from `today`

    A trip needs a shape_id, two calls, a first departure and a last arrival. Blocking."""
    _check_consumer(consumer)
    z = zipfile.ZipFile(io.BytesIO(raw))
    wanted = route_ids(z, consumer)
    stops, names, parent = {}, {}, {}
    for r in _rows(z, "stops.txt"):
        sid = r.get("stop_id", "")
        if not sid:
            continue
        try:
            stops[sid] = [round(float(r["stop_lat"]), 6), round(float(r["stop_lon"]), 6)]
        except (KeyError, ValueError):
            pass
        if r.get("stop_name"):
            names[sid] = r["stop_name"]
        parent[sid] = r.get("parent_station", "")
    names = {s: names.get(parent.get(s) or "") or n for s, n in names.items()}
    routes = {}
    for r in _rows(z, "routes.txt"):
        if r.get("route_id") in wanted:
            colour = r.get("route_color", "")
            routes[r["route_id"]] = [r.get("route_long_name") or r.get("route_short_name") or r["route_id"],
                                     ("#" + colour.upper()) if len(colour) == 6 else ""]
    keep = {}
    for r in _rows(z, "trips.txt"):
        tid = r.get("trip_id", "")
        if tid and r.get("route_id") in wanted and r.get("shape_id"):
            keep[tid] = (r["route_id"], r.get("service_id", ""), r.get("trip_headsign", ""), r.get("direction_id", ""))
    seq = {}
    for r in _rows(z, "stop_times.txt"):
        tid = r.get("trip_id", "")
        if tid not in keep:
            continue
        try:
            n = int(r.get("stop_sequence", ""))
        except ValueError:
            continue
        seq.setdefault(tid, []).append((n, r.get("stop_id", ""), _gtfs_seconds(r.get("arrival_time")),
                                        _gtfs_seconds(r.get("departure_time"))))
    trips, calls = {}, {}
    for tid, rws in seq.items():
        rws.sort(key=lambda x: x[0])
        if len(rws) < 2:
            continue
        dep0 = rws[0][3] if rws[0][3] is not None else rws[0][2]
        arr1 = rws[-1][2] if rws[-1][2] is not None else rws[-1][3]
        if dep0 is None or arr1 is None:
            continue
        rid, sid, head, way = keep[tid]
        trips[tid] = [rid, sid, dep0, arr1, head, way]
        calls[tid] = [[s, a, d] for _, s, a, d in rws]
    window = [today + dt.timedelta(days=i) for i in range(-1, 7)]
    days = _service_days(z, window)
    dates = {s: sorted(days.get(s, ())) for s in {v[1] for v in trips.values()}}
    return {"v": TIMETABLE_VERSION, "consumer": consumer, "stops": stops, "stop_names": names, "routes": routes,
            "trips": trips, "calls": calls, "dates": dates}


# --------------------------------------------------------------------------
# Timetable-driven runs
# --------------------------------------------------------------------------

class Run:
    """One run drawn this poll: `key` "<trip_id>-<day of month>", the trip, `code` its route_id, its service `day`
    and that day's zero `base`, `delay` (always None: Perth publishes none), `times` (saw_livetrack.timetable.call_times
    of its calls, each call a dict with `stop` and aimed times), and where it is: `state` "standing" or "running" at
    `index`. The attributes of saw_livetrack.irish_rail.Run, so a consumer draws both alike."""

    __slots__ = ("key", "trip_id", "code", "day", "base", "delay", "times", "state", "index")

    def __init__(self, key, trip_id, code, day, base, delay, times, state, index):
        self.key, self.trip_id, self.code, self.day, self.base = key, trip_id, code, day, base
        self.delay, self.times, self.state, self.index = delay, times, state, index

    def __repr__(self):
        return "Run(%s %s %s@%d)" % (self.key, self.code, self.state, self.index)


def scheduled_runs(table, now, pre_departure_s=PRE_DEPARTURE_S, finished_s=FINISHED_S):
    """(runs, skipped) at epoch `now` from read_timetable's `table` (module docstring). `skipped` counts, by reason:
    no_timetable, no_times, not_running (out by its times this poll is NOT counted: only a trip inside its window that
    place() still refuses)."""
    skipped = {}
    trips = (table or {}).get("trips") or {}
    if not trips:
        return [], {"no_timetable": 1}
    dates = table.get("dates") or {}
    calls_of = table.get("calls") or {}
    runs = []
    today = local_day(now)
    for day in (today - dt.timedelta(days=1), today):
        iso, base = day.isoformat(), service_base(day)
        for tid, trip in trips.items():
            rid, sid, first, last = trip[0], trip[1], trip[2], trip[3]
            if iso not in dates.get(sid, ()):
                continue
            if now < base + first - pre_departure_s or now > base + last + finished_s:
                continue
            calls = []
            for stop, a, d in calls_of.get(tid) or ():
                calls.append({"stop": stop, "aimed_arrival": base + a if a is not None else None,
                              "aimed_departure": base + d if d is not None else None})
            times = _tt.call_times(calls)
            if len(times) < 2:
                skipped["no_times"] = skipped.get("no_times", 0) + 1
                continue
            where = _tt.place(times, now, pre_departure_s, finished_s)
            if where is None:
                skipped["not_running"] = skipped.get("not_running", 0) + 1
                continue
            runs.append(Run("%s-%d" % (tid, day.day), tid, rid, day, base, None, times, where[0], where[1]))
    return runs, skipped


# --------------------------------------------------------------------------
# The client: the timetable on disk, one copy per Home Assistant
# --------------------------------------------------------------------------

def default_opener(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout)


class TransperthClient:
    """Keeps ONE copy of the PTA's zip on disk for every consumer (B16: budget the IP).

    `consumers` are those this client serves; the shared registration (reference/shared_transperth.py) starts with the
    first integration's and register()s each that joins. With background=True (the default) the zip is checked on its
    own thread, first at the first static_path() call, then daily (a HEAD; downloaded again only when Last-Modified
    changes). static_path() gives the zip's path, static_version() its Last-Modified: a consumer cuts and distills it
    again when that changes. With no storage_dir there is no timetable."""

    def __init__(self, consumers=(), user=DEFAULT_USER, timeout=30, opener=None, clock=None, storage_dir=None,
                 static_timeout=300, background=True):
        if not user or not str(user).strip():
            raise ValueError("a non-empty User-Agent is required")
        for c in consumers:
            _check_consumer(c)
        self.user = user
        self.timeout = timeout
        self.static_timeout = static_timeout
        self._opener = opener or default_opener
        self._clock = clock or time.time
        self.storage_dir = storage_dir
        self._background = background
        self._state = threading.Lock()
        self._wanted = set(consumers)
        self._version = None
        self._static_at = None
        self._static_retry = 0.0
        self._loaded_meta = False
        self._loading = False
        self.fetch_count = 0
        self.downloads = 0
        self.last_error = None

    @property
    def consumers(self):
        with self._state:
            return frozenset(self._wanted)

    def register(self, consumer):
        """Serve `consumer` from now on. True if it is new. Never waits."""
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

    def _paths(self):
        base = os.path.join(self.storage_dir, STATIC_NAME)
        return base + ".zip", base + ".json"

    def _load_meta(self):
        if self.storage_dir is None:
            return
        with self._state:
            if self._loaded_meta:
                return
            self._loaded_meta = True
        zp, meta = self._paths()
        try:
            with open(meta, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return
        if os.path.exists(zp):
            with self._state:
                self._version, self._static_at = d.get("version"), d.get("checked_at")

    def static_path(self):
        """The zip on disk, or None before one exists. Starts a check when one is due."""
        if self.storage_dir is None:
            return None
        self._ensure_static()
        with self._state:
            have = self._version is not None
        path = self._paths()[0]
        return path if have and os.path.exists(path) else None

    def static_version(self):
        """Changes when the timetable does (its Last-Modified)."""
        self._load_meta()
        with self._state:
            return self._version

    def _due(self, now):
        fresh = self._static_at is not None and now - self._static_at < STATIC_TTL_S
        return not fresh and now >= self._static_retry

    def refresh_static(self, force=False):
        """Check and if needed download the zip on THIS thread; never raises. True when it changed."""
        if self.storage_dir is None:
            return False
        self._load_meta()
        with self._state:
            now = self._clock()
            if (not force and not self._due(now)) or self._loading:
                return False
            self._loading = True
            self._static_retry = now + STATIC_RETRY_S
        try:
            changed = self._refresh(now)
            self.last_error = None
            return changed
        except Exception as err:  # noqa: BLE001 - keep the last copy; retried after STATIC_RETRY_S
            self.last_error = type(err).__name__
            return False
        finally:
            with self._state:
                self._loading = False

    def _request(self, method):
        req = urllib.request.Request(STATIC_URL, headers={"User-Agent": self.user}, method=method)
        return self._opener(req, self.timeout if method == "HEAD" else self.static_timeout)

    def _refresh(self, now):
        self.fetch_count += 1
        try:
            with self._request("HEAD") as resp:
                version = resp.headers.get("Last-Modified") or None
        except urllib.error.HTTPError as err:
            raise FeedUnavailable("HTTP %s from %s" % (err.code, STATIC_URL)) from None
        except Exception as err:  # noqa: BLE001
            raise FeedUnavailable("%s from %s" % (type(err).__name__, STATIC_URL)) from None
        zp, meta = self._paths()
        with self._state:
            current = self._version
        if version is not None and version == current and os.path.exists(zp):
            self._write_meta(meta, version, now)
            with self._state:
                self._static_at = now
            return False
        tmp = zp + ".tmp"
        try:
            self._download(tmp)
            with zipfile.ZipFile(tmp) as z:
                if not REQUIRED <= set(z.namelist()):
                    raise FeedUnavailable("timetable zip lacks a required file")
            os.replace(tmp, zp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self.downloads += 1
        version = version or ("downloaded %d" % int(now))
        self._write_meta(meta, version, now)
        with self._state:
            self._version, self._static_at = version, now
        return True

    def _download(self, tmp):
        self.fetch_count += 1
        total = 0
        try:
            with self._request("GET") as resp, open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > STATIC_MAX_BYTES:
                        raise FeedUnavailable("more than %d bytes from %s" % (STATIC_MAX_BYTES, STATIC_URL))
                    out.write(chunk)
        except TransperthError:
            raise
        except urllib.error.HTTPError as err:
            raise FeedUnavailable("HTTP %s from %s" % (err.code, STATIC_URL)) from None
        except Exception as err:  # noqa: BLE001
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
        self._load_meta()
        with self._state:
            due = self._due(self._clock()) and not self._loading
        if not due:
            return
        if not self._background:
            self.refresh_static()
            return
        threading.Thread(target=self.refresh_static, name="saw_transperth_timetable", daemon=True).start()

    def stats(self):
        """Low-cardinality state for a diagnostic."""
        with self._state:
            return {"timetable_version": self._version, "consumers": sorted(self._wanted),
                    "fetch_count": self.fetch_count, "downloads": self.downloads, "last_error": self.last_error}
