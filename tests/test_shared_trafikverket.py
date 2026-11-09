"""reference/shared_trafikverket.py: claims by (consumer, brand) with keys, refcounts, a foreign entry left alone."""

import importlib.util
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from saw_livetrack import trafikverket as tv  # noqa: E402

REF = pathlib.Path(__file__).resolve().parents[1] / "reference" / "shared_trafikverket.py"
spec = importlib.util.spec_from_file_location("shared_trafikverket_ref", REF)
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)


class Config:
    def path(self, *parts):
        return "/config/" + "/".join(parts)


class Hass:
    def __init__(self):
        self.data = {}
        self.config = Config()


class TestSharedTrafikverket(unittest.TestCase):
    def test_claims_and_release(self):
        hass = Hass()
        a = shared.get_shared_trafikverket_client(hass, tv.INTERCITY, "sj", "k1")
        b = shared.get_shared_trafikverket_client(hass, tv.INTERCITY, "oresundstag", "k2")
        c = shared.get_shared_trafikverket_client(hass, tv.COMMUTER, "sl_pendeltag", "k3")
        d = shared.get_shared_trafikverket_client(hass, tv.INTERCITY, "sj", "k1")
        self.assertIs(a, b)
        self.assertIs(a, c)
        self.assertIs(a, d)
        self.assertEqual(a.storage_dir, "/config/.storage")
        entry = hass.data[tv.SHARED_KEY]
        self.assertEqual(entry["refcount"], 4)
        self.assertEqual(entry["claims"][(tv.INTERCITY, "sj")], 2)
        self.assertEqual(a.claims, frozenset({(tv.INTERCITY, "sj"), (tv.INTERCITY, "oresundstag"),
                                              (tv.COMMUTER, "sl_pendeltag")}))
        # The oldest key is the one used.
        self.assertEqual(a._ring.active(), "k1")
        shared.release_shared_trafikverket_client(hass, tv.INTERCITY, "sj")
        self.assertIn((tv.INTERCITY, "sj"), a.claims)     # one claim of two left
        self.assertEqual(a._ring.active(), "k1")
        shared.release_shared_trafikverket_client(hass, tv.INTERCITY, "sj")
        self.assertNotIn((tv.INTERCITY, "sj"), a.claims)
        self.assertEqual(a._ring.active(), "k2")
        shared.release_shared_trafikverket_client(hass, tv.TRANSIT, "sj")   # never claimed: nothing
        self.assertEqual(hass.data[tv.SHARED_KEY]["refcount"], 2)
        shared.release_shared_trafikverket_client(hass, tv.INTERCITY, "oresundstag")
        shared.release_shared_trafikverket_client(hass, tv.COMMUTER, "sl_pendeltag")
        self.assertNotIn(tv.SHARED_KEY, hass.data)
        shared.release_shared_trafikverket_client(hass, tv.COMMUTER, "sl_pendeltag")   # gone: nothing

    def test_foreign_entry_private_client(self):
        for foreign in ("a string", {"contract_version": 99},
                        {"contract_version": tv.CONTRACT_VERSION, "entry_shape": 1, "claims": {}, "client": object()},
                        {"contract_version": tv.CONTRACT_VERSION + 1, "entry_shape": 1, "refcount": 1,
                         "claims": {(tv.INTERCITY, "sj"): 1}, "client": tv.TrafikverketClient({})},
                        {"contract_version": tv.CONTRACT_VERSION, "entry_shape": 2, "refcount": 1,
                         "claims": {}, "client": tv.TrafikverketClient({})},
                        {"contract_version": tv.CONTRACT_VERSION, "entry_shape": 1, "refcount": 1,
                         "claims": [], "client": tv.TrafikverketClient({})}):
            hass = Hass()
            hass.data[tv.SHARED_KEY] = foreign
            with self.assertLogs(shared._LOGGER, level="WARNING"):
                c = shared.get_shared_trafikverket_client(hass, tv.INTERCITY, "sj", "k")
            self.assertIsInstance(c, tv.TrafikverketClient)
            self.assertIs(hass.data[tv.SHARED_KEY], foreign)
            self.assertEqual(c.claims, frozenset({(tv.INTERCITY, "sj")}))
            shared.release_shared_trafikverket_client(hass, tv.INTERCITY, "sj")
            self.assertIs(hass.data[tv.SHARED_KEY], foreign)


if __name__ == "__main__":
    unittest.main()
