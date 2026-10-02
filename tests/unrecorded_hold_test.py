"""`unrecorded_hold`: a host a finding grants access to, but never marked acquired.

When access is achieved on a host (shell / exec channel / cred that logs in) the agent records a
`finding --grants-access-to--> host` edge but can forget to mark the host `acquired`. Then the
position lags the findings: every floor that gates on `held()` is blind to it and `com-resume`
builds a "findings but no position" brief, so a resume lands before the foothold. m11 2026-10-02:
host:db01 had db01-rce / db01-interactive-shell findings granting access, exploitation stayed
`discovered`, and the dead Com's mid-recon resumed stale.

The check is keyed on the edge RELATION, not a finding label, and only on `grants-access-to` from a
FINDING -- `escalates-to` (a reach/escalation path to a not-yet-owned host) and operator/other
sourced access (mere reachability) must NOT fire. `test_escalates_to_only_is_quiet` is the mutation
target: widening the rel filter to include `escalates-to` makes it go red.
"""
import os, sys, uuid, pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from reckon import api, store
from reckon.queries import unrecorded_hold


@pytest.fixture
def eng():
    name = f"unrecorded-hold-{uuid.uuid4().hex[:8]}"
    store.create(name)
    yield name
    try: os.unlink(store.path_for(name))
    except OSError: pass


def _ids(g):
    return {r["id"] for r in unrecorded_hold(g)}


def _hold(name, nid):
    (api.set_exploitation if hasattr(api, "set_exploitation") else api.hold)(name, nid, "acquired")


def test_fires_when_finding_grants_access_but_discovered(eng):
    api.add_node(eng, "host", "1.1.1.1", node_id="host:t")
    api.add_node(eng, "finding", "interactive shell on host:t", node_id="finding:shell")
    api.add_edge(eng, "finding:shell", "grants-access-to", "host:t")
    assert "host:t" in _ids(store.load(eng))


def test_quiet_once_acquired(eng):
    api.add_node(eng, "host", "1.1.1.1", node_id="host:t")
    api.add_node(eng, "finding", "interactive shell on host:t", node_id="finding:shell")
    api.add_edge(eng, "finding:shell", "grants-access-to", "host:t")
    _hold(eng, "host:t")
    assert "host:t" not in _ids(store.load(eng))


def test_quiet_when_granter_is_not_a_finding(eng):
    # mere reachability (operator/other source) is not an achieved hold -- entry-host case.
    api.add_node(eng, "host", "2.2.2.2", node_id="host:gw")
    api.add_node(eng, "host", "1.1.1.1", node_id="host:t")
    api.add_edge(eng, "host:gw", "grants-access-to", "host:t")
    assert "host:t" not in _ids(store.load(eng))


def test_escalates_to_only_is_quiet(eng):
    # a finding ESCALATES-TO a not-yet-owned host (DMZ DC case) -- a path, not a hold.
    # MUTATION TARGET: add "escalates-to" to the rel filter and this goes red.
    api.add_node(eng, "host", "3.3.3.3", node_id="host:dc")
    api.add_node(eng, "finding", "rce reaches host:dc", node_id="finding:rce")
    api.add_edge(eng, "finding:rce", "escalates-to", "host:dc")
    assert "host:dc" not in _ids(store.load(eng))
