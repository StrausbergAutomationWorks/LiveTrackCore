"""Cut a GTFS static zip down to the routes of some route_types, before it is distilled.
No Home Assistant imports.

SE Queensland's timetable is ONE zip for every mode (SSOT section 9): measured
2026-10-15 on the copy of 2026-10-14 (bin\\seq_build_measure.txt), 35,966,145 bytes,
1,093 routes of which 511 are rail (route_type 2), 3,717,795 stop_times rows of which
236,615 are rail's, 13,118 stops of which rail uses 357. Distilled whole it would load
every bus stop time into memory once a day; cut to rail it is a zip of under 5 MB, and
the shapes it gives the off-rail check are rail's alone.

stop_times.txt is read ONCE, by column index (a first version read it twice through
csv.DictReader and took 24.3 s on the laptop; bin\\ltcr_seq_replay.txt). Values are
matched stripped (05_SHARED_LESSONS.md B5, Metra's spaces); rows are written back as
they were read. Files tied to no route (agency, calendars, feed_info, anything else)
are copied whole.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Iterable


def _reader(z: zipfile.ZipFile, name: str):
    """(raw header, stripped header, row iterator, open file), or None when the file
    is absent or empty."""
    if name not in z.namelist():
        return None
    fh = z.open(name)
    rd = csv.reader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline=""))
    try:
        header = next(rd)
    except StopIteration:
        fh.close()
        return None
    return header, [h.strip() for h in header], rd, fh


def _column(z: zipfile.ZipFile, name: str, col: str, where: str | None = None,
            allowed: set[str] | None = None) -> set[str]:
    """Values of `col` (stripped), from rows whose `where` is in `allowed` (every row
    when `where` is None)."""
    r = _reader(z, name)
    if r is None:
        return set()
    _, keys, rd, fh = r
    with fh:
        if col not in keys or (where is not None and where not in keys):
            return set()
        ic = keys.index(col)
        iw = keys.index(where) if where is not None else None
        out: set[str] = set()
        for row in rd:
            if len(row) <= ic:
                continue
            if iw is not None and (len(row) <= iw or row[iw].strip() not in allowed):
                continue
            v = row[ic].strip()
            if v:
                out.add(v)
        return out


def _filter(z: zipfile.ZipFile, out: zipfile.ZipFile, name: str, col: str,
            allowed: set[str], collect: str | None = None) -> set[str]:
    """Copy `name` keeping the rows whose `col` is in `allowed`; returns the stripped
    values of `collect` in the kept rows (empty when None)."""
    got: set[str] = set()
    r = _reader(z, name)
    if r is None:
        out.writestr(name, z.read(name) if name in z.namelist() else b"")
        return got
    header, keys, rd, fh = r
    with fh:
        if col not in keys:
            out.writestr(name, z.read(name))
            return got
        i = keys.index(col)
        j = keys.index(collect) if collect in keys else None
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(header)
        for row in rd:
            if len(row) > i and row[i].strip() in allowed:
                w.writerow(row)
                if j is not None and len(row) > j:
                    got.add(row[j].strip())
    out.writestr(name, buf.getvalue())
    return got


def keep_route_types(raw: bytes, route_types: Iterable[str]) -> bytes:
    """The zip with only the routes whose route_type is in `route_types`, their trips,
    stop times, stops (with their parent stations) and shapes. Blocking."""
    types = {str(t).strip() for t in route_types}
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = z.namelist()
    routes = _column(z, "routes.txt", "route_id", "route_type", types)
    trips = _column(z, "trips.txt", "trip_id", "route_id", routes)
    shapes = _column(z, "trips.txt", "shape_id", "route_id", routes)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        # stop_times first: the stops it uses decide which stops are kept.
        used = (_filter(z, out, "stop_times.txt", "trip_id", trips, collect="stop_id")
                if "stop_times.txt" in names else set())
        parents = _column(z, "stops.txt", "parent_station", "stop_id", used)
        for name in names:
            if name == "stop_times.txt":
                continue
            if name == "routes.txt":
                _filter(z, out, name, "route_id", routes)
            elif name == "trips.txt":
                _filter(z, out, name, "trip_id", trips)
            elif name == "stops.txt":
                _filter(z, out, name, "stop_id", used | parents)
            elif name == "shapes.txt":
                _filter(z, out, name, "shape_id", shapes)
            else:
                out.writestr(name, z.read(name))
    return buf.getvalue()


# --- Renfe: the service window (SSOT 10) -------------------------------------------
# Renfe's timetable is ONE zip for every network and every day of the season: measured on
# the copy of 2026-10-16 (bin\\es_renfe_distill_cost.txt), 15,883,747 bytes, stop_times.txt
# 277 MB unzipped, 1,831,932 rows, 133,481 trips; each service_id runs on ONE date. Cut by
# route_type alone and distilled, it took 66 s and peaked at 930 MB of Python objects on
# the laptop. The trips running the day before, on and after today are about 10,000
# (5,076 on Monday 2026-10-19): cut to them first, and stop_times is read once, line by
# line, keeping about one row in thirteen.

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _rows(z: zipfile.ZipFile, name: str):
    """Stripped dict rows of a small file; nothing when absent."""
    r = _reader(z, name)
    if r is None:
        return
    _, keys, rd, fh = r
    with fh:
        for row in rd:
            yield {k: (row[i].strip() if i < len(row) else "") for i, k in enumerate(keys)}


def _agency_today(z: zipfile.ZipFile) -> date:
    tz = ""
    for r in _rows(z, "agency.txt"):
        tz = r.get("agency_timezone", "")
        break
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz or "UTC")).date()
    except Exception:  # noqa: BLE001 - an unknown zone name in a timetable
        return datetime.now(timezone.utc).date()


def services_on(z: zipfile.ZipFile, days: Iterable[date]) -> set[str] | None:
    """service_ids running on any of `days`, or None when the zip has neither calendar
    file (every trip runs). Each day is worked out on its own: calendar.txt, then THAT
    day's calendar_dates.txt rows (1 adds, 2 removes); the days are then joined. Until
    0.2.26 one set ran across the days, so a removal dated one day also dropped a service
    the calendar ran on the others (measured 2026-11-06 on Renfe's long-distance
    timetable: train 15001 and others, which ran the next day). saw_livetrack.renfe's
    TripIndex keeps its own copy of this rule."""
    cal = list(_rows(z, "calendar.txt"))
    dates = list(_rows(z, "calendar_dates.txt"))
    if not cal and not dates:
        return None
    by_date: dict[str, list[dict[str, str]]] = {}
    for r in dates:
        if r.get("date") and r.get("service_id"):
            by_date.setdefault(r["date"], []).append(r)
    on: set[str] = set()
    for day in days:
        wd, ymd = _WEEKDAYS[day.weekday()], day.strftime("%Y%m%d")
        that_day = {r["service_id"] for r in cal if r.get("service_id") and r.get(wd) == "1"
                    and r.get("start_date", "") <= ymd <= r.get("end_date", "")}
        for r in by_date.get(ymd, ()):
            if r.get("exception_type") == "1":
                that_day.add(r["service_id"])
            elif r.get("exception_type") == "2":
                that_day.discard(r["service_id"])
        on |= that_day
    return on


def keep_service_window(raw: bytes, route_types: Iterable[str] | None = None,
                        today: date | None = None,
                        route_prefixes: Iterable[str] | None = None) -> bytes:
    """The zip with only the trips running the day before, on, or after `today` (the
    agency's date when None), on routes of `route_types` (every route when None) whose
    route_id starts with one of `route_prefixes` (every route when None; Renfe's network
    code); their stop times, stops (with parent stations) and shapes. routes.txt keeps
    every such route (an operator's colours by line need them), calendars and the rest
    are copied whole. Blocking."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = z.namelist()
    if today is None:
        today = _agency_today(z)
    on = services_on(z, [today + timedelta(days=d) for d in (-1, 0, 1)])
    if route_types is None:
        routes = _column(z, "routes.txt", "route_id")
    else:
        types = {str(t).strip() for t in route_types}
        routes = _column(z, "routes.txt", "route_id", "route_type", types)
    if route_prefixes is not None:
        prefixes = tuple(str(p).strip() for p in route_prefixes)
        routes = {r for r in routes if r.startswith(prefixes)}
    trips: set[str] = set()
    shapes: set[str] = set()
    for r in _rows(z, "trips.txt"):
        if r.get("route_id") in routes and (on is None or r.get("service_id") in on):
            if r.get("trip_id"):
                trips.add(r["trip_id"])
            if r.get("shape_id"):
                shapes.add(r["shape_id"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        used = _stop_times_for(z, out, trips) if "stop_times.txt" in names else set()
        parents = _column(z, "stops.txt", "parent_station", "stop_id", used)
        for name in names:
            if name == "stop_times.txt":
                continue
            if name == "routes.txt":
                _filter(z, out, name, "route_id", routes)
            elif name == "trips.txt":
                _filter(z, out, name, "trip_id", trips)
            elif name == "stops.txt":
                _filter(z, out, name, "stop_id", used | parents)
            elif name == "shapes.txt":
                _filter(z, out, name, "shape_id", shapes)
            else:
                out.writestr(name, z.read(name))
    return buf.getvalue()


def _stop_times_for(z: zipfile.ZipFile, out: zipfile.ZipFile, trips: set[str]) -> set[str]:
    """Copy stop_times.txt keeping the rows of `trips`, reading it once line by line:
    a line whose first character is not a quote is split on commas, others through csv.
    Returns the stop_ids of the kept rows."""
    used: set[str] = set()
    with z.open("stop_times.txt") as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")
        header = text.readline()
        keys = [k.strip() for k in next(csv.reader([header]))]
        if "trip_id" not in keys:
            out.writestr("stop_times.txt", header + text.read())
            return used
        it, js = keys.index("trip_id"), (keys.index("stop_id") if "stop_id" in keys else None)
        kept = [header if header.endswith("\n") else header + "\n"]
        for line in text:
            if it == 0 and '"' not in line:
                # The trip_id alone, for the 12 rows in 13 that are dropped.
                k = line.find(",")
                if (line[:k] if k >= 0 else line).strip() not in trips:
                    continue
            elif not line.strip():
                continue
            cells = (next(csv.reader([line])) if '"' in line else line.rstrip("\r\n").split(","))
            if len(cells) <= it or cells[it].strip() not in trips:
                continue
            kept.append(line if line.endswith("\n") else line + "\n")
            if js is not None and len(cells) > js:
                used.add(cells[js].strip())
    out.writestr("stop_times.txt", "".join(kept))
    return used
