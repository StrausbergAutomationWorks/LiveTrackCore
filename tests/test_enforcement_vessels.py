"""Tests for saw_livetrack.enforcement_vessels, the EnforcementVessels reader (backlog 315, 0.2.20; moved from Live
Track SALT with its tests). Offline: a fake session and a temporary cache.

Run: python -m unittest tests.test_enforcement_vessels
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

from saw_livetrack import enforcement_vessels as V  # noqa: E402


def doc(**over):
    d = {
        "name": "EnforcementVessels", "schema": 1, "built": "2026-10-30T07:24:55Z", "license": "ODbL-1.0",
        "theatres": [{"id": "straits", "name": "Florida Straits", "box": [27.5, -79.0, 22.0, -83.5]},
                     {"id": "bad", "name": "bad", "box": [1, 2, 3]}],
        "entries": [{"id": "uscg-active", "short": "USCG"}],
        "vessels": [
            {"mmsi": "367269000", "name": "USCGC Active", "agency": "US Coast Guard", "hull": "WMEC 618",
             "entry": "uscg-active", "status": "sourced"},
            {"mmsi": "338926402", "name": "USCGC Richard Etheridge", "agency": "US Coast Guard", "hull": None,
             "entry": "uscg-re", "status": "sourced"},
            {"mmsi": "366999999", "name": "Cleared", "agency": "US Coast Guard", "entry": "x", "status": "cleared"},
            {"mmsi": "12345", "status": "sourced"},
            "junk",
        ],
    }
    d.update(over)
    return json.dumps(d).encode()


def reader():
    r = V.VesselList(os.path.join(tempfile.mkdtemp(), V.CACHE_NAME))
    r._install(doc())
    return r


class Reader(unittest.TestCase):
    def test_parse(self):
        vessels, theatres, header = V.parse(doc())
        self.assertEqual(sorted(vessels), ["338926402", "366999999", "367269000"])   # the 5-digit row and junk skipped
        self.assertEqual(vessels["367269000"].short, "USCG")
        self.assertEqual(vessels["338926402"].short, None)
        self.assertEqual([t.id for t in theatres], ["straits"])                    # the 3-number box skipped
        t = theatres[0]
        self.assertEqual((t.lat_min, t.lon_min, t.lat_max, t.lon_max), (22.0, -83.5, 27.5, -79.0))   # corners sorted
        self.assertEqual(header["built"], "2026-10-30T07:24:55Z")

    def test_a_row_carries_what_a_consumer_draws(self):
        v = reader().vessel(367269000)
        self.assertEqual((v.mmsi, v.status, v.name, v.agency, v.short, v.hull, v.entry),
                         ("367269000", "sourced", "USCGC Active", "US Coast Guard", "USCG", "WMEC 618", "uscg-active"))
        self.assertIsNone(reader().vessel("12345"))
        self.assertIsNone(reader().vessel("211000000"))

    def test_unknown_status_installed_as_given(self):
        vessels, _t, _h = V.parse(doc(vessels=[{"mmsi": "367000002", "status": "newstatus"},
                                               {"mmsi": "367000003", "status": ""}]))
        self.assertEqual({m: v.status for m, v in vessels.items()}, {"367000002": "newstatus"})

    def test_refuses_other_files(self):
        for bad in (b"{", json.dumps({"name": "EnforcementAircraft", "schema": 1}).encode(),
                    doc(schema=2), doc(vessels=None), doc(name="EnforcementAircraft")):
            with self.assertRaises(ValueError):
                V.parse(bad)

    def test_watched_never_cleared(self):
        self.assertEqual(reader().watched(), frozenset({"367269000", "338926402"}))

    def test_theatre(self):
        r = reader()
        self.assertEqual(r.theatre(24.5, -81.8).id, "straits")
        self.assertIsNone(r.theatre(30.4, -87.2))          # off Pensacola: outside every theatre
        self.assertIsNone(r.theatre(None, -81.8))
        self.assertEqual(r.theatre(22.0, -83.5).id, "straits")   # the edge is inside

    def test_summary(self):
        self.assertTrue(reader().summary.startswith("2 sourced of 3, 1 theatres, built 2026-10-30T07:24:55Z"))
        self.assertEqual(V.VesselList("x").summary, "no list")

    def test_sha_from_sums(self):
        h = "ab" * 32
        self.assertEqual(V.sha_from_sums("%s  other.json\n%s *%s\n" % ("cd" * 32, h.upper(), V.FILE_NAME)), h)
        self.assertIsNone(V.sha_from_sums("%s  other.json\n" % h))

    def test_shared_is_one_per_store(self):
        store = {}
        a = V.shared_list(store, "x")
        self.assertIs(V.shared_list(store, "y"), a)
        self.assertIs(store[V.STORE_KEY], a)
        self.assertEqual(V.CACHE_NAME, "saw_enforcementvessels.json")   # SAW-neutral, never an integration's name

    def test_real_release_parses_if_present(self):
        p = r"C:\Users\chica\bin\enforcement_vessels\release\enforcementvessels.json"
        if not os.path.exists(p):
            self.skipTest("no local release")
        with open(p, "rb") as fh:
            vessels, theatres, _h = V.parse(fh.read())
        self.assertEqual(len(vessels), 41)
        self.assertEqual(len(theatres), 4)


class FakeResp:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def read(self):
        return self.body


class FakeSession:
    def __init__(self, files):
        self.files, self.asked = files, []

    def get(self, url):
        self.asked.append(url.rsplit("/", 1)[-1])
        status, body = self.files.get(url.rsplit("/", 1)[-1], (404, b""))
        return FakeResp(status, body)


async def _sync(func, *args):
    return func(*args)


def sums_for(raw):
    return ("%s  %s\n" % (hashlib.sha256(raw).hexdigest(), V.FILE_NAME)).encode()


class Refresh(unittest.TestCase):
    def run_refresh(self, r, files, force=True):
        s = FakeSession(files)
        return asyncio.run(r.refresh(s, _sync, force=force)), s.asked

    def test_updated_then_unchanged_and_cached(self):
        raw = doc()
        r = V.VesselList(os.path.join(tempfile.mkdtemp(), V.CACHE_NAME))
        res, asked = self.run_refresh(r, {V.SUMS_NAME: (200, sums_for(raw)), V.FILE_NAME: (200, raw)})
        self.assertEqual((res, asked), ("updated", [V.SUMS_NAME, V.FILE_NAME]))
        res, asked = self.run_refresh(r, {V.SUMS_NAME: (200, sums_for(raw)), V.FILE_NAME: (200, raw)})
        self.assertEqual((res, asked), ("unchanged", [V.SUMS_NAME]))
        r2 = V.VesselList(r.cache_path)
        self.assertTrue(r2.load_cache())
        self.assertEqual(r2.watched(), r.watched())

    def test_a_bad_download_keeps_what_is_there(self):
        r = reader()
        before = r.watched()
        sums = ("%s  %s\n" % ("0" * 64, V.FILE_NAME)).encode()
        res, _ = self.run_refresh(r, {V.SUMS_NAME: (200, sums), V.FILE_NAME: (200, doc(vessels=[]))})
        self.assertEqual(res, "failed: checksum mismatch")
        self.assertEqual(r.watched(), before)
        res, _ = self.run_refresh(r, {})
        self.assertEqual(res, "failed: HTTP 404")
        self.assertEqual(r.watched(), before)
        self.assertTrue(r.summary.endswith("(failed: HTTP 404)"))

    def test_a_file_that_matches_but_is_not_the_list_is_refused(self):
        r = reader()
        raw = json.dumps({"name": "EnforcementAircraft", "schema": 1}).encode()
        res, _ = self.run_refresh(r, {V.SUMS_NAME: (200, sums_for(raw)), V.FILE_NAME: (200, raw)})
        self.assertTrue(res.startswith("failed: bad file"), res)
        self.assertFalse(os.path.exists(r.cache_path))
        self.assertEqual(len(r.vessels), 3)

    def test_sums_naming_no_list(self):
        r = reader()
        res, asked = self.run_refresh(r, {V.SUMS_NAME: (200, b"%s  other.json\n" % (b"0" * 64))})
        self.assertEqual((res, asked), ("failed: SHA256SUMS names no %s" % V.FILE_NAME, [V.SUMS_NAME]))

    def test_fresh_is_not_asked_again(self):
        r = reader()
        r.checked_at, r.last_ok = r._clock(), True
        res, asked = self.run_refresh(r, {}, force=False)
        self.assertEqual((res, asked), ("fresh", []))

    def test_due_after_refresh_or_retry(self):
        t = [1000.0]
        r = V.VesselList("x", clock=lambda: t[0])
        self.assertTrue(r.due())
        r.checked_at, r.last_ok = 1000.0, True
        t[0] = 1000.0 + V.REFRESH_S - 1
        self.assertFalse(r.due())
        t[0] = 1000.0 + V.REFRESH_S
        self.assertTrue(r.due())
        r.last_ok = False
        t[0] = 1000.0 + V.RETRY_S
        self.assertTrue(r.due())
        t[0] = 1000.0 + V.RETRY_S - 1
        self.assertFalse(r.due())

    def test_bad_cache_is_not_installed(self):
        path = os.path.join(tempfile.mkdtemp(), V.CACHE_NAME)
        with open(path, "wb") as fh:
            fh.write(b"{")
        r = V.VesselList(path)
        self.assertFalse(r.load_cache())
        self.assertFalse(V.VesselList(path + ".none").load_cache())


class Look(unittest.TestCase):
    """0.2.21: the look of a listed vessel, moved from Live Track Ships' enforcement.py with its checks."""

    DOC = {"name": "EnforcementVessels", "schema": 1, "built": "b", "license": "ODbL-1.0",
           "theatres": [{"id": "s", "name": "Southern California", "box": [34.5, -121.0, 32.0, -117.0]}],
           "entries": [{"id": "uscg-wpc", "short": "USCG"}],
           "vessels": [{"mmsi": "338926432", "name": "USCGC Benjamin Bottoms (WPC 1132)", "agency": "US Coast Guard",
                        "hull": "WPC 1132", "entry": "uscg-wpc", "status": "sourced"},
                       {"mmsi": "366999662", "name": "USCGC Halibut", "agency": "US Coast Guard", "entry": "uscg-x",
                        "status": "newstatus"},
                       {"mmsi": "366999999", "name": "Gone", "agency": "US Coast Guard", "status": "cleared"},
                       {"mmsi": "367000001", "status": "sourced"}]}

    def setUp(self):
        self.r = V.VesselList(os.path.join(tempfile.mkdtemp(), V.CACHE_NAME))
        self.r._install(json.dumps(self.DOC).encode())
        self.bb = V.listed(self.r, "338926432")
        self.hal = V.listed(self.r, "366999662")
        self.bare = V.listed(self.r, "367000001")

    def test_listed(self):
        self.assertEqual(self.bb.mmsi, "338926432")
        self.assertIsNone(V.listed(self.r, "366999999"))          # cleared
        self.assertIsNone(V.listed(self.r, "211000000"))          # not on the list
        self.assertIsNone(V.listed(None, "338926432"))            # no reader, no listing
        self.assertIsNotNone(self.hal)                             # a status the reader does not know is listed

    def test_lights(self):
        self.assertEqual(V.lights(self.bb), "glow")
        self.assertIsNone(V.lights(self.hal))
        self.assertEqual(V.LIST_LIGHTS, "glow")

    def test_label_name_operator(self):
        self.assertEqual(V.label(self.bb), "USCG")
        self.assertEqual(V.label(self.hal), "US Coast Guard")
        self.assertEqual(V.label(self.bare), "Listed vessel")
        self.assertEqual(V.name(self.bb, "CG BOTTOMS"), "USCGC Benjamin Bottoms (WPC 1132)")
        self.assertEqual(V.name(self.bare, "  CG NONAME "), "CG NONAME")
        self.assertIsNone(V.name(self.bare, "  "))
        self.assertIsNone(V.name(self.bare, None))
        self.assertEqual(V.operator_short(self.bb), "USCG")
        self.assertEqual(V.operator_short(self.hal), "US Coast Guard")
        self.assertIsNone(V.operator_short(self.bare))
        long_v = V.Vessel("367000002", "sourced", "X", "Customs and Border Protection Air and Marine")
        self.assertEqual(V.operator_short(long_v), "Customs and Border Pr\u2026")
        self.assertEqual(V.label(long_v), "Customs and Border Pr\u2026")
        self.assertEqual(len(V.label(long_v)), V.MAX_LABEL_CHARS)
        exact = V.Vessel("367000003", "sourced", "X", "A" * 22)
        self.assertEqual(V.label(exact), "A" * 22)                 # 22 is kept whole
        self.assertEqual(V.operator_short(exact), "A" * 22)
        self.assertEqual((V.MAX_LABEL_CHARS, V.OPERATOR_SHORT_MAX), (22, 22))

    def test_attributes(self):
        self.assertEqual(V.attributes(self.bb, "CG BOTTOMS"), {
            "enforcement_status": "sourced", "enforcement_agency": "US Coast Guard",
            "enforcement_entry": "uscg-wpc", "hull": "WPC 1132",
            "list_name": "USCGC Benjamin Bottoms (WPC 1132)",
            "name": "USCGC Benjamin Bottoms (WPC 1132)", "operator_short": "USCG"})
        self.assertEqual(sorted(V.attributes(self.bare, None)), ["enforcement_status"])
        self.assertEqual(V.attributes(self.bare, "CG X")["name"], "CG X")

    def test_attributes_edges(self):
        """Mutation run 1: an empty string is unknown too, and the list name is published stripped."""
        blank = V.Vessel("367000004", "sourced", name="  USCGC Padded  ", agency="", hull="", entry="")
        a = V.attributes(blank, None)
        self.assertEqual(a, {"enforcement_status": "sourced", "list_name": "USCGC Padded", "name": "USCGC Padded"})

    def test_attribution_and_tone(self):
        self.assertEqual(V.attribution("Data: aisstream.io"), "Data: aisstream.io; " + V.LIST_ATTRIBUTION)
        self.assertEqual(V.attribution(None), V.LIST_ATTRIBUTION)
        self.assertEqual(V.attribution(""), V.LIST_ATTRIBUTION)
        self.assertIn("Open Database License (ODbL) v1.0", V.LIST_ATTRIBUTION)
        self.assertIn("https://github.com/StrausbergAutomationWorks/EnforcementVessels", V.LIST_ATTRIBUTION)
        from saw_livetrack import aircraft_icons as AI
        self.assertEqual(V.LIST_TONE, "mil")
        self.assertIn(V.LIST_TONE, AI._TONES)


if __name__ == "__main__":
    unittest.main()
