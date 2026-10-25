"""The EnforcementAircraft list, read once per Home Assistant instance (backlog 299, 0.2.12).

EnforcementAircraft (https://github.com/StrausbergAutomationWorks/EnforcementAircraft) is a
public data repository, ODbL v1.0: immigration and law-enforcement aircraft by Mode S hex, each
with a status (sourced, detected_unconfirmed, cleared). Release `data` holds
enforcementaircraft.json and SHA256SUMS. Live Track Aircraft and Live Track Enforcement Aircraft
both read it through this module, so neither imports the other.

! ONE READER PER INSTANCE. shared(store, cache_path) keeps it in the dict the consumer passes
(hass.data) under a SAW-neutral key; whichever integration starts first builds it, the other
finds it. One lock, one refresh at a time, one cache file.
! ITS OWN SESSION, made by the consumer under SESSION_KEY with SESSION_KWARGS. GitHub's release
302 carries a Content-Security-Policy header over aiohttp's 8,190-byte default
(10_SEED/05_SHARED_LESSONS.md B17). ! Never a session, downloader, cache or token that serves
FiledRoutesDB: the list is not linked to that service in any way (Lee, 2026-10-23).
! SHA256SUMS FIRST. It is small; the list is fetched only when its hash differs from the cached
copy's, and is installed only when its bytes match that hash.
! A FAILURE KEEPS WHAT IS THERE. The cached copy stays in use, and it never expires: the list is
additions only. With no cache at all the reader is empty and every lookup says None, so a
consumer falls back to its own rules.
! No Home Assistant import and no aiohttp import: the session and the executor come from the
consumer.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass

BASE_URL = "https://github.com/StrausbergAutomationWorks/EnforcementAircraft/releases/download/data/"
FILE_NAME = "enforcementaircraft.json"
SUMS_NAME = "SHA256SUMS"
LIST_URL = BASE_URL + FILE_NAME
SUMS_URL = BASE_URL + SUMS_NAME
# The cache in the consumer's .storage, SAW-neutral (never named after an integration).
CACHE_NAME = "saw_enforcementaircraft.json"
STORE_KEY = "saw_enforcementaircraft"
SESSION_KEY = "saw_enforcementaircraft_session"
HEADER_LIMIT = 32768
SESSION_KWARGS = {"max_line_size": HEADER_LIMIT, "max_field_size": HEADER_LIMIT}

# ! FEDERAL LAW-ENFORCEMENT REGISTRANTS, FOUND, NOT BUILT (backlog 297, 0.2.13). The list's federal
# rule names every federal law-enforcement registrant in the FAA registry AS OF ITS BUILD; an
# integration that holds the FAA registry (Live Track Aircraft) publishes the registrants it finds
# under this SAW-neutral key, {hex: {"registration", "operator", "operator_short"}}, so a registration
# newer than the list's build is still watched nationwide by whoever draws the list. Absent when no
# installed integration holds the registry; a consumer then has the list alone.
FEDERAL_KEY = "saw_federal_law_enforcement"

SOURCED = "sourced"
DETECTED = "detected_unconfirmed"
CLEARED = "cleared"
SCHEMA = 1

REFRESH_S = 6 * 3600      # a good check holds this long
RETRY_S = 15 * 60         # after a failed one
TIMEOUT_S = 60
MAX_BYTES = 16 * 1024 * 1024

_HEX = re.compile(r"^[0-9a-f]{6}$")
_SUM_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(\S+)\s*$")


@dataclass(frozen=True)
class Entry:
    hex: str
    status: str
    agency: str | None = None
    entry: str | None = None
    registration: str | None = None


def norm_hex(value) -> str | None:
    """A Mode S hex as the list keys it: six lowercase hex digits. TIS-B's "~" prefix is not one."""
    if not isinstance(value, str):
        return None
    h = value.strip().lower()
    return h if _HEX.match(h) else None


def parse(raw: bytes) -> tuple[dict[str, Entry], dict]:
    """The published file -> ({hex: Entry}, header). Raises ValueError on a file not of this shape.

    A row without a usable hex or status is skipped, not fatal: one bad row must not cost the rest.
    """
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ValueError("not JSON: %s" % err) from err
    if not isinstance(doc, dict) or doc.get("name") != "EnforcementAircraft":
        raise ValueError("not the EnforcementAircraft list")
    if doc.get("schema") != SCHEMA:
        raise ValueError("schema %r, this reader knows %d" % (doc.get("schema"), SCHEMA))
    rows = doc.get("aircraft")
    if not isinstance(rows, list):
        raise ValueError("no aircraft list")
    out: dict[str, Entry] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        h = norm_hex(row.get("hex"))
        status = row.get("status")
        if h is None or not isinstance(status, str) or not status:
            continue
        out[h] = Entry(h, status, row.get("agency"), row.get("entry"), row.get("registration"))
    header = {k: doc.get(k) for k in ("built", "registry_date", "license", "attribution")}
    return out, header


def sha_from_sums(text: str, name: str = FILE_NAME) -> str | None:
    """The hash SHA256SUMS gives for `name`, lowercase, or None."""
    for line in text.splitlines():
        m = _SUM_LINE.match(line.strip())
        if m and m.group(2) == name:
            return m.group(1).lower()
    return None


def _read_file(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return None


def _write_file(path: str, raw: bytes) -> None:
    """Whole or not at all: a temporary file beside it, then a rename."""
    tmp = path + ".new"
    with open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, path)


async def _default_run_sync(func, *args):
    return await asyncio.get_running_loop().run_in_executor(None, func, *args)


class EnforcementList:
    """The list in memory, its cache on disk, and its refresh."""

    def __init__(self, cache_path: str, clock=time.monotonic) -> None:
        self.cache_path = cache_path
        self._clock = clock
        self._lock = asyncio.Lock()
        self.entries: dict[str, Entry] = {}
        self.header: dict = {}
        self.sha: str | None = None
        self.checked_at: float | None = None
        self.last_ok: bool | None = None
        self.last_result = "not checked"
        self._sourced: frozenset[str] = frozenset()

    # --- lookups, cheap enough for every aircraft on every poll ----------------
    def entry(self, hex_code) -> Entry | None:
        h = norm_hex(hex_code)
        return self.entries.get(h) if h else None

    def status(self, hex_code) -> str | None:
        e = self.entry(hex_code)
        return e.status if e else None

    def is_sourced(self, hex_code) -> bool:
        h = norm_hex(hex_code)
        return h is not None and h in self._sourced

    def sourced(self) -> frozenset[str]:
        return self._sourced

    def hexes(self, *statuses: str) -> frozenset[str]:
        """Every hex with one of these statuses (all of them when none is named)."""
        return frozenset(h for h, e in self.entries.items() if not statuses or e.status in statuses)

    @property
    def summary(self) -> str:
        """One low-cardinality line for a sensor attribute: changes with the list, not the clock."""
        if not self.entries:
            return "no list" if self.last_ok is not False else "no list (%s)" % self.last_result
        line = "%d sourced of %d, built %s" % (len(self._sourced), len(self.entries), self.header.get("built"))
        return line if self.last_ok is not False else line + " (%s)" % self.last_result

    def _install(self, raw: bytes) -> None:
        entries, header = parse(raw)
        self.entries, self.header = entries, header
        self.sha = hashlib.sha256(raw).hexdigest()
        self._sourced = frozenset(h for h, e in entries.items() if e.status == SOURCED)

    # --- the cache -----------------------------------------------------------
    def load_cache(self) -> bool:
        """SYNC (run it in an executor). True when a usable cached copy was installed."""
        raw = _read_file(self.cache_path)
        if raw is None or len(raw) > MAX_BYTES:
            return False
        try:
            self._install(raw)
        except ValueError:
            return False
        return True

    # --- the refresh ---------------------------------------------------------
    def due(self) -> bool:
        if self.checked_at is None:
            return True
        wait = REFRESH_S if self.last_ok else RETRY_S
        return self._clock() - self.checked_at >= wait

    async def refresh(self, session, run_sync=None, force: bool = False) -> str:
        """Check the release and install a new list if there is one.

        Returns "fresh" (checked recently, nothing asked), "unchanged", "updated" or
        "failed: <why>". Never raises for a network or file fault.
        """
        run_sync = run_sync or _default_run_sync
        async with self._lock:
            if not force and not self.due():
                return "fresh"
            try:
                result = await self._check(session, run_sync)
                self.last_ok = True
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - every fault keeps the cached list
                result = "failed: %s" % _why(err)
                self.last_ok = False
            self.checked_at = self._clock()
            self.last_result = result
            return result

    async def _check(self, session, run_sync) -> str:
        sums = await _get(session, SUMS_URL)
        want = sha_from_sums(sums.decode("utf-8", "replace"))
        if want is None:
            raise _Fault("SHA256SUMS names no %s" % FILE_NAME)
        if want == self.sha:
            return "unchanged"
        raw = await _get(session, LIST_URL)
        got = hashlib.sha256(raw).hexdigest()
        if got != want:
            raise _Fault("checksum mismatch")
        try:
            parse(raw)
        except ValueError as err:
            raise _Fault("bad file: %s" % err) from err
        await run_sync(_write_file, self.cache_path, raw)
        self._install(raw)
        return "updated"


class _Fault(Exception):
    pass


def _why(err: Exception) -> str:
    if isinstance(err, _Fault):
        return str(err)
    if isinstance(err, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    return type(err).__name__


async def _get(session, url: str) -> bytes:
    async with asyncio.timeout(TIMEOUT_S):
        async with session.get(url) as resp:
            if resp.status != 200:
                raise _Fault("HTTP %d" % resp.status)
            raw = await resp.read()
    if len(raw) > MAX_BYTES:
        raise _Fault("over %d bytes" % MAX_BYTES)
    return raw


def shared(store: dict, cache_path: str) -> EnforcementList:
    """The instance's one reader, made on first use. store is the consumer's hass.data."""
    reader = store.get(STORE_KEY)
    if reader is None:
        reader = store[STORE_KEY] = EnforcementList(cache_path)
    return reader
