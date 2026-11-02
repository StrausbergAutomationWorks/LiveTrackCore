"""What the keyed shared clients have in common: a feed a user needs their own key for, read by one
poller for every Live Track integration on one Home Assistant. Pure Python: NO homeassistant imports.

Used by saw_livetrack.tfnsw and saw_livetrack.transport_vic (backlog 319). Decided 2026-11-02 (Lee, D1):
each config entry holds its own key and passes it at registration; the client polls with the oldest.

A REFUSAL IS NOT ONE 401. MEASURED: TfNSW's realtime/nswtrains answered HTTP 401 four times on 2026-10-20
(05:07, 05:08, 05:11, 05:22Z, two of them in a row), the key good before and after. A key is given up on
only after REFUSED_S of refusals with no success between, then the next is tried; with none left the
consumer is told (AuthRefused) and the integration can ask its user.

A KEY IS NEVER PRINTED: not in an error, not in stats(). It travels only in the request's headers.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request

REFUSED_S = 600.0
# After HTTP 429, or Retry-After when the source sends a longer one (05_SHARED_LESSONS.md B16).
BACKOFF_S = 300.0


class KeyedError(Exception):
    """Base error for the keyed clients."""


class RateLimited(KeyedError):
    """HTTP 429. Back off; NOT 'no trains'."""


class FeedUnavailable(KeyedError):
    """Transport, HTTP or decode failure. Distinct from an empty answer (05_SHARED_LESSONS.md C2)."""


class KeyRefused(FeedUnavailable):
    """HTTP 401 or 403 on this request. Not yet a verdict on the key: see REFUSED_S."""


class AuthRefused(KeyedError):
    """Every registered key has been refused for REFUSED_S, or no key is registered."""


def fetch(url, headers, timeout, opener=None, method="GET", max_bytes=None):
    """(body, response headers). Raises RateLimited, KeyRefused or FeedUnavailable. The url and the
    status go in a message; the headers, which hold the key, never do."""
    req = urllib.request.Request(url, headers=dict(headers), method=method)
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(req, timeout=timeout) as resp:
            raw = resp.read() if max_bytes is None else resp.read(max_bytes + 1)
            got = resp.headers
    except urllib.error.HTTPError as err:
        if err.code == 429:
            exc = RateLimited("HTTP 429 from %s" % url)
            exc.retry_after = _retry_after(err.headers)
            raise exc from None
        if err.code in (401, 403):
            raise KeyRefused("HTTP %s from %s" % (err.code, url)) from None
        raise FeedUnavailable("HTTP %s from %s" % (err.code, url)) from None
    except Exception as err:  # noqa: BLE001 - transport of any kind
        raise FeedUnavailable("%s from %s" % (type(err).__name__, url)) from None
    if max_bytes is not None and len(raw) > max_bytes:
        raise FeedUnavailable("more than %d bytes from %s" % (max_bytes, url))
    return raw, got


def _retry_after(headers):
    try:
        return float(headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        return None


class KeyRing:
    """The registered keys, oldest first. Small lock only: register and unregister run on the event loop."""

    def __init__(self, clock):
        self._clock = clock
        self._lock = threading.Lock()
        self._keys = {}         # consumer -> key, in registration order
        self._bad = set()
        self._since = None      # when the current run of refusals began

    def add(self, consumer, key):
        """Register or replace `consumer`'s key. A key given again is trusted again (a reauth)."""
        if not key or not str(key).strip():
            raise ValueError("%s needs an API key" % (consumer,))
        key = str(key).strip()
        with self._lock:
            self._keys[consumer] = key
            self._bad.discard(key)
            self._since = None

    def remove(self, consumer):
        with self._lock:
            key = self._keys.pop(consumer, None)
            if key is not None and key not in self._keys.values():
                self._bad.discard(key)

    def active(self):
        """The key to use, or raises AuthRefused."""
        with self._lock:
            for key in self._keys.values():
                if key not in self._bad:
                    return key
        raise AuthRefused("no usable API key is registered")

    def ok(self):
        with self._lock:
            self._since = None

    def refused(self, key):
        """One refusal of `key`, now. After REFUSED_S of them with no ok() it is given up on."""
        now = self._clock()
        with self._lock:
            if self._since is None:
                self._since = now
            if now - self._since >= REFUSED_S:
                self._bad.add(key)
                self._since = None
                return True
            return False

    def state(self):
        """For stats(): counts only, never a key."""
        with self._lock:
            return {"keys": len(set(self._keys.values())), "keys_refused": len(self._bad)}


class Backoff:
    """One source's 429 backoff."""

    def __init__(self, clock):
        self._clock = clock
        self.until = 0.0

    def check(self, url):
        if self._clock() < self.until:
            raise RateLimited("backing off %s after HTTP 429" % url)

    def hit(self, err):
        self.until = self._clock() + max(BACKOFF_S, getattr(err, "retry_after", None) or 0.0)
