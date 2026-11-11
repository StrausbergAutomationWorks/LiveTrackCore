"""saw_livetrack.timetable - where a train is by its timetable alone (0.2.34). No Home Assistant imports; stdlib only.

For a railroad whose positions are scarce or absent, the train is drawn from its calls' times: each call's actual,
else expected, else aimed time plus the delay last KNOWN before it. Hoisted VERBATIM from Live Track Intercity Rail
(`sources/entur_et.py` `call_times`/`place`, SJ Nord's rule of 2026-11-08, and `sources/irish_rail.py`
`service_base`) on the second consumer, Live Track Commuter Rail's Irish Rail (its SSOT 13). Intercity keeps its own
copy until its chat switches.

WHERE A TRAIN IS (call_times, place). Each call gets one arrival and one departure: its actual, else its expected, else
its aimed plus the delay last KNOWN before it; a call with no time at all, or cancelled, is left out. Then, at `now`:
* before its first departure, from `pre_departure_s`: standing at its origin;
* between a call's arrival and its departure: standing there;
* after a call's departure: running from that call toward the next, due at the next call's arrival;
* at its last call until `finished_s` after its arrival; then gone (None).

RUN_FLOOR: no stretch is run in less than this share of its scheduled time; an arrival that says so is not believed,
and the call takes its aimed time plus the delay known before it. MEASURED by Intercity on SJ Nord's capture of
2026-11-07/08: actual over scheduled running time, 193 stretches, min 0.33, p5 0.62, p50 0.89.

A call is a mapping with any of the keys in TIMES (epoch seconds or None) and `cancelled`; other keys ride along.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Mapping

# Drawn at its origin from this long before its first departure.
PRE_DEPARTURE_S = 300.0
# Kept at its last call this long after arriving.
FINISHED_S = 300.0
TIMES = ("aimed_arrival", "aimed_departure", "expected_arrival", "expected_departure", "actual_arrival",
         "actual_departure")
RUN_FLOOR = 0.3


def service_base(day: dt.date, tz: dt.tzinfo) -> float:
    """The GTFS service day's zero: noon less 12 h, local (GTFS's own definition; right across a clock change)."""
    return dt.datetime(day.year, day.month, day.day, 12, tzinfo=tz).timestamp() - 12 * 3600


def call_times(calls: Iterable[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], float, float, bool]]:
    """[(call, arrival, departure, known)] for the calls with a time, in order (module docstring). `known`: its
    arrival is the railroad's own word (actual or expected), not the aimed time plus a carried delay. A call with no
    arrival takes its departure, and the reverse; a departure is never before its arrival."""
    out = []
    delay = 0.0
    prev_dep = prev_ad = None
    for c in calls:
        if c.get("cancelled"):
            continue
        aa, ad = c.get("aimed_arrival"), c.get("aimed_departure")
        word_a = c.get("actual_arrival") if c.get("actual_arrival") is not None else c.get("expected_arrival")
        word_d = c.get("actual_departure") if c.get("actual_departure") is not None else c.get("expected_departure")
        if (word_a is not None and aa is not None and prev_dep is not None and prev_ad is not None
                and aa > prev_ad and word_a - prev_dep < RUN_FLOOR * (aa - prev_ad)):
            word_a = None     # faster than any train runs it (RUN_FLOOR)
        arr = word_a if word_a is not None else (aa + delay if aa is not None else None)
        dep = word_d if word_d is not None else (ad + delay if ad is not None else None)
        # What this call says about the delay, for the calls after it that say nothing.
        if word_d is not None and ad is not None:
            delay = word_d - ad
        elif word_a is not None and aa is not None:
            delay = word_a - aa
        if arr is None and dep is None:
            continue
        arr = dep if arr is None else arr
        dep = arr if dep is None else max(dep, arr)
        out.append((c, arr, dep, word_a is not None))
        prev_dep, prev_ad = dep, (ad if ad is not None else aa)
    return out


def place(times: list, now: float, pre_departure_s: float = PRE_DEPARTURE_S,
          finished_s: float = FINISHED_S) -> tuple[str, int] | None:
    """Where the train is: ("standing", i) at times[i], ("running", i) from times[i] toward times[i + 1]; None when
    it is not out (before pre_departure_s of its first departure, or finished_s after its last arrival)."""
    if not times:
        return None
    if now < times[0][2] - pre_departure_s or now > times[-1][1] + finished_s:
        return None
    # The last call it has reached by its times (the origin counts from before it leaves).
    i = 0
    for k in range(1, len(times)):
        if times[k][1] <= now:
            i = k
    if i == len(times) - 1 or now < times[i][2]:
        return ("standing", i)
    return ("running", i)
