"""Shared Transperth timetable client registration (Western Australia). ONE COPY PER INTEGRATION.

Reference implementation, same pattern as shared_irish_rail.py: copy it byte-exact into each Live Track integration
that draws a vehicle from the PTA's timetable (Live Track Commuter Rail, "commuter"; Live Track Intercity Rail,
"intercity"; Live Track Transit, "transit"). It lives outside the package because it needs `hass`.

WHAT IT SOLVES. One 27 MB timetable, several integrations, one public IP (05_SHARED_LESSONS.md B16: budget the IP). The
first integration to load creates the client; the others find it; the last to unload removes it. Keyless: a consumer
registers what it draws, nothing more.

THE TIMETABLE lives at .storage/saw_transperth_gtfs.zip, a SAW-neutral name: whichever integration needs it first
writes it, the others find it.

ON THE EVENT LOOP ONLY (hass.data is not locked). register() and unregister() never wait for a download in progress.

THE FAILURE MODE IT GUARDS (B8.1a). The integrations release independently. A consumer that does not understand the
contract version or entry shape it finds builds a PRIVATE client - two copies is correct, misreading a foreign object
is not - and never overwrites the entry another integration is using.
"""

from __future__ import annotations

import logging

from saw_livetrack.transperth import CONTRACT_VERSION, DEFAULT_USER, SHARED_KEY, TransperthClient

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
    return isinstance(entry.get("client"), TransperthClient)


def get_shared_transperth_client(hass, consumer, user=DEFAULT_USER):
    """The process-wide Transperth client, with `consumer` registered ("commuter", "intercity", "transit" or "bus",
    saw_livetrack.transperth.CONSUMERS). Two config entries of one consumer register twice and must release twice."""
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
            "Found a Transperth client at %s that this integration does not understand (contract=%s, shape=%s, "
            "type=%s). Using a private client: expect one extra timetable check a day. Updating all Live Track "
            "integrations resolves it.",
            SHARED_KEY, details.get("contract_version"), details.get("entry_shape"), type(existing).__name__)
        return TransperthClient((consumer,), user=user, storage_dir=storage)
    client = TransperthClient((consumer,), user=user, storage_dir=storage)
    hass.data[SHARED_KEY] = {"contract_version": CONTRACT_VERSION, "entry_shape": _ENTRY_SHAPE,
                             "client": client, "refcount": 1, "claims": {consumer: 1}}
    return client


def release_shared_transperth_client(hass, consumer):
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
