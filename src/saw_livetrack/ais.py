"""The AIS seam shared by every SAW consumer of AIS (backlog 315, 2026-10-30).

! MOVED VERBATIM from Live Track Ships at ccebaee: sources/base.py (the Observation every AIS source yields,
the rate-of-turn decode, what a transmitter is) and sources/statics.py (the in-memory MMSI join table). What stayed
in Ships: map_layer, object_ids, valid_imo, sar_airframe, and the SQLite load/save of the statics, which Ships
persists (Lee 2026-10-30 03:13 and 03:2x). Ships' modules import these back, so its BarentsWatch and Digitraffic
sources yield this same Observation. "SSOT" in the comments below is Live Track Ships' SSOT unless it says otherwise.
! No Home Assistant import.
"""

from __future__ import annotations

import datetime as _dt
import re
import time
from dataclasses import dataclass, field

KNOTS_TO_KMH = 1.852

# ---------------------------------------------------------------------------
# ⚠ RATE OF TURN — one decode, here, for all three sources.
#
# The AIS wire value is NOT an angular rate. ITU-R M.1371 encodes it as
#     raw = 4.733 * sqrt(ROT in degrees per MINUTE), signed
# so raw 11 is about 5.4 deg/min - 0.09 deg/s - and publishing 11 under a key
# named `_dps` is D0's wrong-value-worse-than-absent case: the consumer cannot
# tell. DECODE, never pass through.
#
# ⚠ THREE values are special and they are NOT all negative:
#     -128  no turn information available
#     -127  turning LEFT  faster than 5 deg/30 s, magnitude unknown
#     +127  turning RIGHT faster than 5 deg/30 s, magnitude unknown
#
# ⚠⚠ THIS CONSTANT LIVED IN FOUR PLACES AND HELD THREE DIFFERENT VALUES -
# (-128, -127) in two adapters, (-128, 128) in a third, and a fourth copy in
# const.py that nothing imported. Every list enumerated the NEGATIVE specials
# rather than the SATURATED ones, so +127 was published as a reading: 127
# "degrees per second" is 21 revolutions a minute. Measured in one Digitraffic
# pull of 1,063 vessels: 12 at -127 and 18 at +127, so the COMMONER of the two
# was the one let through. SSOT.md items 18 and 20.
#
# ±127 is SATURATED, not unavailable - direction is known, magnitude is not.
# No contract field carries that honestly, so under D0 it is OMITTED, by the
# same rule for both signs on every source. Do not reintroduce a per-adapter
# sentinel list for this field; that is what drifted.
ROT_UNAVAILABLE = -128
ROT_SATURATED = (-127, 127)
ROT_SCALE = 4.733


def rot_to_dps(raw) -> float | None:
    """AIS rate-of-turn byte -> degrees per second, signed, or None."""
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value == ROT_UNAVAILABLE or value in ROT_SATURATED:
        return None
    # ⚠ Outside the signed byte it is not an AIS rate of turn at all.
    if not -128 <= value <= 127:
        return None
    dps = ((abs(value) / ROT_SCALE) ** 2) / 60.0
    return round(dps if value >= 0 else -dps, 4)



@dataclass(frozen=True)
class Observation:
    """One sighting of one object, from any source.

    ⚠ `identity` is the natural key (MMSI here) and is what unique_id is built
    from. NEVER build identity from a name - AIS ShipName is a 20-character
    field operators mangle to fit, and entity ids are permanent.
    """

    identity: str                      # MMSI as a string
    source: str                        # the integration domain
    observed_at: _dt.datetime          # when the POSITION was true

    latitude: float | None = None
    longitude: float | None = None

    course_deg: float | None = None    # direction of TRAVEL
    heading_deg: float | None = None   # direction the hull POINTS
    speed_kmh: float | None = None
    rate_of_turn_dps: float | None = None   # DECODED. See rot_to_dps above

    name: str | None = None
    ship_type: int | None = None       # ITU. 0 is "unavailable", not a type
    call_sign: str | None = None
    destination: str | None = None
    imo: int | None = None
    length_m: float | None = None
    beam_m: float | None = None
    draught_m: float | None = None
    nav_status: int | None = None

    # Required before any consumer may dead-reckon. See D3.
    position_source: str = "observed"
    max_extrapolation_s: int | None = None

    raw_message_type: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        """What the object is: one of OBJECT_KINDS. See object_kind()."""
        return object_kind(self.identity, self.name, self.length_m,
                           self.raw_message_type)

    @property
    def is_vessel(self) -> bool:
        """Not every AIS transmitter is on a ship. An aid to navigation is a
        fixed object, a base station is a building, and a net buoy is a float
        on a fishing line; none has a vessel type, a size or a voyage."""
        return self.kind == "vessel"


# ---------------------------------------------------------------------------
# What an AIS transmitter is, from what it sends. ITU-R M.585 fixes the MMSI
# formats for everything that is not a ship station; the net buoys are the
# exception below.
OBJECT_KINDS = ("vessel", "net_buoy", "aton", "base_station", "sar_aircraft",
                "distress_beacon")

NON_VESSEL_MESSAGES = {
    "AidsToNavigationReport": "aton",
    "BaseStationReport": "base_station",
    "StandardSearchAndRescueAircraftReport": "sar_aircraft",
}

# ⚠ A NET BUOY IS KNOWN BY ITS NAME. ITU gives fishing-gear devices 979xxxxxx
# on channel 2006 only, never the AIS channels; the ones on the AIS channels
# carry ship-shaped MMSIs (5867xxxxx off southern Norway), no type, no size
# and no call sign. The factory name is the tell - FISHING BOUY, NET
# MARK, BUOY - and it is also how Global Fishing Watch flags "likely gear".
# A real vessel with such a word in its name (a BUOY TENDER) broadcasts its
# length, so a broadcast length rules the name out.
_GEAR_NAME = re.compile(
    r"\b(BUOY|BOUY|BOYA|BOUE|NET\s*MARK(ER)?|NET\s*BUOY|FISHING\s*NET|"
    r"FISH(ING)?\s*GEAR)\b", re.I)


def object_kind(identity, name=None, length_m=None,
                raw_message_type=None) -> str:
    """vessel, net_buoy, aton, base_station, sar_aircraft or distress_beacon."""
    kind = NON_VESSEL_MESSAGES.get(raw_message_type or "")
    if kind:
        return kind
    mmsi = str(identity or "")
    if len(mmsi) == 9 and mmsi.isdigit():
        if mmsi.startswith("979"):
            return "net_buoy"
        if mmsi.startswith(("970", "972", "974")):
            return "distress_beacon"
        if mmsi.startswith("99"):
            return "aton"
        if mmsi.startswith("00"):
            return "base_station"
        if mmsi.startswith("111"):
            return "sar_aircraft"
    if name and not length_m and _GEAR_NAME.search(name):
        return "net_buoy"
    return "vessel"


# Written to disk, in column order. `name` is kept for the reassignment guard.
PERSISTED = ("name", "type", "call_sign", "imo", "length_m", "beam_m")
HULL = PERSISTED[1:]
VOYAGE = ("destination", "draught_m")

RETENTION_DAYS = 90
MAX_ENTRIES = 50_000

_NAME_KEY = re.compile(r"[^A-Z0-9]")


def name_key(name) -> str | None:
    """A name compared on letters and digits only, so padding, spacing and
    punctuation are not a difference. Both names come from the same
    transponder, mangling included, so nothing is repaired (SSOT section 6)."""
    if not name:
        return None
    key = _NAME_KEY.sub("", str(name).upper())
    return key or None


class StaticRecord:
    """One vessel's statics. Slots, because a busy entry holds tens of
    thousands of these."""

    __slots__ = PERSISTED + VOYAGE + ("first_seen", "last_seen")

    def __init__(self, first_seen: int, last_seen: int):
        for k in PERSISTED + VOYAGE:
            setattr(self, k, None)
        self.first_seen = first_seen
        self.last_seen = last_seen

    def has_hull(self) -> bool:
        return any(getattr(self, k) is not None for k in HULL)


class StaticCache:
    """The MMSI join table: in memory, loaded from and flushed to SQLite."""

    def __init__(self, retention_s: float = RETENTION_DAYS * 86400,
                 max_entries: int = MAX_ENTRIES, clock=time.time):
        self._d: dict[str, StaticRecord] = {}
        self._fresh: set[str] = set()      # heard since this start
        self._dirty: set[str] = set()      # to write at the next flush
        self._retention = retention_s
        self._max = max_entries
        self._clock = clock

    def __len__(self) -> int:
        return len(self._d)

    def __contains__(self, mmsi) -> bool:
        return mmsi in self._d

    # -- from the air ------------------------------------------------------
    def update(self, mmsi: str, fields: dict) -> StaticRecord:
        """Record what one static message carried. Fields it did not carry
        keep their values; a field it carried as None is cleared, exactly as
        the vessel declared it."""
        now = int(self._clock())
        rec = self._d.get(mmsi)
        if rec is None:
            rec = self._d[mmsi] = StaticRecord(now, now)
        for k, v in fields.items():
            if k in PERSISTED or k in VOYAGE:
                setattr(rec, k, v)
        rec.last_seen = now
        self._fresh.add(mmsi)
        self._dirty.add(mmsi)
        return rec

    def set_name(self, mmsi: str, name) -> None:
        """A Class B Part A carries only the name. It never creates a record
        (a name alone is not worth a row), but it gives an existing one the
        name the reassignment guard compares."""
        rec = self._d.get(mmsi)
        if rec is not None and name:
            rec.name = name
            self._dirty.add(mmsi)

    def lookup(self, mmsi: str, current_name=None) -> StaticRecord | None:
        """The record to join onto a position, or None.

        ⚠ A record read back from disk and not yet re-heard is refused when
        its name and the name broadcast now both exist and differ."""
        rec = self._d.get(mmsi)
        if rec is None:
            return None
        if mmsi not in self._fresh:
            old, new = name_key(rec.name), name_key(current_name)
            if old and new and old != new:
                return None
        return rec

    # -- eviction ----------------------------------------------------------
    def evict(self) -> list[str]:
        """Drop records not heard for the retention window, then the least
        recently heard past the ceiling. Returns the MMSIs dropped."""
        cutoff = self._clock() - self._retention
        dead = [m for m, r in self._d.items() if r.last_seen < cutoff]
        over = len(self._d) - len(dead) - self._max
        if over > 0:
            gone = set(dead)
            alive = sorted((r.last_seen, m) for m, r in self._d.items()
                           if m not in gone)
            dead.extend(m for _ls, m in alive[:over])
        for m in dead:
            del self._d[m]
            self._fresh.discard(m)
            self._dirty.discard(m)
        return dead

    # -- to and from disk ----------------------------------------------------
    def load_rows(self, rows) -> int:
        """Rows as returned by load(). Never overwrites a record already heard
        since this start. Returns how many were taken."""
        taken = 0
        for row in rows:
            mmsi = row[0]
            if mmsi in self._d:
                continue
            *values, first_seen, last_seen = row[1:]
            rec = StaticRecord(int(first_seen), int(last_seen))
            for k, v in zip(PERSISTED, values):
                setattr(rec, k, v)
            self._d[mmsi] = rec
            taken += 1
        return taken

    def take_dirty(self) -> list[tuple]:
        """Rows to upsert, and forget them. Only records with a hull field
        are written."""
        rows = []
        for m in self._dirty:
            rec = self._d.get(m)
            if rec is not None and rec.has_hull():
                rows.append((m, *(getattr(rec, k) for k in PERSISTED),
                             rec.first_seen, rec.last_seen))
        self._dirty.clear()
        return rows

    def mark_dirty(self, mmsis) -> None:
        """Put rows back after a failed write."""
        self._dirty.update(m for m in mmsis if m in self._d)
