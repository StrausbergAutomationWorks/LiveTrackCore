"""GTFS-Realtime: one vehicle as an adapter understood it, the two field
readers every adapter shares, and the feed decode.

Moved here from Live Track Commuter Rail (operators/base.py and coordinator.py, commit df972df) under
backlog item 274: Live Track Intercity Rail is its second consumer.
No Home Assistant imports. Storage, coordinators, entities and config
flows stay in each integration.

! A CAPABILITY IS A PROPERTY OF THE SOURCE (05_SHARED_LESSONS.md B11).
Each integration keeps one adapter per feed, stating only what that feed
really carries, and builds Obs records with these helpers. The adapters
are not here: they are per-railroad knowledge, not shared code.

The protobuf bindings are an EXTRA, `saw-livetrack[gtfs]`, imported only
by decode(); the readers here take any object shaped like a
VehiclePosition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Obs:
    """One vehicle in one poll, as its adapter understood it."""

    vehicle_id: str
    lat: float
    lon: float
    # When this position was true: the vehicle's own GTFS-RT timestamp,
    # timezone-aware. None when the vehicle carried none - never the feed
    # header time, which says when the FEED was built (D2).
    observed_at: datetime | None
    in_service: bool
    # The train number a rider would recognise, when in service.
    train: str | None = None
    # The operator's own line id, e.g. "UP-W", or None.
    line: str | None = None
    # A course the SOURCE reports and that is trustworthy WHILE MOVING.
    # None where the source has no usable course. Used only when the vehicle
    # has just moved; at rest it is ignored (Metra reports 359.0 there).
    reported_course: float | None = None
    # The GTFS-RT trip_id while in service: what a TripUpdate is keyed by.
    # Never an identity (NICTD's is the bare train number, and repeats).
    trip_id: str | None = None


def vehicle_time(v) -> datetime | None:
    """A GTFS-RT VehiclePosition's own timestamp, or None.

    ! HasField, never truthiness: proto2 returns a default for an unset field
    and that default is indistinguishable from a value (06 D3b-i; backlog 197
    found the same trap in current_status).
    """
    if v.HasField("timestamp") and int(v.timestamp) > 0:
        return datetime.fromtimestamp(int(v.timestamp), tz=timezone.utc)
    return None


def position_of(v) -> tuple[float, float] | None:
    """(lat, lon) to 6 decimals, or None when unset or the null island.

    GTFS-RT positions are float32. Promoted to Python floats they carry noise
    digits (41.860511779785156, seen on the box 2026-10-02 in previous_*). Six
    decimals is ~0.1 m, below anything a feed resolves; South Shore rounded
    the same way.
    """
    if not v.HasField("position"):
        return None
    lat = round(float(v.position.latitude), 6)
    lon = round(float(v.position.longitude), 6)
    if lat == 0.0 and lon == 0.0:
        return None
    return lat, lon


def decode(raw: bytes):
    """Bytes -> a GTFS-RT FeedMessage, or None for a payload that will not
    parse, whatever its shape. Raises ImportError when the `gtfs` extra is
    not installed: a missing dependency is not a bad payload.
    """
    from google.transit import gtfs_realtime_pb2

    try:
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(raw)
    except Exception:  # noqa: BLE001 - a bad payload, whatever its shape
        return None
    return feed
