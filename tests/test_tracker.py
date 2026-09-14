"""Tests for FixTracker.

The aliasing case is why this file exists. South Shore emitted previous_* by
the book and still stuttered, and every individual update read as correct.
"""
import datetime as _dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import saw_amtraker as sa  # noqa: E402
from saw_amtraker.tracker import FixTracker, bearing_deg  # noqa: E402

CHECKS = []
def check(name, cond): CHECKS.append((name, bool(cond)))

BASE = _dt.datetime(2026, 9, 13, 15, 0, 0, tzinfo=_dt.timezone.utc)

def rec(lat, lon, secs, provider="Amtrak"):
    return {"provider": provider, "trainState": "Active", "lat": lat, "lon": lon,
            "lastValTS": (BASE + _dt.timedelta(seconds=secs)).isoformat()}

# --- bearing ---------------------------------------------------------------
check("bearing north", abs(bearing_deg(25.0, -80.0, 26.0, -80.0)) < 0.5)
check("bearing east", abs(bearing_deg(25.0, -80.0, 25.0, -79.0) - 90) < 1.0)
check("bearing south", abs(bearing_deg(26.0, -80.0, 25.0, -80.0) - 180) < 0.5)
check("bearing west", abs(bearing_deg(25.0, -79.0, 25.0, -80.0) - 270) < 1.0)
check("bearing is 0-359.9", 0 <= bearing_deg(25.0, -80.0, 24.0, -81.0) < 360)

# --- first sighting: no previous_* -----------------------------------------
t = FixTracker()
t.update("a", rec(25.0, -80.0, 0))
f = t.segment_fields("a")
check("first sighting has observed_at", "observed_at" in f)
check("first sighting omits previous_latitude", "previous_latitude" not in f)
check("first sighting omits previous_longitude", "previous_longitude" not in f)
check("first sighting omits course_deg", "course_deg" not in f)

# --- a real move gives the full segment ------------------------------------
t.update("a", rec(25.0180, -80.0, 60))
f = t.segment_fields("a")
check("segment has previous_latitude", f.get("previous_latitude") == 25.0)
check("segment has previous_observed_at", "previous_observed_at" in f)
check("segment has course_deg", "course_deg" in f)
check("course is northbound", abs(f["course_deg"]) < 1 or abs(f["course_deg"] - 360) < 1)
check("observed_at advanced", f["observed_at"] != f["previous_observed_at"])

# === THE ALIASING CASE. D3c-iii. ==========================================
# Brightline: the feed ticks every 30 s, the position moves every 60 s, so
# EXACTLY every other poll is a duplicate. Feed the real pattern.
t = FixTracker()
seq = [(25.0000, 0), (25.0000, 30), (25.0180, 60), (25.0180, 90),
       (25.0360, 120), (25.0360, 150), (25.0540, 180), (25.0540, 210)]
segs = []
for lat, s in seq:
    t.update("b", rec(lat, -80.0, s))
    segs.append(t.segment_fields("b"))

after_dup = [segs[i] for i in (3, 5, 7)]
check("ALIASING: duplicate polls never destroy the segment",
      all("previous_latitude" in s for s in after_dup))
check("ALIASING: duplicate polls never drop course_deg",
      all("course_deg" in s for s in after_dup))
check("ALIASING: segment is unchanged across a duplicate",
      segs[2]["previous_latitude"] == segs[3]["previous_latitude"]
      and segs[4]["previous_latitude"] == segs[5]["previous_latitude"])
check("ALIASING: duplicates were counted, not acted on", t.duplicates == 4)
check("ALIASING: segment length never collapses to zero",
      all(s["previous_latitude"] != s.get("_lat_now", None) for s in after_dup))
prevs = [s["previous_latitude"] for s in after_dup]
check("ALIASING: the segment advances across real moves",
      len(set(prevs)) == 3)

# --- stationary train: course_deg must be ABSENT, and that is correct ------
t = FixTracker()
t.update("c", rec(25.0, -80.0, 0))
t.update("c", rec(25.00005, -80.00005, 60))   # ~7 m of GPS jitter
f = t.segment_fields("c")
check("jitter does not advance the segment", "previous_latitude" not in f)
check("stationary train has NO course_deg (D3c-iii: correct, not a bug)",
      "course_deg" not in f)

# a train that moved, then stopped: the segment stays, course goes
t = FixTracker()
t.update("d", rec(25.0, -80.0, 0))
t.update("d", rec(25.0180, -80.0, 60))
t.update("d", rec(25.01801, -80.0, 120))      # ~1 m
f = t.segment_fields("d")
check("a stopped train keeps its last real segment", "previous_latitude" in f)
check("a stopped train keeps course from that segment", "course_deg" in f)

# --- terminal snap: the old fix is NOT on the new segment -----------------
t = FixTracker()
t.update("e", rec(28.4151, -81.3083, 0))      # Orlando airport
t.update("e", rec(25.7800, -80.1956, 30))     # Miami, 313 km in 30 s
f = t.segment_fields("e")
check("terminal snap detected", t.snaps == 1)
check("snap drops the stale previous fix", "previous_latitude" not in f)
check("snap does not invent a course across 313 km", "course_deg" not in f)

# --- a 125 mph train over 180 s is NOT a snap -----------------------------
t = FixTracker()
t.update("f", rec(25.0000, -80.0, 0))
t.update("f", rec(25.0900, -80.0, 180))       # ~10 km in 180 s
check("10 km in 180 s is real movement, not a snap", t.snaps == 0)
check("...and it produces a segment", "previous_latitude" in t.segment_fields("f"))

# --- batch-stamped provider: observed_at must be ABSENT -------------------
t = FixTracker()
t.update("g", {"provider": "Brightline", "trainState": "Active",
               "lat": 25.0, "lon": -80.0, "lastValTS": "2026-09-13T15:00:00.000Z"})
t.update("g", {"provider": "Brightline", "trainState": "Active",
               "lat": 25.018, "lon": -80.0, "lastValTS": "2026-09-13T15:01:00.000Z"})
f = t.segment_fields("g")
check("Brightline omits observed_at (feed job clock, B8.3)",
      "observed_at" not in f)
check("Brightline omits previous_observed_at", "previous_observed_at" not in f)
check("Brightline still supplies the position segment",
      "previous_latitude" in f and "previous_longitude" in f)
check("Brightline still supplies course_deg", "course_deg" in f)

# --- D0: no key is ever present-but-empty --------------------------------
t = FixTracker()
t.update("h", rec(25.0, -80.0, 0))
t.update("h", rec(25.018, -80.0, 60))
f = t.segment_fields("h")
check("D0: no value is None", all(v is not None for v in f.values()))
check("D0: no value is an empty string", all(v != "" for v in f.values()))
check("bad coordinates are ignored", not FixTracker().update("z",
      {"provider": "Amtrak", "lat": None, "lon": 1.0, "lastValTS": None}))
check("forget clears an object", (lambda: (t.forget("h"),
      t.segment_fields("h") == {}))()[1])

failed = [n for n, ok in CHECKS if not ok]
print("checks: %d   passed: %d   failed: %d"
      % (len(CHECKS), len(CHECKS) - len(failed), len(failed)))
for n in failed:
    print("  FAIL: %s" % n)
sys.exit(1 if failed else 0)
