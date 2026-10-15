"""Shared Digitraffic rail client registration. ONE COPY PER INTEGRATION.

Reference implementation, same pattern as shared_coordinator.py (Amtraker):
copy it into Live Track Intercity Rail, Live Track Commuter Rail and Live Track
Freight Trains. It lives outside the package because it needs `hass`.

WHAT IT SOLVES. One feed, three integrations, one public IP
(05_SHARED_LESSONS.md B16: budget the IP). The first integration to load
creates the client; the others find it; the last to unload removes it.

EACH INTEGRATION REGISTERS WHAT IT DRAWS (saw-livetrack 0.2.7). The client
keeps timetables only for the consumers registered with it: "intercity",
"commuter", "freight". An integration installed alone costs only its own
trains; a second one snaps in and its trains are loaded then; one that leaves
takes its trains with it. Measured 2026-10-14: a client keeping every category
held about 138 MB.

THE HEADER (Lee 2026-10-14 21:10). Every consumer sends the client's own
DEFAULT_USER, so the Digitraffic-User header does not depend on which
integration loaded first, or on how many are installed. Leave `user` alone.

ON THE EVENT LOOP ONLY (hass.data is not locked). register() and unregister()
never wait for a fetch in progress: they only record what is wanted, and the
next refresh applies it.

THE FAILURE MODE IT GUARDS (B8.1a). The integrations release independently. A
consumer that does not understand the contract version or entry shape it
finds builds a PRIVATE client - two pollers is correct, misreading a foreign
object is not - and never overwrites the entry another integration is using.
"""

from __future__ import annotations

import logging

from saw_livetrack.digitraffic_rail import (CONTRACT_VERSION, DEFAULT_USER,
                                            SHARED_KEY, DigitrafficRailClient)

_LOGGER = logging.getLogger(__name__)

# 2: the entry counts registrations per consumer (0.2.7).
_ENTRY_SHAPE = 2


def _usable(entry):
    if not isinstance(entry, dict):
        return False
    if entry.get("contract_version") != CONTRACT_VERSION:
        return False
    if entry.get("entry_shape") != _ENTRY_SHAPE:
        return False
    if not isinstance(entry.get("consumers"), dict):
        return False
    return isinstance(entry.get("client"), DigitrafficRailClient)


def get_shared_rail_client(hass, consumer, user=DEFAULT_USER):
    """The process-wide Digitraffic rail client, with `consumer` registered.

    consumer: what this integration draws - "intercity", "commuter" or
    "freight" (saw_livetrack.digitraffic_rail.CONSUMERS). Two config entries
    of one integration register twice and must release twice.
    """
    existing = hass.data.get(SHARED_KEY)
    if existing is not None and _usable(existing):
        existing["client"].register(consumer)
        counts = existing["consumers"]
        counts[consumer] = counts.get(consumer, 0) + 1
        existing["refcount"] = existing.get("refcount", 0) + 1
        return existing["client"]
    if existing is not None:
        details = existing if isinstance(existing, dict) else {}
        _LOGGER.warning(
            "Found a Digitraffic rail client at %s that this integration does "
            "not understand (contract=%s, shape=%s, type=%s). Using a private "
            "client: expect one extra Digitraffic poll per cycle. Updating all "
            "Live Track rail integrations resolves it.",
            SHARED_KEY, details.get("contract_version"),
            details.get("entry_shape"), type(existing).__name__)
        return DigitrafficRailClient(user=user, consumers=(consumer,))
    client = DigitrafficRailClient(user=user, consumers=(consumer,))
    hass.data[SHARED_KEY] = {"contract_version": CONTRACT_VERSION,
                             "entry_shape": _ENTRY_SHAPE,
                             "client": client, "refcount": 1,
                             "consumers": {consumer: 1}}
    return client


def release_shared_rail_client(hass, consumer):
    """Drop one claim of `consumer`. Its trains go with its last claim; the
    entry goes with the last claim of all."""
    entry = hass.data.get(SHARED_KEY)
    if not _usable(entry):
        return
    counts = entry["consumers"]
    if consumer not in counts:
        # Never claimed here: nothing to release (and no refcount to drop).
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
