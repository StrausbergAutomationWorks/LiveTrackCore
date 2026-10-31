"""The EnforcementVessels list: the Coast Guard vessels a cited release names in immigration work, by MMSI, and the
THEATRES where that work happens (backlog 315, 0.2.20; moved from Live Track SALT).

The list is the public data repository StrausbergAutomationWorks/EnforcementVessels (ODbL v1.0), release `data`:
enforcementvessels.json and SHA256SUMS. Each consumer credits it with the ODbL line in the README.
! ONE READER PER INSTANCE: shared_list(hass.data, cache_path) gives it to whichever consumer asks first (Live Track
SALT, Live Track Ships), cached at .storage/saw_enforcementvessels.json, a SAW-neutral name.
! The reader follows saw_livetrack.enforcement_list: SHA256SUMS first, installed only when the bytes match; a failure
keeps the cached copy, which never expires.
! THE THEATRES are the file's own rule for where a listed vessel is enforcement work. What a consumer draws outside
them is that consumer's decision: Live Track SALT draws only inside; Live Track Ships draws a listed vessel with the
list's look wherever it draws it (Lee 2026-10-30).
! COPIED BY SPAN from Live Track SALT's vessels.py at 10bf259 (bin\\saw0220_build.py): the constants, Vessel,
Theatre, norm_mmsi, parse, sha_from_sums, the file helpers, VesselList and shared_list. SALT's VesselTracker stays
there. bin\\saw0220_verbatim.py proves the copy.
! No Home Assistant import and no aiohttp import: the session and the executor come from the consumer.
! The list stands alone: no data, code, token or task shared with any other SAW data service (Lee, 2026-10-23).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass

BASE_URL = "https://github.com/StrausbergAutomationWorks/EnforcementVessels/releases/download/data/"
FILE_NAME = "enforcementvessels.json"
SUMS_NAME = "SHA256SUMS"
LIST_URL = BASE_URL + FILE_NAME
SUMS_URL = BASE_URL + SUMS_NAME
# In the consumer's .storage, SAW-neutral (never named after an integration).
CACHE_NAME = "saw_enforcementvessels.json"
STORE_KEY = "saw_enforcementvessels"
SCHEMA = 1

SOURCED = "sourced"
CLEARED = "cleared"

REFRESH_S = 6 * 3600
RETRY_S = 15 * 60
TIMEOUT_S = 60
MAX_BYTES = 16 * 1024 * 1024

_MMSI = re.compile(r"^[0-9]{9}$")
_SUM_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(\S+)\s*$")


@dataclass(frozen=True)
class Vessel:
    mmsi: str
    status: str
    name: str | None = None
    agency: str | None = None
    short: str | None = None
    hull: str | None = None
    entry: str | None = None


@dataclass(frozen=True)
class Theatre:
    id: str
    name: str
    lat_min: float
    lon_min: float
    lat_max: float
    lon_max: float

    def holds(self, lat: float, lon: float) -> bool:
        return self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max


def norm_mmsi(value) -> str | None:
    s = str(value).strip() if value is not None and not isinstance(value, bool) else ""
    return s if _MMSI.match(s) else None


def parse(raw: bytes) -> tuple[dict[str, Vessel], list[Theatre], dict]:
    """The published file -> ({mmsi: Vessel}, [Theatre], header). Raises ValueError on a file not of this shape.
    A bad row or box is skipped, not fatal. A status this reader does not know is installed as given."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ValueError("not JSON: %s" % err) from err
    if not isinstance(doc, dict) or doc.get("name") != "EnforcementVessels":
        raise ValueError("not the EnforcementVessels list")
    if doc.get("schema") != SCHEMA:
        raise ValueError("schema %r, this reader knows %d" % (doc.get("schema"), SCHEMA))
    rows = doc.get("vessels")
    if not isinstance(rows, list):
        raise ValueError("no vessels list")
    shorts = {}
    for e in doc.get("entries") or []:
        if isinstance(e, dict) and isinstance(e.get("id"), str):
            shorts[e["id"]] = e.get("short")
    vessels: dict[str, Vessel] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        m = norm_mmsi(row.get("mmsi"))
        status = row.get("status")
        if m is None or not isinstance(status, str) or not status:
            continue
        vessels[m] = Vessel(m, status, row.get("name"), row.get("agency"), shorts.get(row.get("entry")),
                            row.get("hull"), row.get("entry"))
    theatres: list[Theatre] = []
    for t in doc.get("theatres") or []:
        try:
            a, b, c, d = (float(x) for x in t["box"])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(x) for x in (a, b, c, d)):
            continue
        theatres.append(Theatre(str(t.get("id")), str(t.get("name") or t.get("id")),
                                min(a, c), min(b, d), max(a, c), max(b, d)))
    header = {k: doc.get(k) for k in ("built", "license", "attribution")}
    return vessels, theatres, header


def sha_from_sums(text: str, name: str = FILE_NAME) -> str | None:
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
    tmp = path + ".new"
    with open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, path)


async def _default_run_sync(func, *args):
    return await asyncio.get_running_loop().run_in_executor(None, func, *args)


class _Fault(Exception):
    pass


async def _get(session, url: str) -> bytes:
    async with asyncio.timeout(TIMEOUT_S):
        async with session.get(url) as resp:
            if resp.status != 200:
                raise _Fault("HTTP %d" % resp.status)
            raw = await resp.read()
    if len(raw) > MAX_BYTES:
        raise _Fault("over %d bytes" % MAX_BYTES)
    return raw


class VesselList:
    """The list in memory, its cache on disk, and its refresh."""

    def __init__(self, cache_path: str, clock=time.monotonic) -> None:
        self.cache_path = cache_path
        self._clock = clock
        self._lock = asyncio.Lock()
        self.vessels: dict[str, Vessel] = {}
        self.theatres: list[Theatre] = []
        self.header: dict = {}
        self.sha: str | None = None
        self.checked_at: float | None = None
        self.last_ok: bool | None = None
        self.last_result = "not checked"

    def vessel(self, mmsi) -> Vessel | None:
        m = norm_mmsi(mmsi)
        return self.vessels.get(m) if m else None

    def watched(self) -> frozenset[str]:
        """Every MMSI whose status is not cleared: what the VESSELS subscription asks for."""
        return frozenset(m for m, v in self.vessels.items() if v.status != CLEARED)

    def theatre(self, lat, lon) -> Theatre | None:
        if lat is None or lon is None:
            return None
        for t in self.theatres:
            if t.holds(lat, lon):
                return t
        return None

    @property
    def summary(self) -> str:
        if not self.vessels:
            return "no list" if self.last_ok is not False else "no list (%s)" % self.last_result
        n = sum(1 for v in self.vessels.values() if v.status == SOURCED)
        line = "%d sourced of %d, %d theatres, built %s" % (n, len(self.vessels), len(self.theatres),
                                                            self.header.get("built"))
        return line if self.last_ok is not False else line + " (%s)" % self.last_result

    def _install(self, raw: bytes) -> None:
        self.vessels, self.theatres, self.header = parse(raw)
        self.sha = hashlib.sha256(raw).hexdigest()

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

    def due(self) -> bool:
        if self.checked_at is None:
            return True
        return self._clock() - self.checked_at >= (REFRESH_S if self.last_ok else RETRY_S)

    async def refresh(self, session, run_sync=None, force: bool = False) -> str:
        """"fresh", "unchanged", "updated" or "failed: <why>". Never raises for a network or file fault."""
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
                if isinstance(err, _Fault):
                    why = str(err)
                elif isinstance(err, (asyncio.TimeoutError, TimeoutError)):
                    why = "timeout"
                else:
                    why = type(err).__name__
                result = "failed: %s" % why
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
        if hashlib.sha256(raw).hexdigest() != want:
            raise _Fault("checksum mismatch")
        try:
            parse(raw)
        except ValueError as err:
            raise _Fault("bad file: %s" % err) from err
        await run_sync(_write_file, self.cache_path, raw)
        self._install(raw)
        return "updated"


def shared_list(store: dict, cache_path: str) -> VesselList:
    """The instance's one reader, made on first use. store is hass.data."""
    reader = store.get(STORE_KEY)
    if reader is None:
        reader = store[STORE_KEY] = VesselList(cache_path)
    return reader

