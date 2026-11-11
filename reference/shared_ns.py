"""Shared NS client registration (the Netherlands). ONE COPY PER INTEGRATION.

Reference implementation, same pattern as shared_tfnsw.py: copy it byte-exact into each Live Track integration that
draws trains of NS's vehicle feed (Live Track Intercity Rail, "intercity": IC and the regional ARR trains; Live Track
Commuter Rail, "commuter": SPR). It lives outside the package because it needs `hass`.

WHAT IT SOLVES. One feed, two integrations, one public IP and one 300-requests-per-5-minutes budget per key
(05_SHARED_LESSONS.md B16: budget the IP). The first integration to load creates the client; the others find it; the
last to unload removes it.

EACH CONFIG ENTRY REGISTERS WHAT IT DRAWS, AND ITS OWN KEY (Lee 2026-11-02, D1): the key lives in the config entry. The
client polls with the oldest registered key, and moves on only when one has been refused for 10 minutes. Call this
again with a new key after a reauth; AuthRefused from the client is the signal to ask for one.

THE TRACK AND THE TIMETABLE live at .storage/saw_ns_spoorkaart.json and .storage/saw_ovapi_nl_rail.json.gz,
SAW-neutral names: whichever integration needs them first writes them, the others find them.

ON THE EVENT LOOP ONLY (hass.data is not locked). register() and unregister() never wait for a fetch in progress.

THE FAILURE MODE IT GUARDS (B8.1a). The integrations release independently. A consumer that does not understand the
contract version or entry shape it finds builds a PRIVATE client - two pollers is correct, misreading a foreign object is
not - and never overwrites the entry another integration is using.
"""

from __future__ import annotations

import logging

from saw_livetrack.ns import CONTRACT_VERSION, DEFAULT_USER, SHARED_KEY, NsClient

_LOGGER = logging.getLogger(__name__)

# 1: the entry counts registrations per consumer.
_ENTRY_SHAPE = 1


def _usable(entry):
    if not isinstance(entry, dict):
        return False
    if entry.get("contract_version") != CONTRACT_VERSION:
        return False
    if entry.get("entry_shape") != _ENTRY_SHAPE:
        return False
    if not isinstance(entry.get("consumers"), dict):
        return False
    return isinstance(entry.get("client"), NsClient)


def get_shared_ns_client(hass, consumer, key, user=DEFAULT_USER):
    """The process-wide NS client, with `consumer` registered under `key`.

    consumer: what this integration draws - "intercity" or "commuter" (saw_livetrack.ns.CONSUMERS). Several config
    entries of one integration (Intercity Rail's ns, arriva and keolis) register several times and must release as
    many.
    """
    storage = hass.config.path(".storage")
    existing = hass.data.get(SHARED_KEY)
    if existing is not None and _usable(existing):
        existing["client"].register(consumer, key)
        counts = existing["consumers"]
        counts[consumer] = counts.get(consumer, 0) + 1
        existing["refcount"] = existing.get("refcount", 0) + 1
        return existing["client"]
    if existing is not None:
        details = existing if isinstance(existing, dict) else {}
        _LOGGER.warning(
            "Found an NS client at %s that this integration does not understand (contract=%s, shape=%s, type=%s). "
            "Using a private client: expect one extra NS poll per cycle. Updating all Live Track integrations "
            "resolves it.",
            SHARED_KEY, details.get("contract_version"), details.get("entry_shape"), type(existing).__name__)
        return NsClient({consumer: key}, user=user, storage_dir=storage)
    client = NsClient({consumer: key}, user=user, storage_dir=storage)
    hass.data[SHARED_KEY] = {"contract_version": CONTRACT_VERSION, "entry_shape": _ENTRY_SHAPE,
                             "client": client, "refcount": 1, "consumers": {consumer: 1}}
    return client


def release_shared_ns_client(hass, consumer):
    """Drop one claim of `consumer`. It is unregistered with its last claim; the entry goes with the last claim of all."""
    entry = hass.data.get(SHARED_KEY)
    if not _usable(entry):
        return
    counts = entry["consumers"]
    if consumer not in counts:
        return
    left = counts[consumer] - 1
    if left > 0:
        counts[consumer] = left
    else:
        del counts[consumer]
        entry["client"].unregister(consumer)
    entry["refcount"] = entry.get("refcount", 1) - 1
    if entry["refcount"] <= 0:
        hass.data.pop(SHARED_KEY, None)
