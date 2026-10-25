"""The FAA registry for the aircraft integrations, one per Home Assistant instance (0.2.15, backlog 300).

Moved from Live Track Aircraft's registrations.py at 2aa4823 (Lee 2026-10-25 01:42: "Use option1"), so Live
Track Aircraft and Live Track Enforcement Aircraft read ONE table and neither imports the other. Every span
below the holder's section is that file's code, copied verbatim; its comments say "this integration" where
they were written for Live Track Aircraft.

What it holds, from the FAA Releasable Aircraft Database (registry.faa.gov, no agreement):
  * the government table: every government registrant, by Mode S hex, with registration, owner and state;
    categorise() puts each in an owner class; operator_short() gives the agency code (DHS, CBP, ICE, FBI,
    USMS, DEA, ATF, USSS, USCG, DOJ) or a state or local force's short name;
  * the owner table: every valid registration's title holder, in SQLite.

! ONE PER INSTANCE. shared(store, storage_dir) keeps it in the dict the consumer passes (hass.data) under a
SAW-neutral key; whichever integration starts first builds it, the other finds it. One lock: one load, one
73 MB download at a time. The caches keep their SAW-neutral names in .storage (saw_faa_gov_aircraft.json,
saw_faa_owners.sqlite), so an instance that ran Live Track Aircraft before 0.2.15 keeps its tables.
! NOT FiledRoutesDB. This is the FAA's public registry, fetched from the FAA; it shares no file, session,
token or code with FiledRoutesDB (Lee, 2026-10-23).
! A FAILURE KEEPS WHAT IS THERE: a failed download, parse or write leaves the loaded tables in use; the owner
half never breaks the government half. With no table at all every lookup says None.
! No Home Assistant import and no aiohttp import: the session and the executor come from the consumer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)

# US ICAO 24-bit allocation. Exact, not heuristic.
US_HEX_MIN = 0xA00000
US_HEX_MAX = 0xAFFFFF

# Coarse boxes covering the US and its territories. The point is to avoid a
# 73 MB download for someone nowhere near the US, not to police borders - the
# CONUS box does catch southern Canada, which is harmless because Canadian
# aircraft are C0xxxx and fail the hex gate anyway.
_US_BOXES = (
    (24.4, 49.4, -125.0, -66.9),    # CONUS
    (51.0, 71.5, -168.0, -129.0),   # Alaska
    (51.0, 56.0, 172.0, 180.0),     # Aleutians beyond the antimeridian
    (18.9, 22.3, -160.3, -154.8),   # Hawaii
    (17.6, 18.6, -67.3, -64.5),     # Puerto Rico / USVI
    (13.2, 13.7, 144.6, 145.0),     # Guam
    (14.1, 15.3, 145.1, 146.1),     # Northern Marianas
)


def location_is_us(latitude: float | None, longitude: float | None,
                   country: str | None = None) -> bool:
    """Whether this install should bother with the US registry at all.

    ``country`` is Home Assistant's own configured country when available,
    which is the user's declaration and beats any bounding box. The boxes are
    the fallback for installs where it was never set.
    """
    if country:
        return country.upper() == "US"
    if latitude is None or longitude is None:
        return False
    return any(a <= latitude <= b and c <= longitude <= d
               for a, b, c, d in _US_BOXES)


def is_us_hex(hex_code: str | None) -> bool:
    """Whether a Mode S address falls in the US ICAO allocation."""
    if not hex_code:
        return False
    try:
        return US_HEX_MIN <= int(str(hex_code), 16) <= US_HEX_MAX
    except (TypeError, ValueError):
        return False


_SMALL_WORDS = {"of", "and", "the", "for", "at", "in", "on"}
_KEEP_UPPER = {"US", "USA", "USDA", "NASA", "DHS", "FBI", "DEA", "CBP", "ICE",
               "EMS", "LAPD", "NYPD", "CHP", "DPS", "ATF", "TSA", "NOAA"}


def prettify_owner(owner: str) -> str:
    """The registry stores owners in caps; make them readable."""
    words = []
    for i, w in enumerate(owner.split()):
        core = w.strip(".,")
        if core in _KEEP_UPPER:
            words.append(core)
        elif core.lower() in _SMALL_WORDS and i > 0:
            words.append(core.lower())
        elif "'" in w:                       # SHERIFF'S -> Sheriff's
            head, tail = w.split("'", 1)
            words.append(head.capitalize() + "'" + tail.lower())
        else:
            words.append(w.capitalize())
    out = " ".join(words)
    for abbr, full in (("Dept", "Department"), ("Cnty", "County")):
        out = out.replace(f" {abbr} ", f" {full} ")
        if out.endswith(f" {abbr}"):
            out = out[: -len(abbr)] + full
    return out


# ! BY WORD, NOT BY SUBSTRING (2026-10-21). The keys were substrings, and
# measured on the 5,725-row table that put 108 USDA Forest Service and a
# Minnesota wild-rice council under "Federal law enforcement" ("ICE " inside
# servICE, offICE, rICE), Marshall University under "Law enforcement", a
# Louisiana POLICE JURY (parish government) as police and a state FIRE
# MARSHAL as police. Law enforcement now draws police lights on the map
# (Lee, 2026-10-21), so a false hit is visible. Rules are tried in order;
# the first that matches wins. bin\lta_categorise_compare.py has the before
# and after.
_CATEGORY_RULES = (
    # Not police, though they carry the words.
    ("Fire / EMS", r"\bFIRE MARSHALL?S?\b"),
    ("Government", r"\bPOLICE JURY\b"),
    # ! COAST GUARD IS ITS OWN CATEGORY (Lee 2026-10-21: "army green with
    # police lights"): military fill, law-enforcement lights.
    ("Coast Guard", r"\bCOAST GUARD\b"),
    # Federal first, so the US Marshals are federal, not local.
    ("Federal law enforcement",
     r"\b(?:DEPT|DEPARTMENT|DEPARMENT) OF HOMELAND SECURITY\b"
     r"|\bCUSTOMS\b|\bBORDER (?:PROTECTION|PATROL)\b|\bCBP\b"
     r"|\bIMMIGRATION\b|\bDRUG ENFORCEMENT\b|\bDEA\b"
     r"|\bBUREAU OF INVESTIGATION\b|\bFBI\b|\bATF\b|\bSECRET SERVICE\b"
     r"|\bU ?S MARSHALL?S?\b|\bMARSHALS? SERVICE\b|\bJPATS\b"
     r"|\bDEPARTMENT OF JUSTICE\b"),
    ("Law enforcement",
     r"\bSHER+IF+'?S?\b|\bPOLICE\b|\bMARSHALS?\b|\bCONSTABLES?\b"
     r"|\bPUBLIC SAFETY\b|\b(?:DEPT|DEPARTMENT) OF SAFETY\b"
     r"|\bSTATE PATROL\b|\bHIGHWAY PATROL\b|\bDEPARTMENT OF CORRECTIONS?\b"
     r"|\bLAPD\b|\bNYPD\b|\bCHP\b|\bDPS\b|\bTROOPERS?\b"),
    ("Fire / EMS",
     r"\bFIRE\b|\bRESCUE\b|\bEMS\b|\bMEDICAL\b|\bAMBULANCE\b|\bMEDEVAC\b"
     r"|\bLIFE FLIGHT\b|\bAIR CARE\b"),
    ("Military",
     r"\bNAVAL\b|\bNAVY\b|\bARMY\b|AIR FORCE\b|\bMARINE CORPS\b"
     r"|\bNATIONAL GUARD\b|\bDEFENSE\b|\bDOD\b"),
    ("Natural resources",
     r"\bFOREST(?:RY|S)?\b|\bAPHIS\b|\bWILDLIFE\b|\bNATURAL RESOURCES?\b|\bPARKS?\b"
     r"|\bFISH AND GAME\b|\bCONSERVATION\b"),
    ("Research / academic",
     r"\bUNIVERSITY\b|\bCOLLEGE\b|\bINSTITUTE\b|\bNASA\b|\bRESEARCH\b|\bNOAA\b"),
)
_CATEGORY_RES = tuple((label, re.compile(rx)) for label, rx in _CATEGORY_RULES)

# The categories that draw police lights (icons.py). Coast Guard since
# 2026-10-22: its fill is military green (icons.MILITARY_CATEGORIES).
LAW_ENFORCEMENT = frozenset(("Law enforcement", "Federal law enforcement",
                             "Coast Guard"))
_FEDERAL_LABELLED = frozenset(("Federal law enforcement", "Coast Guard"))

# ! SHORT AGENCY CODES ON THE MARKER (Lee 2026-10-21): "DHS, CBP, FBI, USMS,
# ICE" instead of a truncated name. Tried in order, on the raw owner, and
# only for the federal categories: ICE before CBP because its name carries
# CUSTOMS; DHS last, for a DHS registration that names no component.
_AGENCY_CODES = tuple((code, re.compile(rx)) for code, rx in (
    ("ICE", r"\bIMMIGRATION\b"),
    ("CBP", r"\bCUSTOMS\b|\bBORDER (?:PROTECTION|PATROL)\b|\bCBP\b"),
    ("USCG", r"\bCOAST GUARD\b"),
    ("FBI", r"\bBUREAU OF INVESTIGATION\b|\bFBI\b"),
    ("USMS", r"\bMARSHALS? SERV|\bU ?S MARSHALL?S?\b|\bJPATS\b"),
    ("DEA", r"\bDRUG ENFORCEMENT\b|\bDEA\b"),
    ("ATF", r"\bATF\b|\bALCOHOL,? TOBACCO\b"),
    ("USSS", r"\bSECRET SERVICE\b"),
    ("DHS", r"\b(?:DEPT|DEPARTMENT|DEPARMENT) OF HOMELAND SECURITY\b"),
    ("DOJ", r"\bDEPARTMENT OF JUSTICE\b"),
))

_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "Puerto Rico": "PR",
}
_STATE_CODES = frozenset(_STATES.values())
# A state's name is shortened to its code only as the FORCE's name: "Utah
# County", "Mississippi County", "New York City" and "Washington DC" are
# places inside a state, and keep their names.
_STATE_RES = tuple((re.compile(r"^(?:State of |Commonwealth of )?%s\b(?! (?:County|Co|City|Parish|Dc)\b)"
                               % re.escape(n)), c)
                   for n, c in sorted(_STATES.items(), key=lambda kv: -len(kv[0])))
_STATE_TAIL = re.compile(r"\s+(?:%s)$" % "|".join(re.escape(n) for n in _STATES))
# Tried in order. Before the state codes: whole names with a known short form.
_FIRST = tuple((re.compile(rx), to) for rx, to in (
    (r"^(?:City of New York|New York City) Police\b.*$", "NYPD"),
    (r"^(?:City of )?Los Angeles Police\b.*$", "LAPD"),
    (r"^Washington DC\b", "DC"),
    (r"^(?:Department|Dept) of Public Safety of the State of (.+)$", r"\1 Department of Public Safety"),
    (r"^County of (.+?) (Sher+if+|Police)", r"\1 County \2"),
    (r"\s*\([^)]*\)", ""),
))
_WORDS = (
    (r"\b(?:Department|Dept) of Public Safety\b", "DPS"),
    (r"\b(?:Department|Dept) of State Police\b", "State Police"),
    (r"\b(Patrol) (?:Department|Dept) of Safety$", r"\1"),
    (r"\b(?:Department|Dept) of Safety(?: (?:&|and) Homeland Security)?\b", "DOS"),
    (r"\bHighway Patrol\b", "Hwy Patrol"),
    (r"\bSher+if+'?s? (?:Of+f?ice|Department|Dept|Depart)\b", "Sheriff"),
    (r"\bSher+if+'?s?\b", "Sheriff"),
    (r"\bPolice (?:Department|Dept)\b", "Police"),
    (r"\bMetropolitan\b", "Metro"),
    (r"\bCounty\b", "Co"),
    (r"\bDepartment\b", "Dept"),
    (r"\s+(?:Field Supp(?:ort)? )?(?:Office of )?(?:Aviation|Air Support|Air Operations|Air|Aerial|Aircraft|Headquarters)"
     r"(?: (?:Command|Unit|Section|Division|Div|Operations|Bureau))?$", ""),
)
_WORD_RES = tuple((re.compile(rx), to) for rx, to in _WORDS)


# The feed's database flags (readsb dbFlags). Lee 2026-10-21, step 2.
DBF_MILITARY, DBF_INTERESTING, DBF_PIA, DBF_LADD = 1, 2, 4, 8


def from_feed_flags(db_flags, own_op: str | None, gov: dict | None):
    """What the feed's flags add: (military, category, attributes).

    ! Every aircraft the feed flags military is drawn army green on its own
    type icon (icons.tone_for's military argument). A military aircraft is
    in no FAA table, so its category comes from the feed's owner text: a
    Coast Guard or law-enforcement owner keeps its lights, anything else is
    "Military". A government-table row keeps its own category.
    ! PIA (a Privacy ICAO Address) is a temporary hex: nothing can be looked
    up and the marker says so.
    """
    flags = db_flags if isinstance(db_flags, int) and not isinstance(db_flags, bool) and db_flags > 0 else 0
    military = bool(flags & DBF_MILITARY)
    category = (gov or {}).get("operator_category")
    extra: dict = {}
    if military:
        extra["is_military"] = True
        if gov is None:
            found = categorise(own_op) if own_op else None
            category = found if found in LAW_ENFORCEMENT else "Military"
            extra["operator_category"] = category
            extra["identified_by"] = "feed_military"
            if own_op:
                extra["operator"] = prettify_owner(own_op)
                extra["operator_name_source"] = "feed"
            short = operator_short(own_op or "", category)
            if short:
                extra["operator_short"] = short
    if flags & DBF_PIA:
        extra["identity_hidden"] = True
    return military, category, extra


def operator_short(owner: str, category: str | None) -> str | None:
    """What a law-enforcement marker says: an agency code for a federal
    agency, else the operator's name with its state and stock words
    shortened. None for any other category. Rules read against every
    law-enforcement owner in the table (bin/lta_le_labels.txt)."""
    if category not in LAW_ENFORCEMENT or not owner:
        return None
    up = " ".join(owner.upper().replace("&", " AND ").split())
    if category in _FEDERAL_LABELLED:
        for code, rx in _AGENCY_CODES:
            if rx.search(up):
                return code
    name = prettify_owner(owner)
    name = re.sub(r"-([a-z])", lambda m: "-" + m.group(1).upper(), name)
    name = re.sub(r"^City of ", "", name)
    words = name.split()
    if words and words[0].upper() in _STATE_CODES:
        words[0] = words[0].upper()
    name = " ".join("DNR" if w == "Dnr" else "DC" if w == "Dc" else w for w in words)
    for rx, to in _FIRST:
        name = rx.sub(to, name)
    for rx, code in _STATE_RES:
        if rx.search(name):
            name = rx.sub(code, name, count=1)
            break
    name = _STATE_TAIL.sub("", name)
    for rx, to in _WORD_RES:
        name = rx.sub(to, name)
    name = " ".join(name.split())
    # The label is 22 characters (coordinator.MAX_LABEL_CHARS); "San
    # Bernardino Sheriff" fits where "San Bernardino Co Sheriff" would not.
    if len(name) > 22:
        name = name.replace(" Co ", " ")
    return name or None


def categorise(owner: str) -> str:
    up = " ".join(owner.upper().replace("&", " AND ").split())
    for label, rx in _CATEGORY_RES:
        if rx.search(up):
            return label
    return "Government"


class GovRegistry:
    """The lookup table. Loaded lazily, and only for US installs."""

    def __init__(self) -> None:
        self._table: dict[str, list[str]] = {}
        self._loaded = False
        self._warned_unloaded = False
        self._law: list[str] | None = None
        self._fed: list[str] | None = None
        self.schema = 0

    def load(self, path: str | None = None) -> int:
        """Load the table from disk. Returns how many entries were read.

        Never raises: a missing or corrupt file leaves the registry empty and
        every lookup simply returns None, which is the same behaviour as not
        having the feature at all.
        """
        # No snapshot is shipped (Live Track Aircraft's registrations.py): no path, no table.
        target = path or ""
        try:
            with open(target, encoding="utf-8") as fh:
                payload = json.load(fh)
            table = payload.get("aircraft") if isinstance(payload, dict) else None
            if not isinstance(table, dict):
                raise ValueError("no 'aircraft' mapping in payload")
            self._table = table
            self._law = None
            self._fed = None
            self.schema = payload.get("schema", 0) if isinstance(payload.get("schema"), int) else 0
            self._loaded = True
            _LOGGER.debug("Loaded %s government aircraft from %s",
                          len(table), target)
        except Exception:  # noqa: BLE001
            self._table = {}
            self._loaded = True
            _LOGGER.debug("Could not load %s; government lookups disabled",
                          target, exc_info=True)
        return len(self._table)

    def lookup(self, hex_code: str | None) -> dict[str, Any] | None:
        """Resolve a Mode S address, or None.

        Returns None for anything outside the US ICAO block without touching
        the table, so non-US traffic costs a single integer comparison.

        ⚠ This runs in the event loop, so it must NEVER touch disk. The table
        is loaded once during setup via async_load_registry, which uses an
        executor. An earlier version lazily called self.load() from here and
        Home Assistant correctly flagged it:

            Detected blocking call to open ... inside the event loop by
            custom integration 'flights_above'

        If the table was never loaded, return None rather than loading it -
        that is the same behaviour as not having the feature.
        """
        if not is_us_hex(hex_code):
            return None
        if not self._loaded:
            if not self._warned_unloaded:
                self._warned_unloaded = True
                _LOGGER.debug(
                    "Government registry not loaded; lookups return None. "
                    "async_load_registry should have run during setup."
                )
            return None
        row = self._table.get(str(hex_code).upper())
        if not row:
            return None
        registration, owner, state = (row + ["", "", ""])[:3]
        category = categorise(owner) if owner else None
        out = {
            "registration": registration or None,
            "operator": prettify_owner(owner) if owner else None,
            "operator_category": category,
            "operator_state": state or None,
            # HOW it was identified (Lee 2026-10-21: the hover box says so).
            # A code; the words are the card's (06 D4b).
            "identified_by": ("faa_federal_list"
                              if owner.strip().upper() in _FEDERAL_OPERATOR_ALLOWLIST
                              else "faa_government"),
        }
        short = operator_short(owner, category)
        if short:
            out["operator_short"] = short
        return out

    @property
    def size(self) -> int:
        return len(self._table)

    def law_enforcement_hexes(self) -> list[str]:
        """Every hex whose owner is law enforcement (watch.py's list).
        Worked out once per table, not per poll."""
        if self._law is None:
            self._law = sorted(h.lower() for h, row in self._table.items()
                               if row and len(row) > 1
                               and categorise(row[1]) in LAW_ENFORCEMENT)
        return self._law

    def federal_law_enforcement_hexes(self) -> list[str]:
        """Every hex whose owner is FEDERAL law enforcement (watch.py, 2026-10-24).

        The EnforcementAircraft list's federal rule names the same registrants;
        asking the table as well keeps a registration newer than the list's
        build watched. Worked out once per table."""
        if self._fed is None:
            self._fed = sorted(h.lower() for h, row in self._table.items()
                               if row and len(row) > 1
                               and categorise(row[1]) == "Federal law enforcement")
        return self._fed



# --------------------------------------------------------------------------
# Owner lookup
#
# The government table names 5,726 aircraft. Everything else - private jets,
# flight school Cessnas, air ambulances, corporate turboprops - falls through
# to lookup_operator, which can only match airline designators, and therefore
# shows "Private". Measured: 28 of 52 occupied slots.
#
# ⚠ SQLITE, NOT A DICT, and the reason is measured. 181,538 entries are 2.32 MB
# gzipped and 7.63 MB as JSON, but 51.6 MB as a Python dict - twenty-two times
# the file size. parse_faa_archive's docstring promises "peak memory is a few
# MB, so this is safe on a Pi" and that promise is load-bearing. On disk the
# same table is 10.7 MB and a lookup costs about 51 microseconds.
#
# ⚠ NOTHING IS SHIPPED. This is built from the archive the integration ALREADY
# downloads every REFRESH_DAYS for the government table.
#
# ⚠ INDIVIDUAL REGISTRANTS ARE EXCLUDED. Section 803 of the FAA
# Reauthorization Act of 2024 (49 USC 44114, effective 2025-03-28) defines
# personally identifiable information to include the name of the owner where
# the owner is an individual. TYPE REGISTRANT = 1 is dropped: 126,404 of
# 316,483 rows. That sounds fatal to the feature and is not - measured against
# 28 unnamed aircraft overhead, 26 would still be named, because light aircraft
# near a city are overwhelmingly flight school and rental fleets registered to
# companies.
#
# ! SUPERSEDED 2026-09-12 - THIS FILTER NO LONGER EXISTS. The paragraph
# above describes what the FORK still does, and is kept for that reason.
# The withholding is applied by the FAA BEFORE the archive is published,
# so TYPE REGISTRANT = 1 was never the Section 803 population. See the
# DIVERGENCE comment inside build_owner_db below, and
# 30_Integrations/Live_Track_Aircraft/SSOT.md.
# --------------------------------------------------------------------------

_TYPE_REGISTRANT_INDIVIDUAL = "1"


def build_owner_db(raw: bytes, db_path: str) -> int:
    """Extract owners from the FAA archive into SQLite. Returns the row count.

    ⚠ Runs on an executor thread alongside parse_faa_archive. CPU-bound.
    ⚠ Writes to a temporary file and renames, so a failure part-way through
    leaves the previous database intact rather than a half-built one.
    """
    import csv
    import io as _io
    import sqlite3
    import zipfile

    tmp = db_path + ".new"
    for p in (tmp, tmp + "-wal", tmp + "-shm"):
        try:
            os.unlink(p)
        except OSError:
            pass

    zf = zipfile.ZipFile(_io.BytesIO(raw))
    con = sqlite3.connect(tmp)
    n = 0
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("CREATE TABLE owners (hex TEXT PRIMARY KEY, reg TEXT, "
                    "owner TEXT)")
        with zf.open("MASTER.txt") as fh:
            reader = csv.DictReader(_io.TextIOWrapper(fh, "utf-8-sig",
                                                      errors="replace"))
            batch = []
            for row in reader:
                r = {(k or "").strip(): (v or "").strip()
                     for k, v in row.items()}
                if r.get("STATUS CODE") != "V":
                    continue
                # ! DIVERGENCE FROM THE FORK'S COPY, 2026-09-11. This skipped
                # every TYPE REGISTRANT = 1 row - 126,404 of 310,098 valid
                # registrations, 40.8% - believing it implemented 49 U.S.C.
                # 44114 (Section 803).
                #
                # ! IT DID NOT. The statute binds the FAA, not us, and the
                # withholding is ALREADY APPLIED TO THE FILE WE DOWNLOAD.
                # From ardata.pdf inside this very archive: "The FAA has
                # established a procedure in accordance with 49 U.S.C. §
                # 44114(b) by which private aircraft owners can request
                # certain personally identifiable information, such as names
                # and addresses, be withheld ... Some records previously
                # found on this page are not available at this time due to
                # the implementation of these changes."
                #
                # So a name still present in the Releasable Aircraft Database
                # is a name the FAA has decided to release. Dropping type 1
                # discarded 126,404 of them and left the map showing bare
                # tail numbers instead.
                #
                # ! IT ALSO CAUGHT THE WRONG PEOPLE ANYWAY. Type 4 Co-Owned
                # (25,822) and type 2 Partnership (3,085) are usually private
                # individuals too, and both were kept - measured: N5116J,
                # "CARLL ELIZABETH M", type 4, on the map.
                #
                # ! WHAT STILL MATTERS: the snapshot AGES. A withholding
                # granted after our download stays in our copy until the next
                # refresh, which is why REFRESH_DAYS is not decoration.
                hx = (r.get("MODE S CODE HEX") or "").upper()
                name = r.get("NAME") or ""
                if not hx or not name:
                    continue
                batch.append((hx, "N" + (r.get("N-NUMBER") or ""), name[:48]))
                if len(batch) >= 5000:
                    con.executemany("INSERT OR REPLACE INTO owners "
                                    "VALUES (?,?,?)", batch)
                    n += len(batch)
                    batch = []
            if batch:
                con.executemany("INSERT OR REPLACE INTO owners VALUES (?,?,?)",
                                batch)
                n += len(batch)
        con.commit()
    finally:
        con.close()
    os.replace(tmp, db_path)
    return n


class OwnerRegistry:
    """Read-only owner lookups, held open for the life of the process."""

    def __init__(self) -> None:
        self._con = None
        self.count = 0

    def open(self, db_path: str) -> int:
        import sqlite3
        self.close()
        if not os.path.exists(db_path):
            return 0
        try:
            self._con = sqlite3.connect(
                "file:%s?mode=ro" % db_path.replace("\\", "/"),
                uri=True, check_same_thread=False)
            self.count = self._con.execute(
                "SELECT COUNT(*) FROM owners").fetchone()[0]
        except Exception:  # noqa: BLE001
            # ⚠ Degrade, never raise. A missing or corrupt database must not
            # affect setup or flight tracking - the same contract GovRegistry
            # already honours.
            _LOGGER.debug("Owner database unavailable", exc_info=True)
            self._con = None
            self.count = 0
        return self.count

    def close(self) -> None:
        if self._con is not None:
            try:
                self._con.close()
            except Exception:  # noqa: BLE001
                pass
        self._con = None
        self.count = 0

    def lookup(self, hex_code):
        if self._con is None or not hex_code:
            return None
        try:
            row = self._con.execute(
                "SELECT reg, owner FROM owners WHERE hex=?",
                (hex_code.strip().upper(),)).fetchone()
        except Exception:  # noqa: BLE001
            return None
        return {"registration": row[0], "owner": row[1]} if row else None



# ! DIVERGENCE FROM THE FORK'S COPY, 2026-09-11. This was 30, chosen on the
# belief that the FAA refreshed the registry monthly. NOBODY HAD MEASURED IT.
#
# ! MEASURED: the archive is rebuilt EVERY BUSINESS NIGHT, around 23:22 US
# Central. Three MASTER.txt build stamps - 2026-08-28 23:23, 2026-09-04 23:23
# and 2026-09-10 23:22 - and every member of the zip carries the same
# timestamp within twelve minutes, which is one nightly batch. The two Friday
# files are an artefact of WHEN WE DOWNLOADED: 30 August was a Sunday and
# 5 September a Saturday, so each got the previous Friday's build.
#
# ! WEEKLY IS A DELIBERATE CEILING, NOT THE SOURCE'S CADENCE. Daily would be
# a 73 MB download every night for a handful of changed rows - a nuisance for
# almost no benefit. Weekly costs four downloads a month instead of one and
# buys a worst-case staleness of 7 days instead of 30.
#
# ! THIS IS THE ONLY THING ABOUT 44114 THAT STILL BINDS US. The withholding
# itself is applied by the FAA before the file is published, so there is
# nothing to filter - but a withholding granted the day after a download sits
# in our copy until the next refresh. Freshness is the obligation; filtering
# never was.
REFRESH_DAYS = 7
FAA_URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"

# A bare urllib/aiohttp UA is rejected with 403; the registry wants something
# browser-shaped.
_FAA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Accept": "application/zip,*/*",
}

_TYPE_ACFT_ROTORCRAFT = "6"
_TYPE_REGISTRANT_GOVERNMENT = "5"

# LOCAL CHANGE. Federal operators registered as an LLC rather than as
# government, so a registrant-code filter alone misses them. EXACT names only -
# keyword matching was measured and rejected: "MARSHAL" gives 248 hits and 4
# real ones (people surnamed Marshall, Marshall University, Marshall Soaring
# Club); "ICE" gives 14 and ZERO real ones (Ice Man Holdings, Two Bag Ice).
# ! EMPTY SINCE 2026-10-23 (Lee): both names it held were PRIVATE owners.
# HOMELAND AVIATION LLC is a Shreveport, LA owner of light piston aircraft
# (N462WC, an Aviat Husky, FAA registry); CBP AIR LOGISTICS LLC is "managed
# and wholly owned by" a private individual by its own court filing (D. Kan.
# 2:25-cv-02514). A private registrant gets an agency label only when a cited
# document backs it or Lee has reviewed it (an EnforcementAircraft entry),
# never from its name.
_FEDERAL_OPERATOR_ALLOWLIST: frozenset[str] = frozenset()
# ACFTREF TYPE-ENG: 2 turbo-prop, 3 turbo-shaft, 4 turbo-jet, 5 turbo-fan.
_TURBINE_ENGINES = frozenset(("2", "3", "4", "5"))
_DRONE_MFR_PREFIXES = (
    "DJI", "AUTEL", "YUNEEC", "3D ROBOTICS", "PARROT", "SKYDIO", "FREEFLY",
    "AGEAGLE", "INSPIRED FLIGHT", "WINGTRA", "QUANTUM SYSTEMS", "ENYU LUO",
    "SENSEFLY", "EHANG", "XIAOMI", "POWERVISION",
)


def parse_faa_archive(raw: bytes) -> dict[str, list[str]]:
    """Extract every government-registered aircraft, drones excluded.

    Streams row by row: MASTER.txt is 194 MB uncompressed and must never be
    materialised. Peak memory is a few MB, so this is safe on a Pi.

    NOTE: runs on an executor thread. It is CPU-bound for roughly 10-60 s
    depending on hardware and must not touch the event loop.
    """
    import csv
    import io as _io
    import zipfile

    zf = zipfile.ZipFile(_io.BytesIO(raw))

    def rows(name: str):
        # utf-8-sig: these files carry a BOM which would otherwise leave the
        # first column named '\ufeffCODE'.
        with zf.open(name) as fh:
            reader = csv.DictReader(_io.TextIOWrapper(fh, "utf-8-sig", errors="replace"))
            for row in reader:
                yield {(k or "").strip(): (v or "").strip() for k, v in row.items()}

    types: dict[str, tuple[str, int]] = {}
    for r in rows("ACFTREF.txt"):
        # LOCAL CHANGE: no rotorcraft filter. It excluded every government
        # FIXED-WING aircraft, so a government Gulfstream fell through to
        # lookup_operator and displayed as "Private". Measured: rotorcraft
        # only 1,595 entries; all government aircraft 5,730.
        # ! A blank or unreadable seat count is UNKNOWN, not zero: only a
        # stated 0 marks a drone below.
        try:
            seats = int(r.get("NO-SEATS") or "")
        except ValueError:
            seats = None
        types[r.get("CODE", "")] = ((r.get("MFR") or "").upper(), seats,
                                    (r.get("TYPE-ENG") or "").strip(),
                                    (r.get("AC-WEIGHT") or "").strip().upper())

    table: dict[str, list[str]] = {}
    for r in rows("MASTER.txt"):
        if (r.get("TYPE REGISTRANT") != _TYPE_REGISTRANT_GOVERNMENT
                and (r.get("NAME") or "").strip().upper()
                not in _FEDERAL_OPERATOR_ALLOWLIST):
            continue
        t = types.get(r.get("MFR MDL CODE", ""))
        if not t:
            continue
        mfr, seats, engine, weight = t
        # ! DRONES OUT (Lee, 2026-10-22). This read "drones kept", and
        # _DRONE_MFR_PREFIXES was defined and never used. Measured on the
        # FAA archive of 2026-10-21: 1,136 of 5,743 government rows had
        # ACFTREF NO-SEATS 0, every one an unmanned aircraft by make and model
        # (DJI, Skydio, Autel, Hylio, ScanEagle, MQ-9...); all 700 drone-maker
        # rows have 0 seats too, and a word search of make and model found
        # none the seat count missed. ONE seat is a real aircraft (ER-2,
        # F-16A, Air Tractor, gliders) and is kept: the fork's PR branch
        # dropped seats < 2, which was right for rotorcraft only.
        # ! The cost, measured: 10 GENERAL ATOMICS MQ-9 BLOCK 1 go with them,
        # and a large UAS can transmit ADS-B. One that does still gets its
        # owner from the owner table; it is just not drawn as government.
        # ! MQ-9 CLASS KEPT (Lee 2026-10-22 01:27): an unmanned airframe that
        # is turbine-powered (TYPE-ENG 2-5) and not weight class 4 (UAS under
        # 55 lb). Measured: 18 rows - 12 MQ-9, 4 Global Hawk, 2 Scion Weasel;
        # a turbine model jet (class 4) and a DJI coded turbofan stay out.
        # ! DRONES ARE IN THE TABLE (Lee 2026-10-22 02:31: leaving them out
        # was "the opposite of what I want"). From ed51866 to this commit
        # they were left out (0 seats or a drone maker), MQ-9 class kept
        # from 9fd867b; measured, that dropped 1,118 of 5,743 rows. A
        # government drone that broadcasts is drawn as government, with
        # lights if police. _DRONE_MFR_PREFIXES and _TURBINE_ENGINES are no
        # longer used here.
        del mfr, seats, engine, weight
        hx = (r.get("MODE S CODE HEX") or "").strip().upper()
        if hx:
            table[hx] = [
                "N" + (r.get("N-NUMBER") or "").strip(),
                (r.get("NAME") or "").strip(),
                (r.get("STATE") or "").strip(),
            ]
    return table


CACHE_NAME = "saw_faa_gov_aircraft.json"
# Read only while the new file does not exist yet, so the first start after
# the rename keeps its government tones until the refresh lands; deleted after
# the first successful write.
LEGACY_CACHE_NAME = "saw_faa_gov_rotorcraft.json"
# 3 since 2026-10-22 02:3x: drones back in. A cache of an older schema is
# rebuilt at the next start (needs_refresh), not after REFRESH_DAYS.
# 4 since 2026-10-23: the two private LLCs out of the allowlist.
SCHEMA = 4
EXCLUDES = "nothing: every government registrant, drones included"


def needs_refresh(path: str, loaded_schema: int) -> bool:
    """Stale by age, or built by an older rule (SCHEMA)."""
    return _needs_refresh(path) or loaded_schema < SCHEMA


def _needs_refresh(path: str) -> bool:
    import time
    try:
        age_days = (time.time() - os.path.getmtime(path)) / 86400
    except OSError:
        return True                      # no cache yet
    return age_days >= REFRESH_DAYS


# --------------------------------------------------------------------------
# One registry per Home Assistant instance (0.2.15, backlog 300).
# --------------------------------------------------------------------------

STORE_KEY = "saw_faa_registry"
OWNER_DB_NAME = "saw_faa_owners.sqlite"
FAA_HEADERS = _FAA_HEADERS
DOWNLOAD_TIMEOUT_S = 600
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024


async def _default_run_sync(func, *args):
    return await asyncio.get_running_loop().run_in_executor(None, func, *args)


class _Fault(Exception):
    pass


def _why(err: Exception) -> str:
    if isinstance(err, _Fault):
        return str(err)
    if isinstance(err, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    return type(err).__name__


async def _download(session) -> bytes:
    async with asyncio.timeout(DOWNLOAD_TIMEOUT_S):
        async with session.get(FAA_URL, headers=FAA_HEADERS) as resp:
            if resp.status != 200:
                raise _Fault("HTTP %d" % resp.status)
            raw = await resp.read()
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise _Fault("over %d bytes" % MAX_DOWNLOAD_BYTES)
    return raw


def _write_cache(path: str, payload: dict, legacy: str) -> None:
    """Whole or not at all; the pre-rename cache goes after the first good write."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    os.replace(tmp, path)
    try:
        os.unlink(legacy)
    except OSError:
        pass


class FaaRegistry:
    """The instance's government and owner tables, their caches and their refresh."""

    def __init__(self, storage_dir: str) -> None:
        self.storage_dir = storage_dir
        self.cache_path = os.path.join(storage_dir, CACHE_NAME)
        self.legacy_cache_path = os.path.join(storage_dir, LEGACY_CACHE_NAME)
        self.owner_db_path = os.path.join(storage_dir, OWNER_DB_NAME)
        self.gov = GovRegistry()
        self.owners = OwnerRegistry()
        self.loaded = False
        self.last_result = "not refreshed"
        self._lock = asyncio.Lock()

    # --- lookups: never touch disk, safe in the event loop ------------------------
    def lookup_government(self, hex_code: str | None) -> dict[str, Any] | None:
        return self.gov.lookup(hex_code)

    def lookup_owner(self, hex_code):
        return self.owners.lookup(hex_code)

    # --- load once, from the caches -----------------------------------------------
    def _load_sync(self) -> int:
        target = (self.cache_path if os.path.exists(self.cache_path)
                  else self.legacy_cache_path if os.path.exists(self.legacy_cache_path) else "")
        n = self.gov.load(target)
        self.owners.open(self.owner_db_path)
        return n

    async def load(self, run_sync=None) -> int:
        """Load the cached tables, once per instance. Returns the government table's size."""
        run_sync = run_sync or _default_run_sync
        async with self._lock:
            if not self.loaded:
                await run_sync(self._load_sync)
                self.loaded = True
            return self.gov.size

    # --- refresh from the FAA ------------------------------------------------------
    async def refresh(self, session, run_sync=None, force: bool = False) -> str:
        """Fetch, parse and install a new archive when the cache is stale or of an older schema.

        Returns "fresh" (nothing asked), "updated: <n> government, <m> owners" or "failed: <why>".
        Never raises for a network or file fault; a second caller waits on the lock and then finds the
        cache fresh.
        """
        run_sync = run_sync or _default_run_sync
        async with self._lock:
            if not self.loaded:
                await run_sync(self._load_sync)
                self.loaded = True
            if not force and not await run_sync(needs_refresh, self.cache_path, self.gov.schema):
                return "fresh"
            result = await self._refresh(session, run_sync)
            self.last_result = result
            return result

    async def _refresh(self, session, run_sync) -> str:
        try:
            raw = await _download(session)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - every fault keeps the loaded tables
            _LOGGER.debug("Could not download the FAA registry", exc_info=True)
            return "failed: download, %s" % _why(err)
        try:
            table = await run_sync(parse_faa_archive, raw)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not parse the FAA registry", exc_info=True)
            return "failed: parse, %s" % _why(err)
        owners = 0
        try:
            owners = await run_sync(build_owner_db, raw, self.owner_db_path)
            await run_sync(self.owners.open, self.owner_db_path)
        except Exception:  # noqa: BLE001 - never let the owner half break the government half
            _LOGGER.debug("Owner database rebuild failed", exc_info=True)
        if not table:
            return "failed: empty table"
        payload = {"schema": SCHEMA, "source": "FAA Releasable Aircraft Database",
                   "excludes": EXCLUDES, "count": len(table), "aircraft": table}
        try:
            await run_sync(_write_cache, self.cache_path, payload, self.legacy_cache_path)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not write the registry cache", exc_info=True)
            return "failed: write, %s" % _why(err)
        await run_sync(self.gov.load, self.cache_path)
        _LOGGER.info("Government aircraft registry refreshed: %s entries", len(table))
        return "updated: %d government, %d owners" % (len(table), owners)


def shared(store: dict, storage_dir: str) -> FaaRegistry:
    """The instance's one registry, made on first use. store is the consumer's hass.data."""
    reg = store.get(STORE_KEY)
    if reg is None:
        reg = store[STORE_KEY] = FaaRegistry(storage_dir)
    return reg
