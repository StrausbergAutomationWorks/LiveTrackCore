"""Shared Trafikverket client (Sweden's open rail traffic data) for the Live Track family.

Hoisted at first use (35_Dependencies/README.md: a feed already known to serve several integrations is hoisted at its
first use): Sweden's trains are split by Trafikverket's TypeOfTraffic and product between Live Track Intercity Rail,
Live Track Commuter Rail and Live Track Transit (Lee 2026-11-08; Intercity SSOT 8.9, backlog 327). Provider:
35_Dependencies/third_party/trafikverket/SSOT.md. Pure Python: NO homeassistant imports (05_SHARED_LESSONS.md B8.1a).
One poller per Home Assistant (reference/shared_trafikverket.py, B16).

Data: Trafikverket, CC0 1.0 (the API agreement, read 2026-11-08). Each user brings their own free key; the client
polls with the oldest key registered (saw_livetrack._keyed: a key is given up on after 10 minutes of refusals). One
endpoint, POST an XML query, JSON back:
    https://api.trafikinfo.trafikverket.se/v2/data.json
    TrainPosition     (namespace järnväg.trafikinfo, 1.1)  every passenger train's GPS fix, about every 16 s (p50)
    TrainAnnouncement (1.9)                                one row per station call: advertised, estimated, actual
    TrainStation      (namespace rail.infrastructure, 1.5) 1,757 stations: signature, name, WGS84, country

THE POSITION CARRIES NO OPERATOR, so a run is joined to its announcements on the advertised train number and the
run's date: TrainPosition Train.AdvertisedTrainNumber + Train.JourneyPlanDepartureDate is TrainAnnouncement
AdvertisedTrainIdent + ScheduledDepartureDateTime (MEASURED 2026-11-09 00:1xZ: 62 of 62 positioned trains).

EACH RUN GOES TO ONE CONSUMER (06_MAP_CONTRACT.md D1), from its announcements (route_of):

    TypeOfTraffic Tåg         the brand (BRANDS, the run's first ProductInformation)   intercity
    Tåg, operator ATRAIN      Arlanda Express (no product, not advertised)              commuter
    TypeOfTraffic Pendeltåg   SL Pendeltåg, Göteborg's Västtågen pendeltåg              commuter
    TypeOfTraffic Buss        rail-replacement buses on train numbers                  transit
    Vy, Vy Snabbtåg, or no product with operator VY: Vy's own cross-border trains        nobody (Norway's Vy entry
                                                                                         owns them end to end)

TypeOfTraffic is constant along every run (MEASURED on 3,403 runs of 2026-11-09, note §9). A product not in BRANDS
falls to its OPERATOR's own brand where the operator has exactly one family (OPERATOR_BRANDS: SJ's charters and the
EuroNight it runs, Tågab's "Abonnerat tåg", MTRX's and SNÄLL's runs with no product); every other is nobody's and is
counted in stats()["unrouted"] by its product string, so a new string is seen, not guessed at. INLANDSBANAN's product
string could not be read (no run in the API's whole window, 2026-11-05 to 2026-11-23, measured 2026-11-08): a product
starting "Inlandsban" is taken as it (is_inlandsbanan), and its first season must be checked.

READS, per poll, at most one of each:
* positions: changeid deltas (the first read changeid="0": without it the answer carries no INFO.LASTCHANGEID and
  every read is the whole set, MEASURED 2026-11-09). A fix older than POSITION_MAX_AGE_S is dropped.
* new runs: a positioned run not yet known has its announcements read WHOLE, NEW_RUN_BATCH numbers a query, at most
  NEW_RUN_QUERIES queries a poll (a cold start spreads over a few polls).
* changes: the rows of the claimed runs MODIFIED since the newest ModifiedTime of the last answer less
  MODIFIED_OVERLAP_S (the server's own clock; MEASURED: about 55 rows per 30 s at night).
* stations: once a day, kept on disk (.storage/saw_trafikverket_stations.json, a SAW-neutral name).
A day's announcements are 68,713 rows (note §9): never read whole here.

TLS: certifi's bundle when it is importable (Home Assistant ships it), else the system's. MEASURED 2026-11-08: the
Windows store lacks Telia's root this server needs; Home Assistant's container verifies with either. Verification is
never turned off.

A KEY IS NEVER PRINTED: it travels only in the request body, and no message carries the body.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from xml.sax.saxutils import quoteattr

from ._keyed import (AuthRefused, Backoff, FeedUnavailable, KeyedError, KeyRefused, KeyRing,  # noqa: F401
                     RateLimited)

CONTRACT_VERSION = 1
SHARED_KEY = "saw_trafikverket_v%d" % CONTRACT_VERSION

URL = "https://api.trafikinfo.trafikverket.se/v2/data.json"
KEY_URL = "https://data.trafikverket.se/oauth2/Account/register"
TERMS_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
SOURCE_URL = "https://data.trafikverket.se"
DEFAULT_USER = "StrausbergAutomationWorks/saw-livetrack"
# CC0 asks for no credit; the wording is Lee's (2026-11-08 ~18:2x CST: "Data: Trafikverket, CC0").
ATTRIBUTION = "Data: Trafikverket (trafikverket.se), CC0"
TIME_ZONE = "Europe/Stockholm"

INTERCITY = "intercity"
COMMUTER = "commuter"
TRANSIT = "transit"
CONSUMERS = (INTERCITY, COMMUTER, TRANSIT)

TYPE_TRAIN = "Tåg"
TYPE_COMMUTER = "Pendeltåg"
TYPE_BUS = "Buss"

# ProductInformation (the run's first) -> brand id. Intercity's ids are its operator ids (Lee 2026-11-08 ~15:4x CST,
# Intercity SSOT 8.9 decision 4), permanent from the first entry. Census strings, MEASURED (note §9; 2026-11-04 to
# 11-09, bin\\se_inland2.txt).
BRANDS = {
    "SJ Snabbtåg": "sj", "SJ Regional": "sj", "SJ InterCity": "sj", "SJ Nattåg": "sj", "SJ": "sj",
    "VR Snabbtåg": "vr_snabbtag",
    "Snälltåget": "snalltaget",
    "Öresundståg": "oresundstag",
    "Pågatågen": "pagatagen", "Pågatågen Exp": "pagatagen",
    "Mälartåg": "malartag",
    "Västtågen": "vasttagen",
    "Krösatågen": "krosatagen",
    "Östgötapendel": "ostgotapendeln",
    "Norrtåg": "norrtag",
    "TiB": "tag_i_bergslagen",
    "X-Tåget": "x_taget",
    "VTAB": "varmlandstrafik",
    "Tågab": "tagab",
    "SL Pendeltåg": "sl_pendeltag",
}
INLANDSBANAN = "inlandsbanan"
# A run whose product is not in BRANDS, by its Operator, where that operator runs exactly one brand family (MEASURED
# 2026-11-04 to 11-09: SJ Charter and RDC EuroNight with operator SJ; Abonnerat tåg with TÅGAB; MTRX and SNÄLL runs
# with no product). ARRIVA, TDEV and SJ's tendered brands are named by product, never by operator.
OPERATOR_BRANDS = {"SJ": "sj", "MTRX": "vr_snabbtag", "SNÄLL": "snalltaget", "TÅGAB": "tagab"}
# Vy's own trains (decision 2: Norway's Vy entry owns its cross-border trains end to end).
NORWAY_PRODUCTS = frozenset({"Vy", "Vy Snabbtåg"})
NORWAY_OPERATORS = frozenset({"VY"})
ARLANDA_OPERATOR = "ATRAIN"
ARLANDA_EXPRESS = "arlanda_express"

# One (number, date) can hold TWO trips: Öresundståg 1154 and 1165 every day measured (2026-11-09, 14, 15), 20-23 h
# apart; no other intercity run has a gap over 3 h between its rows. Rows are split where they are this far apart, and
# a train is given the trip nearest its fix (trip_of).
TRIP_GAP_S = 6 * 3600.0
TRIP_BEFORE_S = 2 * 3600.0
TRIP_AFTER_S = 6 * 3600.0

POSITION_FIELDS = ("Train.AdvertisedTrainNumber", "Train.JourneyPlanDepartureDate", "Train.OperationalTrainNumber",
                   "Position.WGS84", "Bearing", "Speed", "TimeStamp", "Deleted")
ANNOUNCEMENT_FIELDS = ("ActivityId", "AdvertisedTrainIdent", "ScheduledDepartureDateTime", "ActivityType",
                       "LocationSignature", "AdvertisedTimeAtLocation", "EstimatedTimeAtLocation", "TimeAtLocation",
                       "Operator", "ProductInformation", "TypeOfTraffic", "Advertised", "Canceled", "Deleted",
                       "ModifiedTime")
STATION_FIELDS = ("LocationSignature", "AdvertisedLocationName", "Geometry.WGS84", "CountryCode")

FEED_TTL_S = 25.0
# A fix this old is no train on the map (MEASURED 2026-11-08: fix age p90 82 s, max 320 s on a live read).
POSITION_MAX_AGE_S = 15 * 60.0
POSITION_WINDOW = "-0.00:15:00"
NEW_RUN_BATCH = 50
NEW_RUN_QUERIES = 4
# A number with no announcement is asked again after this long (an unannounced train: Arlanda Express' 78xx).
UNANNOUNCED_RETRY_S = 600.0
MODIFIED_OVERLAP_S = 120.0
# A run is forgotten this long after its position went and its last row's time passed.
FORGET_S = 3600.0
STATIONS_TTL_S = 24 * 3600.0
STATIONS_RETRY_S = 3600.0
STATIONS_NAME = "saw_trafikverket_stations.json"
MAX_BYTES = 32 * 1024 * 1024

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_POINT = re.compile(r"POINT\s*\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)")


def _check_consumer(consumer):
    if consumer not in CONSUMERS:
        raise ValueError("unknown consumer %r" % (consumer,))


def _desc(row, field, i=0):
    items = row.get(field) or ()
    try:
        return (items[i].get("Description") or "").strip() or None
    except (IndexError, AttributeError):
        return None


def is_inlandsbanan(product):
    return isinstance(product, str) and product.strip().casefold().startswith("inlandsban")


def brand_of(product, operator):
    """The brand id of a run's product, else its operator's own brand (OPERATOR_BRANDS), else None."""
    if product in BRANDS:
        return BRANDS[product]
    if is_inlandsbanan(product):
        return INLANDSBANAN
    return OPERATOR_BRANDS.get(operator)


def route_of(rows):
    """(consumer, brand) of one run from its announcement rows, or None (Norway's, unknown, empty)."""
    if not rows:
        return None
    first = rows[0]
    kind = _desc(first, "TypeOfTraffic")
    product = _desc(first, "ProductInformation")
    operator = (first.get("Operator") or "").strip() or None
    if product in NORWAY_PRODUCTS or (product is None and operator in NORWAY_OPERATORS):
        return None
    if kind == TYPE_BUS:
        brand = brand_of(product, operator)
        return (TRANSIT, brand) if brand else None
    if kind == TYPE_COMMUTER:
        brand = brand_of(product, operator)
        return (COMMUTER, brand) if brand else None
    if kind != TYPE_TRAIN:
        return None
    if operator == ARLANDA_OPERATOR:
        return COMMUTER, ARLANDA_EXPRESS
    brand = brand_of(product, operator)
    if brand is None or brand == "sl_pendeltag":
        return None
    return INTERCITY, brand


def parse_time(value):
    """'2026-11-09T07:12:00.000+01:00' -> epoch seconds; None for anything else."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_point(wkt):
    """'POINT (18.05 59.33)' -> (59.33, 18.05) as (lat, lon); None otherwise."""
    m = _POINT.match(wkt.strip()) if isinstance(wkt, str) else None
    if not m:
        return None
    lon, lat = float(m.group(1)), float(m.group(2))
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0) or (lat == 0.0 and lon == 0.0):
        return None
    return round(lat, 6), round(lon, 6)


def local_midnight(day):
    """A date -> its Stockholm midnight as ISO 8601 with the offset in force ('2026-11-09T00:00:00+01:00')."""
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(TIME_ZONE)
    except Exception:  # noqa: BLE001 - no tz database: Sweden's winter offset
        zone = datetime.timezone(datetime.timedelta(hours=1))
    return datetime.datetime(day.year, day.month, day.day, tzinfo=zone).isoformat()


def run_key(number, date):
    """'525', '2026-11-09T00:00:00.000+01:00' -> '525|2026-11-09'."""
    return "%s|%s" % (str(number).strip(), (date or "")[:10])


def _query(objecttype, fields, filt="", schema="1.9", namespace=None, extra=""):
    ns = ' namespace="%s"' % namespace if namespace else ""
    inc = "".join("<INCLUDE>%s</INCLUDE>" % f for f in fields)
    flt = "<FILTER>%s</FILTER>" % filt if filt else ""
    return '<QUERY objecttype="%s"%s schemaversion="%s"%s>%s%s</QUERY>' % (objecttype, ns, schema, extra, inc, flt)


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - no certifi: the system's store
        return ssl.create_default_context()


def post(key, query, user=DEFAULT_USER, timeout=30, opener=None, max_bytes=MAX_BYTES, context=None):
    """One query's RESULT (a dict). Raises RateLimited, KeyRefused or FeedUnavailable; no message holds the body,
    which holds the key."""
    body = ("<REQUEST><LOGIN authenticationkey=%s/>%s</REQUEST>" % (quoteattr(key), query)).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST",
                                 headers={"Content-Type": "text/xml; charset=utf-8", "User-Agent": user})
    try:
        if opener is not None:
            resp_cm = opener(req, timeout=timeout)
        else:
            resp_cm = urllib.request.urlopen(req, timeout=timeout, context=context or _ssl_context())
        with resp_cm as resp:
            raw = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as err:
        if err.code == 429:
            exc = RateLimited("HTTP 429 from %s" % URL)
            try:
                exc.retry_after = float(err.headers.get("Retry-After"))
            except (AttributeError, TypeError, ValueError):
                exc.retry_after = None
            raise exc from None
        if err.code in (401, 403):
            raise KeyRefused("HTTP %s from %s" % (err.code, URL)) from None
        raise FeedUnavailable("HTTP %s from %s" % (err.code, URL)) from None
    except Exception as err:  # noqa: BLE001 - transport of any kind
        raise FeedUnavailable("%s from %s" % (type(err).__name__, URL)) from None
    if len(raw) > max_bytes:
        raise FeedUnavailable("more than %d bytes from %s" % (max_bytes, URL))
    try:
        result = json.loads(raw)["RESPONSE"]["RESULT"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        raise FeedUnavailable("undecodable answer from %s" % URL) from None
    if not isinstance(result, dict):
        raise FeedUnavailable("undecodable answer from %s" % URL)
    if result.get("ERROR"):
        raise FeedUnavailable("the query was refused: %s" % str(result["ERROR"].get("MESSAGE", ""))[:120]
                              if isinstance(result["ERROR"], dict) else "the query was refused")
    return result


def position_record(p):
    """One TrainPosition row -> {"number", "date", "key", "lat", "lon", "bearing", "speed", "ts", "operational"} or
    None (no number, no date, no point, no time). Speed only when the row has one (about half do, measured)."""
    t = p.get("Train") or {}
    number = (t.get("AdvertisedTrainNumber") or "").strip()
    date = (t.get("JourneyPlanDepartureDate") or "")[:10]
    pt = parse_point((p.get("Position") or {}).get("WGS84"))
    ts = parse_time(p.get("TimeStamp"))
    if not number or not _DATE.match(date) or pt is None or ts is None:
        return None
    speed = p.get("Speed")
    bearing = p.get("Bearing")
    return {"number": number, "date": date, "key": run_key(number, date), "lat": pt[0], "lon": pt[1],
            "bearing": float(bearing) if isinstance(bearing, (int, float)) and not isinstance(bearing, bool) else None,
            "speed": float(speed) if isinstance(speed, (int, float)) and not isinstance(speed, bool) else None,
            "ts": ts, "operational": (t.get("OperationalTrainNumber") or "").strip() or None}


def call_record(r):
    """One TrainAnnouncement row -> the shape a consumer reads: {"id", "sig", "activity" ("Ankomst"/"Avgang"),
    "advertised", "estimated", "actual" (epoch or None), "public" (Advertised), "canceled"}."""
    return {"id": r.get("ActivityId"), "sig": (r.get("LocationSignature") or "").strip(),
            "activity": r.get("ActivityType"), "advertised": parse_time(r.get("AdvertisedTimeAtLocation")),
            "estimated": parse_time(r.get("EstimatedTimeAtLocation")), "actual": parse_time(r.get("TimeAtLocation")),
            "public": r.get("Advertised") is not False, "canceled": r.get("Canceled") is True}


def split_trips(calls, gap_s=TRIP_GAP_S):
    """Ordered calls (call_record) -> [trip, ...], split where two consecutive advertised times are gap_s or more apart."""
    trips, cur, last = [], [], None
    for c in calls:
        t = c.get("advertised")
        if cur and t is not None and last is not None and t - last >= gap_s:
            trips.append(cur)
            cur = []
        cur.append(c)
        if t is not None:
            last = t
    if cur:
        trips.append(cur)
    return trips


def trip_of(calls, when):
    """The trip of a run's calls a train seen at `when` is on: of the trips whose advertised span (less TRIP_BEFORE_S,
    plus TRIP_AFTER_S) holds `when`, the one whose span itself is nearest; with none, the nearest; [] for none."""
    best = None
    for trip in split_trips(calls):
        ts = [c["advertised"] for c in trip if c.get("advertised") is not None]
        if not ts:
            continue
        lo, hi = min(ts), max(ts)
        held = lo - TRIP_BEFORE_S <= when <= hi + TRIP_AFTER_S
        near = 0.0 if lo <= when <= hi else min(abs(when - lo), abs(when - hi))
        rank = (not held, near)
        if best is None or rank < best[0]:
            best = (rank, trip)
    return best[1] if best else []


def _order(rows):
    return sorted(rows, key=lambda r: (r.get("AdvertisedTimeAtLocation") or "", r.get("ActivityType") != "Ankomst"))


class _Run:
    __slots__ = ("key", "number", "date", "route", "rows", "product", "operator", "last_t")

    def __init__(self, key, number, date, rows):
        self.key, self.number, self.date = key, number, date
        self.rows = {}
        for r in rows:
            self.rows[r.get("ActivityId") or id(r)] = r
        ordered = _order(self.rows.values())
        self.route = route_of(ordered)
        first = ordered[0] if ordered else {}
        self.product = _desc(first, "ProductInformation")
        self.operator = (first.get("Operator") or "").strip() or None
        self.last_t = max((parse_time(r.get("AdvertisedTimeAtLocation")) or 0.0 for r in ordered), default=0.0)

    def merge(self, row):
        rid = row.get("ActivityId")
        if rid is None:
            return
        if row.get("Deleted") is True:
            self.rows.pop(rid, None)
        else:
            self.rows[rid] = row
        self.last_t = max(self.last_t, parse_time(row.get("AdvertisedTimeAtLocation")) or 0.0)

    def calls(self):
        return [call_record(r) for r in _order(self.rows.values())]


class TrafikverketClient:
    """Polls Trafikverket ONCE for every consumer (B16: budget the IP).

    `claims` are (consumer, brand) pairs, each with its config entry's key: {(consumer, brand): key}. The shared
    registration (reference/shared_trafikverket.py) starts with the first entry's claim and register()s each that
    joins.

    for_consumer(consumer, brand) -> [run, ...]: every positioned run of that brand routed to that consumer, as
    {"key", "number", "date", "brand", "product", "operator", "position", "calls"} (position_record, call_record;
    the calls of the trip the train is on, trip_of).
    stations() -> {signature: {"name", "lat", "lon", "country"}}, read daily and kept on disk.
    """

    def __init__(self, claims, user=DEFAULT_USER, timeout=30, opener=None, clock=None, feed_ttl_s=FEED_TTL_S,
                 storage_dir=None):
        if not user or not str(user).strip():
            raise ValueError("a non-empty User-Agent is required")
        self.user = user
        self.timeout = timeout
        self._opener = opener
        self._clock = clock or time.time
        self.feed_ttl_s = float(feed_ttl_s)
        self.storage_dir = storage_dir
        self._lock = threading.RLock()
        self._state = threading.Lock()
        self._ring = KeyRing(self._clock)
        self._backoff = Backoff(self._clock)
        self._claims = set()
        for claim, key in dict(claims).items():
            self._check_claim(claim)
            self._ring.add(claim, key)
            self._claims.add(claim)
        self._changeid = None
        self._positions = {}        # run key -> position record
        self._runs = {}             # run key -> _Run
        self._unannounced = {}      # run key -> when last asked
        self._cursor = None         # newest ModifiedTime seen (server clock, ISO)
        self._fetched_at = None
        self._stations = None
        self._stations_at = None
        self._stations_retry = 0.0
        self.fetch_count = 0
        self.unrouted = {}          # product string (or "operator:<op>") -> runs, for a new string to be seen

    @staticmethod
    def _check_claim(claim):
        if not (isinstance(claim, tuple) and len(claim) == 2):
            raise ValueError("a claim is (consumer, brand), not %r" % (claim,))
        _check_consumer(claim[0])
        if not isinstance(claim[1], str) or not claim[1]:
            raise ValueError("a claim needs a brand id")

    @property
    def claims(self):
        with self._state:
            return frozenset(self._claims)

    def register(self, consumer, brand, key):
        """Serve (consumer, brand) from now on, with its key. True if new. Never waits for a fetch."""
        claim = (consumer, brand)
        self._check_claim(claim)
        self._ring.add(claim, key)
        with self._state:
            if claim in self._claims:
                return False
            self._claims.add(claim)
            return True

    def unregister(self, consumer, brand):
        """Stop serving (consumer, brand) and forget its key. True if it was registered. Never waits."""
        claim = (consumer, brand)
        self._check_claim(claim)
        self._ring.remove(claim)
        with self._state:
            if claim not in self._claims:
                return False
            self._claims.discard(claim)
            return True

    # --- the requests -----------------------------------------------------------

    def _post(self, query):
        self._backoff.check(URL)
        key = self._ring.active()
        self.fetch_count += 1
        try:
            result = post(key, query, self.user, self.timeout, self._opener)
        except RateLimited as err:
            self._backoff.hit(err)
            raise
        except KeyRefused:
            if self._ring.refused(key):
                self._ring.active()     # AuthRefused now, when that was the last key
            raise
        self._ring.ok()
        return result

    def _read_positions(self, now):
        q = _query("TrainPosition", POSITION_FIELDS, '<GT name="TimeStamp" value="$dateadd(%s)"/>' % POSITION_WINDOW,
                   schema="1.1", namespace="järnväg.trafikinfo", extra=' changeid="%s"' % (self._changeid or "0"))
        try:
            result = self._post(q)
        except FeedUnavailable:
            # A changeid the server no longer knows is not worth a second guess: read the whole set next time.
            self._changeid = None
            raise
        cid = (result.get("INFO") or {}).get("LASTCHANGEID")
        self._changeid = str(cid) if cid else None
        for p in result.get("TrainPosition") or ():
            if p.get("Deleted") is True:
                t = p.get("Train") or {}
                self._positions.pop(run_key(t.get("AdvertisedTrainNumber") or "", t.get("JourneyPlanDepartureDate")),
                                    None)
                continue
            rec = position_record(p)
            if rec is None:
                continue
            old = self._positions.get(rec["key"])
            if old is None or rec["ts"] >= old["ts"]:
                self._positions[rec["key"]] = rec
        for k in [k for k, v in self._positions.items() if now - v["ts"] > POSITION_MAX_AGE_S]:
            del self._positions[k]

    @staticmethod
    def _window(dates):
        """The run dates' span as two Stockholm midnights with their offsets (the server's ScheduledDepartureDateTime
        is a local midnight, '2026-11-09T00:00:00.000+01:00'): from the first date to the day after the last."""
        days = sorted(dates)
        a = datetime.date.fromisoformat(days[0])
        b = datetime.date.fromisoformat(days[-1]) + datetime.timedelta(days=1)
        return local_midnight(a), local_midnight(b)

    def _read_new_runs(self, now):
        want = [k for k in self._positions if k not in self._runs
                and now - self._unannounced.get(k, -1e18) >= UNANNOUNCED_RETRY_S]
        if not want:
            return
        want.sort()
        for i in range(0, min(len(want), NEW_RUN_BATCH * NEW_RUN_QUERIES), NEW_RUN_BATCH):
            part = want[i:i + NEW_RUN_BATCH]
            numbers = sorted({self._positions[k]["number"] for k in part})
            a, b = self._window({self._positions[k]["date"] for k in part})
            filt = ('<AND><IN name="AdvertisedTrainIdent" value="%s"/>'
                    '<GTE name="ScheduledDepartureDateTime" value="%s"/>'
                    '<LT name="ScheduledDepartureDateTime" value="%s"/></AND>' % (",".join(numbers), a, b))
            result = self._post(_query("TrainAnnouncement", ANNOUNCEMENT_FIELDS, filt))
            grouped = {}
            for r in result.get("TrainAnnouncement") or ():
                if r.get("Deleted") is True:
                    continue
                k = run_key(r.get("AdvertisedTrainIdent") or "", r.get("ScheduledDepartureDateTime"))
                grouped.setdefault(k, []).append(r)
                self._advance(r.get("ModifiedTime"))
            for k in part:
                rows = grouped.get(k)
                if not rows:
                    self._unannounced[k] = now
                    continue
                self._unannounced.pop(k, None)
                pos = self._positions[k]
                run = _Run(k, pos["number"], pos["date"], rows)
                self._runs[k] = run
                if run.route is None or run.route[0] not in CONSUMERS:
                    label = run.product or ("operator:%s" % run.operator)
                    self.unrouted[label] = self.unrouted.get(label, 0) + 1

    def _advance(self, modified):
        if isinstance(modified, str) and modified and (self._cursor is None or modified > self._cursor):
            self._cursor = modified

    def _read_changes(self, claimed_routes):
        idents = sorted({r.number for r in self._runs.values() if r.route in claimed_routes})
        if not idents or self._cursor is None:
            return
        since = parse_time(self._cursor)
        if since is None:
            return
        since_iso = datetime.datetime.fromtimestamp(since - MODIFIED_OVERLAP_S, datetime.timezone.utc).isoformat()
        dates = {r.date for r in self._runs.values() if r.route in claimed_routes}
        a, _ = self._window(dates)
        filt = ('<AND><IN name="AdvertisedTrainIdent" value="%s"/><GTE name="ScheduledDepartureDateTime" '
                'value="%s"/><GT name="ModifiedTime" value="%s"/></AND>' % (",".join(idents), a, since_iso))
        result = self._post(_query("TrainAnnouncement", ANNOUNCEMENT_FIELDS, filt))
        for r in result.get("TrainAnnouncement") or ():
            self._advance(r.get("ModifiedTime"))
            run = self._runs.get(run_key(r.get("AdvertisedTrainIdent") or "", r.get("ScheduledDepartureDateTime")))
            if run is not None:
                run.merge(r)

    def _forget(self, now):
        for k in [k for k, r in self._runs.items() if k not in self._positions and now - r.last_t > FORGET_S]:
            del self._runs[k]
        for k in [k for k, t in self._unannounced.items() if k not in self._positions]:
            del self._unannounced[k]

    def poll(self, force=False):
        """Read once for every claim, at most once per feed_ttl_s. Raises the read's error (the last good state is
        kept)."""
        with self._lock:
            now = self._clock()
            if not force and self._fetched_at is not None and now - self._fetched_at < self.feed_ttl_s:
                return
            with self._state:
                claimed = frozenset(self._claims)
            self._read_positions(now)
            self._read_new_runs(now)
            self._read_changes(claimed)
            self._forget(now)
            self._fetched_at = now

    def for_consumer(self, consumer, brand, force=False):
        claim = (consumer, brand)
        self._check_claim(claim)
        if claim not in self.claims:
            raise ValueError("%r is not registered with this client" % (claim,))
        self.poll(force=force)
        with self._lock:
            out = []
            for k, run in self._runs.items():
                if run.route != claim:
                    continue
                pos = self._positions.get(k)
                if pos is None:
                    continue
                out.append({"key": k, "number": run.number, "date": run.date, "brand": brand, "product": run.product,
                            "operator": run.operator, "position": dict(pos), "calls": trip_of(run.calls(), pos["ts"])})
            return out

    # --- the stations ------------------------------------------------------------

    def _stations_path(self):
        return os.path.join(self.storage_dir, STATIONS_NAME) if self.storage_dir else None

    def stations(self, fetch=True):
        """{signature: {"name", "lat", "lon", "country"}}; read from Trafikverket at most daily (the disk copy
        first), never raising: {} until one read works."""
        with self._state:
            have, at, retry = self._stations, self._stations_at, self._stations_retry
        if have is None:
            have, at = self._load_stations()
        now = self._clock()
        if fetch and (at is None or now - at >= STATIONS_TTL_S) and now >= retry:
            with self._state:
                self._stations_retry = now + STATIONS_RETRY_S
            try:
                result = self._post(_query("TrainStation", STATION_FIELDS, schema="1.5",
                                           namespace="rail.infrastructure"))
                got = {}
                for s in result.get("TrainStation") or ():
                    sig = (s.get("LocationSignature") or "").strip()
                    pt = parse_point((s.get("Geometry") or {}).get("WGS84"))
                    if sig and pt is not None:
                        got[sig] = {"name": (s.get("AdvertisedLocationName") or sig).strip(), "lat": pt[0],
                                    "lon": pt[1], "country": s.get("CountryCode")}
                if got:
                    have, at = got, now
                    self._save_stations(got, now)
            except KeyedError:
                pass
        with self._state:
            self._stations, self._stations_at = have, at
        return dict(have or {})

    def stations_version(self):
        with self._state:
            return None if self._stations_at is None else int(self._stations_at)

    def _load_stations(self):
        path = self._stations_path()
        if path is None:
            return None, None
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            return dict(d["stations"]), float(d["read_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return None, None

    def _save_stations(self, got, now):
        path = self._stations_path()
        if path is None:
            return
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"read_at": now, "stations": got}, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass

    def stats(self):
        """Low-cardinality state for a diagnostic. Never a key."""
        with self._lock:
            routes = {}
            for r in self._runs.values():
                label = "%s:%s" % r.route if r.route else "none"
                routes[label] = routes.get(label, 0) + 1
            return {"positions": len(self._positions), "runs": len(self._runs), "runs_by_route": routes,
                    "unannounced": len(self._unannounced), "unrouted": dict(self.unrouted),
                    "fetch_count": self.fetch_count, "changeid": self._changeid is not None,
                    "backing_off": self._clock() < self._backoff.until, **self._ring.state()}


def check_key(key, user=DEFAULT_USER, timeout=30, opener=None):
    """One small read with a user's key, for a setup form: None when it works, else "invalid_key" or
    "cannot_connect". Blocking."""
    try:
        post(key, _query("TrainStation", ("LocationSignature",), schema="1.5", namespace="rail.infrastructure",
                         extra=' limit="1"'), user, timeout, opener)
    except KeyRefused:
        return "invalid_key"
    except KeyedError:
        return "cannot_connect"
    return None
