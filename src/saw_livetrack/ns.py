"""Shared NS client (the Netherlands) for the Live Track family: NS's Virtual Train positions and SpoorKaart track, with
OVapi's timetable and train updates.

Shared from first use (35_Dependencies/README.md; Lee 2026-10-20: a feed known to carry several integrations' vehicles
is shared from the start). One answer carries two integrations' trains, each to ONE consumer (06_MAP_CONTRACT.md D1),
by the feed's own `type` (Lee 2026-11-11 ~03:00 CST, "Build, ship, publish and deploy"):

    intercity   IC     NS's intercity trains (the NS-run international ones come as IC): Live Track Intercity Rail, `ns`
    commuter    SPR    NS's Sprinters (R-net NS among them): Live Track Commuter Rail, `ns`
    intercity   ARR    every KV6-sourced regional train: Live Track Intercity Rail, one entry per COMPANY, the company
                       from the OVapi timetable's agency for the train number: `arriva` (Arriva, RRReis Arriva,
                       Blauwnet Arriva), `keolis` (Blauwnet Keolis, RRReis Keolis); the brand rides along (Lee 03:1x)

Provider: 35_Dependencies/third_party/ns-api/SSOT.md (no product terms; 300 requests / 5 min documented on the
Reisinformatie API; one subscription per account, so a key per user) and 35_Dependencies/third_party/ovapi/SSOT.md
(the timetable CC0; one server for the timetable and the realtime, so fetch rarely and back off on 429). Measurements:
30_Integrations/Live_Track_Intercity_Rail/20_Research/_shared/netherlands/NS_VIRTUAL_TRAIN_20261110.md section 4.
Pure Python: NO homeassistant imports (05_SHARED_LESSONS.md B8.1a). One poller per Home Assistant (reference/
shared_ns.py, B16).

READS:
* /virtual-train-api/vehicle (the user's key): every train's position, speed (km/h) and heading. NO fix time, by design
  (the schema has no time field). A moving train's position changes every ~10 s (p50; NS note 4.5).
* /Spoorkaart-API/api/v1/spoorkaart (the user's key): NS's track as 759 lines, each between two ADJACENT stations,
  `from` and `to` in NS station codes. Kept on disk daily (.storage/saw_ns_spoorkaart.json).
* OVapi's timetable gtfs-nl.zip (keyless, ~166 MB): fetched at most weekly with If-None-Match / If-Modified-Since on
  its own thread, cut to rail as it is read, kept on disk distilled (.storage/saw_ovapi_nl_rail.json.gz); the zip is
  deleted once read. Names each train's trip by `trip_short_name` = `treinNummer` (212 of 214; NS note 4.2): its
  agency (so its company), its calls, its line.
* OVapi's trainUpdates.pb (keyless, ~3.7 MB): every UPDATES_TTL_S, conditional, on its own thread (Lee 03:2x: delays
  from OVapi, one pull per install every 5 minutes, backing off on 429).

FIX TIME, option B (Lee 2026-11-11 02:52, "Use B"; 06_MAP_CONTRACT.md, the observed_at rule under D2): `observed_at` is
the poll time at which this client FIRST saw a position different from the one before (a train seen for the first
time counts as seen then), with `observed_at_source` "first_seen"; an unchanged position keeps its first-seen time,
never re-stamped. Kept HERE, once, so every consumer publishes the same time for the same fix.

ACCURACY: `horizontaleNauwkeurigheid` is NOT metres of error (fixes p50 4-5 m from the track against reported p50
350-467; NS note 4.5): never published, never used to snap (Lee).

THE LINE: a trip's stops, each placed on the nearest SpoorKaart station (OVapi's stops carry no station code), joined
by the shortest path through SpoorKaart's station graph; the path's stations in order are the train's `path`, and
each consecutive pair's SpoorKaart line is one leg (pair_lines()).

A KEY IS NEVER PRINTED (saw_livetrack._keyed).
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import heapq
import io
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
import zipfile

from ._keyed import (AuthRefused, Backoff, FeedUnavailable, KeyedError, KeyRefused, KeyRing,  # noqa: F401
                     RateLimited, fetch)

CONTRACT_VERSION = 1
SHARED_KEY = "saw_ns_v%d" % CONTRACT_VERSION

GATEWAY = "https://gateway.apiportal.ns.nl"
VEHICLE_URL = GATEWAY + "/virtual-train-api/vehicle"
SPOORKAART_URL = GATEWAY + "/Spoorkaart-API/api/v1/spoorkaart"
TIMETABLE_URL = "https://gtfs.ovapi.nl/nl/gtfs-nl.zip"
TRAIN_UPDATES_URL = "https://gtfs.ovapi.nl/nl/trainUpdates.pb"
KEY_URL = "https://apiportal.ns.nl/"
TERMS_URL = "https://apiportal.ns.nl/"
OVAPI_URL = "https://gtfs.ovapi.nl/"
DEFAULT_USER = "StrausbergAutomationWorks/saw-livetrack"
ATTRIBUTION = "Data: NS (apiportal.ns.nl); timetable and delays: OVapi (gtfs.ovapi.nl), CC0"
TIME_ZONE = "Europe/Amsterdam"

INTERCITY = "intercity"
COMMUTER = "commuter"
CONSUMERS = (INTERCITY, COMMUTER)
TYPE_CONSUMER = {"IC": INTERCITY, "SPR": COMMUTER, "ARR": INTERCITY}

OP_NS = "ns"
OP_ARRIVA = "arriva"
OP_KEOLIS = "keolis"
OPERATORS = (OP_NS, OP_ARRIVA, OP_KEOLIS)
# Which company runs a regional train (Lee 2026-11-11 03:1x: one entry per company). MEASURED 03:3x CST: OVapi's
# gtfs-nl.zip names the BRAND as the agency (ARR Arriva, BN Blauwnet, RRREIS RRReis), Blauwnet and RRReis each run by
# both companies, while NS's DVS record names the company: `vervoerder` "Blauwnet A", "Blauwnet K", "RRReis A",
# "Arriva" (17 of 17 ARR trains, one batched /api/v1/trein?ids= read). So the company is DVS's, read once per train
# number and day (CARRIER_TTL_S); where DVS has not answered, by the brand's routes: Keolis runs IC23, RS22 and RS23
# (Blauwnet) and RS34 (RRReis, the Valleilijn), Arriva every other (the timetable's route names, 2026-11-11, and the
# DVS answers agree: 7927, 7934, 7936, 17927 Blauwnet K on Zwolle-Enschede). The EU mirror's agencies (IFF:..., NS note
# 4.4) name the company themselves and are kept.
VERVOERDER = {"arriva": OP_ARRIVA, "blauwnet a": OP_ARRIVA, "rrreis a": OP_ARRIVA,
              "blauwnet k": OP_KEOLIS, "rrreis k": OP_KEOLIS}
BRAND_AGENCIES = {"ARR": "Arriva", "BN": "Blauwnet", "RRREIS": "RRReis"}
KEOLIS_ROUTES = frozenset({("BN", "IC23"), ("BN", "RS22"), ("BN", "RS23"), ("RRREIS", "RS34")})
NS_AGENCIES = frozenset({"NS", "NSI", "RNET", "IFF:NS", "IFF:NS_INT", "IFF:R-NET_NS"})
AGENCIES = {
    "IFF:ARRIVA": (OP_ARRIVA, "Arriva"),
    "IFF:RRREIS_A": (OP_ARRIVA, "RRReis"),
    "IFF:BLAUWNET_A": (OP_ARRIVA, "Blauwnet"),
    "IFF:BLAUWNET_K": (OP_KEOLIS, "Blauwnet"),
    "IFF:RRREIS_K": (OP_KEOLIS, "RRReis"),
}
CARRIER_URL = GATEWAY + "/virtual-train-api/api/v1/trein?ids="
CARRIER_TTL_S = 6 * 3600.0
CARRIER_RETRY_S = 120.0
CARRIER_BATCH = 40

# Two consumers poll at their own intervals (15 s and up): one fetch serves every poll within this.
FEED_TTL_S = 9.0
UPDATES_TTL_S = 300.0
UPDATES_HOLD_S = 900.0
SPOORKAART_TTL_S = 24 * 3600.0
SPOORKAART_RETRY_S = 3600.0
TIMETABLE_TTL_S = 7 * 24 * 3600.0
TIMETABLE_RETRY_S = 6 * 3600.0
TIMETABLE_MAX_BYTES = 400 * 1024 * 1024
SPOORKAART_NAME = "saw_ns_spoorkaart.json"
TIMETABLE_NAME = "saw_ovapi_nl_rail"
DISTILL_V = 1
SPOOR_V = 1
MAX_BYTES = 16 * 1024 * 1024
_CHUNK = 1024 * 1024
# A train unseen this long is forgotten (its first-seen state goes).
FORGET_S = 900.0
# A stop is placed on a SpoorKaart station within this.
STATION_MATCH_M = 800.0
# A train whose fix is further than this from every station of its trip's path is not that trip (NS note 4.2: one IC
# was 94 km from its own number's shape).
TRIP_MATCH_M = 25000.0
# A trip is a candidate from this long before its first departure to this long after its last arrival.
TRIP_PRE_S = 1800.0
TRIP_POST_S = 1800.0
# A path between two stops longer than this many times the straight line, plus PATH_SLACK_M, is not believed.
PATH_FACTOR = 2.5
PATH_SLACK_M = 20000.0
# A stop whose time is this far past, with no expected time saying otherwise, is behind the train.
NEXT_GRACE_S = 60.0
RAIL_ROUTE_TYPES = frozenset({"2"} | {str(t) for t in range(100, 118)})


def _check_consumer(consumer):
    if consumer not in CONSUMERS:
        raise ValueError("unknown consumer %r" % (consumer,))


def metres(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(a)))


def pair_key(a, b):
    lo, hi = sorted((a, b))
    return "%s|%s" % (lo, hi)


class _DutchTime(dt.tzinfo):
    """The Netherlands without an IANA database: CET, CEST (UTC+2) from 01:00 UTC on the last Sunday of March to 01:00
    UTC on the last Sunday of October (the EU's summer-time rule)."""

    @staticmethod
    def _last_sunday(year, month):
        d = dt.date(year, month, 31)
        return d - dt.timedelta(days=(d.weekday() + 1) % 7)

    def _summer(self, utc):
        y = utc.year
        start = dt.datetime.combine(self._last_sunday(y, 3), dt.time(1))
        end = dt.datetime.combine(self._last_sunday(y, 10), dt.time(1))
        return start <= utc.replace(tzinfo=None) < end

    def utcoffset(self, d):
        if d is None:
            return dt.timedelta(hours=1)
        # Local wall time: guess with CET, then check.
        guess = d.replace(tzinfo=None) - dt.timedelta(hours=1)
        if self._summer(guess) and self._summer(guess - dt.timedelta(hours=1)):
            return dt.timedelta(hours=2)
        return dt.timedelta(hours=1)

    def dst(self, d):
        return self.utcoffset(d) - dt.timedelta(hours=1)

    def tzname(self, d):
        return "CEST" if self.dst(d) else "CET"

    def fromutc(self, d):
        utc = d.replace(tzinfo=None)
        off = dt.timedelta(hours=2 if self._summer(utc) else 1)
        return (utc + off).replace(tzinfo=self)


def zone():
    """Europe/Amsterdam from the IANA database, else the EU rule (_DutchTime)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TIME_ZONE)
    except Exception:  # noqa: BLE001 - no IANA database here
        return _DutchTime()


def service_base(day, tz):
    """The GTFS service day's zero: noon less 12 h, local (right across a clock change)."""
    return dt.datetime(day.year, day.month, day.day, 12, tzinfo=tz).timestamp() - 12 * 3600


# --- the vehicle feed ---------------------------------------------------------------------------------------------


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse_vehicles(raw):
    """/virtual-train-api/vehicle -> [{"number", "type", "lat", "lon", "speed_kmh", "heading"}]. A record with no
    number or no position is left out; a number twice keeps the first. Raises FeedUnavailable on a body that is not
    the expected JSON."""
    try:
        d = json.loads(raw)
        rows = d["payload"]["treinen"]
    except (ValueError, TypeError, KeyError) as err:
        raise FeedUnavailable("vehicle answer not understood (%s)" % type(err).__name__) from None
    if not isinstance(rows, list):
        raise FeedUnavailable("vehicle answer not understood (treinen)")
    out, seen = [], set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        n = r.get("treinNummer", r.get("ritId"))
        number = str(n).strip() if n is not None else ""
        lat, lon = _num(r.get("lat")), _num(r.get("lng"))
        if not number or number in seen or lat is None or lon is None or (lat == 0 and lon == 0):
            continue
        seen.add(number)
        heading = _num(r.get("richting"))
        out.append({"number": number, "type": str(r.get("type") or "").strip().upper(), "lat": lat, "lon": lon,
                    "speed_kmh": _num(r.get("snelheid")), "heading": heading % 360 if heading is not None else None})
    return out


# --- SpoorKaart ---------------------------------------------------------------------------------------------------


def _encode(points):
    """[(lat, lon), ...] -> the flat 1e-5 degree integers, the first absolute, the rest differences."""
    out, pla, plo = [], 0, 0
    for la, lo in points:
        a, b = int(round(la * 1e5)), int(round(lo * 1e5))
        out.extend((a - pla, b - plo))
        pla, plo = a, b
    return out


def _line_m(points):
    return sum(metres(a[0], a[1], b[0], b[1]) for a, b in zip(points, points[1:]))


def parse_spoorkaart(raw):
    """SpoorKaart's GeoJSON -> {"v", "stations": {code: [lat, lon]}, "pairs": {pair key: [metres, encoded]}}. Codes
    in upper case (the departure boards' form). A station's place is the end of its lines (every line of a station
    starts or ends at the same point, measured). The pair's line runs from the lower code to the higher (Python
    string order), as geometry.line_from_legs reads it; a pair drawn twice keeps the shorter."""
    try:
        d = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
        feats = d["payload"]["features"]
    except (ValueError, TypeError, KeyError) as err:
        raise FeedUnavailable("SpoorKaart answer not understood (%s)" % type(err).__name__) from None
    stations, pairs = {}, {}
    for f in feats:
        try:
            p = f["properties"]
            a, b = str(p["from"]).strip().upper(), str(p["to"]).strip().upper()
            coords = f["geometry"]["coordinates"]
            pts = [(float(c[1]), float(c[0])) for c in coords]
        except (TypeError, KeyError, ValueError, IndexError):
            continue
        if not a or not b or a == b or len(pts) < 2:
            continue
        stations.setdefault(a, [round(pts[0][0], 6), round(pts[0][1], 6)])
        stations.setdefault(b, [round(pts[-1][0], 6), round(pts[-1][1], 6)])
        if a > b:
            pts = pts[::-1]
        key = pair_key(a, b)
        length = round(_line_m(pts), 1)
        if key not in pairs or length < pairs[key][0]:
            pairs[key] = [length, _encode(pts)]
    if not pairs:
        raise FeedUnavailable("SpoorKaart answer held no lines")
    return {"v": SPOOR_V, "stations": stations, "pairs": pairs}


class Graph:
    """SpoorKaart's stations as a graph: the shortest path between two stations, by track length."""

    def __init__(self, spoor):
        self.stations = spoor["stations"]
        self.adj = {}
        for key, (length, _) in spoor["pairs"].items():
            a, b = key.split("|")
            self.adj.setdefault(a, []).append((b, length))
            self.adj.setdefault(b, []).append((a, length))
        self._cache = {}
        # A coarse grid for the nearest station.
        self._grid = {}
        for code, (la, lo) in self.stations.items():
            self._grid.setdefault((int(la * 20), int(lo * 20)), []).append(code)

    def nearest(self, lat, lon, within=STATION_MATCH_M):
        """The station nearest a point, within `within` metres, or None."""
        gx, gy = int(lat * 20), int(lon * 20)
        best, best_m = None, within
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for code in self._grid.get((gx + dx, gy + dy), ()):
                    la, lo = self.stations[code]
                    m = metres(lat, lon, la, lo)
                    if m <= best_m:
                        best, best_m = code, m
        return best

    def path(self, a, b):
        """[a, ..., b] along the track, or None (no path, or one not believed: PATH_FACTOR)."""
        if a == b:
            return [a]
        k = (a, b)
        if k in self._cache:
            return self._cache[k]
        if a not in self.adj or b not in self.adj:
            self._cache[k] = None
            return None
        la, lo = self.stations[a]
        lb, lob = self.stations[b]
        limit = PATH_FACTOR * metres(la, lo, lb, lob) + PATH_SLACK_M
        dist, prev, heap = {a: 0.0}, {}, [(0.0, a)]
        found = False
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist.get(u, math.inf) or d > limit:
                continue
            if u == b:
                found = True
                break
            for v, w in self.adj.get(u, ()):
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(heap, (nd, v))
        out = None
        if found:
            out = [b]
            while out[-1] != a:
                out.append(prev[out[-1]])
            out.reverse()
        if len(self._cache) > 50000:
            self._cache.clear()
        self._cache[k] = out
        return out

    def route(self, codes):
        """Consecutive stations of a trip -> the stations its track passes, in order; a pair with no believed path is
        joined directly (a straight leg)."""
        out = []
        for a, b in zip(codes, codes[1:]):
            if a == b:
                continue
            p = self.path(a, b) or [a, b]
            if out and out[-1] == p[0]:
                p = p[1:]
            out.extend(p)
        if not out and codes:
            out = [codes[0]]
        return out


# --- the timetable --------------------------------------------------------------------------------------------------


def _secs(text):
    """'25:03:00' -> 90180; None when empty or malformed."""
    if not text:
        return None
    try:
        h, m, s = text.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except ValueError:
        return None


def _rows(z, name):
    with z.open(name) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline=""))


def distill(zip_path, version=None):
    """OVapi's gtfs-nl.zip on disk -> the rail timetable, compact (TimetableData's input). Streams stop_times.txt
    (~836 MB unpacked) once, parsing only the rows of rail trips. Blocking: minutes on a small machine."""
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        need = {"agency.txt", "routes.txt", "trips.txt", "stop_times.txt", "stops.txt"}
        if not need <= names:
            raise FeedUnavailable("timetable zip lacks %s" % sorted(need - names))
        agencies = {r["agency_id"]: (r.get("agency_name") or "").strip() for r in _rows(z, "agency.txt")}
        routes = {}
        for r in _rows(z, "routes.txt"):
            if (r.get("route_type") or "").strip() in RAIL_ROUTE_TYPES:
                routes[r["route_id"]] = [r.get("agency_id") or "", (r.get("route_short_name") or "").strip(),
                                         (r.get("route_long_name") or "").strip()]
        trips = {}
        for r in _rows(z, "trips.txt"):
            if r.get("route_id") in routes:
                trips[r["trip_id"]] = [(r.get("trip_short_name") or "").strip(), r["route_id"],
                                       r.get("service_id") or "", (r.get("trip_headsign") or "").strip()]
        services = {}
        sids = {t[2] for t in trips.values()}
        if "calendar_dates.txt" in names:
            for r in _rows(z, "calendar_dates.txt"):
                sid = r.get("service_id")
                if sid not in sids:
                    continue
                day, kind = (r.get("date") or "").strip(), (r.get("exception_type") or "").strip()
                s = services.setdefault(sid, set())
                if kind == "1":
                    s.add(day)
                elif kind == "2":
                    s.discard(day)
        if "calendar.txt" in names:
            for r in _rows(z, "calendar.txt"):
                sid = r.get("service_id")
                if sid not in sids:
                    continue
                try:
                    d = dt.datetime.strptime(r["start_date"], "%Y%m%d").date()
                    end = dt.datetime.strptime(r["end_date"], "%Y%m%d").date()
                except (KeyError, ValueError):
                    continue
                wd = [r.get(k) == "1" for k in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
                                                 "sunday")]
                s = services.setdefault(sid, set())
                while d <= end:
                    if wd[d.weekday()]:
                        s.add(d.strftime("%Y%m%d"))
                    d += dt.timedelta(days=1)
        times = {}
        used = set()
        with z.open("stop_times.txt") as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")
            header = next(csv.reader([text.readline()]))
            col = {h.strip(): i for i, h in enumerate(header)}
            it, isq, ist = col["trip_id"], col["stop_sequence"], col["stop_id"]
            iar, idp = col.get("arrival_time"), col.get("departure_time")
            ipu, ido = col.get("pickup_type"), col.get("drop_off_type")
            width = len(header)
            first = it == 0
            for line in text:
                if first and line[:1] != '"' and line[:line.find(",")] not in trips:
                    # The common case, cheaply: most rows are not rail (trip_id is the first column).
                    continue
                if '"' in line:
                    vals = next(csv.reader([line]))
                else:
                    vals = line.rstrip("\r\n").split(",")
                if len(vals) < width or vals[it] not in trips:
                    continue
                try:
                    seq = int(vals[isq])
                except ValueError:
                    continue
                a = _secs(vals[iar]) if iar is not None else None
                d = _secs(vals[idp]) if idp is not None else None
                no_stop = (ipu is not None and vals[ipu].strip() == "1" and ido is not None and vals[ido].strip() == "1")
                times.setdefault(vals[it], []).append((seq, vals[ist], a, d, no_stop))
                used.add(vals[ist])
        parents = {}
        stops_all = {}
        for r in _rows(z, "stops.txt"):
            sid = r["stop_id"]
            stops_all[sid] = r
        for sid in used:
            r = stops_all.get(sid)
            if r is not None and r.get("parent_station"):
                parents.setdefault(r["parent_station"], set())
        stops = {}
        for sid, r in stops_all.items():
            par = r.get("parent_station") or ""
            if sid in used or (par and par in parents):
                try:
                    stops[sid] = [(r.get("stop_name") or "").strip(), round(float(r["stop_lat"]), 6),
                                  round(float(r["stop_lon"]), 6), par]
                except (KeyError, ValueError):
                    continue
        del stops_all
    out_times = {}
    for tid, rows in times.items():
        rows.sort()
        out_times[tid] = [[sid, a, d] for _, sid, a, d, no_stop in rows if not no_stop]
    keep = {tid for tid, rows in out_times.items() if len(rows) >= 2}
    return {"v": DISTILL_V, "version": version, "built": time.time(),
            "agencies": {a: agencies.get(a, "") for a in {routes[t[1]][0] for t in trips.values()}},
            "routes": routes,
            "trips": {tid: t for tid, t in trips.items() if tid in keep},
            "services": {sid: sorted(days) for sid, days in services.items()},
            "times": {tid: out_times[tid] for tid in keep},
            "stops": stops}


class TimetableData:
    """The distilled rail timetable, indexed: which trip a train number runs now, its calls, its stations."""

    def __init__(self, d, graph=None):
        if not isinstance(d, dict) or d.get("v") != DISTILL_V:
            raise ValueError("timetable of version %r, not %d" % (d.get("v") if isinstance(d, dict) else None, DISTILL_V))
        self.version = d.get("version")
        self.agencies = d.get("agencies") or {}
        self.routes = d.get("routes") or {}
        self.trips = d.get("trips") or {}
        self.services = {sid: frozenset(days) for sid, days in (d.get("services") or {}).items()}
        self.times = d.get("times") or {}
        self.stops = d.get("stops") or {}
        self.by_number = {}
        for tid, t in self.trips.items():
            if t[0]:
                self.by_number.setdefault(t[0], []).append(tid)
        self.graph = None
        self.station_of = {}
        self.station_names = {}
        self._paths = {}
        if graph is not None:
            self.attach(graph)

    def attach(self, graph):
        """Place every stop area on its SpoorKaart station (STATION_MATCH_M), and name each station."""
        self.graph = graph
        self._paths = {}
        areas = {}
        for sid, (name, la, lo, par) in self.stops.items():
            areas.setdefault(par or sid, []).append((sid, name, la, lo))
        station_of, names = {}, {}
        for members in areas.values():
            la = sum(m[2] for m in members) / len(members)
            lo = sum(m[3] for m in members) / len(members)
            code = graph.nearest(la, lo)
            if code is None:
                continue
            for sid, name, _, _ in members:
                station_of[sid] = code
                if name and code not in names:
                    names[code] = name
        self.station_of, self.station_names = station_of, names

    def company(self, tid):
        """(operator id or None, brand or None, agency name) for a trip."""
        t = self.trips.get(tid)
        if t is None:
            return None, None, None
        route = self.routes.get(t[1]) or ["", "", ""]
        aid = route[0]
        name = self.agencies.get(aid) or ""
        if aid in AGENCIES:
            op, brand = AGENCIES[aid]
            return op, brand, name
        if aid in BRAND_AGENCIES:
            op = OP_KEOLIS if (aid, route[1]) in KEOLIS_ROUTES else OP_ARRIVA
            return op, BRAND_AGENCIES[aid], name
        return (OP_NS if aid in NS_AGENCIES else None), None, name

    def runs(self, tid, day):
        sid = self.trips[tid][2]
        return day in self.services.get(sid, ())

    def trip_for(self, number, now, tz):
        """(trip id, service date as YYYYMMDD, base epoch) of the run of `number` whose span (TRIP_PRE_S before its
        first departure to TRIP_POST_S after its last arrival) holds `now`, the nearest if several; else None."""
        cands = self.by_number.get(number)
        if not cands:
            return None
        today = dt.datetime.fromtimestamp(now, tz).date()
        best, best_gap = None, None
        for day in (today, today - dt.timedelta(days=1)):
            ymd = day.strftime("%Y%m%d")
            base = service_base(day, tz)
            for tid in cands:
                if not self.runs(tid, ymd):
                    continue
                rows = self.times.get(tid) or ()
                if not rows:
                    continue
                first = rows[0][2] if rows[0][2] is not None else rows[0][1]
                last = rows[-1][1] if rows[-1][1] is not None else rows[-1][2]
                if first is None or last is None:
                    continue
                lo, hi = base + first - TRIP_PRE_S, base + last + TRIP_POST_S
                if not lo <= now <= hi:
                    continue
                gap = max(0.0, base + first - now, now - (base + last))
                if best_gap is None or gap < best_gap:
                    best, best_gap = (tid, ymd, base), gap
        return best

    def calls(self, tid, base):
        """[{"stop_id", "code", "name", "aimed_arrival", "aimed_departure"}] in order; epoch seconds or None."""
        out = []
        for sid, a, d in self.times.get(tid) or ():
            st = self.stops.get(sid)
            code = self.station_of.get(sid)
            out.append({"stop_id": sid, "code": code,
                        "name": (self.station_names.get(code) if code else None) or (st[0] if st else None),
                        "aimed_arrival": base + a if a is not None else None,
                        "aimed_departure": base + d if d is not None else None})
        return out

    def path(self, tid):
        """The SpoorKaart stations the trip's track passes, in order; [] without a graph."""
        if self.graph is None:
            return []
        if tid not in self._paths:
            codes = []
            for sid, _, _ in self.times.get(tid) or ():
                c = self.station_of.get(sid)
                if c and (not codes or codes[-1] != c):
                    codes.append(c)
            key = tuple(codes)
            if len(self._paths) > 20000:
                self._paths.clear()
            self._paths[tid] = self.graph.route(list(key)) if len(key) >= 2 else list(key)
        return self._paths[tid]

    def near_path(self, tid, lat, lon, within=TRIP_MATCH_M):
        """The fix is within `within` of a station of the trip's path (or the trip has no placed station)."""
        codes = self.path(tid)
        if not codes:
            return True
        st = self.graph.stations
        return any(metres(lat, lon, st[c][0], st[c][1]) <= within for c in codes if c in st)

    def stop_index(self, tid):
        """{stop_id or its stop area: index of the call} for a trip, for joining updates."""
        out = {}
        for i, (sid, _, _) in enumerate(self.times.get(tid) or ()):
            out.setdefault(sid, i)
            st = self.stops.get(sid)
            if st and st[3]:
                out.setdefault("area:" + st[3], i)
        return out


# --- the train updates ----------------------------------------------------------------------------------------------


def parse_updates(raw):
    """OVapi's trainUpdates.pb -> {trip_id: [(stop_id, arrival epoch or None, departure epoch or None, skipped)]}.
    Needs the `gtfs` extra (gtfs-realtime-bindings); raises FeedUnavailable on an undecodable body."""
    from .gtfs.realtime import decode
    feed = decode(raw)
    if feed is None:
        raise FeedUnavailable("undecodable body from %s" % TRAIN_UPDATES_URL)
    out = {}
    for ent in feed.entity:
        if not ent.HasField("trip_update"):
            continue
        tu = ent.trip_update
        tid = tu.trip.trip_id.strip()
        if not tid:
            continue
        rows = []
        for s in tu.stop_time_update:
            a = s.arrival.time if s.HasField("arrival") and s.arrival.time else None
            d = s.departure.time if s.HasField("departure") and s.departure.time else None
            rows.append((s.stop_id.strip(), a, d, s.schedule_relationship == 1))
        out[tid] = rows
    return out


def apply_updates(calls, rows, index, stops):
    """Each call's expected times and cancellation from a trip's update rows (parse_updates), in place."""
    for sid, a, d, skipped in rows or ():
        i = index.get(sid)
        if i is None:
            st = stops.get(sid)
            if st and st[3]:
                i = index.get("area:" + st[3])
        if i is None:
            continue
        c = calls[i]
        if a is not None:
            c["expected_arrival"] = float(a)
        if d is not None:
            c["expected_departure"] = float(d)
        if skipped:
            c["cancelled"] = True


def next_call(calls, now, grace=NEXT_GRACE_S):
    """(index of the next call or None, its expected arrival or None, the delay in s or None). The next call is the
    first after the origin, not cancelled, whose arrival (expected, else aimed plus the delay last known) is at most
    `grace` past. The delay is that call's expected against its aimed time, else the last one known before it."""
    delay = None
    for i, c in enumerate(calls):
        if c.get("cancelled"):
            continue
        aa = c.get("aimed_arrival") if c.get("aimed_arrival") is not None else c.get("aimed_departure")
        ea = c.get("expected_arrival") if c.get("expected_arrival") is not None else c.get("expected_departure")
        if ea is not None and aa is not None:
            known = int(round(ea - aa))
        else:
            known = None
        if i == 0:
            ed, ad = c.get("expected_departure"), c.get("aimed_departure")
            if ed is not None and ad is not None:
                delay = int(round(ed - ad))
            continue
        when = ea if ea is not None else (aa + (delay or 0) if aa is not None else None)
        if known is not None:
            delay = known
        if when is not None and when >= now - grace:
            return i, when, delay
        ed, ad = c.get("expected_departure"), c.get("aimed_departure")
        if ed is not None and ad is not None:
            delay = int(round(ed - ad))
    return None, None, delay


# --- the client -----------------------------------------------------------------------------------------------------


def check_key(key, user=DEFAULT_USER, timeout=30, opener=None):
    """The setup form's key check, one vehicle read: None when it works, else "invalid_key" or "cannot_connect".
    Blocking."""
    try:
        raw, _ = fetch(VEHICLE_URL, {"User-Agent": user, "Accept": "application/json",
                                     "Ocp-Apim-Subscription-Key": str(key or "").strip()}, timeout, opener)
        parse_vehicles(raw)
    except KeyRefused:
        return "invalid_key"
    except KeyedError:
        return "cannot_connect"
    return None


class NsClient:
    """Polls NS's vehicle feed ONCE for every consumer (B16: budget the IP); keeps SpoorKaart, OVapi's timetable and
    OVapi's train updates on their own threads (a poll never waits for them).

    `consumers` is {consumer: key}. The shared registration (reference/shared_ns.py) starts with the first config
    entry's and register()s each that joins; the vehicle feed is read with the oldest registered key (_keyed.KeyRing).

    for_consumer(name, operator=None) -> [train dict]: the trains of that consumer (and operator), each:
        number, type, lat, lon, speed_kmh, heading, observed_at (epoch, first seen), observed_at_source "first_seen",
        operator ("ns", "arriva", "keolis"), and, when the timetable names its run: trip_id, service_date, agency,
        brand, route_name, route_long, headsign, calls (TimetableData.calls with expected times from the updates),
        path (SpoorKaart stations in order).
    """

    def __init__(self, consumers, user=DEFAULT_USER, timeout=30, opener=None, clock=None, feed_ttl_s=FEED_TTL_S,
                 storage_dir=None, background=True, static_timeout=900):
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
        self._ns_backoff = Backoff(self._clock)
        self._ov_backoff = Backoff(self._clock)
        self._wanted = set()
        for consumer, key in dict(consumers).items():
            _check_consumer(consumer)
            self._ring.add(consumer, key)
            self._wanted.add(consumer)
        self._tz = zone()
        self._fixes = {}
        self._feeds = None
        self._fetched_at = None
        self._spoor = None
        self._spoor_at = None
        self._spoor_retry = 0.0
        self._graph = None
        self._tt = None
        self._tt_meta = {}
        self._tt_retry = 0.0
        self._updates = None
        self._updates_at = None
        self._updates_tag = {}
        self._updates_retry = 0.0
        self._loaded = False
        self._carriers = {}
        self._carrier_retry = 0.0
        self._busy = set()
        self.fetch_count = 0
        self.counts = {}
        self.last_error = {}

    # --- registration -------------------------------------------------------------------------------------------

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
        _check_consumer(consumer)
        self._ring.remove(consumer)
        with self._state:
            if consumer not in self._wanted:
                return False
            self._wanted.discard(consumer)
            return True

    # --- disk -----------------------------------------------------------------------------------------------------

    def _path(self, name):
        return os.path.join(self.storage_dir, name)

    @staticmethod
    def _write_json(path, obj, gz=False):
        tmp = path + ".tmp"
        if gz:
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(obj, fh, separators=(",", ":"))
        else:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, separators=(",", ":"))
        os.replace(tmp, path)

    def _load_cached(self):
        """The copies on disk, once. Blocking (the timetable is a few MB of gzip)."""
        with self._state:
            if self._loaded or self.storage_dir is None:
                self._loaded = True
                return
            self._loaded = True
        try:
            with open(self._path(SPOORKAART_NAME), encoding="utf-8") as fh:
                d = json.load(fh)
            if d.get("v") == SPOOR_V and d.get("pairs"):
                g = Graph(d)
                with self._state:
                    self._spoor, self._spoor_at, self._graph = d, d.get("fetched_at"), g
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        try:
            with open(self._path(TIMETABLE_NAME + ".meta.json"), encoding="utf-8") as fh:
                meta = json.load(fh)
            with gzip.open(self._path(TIMETABLE_NAME + ".json.gz"), "rt", encoding="utf-8") as fh:
                tt = TimetableData(json.load(fh))
            with self._state:
                self._tt_meta = meta if isinstance(meta, dict) else {}
                self._tt = tt
                graph = self._graph
            if graph is not None:
                tt.attach(graph)
        except (OSError, ValueError, KeyError, TypeError, EOFError, AttributeError):
            pass

    # --- SpoorKaart -----------------------------------------------------------------------------------------------

    def refresh_spoorkaart(self, force=False):
        """Fetch SpoorKaart if due, on THIS thread; never raises. True when it changed."""
        now = self._clock()
        with self._state:
            fresh = self._spoor_at is not None and now - self._spoor_at < SPOORKAART_TTL_S
            if (not force and (fresh or now < self._spoor_retry)) or "spoor" in self._busy:
                return False
            self._busy.add("spoor")
            self._spoor_retry = now + SPOORKAART_RETRY_S
        try:
            self._ns_backoff.check(SPOORKAART_URL)
            key = self._ring.active()
            self.fetch_count += 1
            try:
                raw, _ = fetch(SPOORKAART_URL, self._ns_headers(key), self.timeout, self._opener,
                               max_bytes=MAX_BYTES)
            except RateLimited as err:
                self._ns_backoff.hit(err)
                raise
            d = parse_spoorkaart(raw)
            d["fetched_at"] = now
            d["digest"] = hashlib.sha1(json.dumps(d["pairs"], sort_keys=True).encode()).hexdigest()[:12]
            g = Graph(d)
            if self.storage_dir is not None:
                self._write_json(self._path(SPOORKAART_NAME), d)
            with self._state:
                self._spoor, self._spoor_at, self._graph = d, now, g
                tt = self._tt
            if tt is not None:
                tt.attach(g)
            return True
        except Exception as err:  # noqa: BLE001 - keep the last copy; positions do not need it
            self.last_error["spoorkaart"] = "%s: %s" % (type(err).__name__, str(err)[:120])
            return False
        finally:
            with self._state:
                self._busy.discard("spoor")

    def _ns_headers(self, key):
        return {"User-Agent": self.user, "Accept": "application/json", "Ocp-Apim-Subscription-Key": key}

    # --- OVapi's timetable ----------------------------------------------------------------------------------------

    def refresh_timetable(self, force=False):
        """Check and if needed download and distill OVapi's timetable, on THIS thread; never raises. True when it
        changed. A failed refresh keeps the last copy."""
        if self.storage_dir is None:
            return False
        now = self._clock()
        with self._state:
            checked = self._tt_meta.get("checked_at")
            fresh = self._tt is not None and checked is not None and now - checked < TIMETABLE_TTL_S
            if (not force and (fresh or now < self._tt_retry)) or "timetable" in self._busy:
                return False
            self._busy.add("timetable")
            self._tt_retry = now + TIMETABLE_RETRY_S
            meta = dict(self._tt_meta)
            have = self._tt is not None
        zp = self._path(TIMETABLE_NAME + ".zip.tmp")
        try:
            self._ov_backoff.check(TIMETABLE_URL)
            headers = {"User-Agent": self.user}
            if have and meta.get("etag"):
                headers["If-None-Match"] = meta["etag"]
            if have and meta.get("last_modified"):
                headers["If-Modified-Since"] = meta["last_modified"]
            self.fetch_count += 1
            got = self._download(TIMETABLE_URL, headers, zp)
            if got is None:
                meta["checked_at"] = now
                self._write_json(self._path(TIMETABLE_NAME + ".meta.json"), meta)
                with self._state:
                    self._tt_meta = meta
                return False
            version = got.get("ETag") or got.get("Last-Modified") or str(int(now))
            d = distill(zp, version)
            tt = TimetableData(d)
            self._write_json(self._path(TIMETABLE_NAME + ".json.gz"), d, gz=True)
            meta = {"etag": got.get("ETag"), "last_modified": got.get("Last-Modified"), "version": version,
                    "checked_at": now, "trips": len(d["trips"])}
            self._write_json(self._path(TIMETABLE_NAME + ".meta.json"), meta)
            with self._state:
                graph = self._graph
            if graph is not None:
                tt.attach(graph)
            with self._state:
                self._tt, self._tt_meta = tt, meta
            return True
        except Exception as err:  # noqa: BLE001 - keep the last copy
            self.last_error["timetable"] = "%s: %s" % (type(err).__name__, str(err)[:120])
            return False
        finally:
            if os.path.exists(zp):
                try:
                    os.remove(zp)
                except OSError:
                    pass
            with self._state:
                self._busy.discard("timetable")

    def _download(self, url, headers, dest):
        """Stream `url` to `dest`. The response headers, or None on 304. Raises RateLimited, FeedUnavailable."""
        req = urllib.request.Request(url, headers=headers)
        open_fn = self._opener or urllib.request.urlopen
        total = 0
        try:
            with open_fn(req, timeout=self.static_timeout) as resp, open(dest, "wb") as out:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > TIMETABLE_MAX_BYTES:
                        raise FeedUnavailable("more than %d bytes from %s" % (TIMETABLE_MAX_BYTES, url))
                    out.write(chunk)
                return resp.headers
        except urllib.error.HTTPError as err:
            if err.code == 304:
                return None
            if err.code == 429:
                exc = RateLimited("HTTP 429 from %s" % url)
                exc.retry_after = None
                self._ov_backoff.hit(exc)
                raise exc from None
            raise FeedUnavailable("HTTP %s from %s" % (err.code, url)) from None
        except KeyedError:
            raise
        except Exception as err:  # noqa: BLE001
            raise FeedUnavailable("%s from %s" % (type(err).__name__, url)) from None

    # --- OVapi's train updates ------------------------------------------------------------------------------------

    def refresh_updates(self, force=False):
        """Read OVapi's train updates if due (UPDATES_TTL_S), conditionally, on THIS thread; never raises."""
        now = self._clock()
        with self._state:
            fresh = self._updates_at is not None and now - self._updates_at < UPDATES_TTL_S
            if (not force and (fresh or now < self._updates_retry)) or "updates" in self._busy \
                    or "timetable" in self._busy:
                # Never beside the timetable's download: one server, one budget (NS note; EU note section 4).
                return False
            self._busy.add("updates")
            self._updates_retry = now + UPDATES_TTL_S
            tag = dict(self._updates_tag)
        try:
            self._ov_backoff.check(TRAIN_UPDATES_URL)
            headers = {"User-Agent": self.user}
            if tag.get("etag"):
                headers["If-None-Match"] = tag["etag"]
            if tag.get("last_modified"):
                headers["If-Modified-Since"] = tag["last_modified"]
            self.fetch_count += 1
            req = urllib.request.Request(TRAIN_UPDATES_URL, headers=headers)
            open_fn = self._opener or urllib.request.urlopen
            try:
                with open_fn(req, timeout=self.timeout) as resp:
                    raw = resp.read(MAX_BYTES + 1)
                    got = resp.headers
            except urllib.error.HTTPError as err:
                if err.code == 304:
                    with self._state:
                        self._updates_at = now
                    return False
                if err.code == 429:
                    exc = RateLimited("HTTP 429 from %s" % TRAIN_UPDATES_URL)
                    exc.retry_after = None
                    self._ov_backoff.hit(exc)
                    raise exc from None
                raise FeedUnavailable("HTTP %s from %s" % (err.code, TRAIN_UPDATES_URL)) from None
            if len(raw) > MAX_BYTES:
                raise FeedUnavailable("more than %d bytes from %s" % (MAX_BYTES, TRAIN_UPDATES_URL))
            ups = parse_updates(raw)
            with self._state:
                self._updates, self._updates_at = ups, now
                self._updates_tag = {"etag": got.get("ETag"), "last_modified": got.get("Last-Modified")}
            return True
        except Exception as err:  # noqa: BLE001 - the last answer stands for UPDATES_HOLD_S
            self.last_error["updates"] = "%s: %s" % (type(err).__name__, str(err)[:120])
            return False
        finally:
            with self._state:
                self._busy.discard("updates")

    def _start(self, name, fn):
        if not self._background:
            fn()
            return
        threading.Thread(target=fn, name="saw_ns_" + name, daemon=True).start()

    def _ensure_background(self):
        self._load_cached()
        now = self._clock()
        with self._state:
            busy = set(self._busy)
            spoor_due = ("spoor" not in busy and now >= self._spoor_retry
                         and (self._spoor_at is None or now - self._spoor_at >= SPOORKAART_TTL_S))
            checked = self._tt_meta.get("checked_at")
            tt_due = (self.storage_dir is not None and "timetable" not in busy and now >= self._tt_retry
                      and (self._tt is None or checked is None or now - checked >= TIMETABLE_TTL_S))
            up_due = ("updates" not in busy and "timetable" not in busy and now >= self._updates_retry
                      and (self._updates_at is None or now - self._updates_at >= UPDATES_TTL_S))
        if spoor_due:
            self._start("spoorkaart", self.refresh_spoorkaart)
        if tt_due:
            self._start("timetable", self.refresh_timetable)
        elif up_due:
            self._start("updates", self.refresh_updates)

    # --- the vehicle feed -----------------------------------------------------------------------------------------

    def _fetch_vehicles(self):
        self._ns_backoff.check(VEHICLE_URL)
        key = self._ring.active()
        self.fetch_count += 1
        try:
            raw, _ = fetch(VEHICLE_URL, self._ns_headers(key), self.timeout, self._opener, max_bytes=MAX_BYTES)
        except RateLimited as err:
            self._ns_backoff.hit(err)
            raise
        except KeyRefused:
            if self._ring.refused(key):
                self._ring.active()     # AuthRefused now, when that was the last key
            raise
        self._ring.ok()
        return parse_vehicles(raw)

    def _stamp(self, trains, now):
        """Option B (module docstring): each train's first-seen time, kept until its position changes."""
        for tr in trains:
            pos = (round(tr["lat"], 6), round(tr["lon"], 6))
            held = self._fixes.get(tr["number"])
            if held is None or held[0] != pos:
                held = [pos, now, now]
                self._fixes[tr["number"]] = held
            else:
                held[2] = now
            tr["observed_at"] = held[1]
            tr["observed_at_source"] = "first_seen"
        for n in [n for n, h in self._fixes.items() if now - h[2] > FORGET_S]:
            self._fixes.pop(n, None)

    def _carriers_for(self, numbers, now):
        """Which company runs each regional train, from NS's DVS record (VERVOERDER), read in ONE batched request for
        the numbers not known within CARRIER_TTL_S; at most one read per CARRIER_RETRY_S. Never raises: without an
        answer the timetable's route decides (TimetableData.company)."""
        for n in [n for n, h in self._carriers.items() if now - h[2] > CARRIER_TTL_S]:
            self._carriers.pop(n, None)
        missing = sorted({n for n in numbers if n not in self._carriers and n.isdigit()})[:CARRIER_BATCH]
        if not missing or now < self._carrier_retry:
            return
        self._carrier_retry = now + CARRIER_RETRY_S
        try:
            self._ns_backoff.check(CARRIER_URL)
            key = self._ring.active()
            self.fetch_count += 1
            try:
                raw, _ = fetch(CARRIER_URL + ",".join(missing), self._ns_headers(key), self.timeout, self._opener,
                               max_bytes=MAX_BYTES)
            except RateLimited as err:
                self._ns_backoff.hit(err)
                raise
            rows = json.loads(raw)
            if isinstance(rows, dict):
                rows = [rows]
            for r in rows if isinstance(rows, list) else ():
                if not isinstance(r, dict) or r.get("ritnummer") is None:
                    continue
                v = str(r.get("vervoerder") or "").strip()
                self._carriers[str(r["ritnummer"])] = (VERVOERDER.get(v.lower()), v or None, now)
        except Exception as err:  # noqa: BLE001 - the timetable's route decides meanwhile
            self.last_error["carrier"] = "%s: %s" % (type(err).__name__, str(err)[:120])

    def _count(self, reason):
        self.counts[reason] = self.counts.get(reason, 0) + 1

    def snapshot(self, force=False):
        """{consumer: [train dict]} for the registered consumers (class docstring). Raises the feed's errors."""
        self._ensure_background()
        with self._lock:
            now = self._clock()
            if (not force and self._feeds is not None and self._fetched_at is not None
                    and now - self._fetched_at < self.feed_ttl_s):
                return self._feeds
            trains = self._fetch_vehicles()
            now = self._clock()
            self._stamp(trains, now)
            with self._state:
                wanted = frozenset(self._wanted)
                tt = self._tt
                ups = self._updates if (self._updates_at is not None
                                        and now - self._updates_at <= UPDATES_HOLD_S) else None
            out = {c: [] for c in wanted}
            if INTERCITY in wanted:
                self._carriers_for([tr["number"] for tr in trains if tr["type"] == "ARR"], now)
            for tr in trains:
                consumer = TYPE_CONSUMER.get(tr["type"])
                if consumer is None:
                    self._count("unknown_type")
                    continue
                if consumer not in out:
                    continue
                rec = dict(tr)
                match = tt.trip_for(tr["number"], now, self._tz) if tt is not None else None
                if match is not None and tt.graph is not None and not tt.near_path(match[0], tr["lat"], tr["lon"]):
                    self._count("trip_far_from_fix")
                    match = None
                op = OP_NS if tr["type"] in ("IC", "SPR") else None
                if match is not None:
                    tid, ymd, base = match
                    company, brand, agency = tt.company(tid)
                    t = tt.trips[tid]
                    route = tt.routes.get(t[1]) or ["", "", ""]
                    calls = tt.calls(tid, base)
                    if ups is not None and tid in ups:
                        apply_updates(calls, ups[tid], tt.stop_index(tid), tt.stops)
                    rec.update({"trip_id": tid, "service_date": ymd, "agency": agency, "brand": brand,
                                "route_name": route[1], "route_long": route[2], "headsign": t[3],
                                "calls": calls, "path": tt.path(tid), "updated": ups is not None and tid in ups})
                    if op is None:
                        op = company
                if tr["type"] == "ARR":
                    held = self._carriers.get(tr["number"])
                    if held is not None and held[0] is not None:
                        op = held[0]
                        rec["carrier"] = held[1]
                if op is None:
                    self._count("no_operator_" + tr["type"].lower())
                    continue
                rec["operator"] = op
                out[consumer].append(rec)
            self._feeds = out
            self._fetched_at = now
            return out

    def for_consumer(self, consumer, operator=None, force=False):
        _check_consumer(consumer)
        if consumer not in self.consumers:
            raise ValueError("consumer %r is not registered with this client" % (consumer,))
        feeds = self.snapshot(force=force)
        if consumer not in feeds:
            feeds = self.snapshot(force=True)
        trains = feeds[consumer]
        return trains if operator is None else [t for t in trains if t.get("operator") == operator]

    # --- the static views ---------------------------------------------------------------------------------------

    def stations(self):
        """{code: {"lat", "lon", "name"}}: SpoorKaart's stations, named from the timetable where it places a stop;
        {} before SpoorKaart is read."""
        with self._state:
            spoor, tt = self._spoor, self._tt
        if not spoor:
            return {}
        names = tt.station_names if tt is not None else {}
        return {c: {"lat": p[0], "lon": p[1], "name": names.get(c) or c} for c, p in spoor["stations"].items()}

    def pair_lines(self):
        """{pair key: encoded points} (geometry.line_from_legs' "pair_lines"); {} before SpoorKaart is read."""
        with self._state:
            spoor = self._spoor
        return {k: v[1] for k, v in (spoor or {}).get("pairs", {}).items()}

    def static_version(self):
        """Changes when SpoorKaart's lines or the timetable do; None until both are read."""
        self._load_cached()
        with self._state:
            spoor, tt = self._spoor, self._tt
        if not spoor or tt is None:
            return None
        return "ns%d.%d:%s:%s" % (SPOOR_V, DISTILL_V, spoor.get("digest") or int(spoor.get("fetched_at") or 0),
                                  hashlib.sha1(str(tt.version).encode()).hexdigest()[:12])

    def stats(self):
        """Low-cardinality state for a diagnostic. Never a key."""
        with self._state:
            tt, spoor = self._tt, self._spoor
            out = {"timetable": tt.version if tt is not None else None,
                   "timetable_trips": len(tt.trips) if tt is not None else None,
                   "stations_placed": len(set(tt.station_of.values())) if tt is not None else None,
                   "spoorkaart_lines": len(spoor["pairs"]) if spoor else None,
                   "updates_at": self._updates_at, "updates_trips": len(self._updates) if self._updates else None,
                   "busy": sorted(self._busy)}
        out.update({"fetch_count": self.fetch_count, "counts": dict(self.counts),
                    "errors": dict(self.last_error), "trains_tracked": len(self._fixes),
                    "backing_off": self._clock() < max(self._ns_backoff.until, self._ov_backoff.until),
                    **self._ring.state()})
        return out
