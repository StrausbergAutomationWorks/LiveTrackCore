"""Tests for saw_livetrack.map_card: the version rule and the one-row table (backlog 291).

Run: python tests/test_map_card.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from saw_livetrack import map_card as mc  # noqa: E402

CHECKS = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


# --- versions ---------------------------------------------------------------------------------
check("header read", mc.read_version(b"/*! live-track-map-card 0.1.0+6c6c569 */\n(x)") == "0.1.0+6c6c569")
check("no header -> None", mc.read_version(b"console.log(1)") is None)
check("header must be line 1", mc.read_version(b"\n/*! live-track-map-card 0.1.0 */") is None)

ORDER = ["0.1.0-dev", "0.1.0", "0.1.1-dev", "0.1.1", "0.2.0", "1.0.0-alpha", "1.0.0-alpha.1",
         "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
check("SemVer 2.0.0 precedence, including the spec's own example",
      all(mc.compare(a, b) == -1 for a, b in zip(ORDER, ORDER[1:])))
check("build metadata ignored", mc.compare("0.1.0+aaaaaaa", "0.1.0+bbbbbbb") == 0)
check(".dirty ignored for order", mc.compare("0.1.1-dev+a.dirty", "0.1.1-dev+b") == 0)
check("dev build sits above the last release", mc.compare("0.1.1-dev+a", "0.1.0+b") == 1)
check("dev build sits below the next release", mc.compare("0.1.1-dev+a", "0.1.1+b") == -1)
check("unparseable is unknown", mc.compare("1", "0.1.0") is None and mc.precedence("v0.1.0") is None)
check("leading zero refused", mc.precedence("0.01.0") is None)
check("is_release", mc.is_release("0.1.0+6c6c569") and mc.is_release("0.1.0")
      and not mc.is_release("0.1.1-dev+a") and not mc.is_release("0.1.0+a.dirty")
      and not mc.is_release("junk"))

# --- URLs -------------------------------------------------------------------------------------
u = mc.resource_url("live_track_ships", "1a2b3c4d5e6f", "0.1.0+82a9634")
check("URL shape", u == "/live_track_ships/map-card/1a2b3c4d5e6f/live-track-map-card.js?v=0.1.0%2B82a9634&h=1a2b3c4d5e6f")
check("+ is percent-encoded and decodes back (D9c)", mc.url_version(u) == "0.1.0+82a9634")
BOX = "/local/live-track-map-card/live-track-map-card.js?v=0.1.0%2B6c6c569&h=82193686bfa2"
check("box row is a card row", mc.is_card_row(BOX))
check("box row maps to www/", mc.row_file(BOX) == mc.RowFile(mc.LOCAL, "www/live-track-map-card/live-track-map-card.js"))
check("ha-map-card is NOT a card row", not mc.is_card_row("/hacsfiles/ha-map-card/map-card.js?hacstag=1789278795"))
check(".gz sibling is not a card row", not mc.is_card_row("/local/x/live-track-map-card.js.gz"))
check("name match, not prefix", mc.is_card_row("/somewhere/else/live-track-map-card.js"))
rf = mc.row_file(u)
check("SAW row maps to the integration's frontend/", rf == mc.RowFile(
    mc.SAW, "custom_components/live_track_ships/frontend/live-track-map-card.js", "live_track_ships", "1a2b3c4d5e6f"))
check("HACS row maps to www/community/", mc.row_file("/hacsfiles/LiveTrackMapCard/live-track-map-card.js?hacstag=9").kind == mc.HACS)
check("traversal does not escape", mc.row_file("/local/../custom_components/x/live-track-map-card.js").kind == mc.OTHER)

# --- inspect ----------------------------------------------------------------------------------
B = lambda v: ("/*! live-track-map-card %s */\n" % v).encode()  # noqa: E731
check("SAW alive when sha matches and loaded", mc.inspect(u, B("0.1.0+x"), "1a2b3c4d5e6f", True) == mc.RowInfo(True, "0.1.0+x"))
check("SAW dead when the file changed", not mc.inspect(u, B("0.1.1+y"), "ffffffffffff", True).alive)
check("SAW dead when its integration is not loaded", not mc.inspect(u, B("0.1.0+x"), "1a2b3c4d5e6f", False).alive)
check("missing file is dead", mc.inspect(BOX, None) == mc.RowInfo(False, "0.1.0+6c6c569"))
check("local version from the FILE over the URL", mc.inspect(BOX, B("0.1.1-dev+z")).version == "0.1.1-dev+z")


# --- the table --------------------------------------------------------------------------------
def offer(domain, version, sha="1a2b3c4d5e6f"):
    return mc.Offer(domain, version, mc.resource_url(domain, sha, version))


IC = offer("live_track_intercity_rail", "0.1.0+t000001", "111111111111")
SH = offer("live_track_ships", "0.1.1+t000002", "222222222222")
OTHERS = [{"id": "a", "url": "/local/community/lovelace-auto-entities/auto-entities.js"},
          {"id": "b", "url": "/hacsfiles/ha-map-card/map-card.js?hacstag=1789278795"},
          {"id": "c", "url": "/flights_above/flights-above-card.js?v=1.0.0"}]

p = mc.plan(OTHERS, [IC], {})
check("no card row: create the winner", p.create == IC.url and not p.update and not p.delete)

box = OTHERS + [{"id": "r", "url": BOX}]
p = mc.plan(box, [IC], {BOX: mc.RowInfo(True, "0.1.0+6c6c569")})
check("THE BOX TODAY: release 0.1.0 ties with the hand build and leaves it", not (p.create or p.update or p.delete))
p = mc.plan(box, [IC, SH], {BOX: mc.RowInfo(True, "0.1.0+6c6c569")})
check("a higher release replaces the hand row IN PLACE", p.update == [("r", SH.url)] and not p.create and not p.delete)
p = mc.plan(box, [SH], {BOX: mc.RowInfo(True, "0.1.2-dev+abc")})
check("a dev build above the release stays", not (p.create or p.update or p.delete))
p = mc.plan(box, [IC], {BOX: mc.RowInfo(False, "0.1.0+6c6c569")})
check("hand row with its file gone is dead: replaced", p.update == [("r", IC.url)])
p = mc.plan(box, [IC], {BOX: mc.RowInfo(True, "1")})
check("unknown version (README ?v=1, no header): left alone", not (p.create or p.update or p.delete))

ic_row = [{"id": "s", "url": IC.url}]
p = mc.plan(ic_row, [IC, SH], {IC.url: mc.RowInfo(True, IC.version)})
check("SAW row lower than another offer: updated in place", p.update == [("s", SH.url)])
p = mc.plan([{"id": "s", "url": SH.url}], [IC, SH], {SH.url: mc.RowInfo(True, SH.version)})
check("row already the winner: nothing", not (p.create or p.update or p.delete))
p = mc.plan([{"id": "s", "url": SH.url}], [IC], {SH.url: mc.RowInfo(True, SH.version)})
check("newer row from an integration outside this registry: left", not (p.create or p.update or p.delete))
p = mc.plan(ic_row, [IC], {IC.url: mc.RowInfo(False, IC.version)})
check("dead SAW row pointing at the winner's own old path... is the winner: nothing", not p.update)
old = offer("live_track_intercity_rail", "0.1.0+t000001", "000000000000")
p = mc.plan([{"id": "s", "url": old.url}], [IC], {old.url: mc.RowInfo(False, "0.1.0+t000001")})
check("SAW row on an old content path: dead, moved to the current path", p.update == [("s", IC.url)])

ties = [offer("live_track_ships", "0.1.0+t000001", "333333333333"), IC]
p = mc.plan([{"id": "s", "url": ties[0].url}], ties, {ties[0].url: mc.RowInfo(True, "0.1.0+t000001")})
check("tie: keep the offer the row points at", p.winner.domain == "live_track_ships" and not p.update)

HACS = "/hacsfiles/LiveTrackMapCard/live-track-map-card.js?hacstag=42"
p = mc.plan([{"id": "h", "url": HACS}, {"id": "s", "url": IC.url}, {"id": "l", "url": BOX}], [SH],
            {HACS: mc.RowInfo(True, "0.1.0+x"), IC.url: mc.RowInfo(True, IC.version), BOX: mc.RowInfo(True, "0.1.0")})
check("HACS: never touched, never duplicated", "h" not in p.delete and not p.create and not p.update)
check("HACS: our duplicate row deleted, the user's reported", p.delete == ["s"] and p.user_duplicates == [BOX])
check("HACS: notes a newer release", p.hacs_older and p.hacs_url == HACS)

p = mc.plan([{"id": "s", "url": IC.url}, {"id": "l", "url": BOX}], [SH],
            {IC.url: mc.RowInfo(True, IC.version), BOX: mc.RowInfo(True, "0.1.2-dev+q")})
check("two rows: keep the highest (the dev row), delete ours, nothing else", p.delete == ["s"]
      and not p.update and not p.user_duplicates)
p = mc.plan([{"id": "l", "url": BOX}, {"id": "s", "url": IC.url}], [SH],
            {IC.url: mc.RowInfo(True, IC.version), BOX: mc.RowInfo(True, "0.0.9")})
check("two rows, ours highest: user row reported, ours updated", p.user_duplicates == [BOX]
      and p.update == [("s", SH.url)] and not p.delete)

# --- uninstall --------------------------------------------------------------------------------
p = mc.plan([{"id": "s", "url": IC.url}], [SH], {IC.url: mc.RowInfo(False, IC.version)},
            withdrawn="live_track_intercity_rail")
check("withdraw with another offer left: row handed over in place", p.update == [("s", SH.url)])
p = mc.plan([{"id": "s", "url": IC.url}], [], {IC.url: mc.RowInfo(False, IC.version)},
            withdrawn="live_track_intercity_rail")
check("withdraw, no offer left: our own row deleted", p.delete == ["s"])
p = mc.plan([{"id": "s", "url": SH.url}], [], {SH.url: mc.RowInfo(True, SH.version)},
            withdrawn="live_track_intercity_rail")
check("withdraw, no offer left: another integration's row untouched", not p.delete)
p = mc.plan(box, [], {BOX: mc.RowInfo(True, "0.1.0")}, withdrawn="live_track_intercity_rail")
check("withdraw, no offer left: the hand row untouched", not p.delete)

# --- YAML mode --------------------------------------------------------------------------------
y = [{"url": "/local/community/lovelace-auto-entities/auto-entities.js", "type": "module"}]
p = mc.plan(y, [IC], {}, mode="yaml")
check("yaml, no card row: the URL to paste", p.yaml_needs == IC.url and not p.create)
y2 = y + [{"url": IC.url, "type": "module"}]
p = mc.plan(y2, [IC], {IC.url: mc.RowInfo(True, IC.version)}, mode="yaml")
check("yaml, row present at the winner: nothing", p.yaml_needs is None)
p = mc.plan(y2, [IC, SH], {IC.url: mc.RowInfo(True, IC.version)}, mode="yaml")
check("yaml, row lower than the winner: the new URL", p.yaml_needs == SH.url)
p = mc.plan(y + [{"url": HACS, "type": "module"}], [SH], {HACS: mc.RowInfo(True, "0.0.1")}, mode="yaml")
check("yaml, HACS row: nothing to ask", p.yaml_needs is None)

check("dev offer cannot win against nothing-but-unparseable", mc.best_offer([mc.Offer("x", "junk", "u")]) is None)

failed = [n for n, ok in CHECKS if not ok]
print("checks: %d   passed: %d   failed: %d" % (len(CHECKS), len(CHECKS) - len(failed), len(failed)))
for n in failed:
    print("  FAIL: %s" % n)
sys.exit(1 if failed else 0)
