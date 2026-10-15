"""Shared Fintraffic Digitraffic RAIL client (Finland) for the Live Track family.

Hoisted at birth, 2026-10-14 (Lee: "Hoist now"): three consumers read the one
feed - Live Track Intercity Rail (VR long distance), Live Track Commuter Rail
(HSL and VR local) and Live Track Freight Trains (cargo, every other
non-passenger category, and MV empty stock). Pure Python: NO homeassistant imports
(05_SHARED_LESSONS.md B8.1a). One poller per Home Assistant through the
shared-coordinator pattern (reference/shared_digitraffic_rail.py, B16).

Data: Fintraffic / digitraffic.fi, CC BY 4.0. Provider record:
35_Dependencies/third_party/digitraffic/SSOT.md. Every rule here was measured
on capture round 1 (2026-10-11 08:41Z to 2026-10-13 12:00Z):
30_Integrations/Live_Track_Intercity_Rail/20_Research/finland/
FI_CAPTURE1_ANALYSIS_20261014.md.

Two endpoints, joined on (trainNumber, departureDate):
    train-locations/latest   every train with a fix, passenger or not.
                             Position, speed, fix time. NO category, NO bearing.
    trains/<date>,           what each train is (trainCategory, trainType,
    trains?version=<n>       commuterLineID, operator, cancelled) and its
                             timetable. ?version= answers incrementally.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import json
import threading
import urllib.error
import urllib.request

# Stamped into the shared hass.data key (B8.1a): a consumer that finds an entry
# at a version it does not understand builds its own client instead.
# 2 (0.2.7): consumers register with the client, which keeps only their
# trains; the shared entry counts registrations per consumer.
CONTRACT_VERSION = 2
SHARED_KEY = "saw_digitraffic_rail_v%d" % CONTRACT_VERSION

BASE = "https://rata.digitraffic.fi/api/v1"
DEFAULT_USER = "StrausbergAutomationWorks/saw-livetrack"
ATTRIBUTION = "Data: Fintraffic / digitraffic.fi, CC BY 4.0"

# --- Who owns which train (06_MAP_CONTRACT.md D1: one owner per train) -----
INTERCITY = "intercity"
COMMUTER = "commuter"
FREIGHT = "freight"
CONSUMERS = (INTERCITY, COMMUTER, FREIGHT)
# Lee 2026-10-11 03:38: long distance (GTFS 102) to Intercity Rail, commuter
# (109) to Commuter Rail. Lee 2026-10-14 03:23: locomotives, shunting, track
# machines and test runs to Live Track Freight Trains - with cargo, every
# category that is not a passenger train.
PASSENGER_CATEGORIES = {"Long-distance": INTERCITY, "Commuter": COMMUTER}
# Lee 2026-10-14 16:25: MV (matkustajaliikenteen tyhjavaunujuna, an empty-stock
# passenger train) carries no passengers and goes to Freight Trains, although
# Digitraffic files it under Long-distance. A type named here wins over its
# category.
# Lee 2026-10-14 21:55: V, a varikkojuna (depot or yard train, a non-commercial
# move of rolling stock), also Long-distance in Digitraffic, to Freight Trains.
TYPE_OWNERS = {"MV": FREIGHT, "V": FREIGHT}

# Measured: a new fix at every 30 s poll (interval p50 30 s, p90 34 s).
LOCATION_TTL_S = 30.0
TABLE_TTL_S = 60.0


class DigitrafficError(Exception):
    """Base error for this client."""


class RateLimited(DigitrafficError):
    """HTTP 429. Back off; NOT 'no trains'."""


class FeedUnavailable(DigitrafficError):
    """Transport, HTTP or decode failure. Distinct from an empty answer (C2)."""


def _check_consumer(consumer):
    if consumer not in CONSUMERS:
        raise ValueError("unknown consumer %r" % (consumer,))


def consumer_of(category, train_type=None):
    """The integration that owns a train of this `trainCategory`, or None.

    A `trainType` in TYPE_OWNERS overrides the category (MV, empty stock).

    None only when the category is unknown (the train is not in the table
    yet): an unlisted train is drawn by nobody rather than guessed at.
    """
    if not category or not isinstance(category, str):
        return None
    if isinstance(train_type, str) and train_type in TYPE_OWNERS:
        return TYPE_OWNERS[train_type]
    return PASSENGER_CATEGORIES.get(category, FREIGHT)


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------

def _request(path, user, timeout, opener=None):
    """GET one path; returns decoded JSON.

    05_SHARED_LESSONS.md C5: Digitraffic answers HTTP 406 without
    `Accept-Encoding: gzip`, and states the reason in the BODY - so the body
    goes into the error.
    """
    url = "%s/%s" % (BASE, path)
    req = urllib.request.Request(url, headers={
        "Accept-Encoding": "gzip",
        "Digitraffic-User": user,
        "User-Agent": user,
    })
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as err:
        if err.code == 429:
            raise RateLimited("HTTP 429 from %s" % path) from err
        try:
            body = err.read()[:200].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        raise FeedUnavailable("HTTP %s from %s: %s" % (err.code, path, body)) from err
    except Exception as err:  # noqa: BLE001 - transport of any kind
        raise FeedUnavailable("%s from %s" % (type(err).__name__, path)) from err
    try:
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))
    except Exception as err:  # noqa: BLE001
        raise FeedUnavailable("undecodable body from %s" % path) from err


def parse_ts(value):
    """'2026-10-11T08:39:19.000Z' -> aware datetime, or None."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


# --------------------------------------------------------------------------
# Positions
# --------------------------------------------------------------------------

def parse_locations(payload):
    """train-locations/latest -> list of dicts, malformed entries skipped.

    Each: key (number, departure_date), number, departure_date, lat, lon,
    speed_kmh (int or None), observed_at (aware datetime or None), gps.
    The feed publishes NO bearing (measured: none on any train).
    """
    out = []
    if not isinstance(payload, list):
        return out
    for item in payload:
        if not isinstance(item, dict):
            continue
        number, date = item.get("trainNumber"), item.get("departureDate")
        coords = (item.get("location") or {}).get("coordinates")
        if number is None or not date or not isinstance(coords, list) or len(coords) < 2:
            continue
        try:
            lon, lat = float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        speed = item.get("speed")
        out.append({
            "key": (str(number), str(date)),
            "number": str(number),
            "departure_date": str(date),
            "lat": lat,
            "lon": lon,
            "speed_kmh": int(speed) if isinstance(speed, (int, float)) else None,
            "observed_at": parse_ts(item.get("timestamp")),
            "gps": bool(item.get("isGpsLocation")),
        })
    return out


# --------------------------------------------------------------------------
# What each train is
# --------------------------------------------------------------------------

class TrainTable:
    """The day's trains, keyed (number, departureDate), merged by `version`.

    A train is replaced only by an equal or newer version, so an incremental
    answer arriving out of order cannot roll a train back.
    """

    def __init__(self):
        self.trains = {}
        self.version = 0

    def update(self, payload, keep=None):
        """Merge trains in. keep(train) -> bool: a train it rejects is not
        stored, and a stored one it now rejects (its category or type changed
        to a consumer nobody registered) is dropped. The table's version
        still advances past it, so ?version= never asks for it again."""
        changed = 0
        if not isinstance(payload, list):
            return changed
        for t in payload:
            if not isinstance(t, dict):
                continue
            number, date = t.get("trainNumber"), t.get("departureDate")
            if number is None or not date:
                continue
            ver = t.get("version") if isinstance(t.get("version"), int) else 0
            key = (str(number), str(date))
            old = self.trains.get(key)
            self.version = max(self.version, ver)
            if keep is not None and not keep(t):
                if old is not None and ver >= (old.get("version") or 0):
                    del self.trains[key]
                continue
            if old is None or ver >= (old.get("version") or 0):
                self.trains[key] = t
                changed += 1
        return changed

    def drop(self, keep):
        """Remove every stored train keep() rejects; how many went."""
        gone = [k for k, t in self.trains.items() if not keep(t)]
        for k in gone:
            del self.trains[k]
        return len(gone)

    def prune(self, today, keep_days=1):
        """Drop trains whose departure date is before today - keep_days.
        A night train departs one date and runs into the next, hence 1."""
        cutoff = (today - _dt.timedelta(days=keep_days)).isoformat()
        for key in [k for k in self.trains if k[1] < cutoff]:
            del self.trains[key]

    def get(self, key):
        return self.trains.get(key)


def describe(train):
    """The fields a consumer styles and routes by."""
    if not isinstance(train, dict):
        return {"category": None, "train_type": None, "commuter_line": None,
                "operator": None, "cancelled": False}
    return {
        "category": train.get("trainCategory"),
        "train_type": train.get("trainType"),
        "commuter_line": train.get("commuterLineID") or None,
        "operator": train.get("operatorShortCode"),
        "cancelled": bool(train.get("cancelled")),
    }


def next_stop(train, commercial_only=False):
    """(station_code, eta, delay_min) of the next stop, or None when there is none.

    The timetable lists passing points too (`trainStopping` false): only rows
    where the train stops count, and for passengers only commercial stops.
    ETA is the live estimate, else the schedule. `differenceInMinutes` is the
    delay ROUNDED to the minute (measured: 50,137 of 50,551 rows); the next
    stop's estimate wins, else the last recorded actual.
    """
    rows = train.get("timeTableRows") if isinstance(train, dict) else None
    if not isinstance(rows, list):
        return None
    last_actual = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("actualTime"):
            if isinstance(row.get("differenceInMinutes"), int):
                last_actual = row["differenceInMinutes"]
            continue
        if row.get("cancelled") or not row.get("trainStopping"):
            continue
        if commercial_only and not row.get("commercialStop"):
            continue
        eta = parse_ts(row.get("liveEstimateTime")) or parse_ts(row.get("scheduledTime"))
        delay = row.get("differenceInMinutes")
        if not isinstance(delay, int):
            delay = last_actual
        return row.get("stationShortCode"), eta, delay
    return None


def join(locations, table):
    """Each position with what its train is and who owns it.

    `consumer` is None for a train not (yet) in the table: nobody draws it.
    `train` is the table's own record (timetable included), or None, so a
    consumer never has to read the table itself while another thread
    refreshes it.
    """
    out = []
    for loc in locations:
        t = table.get(loc["key"])
        info = describe(t)
        rec = dict(loc)
        rec.update(info)
        rec["train"] = t
        rec["consumer"] = consumer_of(info["category"], info["train_type"])
        out.append(rec)
    return out


def split(records):
    """{consumer: [records]} for the three consumers; unowned records dropped."""
    out = {c: [] for c in CONSUMERS}
    for rec in records:
        if rec.get("consumer") in out:
            out[rec["consumer"]].append(rec)
    return out


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------

class DigitrafficRailClient:
    """Polls Digitraffic rail ONCE for every consumer (B16: budget the IP).

    KEEPS ONLY WHAT IS REGISTERED (0.2.7). `consumers` are the consumers this
    client serves (default: all three, for a standalone client); the shared
    registration (reference/shared_digitraffic_rail.py) starts with the first
    integration's and register()s each that joins. The table stores only the
    registered consumers' trains: measured 2026-10-14, a client keeping
    every category held about 138 MB. A consumer joining a loaded client
    makes the next refresh load the day lists again, for its trains; one
    leaving has its trains dropped at the next refresh.

    THE GUARD (0.2.7). One lock covers every fetch and every table change, so
    consumers on different executor threads make one set of requests between
    them and never see a half-merged table. register() and unregister() take
    only a small state lock, never held across a request: they are called
    on Home Assistant's event loop and must not wait for a fetch.

    First table load asks for the dates around now (departureDate is the
    Finnish date, up to three hours ahead of UTC); after that `?version=`
    returns only what changed. Positions are cached for LOCATION_TTL_S, the
    table for TABLE_TTL_S, so three consumers polling at 30 s cost one request
    of each per cycle.
    """

    def __init__(self, user=DEFAULT_USER, timeout=30, opener=None, clock=None,
                 location_ttl_s=LOCATION_TTL_S, table_ttl_s=TABLE_TTL_S,
                 consumers=CONSUMERS):
        if not user or not str(user).strip():
            raise ValueError("a non-empty Digitraffic-User is required")
        self.user = user
        self.timeout = timeout
        self._opener = opener
        self._clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self.location_ttl_s = float(location_ttl_s)
        self.table_ttl_s = float(table_ttl_s)
        self.table = TrainTable()
        self._locations = None
        self._locations_at = None
        self._table_at = None
        self.fetch_count = 0
        for c in consumers:
            _check_consumer(c)
        # Fetches and the table. Held across requests.
        self._lock = threading.RLock()
        # What is wanted and what the next refresh must do. Never held across
        # a request (register/unregister run on the event loop).
        self._state = threading.Lock()
        self._wanted = set(consumers)
        self._reload = False
        self._prune = False
        self._loaded = False

    @property
    def consumers(self):
        """The consumers this client keeps trains for."""
        with self._state:
            return frozenset(self._wanted)

    def register(self, consumer):
        """Keep `consumer`'s trains from now on. True if it is new. Never waits
        for a fetch: a newcomer to a loaded table is loaded at the next refresh."""
        _check_consumer(consumer)
        with self._state:
            if consumer in self._wanted:
                return False
            self._wanted.add(consumer)
            if self._loaded:
                self._reload = True
            return True

    def unregister(self, consumer):
        """Stop keeping `consumer`'s trains; they go at the next refresh. True if
        it was registered. Never waits for a fetch."""
        _check_consumer(consumer)
        with self._state:
            if consumer not in self._wanted:
                return False
            self._wanted.discard(consumer)
            self._prune = True
            return True

    def _get(self, path):
        self.fetch_count += 1
        return _request(path, self.user, self.timeout, self._opener)

    def _fresh(self, at, ttl):
        return at is not None and (self._clock() - at).total_seconds() < ttl

    def locations(self, force=False):
        with self._lock:
            if force or not self._fresh(self._locations_at, self.location_ttl_s):
                self._locations = parse_locations(self._get("train-locations/latest"))
                self._locations_at = self._clock()
            return self._locations

    def refresh_table(self, force=False):
        with self._lock:
            with self._state:
                wanted = frozenset(self._wanted)
                reload, prune = self._reload, self._prune
                self._prune = False

            def keep(t):
                return consumer_of(t.get("trainCategory"), t.get("trainType")) in wanted

            if prune:
                self.table.drop(keep)
            if not force and not reload and self._fresh(self._table_at, self.table_ttl_s):
                return 0
            now = self._clock()
            changed = 0
            if self.table.version == 0 or reload:
                day = now.date()
                for d in (day - _dt.timedelta(days=1), day, day + _dt.timedelta(days=1)):
                    changed += self.table.update(self._get("trains/%s" % d.isoformat()), keep)
                with self._state:
                    self._loaded = True
                    # A consumer that registered DURING this load is not in it.
                    self._reload = not self._wanted <= wanted
            else:
                changed += self.table.update(self._get("trains?version=%d" % self.table.version), keep)
            self.table.prune(now.date())
            self._table_at = now
            return changed

    def snapshot(self, force=False):
        """{consumer: [records]} - every train with a fix, routed to its owner."""
        with self._lock:
            self.refresh_table(force=force)
            return split(join(self.locations(force=force), self.table))

    def for_consumer(self, consumer, force=False):
        _check_consumer(consumer)
        if consumer not in self.consumers:
            raise ValueError("consumer %r is not registered with this client" % (consumer,))
        return self.snapshot(force=force)[consumer]
