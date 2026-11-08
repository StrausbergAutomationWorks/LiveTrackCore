"""reference/shared_entur.py: claims by (consumer, codespace), refcounts, a foreign entry left alone."""

import importlib.util
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from saw_livetrack import entur  # noqa: E402

REF = pathlib.Path(__file__).resolve().parents[1] / "reference" / "shared_entur.py"
spec = importlib.util.spec_from_file_location("shared_entur_ref", REF)
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)


class Config:
    def path(self, *parts):
        return "/config/" + "/".join(parts)


class Hass:
    def __init__(self):
        self.data = {}
        self.config = Config()


class TestSharedEntur(unittest.TestCase):
    def test_claims_and_release(self):
        hass = Hass()
        a = shared.get_shared_entur_client(hass, entur.INTERCITY, "VYG")
        b = shared.get_shared_entur_client(hass, entur.INTERCITY, "GOA")
        c = shared.get_shared_entur_client(hass, entur.COMMUTER, "VYG")
        d = shared.get_shared_entur_client(hass, entur.INTERCITY, "VYG")
        self.assertIs(a, b)
        self.assertIs(a, c)
        self.assertIs(a, d)
        self.assertEqual(a.storage_dir, "/config/.storage")
        entry = hass.data[entur.SHARED_KEY]
        self.assertEqual(entry["refcount"], 4)
        self.assertEqual(entry["claims"][(entur.INTERCITY, "VYG")], 2)
        self.assertEqual(a.claims, frozenset({(entur.INTERCITY, "VYG"), (entur.INTERCITY, "GOA"),
                                              (entur.COMMUTER, "VYG")}))
        shared.release_shared_entur_client(hass, entur.INTERCITY, "VYG")
        self.assertIn((entur.INTERCITY, "VYG"), a.claims)     # one claim of two left
        shared.release_shared_entur_client(hass, entur.INTERCITY, "VYG")
        self.assertNotIn((entur.INTERCITY, "VYG"), a.claims)
        shared.release_shared_entur_client(hass, entur.BUS, "VYG")   # never claimed: nothing
        self.assertEqual(hass.data[entur.SHARED_KEY]["refcount"], 2)
        shared.release_shared_entur_client(hass, entur.INTERCITY, "GOA")
        shared.release_shared_entur_client(hass, entur.COMMUTER, "VYG")
        self.assertNotIn(entur.SHARED_KEY, hass.data)
        shared.release_shared_entur_client(hass, entur.COMMUTER, "VYG")   # gone: nothing

    def test_foreign_entry_private_client(self):
        for foreign in ("a string", {"contract_version": 99}, {"contract_version": entur.CONTRACT_VERSION,
                                                              "entry_shape": 1, "claims": {}, "client": object()},
                        # Every field right but the contract version: still foreign.
                        {"contract_version": entur.CONTRACT_VERSION + 1, "entry_shape": 1, "refcount": 1,
                         "claims": {(entur.INTERCITY, "VYG"): 1}, "client": entur.EnturClient()},
                        # And the shape.
                        {"contract_version": entur.CONTRACT_VERSION, "entry_shape": 2, "refcount": 1,
                         "claims": {}, "client": entur.EnturClient()}):
            hass = Hass()
            hass.data[entur.SHARED_KEY] = foreign
            with self.assertLogs(shared._LOGGER, level="WARNING"):
                c = shared.get_shared_entur_client(hass, entur.INTERCITY, "VYG")
            self.assertIsInstance(c, entur.EnturClient)
            self.assertIs(hass.data[entur.SHARED_KEY], foreign)
            self.assertEqual(c.claims, frozenset({(entur.INTERCITY, "VYG")}))
            shared.release_shared_entur_client(hass, entur.INTERCITY, "VYG")
            self.assertIs(hass.data[entur.SHARED_KEY], foreign)


if __name__ == "__main__":
    unittest.main()
