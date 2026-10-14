"""Shared Digitraffic rail client registration. ONE COPY PER INTEGRATION.

Reference implementation, same pattern as shared_coordinator.py (Amtraker):
copy it into Live Track Intercity Rail, Live Track Commuter Rail and Live Track
Freight Trains. It lives outside the package because it needs `hass`.

WHAT IT SOLVES. One feed, three integrations, one public IP
(05_SHARED_LESSONS.md B16: budget the IP). The first integration to load
creates the client; the others find it; the last to unload removes it. Each
asks the client for its own consumer's trains (`for_consumer`).

THE FAILURE MODE IT GUARDS (B8.1a). The integrations release independently. A
consumer that does not understand the contract version or entry shape it
finds builds a PRIVATE client - two pollers is correct, misreading a foreign
object is not - and never overwrites the entry another integration is using.
"""

from __future__ import annotations

import logging

from saw_livetrack.digitraffic_rail import (CONTRACT_VERSION, SHARED_KEY,
                                            DigitrafficRailClient)

_LOGGER = logging.getLogger(__name__)

_ENTRY_SHAPE = 1


def _usable(entry):
    if not isinstance(entry, dict):
        return False
    if entry.get("contract_version") != CONTRACT_VERSION:
        return False
    if entry.get("entry_shape") != _ENTRY_SHAPE:
        return False
    return isinstance(entry.get("client"), DigitrafficRailClient)


def get_shared_rail_client(hass, user):
    """The process-wide Digitraffic rail client, created if needed.
    Event loop only (hass.data is not locked)."""
    existing = hass.data.get(SHARED_KEY)
    if existing is not None and _usable(existing):
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
        return DigitrafficRailClient(user=user)
    client = DigitrafficRailClient(user=user)
    hass.data[SHARED_KEY] = {"contract_version": CONTRACT_VERSION,
                             "entry_shape": _ENTRY_SHAPE,
                             "client": client, "refcount": 1}
    return client


def release_shared_rail_client(hass):
    """Drop this integration's claim; remove the entry with the last one."""
    entry = hass.data.get(SHARED_KEY)
    if not _usable(entry):
        return
    entry["refcount"] = entry.get("refcount", 1) - 1
    if entry["refcount"] <= 0:
        hass.data.pop(SHARED_KEY, None)
