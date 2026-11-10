"""Shared Irish Rail client (Iarnród Éireann's realtime API, and the NTA's Irish Rail timetable) for the Live Track
family.

Shared from first use (35_Dependencies/README.md; Lee 2026-10-20: a feed known to carry several integrations' vehicles
is shared from the start). One answer carries three integrations' trains, each to ONE consumer (06_MAP_CONTRACT.md D1),
by the train code's prefix, which agrees with the API's own train type wherever both exist (MEASURED, 461 of 461 trains
on 2026-11-09):

    intercity   A, B     InterCity, the Enterprise, extra mainline trains (type M): Live Track Intercity Rail
    commuter    P, D     Commuter, Dublin and Cork (type S): Live Track Commuter Rail
    transit     E, BUS   DART (type D), replacement buses: Live Track Transit

Provider: 35_Dependencies/third_party/irish-rail/SSOT.md (no licence published; "as is"; Lee 2026-11-09: ship on it as
Brightline's untermed feed). Timetable: 35_Dependencies/third_party/nta-ireland/SSOT.md (CC BY 4.0; name the NTA, link
it, say "as is"). Measurements: 30_Integrations/Live_Track_Intercity_Rail/20_Research/_shared/ireland/
IE_SOURCES_PROBE_20261109.md. Pure Python: NO homeassistant imports (05_SHARED_LESSONS.md B8.1a). One poller per Home
Assistant (reference/shared_irish_rail.py, B16).

READS, keyless, XML:
* getHaconTrainsXML (in the WSDL, NOT on the documentation page): every train the API knows, with `Difference` (seconds
  late), the last and next location codes and the origin's time. Positions identical to getCurrentTrainsXML (3,378 of
  3,450). Undocumented, so it may change: on any failure the documented getCurrentTrainsXML is read instead and its
  "(n mins late)" parsed (source "current"; no location codes there).
* getAllStationsXML: the station codes, names and positions, kept on disk weekly (.storage/saw_irish_rail_stations.json).
* the NTA's GTFS_Irish_Rail.zip, kept on disk (.storage/saw_nta_gtfs_irish_rail.zip), checked daily with a HEAD
  (Last-Modified), on its own thread. A failed download keeps the last copy.

FEED QUIRKS handled once, for every consumer:
* A position of 0,0 is NO position (748 running records in 4 h): lat and lon None.
* ScheduledArrival/ScheduledDeparture of 01/01/1900 00:00:00 means none: None.
* Every value is a string; times are Irish local time, DD/MM/YYYY.
* `PublicMessage` separates its lines with a literal backslash-n.
Ghosts (finished runs left `R` overnight) and untracked branch lines are the CONSUMER's to handle against its timetable.

POLITENESS (SAW's choice; nothing is published): one read a minute per install, FEED_TTL_S.
TLS: certifi's bundle when importable (Home Assistant ships it), else the system's. Verification is never turned off.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import ssl
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

from ._keyed import Backoff, FeedUnavailable, KeyedError, RateLimited, fetch  # noqa: F401

CONTRACT_VERSION = 1
SHARED_KEY = "saw_irish_rail_v%d" % CONTRACT_VERSION

BASE = "https://api.irishrail.ie/realtime/realtime.asmx/"
HACON_URL = BASE + "getHaconTrainsXML"
CURRENT_URL = BASE + "getCurrentTrainsXML"
STATIONS_URL = BASE + "getAllStationsXML"
STATIC_URL = "https://www.transportforireland.ie/transitData/Data/GTFS_Irish_Rail.zip"
TERMS_URL = "https://api.irishrail.ie/realtime/index.htm?realtime_irishrail"
NTA_URL = "https://www.transportforireland.ie/transitData/PT_Data.html"
DEFAULT_USER = "StrausbergAutomationWorks/saw-livetrack"
# Lee 2026-11-09 02:13 CST: "the credit line as proposed" (Intercity SSOT 8.10, decision 9). Linked to NTA_URL.
ATTRIBUTION = ("Data: Irish Rail (irishrail.ie); timetable: National Transport Authority, CC BY 4.0, "
               "provided as is")
TIME_ZONE = "Europe/Dublin"

INTERCITY = "intercity"
COMMUTER = "commuter"
TRANSIT = "transit"
CONSUMERS = (INTERCITY, COMMUTER, TRANSIT)
PREFIXES = {"A": INTERCITY, "B": INTERCITY, "P": COMMUTER, "D": COMMUTER, "E": TRANSIT}

# Positions change every ~3 min at the median (probe note 3): a 60 s poll always reads fresh.
FEED_TTL_S = 55.0
STATIC_TTL_S = 24 * 3600.0
STATIC_RETRY_S = 3600.0
STATIC_MAX_BYTES = 64 * 1024 * 1024
STATIC_NAME = "saw_nta_gtfs_irish_rail"
STATIONS_TTL_S = 7 * 24 * 3600.0
STATIONS_RETRY_S = 3600.0
STATIONS_NAME = "saw_irish_rail_stations.json"
MAX_BYTES = 8 * 1024 * 1024
_CHUNK = 1024 * 1024
_NS = "{http://api.irishrail.ie/realtime/}"
_LATE = re.compile(r"\((-?\d+)\s*mins?\s*late\)", re.I)
_CODE = re.compile(r"^([A-Z]+)(\d+)$")
_NO_TIME = "01/01/1900"


def _check_consumer(consumer):
    if consumer not in CONSUMERS:
        raise ValueError("unknown consumer %r" % (consumer,))


def consumer_of(code):
    """Who draws a train code: INTERCITY, COMMUTER, TRANSIT, or None."""
    c = (code or "").strip().upper() if isinstance(code, str) else ""
    if c.startswith("BUS"):
        return TRANSIT
    m = _CODE.match(c)
    return PREFIXES.get(m.group(1)) if m else None


def number_of(code):
    """'A700' -> '700' (the label: Lee 2026-11-09, "Label 700"); None when it is not a code."""
    m = _CODE.match((code or "").strip().upper()) if isinstance(code, str) else None
    return m.group(2) if m else None


def _zone():
    """Europe/Dublin from the IANA database; where there is none (Windows, slim containers, no tzdata: see pyproject's
    gtfs extra), Ireland's own rule: GMT, Irish Standard Time (UTC+1) from 01:00 UTC on the last Sunday of March to
    01:00 UTC on the last Sunday of October (the EU's summer-time rule)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TIME_ZONE)
    except Exception:  # noqa: BLE001 - no IANA database here
        return _IrishTime()


def _last_sunday(year, month):
    d = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year, 12, 31)
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


class _IrishTime(dt.tzinfo):
    """Ireland's offsets without the IANA database (_zone). Local wall times in the repeated October hour read as
    summer time."""

    def _summer(self, d):
        y = d.year
        start = dt.datetime.combine(_last_sunday(y, 3), dt.time(1))
        end = dt.datetime.combine(_last_sunday(y, 10), dt.time(1))
        # Wall time: summer starts at 01:00 GMT (01:00 on the clock) and ends at 01:00 GMT (02:00 on the clock).
        return start <= d.replace(tzinfo=None) < end + dt.timedelta(hours=1)

    def utcoffset(self, d):
        return dt.timedelta(hours=1) if d is not None and self._summer(d) else dt.timedelta(0)

    def dst(self, d):
        return self.utcoffset(d)

    def tzname(self, d):
        return "IST" if self.utcoffset(d) else "GMT"


def parse_time(value):
    """'09/11/2026 17:00:00' (Irish local time) -> epoch seconds; None for empty, unparseable or 01/01/1900."""
    v = (value or "").strip()
    if not v or v.startswith(_NO_TIME):
        return None
    try:
        d = dt.datetime.strptime(v, "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return None
    return d.replace(tzinfo=_zone()).timestamp()


def parse_date(value):
    """'09 Nov 2026' -> '2026-11-09'; None when unparseable."""
    try:
        return dt.datetime.strptime((value or "").strip(), "%d %b %Y").date().isoformat()
    except ValueError:
        return None


def _float(value):
    try:
        f = float((value or "").strip())
    except ValueError:
        return None
    return f if f == f else None


def _position(lat, lon):
    a, b = _float(lat), _float(lon)
    if a is None or b is None or (a == 0.0 and b == 0.0) or not (-90 <= a <= 90 and -180 <= b <= 180):
        return None, None
    return a, b


def _rows(raw, tag):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as err:
        raise FeedUnavailable("undecodable XML (%s)" % type(err).__name__) from None
    if not root.tag.startswith(_NS + "ArrayOf"):
        raise FeedUnavailable("unexpected XML root %r" % root.tag)
    for el in root.iter(_NS + tag):
        yield {child.tag[len(_NS):]: (child.text or "") for child in el}


def parse_hacon(raw):
    """getHaconTrainsXML -> [train dict]. Keys: code, status, lat, lon, date, delay_s, last_location,
    last_location_type, next_location, origin, origin_time, destination, destination_time, scheduled_departure,
    scheduled_arrival, direction, message (None here). An empty array is an empty list, never an error."""
    out = []
    for r in _rows(raw, "objHaconPositions"):
        code = (r.get("TrainCode") or "").strip().upper()
        if not code:
            continue
        lat, lon = _position(r.get("TrainLatitude"), r.get("TrainLongitude"))
        diff = r.get("Difference")
        try:
            delay = int((diff or "").strip())
        except ValueError:
            delay = None
        out.append({
            "code": code, "status": (r.get("TrainStatus") or "").strip().upper() or None, "lat": lat, "lon": lon,
            "date": parse_date(r.get("TrainDate")), "delay_s": delay,
            "last_location": (r.get("LastLocation") or "").strip() or None,
            "last_location_type": (r.get("LastLocationType") or "").strip().upper() or None,
            "next_location": (r.get("NextLocation") or "").strip() or None,
            "origin": (r.get("TrainOrigin") or "").strip() or None, "origin_time": parse_time(r.get("TrainOriginTime")),
            "destination": (r.get("TrainDestination") or "").strip() or None,
            "destination_time": parse_time(r.get("TrainDestinationTime")),
            "scheduled_departure": parse_time(r.get("ScheduledDeparture")),
            "scheduled_arrival": parse_time(r.get("ScheduledArrival")),
            "direction": (r.get("Direction") or "").strip() or None, "message": None})
    return out


def parse_current(raw):
    """getCurrentTrainsXML -> [train dict], the same keys as parse_hacon; what the documented call lacks is None.
    delay_s from the message's "(n mins late)"."""
    out = []
    for r in _rows(raw, "objTrainPositions"):
        code = (r.get("TrainCode") or "").strip().upper()
        if not code:
            continue
        lat, lon = _position(r.get("TrainLatitude"), r.get("TrainLongitude"))
        msg = (r.get("PublicMessage") or "").replace("\\n", "\n").strip()
        m = _LATE.search(msg)
        out.append({
            "code": code, "status": (r.get("TrainStatus") or "").strip().upper() or None, "lat": lat, "lon": lon,
            "date": parse_date(r.get("TrainDate")), "delay_s": int(m.group(1)) * 60 if m else None,
            "last_location": None, "last_location_type": None, "next_location": None, "origin": None,
            "origin_time": None, "destination": None, "destination_time": None, "scheduled_departure": None,
            "scheduled_arrival": None, "direction": (r.get("Direction") or "").strip() or None, "message": msg or None})
    return out


def parse_stations(raw):
    """getAllStationsXML -> [{code, name, alias, lat, lon, id}]; 0,0 is no position (four records, probe note 2)."""
    out = []
    for r in _rows(raw, "objStation"):
        code = (r.get("StationCode") or "").strip().upper()
        if not code:
            continue
        lat, lon = _position(r.get("StationLatitude"), r.get("StationLongitude"))
        out.append({"code": code, "name": (r.get("StationDesc") or "").strip(),
                    "alias": (r.get("StationAlias") or "").strip() or None, "lat": lat, "lon": lon,
                    "id": (r.get("StationId") or "").strip() or None})
    return out


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - no certifi: the system's store
        return ssl.create_default_context()


def default_opener():
    """urlopen with certifi's bundle (module docstring, TLS)."""
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_context())).open


class IrishRailClient:
    """Reads the feed ONCE for every consumer (B16: budget the IP).

    `consumers` is an iterable of consumer names; the shared registration (reference/shared_irish_rail.py) starts with
    the first integration's and register()s each that joins.

    for_consumer(name) -> [train dict] (parse_hacon's keys) holding only that consumer's codes. `source` says which call
    answered last: "hacon" or "current". Raises FeedUnavailable or RateLimited when neither answered; an empty list is a
    quiet hour (the API answers an empty array at night, MEASURED).

    THE TIMETABLE is on disk, not in memory: static_path(), static_version() (its Last-Modified). A consumer distills it
    again when the version changes. With no storage_dir there is no timetable and no station cache.
    """

    def __init__(self, consumers=(), user=DEFAULT_USER, timeout=30, opener=None, clock=None, feed_ttl_s=FEED_TTL_S,
                 storage_dir=None, static_timeout=300, background=True):
        if not user or not str(user).strip():
            raise ValueError("a non-empty User-Agent is required")
        self.user = user
        self.timeout = timeout
        self.static_timeout = static_timeout
        # Built at first use, in the caller's executor: the certifi context reads a file, and the shared registration
        # constructs this client on Home Assistant's event loop (0.2.33; HA flagged a blocking call on 0.2.32).
        self._opener_given = opener
        self._opener_made = None
        self._clock = clock or time.time
        self.feed_ttl_s = float(feed_ttl_s)
        self.storage_dir = storage_dir
        self._background = background
        self._lock = threading.RLock()
        self._state = threading.Lock()
        self._backoff = Backoff(self._clock)
        self._wanted = set()
        for consumer in consumers:
            _check_consumer(consumer)
            self._wanted.add(consumer)
        self._trains = None
        self._fetched_at = None
        self.source = None
        self.hacon_failures = 0
        self.fetch_count = 0
        self._version = None
        self._static_at = None
        self._static_retry = 0.0
        self._static_loaded = False
        self._loading = False
        self._stations = None
        self._stations_at = None
        self._stations_retry = 0.0

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

    @property
    def _opener(self):
        if self._opener_given is not None:
            return self._opener_given
        if self._opener_made is None:
            self._opener_made = default_opener()
        return self._opener_made

    def _get(self, url, max_bytes=MAX_BYTES):
        self.fetch_count += 1
        raw, _ = fetch(url, {"User-Agent": self.user}, self.timeout, self._opener, max_bytes=max_bytes)
        if len(raw) > max_bytes:
            raise FeedUnavailable("more than %d bytes from %s" % (max_bytes, url))
        return raw

    # --- the feed ---------------------------------------------------------------

    def poll(self, force=False):
        """Every train the API lists (both calls' shape), at most once per feed_ttl_s. Raises as for_consumer."""
        with self._lock:
            now = self._clock()
            if not force and self._trains is not None and now - self._fetched_at < self.feed_ttl_s:
                return self._trains
            self._backoff.check(HACON_URL)
            try:
                trains, source = parse_hacon(self._get(HACON_URL)), "hacon"
            except RateLimited as err:
                self._backoff.hit(err)
                raise
            except FeedUnavailable:
                self.hacon_failures += 1
                try:
                    trains, source = parse_current(self._get(CURRENT_URL)), "current"
                except RateLimited as err:
                    self._backoff.hit(err)
                    raise
            self._trains, self._fetched_at, self.source = trains, now, source
        self._ensure_static()
        return trains

    def for_consumer(self, consumer, force=False):
        """[train dict] for `consumer`'s codes only (module docstring)."""
        _check_consumer(consumer)
        return [t for t in self.poll(force) if consumer_of(t["code"]) == consumer]

    # --- the stations -----------------------------------------------------------

    def _stations_path(self):
        return os.path.join(self.storage_dir, STATIONS_NAME) if self.storage_dir else None

    def stations(self, fetch_now=True):
        """[{code, name, alias, lat, lon, id}], refreshed weekly; the last copy when a read fails; [] with none."""
        with self._lock:
            now = self._clock()
            if self._stations is None:
                self._load_stations()
            fresh = self._stations_at is not None and now - self._stations_at < STATIONS_TTL_S
            if fetch_now and not fresh and now >= self._stations_retry:
                self._stations_retry = now + STATIONS_RETRY_S
                try:
                    got = parse_stations(self._get(STATIONS_URL))
                except KeyedError:
                    got = None
                if got:
                    self._stations, self._stations_at = got, now
                    self._save_stations(got, now)
            return list(self._stations or [])

    def stations_version(self):
        with self._lock:
            return self._stations_at

    def _load_stations(self):
        path = self._stations_path()
        if path is None:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return
        if isinstance(d, dict) and isinstance(d.get("stations"), list):
            self._stations, self._stations_at = d["stations"], d.get("fetched_at")

    def _save_stations(self, got, now):
        path = self._stations_path()
        if path is None:
            return
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"fetched_at": now, "stations": got}, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass

    # --- the timetable ----------------------------------------------------------

    def _paths(self):
        base = os.path.join(self.storage_dir, STATIC_NAME)
        return base + ".zip", base + ".json"

    def static_path(self):
        """The NTA's Irish Rail GTFS zip on disk, or None before one exists."""
        if self.storage_dir is None:
            return None
        self._load_cached()
        with self._state:
            have = self._version is not None
        path = self._paths()[0]
        return path if have and os.path.exists(path) else None

    def static_version(self):
        """Changes when the timetable does (its Last-Modified)."""
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
        except (OSError, ValueError):
            return
        if os.path.exists(zp):
            with self._state:
                self._version, self._static_at = d.get("version"), d.get("checked_at")

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
        except Exception:  # noqa: BLE001 - keep the last copy; the feed does not need it
            return False
        finally:
            with self._state:
                self._loading = False

    def _refresh(self, now):
        import zipfile
        self.fetch_count += 1
        _, got = fetch(STATIC_URL, {"User-Agent": self.user}, self.timeout, self._opener, method="HEAD")
        version = got.get("Last-Modified") or None
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
                names = set(z.namelist())
            if not {"routes.txt", "trips.txt", "stop_times.txt", "stops.txt", "shapes.txt"} <= names:
                raise FeedUnavailable("timetable zip lacks a required file")
            os.replace(tmp, zp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        version = version or ("downloaded %d" % int(now))
        self._write_meta(meta, version, now)
        with self._state:
            self._version, self._static_at = version, now
        return True

    def _download(self, tmp):
        self.fetch_count += 1
        req = urllib.request.Request(STATIC_URL, headers={"User-Agent": self.user})
        total = 0
        try:
            with self._opener(req, timeout=self.static_timeout) as resp, open(tmp, "wb") as out:
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
        self._load_cached()
        with self._state:
            due = self._static_due(self._clock()) and not self._loading
        if not due:
            return
        if not self._background:
            self.refresh_static()
            return
        threading.Thread(target=self.refresh_static, name="saw_irish_rail_timetable", daemon=True).start()

    def stats(self):
        """Low-cardinality state for a diagnostic."""
        with self._lock:
            return {"trains": len(self._trains or ()), "source": self.source, "hacon_failures": self.hacon_failures,
                    "fetch_count": self.fetch_count, "stations": len(self._stations or ()),
                    "timetable": self._version is not None, "consumers": sorted(self._wanted),
                    "backing_off": self._clock() < self._backoff.until}
