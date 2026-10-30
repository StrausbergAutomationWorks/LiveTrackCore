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


class Charter(unittest.TestCase):
    """0.2.16 (backlog 313): the ICE Air charter status."""
    ROWS = ROWS + [{"hex": "A2C4E0", "status": "ice_air_charter", "entry": "charter-n278gx", "registration": "N278GX"}]

    def test_names(self):
        self.assertEqual(el.CHARTER, "ice_air_charter")
        self.assertEqual(el.WATCHED, (el.SOURCED, el.DETECTED, el.CHARTER))
        self.assertEqual(el.CHARTER_LABEL, "ICE Air Charter?")

    def test_parsed(self):
        entries, _ = el.parse(doc(self.ROWS))
        self.assertEqual(entries["a2c4e0"], el.Entry("a2c4e0", el.CHARTER, None, "charter-n278gx", "N278GX"))

    def test_watched_not_sourced(self):
        with tempfile.TemporaryDirectory() as d:
            r = el.EnforcementList(os.path.join(d, el.CACHE_NAME))
            r._install(doc(self.ROWS))
        self.assertEqual(r.hexes(*el.WATCHED), frozenset({"a00808", "a1b2c3", "abc123", "a2c4e0"}))
        self.assertEqual(r.status("A2C4E0"), el.CHARTER)
        self.assertFalse(r.is_sourced("a2c4e0"))
        self.assertNotIn("a2c4e0", r.sourced())

    def test_unknown_status_is_installed(self):
        entries, _ = el.parse(doc(ROWS + [{"hex": "aaaaaa", "status": "some_later_status"}]))
        self.assertEqual(entries["aaaaaa"].status, "some_later_status")


class IceAirCallsign(unittest.TestCase):
    """0.2.18 (backlog 313 part 2): ICE Air's call signs mark the flight. Cases from the TYSON watch, 2026-10-29."""

    def test_label(self):
        self.assertEqual(el.CHARTER_FLIGHT_LABEL, "ICE Air Charter")
        self.assertEqual(el.CHARTER_LABEL, el.CHARTER_FLIGHT_LABEL + "?")

    def test_velux_seen_on_the_box(self):
        for cs, hx in (("VELUX55", "ad563d"), ("VELUX21", "AD7274"), ("VELUX32", "a8b479"), ("VELUX46", "a2c4e0")):
            self.assertEqual(el.ice_air_callsign(cs, hx), "VELUX", cs)

    def test_tyson_and_tys(self):
        self.assertEqual(el.ice_air_callsign("TYSON43", "a8b479"), "TYSON")
        self.assertEqual(el.ice_air_callsign("TYSON51", "a8b479"), "TYSON")
        self.assertEqual(el.ice_air_callsign("TYS44", "a8b479"), "TYS")
        self.assertEqual(el.ice_air_callsign("TYS1", "a8b479"), "TYS")
        self.assertEqual(el.ice_air_callsign("TYS1234", "a8b479"), "TYS")

    def test_trumps_757_never(self):
        self.assertIsNone(el.ice_air_callsign("TYSON1", "aa3410"))
        self.assertIsNone(el.ice_air_callsign("TYSON12", "AA3410"))   # the hex alone excludes it
        self.assertIsNone(el.ice_air_callsign("TYSON1", "a8b479"))    # one digit is not the ICE form

    def test_us_hex_only(self):
        self.assertIsNone(el.ice_air_callsign("TYS44", "e48a31"))     # Brazil: TransHolding's TYS
        self.assertIsNone(el.ice_air_callsign("VELUX21", "c03a11"))   # Canada
        self.assertEqual(el.ice_air_callsign("VELUX21", "a00000"), "VELUX")
        self.assertEqual(el.ice_air_callsign("VELUX21", "afffff"), "VELUX")
        self.assertIsNone(el.ice_air_callsign("VELUX21", "b00000"))
        self.assertIsNone(el.ice_air_callsign("VELUX21", "~a2c4e0"))  # TIS-B, not a Mode S address
        self.assertIsNone(el.ice_air_callsign("VELUX21", None))

    def test_carrier_designators_and_oddities_are_not_evidence(self):
        for cs in ("GXA6276", "GXA201", "BBQ3501", "GXAUX41", "UX23", "VELUX", "VELUX1", "VELUX123", "TYSON",
                   "TYSON123", "TYS12345", "TROY704", "", None):
            self.assertIsNone(el.ice_air_callsign(cs, "a8b479"), cs)

    def test_case_and_padding(self):
        self.assertEqual(el.ice_air_callsign(" velux21 ", "ad7274"), "VELUX")


if __name__ == "__main__":
    unittest.main()
