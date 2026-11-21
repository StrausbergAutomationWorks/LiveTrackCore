"""saw_livetrack.aircraft_photos offline (0.2.43): Planespotters photos, looked up when a viewer hovers an aircraft.
Hoisted from Live Track Aircraft's tests/test_photos.py with the module (backlog 361).

! NO REAL PLANESPOTTERS ANSWER IS IN THIS FILE. The terms forbid re-exposing their data, and this repository is
public. Every name, id and URL below is invented; only the response SHAPE is the one measured on 2026-11-21:
photos[] of {id, thumbnail{src,size}, thumbnail_large{src,size}, link, photographer}.
Run:  python tests/test_aircraft_photos.py  -> exit 0
"""
import asyncio
import inspect
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import saw_livetrack.aircraft_photos as ph  # noqa: E402

BASE = "/api/example_consumer/photo"


def item(**over):
    it = {"id": "1", "thumbnail": {"src": "https://t.example.net/9/1_aa_t.jpg", "size": {"width": 200, "height": 133}},
          "thumbnail_large": {"src": "https://t.example.net/9/1_aa_280.jpg", "size": {"width": 420, "height": 280}},
          "link": "https://photos.example.net/photo/1/n00000-example?utm_source=api", "photographer": "Ann Example"}
    it.update(over)
    return it


def body(*items):
    return json.dumps({"photos": list(items)}).encode()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Fake:
    """Stands in for the HTTP request; counts calls; answers from a queue."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def __call__(self, session, hexid):
        self.calls.append(hexid)
        await asyncio.sleep(0)
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


def run(coro):
    return asyncio.run(coro)


class Paths(unittest.TestCase):
    def test_hex_only(self):
        self.assertEqual(ph.lookup_path(BASE, "abc123"), BASE + "/abc123")
        self.assertEqual(ph.lookup_path(BASE, "ABC123"), BASE + "/abc123")
        for v in ("~abc123", "abc12", "abc1234", "abg123", "", None, 12):
            self.assertIsNone(ph.lookup_path(BASE, v), v)

    def test_no_lookup_for_a_privacy_address(self):
        self.assertIsNone(ph.lookup_path(BASE, "abc123", True))
        self.assertEqual(ph.lookup_path(BASE, "abc123", False), BASE + "/abc123")

    def test_the_base_is_the_consumers_and_must_be_an_api_path(self):
        for bad in ("api/x/photo", "/x/photo", "/api/x/photo/", "/api//photo", "/api/x y", None, ""):
            self.assertIsNone(ph.lookup_path(bad, "abc123"), bad)

    def test_the_card_takes_the_path(self):
        # LiveTrackMapCard's D4h/D4i rule: /api/ then letters, digits, _ - / only, no empty segment, <= 200
        p = ph.lookup_path(BASE, "abc123")
        self.assertRegex(p, r"^/api/[A-Za-z0-9_\-/]+$")
        self.assertNotIn("//", p)
        self.assertLessEqual(len(p), 200)


class Parse(unittest.TestCase):
    def test_photo_in_the_card_shape(self):
        state, p = ph.parse(200, body(item()))
        self.assertEqual(state, ph.PHOTO)
        self.assertEqual(p, {"url": "https://t.example.net/9/1_aa_280.jpg",
                             "page": "https://photos.example.net/photo/1/n00000-example?utm_source=api",
                             "credit": "Ann Example", "source": "Planespotters.net", "width": 420, "height": 280})

    def test_the_link_is_passed_on_unchanged(self):
        link = "https://photos.example.net/photo/1/x-y?utm_source=api&z=1"
        self.assertEqual(ph.parse(200, body(item(link=link)))[1]["page"], link)

    def test_small_thumbnail_when_no_large(self):
        it = item()
        del it["thumbnail_large"]
        p = ph.parse(200, body(it))[1]
        self.assertEqual((p["url"], p["width"], p["height"]), ("https://t.example.net/9/1_aa_t.jpg", 200, 133))

    def test_nothing_shown_without_what_the_terms_need(self):
        for bad in (item(photographer=""), item(photographer=None), item(link="http://x.example/p"),
                    item(link=None), item(thumbnail_large={"src": "http://t.example/x.jpg"}, thumbnail=None)):
            self.assertEqual(ph.parse(200, body(bad)), (ph.NONE, None))

    def test_none_and_errors(self):
        self.assertEqual(ph.parse(200, body()), (ph.NONE, None))
        self.assertEqual(ph.parse(200, b'{"error":"x"}'), (ph.ERROR, None))
        self.assertEqual(ph.parse(200, b"not json"), (ph.ERROR, None))
        self.assertEqual(ph.parse(200, b'{"photos":"x"}'), (ph.ERROR, None))
        self.assertEqual(ph.parse(429, b""), (ph.LIMITED, None))
        self.assertEqual(ph.parse(403, b""), (ph.REFUSED, None))
        self.assertEqual(ph.parse(500, body(item())), (ph.ERROR, None))


class Lookups(unittest.TestCase):
    def test_once_then_kept_12h(self):
        clock, fake = Clock(), Fake((200, {}, body(item())))
        lk = ph.PhotoLookups(fetch=fake, clock=clock)
        self.assertEqual(run(lk.get(None, "abc123"))[0], ph.PHOTO)
        clock.t += ph.KEEP_S - 1
        self.assertEqual(run(lk.get(None, "abc123"))[0], ph.PHOTO)
        self.assertEqual(fake.calls, ["abc123"])
        clock.t += 2
        run(lk.get(None, "abc123"))
        self.assertEqual(len(fake.calls), 2)

    def test_keep_is_12h_so_card_plus_integration_stays_inside_24h(self):
        self.assertEqual(ph.KEEP_S, 12 * 3600)

    def test_none_is_kept_too(self):
        clock, fake = Clock(), Fake((200, {}, body()))
        lk = ph.PhotoLookups(fetch=fake, clock=clock)
        self.assertEqual(run(lk.get(None, "abc123")), (ph.NONE, None))
        self.assertEqual(run(lk.get(None, "abc123")), (ph.NONE, None))
        self.assertEqual(len(fake.calls), 1)

    def test_errors_are_not_kept(self):
        clock, fake = Clock(), Fake(OSError("down"), (200, {}, body(item())))
        lk = ph.PhotoLookups(fetch=fake, clock=clock)
        self.assertEqual(run(lk.get(None, "abc123")), (ph.ERROR, None))
        self.assertEqual(run(lk.get(None, "abc123"))[0], ph.PHOTO)
        self.assertEqual(len(fake.calls), 2)

    def test_429_pauses_every_lookup_by_retry_after(self):
        clock, fake = Clock(), Fake((429, {"Retry-After": "120"}, b""), (200, {}, body(item())))
        lk = ph.PhotoLookups(fetch=fake, clock=clock)
        self.assertEqual(run(lk.get(None, "abc123"))[0], ph.LIMITED)
        self.assertEqual(run(lk.get(None, "def456"))[0], ph.LIMITED)   # paused: nothing sent
        self.assertEqual(len(fake.calls), 1)
        clock.t += 121
        self.assertEqual(run(lk.get(None, "def456"))[0], ph.PHOTO)

    def test_429_without_retry_after_pauses_10_min(self):
        clock, fake = Clock(), Fake((429, {}, b""))
        lk = ph.PhotoLookups(fetch=fake, clock=clock)
        run(lk.get(None, "abc123"))
        self.assertEqual(lk.paused_until, clock.t + ph.PAUSE_429_S)

    def test_403_stops_asking_for_a_day(self):
        clock, fake = Clock(), Fake((403, {}, b""), (200, {}, body(item())))
        lk = ph.PhotoLookups(fetch=fake, clock=clock)
        self.assertEqual(run(lk.get(None, "abc123"))[0], ph.REFUSED)
        clock.t += ph.PAUSE_403_S - 1
        self.assertEqual(run(lk.get(None, "def456"))[0], ph.REFUSED)
        self.assertEqual(len(fake.calls), 1)
        clock.t += 2
        self.assertEqual(run(lk.get(None, "def456"))[0], ph.PHOTO)

    def test_two_viewers_share_one_request(self):
        clock, fake = Clock(), Fake((200, {}, body(item())))
        lk = ph.PhotoLookups(fetch=fake, clock=clock)

        async def both():
            return await asyncio.gather(lk.get(None, "abc123"), lk.get(None, "abc123"))

        a, b = run(both())
        self.assertEqual(a, b)
        self.assertEqual(fake.calls, ["abc123"])

    def test_cache_is_bounded(self):
        clock, fake = Clock(), Fake((200, {}, body()))
        lk = ph.PhotoLookups(fetch=fake, clock=clock)
        for i in range(ph.MAX_CACHE + 3):
            run(lk.get(None, "%06x" % i))
        self.assertEqual(len(lk._kept), ph.MAX_CACHE)


class Shared(unittest.TestCase):
    def test_one_per_instance_under_a_neutral_key(self):
        store = {}
        a, b = ph.shared(store), ph.shared(store)
        self.assertIs(a, b)
        self.assertEqual(list(store), ["saw_aircraft_photos"])
        self.assertNotIn("live_track", ph.STORE_KEY)

    def test_two_consumers_share_the_answers_and_the_pause(self):
        store = {}
        clock, fake = Clock(), Fake((429, {}, b""), (200, {}, body(item())))
        store[ph.STORE_KEY] = ph.PhotoLookups(fetch=fake, clock=clock)
        self.assertEqual(run(ph.answer(store, None, "abc123")), (503, None))   # consumer A hits the 429
        self.assertEqual(run(ph.answer(store, None, "def456")), (503, None))   # consumer B is paused too
        self.assertEqual(len(fake.calls), 1)
        clock.t += ph.PAUSE_429_S + 1
        status, got = run(ph.answer(store, None, "def456"))
        self.assertEqual((status, got["photo"]["credit"]), (200, "Ann Example"))
        self.assertEqual(run(ph.answer(store, None, "def456"))[0], 200)       # kept: no second request
        self.assertEqual(len(fake.calls), 2)

    def test_answer_refuses_a_bad_id_without_asking(self):
        store = {}
        fake = Fake((200, {}, body(item())))
        store[ph.STORE_KEY] = ph.PhotoLookups(fetch=fake)
        for bad in ("~abc123", "abc12", "../x", None):
            self.assertEqual(run(ph.answer(store, None, bad)), (400, None))
        self.assertEqual(fake.calls, [])
        self.assertEqual(run(ph.answer(store, None, "ABC123"))[1]["photo"]["source"], "Planespotters.net")
        self.assertEqual(fake.calls, ["abc123"])


class Wiring(unittest.TestCase):
    def test_user_agent_names_the_library_and_a_contact(self):
        self.assertIn("saw-livetrack/", ph.USER_AGENT)
        self.assertIn("https://github.com/StrausbergAutomationWorks/LiveTrackCore", ph.USER_AGENT)
        self.assertNotIn("Mozilla", ph.USER_AGENT)
        self.assertIn('headers={"User-Agent": USER_AGENT', inspect.getsource(ph._fetch))

    def test_nothing_downloads_an_image(self):
        src = inspect.getsource(ph)
        self.assertEqual(src.count("session.get("), 1)       # the JSON only
        self.assertIn("API_URL + hexid", src)

    def test_pure(self):
        self.assertNotIn("import homeassistant", inspect.getsource(ph))
        self.assertNotIn("from homeassistant", inspect.getsource(ph))


if __name__ == "__main__":
    unittest.main()
