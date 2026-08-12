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

from dataclasses import asdict

from . import store
from .model import (KINDS, RELS, EPISTEMIC, EXPLOITATION, OPERATOR_ID,
                    STEP_STATUS, BLOCKED_REASONS, fold)
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


def _agent(agent=None):
    """Event authorship: explicit argument wins, else $RECKON_AGENT, else none.

    The env var is what lets a harness stamp every write from a session without
    each call site passing it, which is the only way authorship survives an agent
    that forgets.
    """
    import os
    return agent or os.environ.get("RECKON_AGENT") or None


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


# --- plans and steps ----------------------------------------------------------

class AmbiguousHandoff(ValidationError):
    """Several plans are live and none was named. Never resolved by picking one:
    an arbitrary resume point sends a successor to the wrong objective."""


def _plan(g, plan_id):
    p = g.plans.get(plan_id)
    if not p:
        known = sorted(x.id for x in g.active_plans())[:8]
        raise ValidationError(
            f"unknown plan id: {plan_id}."
            + (f" Active: {', '.join(known)}" if known else " No plans yet."))
    return p


def _resolve_step(plan, step_id) -> str:
    """Accept an ordinal or a full step id — the CLI addresses steps by ordinal
    ('step done <plan> 2') and the API by id, and resolving here keeps the CLI
    thin rather than teaching it the id format."""
    s = str(step_id)
    if s.isdigit():
        for st in plan.steps:
            if st.ordinal == int(s):
                return st.id
        raise ValidationError(
            f"{plan.id} has no step {s} — it has {len(plan.steps)}")
    if any(st.id == s for st in plan.steps):
        return s
    raise ValidationError(f"unknown step id: {s} in {plan.id}")


def plan_add(name, objective, title, steps=None, plan_id=None, supersedes=None,
             agent=None) -> str:
    """Attach an ordered plan to an objective.

    One ACTIVE plan per objective. A second is refused rather than silently
    allowed, because two live plans on one objective means two successors
    executing different procedures against the same target. Pass `supersedes` to
    replace the incumbent in the same call.
    """
    if not title or not str(title).strip():
        raise ValidationError("a plan needs a title")
    g = _require_nodes(name, [objective])
    if g.nodes[objective].kind != "objective":
        raise ValidationError(f"{objective} is a {g.nodes[objective].kind}, "
                              "not an objective")
    if supersedes:
        old = _plan(g, supersedes)
        if old.superseded_by:
            raise ValidationError(f"{supersedes} is already superseded by "
                                  f"{old.superseded_by}")
    else:
        live = [p for p in g.active_plans() if p.objective == objective]
        if live:
            raise ValidationError(
                f"{objective} already has an active plan ({live[0].id}: "
                f"{live[0].title!r}). Supersede it rather than running two — "
                "pass supersedes= / --supersedes.")

    by = _agent(agent)
    ev = store.append(name, "plan_add",
                      {"plan_id": plan_id, "objective": objective,
                       "title": title}, by=by)
    pid = plan_id or f"plan:{ev['seq']}"

    batch = []
    for i, text in enumerate(steps or [], start=1):
        batch.append({"op": "step_add", "args": {
            "plan_id": pid, "step_id": f"{pid}#{i}", "ordinal": i,
            "text": text, "command": None}})
    if supersedes:
        batch.append({"op": "plan_supersede", "args": {
            "old_plan_id": supersedes, "new_plan_id": pid,
            "reason": f"replaced by {pid}"}})
    if batch:
        store.append_many(name, batch, by=by)
    return pid


def step_add(name, plan_id, text, command=None, ordinal=None, agent=None) -> str:
    if not text or not str(text).strip():
        raise ValidationError("a step needs text")
    p = _plan(store.load(name), plan_id)
    if p.superseded_by:
        raise ValidationError(
            f"{plan_id} is superseded by {p.superseded_by}; add the step there")
    ordinal = int(ordinal) if ordinal else (
        max((s.ordinal for s in p.steps), default=0) + 1)
    sid = f"{plan_id}#{ordinal}"
    if any(s.id == sid for s in p.steps):
        raise ValidationError(f"{plan_id} already has a step {ordinal}")
    store.append(name, "step_add", {
        "plan_id": plan_id, "step_id": sid, "ordinal": ordinal,
        "text": text, "command": command}, by=_agent(agent))
    return sid


def step_state(name, plan_id, step_id, status, note="", blocked_reason=None,
               produced=None, agent=None) -> None:
    _one_of(status, STEP_STATUS, "step status")
    _one_of(blocked_reason, BLOCKED_REASONS, "blocked reason")
    # Both directions are refused. A blocked step with no cause tells a successor
    # nothing it can act on, and a cause on a step that is not blocked is a stale
    # reason waiting to misdirect one.
    if status == "blocked" and not blocked_reason:
        raise ValidationError(
            "a blocked step needs a reason — the correct successor behaviour "
            f"differs by cause. One of: {', '.join(BLOCKED_REASONS)}")
    if blocked_reason and status != "blocked":
        raise ValidationError(
            f"a blocked reason only means something with status=blocked, "
            f"not {status!r}")

    g = store.load(name)
    p = _plan(g, plan_id)
    sid = _resolve_step(p, step_id)
    for nid in produced or []:
        if nid not in g.nodes:
            raise ValidationError(
                f"step claims it produced {nid}, which is not in the graph. "
                "Record the node first: a step pointing at nothing is exactly "
                "how an output ends up stranded in a dead session.")
    store.append(name, "step_state", {
        "plan_id": plan_id, "step_id": sid, "status": status, "note": note,
        "blocked_reason": blocked_reason,
        "produced": list(produced or [])}, by=_agent(agent))


def plan_supersede(name, old_plan_id, new_plan_id, reason="", agent=None) -> None:
    if old_plan_id == new_plan_id:
        raise ValidationError("a plan cannot supersede itself")
    g = store.load(name)
    old, _new = _plan(g, old_plan_id), _plan(g, new_plan_id)
    if old.superseded_by:
        raise ValidationError(f"{old_plan_id} is already superseded by "
                              f"{old.superseded_by}")
    store.append(name, "plan_supersede", {
        "old_plan_id": old_plan_id, "new_plan_id": new_plan_id,
        "reason": reason}, by=_agent(agent))


def active_plans(name) -> list:
    return store.load(name).active_plans()


def active_plan(name, objective=None):
    """The single live plan, or the live plan for one objective.

    With several live and no objective named this raises rather than choosing:
    picking one arbitrarily is how a successor resumes the wrong work.
    """
    live = store.load(name).active_plans()
    if objective:
        live = [p for p in live if p.objective == objective]
    if not live:
        return None
    if len(live) > 1:
        raise AmbiguousHandoff(
            "several plans are active; name one: "
            + ", ".join(f"{p.id} ({p.objective})" for p in live))
    return live[0]


# --- the change ledger --------------------------------------------------------

def change(name, target, what, reversible=True, revert_hint="", agent=None) -> str:
    """Record a modification made to the TARGET.

    Two readers, one record: a successor who must not re-do it, and whoever runs
    the cleanup at close. Both lists are kept by hand in real workspaces today,
    which is exactly why they drift apart.
    """
    if not what or not str(what).strip():
        raise ValidationError("change requires what was done")
    _require_nodes(name, [target])
    ev = store.append(name, "change", {
        "target": target, "what": what, "reversible": bool(reversible),
        "revert_hint": revert_hint}, by=_agent(agent))
    return f"chg:{ev['seq']}"


def mark_cleaned(name, change_id, agent=None) -> None:
    g = store.load(name)
    if change_id not in {c.id for c in g.changes}:
        outstanding = sorted(c.id for c in g.changes if not c.cleaned)[:8]
        raise ValidationError(
            f"unknown change id: {change_id}. "
            + (f"Outstanding: {', '.join(outstanding)}" if outstanding
               else "Nothing is outstanding."))
    store.append(name, "cleaned", {"change_id": change_id}, by=_agent(agent))


def changes(name, outstanding_only=True) -> list:
    """The RoE cleanup list. Outstanding only by default — a cleaned change is
    history, and history is not a task."""
    g = store.load(name)
    return [asdict(c) for c in g.changes
            if not (outstanding_only and c.cleaned)]


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
        "changes": [asdict(c) for c in g.changes if not c.cleaned],
    }


def explain(name, objective_id) -> dict:
    return why(store.load(name), objective_id)
