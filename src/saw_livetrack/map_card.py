"""Live Track Map Card: which bundle an instance loads, and how its ONE resource row changes.

Backlog 291 (35_Dependencies/ours/live-track-map-card/SSOT.md section 67). Every Live Track
integration ships the released card, serves it from its own static path and offers it. This
module decides; reference/shared_map_card.py does the Home Assistant calls. Pure Python, no
homeassistant import (05_SHARED_LESSONS.md B8.1a). One copy per Python environment, so every
integration on an instance runs THIS decision code, whichever release it came with.

THE VERSION RULE (67.2). SemVer 2.0.0 precedence of MAJOR.MINOR.PATCH[-PRERELEASE]; build
metadata (+hash, .dirty) is ignored. Strictly higher replaces; equal keeps what is registered;
unparseable is unknown and never replaced on version grounds. Integrations ship only released
bundles, so one version is one file.

ONE ROW (67.3, damage 5). A card row is matched by FILE NAME, never by prefix (06_MAP_CONTRACT.md
D9). Rows under /hacsfiles/ are never touched and never duplicated. Writes UPDATE in place.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, unquote, urlsplit

CARD_FILE = "live-track-map-card.js"
FRONTEND_DIR = "frontend"
CONTRACT_VERSION = 1
SHARED_KEY = "saw_live_track_map_card_v%d" % CONTRACT_VERSION
# A Lock's interface never changes, so its key never carries a version: every copy, of any
# contract, must queue on the SAME lock or two of them can both see "no row" and both create one.
LOCK_KEY = "saw_live_track_map_card_lock"

HEADER = re.compile(rb"^/\*! live-track-map-card (\S+) \*/")
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")
_SAW_PATH = re.compile(r"^/(live_track_[a-z0-9_]+)/map-card/([0-9a-f]{12})/" + re.escape(CARD_FILE) + r"$")

SAW, LOCAL, HACS, OTHER = "saw", "local", "hacs", "other"


# --- versions ---------------------------------------------------------------------------------

def read_version(data):
    """The version stamped on line 1 of a bundle (card SSOT section 21), or None."""
    m = HEADER.match(data or b"")
    return m.group(1).decode("ascii", "replace") if m else None


def precedence(version):
    """A sort key for SemVer 2.0.0 precedence, or None if `version` is not semver."""
    m = _SEMVER.match(version or "")
    if not m:
        return None
    core = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    pre = m.group(4)
    if pre is None:
        return core + ((1,),)              # a release ranks above every prerelease of its core
    ids = []
    for p in pre.split("."):
        ids.append((0, int(p), "") if p.isdigit() else (1, 0, p))   # numeric < alphanumeric
    return core + ((0, tuple(ids)),)


def compare(a, b):
    """-1, 0 or 1 by precedence; None when either is unknown."""
    ka, kb = precedence(a), precedence(b)
    if ka is None or kb is None:
        return None
    return (ka > kb) - (ka < kb)


def is_release(version):
    """True for a plain release: semver, no prerelease, no `.dirty`."""
    m = _SEMVER.match(version or "")
    return bool(m) and m.group(4) is None and "dirty" not in (m.group(5) or "").split(".")


# --- URLs -------------------------------------------------------------------------------------

def static_base(domain, sha12):
    """The static path an integration serves its `frontend/` folder at (content-addressed)."""
    return "/%s/map-card/%s" % (domain, sha12)


def resource_url(domain, sha12, version):
    """The resource URL: the served file, the version percent-encoded (D9c), the content hash."""
    return "%s/%s?v=%s&h=%s" % (static_base(domain, sha12), CARD_FILE, quote(version, safe=""), sha12)


def _path(url):
    try:
        return posixpath.normpath(unquote(urlsplit(url).path))
    except (ValueError, TypeError):
        return ""


def is_card_row(url):
    return _path(url).rsplit("/", 1)[-1] == CARD_FILE


def url_version(url):
    """The decoded `v` of a resource URL, or None."""
    try:
        v = parse_qs(urlsplit(url).query).get("v")
    except (ValueError, TypeError):
        return None
    return v[0] if v else None


@dataclass(frozen=True)
class RowFile:
    kind: str                 # SAW, LOCAL, HACS or OTHER
    rel_path: str | None      # under the config directory, or None when it cannot be known
    domain: str | None = None
    sha12: str | None = None


def row_file(url):
    """Where on disk the file behind a card row is, relative to the config directory."""
    p = _path(url)
    m = _SAW_PATH.match(p)
    if m:
        return RowFile(SAW, "custom_components/%s/%s/%s" % (m.group(1), FRONTEND_DIR, CARD_FILE),
                       m.group(1), m.group(2))
    if p.startswith("/local/"):
        return RowFile(LOCAL, "www/" + p[len("/local/"):])
    if p.startswith("/hacsfiles/"):
        return RowFile(HACS, "www/community/" + p[len("/hacsfiles/"):])
    return RowFile(OTHER, None)


@dataclass(frozen=True)
class RowInfo:
    alive: bool               # the file is there and is what the row says it is
    version: str | None       # from the file's line 1, else the URL's v


def inspect(url, data, sha12_of_data=None, domain_loaded=True):
    """What a card row serves. `data` is the file's bytes (None if absent or unknown)."""
    rf = row_file(url)
    if rf.kind == OTHER:
        return RowInfo(True, url_version(url))           # not ours to read: trust what it says
    if data is None:
        return RowInfo(False, url_version(url))
    if rf.kind == SAW and (sha12_of_data != rf.sha12 or not domain_loaded):
        return RowInfo(False, read_version(data))        # another file now, or nothing serves it
    return RowInfo(True, read_version(data) or url_version(url))


# --- the plan ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Offer:
    domain: str
    version: str
    url: str


@dataclass
class Plan:
    winner: Offer | None = None
    create: str | None = None                            # URL of a row to create
    update: list = field(default_factory=list)           # (row id, new URL)
    delete: list = field(default_factory=list)           # row ids
    user_duplicates: list = field(default_factory=list)  # URLs of extra rows the user made
    hacs_url: str | None = None                          # a HACS row: integrations stand down
    hacs_older: bool = False                             # the winner is newer than the HACS copy
    yaml_needs: str | None = None                        # YAML mode: the URL to paste
    notes: list = field(default_factory=list)


def best_offer(offers, current_url=None):
    """Highest precedence; ties go to the offer the row already points at, then by domain."""
    best = None
    for o in sorted(offers, key=lambda o: o.domain):
        if precedence(o.version) is None:
            continue
        if best is None:
            best = o
            continue
        c = compare(o.version, best.version)
        if c > 0 or (c == 0 and o.url == current_url and best.url != current_url):
            best = o
    return best


def _rank(info):
    """Order rows for "keep the highest": dead lowest, unknown next, then by precedence."""
    if not info.alive:
        return (0,)
    k = precedence(info.version)
    return (1,) if k is None else (2, k)


def plan(rows, offers, infos, mode="storage", withdrawn=None):
    """Decide what to do. rows: [{"id", "url", ...}]; offers: [Offer]; infos: {url: RowInfo}.

    withdrawn: the domain being uninstalled; with no offer left, its own rows are deleted.
    """
    out = Plan()
    card = [r for r in rows if is_card_row(r.get("url", ""))]
    hacs = [r for r in card if row_file(r["url"]).kind == HACS]
    current = card[0]["url"] if len(card) == 1 else None
    out.winner = best_offer(offers, current)

    if out.winner is None:
        if withdrawn and mode == "storage":
            for r in card:
                rf = row_file(r["url"])
                if rf.kind == SAW and rf.domain == withdrawn:
                    out.delete.append(r["id"])
            out.notes.append("no offer left: removed this integration's own row" if out.delete
                             else "no offer left and no row of this integration's: nothing to do")
        else:
            out.notes.append("no usable offer: nothing to do")
        return out

    win = out.winner
    if mode != "storage":
        ok = any(row_file(r["url"]).kind == HACS for r in card) or any(
            infos.get(r["url"], RowInfo(True, None)).alive
            and compare(infos.get(r["url"], RowInfo(True, None)).version, win.version) in (0, 1)
            for r in card)
        if not ok:
            out.yaml_needs = win.url
        out.notes.append("yaml mode: " + ("resource present" if ok else "resource to add by hand"))
        return out

    if hacs:
        keep = hacs[0]
        out.hacs_url = keep["url"]
        hv = infos.get(keep["url"], RowInfo(True, None)).version
        out.hacs_older = compare(win.version, hv) == 1
        for r in card:
            if r is keep:
                continue
            if row_file(r["url"]).kind == SAW:
                out.delete.append(r["id"])
            else:
                out.user_duplicates.append(r["url"])
        out.notes.append("HACS installs the card here: standing down")
        return out

    if not card:
        out.create = win.url
        out.notes.append("no card row: creating one")
        return out

    keep = max(card, key=lambda r: _rank(infos.get(r["url"], RowInfo(True, None))))
    for r in card:
        if r is keep:
            continue
        if row_file(r["url"]).kind == SAW:
            out.delete.append(r["id"])
        else:
            out.user_duplicates.append(r["url"])
    info = infos.get(keep["url"], RowInfo(True, None))
    if keep["url"] == win.url:
        out.notes.append("row already serves the winner")
    elif not info.alive:
        out.update.append((keep["id"], win.url))
        out.notes.append("row is dead: replaced by the winner")
    else:
        c = compare(win.version, info.version)
        if c == 1:
            out.update.append((keep["id"], win.url))
            out.notes.append("winner %s is higher than the row's %s" % (win.version, info.version))
        elif c is None:
            out.notes.append("row's version %r is unknown: left alone" % (info.version,))
        else:
            out.notes.append("row's %s is not lower than %s: left alone" % (info.version, win.version))
    return out
