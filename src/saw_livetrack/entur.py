"""Shared Entur client (Norway's national public-transport data) for the Live Track family.

Shared from first use by the hoist rule (Lee 2026-10-20: a feed already known to serve several integrations is hoisted
at its first use): Norway's trains are split by LINE LETTER between Live Track Intercity Rail and Live Track Commuter
Rail (Lee 2026-11-07, backlog 326). Provider: 35_Dependencies/third_party/entur/SSOT.md. Pure Python: NO homeassistant
imports (05_SHARED_LESSONS.md B8.1a). One poller per Home Assistant (reference/shared_entur.py, B16).

Data: Entur AS, NLOD (Norwegian licence for Open Government data). Keyless; Entur asks every consumer to identify
itself with the header ET-Client-Name, sent here as one fixed name for every consumer (CLIENT_NAME). Per CODESPACE (the
railroad: VYG Vy, GOA Go-Ahead Norge, FLT Flytoget; SJN SJ Nord publishes no positions):
    realtime/v2/vehicles/graphql, vehicles(codespaceId)             positions (POST, GraphQL)
    realtime/v1/gtfs-rt/trip-updates?datasource=<codespace>        GTFS-Realtime trip updates
    marduk-production/outbound/gtfs/rb_<codespace>-aggregated-gtfs.zip   the timetable (VYG 66 MB, GOA 17 MB)
and for SJ Nord, which has no positions (0.2.29; et(), et_for_consumer()):
    realtime/v1/rest/et?datasetId=BNR                              SIRI ET: every call's aimed, expected, actual times
`?datasetId=` does NOT filter the GTFS-Realtime files (it returned the whole country, measured 2026-11-07); `datasource`
does.

POSITIONS FROM THE GRAPHQL VEHICLES API, NOT THE GTFS-REALTIME POSITIONS FILE. MEASURED on the capture of 2026-11-07
(21:34Z on; bin\\no_cap_analyse.txt): it is the same stream with the GTFS-Realtime file a step behind. Vy's fix age at
read p50 13 s against 43 s; of 2,245 same-train, same-poll pairs the GraphQL fix was newer on 1,643 and older on none
(p50 23 m apart, p90 912 m); it carried about 10% more of Vy's trains (1033, 1666, 533 ... none in the GTFS-Realtime
file, every one in the timetable); it names the MODE, so a rail-replacement bus is known as one; and its
datedServiceJourney id carries the operating date (VYG:DatedServiceJourney:1033_ASR-KVG_26-11-07), where two of Vy's
positions had no start_date. Its bearing is the chord of its own last two fixes (p50 0 deg, p99 14 deg from it), not a
heading. Each answer is converted to a GTFS-Realtime FeedMessage here, so every consumer reads one shape. No rate limit
is stated (developer.entur.no, Vehicle Positions; it recommends subscriptions for streaming).

EVERY TRAIN IS route_type 100 (Flytoget 101), so each vehicle goes to ONE consumer by its line's letter
(06_MAP_CONTRACT.md D1; Lee 2026-11-07):

    intercity   F, RE, RX, R lines of Vy, Go-Ahead and SJ Nord      Live Track Intercity Rail
    commuter    L lines of Vy and Go-Ahead; all of Flytoget          Live Track Commuter Rail
    bus         rail-replacement buses (mode BUS, or a bus trip id)  a bus integration

A vehicle whose MODE is not rail is the bus consumer's if it is a bus, nobody's otherwise. The line is the vehicle's
line publicCode (F5), else the route's route_short_name in the timetable (Go-Ahead's route ids are numbers: GOA:Line:50
is F5), else a Vy-style route id's own tail (VYG:Line:R13), else nobody: a vehicle is never drawn by a guess. A trip
update goes by its route and trip id the same way.

Every rule MEASURED 2026-11-07 (30_Integrations/Live_Track_Intercity_Rail/20_Research/_shared/norway/):

FEED QUIRKS handled here once, for every consumer:
* start_date is YYYYMMDD in the positions and often YYYY-MM-DD in the trip updates (Vy every row, Go-Ahead some): both
  rewritten to YYYYMMDD, so a run's two halves join.
* ONE TRIP ID RUNS ON CONSECUTIVE DATES (a Vy trip id belongs to a timetable period), and tomorrow's run is already in
  the trip updates at 22:25 Oslo: a consumer's trip updates hold, for a trip with a position, only the runs with one.
* Rail-replacement buses ride on rail line ids, so the route says nothing: GraphQL's mode BUS says it, and so do three
  trip id forms, Vy's BUS-<n> (BUS-336_855501-R on RE10), Go-Ahead's B<n> (B704-DT_72896-R on F5), and a UUID with no
  timetable trip (VYG:ServiceJourney:33d5b4ad-6caa-...-336-2026-11-07-0, mode BUS on RE10 in all 96 GraphQL rows of
  the capture). MEASURED on the 2026-11-06 timetables (bin\\no_entur_buses.txt): every BUS-<n> and B<n> trip stops only
  at bus quays (stops.txt vehicle_type 700; Vy 1,403 of 1,403, Go-Ahead 20 of 20) and no other trip stops at one (Vy
  5,358, Go-Ahead 612, all vehicle_type 100). They are the bus consumer's, never a train's.
* No speed on any row; entity.id and vehicle.label are not looked at.
* FLYTOGET'S RUN DATE IS IN ITS TRIP ID, and nowhere else (MEASURED 2026-11-08, Commuter Rail SSOT 12): its vehicleId is
  the unit (71-08), it has no datedServiceJourney, and its trip id ends in the date
  (FLT:ServiceJourney:1-2614-3724-20261108), as its trip updates' start_date does. Before 0.2.28 its positions carried
  no start_date, so every trip update of a train in the feed was dropped as another run's (0 of 10 trains kept one).
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
import zipfile

CONTRACT_VERSION = 1
SHARED_KEY = "saw_entur_v%d" % CONTRACT_VERSION

RT_BASE = "https://api.entur.io/realtime/v1/gtfs-rt/"
TRIP_UPDATES_URL = RT_BASE + "trip-updates?datasource=%s"
VEHICLES_URL = "https://api.entur.io/realtime/v2/vehicles/graphql"
# One codespace's vehicles. Every field asked for is read below; nothing else is.
VEHICLES_QUERY = ('{ vehicles(codespaceId: "%s") { vehicleId mode line { lineRef publicCode } serviceJourney { id } '
                  'datedServiceJourney { id } lastUpdatedEpochSecond location { latitude longitude } } }')
VEHICLES_MAX_BYTES = 8 * 1024 * 1024
STATIC_URL = "https://storage.googleapis.com/marduk-production/outbound/gtfs/rb_%s-aggregated-gtfs.zip"
DEFAULT_USER = "StrausbergAutomationWorks/saw-livetrack"
# Entur's identification header, "<company>-<application>". One name for every consumer, so it does not depend on
# which integration loaded first (as Digitraffic's, 35_Dependencies/third_party/digitraffic/SSOT.md section 2).
CLIENT_NAME = "strausbergautomationworks-livetrack"
# NLOD 2.0 section 5: the licensor as it specifies, with a reference to the licence; with no form specified, this one.
ATTRIBUTION = "Contains data under the Norwegian licence for Open Government data (NLOD) distributed by Entur"
TERMS_URL = "https://data.norge.no/nlod/en/2.0"
SOURCE_URL = "https://developer.entur.org"
TIME_ZONE = "Europe/Oslo"

INTERCITY = "intercity"
COMMUTER = "commuter"
BUS = "bus"
CONSUMERS = (INTERCITY, COMMUTER, BUS)

VY = "VYG"
GO_AHEAD = "GOA"
FLYTOGET = "FLT"
SJ_NORD = "SJN"
# Codespaces with GTFS-Realtime positions (Entur's table and 2026-11-07's reads agree). SJ Nord has none.
CODESPACES = (VY, GO_AHEAD, FLYTOGET)

# SIRI ESTIMATED TIMETABLE (0.2.29): a railroad that publishes NO position, only its calls' aimed, expected and actual
# times, through Bane NOR's dataset. SJ Nord (Lee 2026-11-08 10:59 CST: "Build SJ Nord from its expected times, actuals
# as a correction"; Intercity SSOT 8.8, "SJ NORD"). MEASURED on the capture of 2026-11-07 21:34Z to 2026-11-08 11:04Z
# (811 polls, bin\\no_sjn_analyse2.txt): every body is a FULL snapshot (37-64 SJ Nord journeys, about 1.3 MB), not a
# delta; `DatedVehicleJourneyRef` ('453:2026-11-07') sits directly on EstimatedVehicleJourney, with no
# FramedVehicleJourneyRef. Entur's SIRI REST allows 4 requests a minute: read once per ET_TTL_S for every consumer.
ET_URL = "https://api.entur.io/realtime/v1/rest/et?datasetId=%s&maxSize=3000"
ET_DATASETS = {SJ_NORD: "BNR"}
ET_CODESPACES = frozenset(ET_DATASETS)
ET_TTL_S = 60.0
ET_MAX_BYTES = 16 * 1024 * 1024
_SIRI = "{http://www.siri.org.uk/siri}"

INTERCITY_LETTERS = frozenset({"F", "RE", "RX", "R"})
COMMUTER_LETTERS = frozenset({"L"})
COMMUTER_CODESPACES = frozenset({FLYTOGET})

FEED_TTL_S = 25.0
STATIC_TTL_S = 24 * 3600.0
STATIC_RETRY_S = 3600.0
STATIC_MAX_BYTES = 192 * 1024 * 1024
STATIC_PREFIX = "saw_entur_"
_CHUNK = 1024 * 1024

_LINE = re.compile(r"^([A-Z]{1,3})\d+[a-z]?$")
_LOCAL = re.compile(r"^[A-Z]{3}:ServiceJourney:(.+)$")
_BUS = re.compile(r"^(?:B(?:US-)?\d|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.IGNORECASE)
_DSJ_DATE = re.compile(r"_(\d{2})-(\d{2})-(\d{2})$")
_VEHICLE_DATE = re.compile(r"-(\d{4})-(\d{2})-(\d{2})$")
_TRIP_DATE = re.compile(r"-(\d{4})(\d{2})(\d{2})$")


class EnturError(Exception):
    """Base error for this client."""


class RateLimited(EnturError):
    """HTTP 429. Back off; NOT 'no trains'."""


class FeedUnavailable(EnturError):
    """Transport, HTTP or decode failure. Distinct from an empty answer (C2)."""


def _check_consumer(consumer):
    if consumer not in CONSUMERS:
        raise ValueError("unknown consumer %r" % (consumer,))


def _check_codespace(codespace):
    if codespace not in CODESPACES:
        raise ValueError("unknown or positionless codespace %r" % (codespace,))


def _check_et(codespace):
    if codespace not in ET_CODESPACES:
        raise ValueError("no SIRI ET dataset for codespace %r" % (codespace,))


def _check_known(codespace):
    if codespace not in CODESPACES and codespace not in ET_CODESPACES:
        raise ValueError("unknown codespace %r" % (codespace,))


def codespace_of(trip_id):
    """'VYG:ServiceJourney:1672_443368-R' -> 'VYG'; None without a codespace."""
    t = trip_id.strip() if isinstance(trip_id, str) else ""
    head, sep, _ = t.partition(":")
    return head if sep and len(head) == 3 and head.isupper() else None


def local_id(trip_id):
    """'VYG:ServiceJourney:1172-MOS_443069-R' -> '1172-MOS_443069-R'; the id itself when it has no such prefix."""
    t = trip_id.strip() if isinstance(trip_id, str) else ""
    m = _LOCAL.match(t)
    return m.group(1) if m else t


def is_bus(trip_id):
    """A rail-replacement bus, by its trip id: Vy's BUS-336_855501-R, Go-Ahead's B704-DT_72896-R, or a UUID
    (33d5b4ad-6caa-4b6d-ac56-fc523e6b96ae-336-2026-11-07-0)."""
    return bool(_BUS.match(local_id(trip_id)))


def _real_date(y, m, d):
    try:
        datetime.date(int(y), int(m), int(d))
    except ValueError:
        return ""
    return y + m + d


def operating_date(dated_journey_id, vehicle_id=None, trip_id=None):
    """YYYYMMDD from a datedServiceJourney id ('VYG:DatedServiceJourney:1033_ASR-KVG_26-11-07' -> '20261107'), else
    from a Vy vehicle id ('1672-2026-11-07'), else from a trip id ending in the date, as Flytoget's do
    ('FLT:ServiceJourney:1-2614-3724-20261108'); '' with none, or with a date that is no date."""
    m = _DSJ_DATE.search(dated_journey_id.strip()) if isinstance(dated_journey_id, str) else None
    if m:
        return "20" + "".join(m.groups())
    m = _VEHICLE_DATE.search(vehicle_id.strip()) if isinstance(vehicle_id, str) else None
    if m:
        return "".join(m.groups())
    m = _TRIP_DATE.search(local_id(trip_id)) if isinstance(trip_id, str) else None
    return _real_date(*m.groups()) if m else ""


def letters(line):
    """'RE10' -> 'RE', 'R13x' -> 'R', 'F4' -> 'F', 'L2x' -> 'L'; None when it is no line code ('50', 'FLY1' -> 'FLY')."""
    m = _LINE.match(line.strip()) if isinstance(line, str) else None
    return m.group(1) if m else None


def line_from_route_id(route_id):
    """'VYG:Line:R13' -> 'R13'; None when the tail is no line code ('GOA:Line:50')."""
    t = route_id.strip() if isinstance(route_id, str) else ""
    tail = t.rsplit(":", 1)[-1]
    return tail if letters(tail) else None


def consumer_of(codespace, line, trip_id, mode=None):
    """Who draws a vehicle: INTERCITY, COMMUTER, BUS, or None (nobody). `mode` is GraphQL's (RAIL, BUS ...), None for
    a trip update, which has none."""
    if mode is not None and mode != "RAIL":
        return BUS if mode == "BUS" else None
    if is_bus(trip_id):
        return BUS
    if codespace in COMMUTER_CODESPACES:
        return COMMUTER
    lt = letters(line)
    if lt in COMMUTER_LETTERS:
        return COMMUTER
    if lt in INTERCITY_LETTERS:
        return INTERCITY
    return None


def ymd(start_date):
    """'2026-11-08' or '20261108' -> '20261108'; anything else unchanged."""
    s = start_date.strip() if isinstance(start_date, str) else ""
    if len(s) == 10 and s[4] == "-" and s[7] == "-" and (s[:4] + s[5:7] + s[8:]).isdigit():
        return s[:4] + s[5:7] + s[8:]
    return s


def route_table(path):
    """{route_id: {"line": route_short_name, "type": route_type, "color": route_color}} from a timetable zip on disk."""
    with zipfile.ZipFile(path) as z:
        with z.open("routes.txt") as fh:
            out = {}
            for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")):
                rid = (r.get("route_id") or "").strip()
                if rid:
                    out[rid] = {"line": (r.get("route_short_name") or "").strip(),
                                "type": (r.get("route_type") or "").strip(),
                                "color": (r.get("route_color") or "").strip().upper()}
            return out


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------

def _headers(user):
    return {"User-Agent": user, "ET-Client-Name": CLIENT_NAME}


def _get(url, user, timeout, opener=None, method="GET"):
    """(body, headers). Raises RateLimited or FeedUnavailable."""
    req = urllib.request.Request(url, headers=_headers(user), method=method)
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(req, timeout=timeout) as response:
            raw = response.read() if method == "GET" else b""
            got = {k: v for k, v in response.headers.items()} if getattr(response, "headers", None) else {}
    except urllib.error.HTTPError as err:
        if err.code == 429:
            raise RateLimited("HTTP 429 from %s" % url) from err
        raise FeedUnavailable("HTTP %s from %s" % (err.code, url)) from err
    except Exception as err:  # noqa: BLE001 - transport of any kind
        raise FeedUnavailable("%s from %s" % (type(err).__name__, url)) from err
    return raw, got


def _post_json(url, payload, user, timeout, opener=None, max_bytes=VEHICLES_MAX_BYTES):
    """The decoded JSON answer to a POST. Raises RateLimited or FeedUnavailable."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={**_headers(user), "Content-Type": "application/json"})
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(req, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as err:
        if err.code == 429:
            raise RateLimited("HTTP 429 from %s" % url) from err
        raise FeedUnavailable("HTTP %s from %s" % (err.code, url)) from err
    except Exception as err:  # noqa: BLE001 - transport of any kind
        raise FeedUnavailable("%s from %s" % (type(err).__name__, url)) from err
    if len(raw) > max_bytes:
        raise FeedUnavailable("more than %d bytes from %s" % (max_bytes, url))
    try:
        return json.loads(raw)
    except ValueError as err:
        raise FeedUnavailable("undecodable answer from %s" % url) from err


def _decode(raw, url):
    from .gtfs.realtime import decode
    feed = decode(raw)
    if feed is None:
        raise FeedUnavailable("undecodable body from %s" % url)
    return feed


def _fix_time(v):
    """A vehicle's fix time as whole epoch seconds, floored: lastUpdatedEpochSecond, else the ISO lastUpdated
    ('2026-11-07T21:21:15.425Z'); None with neither. Floored, so a fix is never in the future of the poll that read it
    (Renfe (Spain)'s lesson: the estimate refuses a fix from the future)."""
    ts = v.get("lastUpdatedEpochSecond")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
        return int(ts)
    text = v.get("lastUpdated")
    if isinstance(text, str) and text.strip():
        try:
            when = datetime.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if when.tzinfo is None:
            return None
        return int(when.timestamp())
    return None


def vehicles_feed(answer, now):
    """A GraphQL vehicles answer -> (a GTFS-Realtime FeedMessage of VehiclePositions, [(mode, publicCode)] in entity
    order). A vehicle with no position or no service journey is left out. Raises FeedUnavailable on an answer with
    errors and no data, or no vehicles list."""
    from google.transit import gtfs_realtime_pb2 as rt
    if not isinstance(answer, dict):
        raise FeedUnavailable("vehicles answer is not an object")
    data = answer.get("data")
    vehicles = data.get("vehicles") if isinstance(data, dict) else None
    if not isinstance(vehicles, list):
        errs = answer.get("errors")
        raise FeedUnavailable("vehicles answer without vehicles%s" % (
            ": " + str(errs)[:200] if errs else ""))
    fm = rt.FeedMessage()
    fm.header.gtfs_realtime_version = "2.0"
    fm.header.timestamp = int(now)
    meta = []
    for v in vehicles:
        if not isinstance(v, dict):
            continue
        sj = (v.get("serviceJourney") or {}).get("id") if isinstance(v.get("serviceJourney"), dict) else None
        loc = v.get("location") if isinstance(v.get("location"), dict) else None
        if not sj or not loc:
            continue
        lat, lon = loc.get("latitude"), loc.get("longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) or (lat == 0 and lon == 0):
            continue
        line = v.get("line") if isinstance(v.get("line"), dict) else {}
        vid = v.get("vehicleId") if isinstance(v.get("vehicleId"), str) else ""
        dsj = (v.get("datedServiceJourney") or {}).get("id") if isinstance(v.get("datedServiceJourney"), dict) else None
        e = fm.entity.add()
        e.id = vid or sj
        e.vehicle.trip.trip_id = sj
        if line.get("lineRef"):
            e.vehicle.trip.route_id = line["lineRef"]
        start = operating_date(dsj, vid, sj)
        if start:
            e.vehicle.trip.start_date = start
        if vid:
            e.vehicle.vehicle.id = vid
        e.vehicle.position.latitude = float(lat)
        e.vehicle.position.longitude = float(lon)
        # GraphQL's bearing is not passed on: it is the chord of its own last two fixes (module docstring), which
        # every consumer can draw for itself, not a heading the train reported.
        ts = _fix_time(v)
        if ts is not None:
            e.vehicle.timestamp = ts
        meta.append((v.get("mode") if isinstance(v.get("mode"), str) else None,
                     line.get("publicCode") if isinstance(line.get("publicCode"), str) else None))
    return fm, meta


def _new_feed(src):
    out = type(src)()
    out.header.CopyFrom(src.header)
    return out


# --------------------------------------------------------------------------
# SIRI Estimated Timetable (0.2.29)
# --------------------------------------------------------------------------

ET_TIMES = ("aimed_arrival", "aimed_departure", "expected_arrival", "expected_departure", "actual_arrival",
            "actual_departure")
_ET_TAGS = ("AimedArrivalTime", "AimedDepartureTime", "ExpectedArrivalTime", "ExpectedDepartureTime",
            "ActualArrivalTime", "ActualDepartureTime")


def siri_time(value):
    """A SIRI time ('2026-11-07T20:37:41+01:00', '2026-11-07T22:34:05.904040342+01:00') -> epoch seconds; None when
    absent, unreadable, or without a UTC offset (a local time would be read in the host's zone)."""
    s = value.strip() if isinstance(value, str) else ""
    if not s:
        return None
    try:
        # Python 3.11+ reads any number of fractional digits (Bane NOR sends nanoseconds) and truncates past six.
        d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.timestamp() if d.tzinfo is not None else None


def _text(el, tag):
    found = el.find(_SIRI + tag)
    return (found.text or "").strip() if found is not None and found.text else ""


def et_journeys(raw, codespace):
    """A SIRI ET body -> [journey] for `codespace` (its LineRef 'SJN:Line:R70'), in the body's order. Raises
    FeedUnavailable when the body is no XML. Each journey:
        id         DatedVehicleJourneyRef, '453:2026-11-07' (directly on the journey in Bane NOR's dataset)
        number     '453' (the id's head, else VehicleRef); journeys with neither are left out
        date       '20261107' (the id's tail), '' without one
        line       'R70', None when LineRef's tail is no line code
        cancelled  the whole journey is (Cancellation)
        calls      [{order, stop (StopPointRef, the quay), name, recorded (a RecordedCall), cancelled, and the six
                   ET_TIMES as epoch seconds or None}], by Order
    Nothing is decided here: which time to believe is the consumer's."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as err:
        raise FeedUnavailable("undecodable SIRI ET body") from err
    prefix = codespace + ":"
    out = []
    for ej in root.iter(_SIRI + "EstimatedVehicleJourney"):
        line_ref = _text(ej, "LineRef")
        if not line_ref.startswith(prefix):
            continue
        dvj = _text(ej, "DatedVehicleJourneyRef")
        head, _, tail = dvj.partition(":")
        number = head if head.isdigit() else _text(ej, "VehicleRef")
        if not number.isdigit():
            continue
        calls = []
        for tag, recorded in (("RecordedCall", True), ("EstimatedCall", False)):
            for c in ej.iter(_SIRI + tag):
                try:
                    order = int(_text(c, "Order"))
                except ValueError:
                    continue
                call = {"order": order, "stop": _text(c, "StopPointRef"), "name": _text(c, "StopPointName"),
                        "recorded": recorded, "cancelled": _text(c, "Cancellation").lower() == "true"}
                for key, t in zip(ET_TIMES, _ET_TAGS):
                    call[key] = siri_time(_text(c, t))
                calls.append(call)
        calls.sort(key=lambda c: c["order"])
        tail_line = line_ref.rsplit(":", 1)[-1]
        out.append({"id": dvj or number, "number": str(int(number)), "date": ymd(tail),
                    "line": tail_line if letters(tail_line) else None,
                    "cancelled": _text(ej, "Cancellation").lower() == "true", "calls": calls})
    return out


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------

class EnturClient:
    """Polls Entur ONCE for every consumer (B16: budget the IP), per codespace.

    A consumer registers WHAT it draws and FROM WHICH codespace: register(consumer, codespace). Only the codespaces
    someone registered are polled; each poll asks the GraphQL vehicles API for that codespace's vehicles and reads its
    GTFS-Realtime trip updates. for_consumer(consumer, codespace) -> (positions, trip_updates): two GTFS-Realtime
    FeedMessages, that consumer's vehicles and trips in that codespace, cleaned (module docstring); trip_updates is
    None when that file failed and the positions did not.

    THE TIMETABLE, per codespace, is kept on disk, not in memory: storage_dir/saw_entur_<codespace>.zip (Vy's is
    66 MB), with its version (Last-Modified) in a .json beside it. Checked with a HEAD at most daily, on its own thread;
    the feeds wait for nothing. A failed download keeps the last copy. It names each route's line; until it has, a
    route id that carries its line (Vy's) is read from the id, and any other vehicle goes to nobody. With no
    storage_dir there is no timetable.

    One lock covers the fetch and the state, so consumers on different executor threads make one set of requests
    between them; register() and unregister() take only a small state lock (they run on Home Assistant's event loop).
    """

    def __init__(self, claims=(), user=DEFAULT_USER, timeout=30, opener=None, clock=None, feed_ttl_s=FEED_TTL_S,
                 storage_dir=None, static_timeout=600, background=True):
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
        self._wanted = set()
        for consumer, codespace in claims:
            _check_consumer(consumer)
            _check_known(codespace)
            self._wanted.add((consumer, codespace))
        self._feeds = {}             # codespace -> {consumer: (vp, tu)}
        self._fetched_at = {}        # codespace -> time
        self.et_ttl_s = ET_TTL_S
        self._et = {}                # ET codespace -> {consumer: [journey]}
        self._et_at = {}             # ET codespace -> time
        self._routes = {}            # codespace -> route_table
        self._version = {}           # codespace -> Last-Modified
        self._static_at = {}
        self._static_retry = {}
        self._static_loaded = set()
        self._loading = set()
        self.fetch_count = 0
        self.dropped_other_runs = 0
        self.unrouted = 0

    @property
    def claims(self):
        with self._state:
            return frozenset(self._wanted)

    def codespaces(self):
        with self._state:
            return frozenset(c for _, c in self._wanted)

    def register(self, consumer, codespace):
        """Serve `consumer` in `codespace` from now on. True if it is new. Never waits for a fetch."""
        _check_consumer(consumer)
        _check_known(codespace)
        with self._state:
            if (consumer, codespace) in self._wanted:
                return False
            self._wanted.add((consumer, codespace))
            return True

    def unregister(self, consumer, codespace):
        """Stop serving `consumer` in `codespace`. True if it was registered. Never waits."""
        _check_consumer(consumer)
        _check_known(codespace)
        with self._state:
            if (consumer, codespace) not in self._wanted:
                return False
            self._wanted.discard((consumer, codespace))
            return True

    # --- the timetable --------------------------------------------------------

    def _paths(self, codespace):
        base = os.path.join(self.storage_dir, STATIC_PREFIX + codespace.lower())
        return base + ".zip", base + ".json"

    def static_path(self, codespace):
        """The codespace's timetable zip on disk, or None before one exists."""
        if self.storage_dir is None:
            return None
        self._load_cached(codespace)
        with self._state:
            have = self._version.get(codespace) is not None
        path = self._paths(codespace)[0]
        return path if have and os.path.exists(path) else None

    def static_version(self, codespace):
        """Changes when the codespace's timetable does (its Last-Modified). A consumer distills again when it changes."""
        self._load_cached(codespace)
        with self._state:
            return self._version.get(codespace)

    def routes(self, codespace):
        """{route_id: {"line", "type", "color"}} from the codespace's timetable; {} before one has loaded."""
        self._load_cached(codespace)
        with self._state:
            return dict(self._routes.get(codespace) or {})

    def _load_cached(self, codespace):
        if self.storage_dir is None:
            return
        with self._state:
            if codespace in self._static_loaded:
                return
            self._static_loaded.add(codespace)
        zp, meta = self._paths(codespace)
        try:
            with open(meta, encoding="utf-8") as fh:
                d = json.load(fh)
            routes = route_table(zp)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            return
        with self._state:
            self._version[codespace] = d.get("version")
            self._static_at[codespace] = d.get("checked_at")
            self._routes[codespace] = routes

    def _static_due(self, codespace, now):
        at = self._static_at.get(codespace)
        fresh = at is not None and now - at < STATIC_TTL_S
        return not fresh and now >= self._static_retry.get(codespace, 0.0)

    def refresh_static(self, codespace, force=False):
        """Check and if needed download one codespace's timetable on THIS thread; never raises. True when it changed."""
        if self.storage_dir is None:
            return False
        self._load_cached(codespace)
        with self._state:
            now = self._clock()
            if (not force and not self._static_due(codespace, now)) or codespace in self._loading:
                return False
            self._loading.add(codespace)
            self._static_retry[codespace] = now + STATIC_RETRY_S
        try:
            return self._refresh(codespace, now)
        except Exception:  # noqa: BLE001 - keep the last copy; the feeds do not need it
            return False
        finally:
            with self._state:
                self._loading.discard(codespace)

    def _refresh(self, codespace, now):
        url = STATIC_URL % codespace.lower()
        self.fetch_count += 1
        _, got = _get(url, self.user, self.timeout, self._opener, method="HEAD")
        version = got.get("Last-Modified") or got.get("last-modified") or got.get("ETag") or None
        zp, meta = self._paths(codespace)
        with self._state:
            current = self._version.get(codespace)
        if version is not None and version == current and os.path.exists(zp):
            self._write_meta(meta, version, now)
            with self._state:
                self._static_at[codespace] = now
            return False
        self.fetch_count += 1
        tmp = zp + ".tmp"
        try:
            self._download(url, tmp)
            with zipfile.ZipFile(tmp) as z:
                names = set(z.namelist())
            if not {"routes.txt", "trips.txt", "stop_times.txt", "stops.txt"} <= names:
                raise FeedUnavailable("timetable zip lacks a required file")
            routes = route_table(tmp)
            os.replace(tmp, zp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self._write_meta(meta, version, now)
        with self._state:
            self._version[codespace], self._static_at[codespace], self._routes[codespace] = version, now, routes
        return True

    def _download(self, url, tmp):
        req = urllib.request.Request(url, headers=_headers(self.user))
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
                        raise FeedUnavailable("more than %d bytes from %s" % (STATIC_MAX_BYTES, url))
                    out.write(chunk)
        except EnturError:
            raise
        except Exception as err:  # noqa: BLE001 - transport of any kind
            raise FeedUnavailable("%s from %s" % (type(err).__name__, url)) from None

    @staticmethod
    def _write_meta(meta, version, now):
        tmp = meta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": version, "checked_at": now}, fh)
        os.replace(tmp, meta)

    def _ensure_static(self, codespace):
        if self.storage_dir is None:
            return
        self._load_cached(codespace)
        with self._state:
            due = self._static_due(codespace, self._clock()) and codespace not in self._loading
        if not due:
            return
        if not self._background:
            self.refresh_static(codespace)
            return
        threading.Thread(target=self.refresh_static, args=(codespace,), name="saw_entur_timetable_%s" % codespace,
                         daemon=True).start()

    # --- the feed ---------------------------------------------------------------

    @staticmethod
    def _line(route_id, routes):
        r = routes.get((route_id or "").strip()) if routes else None
        if r and r.get("line"):
            return r["line"]
        return line_from_route_id(route_id)

    def snapshot(self, codespace, force=False):
        """{consumer: (positions, trip_updates)} for the consumers registered in `codespace`."""
        _check_codespace(codespace)
        self._ensure_static(codespace)
        with self._lock:
            now = self._clock()
            at = self._fetched_at.get(codespace)
            if not force and codespace in self._feeds and at is not None and now - at < self.feed_ttl_s:
                return self._feeds[codespace]
            self.fetch_count += 1
            answer = _post_json(VEHICLES_URL, {"query": VEHICLES_QUERY % codespace}, self.user, self.timeout,
                                self._opener)
            vp, meta = vehicles_feed(answer, now)
            tu_url = TRIP_UPDATES_URL % codespace
            try:
                self.fetch_count += 1
                tu = _decode(_get(tu_url, self.user, self.timeout, self._opener)[0], tu_url)
            except EnturError:
                tu = None
            with self._state:
                wanted = frozenset(c for c, cs in self._wanted if cs == codespace)
                routes = self._routes.get(codespace) or {}
            out = {c: (_new_feed(vp), _new_feed(tu) if tu is not None else None) for c in wanted}
            owner, runs = {}, {}
            for ent, (mode, public) in zip(vp.entity, meta):
                v = ent.vehicle
                tid = v.trip.trip_id.strip()
                line = public if letters(public) or codespace in COMMUTER_CODESPACES else None
                c = consumer_of(codespace, line or self._line(v.trip.route_id, routes), tid, mode)
                if c is None:
                    self.unrouted += 1
                # Two vehicles on one trip id: a train's owner wins over a bus's or nobody's (none seen).
                if tid not in owner or (owner[tid] in (None, BUS) and c not in (None, BUS)):
                    owner[tid] = c
                runs.setdefault(tid, set()).add(v.trip.start_date)
                if c in out:
                    out[c][0].entity.add().CopyFrom(ent)
            if tu is not None:
                for ent in tu.entity:
                    if not ent.HasField("trip_update"):
                        continue
                    trip = ent.trip_update.trip
                    tid = trip.trip_id.strip()
                    c = owner[tid] if tid in owner else consumer_of(codespace, self._line(trip.route_id, routes), tid)
                    if c not in out:
                        continue
                    start = ymd(trip.start_date)
                    if tid in runs and start not in runs[tid]:
                        self.dropped_other_runs += 1
                        continue
                    e = out[c][1].entity.add()
                    e.CopyFrom(ent)
                    if e.trip_update.trip.start_date:
                        e.trip_update.trip.start_date = start
            self._feeds[codespace] = out
            self._fetched_at[codespace] = now
            return out

    def for_consumer(self, consumer, codespace, force=False):
        _check_consumer(consumer)
        _check_codespace(codespace)
        if (consumer, codespace) not in self.claims:
            raise ValueError("consumer %r is not registered in %s with this client" % (consumer, codespace))
        feeds = self.snapshot(codespace, force=force)
        if consumer not in feeds:
            # Registered after this snapshot was built.
            feeds = self.snapshot(codespace, force=True)
        return feeds[consumer]

    def et(self, codespace, force=False):
        """{consumer: [journey]} (et_journeys) for the consumers registered in an ET codespace (SJ Nord), each journey
        routed to ONE consumer by its line's letter, as a vehicle is (consumer_of). Read at most once per et_ttl_s for
        all of them (Entur's SIRI REST: 4 requests a minute). Raises RateLimited or FeedUnavailable; a failure keeps
        nothing, so the next call asks again."""
        _check_et(codespace)
        self._ensure_static(codespace)
        with self._lock:
            now = self._clock()
            at = self._et_at.get(codespace)
            if not force and codespace in self._et and at is not None and now - at < self.et_ttl_s:
                return self._et[codespace]
            url = ET_URL % ET_DATASETS[codespace]
            self.fetch_count += 1
            raw = _get(url, self.user, self.timeout, self._opener)[0]
            if len(raw) > ET_MAX_BYTES:
                raise FeedUnavailable("more than %d bytes from %s" % (ET_MAX_BYTES, url))
            journeys = et_journeys(raw, codespace)
            with self._state:
                wanted = frozenset(c for c, cs in self._wanted if cs == codespace)
            out = {c: [] for c in wanted}
            for j in journeys:
                c = consumer_of(codespace, j["line"], j["id"])
                if c is None:
                    self.unrouted += 1
                elif c in out:
                    out[c].append(j)
            self._et[codespace] = out
            self._et_at[codespace] = now
            return out

    def et_for_consumer(self, consumer, codespace, force=False):
        """[journey] for one registered consumer in an ET codespace (et)."""
        _check_consumer(consumer)
        _check_et(codespace)
        if (consumer, codespace) not in self.claims:
            raise ValueError("consumer %r is not registered in %s with this client" % (consumer, codespace))
        got = self.et(codespace, force=force)
        if consumer not in got:
            got = self.et(codespace, force=True)
        return got[consumer]

    def stats(self):
        """Low-cardinality state for a diagnostic."""
        with self._state:
            versions = dict(self._version)
            routes = {k: len(v) for k, v in self._routes.items()}
            loading = sorted(self._loading)
        return {"timetables": versions, "timetable_routes": routes, "timetable_loading": loading,
                "fetch_count": self.fetch_count, "dropped_other_runs": self.dropped_other_runs,
                "unrouted": self.unrouted}


# --------------------------------------------------------------------------
# Oslo's tunnels, where Entur's fixes go dark (0.2.28)
# --------------------------------------------------------------------------
# Hoisted from Live Track Intercity Rail (sources/entur.py at cee6997, verbatim) when Live Track Commuter Rail became
# the second consumer (Lee 2026-11-07 18:10: "the long tunnels around Oslo should be handled like the tunnel in
# Helsinki"; Commuter Rail SSOT 12). A train's fix FREEZES where a tunnel takes its signal, for minutes, and the next
# comes from the far end. Each way: (id, where its fixes freeze going in, where they come back, the quickest dark time
# in s, passages measured). MEASURED on the capture's first 2.6 hours (2026-11-07 21:34Z-00:10Z; bin\\no_tunnels.txt,
# no_tunnels2.txt): every place a Vy or Go-Ahead train's fix stood still 60 s or more and then came back 3 km or more
# away, clustered within 600 m; the freeze fixes spread up to 534 m (Oslo S), the comebacks up to 690 m; quickest among
# passages under 200 km/h straight (all were). Oslo's tunnels only: Ulriken in Bergen measured the same (Arna-Bergen
# 270 s, Bergen-Arna 270 s), left out. Flytoget was not in that capture: its trains run Lieraasen, Sandvika-Asker,
# Lysaker-Sandvika, Oslotunnelen and Romeriksporten on the same tracks, not measured for it.
TUNNELS = (
    ("oslotunnelen_w", (59.91168, 10.75219), (59.92012, 10.68731), 211.0, 14),     # Oslo S - Nationaltheatret - Skøyen
    ("oslotunnelen_e", (59.91861, 10.69489), (59.91059, 10.75485), 241.0, 13),
    ("lysaker_sandvika_w", (59.91486, 10.62438), (59.89459, 10.53044), 149.0, 11),
    ("lysaker_sandvika_e", (59.89673, 10.53845), (59.91470, 10.62993), 148.0, 8),
    ("sandvika_asker_w1", (59.88973, 10.50076), (59.86480, 10.45693), 89.0, 10),
    ("sandvika_asker_w2", (59.86429, 10.45609), (59.83358, 10.43445), 89.0, 11),
    ("sandvika_asker_e1", (59.83545, 10.43731), (59.87193, 10.46851), 119.0, 6),
    ("sandvika_asker_e2", (59.87016, 10.46576), (59.89089, 10.51527), 91.0, 2),
    ("romeriksporten_w", (59.95273, 11.02512), (59.90594, 10.79167), 329.0, 7),    # Lillestrøm side to Oslo S
    ("romeriksporten_e", (59.90605, 10.79668), (59.95247, 11.03679), 331.0, 10),
    ("lieraasen_w", (59.82812, 10.42795), (59.76262, 10.28762), 270.0, 9),         # Asker side to Drammen
    ("lieraasen_e", (59.76489, 10.29439), (59.83149, 10.43232), 269.0, 6),
    ("follobanen_s", (59.90637, 10.76614), (59.72809, 10.83911), 479.0, 7),        # Oslo S to Ski
    ("follobanen_n", (59.73246, 10.84221), (59.90813, 10.76382), 479.0, 7),
)
# A frozen fix this near a tunnel's way in is taken as going in (the freeze fixes spread up to 534 m), or one already
# inside it: its distances to the way in and to the comeback point add up to at most TUNNEL_ALONG_X times theirs
# (measured: train 1673's fix froze 1.6 km into Romeriksporten, its two distances 1.017 times).
TUNNEL_ENTRY_M = 700.0
TUNNEL_ALONG_X = 1.05


def metres(a, b):
    """Ground distance in m between two (lat, lon), equirectangular (good to a fraction of a percent at these
    distances)."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    return 6371000.0 * math.hypot((lo2 - lo1) * math.cos((la1 + la2) / 2.0), la2 - la1)


def ahead(a, b, e, c):
    """Whether going from stop `a` to stop `b` is going the tunnel's way, from its way in `e` to its comeback `c`: the
    two directions on the ground agree (a positive dot product, longitude scaled by the cosine of latitude). The first
    rule (the next stop nearer the comeback than the stop left) let a train from Drammen to Oslo S into the WESTBOUND
    Lysaker-Sandvika (Intercity's test found it)."""
    k = math.cos(math.radians((a[0] + b[0]) / 2.0))
    return (b[0] - a[0]) * (c[0] - e[0]) + (b[1] - a[1]) * k * k * (c[1] - e[1]) > 0


def tunnel_at(pos, prev_pos, next_pos, tunnels=TUNNELS):
    """The TUNNELS row a frozen fix at `pos` is going into, between the stop the train left (`prev_pos`) and its next
    (`next_pos`), all (lat, lon): its way in within TUNNEL_ENTRY_M or the fix already inside, going the tunnel's way
    (`ahead`, so a train come OUT at that point is not sent back in). The nearest way in; None with none, or without
    both stops."""
    if not prev_pos or not next_pos:
        return None
    best = None
    for t in tunnels:
        d, left = metres(pos, t[1]), metres(pos, t[2])
        # Inside, and not already at the comeback point (a train standing there has come OUT).
        inside = d + left <= TUNNEL_ALONG_X * metres(t[1], t[2]) and left > TUNNEL_ENTRY_M
        if (d > TUNNEL_ENTRY_M and not inside) or not ahead(prev_pos, next_pos, t[1], t[2]):
            continue
        if best is None or d < best[0]:
            best = (d, t)
    return best[1] if best else None


def tunnel_time(pos, tunnel):
    """The quickest time from `pos` to the tunnel's comeback point: its quickest from the way in, in proportion to the
    distance left (a fix that froze inside has less to go)."""
    whole = metres(tunnel[1], tunnel[2])
    return tunnel[3] * min(1.0, metres(pos, tunnel[2]) / whole) if whole > 0 else tunnel[3]
