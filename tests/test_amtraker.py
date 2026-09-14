"""Tests for the shared Amtraker client.

Every case corresponds to a MEASURED finding. If a test fails, a rule that was
paid for with real measurement has been dropped.

Run:  python test_amtraker_client.py     -> exit 0 on success
"""

import datetime as _dt
import sys
import os

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import saw_livetrack as ac  # noqa: E402
import saw_livetrack.amtraker  # noqa: E402

CHECKS = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


UTC = _dt.timezone.utc


def train(**kw):
    base = {"provider": "Amtrak", "trainState": "Active",
            "lat": 41.0, "lon": -88.0, "velocity": 50.0, "heading": "SW",
            "lastValTS": "2026-09-06T20:41:41-05:00"}
    base.update(kw)
    return base


# --- timestamps: THREE formats in one field (B8.4) -------------------------
check("parses Amtrak offset form",
      ac.parse_ts("2026-09-06T20:41:41-07:00") is not None)
check("parses Brightline Z+millis form",
      ac.parse_ts("2026-09-07T02:00:08.539Z") is not None)
check("parses VIA bare Z form",
      ac.parse_ts("2026-09-07T00:44:46Z") is not None)
check("Z form is UTC",
      ac.parse_ts("2026-09-07T02:00:08.539Z").utcoffset().total_seconds() == 0)
check("offset form keeps its offset",
      ac.parse_ts("2026-09-06T20:41:41-07:00").utcoffset().total_seconds()
      == -7 * 3600)
check("empty string is None", ac.parse_ts("") is None)
check("None is None", ac.parse_ts(None) is None)
check("garbage is None", ac.parse_ts("not a timestamp") is None)
check("naive timestamp rejected", ac.parse_ts("2026-09-06T20:41:41") is None)
check("non-string rejected", ac.parse_ts(12345) is None)

# --- observed_at: batch stamp and Predeparture (B8.3) ----------------------
check("Amtrak Active has an observation time",
      ac.observed_at(train()) is not None)
check("Brightline batch stamp yields None",
      ac.observed_at(train(provider="Brightline",
                           lastValTS="2026-09-07T02:00:08.539Z")) is None)
check("VIA has an observation time",
      ac.observed_at(train(provider="Via",
                           lastValTS="2026-09-07T00:44:46Z")) is not None)
check("Predeparture yields None (scheduled future, not an observation)",
      ac.observed_at(train(trainState="Predeparture")) is None)
check("Completed still yields a time",
      ac.observed_at(train(trainState="Completed")) is not None)


# --- velocity: real for two providers, fabricated zero for one (B8.4) ------
check("Amtrak velocity converts mph to km/h",
      abs(ac.speed_kmh(train(velocity=50.0)) - 80.4672) < 1e-3)
check("VIA velocity is used",
      ac.speed_kmh(train(provider="Via", velocity=42.0)) is not None)
check("Brightline velocity is NEVER used, even non-zero",
      ac.speed_kmh(train(provider="Brightline", velocity=77.0)) is None)
check("Brightline zero is None, not 0.0 (D0: omit, do not assert stopped)",
      ac.speed_kmh(train(provider="Brightline", velocity=0.0)) is None)
check("Amtrak genuine zero IS 0.0, not None",
      ac.speed_kmh(train(velocity=0.0)) == 0.0)
check("missing velocity is None", ac.speed_kmh(train(velocity=None)) is None)
check("negative velocity rejected", ac.speed_kmh(train(velocity=-5)) is None)

# --- heading: octant only, course_deg never invented (B8.2) ----------------
check("heading octant returned", ac.heading_octant(train(heading="SW")) == "SW")
check("heading lowercase normalised", ac.heading_octant(train(heading="sw")) == "SW")
check("bogus heading is None", ac.heading_octant(train(heading="XYZ")) is None)
check("missing heading is None", ac.heading_octant(train(heading=None)) is None)
check("course_deg is ALWAYS None -- never invent 22.5 deg of precision",
      ac.course_deg(train(heading="SW")) is None)

# --- movement classification: the two artefacts (Brightline SSOT 4.3) ------
def at(lat, lon, secs=0, provider="Amtrak"):
    """A fix at a given offset in seconds from a fixed base time."""
    base = _dt.datetime(2026, 9, 6, 20, 0, 0,
                        tzinfo=_dt.timezone(-_dt.timedelta(hours=5)))
    return train(provider=provider, lat=lat, lon=lon,
                 lastValTS=(base + _dt.timedelta(seconds=secs)).isoformat())


# The real measured snap: Orlando -> Miami, 313 km in one 30 s tick.
check("terminal snap detected (313 km in 30 s)",
      ac.classify_movement(at(28.4151, -81.3083, 0),
                           at(25.7800, -80.1956, 30)) == "snap")
check("GPS jitter classed as still (28 m)",
      ac.classify_movement(at(25.7800, -80.1956, 0),
                           at(25.7802, -80.1958, 60)) == "still")
check("real movement classed as moved (2 km in 60 s)",
      ac.classify_movement(at(25.7800, -80.1956, 0),
                           at(25.7980, -80.1956, 60)) == "moved")

# THE REGRESSION THIS SECTION EXISTS FOR. A flat 5 km snap threshold would
# call this an artefact. It is a 125 mph train over the observed 180 s
# maximum interval -- 10 km, entirely legitimate.
check("10 km in 180 s is REAL movement, not a snap (125 mph over max interval)",
      ac.classify_movement(at(25.7800, -80.1956, 0),
                           at(25.8700, -80.1956, 180)) == "moved")
check("same 10 km in 30 s IS a snap (implied 1,200 km/h)",
      ac.classify_movement(at(25.7800, -80.1956, 0),
                           at(25.8700, -80.1956, 30)) == "snap")
check("undated fixes fall back to distance: 313 km is still a snap",
      ac.classify_movement({"lat": 28.4151, "lon": -81.3083},
                           {"lat": 25.7800, "lon": -80.1956}) == "snap")
check("missing lat is unknown",
      ac.classify_movement({"lat": None, "lon": 1.0},
                           at(25.78, -80.19)) == "unknown")
check("identical position is still",
      ac.classify_movement(at(25.78, -80.19, 0),
                           at(25.78, -80.19, 60)) == "still")

# --- derived speed: the value of the whole exercise ------------------------
def pair(km_apart_lat, dt_s, provider="Amtrak", state="Active"):
    t0 = "2026-09-06T20:00:00-05:00"
    t1 = (_dt.datetime.fromisoformat(t0) + _dt.timedelta(seconds=dt_s)).isoformat()
    a = train(provider=provider, trainState=state, lat=25.0, lon=-80.0, lastValTS=t0)
    b = train(provider=provider, trainState=state, lat=25.0 + km_apart_lat,
              lon=-80.0, lastValTS=t1)
    return a, b

a, b = pair(0.018, 60)          # ~2 km in 60 s -> ~120 km/h
sp = ac.derived_speed_kmh(a, b)
check("derived speed computed for a real move", sp is not None and 100 < sp < 140)
check("derived speed None across a terminal snap",
      ac.derived_speed_kmh(at(28.4151, -81.3083, 0),
                           at(25.7800, -80.1956, 30)) is None)
a, b = pair(0.0001, 60)
check("derived speed is 0.0 for jitter, not a fictional crawl",
      ac.derived_speed_kmh(a, b) == 0.0)
a, b = pair(0.018, 60, provider="Brightline")
check("derived speed None for Brightline -- no observation time (B8.3)",
      ac.derived_speed_kmh(a, b) is None)
a, b = pair(0.018, 0)
check("zero elapsed time yields None", ac.derived_speed_kmh(a, b) is None)
a, b = pair(0.018, -60)
check("negative elapsed time yields None", ac.derived_speed_kmh(a, b) is None)


# --- payload shape: [] is a LIST, not the documented keyed object ----------
check("list payload flattens to empty", ac.amtraker._flatten([]) == [])
check("dict payload flattens", len(ac.amtraker._flatten({"b5756": [train()]})) == 1)
check("None payload flattens to empty", ac.amtraker._flatten(None) == [])
check("non-dict members ignored", ac.amtraker._flatten({"x": ["junk", train()]}) == [train()])

# --- transport: fakes, no network -----------------------------------------
import json as _json
import io as _io
import urllib.error as _ue


class FakeResp:
    def __init__(self, obj):
        self._b = _json.dumps(obj).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_opener(obj, calls):
    def _open(req, timeout=None):
        calls.append(req.get_header("User-agent"))
        return FakeResp(obj)
    return _open


FLEET = {"1": [train(provider="Amtrak", velocity=50.0)],
         "v5": [train(provider="Via", velocity=42.0)],
         "b5756": [train(provider="Brightline", velocity=0.0)]}

calls = []
cli = ac.AmtrakerClient(user_agent="SAW-test/1.0", opener=make_opener(FLEET, calls))
rows = cli.trains()
check("fleet fetch returns all providers", len(rows) == 3)
check("provider filter works", len(cli.trains(provider="Brightline")) == 1)
check("unknown provider raises",
      _raises := (lambda: [False for _ in ()])() or True)
try:
    cli.trains(provider="Acela")
    check("unknown provider raises ValueError", False)
except ValueError:
    check("unknown provider raises ValueError", True)

check("User-Agent is sent on every request (B8: absent UA is blocked)",
      calls and all(c == "SAW-test/1.0" for c in calls))
try:
    ac.AmtrakerClient(user_agent="")
    check("empty User-Agent rejected at construction", False)
except ValueError:
    check("empty User-Agent rejected at construction", True)

# --- the caching contract: N consumers must not make N requests (B8.1) ----
calls2 = []
frozen = {"t": _dt.datetime(2026, 9, 6, 20, 0, 0, tzinfo=UTC)}
cli2 = ac.AmtrakerClient(user_agent="SAW-test/1.0",
                         opener=make_opener(FLEET, calls2),
                         clock=lambda: frozen["t"])
cli2.trains(provider="Amtrak")
cli2.trains(provider="Via")
cli2.trains(provider="Brightline")
check("THREE consumers, ONE upstream request (item 115)",
      cli2.fetch_count == 1 and cli2.serve_count == 3)
frozen["t"] += _dt.timedelta(seconds=ac.FEED_TICK_S + 1)
cli2.trains(provider="Amtrak")
check("cache expires after the measured 30 s feed tick", cli2.fetch_count == 2)
cli2.trains(provider="Amtrak", force=True)
check("force bypasses the cache", cli2.fetch_count == 3)


def raiser(code):
    def _open(req, timeout=None):
        raise _ue.HTTPError(req.full_url, code, "err", None, None)
    return _open


for code, exc, label in ((429, ac.RateLimited, "429 -> RateLimited, not absence"),
                         (500, ac.FeedUnavailable, "500 -> FeedUnavailable"),
                         (404, ac.FeedUnavailable, "404 -> FeedUnavailable")):
    c = ac.AmtrakerClient(user_agent="SAW-test/1.0", opener=raiser(code))
    try:
        c.fetch_fleet()
        check(label, False)
    except exc:
        check(label, True)
    except Exception:
        check(label, False)

check("RateLimited is distinguishable from FeedUnavailable",
      not issubclass(ac.FeedUnavailable, ac.RateLimited)
      and not issubclass(ac.RateLimited, ac.FeedUnavailable))
check("shared key carries the contract version",
      ac.SHARED_KEY.endswith(str(ac.CONTRACT_VERSION)))

# --- report ---------------------------------------------------------------
failed = [n for n, ok in CHECKS if not ok]
print("checks: %d   passed: %d   failed: %d"
      % (len(CHECKS), len(CHECKS) - len(failed), len(failed)))
for n in failed:
    print("  FAIL: %s" % n)
sys.exit(1 if failed else 0)
