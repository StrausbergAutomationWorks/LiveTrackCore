"""A photo of a vessel from Wikidata and Wikimedia Commons: by IMO, else by MMSI (0.2.43).

Hoisted from Live Track Ships' photos.py (backlog 346) when Live Track SALT became its second consumer
(05_SHARED_LESSONS.md rule 2), and widened on 2026-11-21: a vessel is looked up by its IMO number (P458) and,
when the IMO finds nothing or the vessel has none, by its MMSI (P587). The answer is the card's D4g
`marker_photo`; the consumer puts it on the entity.

HOW (measured from the PC):
  * Wikidata's QUERY SERVICE answered 429 "Aggressively rate-limiting to 1 req / min" (2026-11-16), so it is NOT
    used. The Action API is: `list=search` with `haswbstatement:P458=<imo>|P458=<imo>...` (or P587 for MMSI)
    finds several ships in one request; `wbgetentities` reads each item back, and only an item whose own IMO or
    MMSI is one we asked for counts; Commons `imageinfo` gives a 320 px thumbnail, the file's page, the author
    (HTML: stripped here) and the licence, ten files a request.
  * 2026-11-16, Live Track Ships: of 521 IMO numbers on one map, 322 matched an item, 49 with an image.
  * 2026-11-21, the EnforcementVessels list (41 Coast Guard vessels, none with an IMO): by MMSI 33 items, 29 with
    an image (bin\\ltsalt_photo_probe.py).

! ONE BOOK PER HOME ASSISTANT under STORE_KEY, a SAW-neutral key, cache `.storage/<CACHE_NAME>`. It is NOT Live
  Track Ships' old key and file (`saw_vessel_photos`): a Ships release from before 0.2.43 keeps those, and two
  classes under one key would hand each other an object they cannot use. On first creation the IMO answers in that
  old file are read once (read-only), so nothing already found is asked again.
! Each install looks up the vessels on ITS OWN map, for its own user; nothing passes through SAW.
! Wikimedia asks every client for a User-Agent naming it and a way to reach its maintainer: USER_AGENT.
! A photo found stays FOUND_TTL_S; an IMO or MMSI with none is asked again after NONE_TTL_S. At most one batch
  every PAUSE_S per instance.

Pure apart from the network calls (aiohttp, imported inside lookup); no homeassistant import.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time

from . import __version__

_LOGGER = logging.getLogger(__name__)

STORE_KEY = "saw_vessel_photo_book"
CACHE_NAME = "saw_vessel_photo_book.json"
LEGACY_CACHE_NAME = "saw_vessel_photos.json"      # Live Track Ships' own cache before 0.2.43, IMO keys
USER_AGENT = (f"saw-livetrack/{__version__} Live Track vessel photo lookups "
              "(https://github.com/StrausbergAutomationWorks/LiveTrackCore)")
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
FOUND_TTL_S = 30 * 86400
NONE_TTL_S = 14 * 86400
BATCH = 20                 # numbers per search request
PAUSE_S = 10.0             # between batches: at most 6 lookups a minute per install
THUMB_PX = 320
CREDIT_MAX = 120
CACHE_MAX = 20000

IMO, MMSI = "imo", "mmsi"
PROPERTY = {IMO: "P458", MMSI: "P587"}
_DIGITS = {IMO: re.compile(r"^\d{7}$"), MMSI: re.compile(r"^\d{9}$")}


# -- pure helpers --------------------------------------------------------------------------------------------
def valid(kind: str, value) -> str | None:
    """A 7-digit IMO or a 9-digit MMSI as a string, or None."""
    if value is None or isinstance(value, bool):
        return None
    v = str(value).strip()
    return v if _DIGITS[kind].match(v) else None


def key(kind: str, number: str) -> str:
    return f"{kind}:{number}"


def split(k: str) -> tuple[str, str]:
    kind, _, number = k.partition(":")
    return kind, number


def search_query(kind: str, numbers) -> str:
    return "haswbstatement:" + "|".join(f"{PROPERTY[kind]}={n}" for n in numbers)


def files_by_number(entities: dict, kind: str, wanted) -> dict:
    """wbgetentities' entities -> {number: Commons file name}, only for an item whose own IMO (or MMSI) is one we
    asked for (the search matches on a statement, but the item is read back to be sure)."""
    wanted = set(wanted)
    prop = PROPERTY[kind]
    out = {}
    for ent in (entities or {}).values():
        claims = ent.get("claims") or {}
        numbers = {str(c.get("mainsnak", {}).get("datavalue", {}).get("value", "")) for c in claims.get(prop, [])}
        files = [c.get("mainsnak", {}).get("datavalue", {}).get("value") for c in claims.get("P18", [])
                 if c.get("rank") != "deprecated"]
        files = [f for f in files if isinstance(f, str) and f]
        for n in sorted(numbers & wanted):
            if files and n not in out:
                out[n] = files[0]
    return out


_TAG = re.compile(r"<[^>]+>")


def plain(value, limit=CREDIT_MAX) -> str:
    """Commons' Artist is HTML (a user link, sometimes a whole table): text only, one line, cut."""
    text = html.unescape(_TAG.sub(" ", str(value or "")))
    text = " ".join(text.split())
    return text[:limit].rstrip()


def _https(url) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("http://"):
        url = "https://" + url[7:]
    return url if url.startswith("https://") else None


def photo_from_imageinfo(page: dict) -> dict | None:
    """One Commons page with imageinfo -> D4g `marker_photo`, or None when it lacks a thumbnail or a page."""
    info = (page.get("imageinfo") or [{}])[0]
    meta = info.get("extmetadata") or {}
    thumb = _https(info.get("thumburl"))
    link = _https(info.get("descriptionurl"))
    if not thumb or not link:
        return None
    thumb = thumb.split("?", 1)[0]          # Commons appends utm_* tracking parameters
    credit = plain(meta.get("Artist", {}).get("value")) or plain(meta.get("Credit", {}).get("value"))
    photo = {
        "url": thumb,
        "page": link,
        "credit": credit or "Wikimedia Commons contributor",
        "licence": plain(meta.get("LicenseShortName", {}).get("value"), 60) or "see the photo's page",
        "source": "Wikimedia Commons",
    }
    lic = _https(meta.get("LicenseUrl", {}).get("value"))
    if lic:
        photo["licence_url"] = lic
    for k in ("thumbwidth", "thumbheight"):
        if isinstance(info.get(k), int):
            photo["width" if k == "thumbwidth" else "height"] = info[k]
    return photo


# -- the book: cache, queue, worker --------------------------------------------------------------------------
class PhotoBook:
    """One per Home Assistant: every consumer asks it; it asks Wikidata at most once a PAUSE_S."""

    def __init__(self, path: str, clock=time.time, legacy_path: str | None = None):
        self.path = path
        self.legacy_path = legacy_path
        self._clock = clock
        self._cache: dict[str, dict] = {}        # "imo:N" | "mmsi:N" -> {"t": epoch, "photo": dict|None}
        self._queue: list[str] = []
        self._queued: set[str] = set()
        self._waiters: dict[str, set] = {}
        self._dirty = False
        self._task = None
        self.lookups = 0

    # disk
    def load(self) -> None:
        """BLOCKS. The book's own cache; when it has none yet, the IMO answers of Live Track Ships' old cache,
        read once and never written."""
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._cache = {k: v for k, v in data.items()
                               if isinstance(v, dict) and "t" in v and split(k)[0] in PROPERTY}
            return
        except FileNotFoundError:
            pass
        except Exception as exc:                      # noqa: BLE001 - a bad cache is started over, never fatal
            _LOGGER.warning("Vessel photo cache not read, starting empty: %s", exc)
            return
        if not self.legacy_path:
            return
        try:
            with open(self.legacy_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:                             # noqa: BLE001 - absent or unreadable: nothing to carry over
            return
        if isinstance(data, dict):
            for k, v in data.items():
                imo = valid(IMO, k)
                if imo and isinstance(v, dict) and "t" in v:
                    self._cache[key(IMO, imo)] = v
            self._dirty = bool(self._cache)

    def save(self) -> None:
        """BLOCKS."""
        if not self._dirty:
            return
        if len(self._cache) > CACHE_MAX:
            for k, _ in sorted(self._cache.items(), key=lambda kv: kv[1]["t"])[:len(self._cache) - CACHE_MAX]:
                del self._cache[k]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, self.path)
        self._dirty = False

    # asking
    def _fresh(self, rec) -> bool:
        if rec is None:
            return False
        return self._clock() - rec["t"] < (FOUND_TTL_S if rec.get("photo") else NONE_TTL_S)

    def photo(self, imo=None, mmsi=None, waiter=None) -> dict | None:
        """The photo known for this vessel, or None: by IMO, and by MMSI when the IMO found nothing or there is
        none. An unknown or stale number is queued (the MMSI only once the IMO has answered "none"); `waiter` is
        called once when that answer arrives, and the consumer asks again then (its entity writes its state)."""
        keys = []
        for kind, value in ((IMO, imo), (MMSI, mmsi)):
            v = valid(kind, value)
            if v:
                keys.append(key(kind, v))
        for k in keys:
            rec = self._cache.get(k)
            if self._fresh(rec):
                if rec.get("photo"):
                    return rec["photo"]
                continue                              # answered: no photo; try the next number
            if k not in self._queued:
                self._queue.append(k)
                self._queued.add(k)
            if waiter is not None:
                self._waiters.setdefault(k, set()).add(waiter)
            return rec.get("photo") if rec else None  # a stale photo is shown while it is asked again
        return None

    def forget_waiter(self, waiter) -> None:
        """The consumer's entity went away: it is not called for any number."""
        for k in [k for k, s in self._waiters.items() if waiter in s]:
            s = self._waiters[k]
            s.discard(waiter)
            if not s:
                del self._waiters[k]

    def take_batch(self) -> list:
        """Up to BATCH queued keys of ONE kind (the kind of the oldest queued key)."""
        if not self._queue:
            return []
        kind = split(self._queue[0])[0]
        batch = [k for k in self._queue if split(k)[0] == kind][:BATCH]
        taken = set(batch)
        self._queue = [k for k in self._queue if k not in taken]
        self._queued -= taken
        return batch

    def requeue(self, batch) -> None:
        for k in batch:
            if k not in self._queued:
                self._queue.append(k)
                self._queued.add(k)

    def settle(self, asked, found: dict) -> list:
        """Record the answers for `asked` (found: key -> photo). Returns the waiters to call."""
        now = self._clock()
        call = []
        for k in asked:
            self._cache[k] = {"t": now, "photo": found.get(k)}
            call.extend(self._waiters.pop(k, ()))
        self._dirty = True
        return call

    # the network
    async def lookup(self, session, batch) -> dict:
        """key -> photo for those of `batch` (one kind) Wikidata has a photo of. Raises on a network failure."""
        if not batch:
            return {}
        kind = split(batch[0])[0]
        numbers = [split(k)[1] for k in batch]
        headers = {"User-Agent": USER_AGENT}
        timeout = _timeout(30)
        params = {"action": "query", "list": "search", "srsearch": search_query(kind, numbers), "srlimit": 50,
                  "srprop": "", "format": "json"}
        async with session.get(WIKIDATA_API, params=params, headers=headers, timeout=timeout) as r:
            r.raise_for_status()
            hits = [h["title"] for h in (await r.json()).get("query", {}).get("search", [])]
        if not hits:
            return {}
        params = {"action": "wbgetentities", "ids": "|".join(hits[:50]), "props": "claims", "format": "json"}
        async with session.get(WIKIDATA_API, params=params, headers=headers, timeout=timeout) as r:
            r.raise_for_status()
            files = files_by_number((await r.json()).get("entities"), kind, numbers)
        if not files:
            return {}
        titles = {f"File:{f}": n for n, f in files.items()}
        params = {"action": "query", "titles": "|".join(titles), "prop": "imageinfo",
                  "iiprop": "url|extmetadata", "iiurlwidth": THUMB_PX, "format": "json",
                  "iiextmetadatafilter": "Artist|Credit|LicenseShortName|LicenseUrl"}
        async with session.get(COMMONS_API, params=params, headers=headers, timeout=timeout) as r:
            r.raise_for_status()
            data = await r.json()
        return photos_from_commons(data, titles, kind)

    async def run(self, session, executor_job) -> None:
        """The worker: one batch every PAUSE_S while there is anything to ask; saves after each answer.
        executor_job is the consumer's hass.async_add_executor_job."""
        while True:
            await asyncio.sleep(PAUSE_S)
            batch = self.take_batch()
            if not batch:
                continue
            try:
                found = await self.lookup(session, batch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                  # noqa: BLE001 - asked again later, never fatal
                _LOGGER.debug("Vessel photo lookup failed (%s); asked again later", exc)
                self.requeue(batch)
                await asyncio.sleep(60)
                continue
            self.lookups += 1
            for cb in self.settle(batch, found):
                try:
                    cb()
                except Exception:                     # noqa: BLE001
                    _LOGGER.debug("photo waiter failed", exc_info=True)
            try:
                await executor_job(self.save)
            except Exception as exc:                  # noqa: BLE001
                _LOGGER.warning("Vessel photo cache not saved: %s", exc)


def _timeout(total: float):
    """aiohttp's ClientTimeout (Home Assistant's own dependency); None where aiohttp is absent, as in a test with a
    fake session."""
    try:
        import aiohttp  # noqa: PLC0415
    except ImportError:
        return None
    return aiohttp.ClientTimeout(total=total)


def photos_from_commons(data: dict, titles: dict, kind: str) -> dict:
    """Commons' imageinfo answer -> key -> photo. Commons normalises titles (underscores, capitals): mapped back
    through its own table."""
    norm = {n["to"]: n["from"] for n in (data.get("query", {}).get("normalized") or [])}
    out = {}
    for page in (data.get("query", {}).get("pages") or {}).values():
        title = norm.get(page.get("title"), page.get("title"))
        number = titles.get(title) or titles.get(page.get("title"))
        photo = photo_from_imageinfo(page) if number else None
        if photo:
            out[key(kind, number)] = photo
    return out


async def shared(store: dict, storage_dir: str, executor_job, start_task, session) -> PhotoBook:
    """The instance's PhotoBook, started on first use: cache read in the executor, worker running for the rest of
    the run. store is hass.data; storage_dir is hass.config.path(".storage"); start_task(coro) starts a background
    task (hass.async_create_background_task); session is Home Assistant's shared client session."""
    book = store.get(STORE_KEY)
    if book is None:
        book = store[STORE_KEY] = PhotoBook(os.path.join(storage_dir, CACHE_NAME),
                                            legacy_path=os.path.join(storage_dir, LEGACY_CACHE_NAME))
        await executor_job(book.load)
        book._task = start_task(book.run(session, executor_job))
    return book
