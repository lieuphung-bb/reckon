#!/usr/bin/env bash
#
# a14-no-collision-fixture.sh
#
# Claim under test (the A14 fix's blast-radius guarantee, NOT A14 itself):
#   A write/ACL probe recorded as a `capability-probed` edge is INVISIBLE to
#   A10 (untried-surface) and A13 (unexercised-reachable-service). Recording
#   one must NOT clear either alarm.
#
# Why it can: A10 (`_cred_tried_kinds`) reads the cred's OUTGOING `tested-against`
# edges and classifies the target by service kind/port; A13
# (`unexercised_reachable_service`) reads a service's INCOMING `tested-against`
# edges. Both ignore the edge's `surface` prop. Before the fix a write/ACL probe
# was a `tested-against` edge carrying `surface=smb-write`; on an SMB/ad-auth
# service that ONE edge silently cleared both alarms -- a per-write negative
# masquerading as a per-cred-store / per-service negative. Giving the probe its
# own rel `capability-probed`, which neither A10 nor A13 reads, is the fix.
#
# This fixture proves the read side: with the probe recorded as `capability-probed`,
# A10 and A13 STILL fire. The MUTANT flips that one edge's rel back to
# `tested-against` and asserts both alarms then WRONGLY clear -- the exact
# collision the distinct rel removes.
#
# Offline, single self-contained .sh, temp RECKON_HOME, PASS/FAIL lines, non-zero
# exit on any failure. Uses reckon's edge API (`add_edge`) directly; does not
# depend on the record-probe tool.
#
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

FAIL=0
pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; FAIL=1; }

# Build the synthetic graph, record the probe edge with the given rel, and print
# the state of A10/A13 as machine-readable lines. A fresh RECKON_HOME per call so
# the event logs never bleed between scenarios (this is the "restore": each run is
# independent, the mutation is only which rel arg is passed).
#   $1 = rel for the probe edge  (capability-probed | tested-against)
run_scenario() {
  local probe_rel="$1"
  local home
  home="$(mktemp -d)"
  RECKON_HOME="$home" python3 - "$probe_rel" <<'PY'
import sys
probe_rel = sys.argv[1]

import reckon.api as api
import reckon.model as model

# Scaffold ONLY: `capability-probed` is registered in RELS by the parallel A14
# change. If that change has not landed in this checkout, inject the rel so the
# edge API accepts it -- this fixture tests A10/A13's READ behaviour, not RELS
# registration (that is A14's own fixture's job). A no-op once A14 lands.
if "capability-probed" not in api.RELS:
    inj = tuple(api.RELS) + ("capability-probed",)
    api.RELS = inj
    model.RELS = inj
    print("NOTE: capability-probed absent from RELS; injected as test scaffold "
          "(parallel A14 registration not yet in this checkout)", file=sys.stderr)

name = "a14nc"

# 1. Synthetic graph.
#    - foothold host we hold (source of the `reaches` edge -> A13 gate)
#    - S: SMB service, port 445, ad-auth-classified, verified, reachable
#    - a web service P was tried-and-failed on, to ARM A10 (a cred with zero
#      attempts is `unmined`'s job, not A10's; A10 needs one real refused kind)
#    - P: a held credential
api.add_node(name, "host", "foothold-host",
             node_id="host:10.10.10.5", epistemic="verified",
             exploitation="acquired")
api.add_node(name, "service", "smb microsoft-ds (445)",
             node_id="service:smb", epistemic="verified",
             props={"port": "445", "host": "10.10.10.5"})
api.add_node(name, "service", "http nginx (80)",
             node_id="service:web", epistemic="verified",
             props={"port": "80", "host": "10.10.10.5"})
api.add_node(name, "cred", "P alice:winter2026",
             node_id="cred:P", exploitation="acquired")   # held

# 2a. A13 gate: the held foothold reaches the SMB service.
api.add_edge(name, "host:10.10.10.5", "reaches", "service:smb",
             epistemic="verified")
# 2b. Arm A10: P was tried on the web app and refused (one real earned negative
#     on the webapp kind; ad-auth stays untried).
api.add_edge(name, "cred:P", "tested-against", "service:web",
             epistemic="refuted")
# 2c. THE PROBE: a write/ACL probe of the SMB service by P, refuted, surface
#     smb-write -- recorded under `probe_rel`. Baseline: capability-probed.
api.add_edge(name, "cred:P", probe_rel, "service:smb",
             epistemic="refuted", props={"surface": "smb-write"})

al = api.alarms(name)
by_id = {a["id"]: a for a in al}

a10 = by_id.get("A10/cred:P")
a13 = by_id.get("A13/service:smb")

# A10 must not only fire, it must name ad-auth as the untried door (proves the
# smb-write probe did NOT count as an ad-auth attempt).
a10_adauth = bool(a10) and ("ad-auth" in (a10["detail"].get("untried_kinds") or []))
# A13 must fire for the SMB service specifically.
a13_smb = bool(a13) and a13["detail"].get("service") == "service:smb"

print(f"PROBE_REL={probe_rel}")
print(f"A10_FIRES={bool(a10)}")
print(f"A10_ADAUTH_UNTRIED={a10_adauth}")
print(f"A13_FIRES={bool(a13)}")
print(f"A13_SMB={a13_smb}")
PY
  local rc=$?
  rm -rf "$home"
  return $rc
}

echo "=============================================================="
echo " A14 no-collision fixture: capability-probed is invisible to"
echo " A10 (untried-surface) and A13 (unexercised-reachable-service)"
echo "=============================================================="

# ---- BASELINE: probe recorded as capability-probed -> both alarms STILL fire --
echo
echo "--- BASELINE: probe rel = capability-probed ---"
BASE="$(run_scenario capability-probed)" || { echo "$BASE"; fail "baseline scenario crashed"; }
echo "$BASE"

grep -q '^A10_FIRES=True$'         <<<"$BASE" && pass "A10 still fires for P (capability-probed did NOT clear it)" \
                                              || fail "A10 did not fire under capability-probed -- probe wrongly counted as a cred attempt"
grep -q '^A10_ADAUTH_UNTRIED=True$' <<<"$BASE" && pass "A10 names ad-auth as the untried door (smb-write probe not counted as ad-auth attempt)" \
                                              || fail "A10 fired but did not list ad-auth as untried"
grep -q '^A13_FIRES=True$'         <<<"$BASE" && pass "A13 still fires for the SMB service (capability-probed did NOT clear it)" \
                                              || fail "A13 did not fire under capability-probed -- probe wrongly counted as a service attempt"
grep -q '^A13_SMB=True$'           <<<"$BASE" && pass "A13 fires for service:smb specifically" \
                                              || fail "A13 fired for the wrong service"

# ---- MUTANT: flip that one edge's rel to tested-against -----------------------
# MUTATION NAMED: probe edge rel  capability-probed -> tested-against
# (the single-datum collision the fix removes). Expectation: BOTH A10 and A13
# now WRONGLY clear. Each assertion below is keyed on its own alarm id, so a
# mutant that silenced only one alarm would still be caught by the other's line
# -- the two are independent mechanisms (A10 reads P's out-edges; A13 reads S's
# in-edges) that happen to read the SAME edge's rel.
echo
echo "--- MUTANT: probe rel capability-probed -> tested-against (must go RED) ---"
MUT="$(run_scenario tested-against)" || { echo "$MUT"; fail "mutant scenario crashed"; }
echo "$MUT"

grep -q '^A10_FIRES=False$' <<<"$MUT" && pass "MUTANT caught: A10 wrongly clears when the probe is a tested-against edge" \
                                      || fail "MUTANT NOT caught by A10: A10 still fired under tested-against"
grep -q '^A13_FIRES=False$' <<<"$MUT" && pass "MUTANT caught: A13 wrongly clears when the probe is a tested-against edge" \
                                      || fail "MUTANT NOT caught by A13: A13 still fired under tested-against"

# ---- RESTORE: re-run baseline, both green again -------------------------------
echo
echo "--- RESTORE: probe rel = capability-probed (both green again) ---"
RES="$(run_scenario capability-probed)" || { echo "$RES"; fail "restore scenario crashed"; }
echo "$RES"
{ grep -q '^A10_FIRES=True$' <<<"$RES" && grep -q '^A13_FIRES=True$' <<<"$RES"; } \
    && pass "RESTORE: A10 and A13 fire again once the probe rel is capability-probed" \
    || fail "RESTORE: alarms did not return to firing"

echo
if [ "$FAIL" -ne 0 ]; then
  echo "RESULT: FAIL"
  exit 1
fi
echo "RESULT: PASS"
exit 0
