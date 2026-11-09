"""Shared Trafikverket client registration (Sweden). ONE COPY PER INTEGRATION.

Reference implementation, same pattern as shared_tfnsw.py: copy it byte-exact into each Live Track integration that
draws Trafikverket's trains (Live Track Intercity Rail, "intercity"; Live Track Commuter Rail, "commuter"; Live Track
Transit, "transit"). It lives outside the package because it needs `hass`.

WHAT IT SOLVES. One provider, several integrations, one public IP (05_SHARED_LESSONS.md B16: budget the IP). The first
integration to load creates the client; the others find it; the last to unload removes it.

EACH CONFIG ENTRY REGISTERS WHAT IT DRAWS, A (consumer, brand) CLAIM, WITH ITS OWN KEY (Lee 2026-11-02, D1: the key
lives in the entry). The client polls with the oldest registered key and moves on only when one has been refused for
10 minutes. Call this again with a new key after a reauth; AuthRefused from the client is the signal to ask for one.

THE STATIONS live at .storage/saw_trafikverket_stations.json, a SAW-neutral name: whichever integration needs them
first reads them, the others find them.

ON THE EVENT LOOP ONLY (hass.data is not locked). register() and unregister() never wait for a fetch in progress.

THE FAILURE MODE IT GUARDS (B8.1a). The integrations release independently. A consumer that does not understand the
contract version or entry shape it finds builds a PRIVATE client - two pollers is correct, misreading a foreign object
is not - and never overwrites the entry another integration is using.
"""

from __future__ import annotations

import logging

from saw_livetrack.trafikverket import CONTRACT_VERSION, DEFAULT_USER, SHARED_KEY, TrafikverketClient

_LOGGER = logging.getLogger(__name__)

# 1: the entry counts registrations per (consumer, brand) claim.
_ENTRY_SHAPE = 1


def _usable(entry):
    if not isinstance(entry, dict):
        return False
    if entry.get("contract_version") != CONTRACT_VERSION:
        return False
    if entry.get("entry_shape") != _ENTRY_SHAPE:
        return False
    if not isinstance(entry.get("claims"), dict):
        return False
    return isinstance(entry.get("client"), TrafikverketClient)


def get_shared_trafikverket_client(hass, consumer, brand, key, user=DEFAULT_USER):
    """The process-wide Trafikverket client, with (`consumer`, `brand`) registered under `key`.

    consumer: what this integration draws - "intercity", "commuter" or "transit" (saw_livetrack.trafikverket.CONSUMERS).
    brand: the brand id, e.g. "sj" (saw_livetrack.trafikverket.BRANDS). Two config entries with one claim register twice
    and must release twice.
    """
    storage = hass.config.path(".storage")
    claim = (consumer, brand)
    existing = hass.data.get(SHARED_KEY)
    if existing is not None and _usable(existing):
        existing["client"].register(consumer, brand, key)
        counts = existing["claims"]
        counts[claim] = counts.get(claim, 0) + 1
        existing["refcount"] = existing.get("refcount", 0) + 1
        return existing["client"]
    if existing is not None:
        details = existing if isinstance(existing, dict) else {}
        _LOGGER.warning(
            "Found a Trafikverket client at %s that this integration does not understand (contract=%s, shape=%s, "
            "type=%s). Using a private client: expect one extra Trafikverket poll per cycle. Updating all Live Track "
            "integrations resolves it.",
            SHARED_KEY, details.get("contract_version"), details.get("entry_shape"), type(existing).__name__)
        return TrafikverketClient({claim: key}, user=user, storage_dir=storage)
    client = TrafikverketClient({claim: key}, user=user, storage_dir=storage)
    hass.data[SHARED_KEY] = {"contract_version": CONTRACT_VERSION, "entry_shape": _ENTRY_SHAPE,
                             "client": client, "refcount": 1, "claims": {claim: 1}}
    return client


def release_shared_trafikverket_client(hass, consumer, brand):
    """Drop one claim of (`consumer`, `brand`). It is unregistered with its last claim; the entry goes with the last
    claim of all."""
    entry = hass.data.get(SHARED_KEY)
    if not _usable(entry):
        return
    claim = (consumer, brand)
    counts = entry["claims"]
    if claim not in counts:
        # Never claimed here: nothing to release (and no refcount to drop).
        return
    left = counts[claim] - 1
    if left > 0:
        counts[claim] = left
    else:
        del counts[claim]
        entry["client"].unregister(consumer, brand)
    entry["refcount"] = entry.get("refcount", 1) - 1
    if entry["refcount"] <= 0:
        hass.data.pop(SHARED_KEY, None)
