"""Live Track Map Card: serve, offer and register the card. ONE COPY PER INTEGRATION.

Reference implementation (backlog 291; 35_Dependencies/ours/live-track-map-card/SSOT.md
section 67). Copy it VERBATIM into every Live Track integration as shared_map_card.py;
bin\\lt_reference_drift.py compares the copies with this file. The decisions are in
saw_livetrack.map_card (one copy per Python environment); this file only makes the Home
Assistant calls, because the package must not import homeassistant (05_SHARED_LESSONS.md B8.1a).
Checked against Home Assistant 2026.10.1.

USE, in the integration's __init__.py:

    from .shared_map_card import async_offer_map_card, async_withdraw_map_card
    HERE = Path(__file__).parent

    async def async_setup_entry(hass, entry):
        await async_offer_map_card(hass, DOMAIN, HERE / "frontend")   # never raises
        ...

    async def async_remove_entry(hass, entry):
        if not [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]:
            await async_withdraw_map_card(hass, DOMAIN)

Nothing in async_unload_entry: every restart and reload unloads, and that is not an uninstall.

manifest.json: "dependencies" includes "http"; "after_dependencies": ["lovelace"];
"requirements" includes "saw-livetrack>=0.2.8,<0.3".

translations/en.json needs, under "issues" (the reconcile raises them under the winning
integration's domain, so every integration carries both):
    "map_card_yaml_resource": {"title": "Add the Live Track map card to your resources",
      "description": "Your dashboards' resources are set in YAML, so the map card cannot be
        registered automatically. Add this under lovelace: resources: in configuration.yaml,
        then restart:\n\n- url: {url}\n  type: module"},
    "map_card_duplicate_resources": {"title": "The Live Track map card is registered more than once",
      "description": "These dashboard resources also load the map card: {urls}. Only one is
        used and the others do nothing. Remove them in Settings > Dashboards > Resources."}

WHEN IT RUNS. An offer serves the folder once per run (a route cannot be removed) and records
the offer. The reconcile runs once on homeassistant_started, after every integration has
offered; an offer made later (an integration added while running) reconciles at once. Every
reconcile is under one lock, so two integrations never both see "no row" and both add one.

THE FAILURE IT GUARDS (B8.1a, adapted). A copy that finds a shared entry it does not understand
still reconciles, with its own offer: the table converges on the highest version from either
side. There is one row, so a second private registry would fight over it rather than help.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.helpers import issue_registry as ir

from saw_livetrack import map_card as mc

_LOGGER = logging.getLogger(__name__)

ISSUE_YAML = "map_card_yaml_resource"
ISSUE_DUPLICATES = "map_card_duplicate_resources"
_SERVED = {}      # this copy's domain -> static base it registered this run


def _entry(hass):
    """The shared offers registry, or None when another contract holds the key."""
    e = hass.data.get(mc.SHARED_KEY)
    if e is None:
        e = hass.data[mc.SHARED_KEY] = {"contract": mc.CONTRACT_VERSION, "offers": {},
                                        "listening": False}
        return e
    if isinstance(e, dict) and e.get("contract") == mc.CONTRACT_VERSION and isinstance(e.get("offers"), dict):
        return e
    _LOGGER.warning("Found a map-card registry at %s this integration does not understand; "
                    "reconciling with its own offer only. Updating every Live Track "
                    "integration resolves it.", mc.SHARED_KEY)
    return None


def _lock(hass):
    lock = hass.data.setdefault(mc.LOCK_KEY, asyncio.Lock())
    if isinstance(lock, asyncio.Lock):
        return lock
    _LOGGER.warning("%s holds a %s, not a lock: map-card changes are not serialised",
                    mc.LOCK_KEY, type(lock).__name__)
    return asyncio.Lock()


def _read_bundle(folder):
    data = (Path(folder) / mc.CARD_FILE).read_bytes()
    return data, hashlib.sha256(data).hexdigest()[:12]


async def async_offer_map_card(hass, domain, folder):
    """Serve this integration's card and offer it. Never raises: the entities matter more."""
    try:
        try:
            data, sha12 = await hass.async_add_executor_job(_read_bundle, folder)
        except OSError as err:
            _LOGGER.warning("Live Track map card: %s has no %s (%s): not offered",
                            folder, mc.CARD_FILE, err.strerror)
            return
        version = mc.read_version(data)
        if version is None or not mc.is_release(version):
            _LOGGER.warning("%s/%s is not a released, stamped card (%r): not offered",
                            folder, mc.CARD_FILE, version)
            return
        base = mc.static_base(domain, sha12)
        if _SERVED.get(domain) != base:
            await hass.http.async_register_static_paths([StaticPathConfig(base, str(folder), True)])
            _SERVED[domain] = base
        offer = mc.Offer(domain, version, mc.resource_url(domain, sha12, version))
        entry = _entry(hass)
        if entry is not None:
            entry["offers"][domain] = offer
        if hass.state is CoreState.running:
            await async_reconcile(hass, own=offer if entry is None else None)
        elif entry is not None and not entry["listening"]:
            entry["listening"] = True

            async def _started(_event):
                await async_reconcile(hass)

            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started)
        elif entry is None:
            async def _started_own(_event):
                await async_reconcile(hass, own=offer)

            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started_own)
    except Exception:  # noqa: BLE001 - a card problem must never stop the integration loading
        _LOGGER.exception("Live Track map card: offer from %s failed", domain)


async def async_withdraw_map_card(hass, domain):
    """The domain's last entry is being removed: hand the row on, or remove our own."""
    try:
        entry = _entry(hass)
        if entry is not None:
            entry["offers"].pop(domain, None)
        await async_reconcile(hass, withdrawn=domain)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Live Track map card: withdrawal of %s failed", domain)


def _infos(config_dir, rows, loaded, withdrawn):
    """Read the file behind every card row (executor)."""
    out = {}
    for r in rows:
        url = r.get("url", "")
        if not mc.is_card_row(url):
            continue
        rf = mc.row_file(url)
        data = sha12 = None
        if rf.rel_path is not None:
            p = Path(config_dir) / rf.rel_path
            try:
                data = p.read_bytes()
                sha12 = hashlib.sha256(data).hexdigest()[:12]
            except OSError:
                data = None
        live = rf.domain in loaded and rf.domain != withdrawn if rf.kind == mc.SAW else True
        out[url] = mc.inspect(url, data, sha12, live)
    return out


async def async_reconcile(hass, own=None, withdrawn=None):
    """Bring the ONE card resource row in line with the highest offer (section 67.3)."""
    try:
        async with _lock(hass):
            ll = hass.data.get(LOVELACE_DATA)
            if ll is None:
                _LOGGER.debug("Live Track map card: Lovelace is not set up; nothing to register")
                return
            resources = ll.resources                 # fetched afresh: reload_resources replaces it
            mode = ll.resource_mode
            if mode == "storage":
                await resources.async_get_info()     # LOADS it: async_items() is empty before
            rows = [dict(r) for r in (resources.async_items() or [])]
            entry = hass.data.get(mc.SHARED_KEY)
            offers = list(entry["offers"].values()) if (isinstance(entry, dict)
                          and entry.get("contract") == mc.CONTRACT_VERSION) else []
            if own is not None and own not in offers:
                offers.append(own)
            infos = await hass.async_add_executor_job(
                _infos, hass.config.config_dir, rows, set(hass.config.components), withdrawn)
            p = mc.plan(rows, offers, infos, mode=mode, withdrawn=withdrawn)

            if mode == "storage":
                for rid in p.delete:
                    await resources.async_delete_item(rid)
                for rid, url in p.update:
                    await resources.async_update_item(rid, {"res_type": "module", "url": url})
                if p.create:
                    await resources.async_create_item({"res_type": "module", "url": p.create})
            changed = bool(p.delete or p.update or p.create)
            (_LOGGER.info if changed else _LOGGER.debug)(
                "Live Track map card: %s (winner %s; created %s, updated %d, deleted %d)",
                "; ".join(p.notes), p.winner and "%s %s" % (p.winner.domain, p.winner.version),
                bool(p.create), len(p.update), len(p.delete))
            if p.hacs_older:
                _LOGGER.info("Live Track map card: HACS installs the card here, and %s carries a "
                             "newer release (%s). Update the card in HACS.", p.winner.domain,
                             p.winner.version)
            _issues(hass, offers, p)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Live Track map card: reconcile failed")


def _issues(hass, offers, p):
    """Raise or clear the two Repairs issues, under the winner's domain only."""
    domains = {o.domain for o in offers}
    win = p.winner.domain if p.winner else None
    for d in domains:
        if d != win:
            ir.async_delete_issue(hass, d, ISSUE_YAML)
            ir.async_delete_issue(hass, d, ISSUE_DUPLICATES)
    if win is None:
        return
    if p.yaml_needs:
        ir.async_create_issue(hass, win, ISSUE_YAML, is_fixable=False,
                              severity=ir.IssueSeverity.WARNING, translation_key=ISSUE_YAML,
                              translation_placeholders={"url": p.yaml_needs})
    else:
        ir.async_delete_issue(hass, win, ISSUE_YAML)
    if p.user_duplicates:
        ir.async_create_issue(hass, win, ISSUE_DUPLICATES, is_fixable=False,
                              severity=ir.IssueSeverity.WARNING, translation_key=ISSUE_DUPLICATES,
                              translation_placeholders={"urls": ", ".join(p.user_duplicates)})
    else:
        ir.async_delete_issue(hass, win, ISSUE_DUPLICATES)
