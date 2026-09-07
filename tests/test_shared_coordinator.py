"""Tests for the shared-coordinator reference implementation.

The version-skew path is the reason this file exists: it is the branch that
would otherwise fail silently in a real install, months after release, when
one of three independently-versioned integrations updates and the others do
not.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "reference"))

import saw_amtraker as sa  # noqa: E402
import shared_coordinator as sc  # noqa: E402

CHECKS = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


class FakeHass:
    def __init__(self):
        self.data = {}


UA = "SAW-test/1.0"

# --- the point of the exercise: N integrations, ONE client ----------------
hass = FakeHass()
a = sc.get_shared_client(hass, UA)
b = sc.get_shared_client(hass, UA)
c = sc.get_shared_client(hass, UA)
check("three integrations receive the SAME client object", a is b is c)
check("refcount tracks three claims", hass.data[sa.SHARED_KEY]["refcount"] == 3)
check("entry stamped with the contract version",
      hass.data[sa.SHARED_KEY]["contract_version"] == sa.CONTRACT_VERSION)
check("shared key carries the version in its name",
      sa.SHARED_KEY.endswith(str(sa.CONTRACT_VERSION)))

# --- release: the entry must go when the LAST claim goes ------------------
sc.release_shared_client(hass)
check("entry survives while claims remain", sa.SHARED_KEY in hass.data)
check("refcount decremented", hass.data[sa.SHARED_KEY]["refcount"] == 2)
sc.release_shared_client(hass)
sc.release_shared_client(hass)
check("entry removed after the last release", sa.SHARED_KEY not in hass.data)
sc.release_shared_client(hass)
check("releasing an absent entry is harmless", sa.SHARED_KEY not in hass.data)

# --- VERSION SKEW. The branch that would fail silently in production. -----
hass2 = FakeHass()
foreign = {"contract_version": 999, "entry_shape": 1,
           "client": sa.AmtrakerClient(user_agent=UA), "refcount": 1}
hass2.data[sa.SHARED_KEY] = foreign
own = sc.get_shared_client(hass2, UA)
check("unknown contract version -> a PRIVATE client, not the foreign one",
      own is not foreign["client"])
check("the foreign entry is NOT overwritten (another integration owns it)",
      hass2.data[sa.SHARED_KEY] is foreign)
check("the foreign refcount is left alone", foreign["refcount"] == 1)

hass3 = FakeHass()
odd = {"contract_version": sa.CONTRACT_VERSION, "entry_shape": 42,
       "client": sa.AmtrakerClient(user_agent=UA), "refcount": 1}
hass3.data[sa.SHARED_KEY] = odd
check("unknown entry SHAPE also falls back to private",
      sc.get_shared_client(hass3, UA) is not odd["client"])

for label, junk in (("a non-dict", "not a dict"),
                    ("a dict with no client", {"contract_version":
                                               sa.CONTRACT_VERSION,
                                               "entry_shape": 1}),
                    ("a dict whose client is the wrong type",
                     {"contract_version": sa.CONTRACT_VERSION,
                      "entry_shape": 1, "client": object()})):
    h = FakeHass()
    h.data[sa.SHARED_KEY] = junk
    got = sc.get_shared_client(h, UA)
    check("garbage at the shared key (%s) -> private client" % label,
          isinstance(got, sa.AmtrakerClient))
    check("garbage at the shared key (%s) left untouched" % label,
          h.data[sa.SHARED_KEY] is junk)
    sc.release_shared_client(h)
    check("release ignores garbage (%s)" % label,
          h.data.get(sa.SHARED_KEY) is junk)

failed = [n for n, ok in CHECKS if not ok]
print("checks: %d   passed: %d   failed: %d"
      % (len(CHECKS), len(CHECKS) - len(failed), len(failed)))
for n in failed:
    print("  FAIL: %s" % n)
sys.exit(1 if failed else 0)
