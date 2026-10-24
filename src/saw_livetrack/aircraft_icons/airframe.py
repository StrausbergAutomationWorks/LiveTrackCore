"""Is it a rotorcraft? ONE answer, and everything that cares asks here.

Moved verbatim from Live Track Aircraft's airframe.py at ac45b2e (saw-livetrack
0.2.11, backlog 298), with the icon table it reads. "The icon table" below is
this package's __init__.py.

! DECIDED BY LEE, 2026-09-25, in this order:
  1. the TYPE CODE, when the icon table knows it: rotorcraft exactly when it
     draws the helicopter (a7). The table is what the airframe LOOKS like.
  2. otherwise the ROTOR TYPE TABLE below, with prefixes that cannot match
     gliders.
  3. otherwise ADS-B emitter category A7.

! WHY ONE FUNCTION. Before this, aircraft.classify() trusted the CATEGORY first
and icons.pick() trusted the TYPE first, so the tone, icon_category and the
silhouette could disagree. Measured 2026-09-25 over all 539 type codes: 18
codes drawn as a helicopter that classify() called an aeroplane (BK117,
H145M, H60M, H65 ...), 7 it called rotorcraft that were drawn otherwise (five
of them GLIDERS: KA6 caught by the Kamov prefix, MINI-NIMBUS by Mil, AS30 by
AS3...), and 57 helicopter types sent with category A1/A2 drawn as helicopters
in an aeroplane's colour. The silhouette (icons.pick), the tone and
icon_category (aircraft.classify) and map_layer all call is_rotorcraft().
"""

from __future__ import annotations

from . import ROTOR_KEYS, type_icon

# ! EXACT DESIGNATORS FIRST, PREFIXES ONLY WHERE THEY CANNOT COLLIDE.
# The first version of this was prefixes alone, and "A19" matched A19N, the
# Airbus A319neo - an airliner drawn as a helicopter. Moved here from
# aircraft.py on 2026-09-25; tests/test_airframe.py checks every prefix
# against every non-helicopter the icon table knows.
_ROTOR_TYPES = frozenset((
    # Bell
    "B06", "B06T", "B204", "B205", "B206", "B210", "B212", "B214", "B222",
    "B230", "B407", "B412", "B427", "B429", "B430", "B505", "B525", "B47G",
    "B47J", "UH1", "UH1H", "UH1Y", "H1",
    # Robinson
    "R22", "R44", "R66",
    # Airbus Helicopters / Eurocopter / Aerospatiale
    "AS50", "AS55", "AS65", "AS32", "AS3B", "ALO2", "ALO3",
    "EC20", "EC25", "EC30", "EC35", "EC45", "EC55", "EC75",
    "H125", "H130", "H135", "H145", "H160", "H175", "H215", "H225",
    "GAZL", "PUMA", "SUCO",
    # Leonardo / AgustaWestland
    "A109", "A119", "A139", "A169", "A189", "AW09", "AW139", "EH10", "A129",
    # Sikorsky
    "S61", "S61R", "S64", "S70", "S76", "S92", "H60", "UH60", "MH60", "S300",
    # Boeing / vertical lift
    "CH47", "H47", "V22", "V280", "CH46", "AH64", "H64",
    # MD / Hughes / Schweizer / Enstrom
    "MD50", "MD52", "MD53", "MD60", "MD62", "MD87H", "H500", "H269", "H369",
    "EN28", "EN48", "S269", "S300C",
    # Russian
    "MI8", "MI17", "MI24", "MI26", "MI2", "KA32", "KA26", "KA52",
    # Other
    "NH90", "LYNX", "EH101", "AW101", "W3", "BK17", "BO05", "SK76",
    # Autogyro: a rotorcraft, and ADS-B category A7 covers it too. Added
    # 2026-09-25 when the name table was read for rotorcraft.
    "GYRO",
))

# ! NARROWED 2026-09-25. "MI" caught MINI-NIMBUS, "KA" caught the Ka 6
# glider, "AS3" caught the AS 30 glider and "B42" caught the B4208
# ultralight. Every prefix now carries a digit that only a helicopter family
# has there. Bell's B21/B22/B23/B40-B43/B47 prefixes are gone: every Bell in
# service is listed exactly above.
_ROTOR_PREFIXES = (
    "EC1", "EC2", "EC3", "EC4", "EC5", "EC6", "EC7",   # Eurocopter EC1xx-EC7xx
    "AS33", "AS35", "AS36",                            # AS332 / AS350-355 / AS365
    "MI1", "MI2", "MI3", "MI8",                        # Mil Mi-2/8/17/24/26/28/34/35
    "KA2", "KA3", "KA5",                               # Kamov Ka-26/27/32/50/52
    "MD5", "MD6",                                      # MD 500/600 - NOT MD8/MD9
)


def _norm(value: str | None) -> str:
    return (value or "").strip().upper()


def is_rotorcraft(emitter_category: str | None, type_code: str | None) -> bool:
    """The one answer. See the module docstring for the order."""
    t = _norm(type_code)
    if t:
        key = type_icon(t)
        if key is not None:
            return key in ROTOR_KEYS
        if t in _ROTOR_TYPES or t.startswith(_ROTOR_PREFIXES):
            return True
    return _norm(emitter_category) == "A7"


def map_layer(emitter_category: str | None, type_code: str | None) -> str:
    """06_MAP_CONTRACT D10: rotorcraft from ANY source is air_low."""
    return "air_low" if is_rotorcraft(emitter_category, type_code) else "air"
