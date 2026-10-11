"""Time zones anywhere on Earth, from a coordinate (0.2.5).

Lee, 2026-10-10 15:34: one time-zone resource for every integration,
worldwide. DECIDED 2026-10-11 00:02 ("Use A"): tzfpy, measured in
35_Dependencies/third_party/tzfpy/EVAL_time_zone_options_20261010.md -
any coordinate, wheels for every Home Assistant platform (musllinux x86_64
and aarch64), no dependencies, about a million lookups a second, at most
~111 m off a zone border. A feed's own stated zone is NOT trusted: Amtrak's
GTFS puts all of Arizona on Denver time and the Mississippi Gulf Coast on New
York time (20 of its 647 stops); a coordinate lookup puts them right.

No Home Assistant imports. NOT imported by `saw_livetrack`: `import
saw_livetrack.zones` and install the `zones` extra (tzfpy, and tzdata so
zoneinfo works where the OS has no IANA database - Windows, slim
containers). Without tzfpy, zone_at() raises ImportError: a missing
dependency is not an unknown place.

Data: Timezone Boundary Builder (github.com/evansiroky/timezone-boundary-builder),
embedded in tzfpy, licensed under the Open Database License (ODbL) v1.0;
DATA_VERSION() names the release. tzfpy's own code is MIT.
"""

from __future__ import annotations

import math
from datetime import datetime
from functools import lru_cache

ATTRIBUTION = ("Time zones: Timezone Boundary Builder (evansiroky/timezone-boundary-builder), "
               "ODbL v1.0, via tzfpy")


def _coord(lat, lon):
    """(lat, lon) as finite floats inside the globe, else None. bool is not
    a number here, though Python says it is an int."""
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(la) and math.isfinite(lo)) or not (-90.0 <= la <= 90.0) or not (-180.0 <= lo <= 180.0):
        return None
    return la, lo


def zone_at(lat, lon) -> str | None:
    """The IANA zone name at a coordinate (`"America/Chicago"`), or None for a
    coordinate that is not one. Open sea answers a nautical zone
    (`"Etc/GMT+5"`), which is a real zone. Raises ImportError when the
    `zones` extra is not installed.
    """
    from tzfpy import get_tz  # noqa: PLC0415 - optional dependency

    c = _coord(lat, lon)
    if c is None:
        return None
    name = get_tz(c[1], c[0])  # tzfpy takes LONGITUDE first
    return name or None


@lru_cache(maxsize=512)
def _zoneinfo(name: str):
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    return ZoneInfo(name)


def local_time(when: datetime, lat, lon) -> datetime | None:
    """`when` (timezone-aware) on the local clock at a coordinate, or None for
    a coordinate that is not one or a zone zoneinfo cannot load (no IANA
    database: install the extra's tzdata). A naive `when` is refused with
    ValueError: its zone would be a guess.
    """
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("local_time needs a timezone-aware datetime")
    name = zone_at(lat, lon)
    if name is None:
        return None
    try:
        tz = _zoneinfo(name)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError and friends
        return None
    return when.astimezone(tz)


def utc_offset_min(when: datetime, lat, lon) -> int | None:
    """The local clock's offset from UTC at `when`, in whole minutes
    (Chicago in July: -300), or None as local_time()."""
    t = local_time(when, lat, lon)
    if t is None:
        return None
    return int(t.utcoffset().total_seconds() // 60)


def DATA_VERSION() -> str | None:  # noqa: N802 - reads like the constant it reports
    """The Timezone Boundary Builder release inside the installed tzfpy
    (`"2026d"`), or None if tzfpy cannot say."""
    import tzfpy  # noqa: PLC0415

    f = getattr(tzfpy, "data_version", None)
    return f() if callable(f) else None


__all__ = ["ATTRIBUTION", "DATA_VERSION", "local_time", "utc_offset_min", "zone_at"]
