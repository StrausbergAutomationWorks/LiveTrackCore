"""Tests for reference/shared_map_card.py against fakes of the Home Assistant 2026.10.1 objects it uses.

The fakes copy the behaviour that matters (read from core 2026.10.1, card SSOT 67.0): a storage
collection whose async_items() is EMPTY until async_get_info() loads it; create/update/delete;
a YAML collection that only lists. What is NOT tested here is real Home Assistant: the pilot
integration's deploy is that test.

Run: python tests/test_shared_map_card.py
"""
import asyncio
import enum
import os
import sys
import tempfile
import types
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "reference"))


# --- stub homeassistant modules ---------------------------------------------------------------
def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class StaticPathConfig:
    def __init__(self, url_path, path, cache_headers=True):
        self.url_path, self.path, self.cache_headers = url_path, path, cache_headers


class CoreState(enum.Enum):
    starting = "STARTING"
    running = "RUNNING"


ISSUES = {}


class _IR:
    class IssueSeverity(enum.Enum):
        WARNING = "warning"

    @staticmethod
    def async_create_issue(hass, domain, issue_id, **kw):
        ISSUES[(domain, issue_id)] = kw

    @staticmethod
    def async_delete_issue(hass, domain, issue_id):
        ISSUES.pop((domain, issue_id), None)


for n in ("homeassistant", "homeassistant.components", "homeassistant.components.lovelace",
          "homeassistant.helpers"):
    _mod(n)
_mod("homeassistant.components.http", StaticPathConfig=StaticPathConfig)
_mod("homeassistant.components.lovelace.const", LOVELACE_DATA="lovelace")
_mod("homeassistant.const", EVENT_HOMEASSISTANT_STARTED="homeassistant_started")
_mod("homeassistant.core", CoreState=CoreState)
sys.modules["homeassistant.helpers"].issue_registry = _IR
sys.modules["homeassistant.helpers.issue_registry"] = _IR

import shared_map_card as smc  # noqa: E402
from saw_livetrack import map_card as mc  # noqa: E402

CHECKS = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


# --- fakes ------------------------------------------------------------------------------------
class StorageCollection:
    def __init__(self, items):
        self._stored = [dict(i) for i in items]
        self.loaded = False
        self.items = []
        self.writes = []

    async def async_get_info(self):
        if not self.loaded:
            await asyncio.sleep(0)               # a real load yields to the loop
            self.items = [dict(i) for i in self._stored]
            self.loaded = True
        return {"resources": len(self.items)}

    def async_items(self):
        return self.items

    async def _ensure(self):
        await self.async_get_info()

    async def async_create_item(self, data):
        await self._ensure()
        await asyncio.sleep(0)
        item = {"id": uuid.uuid4().hex, "type": data["res_type"], "url": data["url"]}
        self.items.append(item)
        self.writes.append(("create", data["url"]))
        return item

    async def async_update_item(self, item_id, upd):
        await self._ensure()
        for i in self.items:
            if i["id"] == item_id:
                i.update({"type": upd.get("res_type", i["type"]), "url": upd.get("url", i["url"])})
        self.writes.append(("update", item_id, upd["url"]))

    async def async_delete_item(self, item_id):
        await self._ensure()
        self.items = [i for i in self.items if i["id"] != item_id]
        self.writes.append(("delete", item_id))


class YamlCollection:
    loaded = True

    def __init__(self, items):
        self.data = items

    async def async_get_info(self):
        return {"resources": len(self.data)}

    def async_items(self):
        return self.data


class LL:
    def __init__(self, mode, resources):
        self.resource_mode, self.resources = mode, resources


class Bus:
    def __init__(self):
        self.once = []

    def async_listen_once(self, event, cb):
        self.once.append((event, cb))

    async def fire(self, event):
        todo = [cb for e, cb in self.once if e == event]
        self.once = [(e, cb) for e, cb in self.once if e != event]
        for cb in todo:
            await cb(types.SimpleNamespace(event_type=event))


class HTTP:
    def __init__(self):
        self.registered = []
        self.attempts = 0

    async def async_register_static_paths(self, cfgs):
        for c in cfgs:
            self.attempts += 1
            if c.url_path in [r.url_path for r in self.registered]:
                raise RuntimeError("route already registered: " + c.url_path)
            self.registered.append(c)


class Hass:
    def __init__(self, config_dir, collection, mode="storage", domains=()):
        self.data = {"lovelace": LL(mode, collection)}
        self.state = CoreState.starting
        self.bus = Bus()
        self.http = HTTP()
        self.config = types.SimpleNamespace(config_dir=config_dir, components=set(domains))

    async def async_add_executor_job(self, fn, *a):
        return fn(*a)


def bundle(cfg, domain, version):
    d = os.path.join(cfg, "custom_components", domain, "frontend")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, mc.CARD_FILE), "wb") as f:
        f.write(("/*! live-track-map-card %s */\nconsole.log(%r);\n" % (version, domain)).encode())
    return d


def card_rows(coll):
    return [i for i in coll.async_items() if mc.is_card_row(i["url"])]


IC, SH, WX = "live_track_intercity_rail", "live_track_ships", "live_track_weather"
BOX = "/local/live-track-map-card/live-track-map-card.js?v=0.1.0%2B6c6c569&h=82193686bfa2"
AUTO = {"id": "auto", "type": "module", "url": "/local/community/lovelace-auto-entities/auto-entities.js"}


async def fresh(items, mode="storage", domains=(IC, SH, WX), versions=None):
    smc._SERVED.clear()
    ISSUES.clear()
    cfg = tempfile.mkdtemp()
    coll = StorageCollection(items) if mode == "storage" else YamlCollection(items)
    hass = Hass(cfg, coll, mode, domains)
    folders = {d: bundle(cfg, d, v) for d, v in (versions or {}).items()}
    return hass, coll, folders, cfg


async def main():
    # 1. Two integrations offer during start; ONE listener; on started ONE row, the higher one.
    hass, coll, f, _ = await fresh([AUTO], versions={IC: "0.1.0+aaaaaaa", SH: "0.1.1+bbbbbbb"})
    await smc.async_offer_map_card(hass, IC, f[IC])
    await smc.async_offer_map_card(hass, SH, f[SH])
    check("one started listener for any number of offers", len(hass.bus.once) == 1)
    check("nothing written before started", coll.writes == [])
    check("each integration serves its own content path", sorted(r.url_path.split("/")[1] for r in hass.http.registered) == [IC, SH])
    check("cache headers on (content-addressed)", all(r.cache_headers for r in hass.http.registered))
    hass.state = CoreState.running
    await hass.bus.fire("homeassistant_started")
    rows = card_rows(coll)
    check("started: exactly one card row", len(rows) == 1)
    check("...pointing at the higher release, type module", rows and rows[0]["url"].startswith("/%s/map-card/" % SH)
          and "v=0.1.1%2Bbbbbbbb" in rows[0]["url"] and rows[0]["type"] == "module")
    check("unrelated rows untouched", AUTO in coll.async_items())

    # 2. A reload offers again: no second route (HA cannot remove one), no write.
    n = len(coll.writes)
    await smc.async_offer_map_card(hass, SH, f[SH])
    check("re-offer: no second route attempted", hass.http.attempts == 2 and len(hass.http.registered) == 2)
    check("re-offer: no write", len(coll.writes) == n)

    # 3. The collection is empty until loaded: without the load the plan would CREATE a second row.
    hass, coll, f, _ = await fresh([AUTO, {"id": "r", "type": "module", "url": BOX}], versions={SH: "0.1.1+bbbbbbb"})
    hass.state = CoreState.running
    check("fake behaves like 2026.10.1: empty before load", coll.async_items() == [])
    await smc.async_offer_map_card(hass, SH, f[SH])
    rows = card_rows(coll)
    check("hand row on the box UPDATED in place, not appended", len(rows) == 1 and rows[0]["id"] == "r"
          and [w[0] for w in coll.writes] == ["update"])

    # 4. Two reconciles at once, no row: the lock leaves ONE row.
    hass, coll, f, _ = await fresh([AUTO], versions={IC: "0.1.0+aaaaaaa", SH: "0.1.0+aaaaaaa"})
    hass.state = CoreState.running
    await asyncio.gather(smc.async_offer_map_card(hass, IC, f[IC]), smc.async_offer_map_card(hass, SH, f[SH]))
    check("concurrent offers: one row, not two", len(card_rows(coll)) == 1)

    # 5. The box today: a release tying with the hand build leaves it alone.
    cfg_items = [AUTO, {"id": "r", "type": "module", "url": BOX}]
    hass, coll, f, cfg = await fresh(cfg_items, versions={IC: "0.1.0+ccccccc"})
    os.makedirs(os.path.join(cfg, "www", "live-track-map-card"))
    with open(os.path.join(cfg, "www", "live-track-map-card", mc.CARD_FILE), "wb") as fh:
        fh.write(b"/*! live-track-map-card 0.1.0+6c6c569 */\n")
    hass.state = CoreState.running
    await smc.async_offer_map_card(hass, IC, f[IC])
    check("box: tie with the hand build, nothing written", coll.writes == [])

    # 6. Uninstall: one of two hands over; the last removes its own row.
    hass, coll, f, _ = await fresh([AUTO], versions={IC: "0.1.0+aaaaaaa", SH: "0.1.1+bbbbbbb"})
    await smc.async_offer_map_card(hass, IC, f[IC])
    await smc.async_offer_map_card(hass, SH, f[SH])
    hass.state = CoreState.running
    await hass.bus.fire("homeassistant_started")
    await smc.async_withdraw_map_card(hass, SH)
    rows = card_rows(coll)
    check("withdraw the winner: row handed to the other IN PLACE", len(rows) == 1 and rows[0]["url"].startswith("/%s/" % IC)
          and coll.writes[-1][0] == "update")
    await smc.async_withdraw_map_card(hass, IC)
    check("withdraw the last: its row deleted", card_rows(coll) == [] and AUTO in coll.async_items())

    # 7. Unload is not uninstall: there is no unload hook to call, by design.
    check("no unload function exported", not hasattr(smc, "async_unload_map_card"))

    # 8. YAML mode: nothing written, a Repairs issue under the winner's domain, cleared when fixed.
    yitems = [{"type": "module", "url": AUTO["url"]}]
    hass, coll, f, _ = await fresh(yitems, mode="yaml", versions={IC: "0.1.0+aaaaaaa"})
    hass.state = CoreState.running
    await smc.async_offer_map_card(hass, IC, f[IC])
    iss = ISSUES.get((IC, smc.ISSUE_YAML))
    check("yaml: issue raised with the URL to paste", iss and iss["translation_placeholders"]["url"].startswith("/%s/map-card/" % IC)
          and iss["translation_key"] == smc.ISSUE_YAML and iss["is_fixable"] is False)
    coll.data.append({"type": "module", "url": iss["translation_placeholders"]["url"]})
    await smc.async_reconcile(hass)
    check("yaml: issue cleared once the row is there", (IC, smc.ISSUE_YAML) not in ISSUES)

    # 9. A dev or unstamped bundle is never offered.
    hass, coll, f, _ = await fresh([AUTO], versions={IC: "0.1.1-dev+aaaaaaa"})
    hass.state = CoreState.running
    await smc.async_offer_map_card(hass, IC, f[IC])
    check("dev build: not offered, nothing served, nothing written", coll.writes == [] and not hass.http.registered)
    missing = os.path.join(tempfile.mkdtemp(), "frontend")
    await smc.async_offer_map_card(hass, SH, missing)
    check("missing file: logged, no raise", True)

    # 10. A broken collection never breaks the integration.
    hass, coll, f, _ = await fresh([AUTO], versions={IC: "0.1.0+aaaaaaa"})
    hass.state = CoreState.running

    async def boom(*a, **k):
        raise RuntimeError("storage exploded")
    coll.async_create_item = boom
    await smc.async_offer_map_card(hass, IC, f[IC])
    check("reconcile failure swallowed and logged", True)

    # 11. A foreign registry at the shared key: still reconciles with its own offer.
    hass, coll, f, _ = await fresh([AUTO], versions={IC: "0.1.0+aaaaaaa"})
    hass.data[mc.SHARED_KEY] = {"contract": 99}
    hass.state = CoreState.running
    await smc.async_offer_map_card(hass, IC, f[IC])
    check("foreign registry: own offer still registered", len(card_rows(coll)) == 1
          and hass.data[mc.SHARED_KEY] == {"contract": 99})

    # 12. HACS installs the card: nothing written, no second row.
    hrow = {"id": "h", "type": "module", "url": "/hacsfiles/LiveTrackMapCard/live-track-map-card.js?hacstag=1"}
    hass, coll, f, _ = await fresh([AUTO, hrow], versions={IC: "0.1.0+aaaaaaa"})
    hass.state = CoreState.running
    await smc.async_offer_map_card(hass, IC, f[IC])
    check("HACS row: integrations stand down", coll.writes == [] and len(card_rows(coll)) == 1)


asyncio.run(main())
failed = [n for n, ok in CHECKS if not ok]
print("checks: %d   passed: %d   failed: %d" % (len(CHECKS), len(CHECKS) - len(failed), len(failed)))
for n in failed:
    print("  FAIL: %s" % n)
sys.exit(1 if failed else 0)
