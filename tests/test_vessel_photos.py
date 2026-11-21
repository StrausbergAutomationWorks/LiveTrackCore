"""saw_livetrack.vessel_photos offline (0.2.43): a vessel's photo from Wikidata and Commons, by IMO, else by MMSI.
Hoisted from Live Track Ships' photos.py (backlog 346); the MMSI fallback was added 2026-11-21.

Every item id, number, file name, person and URL below is INVENTED; only the response SHAPES are the ones the
Action API and Commons give (measured 2026-11-16 and 2026-11-21).
Run:  python tests/test_vessel_photos.py  -> exit 0
"""
import asyncio
import inspect
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import saw_livetrack.vessel_photos as vp  # noqa: E402

PHOTO_A = {"url": "https://img.example.org/a.jpg", "page": "https://commons.example.org/a", "credit": "Ann Example",
           "licence": "CC BY-SA 4.0", "source": "Wikimedia Commons"}
PHOTO_B = dict(PHOTO_A, url="https://img.example.org/b.jpg", credit="Bo Example")


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def entity(qid, prop, numbers, files, deprecated=False):
    claims = {prop: [{"mainsnak": {"datavalue": {"value": n}}} for n in numbers]}
    if files:
        claims["P18"] = [{"mainsnak": {"datavalue": {"value": f}}, "rank": "deprecated" if deprecated else "normal"}
                         for f in files]
    return {qid: {"claims": claims}}


class Pure(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(vp.valid(vp.IMO, "9000001"), "9000001")
        self.assertEqual(vp.valid(vp.IMO, 9000001), "9000001")
        self.assertEqual(vp.valid(vp.MMSI, "300000001"), "300000001")
        for bad in ("900000", "90000011", "abcdefg", "", None, True):
            self.assertIsNone(vp.valid(vp.IMO, bad), bad)
        for bad in ("30000000", "3000000011", "9000001", None):
            self.assertIsNone(vp.valid(vp.MMSI, bad), bad)

    def test_query_uses_the_right_property(self):
        self.assertEqual(vp.search_query(vp.IMO, ["9000001", "9000002"]),
                         "haswbstatement:P458=9000001|P458=9000002")
        self.assertEqual(vp.search_query(vp.MMSI, ["300000001"]), "haswbstatement:P587=300000001")

    def test_files_only_for_an_item_whose_own_number_we_asked(self):
        ents = {}
        ents.update(entity("Q1", "P587", ["300000001"], ["Ship one.jpg"]))
        ents.update(entity("Q2", "P587", ["300000999"], ["Other.jpg"]))      # not asked
        ents.update(entity("Q3", "P587", ["300000003"], []))                 # no image
        ents.update(entity("Q4", "P587", ["300000004"], ["Old.jpg"], deprecated=True))
        self.assertEqual(vp.files_by_number(ents, vp.MMSI, ["300000001", "300000003", "300000004"]),
                         {"300000001": "Ship one.jpg"})
        # an MMSI item is not read as an IMO one
        self.assertEqual(vp.files_by_number(ents, vp.IMO, ["300000001"]), {})

    def test_commons_page_to_card_shape(self):
        page = {"title": "File:Ship one.jpg", "imageinfo": [{
            "thumburl": "//upload.example.org/thumb/ship.jpg?utm_source=x", "descriptionurl": "http://commons.example.org/f",
            "thumbwidth": 320, "thumbheight": 213, "extmetadata": {
                "Artist": {"value": '<a href="//x">Ann &amp; Bo</a>'}, "LicenseShortName": {"value": "CC BY 4.0"},
                "LicenseUrl": {"value": "https://licence.example.org/by4"}}}]}
        self.assertEqual(vp.photo_from_imageinfo(page), {
            "url": "https://upload.example.org/thumb/ship.jpg", "page": "https://commons.example.org/f",
            "credit": "Ann & Bo", "licence": "CC BY 4.0", "licence_url": "https://licence.example.org/by4",
            "source": "Wikimedia Commons", "width": 320, "height": 213})
        self.assertIsNone(vp.photo_from_imageinfo({"imageinfo": [{"thumburl": "x"}]}))

    def test_commons_titles_mapped_back_through_normalisation(self):
        data = {"query": {"normalized": [{"from": "File:Ship_one.jpg", "to": "File:Ship one.jpg"}],
                          "pages": {"1": {"title": "File:Ship one.jpg", "imageinfo": [{
                              "thumburl": "https://u.example.org/t.jpg", "descriptionurl": "https://c.example.org/p"}]}}}}
        out = vp.photos_from_commons(data, {"File:Ship_one.jpg": "300000001"}, vp.MMSI)
        self.assertEqual(list(out), ["mmsi:300000001"])


class Fallback(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.book = vp.PhotoBook("/nonexistent/x.json", clock=self.clock)
        self.called = []
        self.cb = lambda: self.called.append(1)

    def test_imo_first_mmsi_not_asked_until_imo_answers(self):
        self.assertIsNone(self.book.photo("9000001", "300000001", self.cb))
        self.assertEqual(self.book.take_batch(), ["imo:9000001"])
        self.assertEqual(self.book.take_batch(), [])                       # MMSI not queued yet

    def test_imo_with_a_photo_wins_and_mmsi_is_never_asked(self):
        self.book.photo("9000001", "300000001", self.cb)
        batch = self.book.take_batch()
        for cb in self.book.settle(batch, {"imo:9000001": PHOTO_A}):
            cb()
        self.assertEqual(self.called, [1])
        self.assertEqual(self.book.photo("9000001", "300000001", self.cb), PHOTO_A)
        self.assertEqual(self.book.take_batch(), [])

    def test_imo_none_falls_back_to_mmsi(self):
        self.book.photo("9000001", "300000001", self.cb)
        for cb in self.book.settle(self.book.take_batch(), {}):            # IMO: no photo
            cb()
        self.assertIsNone(self.book.photo("9000001", "300000001", self.cb))  # the entity asks again
        self.assertEqual(self.book.take_batch(), ["mmsi:300000001"])
        self.book.settle(["mmsi:300000001"], {"mmsi:300000001": PHOTO_B})
        self.assertEqual(self.book.photo("9000001", "300000001"), PHOTO_B)

    def test_mmsi_only_vessel(self):
        self.book.photo(None, "300000001", self.cb)
        self.assertEqual(self.book.take_batch(), ["mmsi:300000001"])

    def test_nothing_without_a_valid_number(self):
        self.assertIsNone(self.book.photo(None, None, self.cb))
        self.assertIsNone(self.book.photo("12", "abc", self.cb))
        self.assertEqual(self.book.take_batch(), [])

    def test_none_answer_asked_again_after_14_days_found_after_30(self):
        self.book.settle(["mmsi:300000001", "mmsi:300000002"], {"mmsi:300000002": PHOTO_B})
        self.clock.t += vp.NONE_TTL_S + 1
        self.book.photo(None, "300000001")
        self.assertEqual(self.book.photo(None, "300000002"), PHOTO_B)
        self.assertEqual(self.book.take_batch(), ["mmsi:300000001"])
        self.clock.t += vp.FOUND_TTL_S
        self.assertEqual(self.book.photo(None, "300000002"), PHOTO_B)        # stale photo shown while re-asked
        self.assertEqual(self.book.take_batch(), ["mmsi:300000002"])

    def test_batches_are_one_kind(self):
        self.book.photo("9000001")
        self.book.photo(None, "300000001")
        self.book.photo("9000002")
        self.assertEqual(self.book.take_batch(), ["imo:9000001", "imo:9000002"])
        self.assertEqual(self.book.take_batch(), ["mmsi:300000001"])

    def test_batch_size(self):
        for i in range(vp.BATCH + 3):
            self.book.photo(None, "3%08d" % i)
        self.assertEqual(len(self.book.take_batch()), vp.BATCH)
        self.assertEqual(len(self.book.take_batch()), 3)

    def test_forget_waiter(self):
        self.book.photo("9000001", None, self.cb)
        self.book.forget_waiter(self.cb)
        for cb in self.book.settle(self.book.take_batch(), {}):
            cb()
        self.assertEqual(self.called, [])

    def test_requeue_after_a_failure(self):
        self.book.photo(None, "300000001")
        batch = self.book.take_batch()
        self.book.requeue(batch)
        self.assertEqual(self.book.take_batch(), ["mmsi:300000001"])


class Disk(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_save_and_load_round_trip(self):
        a = vp.PhotoBook(self.path(vp.CACHE_NAME))
        a.settle(["mmsi:300000001", "imo:9000001"], {"mmsi:300000001": PHOTO_A})
        a.save()
        b = vp.PhotoBook(self.path(vp.CACHE_NAME))
        b.load()
        self.assertEqual(b.photo(None, "300000001"), PHOTO_A)
        self.assertEqual(sorted(b._cache), ["imo:9000001", "mmsi:300000001"])

    def test_the_old_ships_cache_is_read_once_and_never_written(self):
        legacy = self.path(vp.LEGACY_CACHE_NAME)
        old = {"9000001": {"t": 1.0, "photo": PHOTO_A}, "junk": {"t": 1.0}, "9000002": "x"}
        with open(legacy, "w", encoding="utf-8") as f:
            json.dump(old, f)
        before = open(legacy, "rb").read()
        book = vp.PhotoBook(self.path(vp.CACHE_NAME), legacy_path=legacy)
        book.load()
        self.assertEqual(list(book._cache), ["imo:9000001"])
        book.save()
        self.assertEqual(open(legacy, "rb").read(), before)
        self.assertTrue(os.path.exists(self.path(vp.CACHE_NAME)))
        # with its own cache present, the old one is not read again
        with open(legacy, "w", encoding="utf-8") as f:
            json.dump({"9000005": {"t": 1.0, "photo": PHOTO_B}}, f)
        again = vp.PhotoBook(self.path(vp.CACHE_NAME), legacy_path=legacy)
        again.load()
        self.assertEqual(list(again._cache), ["imo:9000001"])

    def test_a_bad_cache_starts_empty(self):
        with open(self.path(vp.CACHE_NAME), "w") as f:
            f.write("{not json")
        book = vp.PhotoBook(self.path(vp.CACHE_NAME))
        book.load()
        self.assertEqual(book._cache, {})

    def test_shared_one_per_instance_neutral_key_new_file(self):
        store = {}
        started = []

        async def jobs(fn, *a):
            return fn(*a)

        def start(coro):
            started.append(coro)
            coro.close()
            return "task"

        async def go():
            a = await vp.shared(store, self.dir, jobs, start, None)
            b = await vp.shared(store, self.dir, jobs, start, None)
            return a, b

        a, b = asyncio.run(go())
        self.assertIs(a, b)
        self.assertEqual(len(started), 1)
        self.assertEqual(list(store), ["saw_vessel_photo_book"])
        self.assertNotEqual(vp.STORE_KEY, "saw_vessel_photos")      # Ships' old key: never shared with it
        self.assertNotEqual(vp.CACHE_NAME, vp.LEGACY_CACHE_NAME)
        self.assertEqual(a.path, os.path.join(self.dir, vp.CACHE_NAME))


class Resp:
    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return self.data


class Session:
    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.asked.append((url, params, headers))
        return Resp(self.answers.pop(0))


PAGE = {"imageinfo": [{"thumburl": "https://u.example.org/ship.jpg", "descriptionurl": "https://c.example.org/ship",
                       "extmetadata": {"Artist": {"value": "Ann Example"}, "LicenseShortName": {"value": "CC0"}}}]}


class Network(unittest.TestCase):
    def test_three_requests_by_mmsi(self):
        ents = {}
        ents.update(entity("Q1", "P587", ["300000001"], ["Ship one.jpg"]))
        ents.update(entity("Q2", "P587", ["300000002"], ["Ship two.jpg"]))
        s = Session([{"query": {"search": [{"title": "Q1"}, {"title": "Q2"}]}}, {"entities": ents},
                     {"query": {"pages": {"1": dict(PAGE, title="File:Ship one.jpg"),
                                          "2": {"title": "File:Ship two.jpg", "missing": ""}}}}])
        found = asyncio.run(vp.PhotoBook("x").lookup(s, ["mmsi:300000001", "mmsi:300000002"]))
        self.assertEqual(list(found), ["mmsi:300000001"])                  # a missing Commons file is no photo
        self.assertEqual([a[0].split("/")[2] for a in s.asked],
                         ["www.wikidata.org", "www.wikidata.org", "commons.wikimedia.org"])
        self.assertEqual(s.asked[0][1]["srsearch"], "haswbstatement:P587=300000001|P587=300000002")
        self.assertTrue(all(a[2]["User-Agent"] == vp.USER_AGENT for a in s.asked))

    def test_by_imo_uses_p458(self):
        s = Session([{"query": {"search": []}}])
        self.assertEqual(asyncio.run(vp.PhotoBook("x").lookup(s, ["imo:9000001"])), {})
        self.assertEqual(len(s.asked), 1)                                   # no hits: one request
        self.assertEqual(s.asked[0][1]["srsearch"], "haswbstatement:P458=9000001")


class Wiring(unittest.TestCase):
    def test_user_agent(self):
        self.assertIn("saw-livetrack/", vp.USER_AGENT)
        self.assertIn("https://github.com/StrausbergAutomationWorks/LiveTrackCore", vp.USER_AGENT)
        self.assertEqual(inspect.getsource(vp.PhotoBook.lookup).count('headers=headers'), 3)

    def test_no_query_service_and_no_image_download(self):
        src = inspect.getsource(vp)
        self.assertNotIn("query.wikidata.org", src)
        self.assertEqual(src.count("session.get("), 3)               # search, entities, imageinfo: JSON only

    def test_pure(self):
        src = inspect.getsource(vp)
        self.assertNotIn("import homeassistant", src)
        self.assertNotIn("from homeassistant", src)


if __name__ == "__main__":
    unittest.main()
