"""saw_livetrack.airlines (0.2.17, moved from Live Track Aircraft, Lee 2026-10-29): the operator and the hover's short name."""
import unittest

from saw_livetrack import airlines


class Tables(unittest.TestCase):
    def test_every_designator_has_a_short_name_but_the_non_commercial(self):
        self.assertEqual(set(airlines._OPERATORS) - set(airlines._SHORT), {"RCH", "CAP"})

    def test_no_short_name_without_an_operator(self):
        self.assertEqual(set(airlines._SHORT) - set(airlines._OPERATORS), set())

    def test_short_names_fit(self):
        for code, short in airlines._SHORT.items():
            self.assertTrue(0 < len(short) <= 22 and short == short.strip(), (code, short))

    def test_the_charter_operators(self):
        self.assertEqual({c: airlines._SHORT[c] for c in ("GXA", "BBQ", "VXP", "LYM", "SWQ")},
                         {"GXA": "GlobalX", "BBQ": "Eastern Express", "VXP": "Avelo", "LYM": "Key Lime", "SWQ": "iAero"})
        self.assertEqual(airlines.lookup_operator("GXA6181"), ("GXA", "GlobalX"))


class Lookup(unittest.TestCase):
    def test_mainline_and_charter(self):
        for cs, short in (("UAL1678", "United"), ("AAL861", "American"), ("DAL1519", "Delta"),
                          ("GXA6181", "GlobalX"), ("BBQ8257", "Eastern Express"), ("VXP53", "Avelo")):
            self.assertEqual(airlines.airline_short(cs), short, cs)

    def test_single_brand_regionals_are_their_brand(self):
        for cs, brand in (("ENY3548", "American Eagle"), ("JIA5123", "American Eagle"), ("PDT4400", "American Eagle"),
                          ("EDV5335", "Delta Connection"), ("GJS4513", "United Express"), ("ASH6012", "United Express"),
                          ("UCA4801", "United Express"), ("QXE2201", "Alaska Horizon")):
            self.assertEqual(airlines.airline_short(cs), brand, cs)

    def test_multi_brand_regionals_are_the_operator(self):
        self.assertEqual(airlines.airline_short("SKW5500"), "SkyWest")
        self.assertEqual(airlines.airline_short("SKW476L"), "SkyWest")
        self.assertEqual(airlines.airline_short("RPA3406"), "Republic")

    def test_letters_after_the_number(self):
        self.assertEqual(airlines.airline_short("BAW2PD"), "British Airways")
        self.assertEqual(airlines.airline_short(" ual1678 "), "United")

    def test_not_an_airline_flight(self):
        for cs in (None, "", "UAL", "UALX", "UAL12345", "N123AB", "N959BP", "COL120", "TROY329", "VELUX55",
                   "TYSON51", "RCH123", "CAP2541", "IDT84A"):
            self.assertIsNone(airlines.airline_short(cs), cs)

    def test_operator_lookup_unchanged(self):
        self.assertEqual(airlines.lookup_operator("UAL308"), ("UAL", "United Airlines"))
        self.assertEqual(airlines.lookup_operator("ZZZ1"), ("ZZZ", None))
        self.assertEqual(airlines.lookup_operator("N123AB"), (None, "Private"))
        self.assertEqual(airlines.lookup_operator(""), (None, None))


if __name__ == "__main__":
    unittest.main()
