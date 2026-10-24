"""Tests for saw_livetrack.enforcement_list (backlog 299, 0.2.12). Offline: a fake session.

Run: python -m unittest tests.test_enforcement_list   (or discover -p "test_enforcement_list.py")
"""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from saw_livetrack import enforcement_list as el  # noqa: E402


def doc(rows, built="2026-10-24T04:04:22Z", schema=1, name="EnforcementAircraft"):
    return json.dumps({"name": name, "schema": schema, "built": built, "license": "ODbL-1.0",
                       "attribution": "Contains information from EnforcementAircraft", "aircraft": rows}).encode()


ROWS = [
    {"hex": "a00808", "status": "sourced", "agency": "DHS", "entry": "us-federal-law-enforcement", "registration": "N1007G"},
    {"hex": "A1B2C3", "status": "sourced", "agency": "TX DPS", "entry": "us-tx-dps"},
    {"hex": "abc123", "status": "detected_unconfirmed", "agency": None},
    {"hex": "def456", "status": "cleared", "agency": "HOMELAND AVIATION LLC"},
    {"hex": "~a99999", "status": "sourced"},          # TIS-B form: skipped
    {"hex": "a0080", "status": "sourced"},            # five digits: skipped
    {"hex": "a77777"},                                # no status: skipped
    "not a row",
]


def sums(raw, name=el.FILE_NAME):
    return ("%s  %s\n" % (hashlib.sha256(raw).hexdigest(), name)).encode()


class Resp:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def read(self):
        return self.body


class Session:
    """{url: (status, body) or an exception}; records every URL asked."""

    def __init__(self, routes):
        self.routes, self.asked = routes, []

    def get(self, url):
        self.asked.append(url)
        r = self.routes[url]
        if isinstance(r, BaseException):
            raise r
        return Resp(*r)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def run_sync(func, *args):
    return func(*args)



def slurp(p):
    with open(p, "rb") as fh:
        return fh.read()


def spit(p, raw):
    with open(p, "wb") as fh:
        fh.write(raw)

def run(coro):
    return asyncio.run(coro)


class Parse(unittest.TestCase):
    def test_rows(self):
        entries, header = el.parse(doc(ROWS))
        self.assertEqual(sorted(entries), ["a00808", "a1b2c3", "abc123", "def456"])
        self.assertEqual(entries["a00808"], el.Entry("a00808", "sourced", "DHS", "us-federal-law-enforcement", "N1007G"))
        self.assertEqual(header["built"], "2026-10-24T04:04:22Z")
        self.assertEqual(header["license"], "ODbL-1.0")

    def test_refuses_other_files(self):
        for raw in (b"not json", b"[]", doc(ROWS, name="FiledRoutesDB"), doc(ROWS, schema=2),
                    json.dumps({"name": "EnforcementAircraft", "schema": 1}).encode(), b"\xff\xfe"):
            with self.assertRaises(ValueError, msg=raw[:40]):
                el.parse(raw)

    def test_hex(self):
        self.assertEqual(el.norm_hex(" A00808 "), "a00808")
        for bad in ("~a00808", "a0080", "a008089", "g00808", None, 12345):
            self.assertIsNone(el.norm_hex(bad))

    def test_sums(self):
        text = "aa" * 32 + "  other.json\n" + "BB" * 32 + " *enforcementaircraft.json\n"
        self.assertEqual(el.sha_from_sums(text), "bb" * 32)
        self.assertIsNone(el.sha_from_sums("aa" * 32 + "  other.json\n"))
        self.assertIsNone(el.sha_from_sums(""))


class Refresh(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, el.CACHE_NAME)
        self.clock = Clock()
        self.raw = doc(ROWS)

    def tearDown(self):
        self.dir.cleanup()

    def reader(self):
        return el.EnforcementList(self.path, clock=self.clock)

    def session(self, raw=None, sums_body=None, **over):
        raw = self.raw if raw is None else raw
        routes = {el.SUMS_URL: (200, sums(raw) if sums_body is None else sums_body), el.LIST_URL: (200, raw)}
        routes.update(over)
        return Session(routes)

    def test_first_check_installs_and_caches(self):
        r, s = self.reader(), self.session()
        self.assertEqual(r.summary, "no list")
        self.assertEqual(run(r.refresh(s, run_sync)), "updated")
        self.assertEqual(s.asked, [el.SUMS_URL, el.LIST_URL])
        self.assertEqual(slurp(self.path), self.raw)
        self.assertFalse(os.path.exists(self.path + ".new"))
        self.assertEqual(r.sourced(), frozenset({"a00808", "a1b2c3"}))
        self.assertTrue(r.is_sourced("A1B2C3") and r.is_sourced("a00808"))
        self.assertFalse(r.is_sourced("abc123") or r.is_sourced("def456") or r.is_sourced("~a00808") or r.is_sourced(None))
        self.assertEqual(r.status("abc123"), el.DETECTED)
        self.assertEqual(r.status("def456"), el.CLEARED)
        self.assertIsNone(r.status("ffffff"))
        self.assertEqual(r.hexes(el.DETECTED), frozenset({"abc123"}))
        self.assertEqual(len(r.hexes()), 4)
        self.assertEqual(r.summary, "2 sourced of 4, built 2026-10-24T04:04:22Z")

    def test_throttle_then_sums_only(self):
        r, s = self.reader(), self.session()

        async def go():
            a = await r.refresh(s, run_sync)
            b = await r.refresh(s, run_sync)              # at once: nothing asked
            self.clock.t += el.REFRESH_S - 1
            c = await r.refresh(s, run_sync)
            self.clock.t += 2
            d = await r.refresh(s, run_sync)              # due: SHA256SUMS only, same hash
            e = await r.refresh(s, run_sync, force=True)
            return a, b, c, d, e
        self.assertEqual(run(go()), ("updated", "fresh", "fresh", "unchanged", "unchanged"))
        self.assertEqual(s.asked, [el.SUMS_URL, el.LIST_URL, el.SUMS_URL, el.SUMS_URL])

    def test_new_list_replaces(self):
        r = self.reader()
        run(r.refresh(self.session(), run_sync))
        new = doc(ROWS + [{"hex": "a55555", "status": "sourced"}], built="2026-10-25T04:00:00Z")
        self.assertEqual(run(r.refresh(self.session(raw=new), run_sync, force=True)), "updated")
        self.assertTrue(r.is_sourced("a55555"))
        self.assertEqual(slurp(self.path), new)

    def test_faults_keep_the_list(self):
        r = self.reader()
        run(r.refresh(self.session(), run_sync))
        new = doc([{"hex": "a55555", "status": "sourced"}])
        cases = [
            (self.session(raw=new, sums_body=sums(b"something else")), "failed: checksum mismatch"),
            (self.session(**{el.SUMS_URL: (404, b"")}), "failed: HTTP 404"),
            (self.session(raw=new, **{el.LIST_URL: (502, b"")}), "failed: HTTP 502"),
            (self.session(**{el.SUMS_URL: asyncio.TimeoutError()}), "failed: timeout"),
            (self.session(**{el.SUMS_URL: ConnectionResetError()}), "failed: ConnectionResetError"),
            (self.session(sums_body=b"nothing here\n"), "failed: SHA256SUMS names no enforcementaircraft.json"),
            (self.session(raw=b"{}"), "failed: bad file: not the EnforcementAircraft list"),
        ]
        for s, want in cases:
            self.assertEqual(run(r.refresh(s, run_sync, force=True)), want)
            self.assertEqual(r.sourced(), frozenset({"a00808", "a1b2c3"}), want)
            self.assertEqual(slurp(self.path), self.raw, want)
        self.assertEqual(r.summary, "2 sourced of 4, built 2026-10-24T04:04:22Z (failed: bad file: not the EnforcementAircraft list)")

    def test_retry_sooner_after_a_failure(self):
        r = self.reader()
        run(r.refresh(self.session(**{el.SUMS_URL: (500, b"")}), run_sync))
        self.assertEqual(r.summary, "no list (failed: HTTP 500)")
        self.clock.t += el.RETRY_S - 1
        self.assertFalse(r.due())
        self.clock.t += 1
        self.assertTrue(r.due())

    def test_cache(self):
        r = self.reader()
        self.assertFalse(r.load_cache())                   # none yet
        spit(self.path, self.raw)
        r = self.reader()
        self.assertTrue(r.load_cache())
        self.assertTrue(r.is_sourced("a00808"))
        self.assertEqual(r.sha, hashlib.sha256(self.raw).hexdigest())
        s = self.session()
        self.assertEqual(run(r.refresh(s, run_sync)), "unchanged")   # the cache's hash is current
        self.assertEqual(s.asked, [el.SUMS_URL])
        spit(self.path, b"{broken")
        r = self.reader()
        self.assertFalse(r.load_cache())
        self.assertEqual(r.sourced(), frozenset())

    def test_default_executor(self):
        r = self.reader()
        self.assertEqual(run(r.refresh(self.session())), "updated")
        self.assertTrue(os.path.exists(self.path))


class Shape(unittest.TestCase):
    def test_one_reader_per_store(self):
        store = {}
        a = el.shared(store, "/x/" + el.CACHE_NAME)
        self.assertIs(el.shared(store, "/x/" + el.CACHE_NAME), a)
        self.assertIs(store[el.STORE_KEY], a)

    def test_names(self):
        self.assertTrue(el.CACHE_NAME.startswith("saw_") and el.STORE_KEY.startswith("saw_")
                        and el.SESSION_KEY.startswith("saw_"))
        self.assertEqual(el.SESSION_KWARGS, {"max_line_size": 32768, "max_field_size": 32768})
        # the cadence, pinned: a good check holds 6 h, a failed one 15 min, a request 60 s
        self.assertEqual((el.REFRESH_S, el.RETRY_S, el.TIMEOUT_S), (21600, 900, 60))
        self.assertEqual(el.LIST_URL, "https://github.com/StrausbergAutomationWorks/EnforcementAircraft/releases/download/data/enforcementaircraft.json")

    def test_nothing_of_filedroutesdb(self):
        src = slurp(el.__file__).decode("utf-8")
        code = src.split('"""', 2)[2]                      # past the module docstring
        for word in ("FiledRoutesDB", "filedroutes", "frdb", "github_frdb", "routes"):
            self.assertNotIn(word.lower(), code.lower(), word)
        self.assertNotIn("homeassistant", src)
        self.assertNotIn("import aiohttp", src)


if __name__ == "__main__":
    unittest.main()
