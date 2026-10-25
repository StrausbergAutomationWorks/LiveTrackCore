"""Tests for saw_livetrack.faa_registry (backlog 300, 0.2.15). Offline: synthetic archives, a fake session.

The owner-name cases are Live Track Aircraft's own (tests/test_categorise.py, test_law.py, test_gov_table.py at
2aa4823), copied verbatim, so the moved code is held to the answers it gave there.
Run: python -m unittest tests.test_faa_registry
"""
import asyncio
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from saw_livetrack import faa_registry as fr  # noqa: E402

FED, LAW, FIRE, MIL, NAT, GOV, RES = ("Federal law enforcement", "Law enforcement", "Fire / EMS",
                                      "Military", "Natural resources", "Government", "Research / academic")
CG = "Coast Guard"
CASES = [
    ("DEPARTMENT OF HOMELAND SECURITY", FED), ("US DEPARTMENT OF HOMELAND SECURITY", FED),
    ("DEPT OF HOMELAND SECURITY", FED), ("DEPARMENT OF HOMELAND SECURITY", FED),
    ("UNITED STATES CUSTOMS AND BORDER PROTECTION", FED), ("US CUSTOMS & BORDER PROTECTION", FED),
    # not a federal operator (2026-10-23): a private LLC, no longer named by any rule
    ("HOMELAND AVIATION LLC", GOV),
    ("U S MARSHALS SERVICE", FED), ("U S MARSHALL SERV JUSTICE & ALIEN TRANS SYS(JPATS)", FED),
    ("US DEPARTMENT OF JUSTICE", FED), ("UNITED STATES COAST GUARD", "Coast Guard"),
    ("CALIFORNIA HIGHWAY PATROL", LAW), ("ORANGE COUNTY SHERIFFS OFFICE", LAW),
    ("MIAMI-DADE SHERIFF'S OFFICE", LAW), ("CHARLOTTE COUNTY SHERRIFS DEPARTMENT", LAW),
    ("SAN DIEGO COUNTY SHERRIFFS DEPARTMENT", LAW), ("LAPD AIR SUPPORT DIVISION", LAW),
    ("TEXAS DEPARTMENT OF PUBLIC SAFETY", LAW), ("ILLINOIS STATE POLICE", LAW),
    ("TENNESSEE DEPARTMENT OF SAFETY & HOMELAND SECURITY", LAW),
    ("STATE OF NEW HAMPSHIRE DEPT OF SAFETY", LAW), ("LEHIGH UNIVERSITY POLICE DEPARTMENT", LAW),
    # the false positives the substring keys produced
    ("USDA FOREST SERVICE FEPP", NAT), ("MINNESOTA CULTIVATED WILD RICE COUNCIL", GOV),
    ("SULLIVAN COUNTY OFFICE OF EMERGENCY MANAGEMENT", GOV), ("OFFICE OF AVIATION SERVICES", GOV),
    ("MARSHALL UNIVERSITY", RES), ("CITY OF MARSHALL", GOV),
    ("OFFICE OF LOUISIANA STATE FIRE MARSHAL", FIRE), ("OFFICE OF LOUISIANA STATE FIRE MARSHALL", FIRE),
    ("CALCASIEU PARISH POLICE JURY", GOV), ("COLUMBUS OFFICE OF HOMELAND SECURITY", GOV),
    ("NAVAL AIR SYSTEMS COMMAND", MIL), ("UNITED STATES NAVY", MIL), ("UNITED STATESAIR FORCE", MIL),
    ("BIOSYSTEMS ENGINEERING", GOV), ("GEORGIA FORESTRY COMMISSION", NAT),
    ("STATE OF ALASKA DEPT OF FISH & GAME", NAT), ("CLEMSON UNIVERSITY", RES),
]



class Categorise(unittest.TestCase):
    def test_real_owner_names(self):
        for owner, want in CASES:
            with self.subTest(owner=owner):
                self.assertEqual(fr.categorise(owner), want)

    def test_ice_is_never_a_substring(self):
        for owner in ("ICE MAN HOLDINGS", "TWO BAG ICE", "PRICE AVIATION", "OFFICE OF THE GOVERNOR"):
            self.assertNotIn("law enforcement", fr.categorise(owner).lower(), owner)

    def test_law_enforcement_set(self):
        self.assertEqual(fr.LAW_ENFORCEMENT, frozenset((LAW, FED, CG)))


class Label(unittest.TestCase):
    CASES = [
        ("DEPARTMENT OF HOMELAND SECURITY", FED, "DHS"),
        ("DEPARTMENT OF HOMELAND SECURITY US CUSTOMS &", FED, "CBP"),
        ("U S IMMIGRATION AND CUSTOMS ENFORCEMENT", FED, "ICE"),
        ("FEDERAL BUREAU OF INVESTIGATION", FED, "FBI"),
        ("U S MARSHALS SERVICE", FED, "USMS"),
        ("UNITED STATES COAST GUARD", CG, "USCG"),
        ("TEXAS DEPARTMENT OF PUBLIC SAFETY", LAW, "TX DPS"),
        ("CALIFORNIA HIGHWAY PATROL", LAW, "CA Hwy Patrol"),
        ("NEW YORK CITY POLICE DEPARTMENT", LAW, "NYPD"),
        ("LOS ANGELES POLICE DEPT AIR SUPPORT DIVISION", LAW, "LAPD"),
        ("UTAH COUNTY SHERIFF", LAW, "Utah Co Sheriff"),
        ("MISSISSIPPI COUNTY SHERIFFS DEPARTMENT", LAW, "Mississippi Co Sheriff"),
        ("MIAMI-DADE SHERIFFS OFFICE", LAW, "Miami-Dade Sheriff"),
        ("MARYLAND STATE POLICE AVIATION COMMAND", LAW, "MD State Police"),
        ("SAN BERNARDINO COUNTY SHERIFFS DEPT", LAW, "San Bernardino Sheriff"),
        ("TENNESSEE DEPARTMENT OF SAFETY & HOMELAND SECURITY", LAW, "TN DOS"),
        ("COUNTY OF MERCED SHERIFF'S DEPT", LAW, "Merced Co Sheriff"),
    ]

    def test_cases(self):
        for owner, cat, want in self.CASES:
            with self.subTest(owner=owner):
                self.assertEqual(fr.operator_short(owner, cat), want)

    def test_only_law_enforcement(self):
        self.assertIsNone(fr.operator_short("NAVAL AIR SYSTEMS COMMAND", MIL))
        self.assertIsNone(fr.operator_short("CITY OF CHICAGO", "Government"))
        self.assertIsNone(fr.operator_short("", LAW))

    def test_every_agency_code(self):
        for owner, want in (("ALCOHOL TOBACCO FIREARMS", "ATF"), ("ATF", "ATF"), ("DRUG ENFORCEMENT ADMIN", "DEA"),
                            ("UNITED STATES SECRET SERVICE", "USSS"), ("US DEPARTMENT OF JUSTICE", "DOJ"),
                            ("DEPT OF HOMELAND SECURITY", "DHS")):
            with self.subTest(owner=owner):
                self.assertEqual(fr.operator_short(owner, FED), want)

    def test_a_long_name_drops_co(self):
        out = fr.operator_short("SAN BERNARDINO COUNTY SHERIFFS DEPT", LAW)
        self.assertLessEqual(len(out), 22)


class GovLookup(unittest.TestCase):
    def table(self, rows):
        r = fr.GovRegistry()
        r._table = rows
        r._loaded = True
        return r

    def test_lookup(self):
        r = self.table({"A00001": ["N1", "U S CUSTOMS & BORDER PROTECTION", "TX"],
                        "A00002": ["N2", "ILLINOIS STATE POLICE", "IL"],
                        "A00003": ["N3", "NASA", "CA"]})
        a, b, c = (r.lookup(h) for h in ("A00001", "a00002", "A00003"))
        self.assertEqual(a, {"registration": "N1", "operator": "U S Customs & Border Protection",
                             "operator_category": FED, "operator_state": "TX",
                             "identified_by": "faa_government", "operator_short": "CBP"})
        self.assertEqual((b["identified_by"], b["operator_short"]), ("faa_government", "IL State Police"))
        self.assertEqual(c["operator_category"], "Research / academic")
        self.assertNotIn("operator_short", c)

    def test_outside_the_us_block_or_unloaded(self):
        r = self.table({"A00001": ["N1", "NASA", "CA"]})
        self.assertIsNone(r.lookup("C00001"))
        self.assertIsNone(r.lookup(None))
        self.assertIsNone(r.lookup("zzzzzz"))
        self.assertIsNone(fr.GovRegistry().lookup("A00001"))

    def test_watch_list_hexes(self):
        r = self.table({"A00002": ["N2", "ILLINOIS STATE POLICE", "IL"],
                        "A00001": ["N1", "UNITED STATES COAST GUARD", ""],
                        "A00004": ["N4", "DEPARTMENT OF HOMELAND SECURITY", "DC"],
                        "A00003": ["N3", "NASA", "CA"]})
        self.assertEqual(r.law_enforcement_hexes(), ["a00001", "a00002", "a00004"])
        self.assertEqual(r.federal_law_enforcement_hexes(), ["a00004"])
        r._table = {}
        self.assertEqual(r.law_enforcement_hexes(), ["a00001", "a00002", "a00004"])   # cached per table

    def test_load_never_raises(self):
        r = fr.GovRegistry()
        self.assertEqual(r.load(""), 0)
        self.assertEqual(r.load(os.path.join(tempfile.gettempdir(), "no_such_saw_faa.json")), 0)
        self.assertIsNone(r.lookup("A00001"))


class FeedFlags(unittest.TestCase):
    def test_military_no_table_row(self):
        mil, cat, x = fr.from_feed_flags(1, None, None)
        self.assertEqual((mil, cat), (True, "Military"))
        self.assertEqual(x, {"is_military": True, "operator_category": "Military", "identified_by": "feed_military"})

    def test_military_coast_guard_keeps_lights(self):
        mil, cat, x = fr.from_feed_flags(1, "UNITED STATES COAST GUARD", None)
        self.assertEqual(cat, CG)
        self.assertEqual(x["operator_short"], "USCG")
        self.assertEqual(x["operator_name_source"], "feed")

    def test_table_row_keeps_its_category(self):
        mil, cat, x = fr.from_feed_flags(1, "SOMETHING ELSE", {"operator_category": FED})
        self.assertEqual((cat, x), (FED, {"is_military": True}))

    def test_pia_and_others(self):
        self.assertEqual(fr.from_feed_flags(4, None, None), (False, None, {"identity_hidden": True}))
        for f in (0, 2, 8, None, -1, True):
            self.assertEqual(fr.from_feed_flags(f, "X", None), (False, None, {}), f)
        mil, _, x = fr.from_feed_flags(13, None, None)
        self.assertTrue(mil and x["identity_hidden"])


REF_COLS = ["CODE", "MFR", "MODEL", "TYPE-ACFT", "NO-SEATS", "TYPE-ENG", "AC-WEIGHT"]
MASTER_COLS = ["N-NUMBER", "MFR MDL CODE", "TYPE REGISTRANT", "NAME", "STATE",
               "MODE S CODE HEX", "STATUS CODE"]


def _csv(cols, rows):
    s = io.StringIO()
    w = csv.writer(s)
    w.writerow(cols)
    w.writerows(rows)
    return ("\ufeff" + s.getvalue()).encode("utf-8")


def archive(ref, master):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("ACFTREF.txt", _csv(REF_COLS, ref))
        z.writestr("MASTER.txt", _csv(MASTER_COLS, master))
    return b.getvalue()


REF = [
    ["FW4", "CESSNA", "206H", "4", "6", "1", "CLASS 1"],
    ["HEL", "BELL", "407", "6", "7", "3", "CLASS 1"],
    ["MQ9", "GENERAL ATOMICS", "MQ-9 BLOCK 1", "4", "0", "4", "CLASS 1"],
    ["DJ2", "DJI", "IMAGINARY TWO-SEAT", "6", "2", "10", "CLASS 4"],
    ["ER2", "LOCKHEED", "ER-2", "4", "1", "4", "CLASS 1"],
    ["UNK", "ACME", "NO SEAT COUNT", "4", "", "1", "CLASS 1"],
    ["QUD", "ACME", "QUADCOPTER", "6", "0", "10", "CLASS 4"],
    ["JET", "BOB VIOLETT MODELS", "KINGCAT", "4", "0", "4", "CLASS 4"],
    ["DJ5", "DJI", "AIR 3", "4", "0", "5", "CLASS 1"],
    ["TGS", "NAVMAR APPLIED SCIENCES CORP", "TIGERSHARK", "4", "0", "1", "CLASS 1"],
]
MASTER = [
    ["101PD", "FW4", "5", "CITY OF EXAMPLE POLICE DEPT", "IL", "A00001", "V"],
    ["202SO", "HEL", "5", "EXAMPLE COUNTY SHERIFF", "IL", "A00002", "V"],
    ["303CB", "MQ9", "5", "U S CUSTOMS AND BORDER PROTECTION", "AZ", "A00003", "V"],
    ["404DJ", "DJ2", "5", "EXAMPLE STATE UNIVERSITY", "OK", "A00004", "V"],
    ["505NA", "ER2", "5", "NASA", "CA", "A00005", "V"],
    ["606PV", "FW4", "1", "PRIVATE PERSON", "IL", "A00006", "V"],
    ["707CB", "HEL", "7", "CBP AIR LOGISTICS LLC", "TX", "A00007", "V"],
    ["808UK", "UNK", "5", "EXAMPLE CITY", "IL", "A00008", "V"],
    ["909QD", "QUD", "5", "EXAMPLE CITY POLICE", "IL", "A00009", "V"],
    ["111JT", "JET", "5", "EXAMPLE STATE UNIVERSITY", "OK", "A00010", "V"],
    ["222DJ", "DJ5", "5", "EXAMPLE COUNTY", "IL", "A00011", "V"],
    ["333TS", "TGS", "5", "EXAMPLE STATE UNIVERSITY", "OK", "A00012", "V"],
]



class Table(unittest.TestCase):
    def setUp(self):
        self.t = fr.parse_faa_archive(archive(REF, MASTER))

    def test_every_type_and_drone_kept(self):
        self.assertEqual(self.t["A00001"], ["N101PD", "CITY OF EXAMPLE POLICE DEPT", "IL"])
        self.assertEqual(sorted(self.t), ["A000%02d" % i for i in range(1, 13) if i not in (6, 7)])

    def test_registrant(self):
        self.assertNotIn("A00006", self.t)         # an individual
        self.assertNotIn("A00007", self.t)         # a private LLC with CBP in its name (Lee 2026-10-23)
        self.assertEqual(fr._FEDERAL_OPERATOR_ALLOWLIST, frozenset())

    def test_owner_db(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, fr.OWNER_DB_NAME)
            n = fr.build_owner_db(archive(REF, MASTER), p)
            self.assertEqual(n, len(MASTER))
            o = fr.OwnerRegistry()
            self.assertEqual(o.open(p), len(MASTER))
            self.assertEqual(o.lookup("a00006"), {"registration": "N606PV", "owner": "PRIVATE PERSON"})
            self.assertIsNone(o.lookup("A99999"))
            o.close()
            self.assertIsNone(o.lookup("A00006"))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_owner_db_keeps_valid_registrations_only(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, fr.OWNER_DB_NAME)
            rows = MASTER + [["444DR", "FW4", "1", "DEREGISTERED OWNER", "IL", "A00013", "D"]]
            self.assertEqual(fr.build_owner_db(archive(REF, rows), p), len(MASTER))
            o = fr.OwnerRegistry()
            o.open(p)
            self.assertIsNone(o.lookup("A00013"))
            o.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_names_and_schema(self):
        self.assertEqual((fr.CACHE_NAME, fr.LEGACY_CACHE_NAME, fr.OWNER_DB_NAME, fr.SCHEMA),
                         ("saw_faa_gov_aircraft.json", "saw_faa_gov_rotorcraft.json", "saw_faa_owners.sqlite", 4))
        self.assertEqual(fr.FAA_URL, "https://registry.faa.gov/database/ReleasableAircraft.zip")
        self.assertIn("Mozilla", fr.FAA_HEADERS["User-Agent"])
        self.assertEqual(fr.REFRESH_DAYS, 7)
        self.assertEqual(fr.STORE_KEY, "saw_faa_registry")

    def test_an_older_schema_is_rebuilt_now(self):
        fd, p = tempfile.mkstemp(); os.close(fd)
        try:
            self.assertFalse(fr.needs_refresh(p, 4))
            self.assertTrue(fr.needs_refresh(p, 3))
            old = time.time() - 8 * 86400
            os.utime(p, (old, old))
            self.assertTrue(fr.needs_refresh(p, 4))
        finally:
            os.unlink(p)
        self.assertTrue(fr.needs_refresh(p, 4))


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
    def __init__(self, status=200, body=b"", exc=None):
        self.status, self.body, self.exc, self.asked = status, body, exc, []

    def get(self, url, headers=None):
        self.asked.append((url, dict(headers or {})))
        if self.exc:
            raise self.exc
        return Resp(self.status, self.body)


async def run_now(func, *args):
    return func(*args)


class Holder(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.raw = archive(REF, MASTER)
        self.made = []

    def make(self):
        reg = fr.FaaRegistry(self.dir)
        self.made.append(reg)
        return reg

    def tearDown(self):
        for reg in self.made:
            reg.owners.close()
        shutil.rmtree(self.dir)

    def run_(self, coro):
        return asyncio.run(coro)

    def test_shared_is_one_per_store(self):
        store = {}
        a = fr.shared(store, self.dir)
        self.assertIs(fr.shared(store, "elsewhere"), a)
        self.assertIs(store[fr.STORE_KEY], a)
        self.assertIsNot(fr.shared({}, self.dir), a)

    def test_refresh_then_fresh(self):
        reg = self.make()
        s = Session(body=self.raw)
        out = self.run_(reg.refresh(s, run_now))
        self.assertEqual(out, "updated: 10 government, 12 owners")
        self.assertEqual(s.asked, [(fr.FAA_URL, fr.FAA_HEADERS)])
        self.assertEqual(reg.lookup_government("A00003")["operator_short"], "CBP")
        self.assertEqual(reg.lookup_owner("A00006")["owner"], "PRIVATE PERSON")
        with open(reg.cache_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual((doc["schema"], doc["count"]), (fr.SCHEMA, 10))
        self.assertEqual(self.run_(reg.refresh(s, run_now)), "fresh")
        self.assertEqual(len(s.asked), 1)
        self.assertEqual(self.run_(reg.refresh(s, run_now, force=True))[:8], "updated:")

    def test_a_new_holder_loads_the_cache(self):
        self.run_(self.make().refresh(Session(body=self.raw), run_now))
        reg = self.make()
        self.assertEqual(self.run_(reg.load(run_now)), 10)
        self.assertEqual(reg.lookup_owner("a00001")["registration"], "N101PD")
        self.assertEqual(self.run_(reg.refresh(Session(exc=AssertionError("asked")), run_now)), "fresh")

    def test_legacy_cache_read_then_removed(self):
        legacy = os.path.join(self.dir, fr.LEGACY_CACHE_NAME)
        with open(legacy, "w", encoding="utf-8") as fh:
            json.dump({"schema": 4, "aircraft": {"A00001": ["N1", "FEDERAL BUREAU OF INVESTIGATION", "VA"]}}, fh)
        reg = self.make()
        self.assertEqual(self.run_(reg.load(run_now)), 1)
        self.assertEqual(reg.lookup_government("A00001")["operator_short"], "FBI")
        self.run_(reg.refresh(Session(body=self.raw), run_now))      # no new cache yet: stale
        self.assertFalse(os.path.exists(legacy))
        self.assertTrue(os.path.exists(reg.cache_path))

    def test_failures_keep_the_tables(self):
        reg = self.make()
        self.run_(reg.refresh(Session(body=self.raw), run_now))
        for s, why in ((Session(status=403), "failed: download, HTTP 403"),
                       (Session(exc=asyncio.TimeoutError()), "failed: download, timeout"),
                       (Session(exc=OSError("down")), "failed: download, OSError"),
                       (Session(body=b"not a zip"), "failed: parse, BadZipFile")):
            with self.subTest(why=why):
                self.assertEqual(self.run_(reg.refresh(s, run_now, force=True)), why)
                self.assertEqual(reg.last_result, why)
                self.assertEqual(reg.gov.size, 10)
                self.assertEqual(reg.lookup_government("A00002")["operator_category"], LAW)

    def test_empty_table_is_refused(self):
        reg = self.make()
        raw = archive(REF, [r for r in MASTER if r[2] != "5"])
        self.assertEqual(self.run_(reg.refresh(Session(body=raw), run_now)), "failed: empty table")
        self.assertFalse(os.path.exists(reg.cache_path))

    def test_cancel_is_not_swallowed(self):
        reg = self.make()
        with self.assertRaises(asyncio.CancelledError):
            self.run_(reg.refresh(Session(exc=asyncio.CancelledError()), run_now))

    def test_two_callers_one_download(self):
        reg = self.make()
        s = Session(body=self.raw)

        async def both():
            return await asyncio.gather(reg.refresh(s, run_now), reg.refresh(s, run_now))
        a, b = self.run_(both())
        self.assertEqual(sorted([a[:8], b]), ["fresh", "updated:"])
        self.assertEqual(len(s.asked), 1)

    def test_refresh_first_finds_the_cache(self):
        self.run_(self.make().refresh(Session(body=self.raw), run_now))
        reg = self.make()                       # a restart: refresh is the first call
        self.assertEqual(self.run_(reg.refresh(Session(exc=AssertionError("asked")), run_now)), "fresh")
        self.assertEqual(reg.lookup_government("A00003")["operator_short"], "CBP")

    def test_loads_once(self):
        reg = self.make()
        calls = []

        async def counting(func, *args):
            calls.append(func)
            return func(*args)
        self.run_(reg.load(counting))
        self.run_(reg.load(counting))
        self.assertEqual(len(calls), 1)

    def test_too_big_is_refused(self):
        reg = self.make()
        old = fr.MAX_DOWNLOAD_BYTES
        fr.MAX_DOWNLOAD_BYTES = 10
        try:
            self.assertEqual(self.run_(reg.refresh(Session(body=self.raw), run_now)),
                             "failed: download, over 10 bytes")
        finally:
            fr.MAX_DOWNLOAD_BYTES = old

    def test_owner_half_never_breaks_the_government_half(self):
        reg = self.make()
        os.makedirs(reg.owner_db_path)          # a directory where the owner table goes
        self.assertEqual(self.run_(reg.refresh(Session(body=self.raw), run_now)), "updated: 10 government, 0 owners")
        self.assertEqual(reg.lookup_government("A00002")["operator_category"], LAW)
        self.assertIsNone(reg.lookup_owner("A00006"))

    def test_write_failure(self):
        reg = self.make()
        os.makedirs(reg.cache_path)             # a directory where the cache goes
        self.assertEqual(self.run_(reg.refresh(Session(body=self.raw), run_now))[:15], "failed: write, ")
        self.assertIsNone(reg.lookup_government("A00002"))

    def test_cache_before_legacy(self):
        for name, owner in ((fr.CACHE_NAME, "FEDERAL BUREAU OF INVESTIGATION"),
                            (fr.LEGACY_CACHE_NAME, "DRUG ENFORCEMENT ADMINISTRATION")):
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as fh:
                json.dump({"schema": 4, "aircraft": {"A00001": ["N1", owner, "VA"]}}, fh)
        reg = self.make()
        self.run_(reg.load(run_now))
        self.assertEqual(reg.lookup_government("A00001")["operator_short"], "FBI")

    def test_default_executor(self):
        reg = self.make()
        self.assertEqual(self.run_(reg.refresh(Session(body=self.raw)))[:8], "updated:")
        self.assertEqual(self.run_(reg.load()), 10)


if __name__ == "__main__":
    unittest.main()
