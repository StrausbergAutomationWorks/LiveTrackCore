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
        self.assertIsNotNone(p.backoff.remaining(ap.LOL))    # 0.2.24: this answer refuses every URL, the fallback's too
        with self.assertRaises(ap.SourceError):
            run(p.circle(s, 41.9, -87.9, 56, 1000.0, sources=ap.FI_ONLY))
        self.assertEqual(len(s.asked), 2)    # the circle was never requested
        clock.t += 61
        self.assertTrue(run(p.hex_round(s, 1000.0)).startswith("waiting"))
        self.assertEqual(len(s.asked), 2)

    def test_no_hexes_no_request(self):
        p, _, _ = make()
        s = Session(lambda u: Resp(200, payload()))
        self.assertEqual(run(p.hex_round(s, 1000.0)), "idle")
        self.assertEqual(s.asked, [])

    def test_adsb_fi_first_and_adsb_lol_never_while_it_answers(self):
        # 0.2.24 (Lee 2026-11-02 22:11) replaced B16's "never adsb.lol": only while adsb.fi is failing
        self.assertIn("opendata.adsb.fi", ap.HEX_URL)
        self.assertNotIn("lol", ap.HEX_URL)
        self.assertEqual(ap.HEX_SOURCES, (ap.FI, ap.LOL))
        p, clock, _ = make()
        p.set_hexes("x", ["a00808"])
        s = Session(lambda u: Resp(200, payload(rec("a00808"))))
        for _ in range(3):
            self.assertEqual(run(p.hex_round(s, 1000.0)), "ok")
            clock.t += 61
        self.assertEqual(len(s.asked), 3)
        self.assertFalse(any("adsb.lol" in u for u in s.asked))
        self.assertEqual(p.hex_source, ap.FI)


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


class TestCovered(unittest.TestCase):
    """0.2.14: a circle wholly inside another consumer's fresh answer costs no request."""
    BIG = (43.3925, -87.8218, 463.0)          # Live Track Aircraft's circle as Lee set it, 2026-10-24
    HOME = (41.895, -87.995, 56.0)            # 167 km from that centre: wholly inside

    def setup(self, answer=None):
        p, clock, _ = make()
        s = Session(answer or (lambda u: Resp(200, payload(rec("a00001", lat=41.90, lon=-87.99),
                                                           rec("a00002", lat=43.39, lon=-87.82)))))
        run(p.circle(s, *self.BIG, 1000.0, consumer="lta"))
        return p, clock, s

    def test_inside_costs_no_request_and_is_filtered(self):
        p, _, s = self.setup()
        name, obs = run(p.circle(s, *self.HOME, 1000.0, sources=ap.FI_ONLY, consumer="ltea"))
        self.assertEqual(len(s.asked), 1)
        self.assertEqual([o.identity for o in obs], ["a00001"])      # the far one is outside 56 km
        self.assertEqual(p.circle_how["ltea"], "covered")
        self.assertEqual(p.covered_answers, 1)

    def test_whatever_the_sources_and_the_answering_feed_is_passed_on(self):
        p, _, s = self.setup(lambda u: Resp(503, {}) if "adsb.fi" in u else Resp(200, payload(rec("a00001", lat=41.9, lon=-87.99))))
        name, obs = run(p.circle(s, *self.HOME, 1000.0, sources=ap.FI_ONLY, consumer="ltea"))
        self.assertEqual(name, ap.LOL)
        self.assertFalse(any("adsb.lol" in u for u in s.asked[2:]))   # nothing asked for the covered one
        self.assertEqual(len(s.asked), 2)

    def test_partly_outside_is_asked(self):
        p, _, s = self.setup()
        run(p.circle(s, 41.895, -87.995, 300.0, 1000.0, sources=ap.FI_ONLY, consumer="ltea"))
        self.assertEqual(len(s.asked), 2)
        self.assertEqual(p.circle_how["ltea"], "asked")

    def test_stale_neighbour_is_not_used(self):
        # over the 15 s poll floor, so a neighbour polling every 15 s always covers; 21 s is too old
        self.assertTrue(15.0 < ap.COVER_FRESH_S <= 20.0)
        p, clock, s = self.setup()
        clock.t += 21.0
        run(p.circle(s, *self.HOME, 1000.0, sources=ap.FI_ONLY, consumer="ltea"))
        self.assertEqual(len(s.asked), 2)

    def test_own_answer_never_covers_itself(self):
        p, clock, s = self.setup()
        clock.t += ap.CIRCLE_SHARE_S + 0.1
        run(p.circle(s, 43.3925, -87.8218, 400.0, 1000.0, consumer="lta"))
        self.assertEqual(len(s.asked), 2)

    def test_the_asked_radius_is_the_nm_one(self):
        p, _, s = make()[0], None, Session(lambda u: Resp(200, payload()))
        run(p.circle(s, 41.9, -87.9, 56.0, 1000.0, consumer="lta"))      # 30 nm = 55.56 km asked
        run(p.circle(s, 41.9, -87.9, 55.6, 1000.0, sources=ap.FI_ONLY, consumer="ltea"))
        self.assertEqual(len(s.asked), 2)                                # 55.6 > 55.56: not covered
        run(p.circle(s, 41.9, -87.9, 55.5, 1000.0, sources=ap.FI_ONLY, consumer="ltea2"))
        self.assertEqual(len(s.asked), 2)                                # 55.5 is

    def test_without_a_consumer_nothing_changes(self):
        p, _, s = self.setup()
        run(p.circle(s, *self.HOME, 1000.0, sources=ap.FI_ONLY))
        self.assertEqual(len(s.asked), 2)

    def test_a_failed_circle_leaves_nothing_to_cover_with(self):
        p, _, _ = make()
        bad = Session(lambda u: Resp(503, {}))
        with self.assertRaises(ap.SourceError):
            run(p.circle(bad, *self.BIG, 1000.0, consumer="lta"))
        self.assertIsNone(p.covered("ltea", *self.HOME))

    def test_release_drops_the_answer(self):
        p, _, s = self.setup()
        p.release("lta")
        self.assertIsNone(p.covered("ltea", *self.HOME))


class Hung:
    """A response that never comes: what adsb.fi did from 2026-11-02 21:29."""

    async def __aenter__(self):
        await asyncio.sleep(5)

    async def __aexit__(self, *a):
        return False


def fi_down(ok_records=("a00808",)):
    """adsb.fi raises as a timeout does; adsb.lol answers."""
    return lambda u: TimeoutError() if "adsb.fi" in u else Resp(200, payload(*[rec(h) for h in ok_records]))


class TestFailureSkip(unittest.TestCase):
    """0.2.24: a source that fails gets a skip window, as a 429 does."""

    def ask(self, p, s, lat=41.9):
        return run(p.circle(s, lat, -87.9, 56, 1000.0, sources=ap.FI_ONLY))

    def test_one_failure_costs_nothing_and_the_second_starts_the_window(self):
        p, clock, _ = make()
        s = Session(lambda u: TimeoutError())
        with self.assertRaises(ap.SourceError):
            self.ask(p, s)
        self.assertIsNone(p.backoff.remaining(ap.FI))
        with self.assertRaises(ap.SourceError):
            self.ask(p, s, 42.0)
        self.assertAlmostEqual(p.backoff.remaining(ap.FI), ap.BACKOFF_FIRST_S, places=3)
        self.assertEqual(len(s.asked), 2)
        with self.assertRaises(ap.SourceError):
            self.ask(p, s, 42.1)
        self.assertEqual(len(s.asked), 2)    # not asked inside the window

    def test_the_window_doubles_to_the_ceiling(self):
        b = ap.Backoff(clock=(c := Clock()))
        self.assertIsNone(b.failed("x"))
        seen = []
        for _ in range(7):
            seen.append(b.failed("x"))
            c.t += seen[-1] + 1
        self.assertEqual(seen, [60.0, 120.0, 240.0, 480.0, 600.0, 600.0, 600.0])

    def test_a_failure_inside_a_window_was_already_in_flight(self):
        c = Clock()
        b = ap.Backoff(clock=c)
        b.failed("x")
        self.assertEqual(b.failed("x"), ap.BACKOFF_FIRST_S)
        c.t += 5
        self.assertIsNone(b.failed("x"))
        self.assertIsNone(b.failed("x"))
        self.assertAlmostEqual(b.remaining("x"), ap.BACKOFF_FIRST_S - 5, places=3)
        c.t += 60
        self.assertEqual(b.failed("x"), 2 * ap.BACKOFF_FIRST_S)     # the next step, not two steps on

    def test_an_answer_clears_it(self):
        p, clock, _ = make()
        bad = Session(lambda u: TimeoutError())
        for lat in (41.0, 42.0):
            with self.assertRaises(ap.SourceError):
                self.ask(p, bad, lat)
        clock.t += ap.BACKOFF_FIRST_S + 1
        self.ask(p, Session(lambda u: Resp(200, payload())), 43.0)
        self.assertEqual(p.backoff.status(), {})
        with self.assertRaises(ap.SourceError):
            self.ask(p, bad, 44.0)
        self.assertIsNone(p.backoff.remaining(ap.FI))     # one failure again, not the third

    def test_any_failure_counts_but_a_429_keeps_its_own_steps(self):
        for answer in (lambda u: OSError("reset"), lambda u: Resp(503, {}), lambda u: Resp(403, {}),
                       lambda u: Resp(200, ValueError("not json"))):
            p, _, _ = make()
            s = Session(answer)
            for lat in (41.0, 42.0):
                with self.assertRaises(ap.SourceError):
                    self.ask(p, s, lat)
            self.assertAlmostEqual(p.backoff.remaining(ap.FI), ap.BACKOFF_FIRST_S, places=3)
        p, clock, _ = make()
        with self.assertRaises(ap.SourceError):
            self.ask(p, Session(lambda u: Resp(429, {})), 41.0)
        self.assertAlmostEqual(p.backoff.remaining(ap.FI), ap.BACKOFF_FIRST_S, places=3)
        clock.t += ap.BACKOFF_FIRST_S + 1
        with self.assertRaises(ap.SourceError):
            self.ask(p, Session(lambda u: TimeoutError()), 42.0)
        self.assertIsNone(p.backoff.remaining(ap.FI))     # the 429 was not a failure of this kind

    def test_a_real_hang_is_cut_at_the_timeout_and_counted(self):
        p, _, _ = make()
        old = ap.REQUEST_TIMEOUT_S
        ap.REQUEST_TIMEOUT_S = 0.01
        try:
            s = Session(lambda u: Hung())
            for lat in (41.0, 42.0):
                with self.assertRaisesRegex(ap.SourceError, "unreachable: TimeoutError"):
                    self.ask(p, s, lat)
        finally:
            ap.REQUEST_TIMEOUT_S = old
        self.assertIsNotNone(p.backoff.remaining(ap.FI))

    def test_the_window_shows_in_the_status(self):
        p, _, _ = make()
        s = Session(lambda u: TimeoutError())
        for lat in (41.0, 42.0):
            with self.assertRaises(ap.SourceError):
                self.ask(p, s, lat)
        self.assertEqual(list(p.status()["backoff"]), [ap.FI])
        self.assertEqual(p.status()["hex_source"], ap.FI)

    def test_a_consumer_with_a_fallback_stops_waiting(self):
        # what the night measured: Live Track Aircraft asked adsb.fi first every poll and waited 20 s each time
        p, clock, _ = make()
        s = Session(fi_down())
        for i in range(4):
            name, _obs = run(p.circle(s, 41.0 + i, -87.9, 56, 1000.0))
            self.assertEqual(name, ap.LOL)
            clock.t += 15
        self.assertEqual(sum("adsb.fi" in u for u in s.asked), 2)
        self.assertEqual(sum("adsb.lol" in u for u in s.asked), 4)


class TestHexFallback(unittest.TestCase):
    """0.2.24: the hex round falls back to adsb.lol only while adsb.fi is failing."""

    def poll(self, hexes=("a00808",)):
        p, clock, slept = make()
        p.set_hexes("x", hexes)
        return p, clock, slept

    def test_a_failing_round_is_answered_by_adsb_lol_at_once(self):
        p, _, _ = self.poll(("a00808", "a022b0"))
        s = Session(fi_down())
        status = run(p.hex_round(s, 1000.0))
        self.assertEqual(status, "ok from adsb.lol: adsb.fi unreachable: TimeoutError()")
        self.assertEqual(p.hex_source, ap.LOL)
        self.assertEqual([o.identity for o in p.hex_observations("x")], ["a00808"])
        self.assertEqual(len(s.asked), 2)
        self.assertTrue(s.asked[0].startswith(ap.HEX_URL))
        self.assertEqual(s.asked[1], "https://api.adsb.lol/v2/hex/A00808,A022B0")

    def test_inside_adsb_fis_window_it_is_not_asked(self):
        p, clock, _ = self.poll()
        p.backoff.refused(ap.FI, 300.0)
        s = Session(fi_down())
        self.assertEqual(run(p.hex_round(s, 1000.0)), "ok from adsb.lol: adsb.fi backing off")
        self.assertEqual(len(s.asked), 1)
        self.assertIn("adsb.lol", s.asked[0])

    def test_back_to_adsb_fi_when_it_answers(self):
        p, clock, _ = self.poll()
        run(p.hex_round(Session(fi_down()), 1000.0))
        self.assertEqual(p.hex_source, ap.LOL)
        clock.t += 61
        s = Session(lambda u: Resp(200, payload(rec("a00808"))))
        self.assertEqual(run(p.hex_round(s, 1000.0)), "ok")
        self.assertEqual(p.hex_source, ap.FI)
        self.assertEqual(len(s.asked), 1)
        self.assertIn("adsb.fi", s.asked[0])

    def test_both_failing_keeps_the_last_answer_and_its_source(self):
        p, clock, _ = self.poll()
        run(p.hex_round(Session(lambda u: Resp(200, payload(rec("a00808")))), 1000.0))
        clock.t += 61
        s = Session(lambda u: TimeoutError())
        status = run(p.hex_round(s, 1000.0))
        self.assertTrue(status.startswith("failed: adsb.fi unreachable"), status)
        self.assertIn("adsb.lol unreachable", status)
        self.assertEqual(p.hex_source, ap.FI)
        self.assertEqual(len(p.hex_observations("x")), 1)
        clock.t += ap.HEX_STALE_S
        self.assertEqual(p.hex_observations("x"), [])

    def test_both_in_their_windows_asks_nobody(self):
        p, _, _ = self.poll()
        p.backoff.refused(ap.FI, 300.0)
        p.backoff.refused(ap.LOL, 300.0)
        s = Session(fi_down())
        self.assertEqual(run(p.hex_round(s, 1000.0)), "waiting: adsb.fi backing off; adsb.lol backing off")
        self.assertEqual(s.asked, [])

    def test_a_round_is_one_sources_whole_answer(self):
        hexes = ["%06x" % (0xa00000 + i) for i in range(ap.MAX_PER_REQUEST + 5)]
        p, _, _ = self.poll(hexes)
        calls = []

        def answer(u):
            calls.append(u)
            if "adsb.lol" in u:
                return Resp(200, payload(rec(hexes[-1])))
            return Resp(200, payload(rec(hexes[0]))) if len(calls) == 1 else TimeoutError()
        status = run(p.hex_round(Session(answer), 1000.0))
        self.assertTrue(status.startswith("ok from adsb.lol"))
        # adsb.fi's first part is not mixed into adsb.lol's round
        self.assertNotIn(hexes[0], [o.identity for o in p.hex_observations("x")])
        self.assertEqual(sum("adsb.lol" in u for u in calls), 2)

    def test_the_fallback_answer_parses(self):
        p, _, _ = self.poll()
        run(p.hex_round(Session(fi_down()), 1000.0))
        self.assertEqual(p.hex_observations("x")[0].observed_at, 1_800_000_000 - 1)


class TestWarnings(unittest.TestCase):
    """0.2.24: one WARNING when a source starts failing, one when it has answered again."""
    LOG = "saw_livetrack.adsb_poll"

    def fail(self, p, clock, n=1, answer=None):
        s = Session(answer or (lambda u: TimeoutError()))
        for i in range(n):
            left = p.backoff.remaining(ap.FI)
            if left is not None:
                clock.t += left + 1
            with self.assertRaises(ap.SourceError):
                run(p.get_json(s, ap.FI, "https://x/%d" % i))

    def good(self, p):
        run(p.get_json(Session(lambda u: Resp(200, {})), ap.FI, "https://x/ok"))

    def test_one_failure_says_nothing(self):
        p, clock, _ = make()
        with self.assertNoLogs(self.LOG, "WARNING"):
            self.fail(p, clock, 1)
            self.good(p)
        self.assertEqual(p._health, {})

    def test_one_warning_when_it_starts_failing(self):
        p, clock, _ = make()
        with self.assertLogs(self.LOG, "WARNING") as cm:
            self.fail(p, clock, 6)
        self.assertEqual(len(cm.output), 1)
        self.assertIn("adsb.fi is failing (TimeoutError())", cm.output[0])

    def test_one_warning_when_it_has_answered_again(self):
        p, clock, _ = make()
        self.fail(p, clock, 2)
        clock.t += ap.BACKOFF_FIRST_S + 1
        with self.assertNoLogs(self.LOG, "WARNING"):
            self.good(p)
            clock.t += ap.ANSWERING_AGAIN_AFTER_S - 1
            self.good(p)
        clock.t += 1
        with self.assertLogs(self.LOG, "WARNING") as cm:
            self.good(p)
        self.assertEqual(len(cm.output), 1)
        self.assertIn("adsb.fi is answering again, after", cm.output[0])
        clock.t += ap.ANSWERING_AGAIN_AFTER_S + 1
        with self.assertNoLogs(self.LOG, "WARNING"):
            self.good(p)

    def test_a_feed_answering_now_and_then_is_one_episode(self):
        p, clock, _ = make()
        with self.assertLogs(self.LOG, "WARNING") as cm:
            for _ in range(5):
                self.fail(p, clock, 2)
                clock.t += ap.BACKOFF_MAX_S + 1
                self.good(p)
                clock.t += ap.ANSWERING_AGAIN_AFTER_S - 10
        self.assertEqual(len(cm.output), 1)
        self.assertIn("is failing", cm.output[0])

    def test_a_steady_429_warns_once(self):
        p, clock, _ = make()
        with self.assertLogs(self.LOG, "WARNING") as cm:
            self.fail(p, clock, 5, lambda u: Resp(429, {}))
        self.assertEqual(len(cm.output), 1)
        self.assertIn("(HTTP 429)", cm.output[0])

    def test_each_source_has_its_own_episode(self):
        p, clock, _ = make()
        s = Session(lambda u: TimeoutError())
        with self.assertLogs(self.LOG, "WARNING") as cm:
            for source in (ap.FI, ap.LOL):
                for lap in range(2):
                    with self.assertRaises(ap.SourceError):
                        run(p.get_json(s, source, "https://x/"))
        self.assertEqual(len(cm.output), 2)


class TestFallbackEvery(unittest.TestCase):
    """0.2.24: a second consumer on the fallback asks it at most every fallback_every_s while the first source is out."""

    def ask(self, p, s):
        return run(p.circle(s, 30.0, -96.0, 463.0, 1000.0, consumer="salt", fallback_every_s=30.0))

    def out(self):
        p, clock, _ = make()
        s = Session(fi_down())
        p.backoff.failed(ap.FI)
        p.backoff.failed(ap.FI)      # adsb.fi in its window
        return p, clock, s

    def test_spaced_while_the_first_source_is_out(self):
        p, clock, s = self.out()
        a = self.ask(p, s)
        self.assertEqual(a[0], ap.LOL)
        self.assertEqual(len(s.asked), 1)
        clock.t += 15
        self.assertEqual(self.ask(p, s), a)
        self.assertEqual(len(s.asked), 1)
        clock.t += 15
        self.ask(p, s)
        self.assertEqual(len(s.asked), 2)

    def test_the_first_source_is_asked_as_soon_as_its_window_ends(self):
        p, clock, s = self.out()
        self.ask(p, s)
        p.backoff.answered(ap.FI)      # no window now
        clock.t += 15
        ok = Session(lambda u: Resp(200, payload(rec("a00808"))))
        self.assertEqual(self.ask(p, ok)[0], ap.FI)
        self.assertEqual(len(ok.asked), 1)

    def test_an_answer_from_the_first_source_is_never_reused(self):
        p, clock, _ = make()
        s = Session(lambda u: Resp(200, payload(rec("a00808"))))
        self.assertEqual(self.ask(p, s)[0], ap.FI)
        p.backoff.failed(ap.FI)
        p.backoff.failed(ap.FI)      # adsb.fi goes out right after answering
        clock.t += ap.CIRCLE_SHARE_S + 1
        self.assertEqual(self.ask(p, s)[0], ap.LOL)
        self.assertEqual(len(s.asked), 2)

    def test_without_it_every_poll_asks(self):
        p, clock, s = self.out()
        for _ in range(3):
            run(p.circle(s, 30.0, -96.0, 463.0, 1000.0, consumer="lta"))
            clock.t += ap.CIRCLE_SHARE_S + 1
        self.assertEqual(len(s.asked), 3)


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
