"""A photo of an aircraft from Planespotters.net, looked up when a viewer hovers it (0.2.43).

Hoisted from Live Track Aircraft's photos.py (backlog 361) when Live Track SALT became its second consumer
(05_SHARED_LESSONS.md rule 2). Each consumer serves its own signed-in view and emits its own path as the entity's
`marker_photo_lookup` (06_MAP_CONTRACT.md D4i); the map card asks that path the first time the viewer's hover box
opens, and the view answers from this module in the card's D4g `marker_photo` shape. Nothing is looked up for an
aircraft nobody hovers.

! ONE PER HOME ASSISTANT: shared(hass.data) keeps one PhotoLookups under STORE_KEY, a SAW-neutral key, so with
several consumers installed there is one set of kept answers and one 429/403 pause per instance: the terms'
"reasonable limits" are counted per caller, not per integration.

! THE TERMS (https://www.planespotters.net/photo/api) and how this code keeps them. SAW ships code; each install
makes its own requests. What the code itself decides:
* A server-side caller sends a unique, descriptive User-Agent with a contact URL, and no browser headers:
  USER_AGENT.
* JSON may be kept up to 24 h: kept KEEP_S here (12 h) and at most 12 h more in the viewer's page (the card), so
  nothing shown is older than 24 h. A "no photo" answer is kept the same way; an error is not.
* Images: the viewer's browser loads them straight from the returned `thumbnail_large` URL. Nothing here downloads,
  stores, proxies or rewrites an image, and every URL is passed on unchanged (`link` keeps its `?utm_source=api`).
* The photographer is credited beside the image and the photo links to its Planespotters page (`link`): the card
  (D4g) draws both, in a plain anchor without nofollow.
* Traffic within reasonable limits: one request per aircraft actually hovered, per KEEP_S, one at a time per hex;
  HTTP 429 pauses every lookup (Retry-After, else PAUSE_429_S); HTTP 403 means access was refused or revoked, so
  lookups stop for PAUSE_403_S rather than retrying.
* No lookup for a Privacy ICAO Address (the feed's `identity_hidden`) or anything that is not a 24-bit ICAO
  address (a TIS-B `~` track): lookup_path.

Pure: no homeassistant import. aiohttp is imported only inside _fetch.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Awaitable, Callable

from . import __version__

STORE_KEY = "saw_aircraft_photos"
USER_AGENT = (f"saw-livetrack/{__version__} Live Track aircraft photo lookups "
              "(+https://github.com/StrausbergAutomationWorks/LiveTrackCore)")
API_URL = "https://api.planespotters.net/pub/photos/hex/"
SOURCE = "Planespotters.net"
KEEP_S = 12 * 3600
PAUSE_429_S = 600
PAUSE_403_S = 24 * 3600
TIMEOUT_S = 10
MAX_BODY = 64 * 1024
MAX_CACHE = 5000

_HEX = re.compile(r"^[0-9a-f]{6}$")
_HTTPS = re.compile(r"^https://[^\s\"'<>]+$")
_BASE = re.compile(r"^/api(/[A-Za-z0-9_\-]+)+$")

# what a lookup came to
PHOTO = "photo"      # a photo
NONE = "none"        # Planespotters answered: no photo
LIMITED = "limited"  # 429
REFUSED = "refused"  # 403
ERROR = "error"      # anything else


def valid_hex(value: Any) -> str | None:
    """A 24-bit ICAO address as six lower-case hex digits, or None."""
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    return v if _HEX.match(v) else None


def lookup_path(url_base: str, identity: Any, identity_hidden: Any = False) -> str | None:
    """The entity's `marker_photo_lookup` under the consumer's own `url_base` (e.g.
    "/api/live_track_salt/photo"), or None: not for a TIS-B track or anything that is not an ICAO address, and
    not for a Privacy ICAO Address, whose hex is a temporary one that names no airframe."""
    if identity_hidden or not isinstance(url_base, str) or not _BASE.match(url_base):
        return None
    h = valid_hex(identity)
    return f"{url_base}/{h}" if h else None


def _size(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 10000 else None


def marker_photo(item: Any) -> dict | None:
    """One entry of Planespotters' `photos` -> the card's D4g `marker_photo`, or None when it lacks what the
    terms need shown: an https image, an https page to link, and a photographer to credit. The large thumbnail
    (280 px tall) is preferred: the card draws 160 px in the hover box and 280 px in the click popup."""
    if not isinstance(item, dict):
        return None
    thumb = item.get("thumbnail_large") if isinstance(item.get("thumbnail_large"), dict) else None
    if not thumb or not isinstance(thumb.get("src"), str):
        thumb = item.get("thumbnail") if isinstance(item.get("thumbnail"), dict) else None
    src = thumb.get("src") if thumb else None
    page = item.get("link")
    credit = item.get("photographer")
    if not (isinstance(src, str) and _HTTPS.match(src) and isinstance(page, str) and _HTTPS.match(page)):
        return None
    if not isinstance(credit, str) or not credit.strip():
        return None
    out: dict[str, Any] = {"url": src, "page": page, "credit": credit.strip()[:120], "source": SOURCE}
    size = thumb.get("size") if isinstance(thumb.get("size"), dict) else {}
    w, h = _size(size.get("width")), _size(size.get("height"))
    if w and h:
        out["width"], out["height"] = w, h
    return out


def parse(status: int, body: bytes) -> tuple[str, dict | None]:
    """An HTTP answer -> (PHOTO, photo) | (NONE, None) | (LIMITED|REFUSED|ERROR, None). Branches on the status
    first; the body is detail."""
    if status == 429:
        return LIMITED, None
    if status == 403:
        return REFUSED, None
    if status != 200:
        return ERROR, None
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ERROR, None
    if not isinstance(data, dict) or "error" in data or not isinstance(data.get("photos"), list):
        return ERROR, None
    for item in data["photos"]:
        photo = marker_photo(item)
        if photo:
            return PHOTO, photo
    return NONE, None


Fetch = Callable[[Any, str], Awaitable[tuple[int, dict, bytes]]]


async def _fetch(session, hexid: str) -> tuple[int, dict, bytes]:
    import aiohttp  # noqa: PLC0415 - Home Assistant's own dependency; kept out of the pure part

    async with session.get(API_URL + hexid, timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
                           headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as resp:
        body = bytearray()
        async for chunk in resp.content.iter_chunked(1 << 14):
            body += chunk
            if len(body) > MAX_BODY:
                return 0, {}, b""
        return resp.status, dict(resp.headers), bytes(body)


def _retry_after(headers: dict) -> float:
    for k, v in headers.items():
        if k.lower() == "retry-after":
            try:
                return max(1.0, min(float(v), 3600.0))
            except (TypeError, ValueError):
                break
    return float(PAUSE_429_S)


class PhotoLookups:
    """One per Home Assistant instance: the answers kept, the lookups in flight, and the pause."""

    def __init__(self, fetch: Fetch | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._fetch = fetch or _fetch
        self._clock = clock
        self._kept: dict[str, tuple[float, dict | None]] = {}
        self._flight: dict[str, Any] = {}
        self.paused_until = 0.0
        self.last_state: str | None = None

    def kept(self, hexid: str) -> tuple[bool, dict | None]:
        """(True, photo-or-None) when an answer under KEEP_S old is kept; (False, None) otherwise."""
        got = self._kept.get(hexid)
        if got is None or self._clock() - got[0] > KEEP_S:
            return False, None
        return True, got[1]

    async def get(self, session, hexid: str) -> tuple[str, dict | None]:
        """(PHOTO, photo) or (NONE, None) from the kept answers or one lookup; (LIMITED|REFUSED|ERROR, None)
        while paused or when the lookup failed. Two viewers (or two consumers) asking for one aircraft share one
        request."""
        hit, photo = self.kept(hexid)
        if hit:
            return (PHOTO, photo) if photo else (NONE, None)
        if self._clock() < self.paused_until:
            return (self.last_state if self.last_state in (LIMITED, REFUSED) else LIMITED), None
        flight = self._flight.get(hexid)
        if flight is None:
            flight = self._flight[hexid] = _Flight(self._lookup(session, hexid))
        try:
            return await flight.result()
        finally:
            self._flight.pop(hexid, None)

    async def _lookup(self, session, hexid: str) -> tuple[str, dict | None]:
        try:
            status, headers, body = await self._fetch(session, hexid)
            state, photo = parse(status, body)
        except Exception:  # noqa: BLE001 - network, timeouts
            state, photo, headers = ERROR, None, {}
        self.last_state = state
        now = self._clock()
        if state == LIMITED:
            self.paused_until = now + _retry_after(headers)
        elif state == REFUSED:
            self.paused_until = now + PAUSE_403_S
        elif state in (PHOTO, NONE):
            self._kept.pop(hexid, None)
            self._kept[hexid] = (now, photo)
            while len(self._kept) > MAX_CACHE:
                self._kept.pop(next(iter(self._kept)))
        return state, photo


class _Flight:
    """One lookup awaited by every viewer asking for the same aircraft while it runs."""

    def __init__(self, coro) -> None:
        self._coro = coro
        self._task = None

    async def result(self):
        import asyncio  # noqa: PLC0415

        if self._task is None:
            self._task = asyncio.ensure_future(self._coro)
        return await asyncio.shield(self._task)


def shared(store: dict) -> PhotoLookups:
    """The instance's one PhotoLookups, made on first use. store is the consumer's hass.data."""
    lookups = store.get(STORE_KEY)
    if lookups is None:
        lookups = store[STORE_KEY] = PhotoLookups()
    return lookups


async def answer(store: dict, session, hexid: Any) -> tuple[int, dict | None]:
    """What a consumer's view answers: (400, None) for a bad id; (200, {"photo": ...}); (503, None) while paused or
    after a failed lookup, so the card asks again later. The view adds `Cache-Control: no-store`."""
    h = valid_hex(hexid)
    if h is None:
        return 400, None
    state, photo = await shared(store).get(session, h)
    if state in (PHOTO, NONE):
        return 200, {"photo": photo}
    return 503, None
