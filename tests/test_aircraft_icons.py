"""Tests for saw_livetrack.aircraft_icons: every picture and every picture decision (backlog 298).

Run: python tests/test_aircraft_icons.py

The module was moved verbatim from Live Track Aircraft's icons.py and airframe.py at
ac45b2e (2026-10-24), with no picture allowed to change. tests/aircraft_icons_golden.json
holds digests made by digests() below from THAT code: one per icon over every tone,
lights state, badge, angle and a spread of weights, and one per decision table. Any
change to what a picture shows changes a digest. When a change is MEANT, bump ART_REV
in the same commit and regenerate the fixture (bin\\l298_golden.py).
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from saw_livetrack import aircraft_icons as ai  # noqa: E402
from saw_livetrack.aircraft_icons import airframe  # noqa: E402

CHECKS = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


ANGLES = (None, 45)
# 0.2.12 (backlog 299): the two explicit modes join False and True.
LIGHTS = (False, True, "glow", "wigwag")
WEIGHTS = (0.05, 0.62, 1.5)
CATS = (None, "", "A0", "A1", "A2", "A3", "A5", "A7", "B0", "B1", "B2", "B3", "B4", "B6", "C0", "ZZ")
OPCATS = (None, "Government", "Military", "Coast Guard", "Law enforcement",
          "Federal law enforcement", "Fire / EMS")


def digests(icons, airframe, keys):
    """{name: sha256}. The SAME function made the fixture from the ac45b2e code."""
    out = {}
    tones = sorted(icons._TONES) + ["nosuchtone"]
    for key in keys:
        h = hashlib.sha256()
        path = icons.icon_path(key)
        for tone in tones:
            for lights in LIGHTS:
                for w in WEIGHTS + (icons.weight_for("A5"),):
                    for badge in (True, False):
                        for deg in ANGLES:
                            h.update(icons.render(path, deg, 64, tone, w, badge, lights).encode("utf-8"))
                            h.update(b"\0")
        out["render/" + key] = h.hexdigest()
    codes = sorted(set(icons._BY_TYPE) | set(airframe._ROTOR_TYPES) | {"ZZZZ", "EC130", "KA6", "A19N", " b350 "})
    table = [(c, k, icons.pick(k, c), airframe.is_rotorcraft(k, c), airframe.map_layer(k, c))
             for c in codes for k in CATS]
    table += [(k, ic, icons.pick(k, None, ic)) for k in CATS for ic in (None, "balloon", "uav", "rotorcraft")]
    table += [(n, icons.pick("A1", None, None, n)) for n in (
        "Cessna Citation Mustang", "Gulfstream G650", "Dassault Falcon 7X", "Embraer E175",
        "De Havilland Dash 8", "Cirrus SR22", "Cessna 421", "Boeing 737")]
    out["decide/pick"] = hashlib.sha256(json.dumps(table).encode()).hexdigest()
    looks = [(g, ic, oc, m, icons.tone_for(g, ic, oc, m), icons.lights_for(oc))
             for g in (False, True) for ic in (None, "rotorcraft") for oc in OPCATS for m in (False, True)]
    looks += [(k, icons.weight_for(k)) for k in CATS]
    looks += [(k, icons.is_rotatable(k)) for k in keys]
    looks += [(b / 2.0, icons.quantise(b / 2.0)) for b in range(-20, 741)]
    out["decide/look"] = hashlib.sha256(json.dumps(looks).encode()).hexdigest()
    lit = [(t, repr(v), icons.lights_mode(t, v)) for t in tones
           for v in (False, None, True, "glow", "wigwag", "1", "x", 1, 0)]
    lit += [(repr(q), repr(icons.lights_from_query(q))) for q in (None, "", "0", "1", "g", "w", "G", "glow", "x")]
    out["decide/lights"] = hashlib.sha256(json.dumps(lit).encode()).hexdigest()
    return out


def library_keys():
    return sorted(f[:-4] for f in os.listdir(ai.icon_dir()) if f.endswith(".svg"))


if __name__ == "__main__":
    golden = json.load(open(os.path.join(ROOT, "tests", "aircraft_icons_golden.json"), encoding="utf-8"))
    keys = library_keys()
    check("the icon folder holds the fixture's icons, no more, no fewer", keys == golden["keys"])
    got = digests(ai, airframe, keys)
    for name, want in sorted(golden["digests"].items()):
        check("unchanged: " + name, got.get(name) == want)
    check("ART_REV is the fixture's", ai.ART_REV == golden["ART_REV"])

    # every key the type table can return has a file; nothing in the table points nowhere
    drawn = set(ai._BY_TYPE.values()) | set(ai._BY_CATEGORY.values()) | ai.ROTOR_KEYS | {"a0", "b2", "b0"}
    check("every key the table returns has its file", all(ai.icon_path(k) for k in drawn))
    check("a path segment cannot leave the folder", ai.icon_path("../x") is None and ai.icon_path("A320") is None)
    check("no presidential picture here (they stay in Live Track Aircraft)",
          ai.icon_path("vc25a") is None and ai.icon_path("af83f3") is None and not hasattr(ai, "HEX_KEYS"))

    # the query string: as Live Track Aircraft's picture_url wrote it at ac45b2e
    check("query: default tone omitted, b=0 and a=ART_REV last",
          ai.picture_query("default", 0.85) == "w=0.85&b=0&a=%d" % ai.ART_REV)
    check("query: tone, weight, lights", ai.picture_query("law", 0.66, True) == "t=law&w=0.66&l=g&b=0&a=%d" % ai.ART_REV)
    check("query: True on the police white is the wig-wag", ai.picture_query("police", 0.78, True) == "t=police&w=0.78&l=w&b=0&a=%d" % ai.ART_REV)
    check("query: glow on the police white", ai.picture_query("police", 0.78, "glow") == "t=police&w=0.78&l=g&b=0&a=%d" % ai.ART_REV)
    check("query: a mangled lights value is unlit", "l=" not in ai.picture_query("law", 0.66, "x"))

    # the lights modes (backlog 299). The approved look: white with the red/blue glow, as drawn in
    # 20_Research/le_flash_299_20261024 by lights=True over a probe tone equal to the police white.
    a7 = ai.icon_path("a7")
    ai._TONES["probe_white"] = ai._TONES["police"]
    try:
        approved = ai.render(a7, None, 64, "probe_white", 0.78, False, True)
    finally:
        del ai._TONES["probe_white"]
    check("glow on the police white is the picture Lee looked at", ai.render(a7, None, 64, "police", 0.78, False, "glow") == approved)
    check("True on police is the wig-wag", ai.render(a7, None, 64, "police", 0.78, False, True) == ai.render(a7, None, 64, "police", 0.78, False, "wigwag"))
    check("True on law is the glow", ai.render(a7, None, 64, "law", 0.78, False, True) == ai.render(a7, None, 64, "law", 0.78, False, "glow"))
    check("a mangled lights value renders unlit", ai.render(a7, None, 64, "law", 0.78, False, "x") == ai.render(a7, None, 64, "law", 0.78, False, False))
    check("the view's l=: 1 True, g, w, anything else off",
          (ai.lights_from_query("1"), ai.lights_from_query("g"), ai.lights_from_query("w"),
           ai.lights_from_query("x"), ai.lights_from_query(None)) == (True, "glow", "wigwag", False, False))
    check("query: no weight", ai.picture_query("mil") == "t=mil&b=0&a=%d" % ai.ART_REV)
    check("query: a consumer's own revision", ai.picture_query("mil", rev=13).endswith("&a=13"))

    # 0.2.16 (backlog 313): the ICE Air charter glow, look C4, steady. Rebuilt here exactly as
    # bin\ltsalt_charter_look.py drew C4 on the sheet Lee chose from (halos() under the art).
    import re as _re

    def c4(svg):
        w, h = [float(v) for v in _re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg).groups()]
        px = max(w, h) / 64.0
        defs, uses = "", ""
        for fid, colour, grow, blur, op in (("cr", "#FF2020", 3.0, 2.2, 0.85), ("cb", "#2060FF", 1.4, 1.0, 0.9)):
            defs += ('<filter id="%s" filterUnits="userSpaceOnUse" x="0" y="0" width="%s" height="%s">'
                     '<feMorphology in="SourceAlpha" operator="dilate" radius="%.4f"/><feGaussianBlur stdDeviation="%.4f" result="g"/>'
                     '<feFlood flood-color="%s" flood-opacity="%.2f"/><feComposite in2="g" operator="in"/></filter>'
                     % (fid, ai._fmt(w), ai._fmt(h), grow * px, blur * px, colour, op))
            uses += '<g opacity="1"><use href="#art" filter="url(#%s)"/></g>' % fid
        i = svg.index('<g filter="url(#e)">')
        return svg[:i] + "<defs>" + defs + "</defs>" + uses + svg[i:]

    for key, tone, w in (("a320", "default", 0.85), ("b737", "default", 0.85), ("kingair350", "default", 0.72),
                         ("a7", "police", 0.78), ("a7", "gov", 0.78)):
        p = ai.icon_path(key)
        check("charter on %s/%s is look C4 as Lee saw it" % (key, tone),
              ai.render(p, None, 64, tone, w, False, "charter") == c4(ai.render(p, None, 64, tone, w, False, False)))
    a320 = ai.icon_path("a320")
    cp = ai.render(a320, None, 64, "default", 0.85, False, "charter")
    check("the charter glow is steady: no opacity animation", 'attributeName="opacity"' not in cp)
    check("the charter glow is not the sourced glow", cp != ai.render(a320, None, 64, "default", 0.85, False, "glow"))
    check("charter on a turned picture or a badge draws nothing extra",
          ai.render(a320, 45, 64, "default", 0.85, False, "charter") == ai.render(a320, 45, 64, "default", 0.85, False, False)
          and ai.render(a320, None, 64, "default", 0.85, True, "charter") == ai.render(a320, None, 64, "default", 0.85, True, False))
    check("query: charter is l=c", ai.picture_query("default", 0.85, "charter") == "w=0.85&l=c&b=0&a=%d" % ai.ART_REV)
    check("query: charter on the police white is still l=c", ai.picture_query("police", 0.78, "charter") == "t=police&w=0.78&l=c&b=0&a=%d" % ai.ART_REV)
    check("the view's l=c is charter", ai.lights_from_query("c") == "charter" == ai.LIGHTS_CHARTER)
    check("True never means charter", all(ai.lights_mode(t, True) != "charter" for t in ai._TONES))

    # the licence travels with the icons
    att = open(os.path.join(os.path.dirname(ai.__file__), "ATTRIBUTION.md"), encoding="utf-8").read()
    check("ATTRIBUTION.md ships beside the icons", "ADS-B Radar" in att)
    check("the backlink is in it", "https://adsb-radar.com" in att)
    check("the note that MIT covers the code is in it", "MIT licence" in att and "code only" in att)
    check("the module names the backlink too", "https://adsb-radar.com" in (ai.__doc__ or ""))

    # rotorcraft is ONE answer (airframe), and pick() follows it
    check("R22 is a helicopter", ai.pick(None, "R22") == "a7" and airframe.is_rotorcraft("A1", "R22"))
    check("A19N is an airliner, not a helicopter", not airframe.is_rotorcraft("A7", "A19N"))
    check("rotorcraft is air_low", airframe.map_layer("A7", None) == "air_low")

    bad = [n for n, ok in CHECKS if not ok]
    for n in bad:
        print("FAIL", n)
    print("%d of %d checks passed" % (len(CHECKS) - len(bad), len(CHECKS)))
    sys.exit(1 if bad else 0)
