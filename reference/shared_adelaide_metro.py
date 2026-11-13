"""Shared Adelaide Metro client registration. ONE COPY PER INTEGRATION.

Reference implementation, same pattern as shared_digitraffic_rail.py: copy it
byte-exact into each Live Track integration that draws Adelaide Metro's vehicles
(Live Track Commuter Rail, "commuter"; Live Track Transit, "transit"; Live Track
Intercity Buses, "bus"). It lives outside the package because it needs `hass`.

WHAT IT SOLVES. One feed, several integrations, one public IP
(05_SHARED_LESSONS.md B16: budget the IP). The first integration to load
creates the client; the others find it; the last to unload removes it.

EACH INTEGRATION REGISTERS WHAT IT DRAWS. The client builds feeds only for
the consumers registered with it; for_consumer() refuses one that is not.

THE HEADER. Every consumer sends the client's own DEFAULT_USER, so the
User-Agent does not depend on which integration loaded first. Leave `user`
alone.

ON THE EVENT LOOP ONLY (hass.data is not locked). register() and unregister()
never wait for a fetch in progress.

THE FAILURE MODE IT GUARDS (B8.1a). The integrations release independently. A
consumer that does not understand the contract version or entry shape it
finds builds a PRIVATE client - two pollers is correct, misreading a foreign
object is not - and never overwrites the entry another integration is using.
"""

from __future__ import annotations

import logging

from saw_livetrack.adelaide_metro import (AdelaideMetroClient, CONTRACT_VERSION, DEFAULT_USER,
                                          SHARED_KEY)

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
    return isinstance(entry.get("client"), AdelaideMetroClient)


def get_shared_adelaide_metro_client(hass, consumer, user=DEFAULT_USER):
    """The process-wide Adelaide Metro client, with `consumer` registered.

    consumer: what this integration draws - "commuter", "transit" or "bus"
    (saw_livetrack.adelaide_metro.CONSUMERS). Two config entries of one integration
    register twice and must release twice.
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
            "Found an Adelaide Metro client at %s that this integration does not "
            "understand (contract=%s, shape=%s, type=%s). Using a private "
            "client: expect one extra Adelaide Metro poll per cycle. Updating all "
            "Live Track integrations resolves it.",
            SHARED_KEY, details.get("contract_version"),
            details.get("entry_shape"), type(existing).__name__)
        return AdelaideMetroClient(user=user, consumers=(consumer,))
    client = AdelaideMetroClient(user=user, consumers=(consumer,))
    hass.data[SHARED_KEY] = {"contract_version": CONTRACT_VERSION,
                             "entry_shape": _ENTRY_SHAPE,
                             "client": client, "refcount": 1,
                             "consumers": {consumer: 1}}
    return client


def release_shared_adelaide_metro_client(hass, consumer):
    """Drop one claim of `consumer`. It is unregistered with its last claim;
    the entry goes with the last claim of all."""
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
