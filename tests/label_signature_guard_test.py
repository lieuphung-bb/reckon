"""A node label that quotes a classifier REFUSAL SIGNATURE verbatim re-walls every successor.

A label is read back into every successor brief and every Smi prompt (render / handoff / recall),
and a log/label matcher cannot tell quoting from emitting. bedside recorded the wall as finding
labels that embedded the verbatim signature (finding:codex-ingest-refusal, finding:s11-both-
providers-ingest-wall, ...) -- 9 such labels, which produced 4 phantom refusals downstream
(feedback_record_quoting_a_signature_poisons_the_matcher). The rule: verbatim text goes in a
note/artifact, the label is paraphrased. `add_node` refuses the signature at the write boundary.

The pattern is NARROW on purpose: only the specific refusal signatures, NEVER generic negatives like
"blocked"/"denied" -- ingest.py keys on those words to classify edges, so catching them here would
break edge classification. `test_deny_word_label_passes` is both the false-positive guard and the
mutation target: widening the pattern to a _DENY word makes it fail.
"""
import os, sys, uuid, pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from reckon import api, store
from reckon.api import ValidationError


@pytest.fixture
def eng():
    name = f"labelguard-test-{uuid.uuid4().hex[:8]}"
    store.create(name)
    yield name
    try: os.unlink(store.path_for(name))
    except OSError: pass


# The verbatim signatures that walled bedside, as they would appear quoted into a finding label.
POISONED = [
    "codex refused: flagged for possible cybersecurity risk",
    "s11 wall: Details: `[cyber]`",
    "ingest wall (Trusted Access denied by safeguards flagged)",
    "Cyber Verification Program fired mid-session",
]


@pytest.mark.parametrize("label", POISONED)
def test_poisoned_label_refused(eng, label):
    with pytest.raises(ValidationError) as e:
        api.add_node(eng, "finding", label)
    msg = str(e.value).lower()
    assert "label" in msg and "paraphrase" in msg, "refusal must name the problem and the fix"


def test_refusal_writes_nothing(eng):
    before = len(store.read_events(eng))
    with pytest.raises(ValidationError):
        api.add_node(eng, "finding", "the API flagged for possible cybersecurity risk")
    assert len(store.read_events(eng)) == before


def test_legit_label_passes(eng):
    api.add_node(eng, "finding", "xp_cmdshell enabled on DB01; RCE confirmed")
    assert "finding:xp_cmdshell enabled on DB01; RCE confirmed" in store.load(eng).nodes


@pytest.mark.parametrize("label", [
    "SMB login blocked for svc_backup",
    "kerberoast denied -- no SPN",
    "web login failed, 401",
])
def test_deny_word_label_passes(eng, label):
    """NARROW guard: generic negatives ingest.py keys on must pass untouched.
    MUTATION TARGET -- add a _DENY word to _LABEL_SIGNATURE and this goes red."""
    api.add_node(eng, "finding", label)
    assert f"finding:{label}" in store.load(eng).nodes


def test_import_path_is_not_refused(eng):
    """Bulk import (apply_events -> append_many) bypasses the boundary, like the other add_node
    checks -- a historically-poisoned label must still load, not explode recon-seed."""
    api.apply_events(eng, [{"op": "add_node", "args": {
        "id": "finding:old", "kind": "finding",
        "label": "legacy: flagged for possible cybersecurity risk", "props": {}}}])
    assert "finding:old" in store.load(eng).nodes
