"""Moved here from Live Track Commuter Rail (schedule.py, commit df972df) under
backlog item 274: Live Track Intercity Rail is its second consumer.
No Home Assistant imports. Storage, coordinators, entities and config
flows stay in each integration.

Next station and its estimated arrival, from GTFS-Realtime TripUpdates and
the operator's GTFS static timetable. No Home Assistant imports.

Lee 2026-10-02: the map's hover box shows "<next station> in <n> min" under
the train. The integration publishes the PHYSICS - `next_stop` (the station's
name) and `arrival_estimated_at` (when it is expected) - and the card writes
the words (06_MAP_CONTRACT.md D4: integrations emit physics, consumers derive
display). Brightline already publishes the same two names.

What each feed carries, measured 2026-10-02 19:45 (bin\\ltcr_eta_probe.txt,
bin\\ltcr_nictd_static_check.txt):

* Metra: stop_id and an absolute arrival time on the next stop (36 of 46
  trains in service; 40 had a trip update). The name comes from stops.txt.
* NICTD: only stop_sequence and a delay - no stop_id, no time. stop_times.txt
  gives the station and the SCHEDULED time at (trip, sequence); scheduled +
  delay is the estimate. All 3 trains resolved.

One rule set, chosen by what each stop update carries, never by which
operator it is (operators/base.py: a capability is a property of the source).

! GTFS times are in the AGENCY's timezone, whatever a stop's own: South Bend
keeps Eastern time, NICTD's timetable is written in America/Chicago. A time
may also pass 24:00:00 on a trip that runs past midnight.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

# Format of the distilled cache; a different number means rebuild it.
# 2 (2026-10-03): routes (line names) and, with stop_times, trip_routes.
# 3 (2026-10-04, backlog 275): stop_pos (stop coordinates, for `stalled`)
#   and sched (scheduled seconds by trip and stop, for the trips running
#   yesterday, today or tomorrow: delay where a feed sends predicted times
#   and no delay, which is Metra).
STATIC_VERSION = 3

# An update for a stop this far in the past is a stop already served.
PAST_S = 30
# Further ahead than this is not "the next station"; it is a wrong date.
AHEAD_S = 3 * 3600


def _rows(z: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    # Metra's headers AND values carry a space after each comma
    # (05_SHARED_LESSONS.md B5), so both are stripped.
    with z.open(name) as fh:
        return [{(k or "").strip(): (v or "").strip() for k, v in r.items() if k}
                for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))]


def _rows_opt(z: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    # A line name is a nicety: a timetable without the file still gives
    # stations and times.
    return _rows(z, name) if name in z.namelist() else []


def _clean(text: str) -> str:
    # NICTD's routes.txt reads "Monon  Corridor", two spaces.
    return " ".join(text.split())


def line_name(static: dict[str, Any] | None, line: str | None,
              trip_id: str | None) -> str | None:
    """The line's name from the timetable, or None.

    By the operator's line id when it IS a route_id (Metra), else by the
    trip's route (NICTD, whose line ids are ours). Never a guess.
    """
    if not static or static.get("v") != STATIC_VERSION:
        return None
    routes = static.get("routes") or {}
    name = routes.get(line or "")
    if not name and trip_id:
        name = routes.get((static.get("trip_routes") or {}).get(trip_id.strip(), ""))
    return name or None


def distill(raw: bytes, *, stop_times: bool,
            today: date | None = None) -> dict[str, Any]:
    """A GTFS static zip -> the little this module needs, JSON-ready.

    Always: stops (names), stop_pos (coordinates), routes, and sched -
    {trip: {stop_id: seconds}} for the trips running on `today` (the
    agency's date when None) and the day either side. Measured on Metra
    2026-10-04: 977 of 8,504 trips, ~320 KB of JSON against ~4.7 MB
    for every trip (bin\\ltcr275_measure.txt). Refreshed daily, the
    window always holds today.

    stop_times only where the realtime feed cannot name the stop itself:
    Metra's stop_times.txt is ~14 MB and its feed names every stop.
    """
    z = zipfile.ZipFile(io.BytesIO(raw))
    agency = _rows(z, "agency.txt")
    out: dict[str, Any] = {
        "v": STATIC_VERSION,
        "tz": (agency[0].get("agency_timezone") if agency else "") or "UTC",
        "stops": {r["stop_id"]: r.get("stop_name") or r["stop_id"]
                  for r in _rows(z, "stops.txt") if r.get("stop_id")},
        # The line's name, for the map's hover box (Lee 2026-10-03: the line
        # name, not the train number, which is already on the marker).
        "routes": {r["route_id"]: _clean(r.get("route_long_name") or r.get("route_short_name") or "")
                   for r in _rows_opt(z, "routes.txt") if r.get("route_id")},
    }
    # Stop coordinates, for `stalled` (fleet.py): a train standing farther
    # than STALL_M from every stop is not at a platform. Backlog 275.
    pos: dict[str, list[float]] = {}
    for r in _rows(z, "stops.txt"):
        try:
            if r.get("stop_id"):
                pos[r["stop_id"]] = [round(float(r["stop_lat"]), 6),
                                     round(float(r["stop_lon"]), 6)]
        except (KeyError, ValueError):
            continue
    out["stop_pos"] = pos
    out["sched"] = _scheduled(z, out["tz"], today)
    if stop_times:
        # Where the realtime feed names a trip but not its route (NICTD:
        # route_id always empty, trip_id the train number).
        out["trip_routes"] = {r["trip_id"]: r["route_id"]
                              for r in _rows_opt(z, "trips.txt")
                              if r.get("trip_id") and r.get("route_id")}
        st: dict[str, dict[str, list[str]]] = {}
        for r in _rows(z, "stop_times.txt"):
            trip, seq = r.get("trip_id"), r.get("stop_sequence")
            when = r.get("arrival_time") or r.get("departure_time")
            if trip and seq and when:
                st.setdefault(trip, {})[str(int(seq))] = [r.get("stop_id", ""), when]
        out["stop_times"] = st
    return out


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday")


def _active(cal: list[dict[str, str]], dates: list[dict[str, str]], day: date) -> set[str]:
    """service_ids running on `day`: calendar.txt, then calendar_dates.txt
    exceptions (1 adds, 2 removes)."""
    wd, ymd = _WEEKDAYS[day.weekday()], day.strftime("%Y%m%d")
    on = {r["service_id"] for r in cal
          if r.get("service_id") and r.get(wd) == "1"
          and r.get("start_date", "") <= ymd <= r.get("end_date", "")}
    for r in dates:
        if r.get("date") == ymd and r.get("service_id"):
            if r.get("exception_type") == "1":
                on.add(r["service_id"])
            elif r.get("exception_type") == "2":
                on.discard(r["service_id"])
    return on


def _scheduled(z: zipfile.ZipFile, tz_name: str,
               today: date | None) -> dict[str, dict[str, int]]:
    """{trip_id: {stop_id: GTFS seconds}} for trips running the day before,
    on, or after `today`. Every trip when the zip carries no calendar."""
    if "stop_times.txt" not in z.namelist():
        return {}
    if today is None:
        try:
            today = datetime.now(ZoneInfo(tz_name)).date()
        except Exception:  # noqa: BLE001 - an unknown zone name in a timetable
            today = datetime.now(timezone.utc).date()
    cal, dates = _rows_opt(z, "calendar.txt"), _rows_opt(z, "calendar_dates.txt")
    keep: set[str] | None = None
    if cal or dates:
        on: set[str] = set()
        for d in (-1, 0, 1):
            on |= _active(cal, dates, today + timedelta(days=d))
        keep = {r["trip_id"] for r in _rows_opt(z, "trips.txt")
                if r.get("trip_id") and r.get("service_id") in on}
    out: dict[str, dict[str, int]] = {}
    for r in _rows(z, "stop_times.txt"):
        trip, stop = r.get("trip_id"), r.get("stop_id")
        if not trip or not stop or (keep is not None and trip not in keep):
            continue
        secs = _gtfs_seconds(r.get("arrival_time") or r.get("departure_time") or "")
        if secs is not None:
            # A stop visited twice keeps its later time; none on Metra
            # (0 of 8,504 trips, measured 2026-10-04).
            out.setdefault(trip, {})[stop] = secs
    return out


def _gtfs_seconds(hms: str) -> int | None:
    try:
        h, m, s = (int(x) for x in hms.split(":"))
    except ValueError:
        return None
    return h * 3600 + m * 60 + s


def _service_day_epoch(day: date, tz: ZoneInfo, secs: int) -> float:
    # GTFS: the time is measured from noon minus 12 h of the service day,
    # which is local midnight except on the two DST change days.
    noon = datetime.combine(day, dtime(12, 0), tzinfo=tz)
    return (noon - timedelta(hours=12)).timestamp() + secs


def _scheduled_epoch(hms: str, tz: ZoneInfo, now: float,
                     start_date: str | None) -> float | None:
    secs = _gtfs_seconds(hms)
    if secs is None:
        return None
    if start_date and len(start_date) == 8 and start_date.isdigit():
        day = date(int(start_date[:4]), int(start_date[4:6]), int(start_date[6:]))
        return _service_day_epoch(day, tz, secs)
    # No start date: the service day that puts the stop nearest to now.
    today = datetime.fromtimestamp(now, tz).date()
    return min((_service_day_epoch(today + timedelta(days=d), tz, secs)
                for d in (-1, 0, 1)), key=lambda t: abs(t - now))


def _update_time(stu) -> float | None:
    for ev in ("arrival", "departure"):
        if stu.HasField(ev) and getattr(stu, ev).HasField("time"):
            t = int(getattr(stu, ev).time)
            if t > 0:
                return float(t)
    return None


def _update_delay(stu) -> int | None:
    for ev in ("arrival", "departure"):
        if stu.HasField(ev) and getattr(stu, ev).HasField("delay"):
            return int(getattr(stu, ev).delay)
    return None


def _resolve(stu, trip_id: str, start_date: str | None,
             static: dict[str, Any], tz: ZoneInfo, now: float):
    """(station name or None, epoch or None) for one stop update."""
    stops = static.get("stops", {})
    stop_id = stu.stop_id.strip() if stu.HasField("stop_id") else ""
    when = _update_time(stu)
    if when is not None:
        return (stops.get(stop_id) if stop_id else None), when
    # Only a sequence: the timetable says which station and when, the
    # delay says how late (NICTD).
    if not stu.HasField("stop_sequence"):
        return (stops.get(stop_id) if stop_id else None), None
    row = static.get("stop_times", {}).get(trip_id, {}).get(str(stu.stop_sequence))
    if row is None:
        return (stops.get(stop_id) if stop_id else None), None
    name = stops.get(stop_id or row[0])
    delay = _update_delay(stu)
    sched = _scheduled_epoch(row[1], tz, now, start_date)
    if delay is None or sched is None:
        return name, None
    return name, sched + delay


def next_stops(feed, static: dict[str, Any] | None,
               now: float) -> dict[str, tuple[str, float | None]]:
    """{trip_id: (next station's name, estimated arrival epoch or None)}.

    The next station is the first stop update, in sequence order, that is
    not already behind the train (PAST_S). Its time is dropped, the station
    kept, when the update carries none or one implausibly far ahead
    (AHEAD_S): Lee 2026-10-03, "station with no time is fine; time with no
    station is meaningless". A trip with no NAME is absent: no name is
    guessed and no station code is shown in its place.

    ! Inferred, not measured: an update with no time cannot be checked
    against PAST_S, so it is taken to be ahead of the train. GTFS-RT feeds
    list the stops still to come; one that kept a served stop with no time
    would show that stop until it drops off.
    """
    out: dict[str, tuple[str, float | None]] = {}
    if feed is None or not static or static.get("v") != STATIC_VERSION:
        return out
    try:
        tz = ZoneInfo(static.get("tz") or "UTC")
    except Exception:  # noqa: BLE001 - an unknown zone name in a timetable
        return out
    for ent in feed.entity:
        if not ent.HasField("trip_update"):
            continue
        tu = ent.trip_update
        trip_id = (tu.trip.trip_id or "").strip()
        if not trip_id:
            continue
        start = (tu.trip.start_date or "").strip() if tu.trip.HasField("start_date") else None
        updates = sorted(tu.stop_time_update,
                         key=lambda s: s.stop_sequence if s.HasField("stop_sequence") else 0)
        for stu in updates:
            name, when = _resolve(stu, trip_id, start, static, tz, now)
            if when is not None and when < now - PAST_S:
                continue         # already served
            # The next stop. Unnamed (a stop_id the timetable lacks) means
            # nothing is shown, never the stop AFTER it as if it were next.
            if name:
                ok = when is not None and when <= now + AHEAD_S
                out[trip_id] = (name, when if ok else None)
            break
    return out


def _scheduled_at(secs: int, tz: ZoneInfo, anchor: float,
                  start_date: str | None) -> float:
    """The epoch of a GTFS time: on the trip's start_date when the feed
    sends one, else on the service day that puts it nearest `anchor`."""
    if start_date and len(start_date) == 8 and start_date.isdigit():
        day = date(int(start_date[:4]), int(start_date[4:6]), int(start_date[6:]))
        return _service_day_epoch(day, tz, secs)
    today = datetime.fromtimestamp(anchor, tz).date()
    return min((_service_day_epoch(today + timedelta(days=d), tz, secs)
                for d in (-1, 0, 1)), key=lambda t: abs(t - anchor))


def stop_delays(feed, static: dict[str, Any] | None,
                now: float) -> dict[str, int]:
    """{trip_id: seconds late at the next stop}, negative when early.

    Backlog 275 (Lee 2026-10-04): a delay for every train in service. The
    next stop is chosen exactly as next_stops chooses it. Its delay is the
    stop update's own delay field when the feed sends one (NICTD);
    otherwise the predicted time minus the timetable's (Metra: 0 of 7,318
    recorded stop updates carry a delay field, bin\\saw_835.txt). One rule
    set, chosen by what the update carries, never by operator.

    Absent, never 0, when neither is known. Where the feed sends the trip's
    start_date the service day is known, and a delay up to a day either way
    is kept; without it the day is a guess, and a delay beyond AHEAD_S either
    way is taken for a wrong guess and dropped.
    """
    out: dict[str, int] = {}
    if feed is None:
        return out
    usable = bool(static) and static.get("v") == STATIC_VERSION
    tz = None
    if usable:
        try:
            tz = ZoneInfo(static.get("tz") or "UTC")
        except Exception:  # noqa: BLE001 - an unknown zone name in a timetable
            usable = False
    sched = (static.get("sched") or {}) if usable else {}
    for ent in feed.entity:
        if not ent.HasField("trip_update"):
            continue
        tu = ent.trip_update
        trip_id = (tu.trip.trip_id or "").strip()
        if not trip_id:
            continue
        start = (tu.trip.start_date or "").strip() if tu.trip.HasField("start_date") else None
        updates = sorted(tu.stop_time_update,
                         key=lambda s: s.stop_sequence if s.HasField("stop_sequence") else 0)
        for stu in updates:
            if usable:
                _, when = _resolve(stu, trip_id, start, static, tz, now)
            else:
                when = _update_time(stu)
            if when is not None and when < now - PAST_S:
                continue         # already served
            delay = _update_delay(stu)
            if delay is None and when is not None and usable:
                stop_id = stu.stop_id.strip() if stu.HasField("stop_id") else ""
                secs = sched.get(trip_id, {}).get(stop_id)
                if secs is not None:
                    delay = round(when - _scheduled_at(secs, tz, when, start))
            dated = bool(start) and len(start) == 8 and start.isdigit()
            if delay is not None and abs(delay) <= (86400 if dated else AHEAD_S):
                out[trip_id] = int(delay)
            break
    return out


def iso_utc(epoch: float) -> str:
    return datetime.fromtimestamp(round(epoch), tz=timezone.utc).isoformat()
