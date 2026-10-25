"""Tests for saw_livetrack.adsb_poll (backlog 297, 0.2.13). Offline: a fake session and clock.

Run: python -m unittest tests.test_adsb_poll
"""
import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from saw_livetrack import adsb_poll as ap  # noqa: E402


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Resp:
    def __init__(self, status, body, headers=None):
        self.status, self.body, self.headers = status, body, headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class Session:
    """answer(url) -> Resp or an exception; records every URL asked, with its User-Agent."""

    def __init__(self, answer):
        self.answer, self.asked, self.agents = answer, [], []

    def get(self, url, headers=None):
        self.asked.append(url)
        self.agents.append((headers or {}).get("User-Agent"))
        r = self.answer(url)
        if isinstance(r, Exception):
            raise r
        return r


def rec(h, lat=41.9, lon=-87.9, **kw):
    r = {"hex": h, "lat": lat, "lon": lon, "seen_pos": 1.0, "alt_baro": 3000, "gs": 120, "track": 90}
    r.update(kw)
    return r


def payload(*records, now=1_800_000_000_000):
    return {"now": now, "ac": list(records)}


def run(coro):
    return asyncio.run(coro)


def make(clock=None):
    clock = clock or Clock()
    slept = []

    async def sleep(s):
        slept.append(s)
        clock.t += s

    return ap.Poll(clock=clock, sleep=sleep), clock, slept


class TestParse(unittest.TestCase):
    def test_record_units_and_skips(self):
        o = ap.parse_record(rec("A1B2C3", flight="DHS12 ", dbFlags=5, ownOp="DHS"), 1000.0)
        self.assertEqual(o.identity, "a1b2c3")
        self.assertEqual(o.observed_at, 999)
        self.assertEqual(o.speed_kmh, 222.2)
        self.assertEqual(o.altitude_m, 914.4)
        self.assertEqual(o.callsign, "DHS12")
        self.assertEqual(o.db_flags, 5)
        self.assertIsNone(ap.parse_record(rec("~a1b2c3"), 1000.0))
        self.assertIsNone(ap.parse_record(rec("a1b2c3", alt_baro="ground"), 1000.0))
        self.assertIsNone(ap.parse_record({"hex": "a1b2c3"}, 1000.0))
        self.assertIsNone(ap.parse_record(rec("a1b2c3", dbFlags=True), 1000.0).db_flags)

    def test_feed_clock_units(self):
        self.assertEqual(ap.feed_clock({"now": 1_789_164_099_000}, 5.0), 1_789_164_099.0)
        self.assertEqual(ap.feed_clock({"now": 1_789_164_098.001}, 5.0), 1_789_164_098.001)
        self.assertEqual(ap.feed_clock({}, 5.0), 5.0)

    def test_retry_after(self):
        self.assertEqual(ap.retry_after_s("30", 0), 30.0)
        self.assertEqual(ap.retry_after_s("99999", 0), ap.RETRY_AFTER_MAX_S)
        self.assertEqual(ap.retry_after_s("0", 0), 1.0)
        self.assertIsNone(ap.retry_after_s("soon", 0))
        self.assertIsNone(ap.retry_after_s(None, 0))

    def test_short_floor(self):
        self.assertIsNone(ap.short_floor(19))
        self.assertEqual(ap.short_floor(200), 100)


class TestHexRound(unittest.TestCase):
    def test_union_once_a_minute_and_each_reads_its_own(self):
        p, clock, _ = make()
        p.set_hexes("lta", ["ADFDF8", "a00808", "bad"])
        p.set_hexes("ltea", ["a00808", "a022b0"])
        self.assertEqual(p.union(), ["a00808", "a022b0", "adfdf8"])
        s = Session(lambda u: Resp(200, payload(rec("a00808"), rec("adfdf8"), rec("ffffff"))))
        self.assertEqual(run(p.hex_round(s, 1000.0)), "ok")
        self.assertEqual(len(s.asked), 1)
        self.assertTrue(s.asked[0].endswith("A00808,A022B0,ADFDF8"))
        self.assertEqual(sorted(o.identity for o in p.hex_observations("lta")), ["a00808", "adfdf8"])
        self.assertEqual([o.identity for o in p.hex_observations("ltea")], ["a00808"])
        run(p.hex_round(s, 1000.0))          # not due: no request
        clock.t += 30
        run(p.hex_round(s, 1000.0))
        self.assertEqual(len(s.asked), 1)
        clock.t += 31
        run(p.hex_round(s, 1000.0))
        self.assertEqual(len(s.asked), 2)

    def test_chunks_are_paced(self):
        p, clock, slept = make()
        hexes = ["%06x" % (0xa00000 + i) for i in range(ap.MAX_PER_REQUEST + 5)]
        p.set_hexes("x", hexes)
        s = Session(lambda u: Resp(200, payload()))
        run(p.hex_round(s, 1000.0))
        self.assertEqual(len(s.asked), 2)
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], ap.PACE_S, places=6)

    def test_failure_keeps_last_answer_until_stale(self):
        p, clock, _ = make()
        p.set_hexes("x", ["a00808"])
        ok = Session(lambda u: Resp(200, payload(rec("a00808"))))
        run(p.hex_round(ok, 1000.0))
        clock.t += 61
        bad = Session(lambda u: Resp(503, {}))
        self.assertTrue(run(p.hex_round(bad, 1000.0)).startswith("failed"))
        self.assertEqual(len(p.hex_observations("x")), 1)
        clock.t += ap.HEX_STALE_S
        self.assertEqual(p.hex_observations("x"), [])

    def test_429_backs_off_the_circle_too(self):
        p, clock, _ = make()
        p.set_hexes("x", ["a00808"])
        s = Session(lambda u: Resp(429, {}, {"Retry-After": "120"}))
        run(p.hex_round(s, 1000.0))
        self.assertIsNotNone(p.backoff.remaining(ap.FI))
        with self.assertRaises(ap.SourceError):
            run(p.circle(s, 41.9, -87.9, 56, 1000.0, sources=ap.FI_ONLY))
        self.assertEqual(len(s.asked), 1)    # the circle was never requested
        clock.t += 61
        self.assertTrue(run(p.hex_round(s, 1000.0)).startswith("waiting"))
        self.assertEqual(len(s.asked), 1)

    def test_no_hexes_no_request(self):
        p, _, _ = make()
        s = Session(lambda u: Resp(200, payload()))
        self.assertEqual(run(p.hex_round(s, 1000.0)), "idle")
        self.assertEqual(s.asked, [])

    def test_never_adsb_lol(self):
        self.assertIn("opendata.adsb.fi", ap.HEX_URL)
        self.assertNotIn("lol", ap.HEX_URL)


class TestCircle(unittest.TestCase):
    def test_identical_circle_asked_once(self):
        p, clock, _ = make()
        s = Session(lambda u: Resp(200, payload(rec("a1b2c3"))))
        a = run(p.circle(s, 41.89501, -87.99461, 56.0, 1000.0))
        b = run(p.circle(s, 41.89502, -87.99459, 56.0, 1000.0))
        self.assertEqual(len(s.asked), 1)
        self.assertEqual(a, b)
        clock.t += ap.CIRCLE_SHARE_S + 0.1
        run(p.circle(s, 41.89501, -87.99461, 56.0, 1000.0))
        self.assertEqual(len(s.asked), 2)

    def test_different_circles_and_sources_are_separate(self):
        p, _, _ = make()
        s = Session(lambda u: Resp(200, payload()))
        run(p.circle(s, 41.9, -87.9, 56, 1000.0))
        run(p.circle(s, 41.9, -87.9, 100, 1000.0))
        run(p.circle(s, 41.9, -87.9, 56, 1000.0, sources=ap.FI_ONLY))
        self.assertEqual(len(s.asked), 3)

    def test_concurrent_identical_waits_for_one(self):
        p, _, _ = make()
        s = Session(lambda u: Resp(200, payload(rec("a1b2c3"))))

        async def both():
            return await asyncio.gather(p.circle(s, 41.9, -87.9, 56, 1000.0),
                                        p.circle(s, 41.9, -87.9, 56, 1000.0))
        r1, r2 = run(both())
        self.assertEqual(len(s.asked), 1)
        self.assertEqual(r1, r2)

    def test_shared_failure_is_not_retried_inside_the_window(self):
        p, _, _ = make()
        s = Session(lambda u: Resp(503, {}))
        for _ in range(2):
            with self.assertRaises(ap.SourceError):
                run(p.circle(s, 41.9, -87.9, 56, 1000.0, sources=ap.FI_ONLY))
        self.assertEqual(len(s.asked), 1)

    def test_falls_through_and_fi_only_never_asks_lol(self):
        p, _, _ = make()
        s = Session(lambda u: Resp(503, {}) if "adsb.fi" in u else Resp(200, payload(rec("a1b2c3"))))
        name, obs = run(p.circle(s, 41.9, -87.9, 56, 1000.0))
        self.assertEqual(name, ap.LOL)
        p2, _, _ = make()
        s2 = Session(lambda u: Resp(503, {}) if "adsb.fi" in u else Resp(200, payload()))
        with self.assertRaises(ap.SourceError):
            run(p2.circle(s2, 41.9, -87.9, 56, 1000.0, sources=ap.FI_ONLY))
        self.assertFalse(any("adsb.lol" in u for u in s2.asked))

    def test_short_answer_tries_next_and_both_short_believed(self):
        p, _, _ = make()
        s = Session(lambda u: Resp(200, payload(rec("a1b2c3"))) if "adsb.fi" in u
                    else Resp(200, payload(rec("a1b2c3"), rec("a1b2c4"))))
        name, obs = run(p.circle(s, 41.9, -87.9, 56, 1000.0, min_expected=10))
        self.assertEqual((name, len(obs)), (ap.LOL, 2))

    def test_short_and_unanswered_is_a_failure(self):
        p, _, _ = make()
        s = Session(lambda u: Resp(200, payload(rec("a1b2c3"))) if "adsb.fi" in u else Resp(503, {}))
        with self.assertRaises(ap.SourceError):
            run(p.circle(s, 41.9, -87.9, 56, 1000.0, min_expected=10))

    def test_requests_to_one_source_are_paced(self):
        p, clock, slept = make()
        s = Session(lambda u: Resp(200, payload()))
        run(p.circle(s, 41.9, -87.9, 56, 1000.0, sources=ap.FI_ONLY))
        run(p.circle(s, 42.9, -87.9, 56, 1000.0, sources=ap.FI_ONLY))
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], ap.PACE_S, places=6)

    def test_network_fault_is_a_source_error_and_200_ends_backoff(self):
        p, clock, _ = make()
        p.backoff.refused(ap.FI, 5)
        clock.t += 6
        s = Session(lambda u: OSError("down"))
        with self.assertRaises(ap.SourceError):
            run(p.circle(s, 41.9, -87.9, 56, 1000.0, sources=ap.FI_ONLY))
        ok = Session(lambda u: Resp(200, payload()))
        run(p.circle(ok, 43.0, -87.9, 56, 1000.0, sources=ap.FI_ONLY))
        self.assertEqual(p.backoff.status(), {})

    def test_a_200_resets_the_strikes(self):
        p, clock, _ = make()
        p.backoff.refused(ap.FI, 5)
        clock.t += 6
        run(p.circle(Session(lambda u: Resp(200, payload())), 43.0, -87.9, 56, 1000.0, sources=ap.FI_ONLY))
        clock.t += ap.CIRCLE_SHARE_S + 1
        with self.assertRaises(ap.SourceError):
            run(p.circle(Session(lambda u: Resp(429, {})), 43.0, -87.9, 56, 1000.0, sources=ap.FI_ONLY))
        # one strike, not two: the first backoff step, not the doubled one
        self.assertAlmostEqual(p.backoff.remaining(ap.FI), ap.BACKOFF_FIRST_S, places=3)

    def test_user_agent_is_the_callers(self):
        p, _, _ = make()
        s = Session(lambda u: Resp(200, payload()))
        run(p.circle(s, 41.9, -87.9, 56, 1000.0, user_agent="X/1"))
        self.assertEqual(s.agents, ["X/1"])


class TestClaims(unittest.TestCase):
    def test_claims_and_release(self):
        p, _, _ = make()
        p.claim("lta", ["A00808", "a022b0"])
        p.claim("ltea", ["adce96"])
        self.assertEqual(p.claimed_by_others("ltea"), frozenset({"a00808", "a022b0"}))
        self.assertEqual(p.claimed_by_others("lta"), frozenset({"adce96"}))
        p.set_hexes("lta", ["adfdf8"])
        p.release("lta")
        self.assertEqual(p.claimed_by_others("ltea"), frozenset())
        self.assertEqual(p.union(), [])

    def test_shared_is_one_per_store(self):
        store = {}
        self.assertIs(ap.shared(store), ap.shared(store))
        self.assertIn(ap.STORE_KEY, store)


if __name__ == "__main__":
    unittest.main()
