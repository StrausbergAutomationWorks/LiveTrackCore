"""Airline lookup by callsign: the operator's name and, for the hover, its short name.

Moved here from Live Track Aircraft's airlines.py in 0.2.17 (Lee 2026-10-29 14:51) so Live Track SALT reads the same
table: one table, two consumers (rule 2). The ADS-B feed gives a callsign but never the airline behind it. For airline
traffic the first three characters are the operator's ICAO designator (``UAL308`` -> United Airlines), so a prefix
lookup turns a callsign into something a human recognises; ``airline_short`` gives the hover's first line its name
("United 1678", Lee 2026-10-29). General-aviation aircraft usually fly under their own registration rather than an
airline callsign, which is why an N-number is reported as private traffic instead of being matched against the table.
No dependencies.
"""

from __future__ import annotations

import re

# ICAO three-letter operator designators -> operator name.
# Not exhaustive: covers the carriers most likely to appear over Europe and
# North America, plus the major cargo operators.
_OPERATORS: dict[str, str] = {
    # --- North America, mainline ---
    "AAL": "American Airlines",
    "UAL": "United Airlines",
    "DAL": "Delta Air Lines",
    "SWA": "Southwest Airlines",
    "JBU": "JetBlue Airways",
    "ASA": "Alaska Airlines",
    "NKS": "Spirit Airlines",
    "FFT": "Frontier Airlines",
    "HAL": "Hawaiian Airlines",
    "AAY": "Allegiant Air",
    "SCX": "Sun Country Airlines",
    "MXY": "Breeze Airways",
    # --- North America, charter (Lee 2026-10-29 14:51): the ICE Air contractors' own designators. Their ICE flights
    # fly under special callsigns (TYSON, VELUX) that are not designators, so these name only their other work. ---
    "GXA": "GlobalX",
    "BBQ": "Eastern Air Express",
    "VXP": "Avelo Airlines",
    "LYM": "Key Lime Air",
    "SWQ": "iAero Airways",
    # --- North America, regional ---
    "RPA": "Republic Airways as American Eagle, Delta Connection or United Express",
    "SKW": "SkyWest Airlines as American Eagle, Delta Connection, United Express or Alaska SkyWest",
    "ENY": "American Eagle by Envoy Air",
    "EDV": "Delta Connection by Endeavor Air",
    # Mesa merged into Republic Airways Holdings 2025-11-25 but retains its
    # own AOC and callsign. United Express is a 10-year contract, not
    # ownership — recheck periodically.
    "ASH": "United Express by Mesa Airlines",
    "JIA": "American Eagle by PSA Airlines",
    "PDT": "American Eagle by Piedmont Airlines",
    "GJS": "United Express by GoJet Airlines",
    "QXE": "Alaska Horizon by Horizon Air",
    # --- Canada / Mexico / Latin America ---
    "ACA": "Air Canada",
    "ROU": "Air Canada Rouge",
    "JZA": "Jazz Aviation",
    "WJA": "WestJet",
    "POE": "Porter Airlines",
    "AMX": "Aeromexico",
    "VOI": "Volaris",
    "CMP": "Copa Airlines",
    "AVA": "Avianca",
    "ARG": "Aerolineas Argentinas",
    "TAM": "LATAM Brasil",
    "LAN": "LATAM Airlines",
    # --- Europe ---
    "BAW": "British Airways",
    "VIR": "Virgin Atlantic",
    "DLH": "Lufthansa",
    "AFR": "Air France",
    "KLM": "KLM",
    "IBE": "Iberia",
    "SWR": "Swiss",
    "AUA": "Austrian Airlines",
    "SAS": "Scandinavian Airlines",
    "FIN": "Finnair",
    "ICE": "Icelandair",
    "NAX": "Norwegian",
    "EIN": "Aer Lingus",
    "BEL": "Brussels Airlines",
    "TAP": "TAP Air Portugal",
    "ITY": "ITA Airways",
    "LOT": "LOT Polish Airlines",
    "AEE": "Aegean Airlines",
    "RYR": "Ryanair",
    "EZY": "easyJet",
    "WZZ": "Wizz Air",
    "VLG": "Vueling",
    "EWG": "Eurowings",
    "TVF": "Transavia France",
    "CFG": "Condor",
    # --- Middle East / Africa / South Asia ---
    "THY": "Turkish Airlines",
    "ELY": "El Al",
    "RJA": "Royal Jordanian",
    "MSR": "EgyptAir",
    "ETH": "Ethiopian Airlines",
    "KQA": "Kenya Airways",
    "SAA": "South African Airways",
    "QTR": "Qatar Airways",
    "UAE": "Emirates",
    "ETD": "Etihad Airways",
    "AIC": "Air India",
    "IGO": "IndiGo",
    "PIA": "Pakistan International",
    # --- Asia / Pacific ---
    "JAL": "Japan Airlines",
    "ANA": "All Nippon Airways",
    "KAL": "Korean Air",
    "AAR": "Asiana Airlines",
    "CPA": "Cathay Pacific",
    "CCA": "Air China",
    "CES": "China Eastern",
    "CSN": "China Southern",
    "EVA": "EVA Air",
    "CAL": "China Airlines",
    "SIA": "Singapore Airlines",
    "THA": "Thai Airways",
    "MAS": "Malaysia Airlines",
    "GIA": "Garuda Indonesia",
    "PAL": "Philippine Airlines",
    "QFA": "Qantas",
    "ANZ": "Air New Zealand",
    # --- Cargo ---
    "FDX": "FedEx Express",
    "UPS": "U.P.S. Airlines",
    "ABX": "ABX Air - Cargo",
    "ATN": "Air Transport International - Cargo",
    "GTI": "Atlas Air - Cargo",
    "CKS": "Kalitta Air - Cargo",
    "KFS": "Kalitta Charters - Cargo",
    "PAC": "Polar Air Cargo",
    "CLX": "Cargolux",
    "GEC": "Lufthansa Cargo",
    "NCA": "Nippon Cargo Airlines",
    "CKK": "China Cargo Airlines",
    "CAO": "Air China Cargo",
    "BOX": "AeroLogic - Cargo",
    "MPH": "Martinair Cargo",
    "SQC": "Singapore Airlines Cargo",
    "GSS": "Atlas Air (Global Supply)",
    # --- Business / fractional / state ---
    "EJA": "NetJets",
    "LXJ": "Flexjet",
    "RCH": "USAF Air Mobility Command",
    "ABY": "Air Arabia",
    "AIQ": "Thai AirAsia",
    "AMF": "Ameriflight",
    "AWI": "Air Wisconsin",
    "AXB": "Air India Express",
    "AXM": "AirAsia",
    "AZU": "Azul",
    "BCS": "European Air Transport (DHL)",
    "CAP": "Civil Air Patrol",
    "CBJ": "Beijing Capital Airlines",
    "CDG": "Shandong Airlines",
    "CFG": "Condor",
    "CHH": "Hainan Airlines",
    "CJT": "Cargojet",
    "CQH": "Spring Airlines",
    "CSC": "Sichuan Airlines",
    "CSZ": "Shenzhen Airlines",
    "CXA": "Xiamen Airlines",
    "CYO": "ATI Jet",
    "DAT": "DAT (Danish Air Transport)",
    "EJM": "Executive Jet Management",
    "EJU": "easyJet Europe",
    "EXS": "Jet2",
    "FDB": "flydubai",
    "FLE": "Flair Airlines",
    "GLO": "GOL",
    "IGO": "IndiGo",
    "JAF": "TUI fly Belgium",
    "JJP": "Jetstar Japan",
    "JST": "Jetstar",
    "JSX": "JSX",
    "JUS": "USA Jet Airlines",
    "MRA": "Martinaire",
    "MSC": "Air Cairo",
    "NSZ": "Norwegian Air Sweden",
    "PGT": "Pegasus Airlines",
    "QLK": "QantasLink",
    "RAX": "Royal Air Freight",
    "SAS": "SAS",
    "SEJ": "SpiceJet",
    "SKY": "Skymark Airlines",
    "SVA": "Saudia",
    "SXS": "SunExpress",
    "TOM": "TUI Airways",
    "TRA": "Transavia",
    "TSC": "Air Transat",
    "UCA": "CommutAir",
    "VIV": "Viva Aerobus",
    "VOZ": "Virgin Australia",
    "VTE": "Contour Aviation",
    "VTI": "Vistara",
    "WUK": "Wizz Air UK",
}

# ! SHORT NAMES FOR THE HOVER'S FIRST LINE (Lee 2026-10-29 14:14, 14:24): "United 1678", not "UAL1678".
# A single-brand regional reads as its BRAND (ENY Envoy -> American Eagle); the two multi-brand regionals as the
# OPERATOR (SkyWest, Republic), because a callsign cannot tell which brand a given flight is. Explicit, one per
# designator in _OPERATORS, except the non-commercial RCH (USAF Air Mobility Command) and CAP (Civil Air Patrol),
# which keep their callsign. tests/test_airlines.py holds the two tables in step.
_SHORT: dict[str, str] = {
    "AAL": "American", "UAL": "United", "DAL": "Delta", "SWA": "Southwest", "JBU": "JetBlue", "ASA": "Alaska",
    "NKS": "Spirit", "FFT": "Frontier", "HAL": "Hawaiian", "AAY": "Allegiant", "SCX": "Sun Country", "MXY": "Breeze",
    "GXA": "GlobalX", "BBQ": "Eastern Express", "VXP": "Avelo", "LYM": "Key Lime", "SWQ": "iAero",
    "RPA": "Republic", "SKW": "SkyWest", "ENY": "American Eagle", "EDV": "Delta Connection",
    "ASH": "United Express", "JIA": "American Eagle", "PDT": "American Eagle", "GJS": "United Express",
    "QXE": "Alaska Horizon", "UCA": "United Express", "JZA": "Air Canada Express", "AWI": "Air Wisconsin",
    "ACA": "Air Canada", "ROU": "Air Canada Rouge", "WJA": "WestJet", "POE": "Porter", "AMX": "Aeromexico",
    "VOI": "Volaris", "CMP": "Copa", "AVA": "Avianca", "ARG": "Aerolineas Argentinas", "TAM": "LATAM Brasil",
    "LAN": "LATAM", "VIV": "Viva Aerobus", "AZU": "Azul", "GLO": "GOL", "FLE": "Flair", "TSC": "Air Transat",
    "BAW": "British Airways", "VIR": "Virgin Atlantic", "DLH": "Lufthansa", "AFR": "Air France", "KLM": "KLM",
    "IBE": "Iberia", "SWR": "Swiss", "AUA": "Austrian", "SAS": "SAS", "FIN": "Finnair", "ICE": "Icelandair",
    "NAX": "Norwegian", "NSZ": "Norwegian", "EIN": "Aer Lingus", "BEL": "Brussels Airlines", "TAP": "TAP",
    "ITY": "ITA", "LOT": "LOT", "AEE": "Aegean", "RYR": "Ryanair", "EZY": "easyJet", "EJU": "easyJet",
    "WZZ": "Wizz Air", "WUK": "Wizz Air", "VLG": "Vueling", "EWG": "Eurowings", "TVF": "Transavia",
    "TRA": "Transavia", "CFG": "Condor", "EXS": "Jet2", "TOM": "TUI", "JAF": "TUI fly", "SXS": "SunExpress",
    "PGT": "Pegasus", "DAT": "DAT",
    "THY": "Turkish", "ELY": "El Al", "RJA": "Royal Jordanian", "MSR": "EgyptAir", "MSC": "Air Cairo",
    "ETH": "Ethiopian", "KQA": "Kenya Airways", "SAA": "South African", "QTR": "Qatar", "UAE": "Emirates",
    "ETD": "Etihad", "FDB": "flydubai", "ABY": "Air Arabia", "SVA": "Saudia", "AIC": "Air India",
    "AXB": "Air India Express", "VTI": "Vistara", "IGO": "IndiGo", "SEJ": "SpiceJet", "PIA": "PIA",
    "JAL": "JAL", "ANA": "ANA", "SKY": "Skymark", "JJP": "Jetstar Japan", "KAL": "Korean Air", "AAR": "Asiana",
    "CPA": "Cathay Pacific", "CCA": "Air China", "CES": "China Eastern", "CSN": "China Southern",
    "CHH": "Hainan", "CXA": "Xiamen", "CSZ": "Shenzhen", "CSC": "Sichuan", "CDG": "Shandong",
    "CQH": "Spring", "CBJ": "Beijing Capital", "EVA": "EVA Air", "CAL": "China Airlines",
    "SIA": "Singapore", "THA": "Thai", "AIQ": "Thai AirAsia", "AXM": "AirAsia", "MAS": "Malaysia",
    "GIA": "Garuda", "PAL": "Philippine", "QFA": "Qantas", "QLK": "QantasLink", "JST": "Jetstar",
    "VOZ": "Virgin Australia", "ANZ": "Air New Zealand",
    "FDX": "FedEx", "UPS": "UPS", "ABX": "ABX", "ATN": "ATI", "GTI": "Atlas", "GSS": "Atlas",
    "CKS": "Kalitta", "KFS": "Kalitta Charters", "PAC": "Polar", "CLX": "Cargolux", "GEC": "Lufthansa Cargo",
    "NCA": "Nippon Cargo", "CKK": "China Cargo", "CAO": "Air China Cargo", "BOX": "AeroLogic",
    "MPH": "Martinair", "SQC": "Singapore Cargo", "BCS": "DHL", "CJT": "Cargojet", "AMF": "Ameriflight",
    "MRA": "Martinaire", "RAX": "Royal Air Freight", "JUS": "USA Jet", "CYO": "ATI Jet",
    "EJA": "NetJets", "LXJ": "Flexjet", "EJM": "Executive Jet", "JSX": "JSX", "VTE": "Contour",
}

# A designator then a flight number that starts with a digit: UAL1678, BAW2PD, SKW476L. At most 7 characters in
# all, the FAA's limit for a designator and flight number (AC 120-26).
_FLIGHT = re.compile(r"^[A-Z]{3}[0-9][0-9A-Z]{0,3}$")


def airline_short(callsign: str | None) -> str | None:
    """The airline's short name for the hover's first line, or None.

    None unless the callsign is a known airline's designator followed by a
    flight number: an unknown designator, a registration (N123AB) or a special
    callsign (TROY329, VELUX55) keeps its callsign in the hover.
    """
    if not callsign:
        return None
    text = str(callsign).strip().upper()
    if not _FLIGHT.match(text):
        return None
    return _SHORT.get(text[:3])


# An aircraft flying under a US registration (N followed by a digit) is
# general aviation rather than a scheduled operator.
_US_REGISTRATION = re.compile(r"^N[0-9]")

# Callsign placeholders used when the feed reports no callsign at all.
_PLACEHOLDERS = frozenset({"", "?", "UNKNOWN", "NONE", "NULL"})


def lookup_operator(callsign: str | None) -> tuple[str | None, str | None]:
    """Return ``(operator_code, operator_name)`` for a callsign.

    Both values are ``None`` when nothing sensible can be derived, so callers
    can pass the result straight through to entity attributes.

    ``("UAL", "United Airlines")``  a known airline
    ``("ZZZ", None)``               looks like a designator, not in the table
    ``(None, "Private")``  flying under a US registration
    ``(None, None)``                no usable callsign
    """
    if not callsign:
        return None, None

    text = str(callsign).strip().upper()
    if text in _PLACEHOLDERS:
        return None, None

    if _US_REGISTRATION.match(text):
        return None, "Private"

    prefix = text[:3]
    if len(prefix) < 3 or not prefix.isalpha():
        return None, None

    return prefix, _OPERATORS.get(prefix)
