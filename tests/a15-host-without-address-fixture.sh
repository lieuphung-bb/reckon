#!/usr/bin/env bash
# a15-host-without-address-fixture.sh -- offline shape test for reckon alarm A15
# (host-without-address). Single self-contained entrypoint: no sidecar files, no
# network, no live engagement. Builds synthetic graphs, folds them, asserts the
# SHAPE of hostless_address(), and then asserts the end-to-end api.alarms()
# emission (the A15/<id> entry and its RECORDING group, which is what --strict
# gates on). PASS/FAIL per case; non-zero exit on any failure.
#
# The incident corpus does not travel; these are minimal synthetic graphs that
# assert the same shape it would. Layered defences (BUILDER.md) -- each case is
# built so ONLY the layer it targets can keep or flip its verdict:
#   C1 pins the id-field check     C2 the label-field check   C3 the props-field check
#   C6 the kind==host gate         C7 the 0-255 octet check   C9 the word-boundary anchor
# Mutation testing is driven externally (see the Builder report); this fixture is
# the baseline that must go green against the real code.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"   # the reckon repo this fixture ships with

PYTHONPATH="$ROOT" python3 - <<'PY'
import os
import sys
import tempfile

from reckon.model import fold
from reckon.queries import hostless_address as A15


def ev(op, seq, **a):
    return {"op": op, "seq": seq, "args": a}


def ids(rows):
    return sorted(r["id"] for r in rows)


def flagged(rows, nid):
    return any(r["id"] == nid for r in rows)


fails = []
def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


print("=== detector: hostless_address() shape ===")

# ---- C1: IPv4 in the ID only -> NOT flagged. id carries the address; label and
#          props do not, so ONLY the id-field check can keep this quiet. --------
c1 = fold([ev("add_node", 1, id="host:10.80.112.35", kind="host",
              label="web frontend", props={})])
check("C1 IPv4 in id only -> NOT flagged",
      not flagged(A15(c1), "host:10.80.112.35"), f"got {ids(A15(c1))}")

# ---- C2: IPv4 in the LABEL only (id is a name, props empty) -> NOT flagged.
#          ONLY the label-field check can keep this quiet. ---------------------
c2 = fold([ev("add_node", 1, id="host_web01", kind="host",
              label="web01 at 10.80.112.36", props={})])
check("C2 IPv4 in label only -> NOT flagged",
      not flagged(A15(c2), "host_web01"), f"got {ids(A15(c2))}")

# ---- C3: IPv4 in PROPS only (id and label are names/prose) -> NOT flagged.
#          ONLY the props-field check can keep this quiet. ---------------------
c3 = fold([ev("add_node", 1, id="host_db01", kind="host",
              label="db primary", props={"ip": "10.80.112.37"})])
check("C3 IPv4 in props only -> NOT flagged",
      not flagged(A15(c3), "host_db01"), f"got {ids(A15(c3))}")

# ---- C4 (incident shape): IPv4 in NO field -> FLAGGED. id is a name, label is
#          prose naming CLIENT03 with no dotted-quad, props empty. -------------
c4 = fold([ev("add_node", 1, id="host_client03_internal", kind="host",
              label="CLIENT03 (release build host)", props={})])
r4 = A15(c4)
check("C4 IPv4 in NO field -> FLAGGED", flagged(r4, "host_client03_internal"),
      f"got {ids(r4)}")
check("C4 one entry only", len(r4) == 1, f"got {ids(r4)}")
check("C4 row shape is {{id,label}}",
      r4 == [{"id": "host_client03_internal",
              "label": "CLIENT03 (release build host)"}], f"got {r4}")

# ---- C5: two IP-less hosts -> both flagged, one entry each. ------------------
c5 = fold([
    ev("add_node", 1, id="host_client03_internal", kind="host",
       label="CLIENT03", props={}),
    ev("add_node", 2, id="host_client04_internal", kind="host",
       label="CLIENT04", props={}),
])
check("C5 two IP-less hosts -> both flagged, one each",
      ids(A15(c5)) == ["host_client03_internal", "host_client04_internal"],
      f"got {ids(A15(c5))}")

# ---- C6: a non-host node (finding) with no IP -> NOT flagged. ONLY the
#          kind==host gate can keep this quiet (it has no address). -----------
c6 = fold([ev("add_node", 1, id="finding:weak-policy", kind="finding",
              label="password policy is weak", props={})])
check("C6 non-host (finding), no IP -> NOT flagged", A15(c6) == [],
      f"got {ids(A15(c6))}")
c6b = fold([ev("add_node", 1, id="cred:svc-acct", kind="cred",
               label="service account, no address", props={})])
check("C6b non-host (cred), no IP -> NOT flagged", A15(c6b) == [],
      f"got {ids(A15(c6b))}")

# ---- C7: near-miss octet 10.80.112.999 in the label, no real IP -> FLAGGED.
#          ONLY the 0-255 octet check tells this from an address. -------------
c7 = fold([ev("add_node", 1, id="host_edge01", kind="host",
              label="edge gw 10.80.112.999", props={})])
check("C7 near-miss octet (>255) -> still FLAGGED",
      flagged(A15(c7), "host_edge01"), f"got {ids(A15(c7))}")

# ---- C8: dotted non-numeric token client.03.release.5 -> FLAGGED. Regex
#          structure: not four numeric groups, so not an address. -------------
c8 = fold([ev("add_node", 1, id="host_rel01", kind="host",
              label="client.03.release.5 build host", props={})])
check("C8 dotted non-IP token -> still FLAGGED",
      flagged(A15(c8), "host_rel01"), f"got {ids(A15(c8))}")

# ---- C9: 5-group version string 1.2.3.4.5 -> FLAGGED. ONLY the word-boundary
#          anchor stops a dotted-quad embedded in a longer run from matching. --
c9 = fold([ev("add_node", 1, id="host_ver01", kind="host",
              label="appliance firmware 1.2.3.4.5", props={})])
check("C9 5-group version string -> still FLAGGED",
      flagged(A15(c9), "host_ver01"), f"got {ids(A15(c9))}")

# ---- C10: a refuted IP-less host -> NOT flagged (a refuted host does not
#           exist). -----------------------------------------------------------
c10 = fold([
    ev("add_node", 1, id="host_ghost", kind="host", label="GHOST", props={}),
    ev("set_epistemic", 2, id="host_ghost", state="refuted"),
])
check("C10 refuted IP-less host -> NOT flagged", A15(c10) == [],
      f"got {ids(A15(c10))}")

# === end-to-end: api.alarms() emits A15 with group RECORDING ===================
# This is the wiring (registry + emitter) that mutant M3 breaks; the detector
# cases above cannot see it. A fresh temp store so nothing touches real data.
print()
print("=== emission: api.alarms() wires A15 into the RECORDING group ===")

import reckon.api as api
import reckon.store as store

home = tempfile.mkdtemp()
_oE, _oH, _oO = store.ENGAGEMENTS, store.RECKON_HOME, store.OUT
store.ENGAGEMENTS = home
store.RECKON_HOME = home
store.OUT = os.path.join(home, "out")
try:
    api.create("a15")
    api.add_node("a15", "host", "CLIENT03 release host",
                 node_id="host_client03_internal", epistemic="verified")  # IP-less
    api.add_node("a15", "host", "web frontend",
                 node_id="host:10.80.112.35", epistemic="verified")       # addressed
    al = api.alarms("a15")
    by_id = {a["id"]: a for a in al}
    a15s = [a for a in al if a["name"] == "host-without-address"]
    check("E2E A15 emitted for the IP-less host",
          "A15/host_client03_internal" in by_id, f"got {sorted(by_id)}")
    check("E2E A15 NOT emitted for the addressed host",
          "A15/host:10.80.112.35" not in by_id, f"got {sorted(by_id)}")
    check("E2E exactly one A15 fired", len(a15s) == 1,
          f"got {[a['id'] for a in a15s]}")
    rec = a15s[0] if a15s else {}
    check("E2E A15 group is RECORDING (so --strict gates on it)",
          rec.get("group") == api.RECORDING, f"got {rec.get('group')}")
finally:
    store.ENGAGEMENTS, store.RECKON_HOME, store.OUT = _oE, _oH, _oO

print()
if fails:
    print(f"a15 fixture: FAIL ({len(fails)} case(s): {', '.join(fails)})")
    sys.exit(1)
print("a15 fixture: PASS (all cases)")
PY
rc=$?
exit $rc
