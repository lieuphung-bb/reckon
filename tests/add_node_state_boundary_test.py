"""`add` on an existing node must refuse state it cannot apply, not report success.

cohort 2026-09-16: a Com re-established a foothold and recorded it with
`reckon add ... --exploitation acquired`. The node already existed, so the fold merged
props and label and discarded the state (model.py, add_node branch: "idempotent: merge,
never clobber"). Exit 0, node id printed, nothing to tell anyone. The host then read
`discovered` while its own label said execution was held, so two verdict-time floor checks
that gate on acquired|examined could never fire -- and the run's conclusion that they were
broken was wrong.

Across the stored engagements, 25 of 898 add_node events dropped state this way (eureka 14,
fries 7, cohort 4), so this was silently losing writes in three engagements.

The merge itself is correct and is NOT changed here: ingest re-emits add_node for every
imported host, so a clobbering add would demote an acquired host every time recon-seed
re-runs after a revert. The fix is a refusal at the api boundary, which bulk import does not
pass through (it goes via apply_events -> store.append_many).
"""
import os, sys, uuid, pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from reckon import api, store
from reckon.api import ValidationError


@pytest.fixture
def eng():
    name = f"boundary-test-{uuid.uuid4().hex[:8]}"
    store.create(name)
    api.add_node(name, "host", "1.1.1.1", node_id="host:t")
    yield name
    try: os.unlink(store.path_for(name))
    except OSError: pass


def _expl(name, nid="host:t"):
    return store.load(name).nodes[nid].exploitation


@pytest.mark.parametrize("kwargs,field", [
    ({"exploitation": "acquired"}, "exploitation"),
    ({"epistemic": "verified"},    "epistemic"),
    ({"confidence": "A"},          "confidence"),
    ({"source": "s.txt"},          "source"),
])
def test_refuses_state_it_would_discard(eng, kwargs, field):
    with pytest.raises(ValidationError) as e:
        api.add_node(eng, "host", "1.1.1.1", node_id="host:t", **kwargs)
    assert field in str(e.value), "the refusal must name the field being dropped"


def test_refusal_names_the_verb_that_works(eng):
    with pytest.raises(ValidationError) as e:
        api.add_node(eng, "host", "1.1.1.1", node_id="host:t", exploitation="acquired")
    # A refusal that does not say what to do instead just moves the dead end.
    assert "hold host:t acquired" in str(e.value)


def test_refusal_writes_nothing(eng):
    before = len(store.read_events(eng))
    with pytest.raises(ValidationError):
        api.add_node(eng, "host", "1.1.1.1", node_id="host:t", exploitation="acquired")
    assert len(store.read_events(eng)) == before
    assert _expl(eng) == "discovered"


def test_plain_re_add_still_merges(eng):
    """props/label updates are the legitimate use and must stay silent and idempotent."""
    api.add_node(eng, "host", "relabelled", node_id="host:t", props={"k": "v"})
    n = store.load(eng).nodes["host:t"]
    assert n.label == "relabelled" and n.props.get("k") == "v"


def test_creation_still_accepts_state(eng):
    """The refusal is about EXISTING ids only -- creating with state is the normal path."""
    api.add_node(eng, "host", "2.2.2.2", node_id="host:new", exploitation="acquired")
    assert _expl(eng, "host:new") == "acquired"


def test_import_path_is_not_refused(eng):
    """Bulk import re-emits add_node for every host and must stay idempotent: a refusal here
    would make recon-seed demote-or-explode after a revert."""
    api.apply_events(eng, [{"op": "add_node", "args": {
        "id": "host:t", "kind": "host", "label": "1.1.1.1",
        "epistemic": "verified", "exploitation": "acquired", "props": {}}}])
    assert _expl(eng) == "discovered"   # merged, not clobbered, and not refused


def test_hold_is_the_working_verb(eng):
    api.set_exploitation(eng, "host:t", "acquired") if hasattr(api, "set_exploitation") \
        else api.hold(eng, "host:t", "acquired")
    assert _expl(eng) == "acquired"
