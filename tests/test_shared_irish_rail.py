"""reference/shared_irish_rail.py: claims by consumer, refcounts, a foreign entry left alone."""

import importlib.util
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from saw_livetrack import irish_rail as ir  # noqa: E402

REF = pathlib.Path(__file__).resolve().parents[1] / "reference" / "shared_irish_rail.py"
spec = importlib.util.spec_from_file_location("shared_irish_rail_ref", REF)
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)


class Config:
    def path(self, *parts):
        return "/config/" + "/".join(parts)


class Hass:
    def __init__(self):
        self.data = {}
        self.config = Config()


class TestSharedIrishRail(unittest.TestCase):
    def test_claims_and_release(self):
        hass = Hass()
        a = shared.get_shared_irish_rail_client(hass, ir.INTERCITY)
        b = shared.get_shared_irish_rail_client(hass, ir.COMMUTER)
        c = shared.get_shared_irish_rail_client(hass, ir.INTERCITY)
        self.assertIs(a, b)
        self.assertIs(a, c)
        self.assertEqual(a.storage_dir, "/config/.storage")
        entry = hass.data[ir.SHARED_KEY]
        self.assertEqual((entry["refcount"], entry["claims"]), (3, {ir.INTERCITY: 2, ir.COMMUTER: 1}))
        self.assertEqual(a.consumers, frozenset({ir.INTERCITY, ir.COMMUTER}))
        shared.release_shared_irish_rail_client(hass, ir.INTERCITY)
        self.assertIn(ir.INTERCITY, a.consumers)          # one claim of two left
        shared.release_shared_irish_rail_client(hass, ir.INTERCITY)
        self.assertNotIn(ir.INTERCITY, a.consumers)
        shared.release_shared_irish_rail_client(hass, ir.TRANSIT)   # never claimed: nothing
        self.assertEqual(hass.data[ir.SHARED_KEY]["refcount"], 1)
        shared.release_shared_irish_rail_client(hass, ir.COMMUTER)
        self.assertNotIn(ir.SHARED_KEY, hass.data)
        shared.release_shared_irish_rail_client(hass, ir.COMMUTER)  # gone: nothing

    def test_foreign_entry_private_client(self):
        for foreign in ("a string", {"contract_version": 99},
                        {"contract_version": ir.CONTRACT_VERSION, "entry_shape": 1, "claims": {}, "client": object()},
                        {"contract_version": ir.CONTRACT_VERSION + 1, "entry_shape": 1, "refcount": 1,
                         "claims": {ir.INTERCITY: 1}, "client": ir.IrishRailClient()},
                        {"contract_version": ir.CONTRACT_VERSION, "entry_shape": 2, "refcount": 1,
                         "claims": {}, "client": ir.IrishRailClient()},
                        {"contract_version": ir.CONTRACT_VERSION, "entry_shape": 1, "refcount": 1,
                         "claims": [], "client": ir.IrishRailClient()}):
            hass = Hass()
            hass.data[ir.SHARED_KEY] = foreign
            with self.assertLogs(shared._LOGGER, level="WARNING"):
                c = shared.get_shared_irish_rail_client(hass, ir.INTERCITY)
            self.assertIsInstance(c, ir.IrishRailClient)
            self.assertIs(hass.data[ir.SHARED_KEY], foreign)
            self.assertEqual(c.consumers, frozenset({ir.INTERCITY}))
            shared.release_shared_irish_rail_client(hass, ir.INTERCITY)
            self.assertIs(hass.data[ir.SHARED_KEY], foreign)


if __name__ == "__main__":
    unittest.main()
