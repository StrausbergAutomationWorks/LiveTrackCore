"""Shared Irish Rail client registration (Ireland). ONE COPY PER INTEGRATION.

Reference implementation, same pattern as shared_trafikverket.py: copy it byte-exact into each Live Track integration
that draws Irish Rail's trains (Live Track Intercity Rail, "intercity"; Live Track Commuter Rail, "commuter"; Live Track
Transit, "transit"). It lives outside the package because it needs `hass`.

WHAT IT SOLVES. One provider, several integrations, one public IP (05_SHARED_LESSONS.md B16: budget the IP). The first
integration to load creates the client; the others find it; the last to unload removes it. Keyless: a consumer
registers what it draws, nothing more.

THE TIMETABLE AND STATIONS live at .storage/saw_nta_gtfs_irish_rail.zip and .storage/saw_irish_rail_stations.json,
SAW-neutral names: whichever integration needs them first writes them, the others find them.

ON THE EVENT LOOP ONLY (hass.data is not locked). register() and unregister() never wait for a fetch in progress.

THE FAILURE MODE IT GUARDS (B8.1a). The integrations release independently. A consumer that does not understand the
contract version or entry shape it finds builds a PRIVATE client - two pollers is correct, misreading a foreign object
is not - and never overwrites the entry another integration is using.
"""

from __future__ import annotations

import logging

from saw_livetrack.irish_rail import CONTRACT_VERSION, DEFAULT_USER, SHARED_KEY, IrishRailClient

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
    if not isinstance(entry.get("claims"), dict):
        return False
    return isinstance(entry.get("client"), IrishRailClient)


def get_shared_irish_rail_client(hass, consumer, user=DEFAULT_USER):
    """The process-wide Irish Rail client, with `consumer` registered ("intercity", "commuter" or "transit",
    saw_livetrack.irish_rail.CONSUMERS). Two config entries of one consumer register twice and must release twice."""
    storage = hass.config.path(".storage")
    existing = hass.data.get(SHARED_KEY)
    if existing is not None and _usable(existing):
        existing["client"].register(consumer)
        counts = existing["claims"]
        counts[consumer] = counts.get(consumer, 0) + 1
        existing["refcount"] = existing.get("refcount", 0) + 1
        return existing["client"]
    if existing is not None:
        details = existing if isinstance(existing, dict) else {}
        _LOGGER.warning(
            "Found an Irish Rail client at %s that this integration does not understand (contract=%s, shape=%s, "
            "type=%s). Using a private client: expect one extra Irish Rail poll per cycle. Updating all Live Track "
            "integrations resolves it.",
            SHARED_KEY, details.get("contract_version"), details.get("entry_shape"), type(existing).__name__)
        return IrishRailClient((consumer,), user=user, storage_dir=storage)
    client = IrishRailClient((consumer,), user=user, storage_dir=storage)
    hass.data[SHARED_KEY] = {"contract_version": CONTRACT_VERSION, "entry_shape": _ENTRY_SHAPE,
                             "client": client, "refcount": 1, "claims": {consumer: 1}}
    return client


def release_shared_irish_rail_client(hass, consumer):
    """Drop one claim of `consumer`. It is unregistered with its last claim; the entry goes with the last claim of
    all."""
    entry = hass.data.get(SHARED_KEY)
    if not _usable(entry):
        return
    counts = entry["claims"]
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
