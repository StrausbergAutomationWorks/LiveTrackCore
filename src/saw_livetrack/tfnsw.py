"""Shared Transport for NSW client (the `nswtrains` and Sydney Trains feeds) for the Live Track family.

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

SYDNEY TRAINS (0.2.37, backlog 319's second part). Design: DESIGN_319_sydneytrains_20261112.md beside the first, approved
as written by Lee 2026-11-12, D7 "intercity merges Sydney Trains' CTY". Three more endpoints:
    v2/gtfs/vehiclepos/sydneytrains   positions: no start_date, direction or status; bearing 0 and no speed on every row
    v2/gtfs/realtime/sydneytrains     trip updates: stop_sequence 0 on every stop update, stop_id set
    v1/gtfs/schedule/sydneytrains     the timetable, about 11 MB, no Range, a Last-Modified

A CONSUMER ASKS FOR THEM: client.set_sydney_trains(consumer, True) right after get_shared_tfnsw_client() (or
register(consumer, key, sydney_trains=True)); `transit` always has them; unregister() at the last claim forgets the asking.
reference/shared_tfnsw.py is UNCHANGED from 0.2.23, so the copies in the integrations stay byte-exact (Commuter Rail's
test_nsw checks it): a consumer is claimed by one integration only, so the asking needs no count of its own. A consumer
that does not ask gets exactly the nswtrains-only feed of 0.2.23. An integration released before 0.2.37 cannot ask, so it
sees no change when Home Assistant installs this version for another (MEASURED: every reference copy imports
CONTRACT_VERSION and SHARED_KEY from this module, so a new key could not keep an old copy off a new client; the design's
"v1 and v2 side by side" cannot happen with one installed package, and the contract stays 1). Sydney Trains is polled
only while a consumer that asked is registered, and nswtrains only while a consumer other than `transit` is.

    commuter    CCN, BMT, SCO, SHL, HUN                         merged with nswtrains' commuter vehicles, one per run-day
    intercity   CTY                                             merged with nswtrains' intercity vehicles (D7)
    transit     APS, CMB, ESI, IWL, NSN, WST, NTH, OLY, T3, T6  Live Track Transit (the DART rule); nothing merged
    nobody      RTTA, no route, NonTimetabled.*; another prefix by the timetable's agency, nobody before one is loaded

Handled here once, MEASURED on the design's capture (Friday 2026-11-13 from 14:13 Sydney, bin\\nsw319\\rec\\):
* THE DAY. Every Sydney Trains trip id carries its calendar service as fields 2-4 (70,615 of 70,615), which runs on ONE
  date among yesterday, today and tomorrow for every positioned trip: trip.start_date is SET from calendar.txt. The
  previous publication's calendar answers for yesterday until 08:00 Sydney (a copy published at 01:01 lacks yesterday's
  services; yesterday's trips run to 31:35).
* stop_id is a signalling location ("Hornsby.Cowan 2 Loc", 216 of 216 not a stop), bearing 0 and speed absent: cleared.
* S1, BEFORE DEPARTURE: the old fixes are mostly parked sets carrying their NEXT trip. A run whose departure (the "HH:MM"
  opening vehicle.label, equal to the first departure or up to 7 minutes earlier) is still ahead is handed out only on a
  fix at most BEFORE_DEPARTURE_MAX_AGE_S old. During the run (S2, the 600 s hold) and after it (S3) are each integration's.
* ONE ENTITY PER RUN-DAY (commuter, intercity) across both feeds. The run is the trip id's first field in either. The fix
  is the newer one, nswtrains' within TIE_S (a real bearing); never older than the fix last handed out for that run-day
  (kept LAST_FIX_KEEP_S). The trip descriptor and the trip updates are nswtrains' whenever nswtrains has the run-day, in
  its positions or its updates (54 of 54 run-reads with updates there, 13 of 54 in Sydney Trains), else Sydney Trains'.
* A TRIP ID STAYS WITH ITS TIMETABLE: timetable_of() names it. The two timetables share 353 stop ids of 1,214 and 1,174.
Left to each integration: S2, S3, stop sequences (gtfs.realtime.sequence_by_stop with its own rows), Empty Trains' look.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, time as dtime, timedelta

from ._keyed import (AuthRefused, Backoff, FeedUnavailable, KeyedError, KeyRefused, KeyRing,  # noqa: F401
                     RateLimited, fetch)

# Still 1 in 0.2.37 (Lee 2026-11-12, against the design's 2): every addition is opt-in or new (`transit`,
# set_sydney_trains), the entry and the reference copy are unchanged, and every copy reads this constant from the
# installed package, so a bump could not separate old copies from new; it only broke Intercity Rail's tests, which name
# the key saw_tfnsw_v1 (bin\saw0237_commit.run2.txt).
CONTRACT_VERSION = 1
SHARED_KEY = "saw_tfnsw_v%d" % CONTRACT_VERSION

BASE = "https://api.transport.nsw.gov.au/v1/gtfs/"
POSITIONS_URL = BASE + "vehiclepos/nswtrains"
TRIP_UPDATES_URL = BASE + "realtime/nswtrains"
STATIC_URL = BASE + "schedule/nswtrains"
SYDNEY_BASE = "https://api.transport.nsw.gov.au/v2/gtfs/"
SYDNEY_POSITIONS_URL = SYDNEY_BASE + "vehiclepos/sydneytrains"
SYDNEY_TRIP_UPDATES_URL = SYDNEY_BASE + "realtime/sydneytrains"
SYDNEY_STATIC_URL = BASE + "schedule/sydneytrains"
TERMS_URL = "https://opendata.transport.nsw.gov.au/datalicence"
DEFAULT_USER = "StrausbergAutomationWorks/saw-livetrack"
# Text only, no logo on a map marker (Lee 2026-11-02, D4).
ATTRIBUTION = "Data: Transport for NSW (opendata.transport.nsw.gov.au), CC BY 4.0"
TIME_ZONE = "Australia/Sydney"

INTERCITY = "intercity"
COMMUTER = "commuter"
BUS = "bus"
TRANSIT = "transit"
CONSUMERS = (INTERCITY, COMMUTER, BUS, TRANSIT)
# Who may take Sydney Trains. `transit` always does; a bus is never in it.
SYDNEY_CONSUMERS = frozenset({INTERCITY, COMMUTER, TRANSIT})

NSWTRAINS = "nswtrains"
SYDNEYTRAINS = "sydneytrains"
TIMETABLES = (NSWTRAINS, SYDNEYTRAINS)

BY_ROUTE_TYPE = {"106": INTERCITY, "100": COMMUTER, "204": BUS, "205": BUS}
COMMUTER_LINES = frozenset({"BMT", "HUN", "SCO", "SHL"})

# Sydney Trains' route_id prefixes (before the first "_"), checked against the timetable's agency on 137 of 137 routes.
SYDNEY_COMMUTER = frozenset({"CCN", "BMT", "SCO", "SHL", "HUN"})
SYDNEY_TRANSIT = frozenset({"APS", "CMB", "ESI", "IWL", "NSN", "WST", "NTH", "OLY", "T3", "T6"})
SYDNEY_INTERCITY = frozenset({"CTY"})            # D7 (Lee 2026-11-12): intercity merges them
SYDNEY_NOBODY = frozenset({"RTTA"})              # "Out Of Service": no line, so no owner (06_MAP_CONTRACT.md D1)
BY_AGENCY = {"SydneyTrains": TRANSIT, "NSWTrains": COMMUTER}

# A new fix about once a minute: a 60 s poll (Lee 2026-11-01, decision 12) always reads fresh.
FEED_TTL_S = 55.0
STATIC_TTL_S = 24 * 3600.0
STATIC_RETRY_S = 3600.0
STATIC_MAX_BYTES = 128 * 1024 * 1024
STATIC_NAME = "saw_tfnsw_nswtrains"
SYDNEY_STATIC_NAME = "saw_tfnsw_sydneytrains"
SYDNEY_REQUIRED = frozenset({"routes.txt", "trips.txt", "stop_times.txt", "stops.txt", "calendar.txt"})
_CHUNK = 1024 * 1024

# Design sections 4 and 5, MEASURED: nswtrains' fix fresher on 8 of 10 same-read pairs; without "never older", 6 changes
# of feed in 8 reads, 2 of them back to an older fix; parked sets carrying their next trip sat p50 66 minutes before it.
TIE_S = 30.0
LAST_FIX_KEEP_S = 2 * 3600.0
BEFORE_DEPARTURE_MAX_AGE_S = 300.0
PREVIOUS_CALENDAR_UNTIL_HOUR = 8

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_LABEL_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})(?!\d)")


def _check_consumer(consumer):
    if consumer not in CONSUMERS:
        raise ValueError("unknown consumer %r" % (consumer,))


def _check_timetable(timetable):
    if timetable not in TIMETABLES:
        raise ValueError("unknown timetable %r" % (timetable,))


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


def sydney_consumer_of(route_id, agencies=None):
    """Who draws a Sydney Trains route: COMMUTER, TRANSIT, INTERCITY or None, by its prefix. A prefix not listed goes by
    the loaded timetable's agency ({route_id: agency_id}): SydneyTrains to transit, NSWTrains to commuter; nobody before
    a timetable is loaded. `WST` is a suburban prefix AND a CTY short name; CTY routes carry `CTY_`, so it is unambiguous."""
    rid = (route_id or "").strip() if isinstance(route_id, str) else ""
    if not rid:
        return None
    prefix = rid.split("_", 1)[0]
    if prefix in SYDNEY_COMMUTER:
        return COMMUTER
    if prefix in SYDNEY_TRANSIT:
        return TRANSIT
    if prefix in SYDNEY_INTERCITY:
        return INTERCITY
    if prefix in SYDNEY_NOBODY:
        return None
    return BY_AGENCY.get((agencies or {}).get(rid))


def run_of(trip_id):
    """The run: the trip id's first field, in either feed (`KN56.131126.16...`, `W665.1421.149.100.D.10...`)."""
    tid = trip_id.strip() if isinstance(trip_id, str) else ""
    return tid.split(".", 1)[0]


def service_of(trip_id):
    """A Sydney Trains trip id's calendar service, its fields 2-4 (`W665.1421.149.100.D...` -> `1421.149.100`), or None.
    MEASURED: equal to trips.txt's service_id on 70,615 of 70,615 trips."""
    parts = trip_id.strip().split(".") if isinstance(trip_id, str) else []
    if len(parts) < 4 or not all(parts[1:4]):
        return None
    return ".".join(parts[1:4])


def _ddmmyy(text):
    if len(text) != 6 or not text.isdigit():
        return False
    try:
        date(2000 + int(text[4:]), int(text[2:4]), int(text[:2]))
    except ValueError:
        return False
    return True


def timetable_of(trip_id):
    """NSWTRAINS, SYDNEYTRAINS or None: which timetable a trip id belongs to. One run, two namespaces (design #8).

    MEASURED 2026-11-12 (bin\\saw0237_ids*.txt): nswtrains' run date `ddmmyy` is field 2 (`KN56.131126.16.N...`) or, on
    264 North Coast trips (NP, NT, all route_type 106: the CTY trains D7 merges), field 3 (`NT32.1421.091126.116.X...`):
    58,151 of 58,151 nswtrains trips, 0 of 70,615 Sydney Trains trips (whose fields 2 and 3 are 3-4 and 3 digits). The
    design's "field 2" alone would have filed those 264 under Sydney Trains."""
    parts = trip_id.strip().split(".") if isinstance(trip_id, str) else []
    if len(parts) >= 2 and (_ddmmyy(parts[1]) or (len(parts) >= 3 and _ddmmyy(parts[2]))):
        return NSWTRAINS
    if service_of(trip_id) is not None:
        return SYDNEYTRAINS
    return None


def read_calendar(rows):
    """{service_id: (start "YYYYMMDD", end "YYYYMMDD", (mon..sun) bools)} from calendar.txt rows."""
    out = {}
    for r in rows:
        sid = (r.get("service_id") or "").strip()
        if not sid:
            continue
        out[sid] = ((r.get("start_date") or "").strip(), (r.get("end_date") or "").strip(),
                    tuple((r.get(d) or "").strip() == "1" for d in _WEEKDAYS))
    return out


def runs_on(calendar, service, day):
    """Does `service` run on `day` (a date) by `calendar` (read_calendar's)? Sydney Trains publishes no calendar_dates."""
    entry = (calendar or {}).get(service)
    if entry is None:
        return False
    ds = day.strftime("%Y%m%d")
    return entry[0] <= ds <= entry[1] and entry[2][day.weekday()]


def label_minutes(label):
    """Minutes after midnight of the "HH:MM" opening a Sydney Trains vehicle.label ("14:25 Central Station to Lithgow
    Station "), or None. MEASURED: the trip's first departure on 1,311 of 1,511 checks, else up to 7 minutes EARLIER."""
    m = _LABEL_TIME.match(label or "") if isinstance(label, str) else None
    if m is None:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if mi > 59 or h > 47:
        return None
    return h * 60 + mi


def _zone():
    from zoneinfo import ZoneInfo
    return ZoneInfo(TIME_ZONE)


def local_day(now, tz=None):
    """Sydney's date at epoch `now`."""
    return datetime.fromtimestamp(now, tz or _zone()).date()


def service_day_epoch(day, tz=None):
    """The epoch GTFS times of service day `day` count from: noon minus 12 h (local midnight but on DST change days)."""
    noon = datetime.combine(day, dtime(12, 0), tzinfo=tz or _zone())
    return (noon - timedelta(hours=12)).timestamp()


def departure_epoch(day, minutes, now, tz=None):
    """The label's departure on service day `day`: the label shows a clock time, so a run after midnight ("00:15" on
    yesterday's service) is the next day's; of the two, the one nearer `now`."""
    tz = tz or _zone()
    cands = (service_day_epoch(day, tz) + minutes * 60, service_day_epoch(day + timedelta(days=1), tz) + minutes * 60)
    return min(cands, key=lambda t: abs(t - now))


def service_dates(calendar, service, now, previous=None, previous_until=None, tz=None):
    """The dates among yesterday, today and tomorrow (Sydney) on which `service` runs. `previous` (an older publication's
    calendar) answers for yesterday only, and only before `previous_until`."""
    tz = tz or _zone()
    today = local_day(now, tz)
    out = []
    for d in (today - timedelta(days=1), today, today + timedelta(days=1)):
        if runs_on(calendar, service, d):
            out.append(d)
        elif (d < today and previous and previous_until is not None and now < previous_until
              and runs_on(previous, service, d)):
            out.append(d)
    return out


def next_morning(now, tz=None):
    """The epoch of the next PREVIOUS_CALENDAR_UNTIL_HOUR o'clock in Sydney after `now`."""
    tz = tz or _zone()
    day = local_day(now, tz)
    for d in (day, day + timedelta(days=1)):
        t = datetime.combine(d, dtime(PREVIOUS_CALENDAR_UNTIL_HOUR, 0), tzinfo=tz).timestamp()
        if t > now:
            return t
    return now + 86400.0      # not reached


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


def _scrub_sydney(vehicle):
    """Sydney Trains: the nswtrains fields, and its bearing (0 on 2,137 of 2,137 rows)."""
    _scrub(vehicle)
    if vehicle.HasField("position"):
        vehicle.position.ClearField("bearing")


def _ts(entity):
    """A position entity's own timestamp, 0 when it has none."""
    if entity is None:
        return 0
    v = entity.vehicle
    return int(v.timestamp) if v.HasField("timestamp") and int(v.timestamp) > 0 else 0


def _route_types(path):
    """{route_id: route_type} from a timetable zip on disk."""
    with zipfile.ZipFile(path) as z:
        with z.open("routes.txt") as fh:
            return {(r.get("route_id") or "").strip(): (r.get("route_type") or "").strip()
                    for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
                    if (r.get("route_id") or "").strip()}


def _sydney_tables(path):
    """({route_id: agency_id}, calendar) from the Sydney Trains timetable zip: routes.txt and calendar.txt only."""
    with zipfile.ZipFile(path) as z:
        with z.open("routes.txt") as fh:
            agencies = {(r.get("route_id") or "").strip(): (r.get("agency_id") or "").strip()
                        for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
                        if (r.get("route_id") or "").strip()}
        with z.open("calendar.txt") as fh:
            calendar = read_calendar(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")))
    return agencies, calendar


def _calendar_json(calendar):
    return {sid: [a, b, "".join("1" if x else "0" for x in mask)] for sid, (a, b, mask) in (calendar or {}).items()}


def _calendar_from_json(data):
    out = {}
    for sid, row in (data or {}).items():
        a, b, mask = row
        out[sid] = (a, b, tuple(c == "1" for c in mask))
    return out


class TfnswClient:
    """Polls the feeds ONCE for every consumer (B16: budget the IP).

    `consumers` is {consumer: key}; `sydney_trains` names the consumers that take Sydney Trains (`transit` always does;
    set_sydney_trains() changes it later). The shared registration (reference/shared_tfnsw.py) starts with the first
    integration's and register()s each that joins. Only registered consumers get feeds built.

    for_consumer(name) -> (positions, trip_updates): two GTFS-Realtime FeedMessages holding only that consumer's vehicles
    and trips, cleaned (module docstring). trip_updates is None when that file failed and the positions did not. A
    consumer whose own feed failed while another's answered gets the error raised from for_consumer().

    THE TIMETABLES are kept on disk, not in memory: storage_dir/saw_tfnsw_nswtrains.zip (about 40 MB) and, while a
    consumer takes Sydney Trains, storage_dir/saw_tfnsw_sydneytrains.zip (about 11 MB), read by each consumer when
    static_version(timetable) changes. Each is checked with a HEAD at most daily, on its own thread; the feeds wait for
    nothing. A failed download keeps the last copy. With no storage_dir there is no timetable: routing is by prefix alone
    and Sydney Trains' runs carry no start_date (so S1 cannot apply).
    """

    def __init__(self, consumers, user=DEFAULT_USER, timeout=30, opener=None, clock=None, feed_ttl_s=FEED_TTL_S,
                 storage_dir=None, static_timeout=300, background=True, sydney_trains=()):
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
        self._sydney = set()
        consumers = dict(consumers)
        asked = set(sydney_trains or ())
        for consumer in asked:
            _check_consumer(consumer)
            if consumer not in consumers:
                raise ValueError("consumer %r takes Sydney Trains but is not among the consumers" % (consumer,))
        for consumer, key in consumers.items():
            _check_consumer(consumer)
            wants = self._wants_sydney(consumer, consumer in asked)
            self._ring.add(consumer, key)
            self._wanted.add(consumer)
            if wants:
                self._sydney.add(consumer)
        self._feeds = None
        self._fetched_at = None
        self._built_sydney = frozenset()
        self._types = None
        self._version = None
        self._static_at = None
        self._static_retry = 0.0
        self._static_loaded = False
        self._loading = False
        # Sydney Trains' timetable: {route_id: agency_id}, its calendar, and the previous publication's for yesterday.
        self._s_agencies = None
        self._s_calendar = None
        self._s_previous = None
        self._s_previous_until = None
        self._s_version = None
        self._s_static_at = None
        self._s_static_retry = 0.0
        self._s_static_loaded = False
        self._s_loading = False
        # (consumer, run, start_date) -> (fix timestamp, the FeedEntity handed out, when it was handed out)
        self._last_fix = {}
        self.fetch_count = 0
        self.dropped_other_runs = 0
        self.before_departure_dropped = 0
        self.kept_last_fix = 0
        self.undated = 0
        self.sydney_updates_yielded = 0

    @staticmethod
    def _wants_sydney(consumer, asked):
        if consumer == TRANSIT:
            return True
        if asked and consumer not in SYDNEY_CONSUMERS:
            raise ValueError("consumer %r takes nothing from Sydney Trains" % (consumer,))
        return bool(asked)

    @property
    def consumers(self):
        with self._state:
            return frozenset(self._wanted)

    @property
    def sydney_consumers(self):
        """The registered consumers that take Sydney Trains."""
        with self._state:
            return frozenset(self._sydney & self._wanted)

    def register(self, consumer, key, sydney_trains=False):
        """Serve `consumer` from now on, with its key. True if it is new. Never waits for a fetch. sydney_trains=True asks
        for Sydney Trains as well (commuter, intercity; `transit` always has it); it is never turned off here (see
        set_sydney_trains)."""
        _check_consumer(consumer)
        wants = self._wants_sydney(consumer, sydney_trains)
        self._ring.add(consumer, key)
        with self._state:
            if wants:
                self._sydney.add(consumer)
            if consumer in self._wanted:
                return False
            self._wanted.add(consumer)
            return True

    def set_sydney_trains(self, consumer, on):
        """Turn Sydney Trains on or off for a registered consumer (commuter, intercity; `transit` always has it). An
        integration calls it right after reference/shared_tfnsw.py's get_shared_tfnsw_client(), each time it claims;
        unregister() at the last claim forgets it."""
        _check_consumer(consumer)
        if consumer == TRANSIT:
            return
        wants = self._wants_sydney(consumer, on)
        with self._state:
            if wants:
                self._sydney.add(consumer)
            else:
                self._sydney.discard(consumer)

    def unregister(self, consumer):
        """Stop serving `consumer` and forget its key. True if it was registered. Never waits."""
        _check_consumer(consumer)
        self._ring.remove(consumer)
        with self._state:
            self._sydney.discard(consumer)
            if consumer not in self._wanted:
                return False
            self._wanted.discard(consumer)
            return True

    # --- the timetables -------------------------------------------------------

    def _paths(self):
        base = os.path.join(self.storage_dir, STATIC_NAME)
        return base + ".zip", base + ".json"

    def _s_paths(self):
        base = os.path.join(self.storage_dir, SYDNEY_STATIC_NAME)
        return base + ".zip", base + ".json"

    def static_path(self, timetable=NSWTRAINS):
        """A timetable zip on disk (NSWTRAINS or SYDNEYTRAINS), or None before one exists."""
        _check_timetable(timetable)
        if self.storage_dir is None:
            return None
        if timetable == SYDNEYTRAINS:
            self._s_load_cached()
            with self._state:
                have = self._s_version is not None
            path = self._s_paths()[0]
        else:
            self._load_cached()
            with self._state:
                have = self._version is not None
            path = self._paths()[0]
        return path if have and os.path.exists(path) else None

    def static_version(self, timetable=NSWTRAINS):
        """Changes when the timetable does (its Last-Modified). A consumer distills again when it changes."""
        _check_timetable(timetable)
        if timetable == SYDNEYTRAINS:
            self._s_load_cached()
            with self._state:
                return self._s_version
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

    def _s_load_cached(self):
        if self.storage_dir is None:
            return
        with self._state:
            if self._s_static_loaded:
                return
            self._s_static_loaded = True
        zp, meta = self._s_paths()
        try:
            with open(meta, encoding="utf-8") as fh:
                d = json.load(fh)
            agencies, calendar = _sydney_tables(zp)
            previous = _calendar_from_json(d.get("previous_calendar"))
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
            return
        with self._state:
            self._s_version, self._s_static_at = d.get("version"), d.get("checked_at")
            self._s_agencies, self._s_calendar = agencies, calendar
            self._s_previous, self._s_previous_until = previous or None, d.get("previous_until")

    def _static_due(self, now):
        fresh = self._static_at is not None and now - self._static_at < STATIC_TTL_S
        return not fresh and now >= self._static_retry

    def _s_static_due(self, now):
        fresh = self._s_static_at is not None and now - self._s_static_at < STATIC_TTL_S
        return not fresh and now >= self._s_static_retry

    def refresh_static(self, force=False, timetable=NSWTRAINS):
        """Check and if needed download a timetable on THIS thread; never raises. True when it changed."""
        _check_timetable(timetable)
        if timetable == SYDNEYTRAINS:
            return self._s_refresh_static(force)
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

    def _s_refresh_static(self, force):
        if self.storage_dir is None:
            return False
        self._s_load_cached()
        with self._state:
            now = self._clock()
            if (not force and not self._s_static_due(now)) or self._s_loading:
                return False
            self._s_loading = True
            self._s_static_retry = now + STATIC_RETRY_S
        try:
            return self._s_refresh(now)
        except Exception:  # noqa: BLE001 - keep the last copy; the feeds do not need it
            return False
        finally:
            with self._state:
                self._s_loading = False

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

    def _s_refresh(self, now):
        key = self._ring.active()
        self.fetch_count += 1
        _, got = fetch(SYDNEY_STATIC_URL, self._headers(key), self.timeout, self._opener, method="HEAD")
        version = got.get("Last-Modified") or None
        zp, meta = self._s_paths()
        with self._state:
            current, old_calendar = self._s_version, self._s_calendar
            previous, previous_until = self._s_previous, self._s_previous_until
        if version is not None and version == current and os.path.exists(zp):
            self._s_write_meta(meta, version, now, previous, previous_until)
            with self._state:
                self._s_static_at = now
            return False
        self.fetch_count += 1
        tmp = zp + ".tmp"
        try:
            self._download(key, tmp, SYDNEY_STATIC_URL)
            with zipfile.ZipFile(tmp) as z:
                names = set(z.namelist())
            if not SYDNEY_REQUIRED <= names:
                raise FeedUnavailable("timetable zip lacks a required file")
            agencies, calendar = _sydney_tables(tmp)
            os.replace(tmp, zp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        if old_calendar:
            # Design #13: a copy published at 01:01 lacks yesterday's services, whose trips run to 31:35.
            previous, previous_until = old_calendar, next_morning(now)
        self._s_write_meta(meta, version, now, previous, previous_until)
        with self._state:
            self._s_version, self._s_static_at = version, now
            self._s_agencies, self._s_calendar = agencies, calendar
            self._s_previous, self._s_previous_until = previous, previous_until
        return True

    def _download(self, key, tmp, url=STATIC_URL):
        req = urllib.request.Request(url, headers=self._headers(key))
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
        except KeyedError:
            raise
        except Exception as err:  # noqa: BLE001 - never with the headers in the message
            raise FeedUnavailable("%s from %s" % (type(err).__name__, url)) from None

    @staticmethod
    def _write_meta(meta, version, now):
        tmp = meta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": version, "checked_at": now}, fh)
        os.replace(tmp, meta)

    @staticmethod
    def _s_write_meta(meta, version, now, previous, previous_until):
        tmp = meta + ".tmp"
        keep = previous is not None and previous_until is not None and now < previous_until
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": version, "checked_at": now,
                       "previous_calendar": _calendar_json(previous) if keep else None,
                       "previous_until": previous_until if keep else None}, fh)
        os.replace(tmp, meta)

    def _ensure_static(self):
        if self.storage_dir is None:
            return
        with self._state:
            wanted, sydney = frozenset(self._wanted), frozenset(self._sydney & self._wanted)
        if wanted != {TRANSIT}:
            self._ensure_one(NSWTRAINS)
        if sydney:
            self._ensure_one(SYDNEYTRAINS)

    def _ensure_one(self, timetable):
        if timetable == SYDNEYTRAINS:
            self._s_load_cached()
            with self._state:
                due = self._s_static_due(self._clock()) and not self._s_loading
        else:
            self._load_cached()
            with self._state:
                due = self._static_due(self._clock()) and not self._loading
        if not due:
            return
        if not self._background:
            self.refresh_static(timetable=timetable)
            return
        # nswtrains' thread keeps its 0.2.23 name (a test joins it by name).
        name = "saw_tfnsw_timetable" if timetable == NSWTRAINS else "saw_tfnsw_timetable_" + timetable
        threading.Thread(target=self.refresh_static, kwargs={"timetable": timetable}, name=name, daemon=True).start()

    # --- the feeds --------------------------------------------------------------

    def _fetch_positions(self, url=POSITIONS_URL, updates_url=TRIP_UPDATES_URL):
        self._backoff.check(url)
        key = self._ring.active()
        self.fetch_count += 1
        try:
            raw, _ = fetch(url, self._headers(key), self.timeout, self._opener)
        except RateLimited as err:
            self._backoff.hit(err)
            raise
        except KeyRefused:
            self._positions_refused(key, updates_url)
            raise
        self._ring.ok()
        return _decode(raw, url), key

    def _positions_refused(self, key, updates_url=TRIP_UPDATES_URL):
        """Counts against the key only if the same feed's updates refuse it too (design section 2)."""
        self.fetch_count += 1
        try:
            fetch(updates_url, self._headers(key), self.timeout, self._opener)
        except KeyRefused:
            if self._ring.refused(key):
                self._ring.active()     # AuthRefused now, when that was the last key
        except KeyedError:
            pass
        else:
            self._ring.ok()

    def _fetch_updates(self, key, url=TRIP_UPDATES_URL):
        self.fetch_count += 1
        try:
            raw, _ = fetch(url, self._headers(key), self.timeout, self._opener)
            return _decode(raw, url)
        except RateLimited as err:
            self._backoff.hit(err)
            return None
        except KeyedError:
            # Measured: this endpoint alone answered 401 four times with the key good. Not counted against the key.
            return None

    def _split(self, vp, tu, wanted, types):
        """nswtrains' two feeds cut per consumer: the 0.2.23 rules, unchanged."""
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
        return out

    def _date_for(self, trip_id, label, now, tables):
        """The service day of a Sydney Trains trip (a date), or None: the one date among yesterday, today and tomorrow
        on which its service runs; of two (none measured), the one whose label time ON THAT DATE is nearer now, as the
        design reads. Not departure_epoch: it lets a date reach into the next day, and two dates then share one
        departure. Known limit: a run after midnight ("00:15" on yesterday's service) is taken for today's."""
        _agencies, calendar, previous, previous_until = tables
        service = service_of(trip_id)
        if service is None or calendar is None:
            return None
        tz = _zone()
        days = service_dates(calendar, service, now, previous, previous_until, tz)
        if len(days) == 1:
            return days[0]
        minutes = label_minutes(label)
        if not days or minutes is None:
            return None
        return min(days, key=lambda d: abs(service_day_epoch(d, tz) + minutes * 60 - now))

    @staticmethod
    def _before_departure_stale(vehicle, day, now):
        """S1: True for a run not yet departed whose fix is older than BEFORE_DEPARTURE_MAX_AGE_S (or has no time)."""
        if day is None:
            return False
        minutes = label_minutes(vehicle.vehicle.label if vehicle.HasField("vehicle") else "")
        if minutes is None:
            return False
        if now >= departure_epoch(day, minutes, now):
            return False
        if not vehicle.HasField("timestamp") or int(vehicle.timestamp) <= 0:
            return True
        return now - int(vehicle.timestamp) > BEFORE_DEPARTURE_MAX_AGE_S

    def _split_sydney(self, svp, stu, consumers, now):
        """{consumer: ([position entities], [update entities] or None)}: Sydney Trains cut per consumer, dated,
        cleaned, S1 applied."""
        with self._state:
            tables = (self._s_agencies, self._s_calendar, self._s_previous, self._s_previous_until)
        agencies = tables[0]
        pos = {c: [] for c in consumers}
        upd = {c: ([] if stu is not None else None) for c in consumers}
        owner, dated = {}, {}
        for ent in svp.entity:
            if not ent.HasField("vehicle"):
                continue
            v = ent.vehicle
            if not v.HasField("trip") or not v.trip.trip_id.strip() or not v.HasField("position"):
                continue
            tid = v.trip.trip_id.strip()
            c = sydney_consumer_of(v.trip.route_id, agencies)
            owner[tid] = c
            if c not in pos:
                continue
            day = self._date_for(tid, v.vehicle.label if v.HasField("vehicle") else "", now, tables)
            if day is not None:
                dated[tid] = day
            else:
                self.undated += 1
            e = type(ent)()
            e.CopyFrom(ent)
            _scrub_sydney(e.vehicle)
            if day is not None:
                e.vehicle.trip.start_date = day.strftime("%Y%m%d")
            if self._before_departure_stale(e.vehicle, day, now):
                self.before_departure_dropped += 1
                continue
            pos[c].append(e)
        if stu is not None:
            for ent in stu.entity:
                if not ent.HasField("trip_update"):
                    continue
                trip = ent.trip_update.trip
                tid = trip.trip_id.strip()
                if not tid:
                    continue
                c = owner[tid] if tid in owner else sydney_consumer_of(trip.route_id, agencies)
                if c not in upd:
                    continue
                day = dated.get(tid) or self._date_for(tid, "", now, tables)
                e = type(ent)()
                e.CopyFrom(ent)
                if day is not None:
                    e.trip_update.trip.start_date = day.strftime("%Y%m%d")
                upd[c].append(e)
        return {c: (pos[c], upd[c]) for c in consumers}

    def _merged(self, consumer, nsw, syd, src, now):
        """ONE entity per run-day across nswtrains' cut (`nsw`, from _split) and Sydney Trains' (`syd`), design section 4.
        `src` holds the four source feeds (for headers); either side may be absent."""
        vp, tu, svp, stu = src
        nsw_pos = list(nsw[0].entity) if nsw is not None else []
        nsw_upd = list(nsw[1].entity) if nsw is not None and nsw[1] is not None else []
        syd_pos, syd_upd = syd if syd is not None else ([], None)
        positions = _new_feed(vp if vp is not None else svp)
        upd_src = tu if (nsw is not None and nsw[1] is not None) else stu if syd_upd is not None else None
        updates = _new_feed(upd_src) if upd_src is not None else None

        groups, nsw_days, nsw_trip = {}, {}, {}
        for e in nsw_pos:
            t = e.vehicle.trip
            key = (run_of(t.trip_id), t.start_date)
            g = groups.setdefault(key, [None, None])
            if g[0] is None:
                g[0] = e
            nsw_days.setdefault(key[0], set()).add(key[1])
        for e in nsw_upd:
            t = e.trip_update.trip
            key = (run_of(t.trip_id), t.start_date)
            nsw_trip.setdefault(key, e)
            nsw_days.setdefault(key[0], set()).add(key[1])
        for e in syd_pos:
            t = e.vehicle.trip
            run, day = run_of(t.trip_id), t.start_date
            if not day and len(nsw_days.get(run, ())) == 1:
                day = next(iter(nsw_days[run]))
            g = groups.setdefault((run, day), [None, None])
            if g[1] is None or _ts(e) > _ts(g[1]):
                g[1] = e

        for key in sorted(groups):
            n, s = groups[key]
            if n is not None and s is not None:
                fix = s if _ts(s) > _ts(n) + TIE_S else n
            else:
                fix = n if n is not None else s
            fix_ts = _ts(fix)
            mem = (consumer,) + key
            last = self._last_fix.get(mem)
            if last is not None and fix_ts < last[0]:
                fix, fix_ts = last[1], last[0]
                self.kept_last_fix += 1
            kept = type(fix)()
            kept.CopyFrom(fix)
            self._last_fix[mem] = (fix_ts, kept, now)
            e = positions.entity.add()
            e.CopyFrom(fix)
            if n is not None:
                e.vehicle.trip.CopyFrom(n.vehicle.trip)
                e.vehicle.vehicle.CopyFrom(n.vehicle.vehicle)
            elif key in nsw_trip:
                e.vehicle.trip.CopyFrom(nsw_trip[key].trip_update.trip)
            else:
                e.vehicle.trip.CopyFrom(s.vehicle.trip)

        if updates is not None:
            positioned = {}
            for e in positions.entity:
                t = e.vehicle.trip
                positioned.setdefault(t.trip_id.strip(), set()).add(t.start_date)
            for e in nsw_upd:
                t = e.trip_update.trip
                tid = t.trip_id.strip()
                if tid in positioned and t.start_date not in positioned[tid]:
                    self.dropped_other_runs += 1
                    continue
                updates.entity.add().CopyFrom(e)
            for e in syd_upd or ():
                t = e.trip_update.trip
                run, day = run_of(t.trip_id), t.start_date
                if (day in nsw_days.get(run, ())) if day else bool(nsw_days.get(run)):
                    self.sydney_updates_yielded += 1
                    continue
                updates.entity.add().CopyFrom(e)
        return positions, updates

    def _forget_old_fixes(self, now):
        for mem in [m for m, v in self._last_fix.items() if now - v[2] > LAST_FIX_KEEP_S]:
            del self._last_fix[mem]

    def snapshot(self, force=False):
        """{consumer: (positions, trip_updates), or the error that consumer's feed met} for the registered consumers."""
        self._ensure_static()
        with self._lock:
            now = self._clock()
            if (not force and self._feeds is not None and self._fetched_at is not None
                    and now - self._fetched_at < self.feed_ttl_s):
                return self._feeds
            with self._state:
                wanted = frozenset(self._wanted)
                sydney = frozenset(self._sydney & self._wanted)
                types = self._types
            if not sydney:
                # No consumer takes Sydney Trains: 0.2.23 exactly.
                vp, key = self._fetch_positions()
                tu = self._fetch_updates(key)
                out = self._split(vp, tu, wanted, types)
            else:
                out = self._snapshot_with_sydney(wanted, sydney, types, now)
            self._feeds = out
            self._fetched_at = now
            self._built_sydney = sydney
            return out

    def _snapshot_with_sydney(self, wanted, sydney, types, now):
        need_nsw = bool(wanted - {TRANSIT})
        vp = tu = svp = stu = None
        nsw_err = syd_err = None
        if need_nsw:
            try:
                vp, key = self._fetch_positions()
                tu = self._fetch_updates(key)
            except KeyedError as err:
                nsw_err = err
        try:
            svp, key = self._fetch_positions(SYDNEY_POSITIONS_URL, SYDNEY_TRIP_UPDATES_URL)
            stu = self._fetch_updates(key, SYDNEY_TRIP_UPDATES_URL)
        except KeyedError as err:
            syd_err = err
        if vp is None and svp is None:
            raise nsw_err if nsw_err is not None else syd_err
        base = self._split(vp, tu, wanted - {TRANSIT}, types) if vp is not None else {}
        cut = self._split_sydney(svp, stu, sydney, now) if svp is not None else None
        self._forget_old_fixes(now)
        out = {}
        src = (vp, tu, svp, stu)
        for c in wanted:
            if c == TRANSIT:
                if cut is None:
                    out[c] = syd_err
                    continue
                p_list, u_list = cut[c]
                positions = _new_feed(svp)
                for e in p_list:
                    positions.entity.add().CopyFrom(e)
                updates = None
                if u_list is not None:
                    updates = _new_feed(stu)
                    for e in u_list:
                        updates.entity.add().CopyFrom(e)
                out[c] = (positions, updates)
            elif c in sydney:
                out[c] = self._merged(c, base.get(c), cut[c] if cut is not None else None, src, now)
            else:
                out[c] = base[c] if c in base else nsw_err
        return out

    def for_consumer(self, consumer, force=False):
        _check_consumer(consumer)
        if consumer not in self.consumers:
            raise ValueError("consumer %r is not registered with this client" % (consumer,))
        feeds = self.snapshot(force=force)
        if consumer not in feeds or ((consumer in self.sydney_consumers) != (consumer in self._built_sydney)):
            # Registered, or its Sydney Trains choice changed, after this snapshot was built.
            feeds = self.snapshot(force=True)
        got = feeds[consumer]
        if isinstance(got, Exception):
            raise got
        return got

    def stats(self):
        """Low-cardinality state for a diagnostic. Never a key."""
        with self._state:
            version, types, loading = self._version, self._types, self._loading
            s_version, s_agencies, s_loading = self._s_version, self._s_agencies, self._s_loading
            sydney = sorted(self._sydney & self._wanted)
        return {"timetable": version, "timetable_routes": len(types) if types else None, "timetable_loading": loading,
                "sydney_timetable": s_version, "sydney_timetable_routes": len(s_agencies) if s_agencies else None,
                "sydney_timetable_loading": s_loading, "sydney_consumers": sydney,
                "fetch_count": self.fetch_count, "dropped_other_runs": self.dropped_other_runs,
                "before_departure_dropped": self.before_departure_dropped, "kept_last_fix": self.kept_last_fix,
                "undated": self.undated, "sydney_updates_yielded": self.sydney_updates_yielded,
                "runs_remembered": len(self._last_fix),
                "backing_off": self._clock() < self._backoff.until, **self._ring.state()}
