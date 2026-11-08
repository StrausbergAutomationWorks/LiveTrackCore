"""Shared Entur client registration (Norway). ONE COPY PER INTEGRATION.

Reference implementation, same pattern as shared_renfe.py: copy it byte-exact into each Live Track integration that
draws Entur's vehicles (Live Track Intercity Rail, "intercity"; Live Track Commuter Rail, "commuter"; a bus
integration, "bus"). It lives outside the package because it needs `hass`.

WHAT IT SOLVES. One provider, several integrations, one public IP (05_SHARED_LESSONS.md B16: budget the IP). The first
integration to load creates the client; the others find it; the last to unload removes it.

EACH CONFIG ENTRY REGISTERS WHAT IT DRAWS AND FROM WHICH CODESPACE (the railroad: "VYG" Vy, "GOA" Go-Ahead Norge,
"FLT" Flytoget). The client polls only the codespaces someone registered and builds feeds only for the claims it holds.

THE HEADERS. Every consumer sends the client's own DEFAULT_USER and its fixed ET-Client-Name, so neither depends on
which integration loaded first. Leave `user` alone.

THE TIMETABLES are kept in Home Assistant's .storage as saw_entur_<codespace>.zip, a SAW-neutral name: whichever
integration needs one first downloads it, the others find it.

ON THE EVENT LOOP ONLY (hass.data is not locked). register() and unregister() never wait for a fetch in progress.

THE FAILURE MODE IT GUARDS (B8.1a). The integrations release independently. A consumer that does not understand the
contract version or entry shape it finds builds a PRIVATE client - two pollers is correct, misreading a foreign object
is not - and never overwrites the entry another integration is using.
"""

from __future__ import annotations

import logging

from saw_livetrack.entur import CONTRACT_VERSION, DEFAULT_USER, SHARED_KEY, EnturClient

_LOGGER = logging.getLogger(__name__)

# 1: the entry counts registrations per (consumer, codespace) claim.
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
    return isinstance(entry.get("client"), EnturClient)


def get_shared_entur_client(hass, consumer, codespace, user=DEFAULT_USER):
    """The process-wide Entur client, with (`consumer`, `codespace`) registered.

    consumer: what this integration draws - "intercity", "commuter" or "bus" (saw_livetrack.entur.CONSUMERS).
    codespace: the railroad's, e.g. "VYG". Two config entries with one claim register twice and must release twice.
    """
    storage = hass.config.path(".storage")
    claim = (consumer, codespace)
    existing = hass.data.get(SHARED_KEY)
    if existing is not None and _usable(existing):
        existing["client"].register(consumer, codespace)
        counts = existing["claims"]
        counts[claim] = counts.get(claim, 0) + 1
        existing["refcount"] = existing.get("refcount", 0) + 1
        return existing["client"]
    if existing is not None:
        details = existing if isinstance(existing, dict) else {}
        _LOGGER.warning(
            "Found an Entur client at %s that this integration does not understand (contract=%s, shape=%s, type=%s). "
            "Using a private client: expect one extra Entur poll per cycle. Updating all Live Track integrations "
            "resolves it.",
            SHARED_KEY, details.get("contract_version"), details.get("entry_shape"), type(existing).__name__)
        return EnturClient([claim], user=user, storage_dir=storage)
    client = EnturClient([claim], user=user, storage_dir=storage)
    hass.data[SHARED_KEY] = {"contract_version": CONTRACT_VERSION, "entry_shape": _ENTRY_SHAPE,
                             "client": client, "refcount": 1, "claims": {claim: 1}}
    return client


def release_shared_entur_client(hass, consumer, codespace):
    """Drop one claim of (`consumer`, `codespace`). It is unregistered with its last claim; the entry goes with the
    last claim of all."""
    entry = hass.data.get(SHARED_KEY)
    if not _usable(entry):
        return
    claim = (consumer, codespace)
    counts = entry["claims"]
    if claim not in counts:
        # Never claimed here: nothing to release (and no refcount to drop).
        return
    left = counts[claim] - 1
    if left > 0:
        counts[claim] = left
    else:
        del counts[claim]
        entry["client"].unregister(consumer, codespace)
    entry["refcount"] = entry.get("refcount", 1) - 1
    if entry["refcount"] <= 0:
        hass.data.pop(SHARED_KEY, None)
