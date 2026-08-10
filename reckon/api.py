"""The public, validated API. The CLI is a thin wrapper over it, and phase 2's
MCP server will be another.

Validation lives HERE rather than in the CLI so a second caller cannot bypass it.
Everything is strict on write and lenient on read: an invalid write is refused
loudly, while `fold` stays tolerant so a log written by an older build still
loads.

The loudness matters more than it sounds. `reckon state host:TYPO verified` used to
print "host:TYPO -> verified" and do nothing at all: the event appended, the fold
found no such node, and the operator believed a fact had been recorded. A tool
whose whole purpose is catching what you missed must never quietly miss.
"""

from . import store
from .model import (KINDS, RELS, EPISTEMIC, EXPLOITATION, OPERATOR_ID, fold)
from .reference import make_reference
from .queries import (frontier, unrealized, unmined, stale, coverage, why,
                      verification_queue, reach, budget, delta as _delta,
                      DEFAULT_BUDGET)
from . import recall as _recall

OBJECTIVE_STATUS = ("open", "achieved", "blocked")


class ValidationError(ValueError):
    pass


# --- helpers ------------------------------------------------------------------

def _one_of(value, allowed, what):
    if value is not None and value not in allowed:
        raise ValidationError(
            f"invalid {what}: {value!r}. Expected one of {', '.join(allowed)}")
    return value


def _require_nodes(name, ids):
    """Refuse to reference nodes that do not exist."""
    g = store.load(name)
    missing = [i for i in ids if i and i not in g.nodes]
    if missing:
        known = sorted(g.nodes)[:8]
        raise ValidationError(
            f"unknown node id(s): {', '.join(missing)}. "
            f"Known ids include: {', '.join(known)}"
            + ("…" if len(g.nodes) > 8 else ""))
    return g


def slug(kind: str, label: str) -> str:
    return f"{kind}:{label}"


def parse_requires(specs) -> list:
    """`host:dc01@3` -> {"target": "host:dc01", "min_rank": 3}."""
    out = []
    for s in specs or []:
        target, _, rank = str(s).partition("@")
        if not target:
            raise ValidationError(f"malformed --requires entry: {s!r}")
        try:
            out.append({"target": target, "min_rank": int(rank) if rank else 0})
        except ValueError:
            raise ValidationError(f"non-numeric rank in {s!r}")
    return out


# --- writes -------------------------------------------------------------------

def create(name: str, force: bool = False) -> str:
    path = store.create(name, force=force)
    store.append(name, "note", {"target_id": OPERATOR_ID,
                                "text": f"engagement {name} opened"})
    return path


def add_node(name, kind, label, node_id=None, epistemic="unexplored",
             exploitation="discovered", confidence=None, source=None,
             props=None, requires=None, crown=False, status=None):
    _one_of(kind, KINDS, "kind")
    _one_of(epistemic, EPISTEMIC, "epistemic state")
    _one_of(exploitation, EXPLOITATION, "exploitation state")
    props = dict(props or {})
    if requires:
        props["requires"] = parse_requires(requires)
    if crown:
        props["crown_jewel"] = True
    if kind == "objective":
        status = _one_of(status or "open", OBJECTIVE_STATUS, "objective status")
    nid = node_id or slug(kind, label)
    store.append(name, "add_node", {
        "id": nid, "kind": kind, "label": label, "props": props,
        "epistemic": epistemic, "exploitation": exploitation,
        "confidence": confidence, "source": source, "status": status})
    return nid


def add_edge(name, src, rel, dst, edge_id=None, epistemic="hypothesized",
             confidence=None, props=None):
    _one_of(rel, RELS, "relation")
    _one_of(epistemic, EPISTEMIC, "epistemic state")
    _require_nodes(name, [src, dst])
    eid = edge_id or f"{src}--{rel}--{dst}"
    store.append(name, "add_edge", {
        "id": eid, "src": src, "dst": dst, "rel": rel,
        "epistemic": epistemic, "confidence": confidence,
        "props": dict(props or {})})
    return eid


def set_epistemic(name, target_id, state, confidence=None, source=None):
    _one_of(state, EPISTEMIC, "epistemic state")
    g = store.load(name)
    if target_id not in g.nodes and target_id not in g.edges:
        raise ValidationError(f"unknown node or edge id: {target_id}")
    store.append(name, "set_epistemic", {
        "id": target_id, "state": state, "confidence": confidence,
        "source": source})
    return target_id


def set_exploitation(name, target_id, state):
    _one_of(state, EXPLOITATION, "exploitation state")
    _require_nodes(name, [target_id])
    store.append(name, "set_exploitation", {"id": target_id, "state": state})
    return target_id


def examine(name, target_id, outcome=""):
    _require_nodes(name, [target_id])
    store.append(name, "examine", {"id": target_id, "outcome": outcome})
    return target_id


def set_objective(name, target_id, status):
    _one_of(status, OBJECTIVE_STATUS, "objective status")
    g = _require_nodes(name, [target_id])
    if g.nodes[target_id].kind != "objective":
        raise ValidationError(f"{target_id} is a {g.nodes[target_id].kind}, "
                              "not an objective")
    store.append(name, "set_objective", {"id": target_id, "status": status})
    return target_id


def note(name, target_id, text):
    _require_nodes(name, [target_id])
    store.append(name, "note", {"target_id": target_id, "text": text})


def supersede(name, old_id, new_id, reason=""):
    _require_nodes(name, [old_id, new_id])
    store.append(name, "supersede",
                 {"old_id": old_id, "new_id": new_id, "reason": reason})


def add_reference(name, target_id, store_name, label, key):
    """Point an engagement node at the curated reference layer (phase 2)."""
    _require_nodes(name, [target_id])
    ref = make_reference(store_name, label, key)
    g = store.load(name)
    refs = list(g.nodes[target_id].props.get("references") or [])
    if ref not in refs:
        refs.append(ref)
    store.append(name, "add_node", {
        "id": target_id, "kind": g.nodes[target_id].kind,
        "label": g.nodes[target_id].label, "props": {"references": refs}})
    return ref


ATTEMPT_OUTCOMES = ("failed", "succeeded")


def decide(name, chose, reason="", rejected=None, about=None):
    """Record a decision: what was chosen, what was ruled out, and why.

    The reasons stream. `why` explains a path mechanically; nothing captured why
    you PICKED it or what you ruled out, so that reasoning lived in scrollback
    and got re-litigated later.
    """
    if not chose:
        raise ValidationError("decide requires what was chosen")
    if about:
        _require_nodes(name, [about])
    store.append(name, "decide", {
        "chose": chose, "reason": reason,
        "rejected": list(rejected or []), "about": about})
    return chose


def attempt(name, target_id, outcome="failed", note=""):
    """Record an attempt against a node or edge. Feeds the failure budget."""
    _one_of(outcome, ATTEMPT_OUTCOMES, "attempt outcome")
    g = store.load(name)
    if target_id not in g.nodes and target_id not in g.edges:
        raise ValidationError(f"unknown node or edge id: {target_id}")
    store.append(name, "attempt",
                 {"id": target_id, "outcome": outcome, "note": note})
    return target_id


# --- the "since I last looked" marker ----------------------------------------

def _seen_path(name):
    import os
    return os.path.join(store.ENGAGEMENTS, f"{name}.seen")


def last_seen(name) -> int:
    import os
    p = _seen_path(name)
    if not os.path.exists(p):
        return 0
    try:
        with open(p) as fh:
            return int(fh.read().strip() or 0)
    except (ValueError, OSError):
        return 0


def stamp_seen(name, seq: int) -> None:
    import os
    os.makedirs(store.ENGAGEMENTS, exist_ok=True)
    with open(_seen_path(name), "w") as fh:
        fh.write(str(seq))


def delta(name, since: int | None = None, stamp: bool = True) -> dict:
    """What changed since `since` (default: since you last looked).

    A bare call re-stamps the marker; passing an explicit `since` does not, so
    inspecting history never disturbs where you were up to.
    """
    explicit = since is not None
    frm = since if explicit else last_seen(name)
    before = store.snapshot_at(name, frm)
    after = store.load(name)
    out = _delta(before, after)
    if stamp and not explicit:
        stamp_seen(name, after.seq)
    return out


def recall(name, node_id) -> list:
    return _recall.recall(store.load(name), node_id, exclude=name)


def suggestions(name, limit: int = 3) -> dict:
    return _recall.suggestions(store.load(name), engagement=name, limit=limit)


def apply_events(name, events: list) -> int:
    """Batch import. Validated shallowly — enough to reject a malformed file."""
    if not isinstance(events, list):
        raise ValidationError("expected a JSON list of {op, args} objects")
    for i, e in enumerate(events):
        if not isinstance(e, dict) or "op" not in e:
            raise ValidationError(f"event {i}: missing 'op'")
    return len(store.append_many(name, events))


# --- reads (pure; safe for an MCP server to expose) ---------------------------

def graph(name):
    return store.load(name)


def status(name) -> dict:
    """One call an integrator can hang a UI or an agent prompt off.

    Named `status`, not `report`: in this workflow "the report" is the graded
    engagement deliverable, and a command that prints JSON must not share its
    name.
    """
    g = store.load(name)
    return {
        "engagement": name,
        "seq": g.seq,
        "coverage": coverage(g),
        "frontier": frontier(g),
        "unrealized": unrealized(g),
        "unmined": unmined(g),
        "stale": stale(g),
        "verification_queue": verification_queue(g),
        "budget_blown": budget(g),
        "decisions": g.decisions[-5:],
    }


def explain(name, objective_id) -> dict:
    return why(store.load(name), objective_id)
