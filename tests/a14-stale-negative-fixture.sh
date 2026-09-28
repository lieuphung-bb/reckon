#!/usr/bin/env bash
# a14-stale-negative-fixture.sh -- offline shape test for reckon alarm A14
# (stale-negative-on-identity-gain). Single self-contained entrypoint: no sidecar
# files, no network, no live engagement. Builds synthetic event logs, folds them,
# and asserts the SHAPE of stale_negative_on_identity_gain() over the six cases the
# spec names. PASS/FAIL per case; non-zero exit on any failure.
#
# The incident corpus does not travel; these are minimal synthetic graphs that
# assert the same shape it would.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"   # the reckon repo this fixture ships with

PYTHONPATH="$ROOT" python3 - <<'PY'
import sys
from reckon.model import fold
from reckon.queries import stale_negative_on_identity_gain as A14

def ev(op, seq, **a):
    return {"op": op, "seq": seq, "args": a}

def fires_for(rows, principal, resource):
    return any(r["principal"] == principal and r["resource"] == resource
               for r in rows)

fails = []
def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)

# Shared principal + resource ids across cases (each case folds a fresh log).
P1, P2, R = "cred:p1", "cred:p2", "svc:share"

# ---- C1: P1 records a REFUTED write capability-probed edge to R. Only P1 held.
#         A14 must be SILENT (no newly-gained identity to go stale for). --------
c1 = fold([
    ev("add_node", 1, id=P1, kind="cred", label="p1"),
    ev("set_exploitation", 2, id=P1, state="acquired"),
    ev("add_node", 3, id=R, kind="service", label="backups share"),
    ev("add_edge", 4, id=f"{P1}--capability-probed--{R}", src=P1, rel="capability-probed",
       dst=R, epistemic="refuted", props={"surface": "share"}),
])
r1 = A14(c1)
check("C1 only-P1 refuted write -> SILENT", r1 == [], f"got {r1}")

# ---- C2: P2 acquired (acquired_at set), no P2-scoped edge to R.
#         A14 must FIRE for (P2, R). ------------------------------------------
c2 = fold([
    ev("add_node", 1, id=P1, kind="cred", label="p1"),
    ev("set_exploitation", 2, id=P1, state="acquired"),
    ev("add_node", 3, id=R, kind="service", label="backups share"),
    ev("add_edge", 4, id=f"{P1}--capability-probed--{R}", src=P1, rel="capability-probed",
       dst=R, epistemic="refuted", props={"surface": "share"}),
    ev("add_node", 5, id=P2, kind="cred", label="p2"),
    ev("set_exploitation", 6, id=P2, state="acquired"),
])
r2 = A14(c2)
check("C2 new identity P2, unprobed -> FIRES (P2,R)", fires_for(r2, P2, R), f"got {r2}")
check("C2 P1 itself does not fire (it probed R)", not fires_for(r2, P1, R), f"got {r2}")

# ---- C3: P2 then records a capability-probed edge to R (its own probe).
#         A14 must CLEAR for (P2, R) -- principal-scoped clearing. -------------
c3 = fold([
    ev("add_node", 1, id=P1, kind="cred", label="p1"),
    ev("set_exploitation", 2, id=P1, state="acquired"),
    ev("add_node", 3, id=R, kind="service", label="backups share"),
    ev("add_edge", 4, id=f"{P1}--capability-probed--{R}", src=P1, rel="capability-probed",
       dst=R, epistemic="refuted", props={"surface": "share"}),
    ev("add_node", 5, id=P2, kind="cred", label="p2"),
    ev("set_exploitation", 6, id=P2, state="acquired"),
    ev("add_edge", 7, id=f"{P2}--capability-probed--{R}", src=P2, rel="capability-probed",
       dst=R, epistemic="refuted", props={"surface": "share"}),
])
r3 = A14(c3)
check("C3 P2 probed R -> CLEARS (P2,R)", not fires_for(r3, P2, R), f"got {r3}")

# ---- C4 (flood): a per-account credential-guess failure (ad-auth). Not a
#         write/ACL surface -> A14 must STAY SILENT even with a new identity. --
c4 = fold([
    ev("add_node", 1, id=P1, kind="cred", label="p1"),
    ev("set_exploitation", 2, id=P1, state="acquired"),
    ev("add_node", 3, id="svc:dc-smb", kind="service", label="DC smb",
       props={"surface": "ad-auth"}),
    ev("add_edge", 4, id=f"{P1}--capability-probed--svc:dc-smb", src=P1,
       rel="capability-probed", dst="svc:dc-smb", epistemic="refuted",
       props={"surface": "ad-auth"}),
    ev("add_node", 5, id=P2, kind="cred", label="p2"),
    ev("set_exploitation", 6, id=P2, state="acquired"),
])
r4 = A14(c4)
check("C4 cred-guess (ad-auth) negative -> SILENT", r4 == [], f"got {r4}")

# ---- C5 (technique): an identity-invariant version/technique dead-end negative
#         on R (surface not in the write/ACL family) -> A14 must STAY SILENT. --
c5 = fold([
    ev("add_node", 1, id=P1, kind="cred", label="p1"),
    ev("set_exploitation", 2, id=P1, state="acquired"),
    ev("add_node", 3, id=R, kind="service", label="backups share"),
    ev("add_edge", 4, id=f"{P1}--capability-probed--{R}", src=P1, rel="capability-probed",
       dst=R, epistemic="refuted", props={"surface": "version"}),
    ev("add_node", 5, id=P2, kind="cred", label="p2"),
    ev("set_exploitation", 6, id=P2, state="acquired"),
])
r5 = A14(c5)
check("C5 technique/version negative -> SILENT", r5 == [], f"got {r5}")

# ---- C6 (DISCRIMINATOR, load-bearing): R is a write/ACL resource EXAMINED by a
#         prior identity (P1), with NO capability-probed edge at all. P2 gained,
#         never probed R. A14 must STILL FIRE for (P2,R). Only the "never trust
#         examined" design catches this: `examined` is set, but no principal-
#         scoped probe exists, and there is no any-src edge for a broken clearing
#         predicate to (wrongly) latch onto. ------------------------------------
c6 = fold([
    ev("add_node", 1, id=R, kind="service", label="backups share",
       props={"surface": "share"}),
    ev("add_node", 2, id=P1, kind="cred", label="p1"),
    ev("set_exploitation", 3, id=P1, state="acquired"),
    ev("examine", 4, id=R, outcome="listed the share as p1; write untested"),
    ev("add_node", 5, id=P2, kind="cred", label="p2"),
    ev("set_exploitation", 6, id=P2, state="acquired"),
])
assert c6.nodes[R].exploitation == "examined", "fixture setup: R must be examined"
r6 = A14(c6)
check("C6 examined-by-prior, no P2 probe -> STILL FIRES (P2,R)",
      fires_for(r6, P2, R), f"got {r6}")

print()
if fails:
    print(f"a14 fixture: FAIL ({len(fails)} case(s): {', '.join(fails)})")
    sys.exit(1)
print("a14 fixture: PASS (6/6 cases)")
PY
rc=$?
exit $rc
