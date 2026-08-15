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
                    STEP_STATUS, BLOCKED_REASONS, BLOCKED_IMPLICATION, fold)
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
    """Point an engagement node at the curated reference layer.

    Only the `(store, label, key)` triple is recorded — never the resolved name
    — so a reference source can change without touching engagement data. Where
    the store can be asked, `make_reference` refuses an id it does not contain.
    """
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
                       "title": title, "agent": by}, by=by)
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


def plan_reassign(name, plan_id, to_agent, reason="") -> None:
    """Hand a plan to another agent.

    Supersede-style: the previous owner stays readable in the log, because
    "a3 died at step 3 and a1 picked it up" is engagement history. Step state
    and `produced` are preserved — the new owner inherits the cursor.

    Deliberately operator-facing and NOT exposed over MCP: an agent taking over
    another agent's plan unprompted is how two agents end up executing the same
    steps against one target.
    """
    if not to_agent:
        raise ValidationError("plan_reassign needs an agent to hand it to")
    g = store.load(name)
    p = _plan(g, plan_id)
    if p.superseded_by:
        raise ValidationError(f"{plan_id} is superseded by {p.superseded_by}; "
                              "reassign the plan that replaced it")
    store.append(name, "plan_reassign", {
        "plan_id": plan_id, "from_agent": p.agent, "to_agent": to_agent,
        "reason": reason}, by=_agent())


def active_plans(name, agent=None) -> list:
    live = store.load(name).active_plans()
    return [p for p in live if p.agent == agent] if agent else live


def active_plan(name, objective=None, agent=None):
    """The single live plan, or the live plan for one objective or one agent.

    With several live and nothing named this raises rather than choosing:
    picking one arbitrarily is how a successor resumes the wrong work.
    """
    live = store.load(name).active_plans()
    if objective:
        live = [p for p in live if p.objective == objective]
    if agent:
        live = [p for p in live if p.agent == agent]
    if not live:
        return None
    if len(live) > 1:
        raise AmbiguousHandoff(
            "several plans are active; name one with --agent or an objective: "
            + ", ".join(f"{p.id} ({p.objective}"
                        + (f", {p.agent}" if p.agent else "") + ")"
                        for p in live))
    return live[0]


# --- fleet: who has stopped without saying so ---------------------------------

def _last_authored(name) -> dict:
    """{agent: seq of its most recent authored event}. A "running" agent that
    has written nothing for a long time is visible from this even before any
    lease expires."""
    out = {}
    for ev in store.read_events(name):
        who = ev.get("by")
        if who:
            out[who] = max(out.get(who, 0), ev.get("seq", 0))
    return out


def fleet(name) -> list:
    """One row per agent, stalled first.

    The useful fleet question is not "who is working" but "who has stopped
    without saying so". That signal — STALLED — is defined against claim expiry
    from SPEC-002, which is not built: `claim` stays empty and `stalled` is
    always False until it lands. The shape is here so lighting it up later is a
    computation change, not a schema change.
    """
    g = store.load(name)
    last = _last_authored(name)
    owned = {}
    for p in g.active_plans():
        if p.agent:
            owned.setdefault(p.agent, p)

    rows = []
    for who in sorted(set(owned) | set(last)):
        p = owned.get(who)
        cur = p.cursor if p else None
        rows.append({
            "agent": who,
            "plan": p.id if p else None,
            "title": p.title if p else None,
            "cursor": (f"{cur.ordinal}/{len(p.steps)} {cur.text}"
                       if cur else None),
            "cursor_status": cur.status if cur else None,
            "status": (cur.status if cur else "idle") if p else "idle",
            "claim": None,          # SPEC-002
            "stalled": False,       # SPEC-002: claim expired AND cursor running
            "last_seq": last.get(who, 0),
        })
    rows.sort(key=lambda r: (not r["stalled"], r["status"] == "idle",
                             -r["last_seq"]))
    return rows


# --- handoff ------------------------------------------------------------------

def _explain_node(g, nid) -> dict:
    """Every id a brief prints must come with what it IS (§9.6): the reader may
    be a different model with no history of this engagement."""
    n = g.nodes.get(nid)
    if not n:
        return {"id": nid, "label": nid, "kind": "?",
                "note": "not in the graph"}
    return {"id": nid, "label": n.label, "kind": n.kind,
            "epistemic": n.epistemic, "exploitation": n.exploitation,
            "confidence": n.confidence}


def _explain_edge(g, eid) -> dict:
    e = g.edges.get(eid)
    if not e:
        return {"id": eid, "text": eid}
    src = g.nodes.get(e.src)
    dst = g.nodes.get(e.dst)
    return {"id": eid, "rel": e.rel, "confidence": e.confidence,
            "from": src.label if src else e.src,
            "to": dst.label if dst else e.dst,
            "privilege": e.props.get("privilege"),
            "text": f"{src.label if src else e.src} —{e.rel}→ "
                    f"{dst.label if dst else e.dst}"}


def _resume_block(g, p) -> dict:
    """One plan's resume point, with everything needed to act on it."""
    cur = p.cursor
    obj = g.nodes.get(p.objective)
    produced, warnings = [], []
    for s in p.steps:
        if s.status != "done":
            continue
        if s.produced:
            produced.append({
                "ordinal": s.ordinal, "text": s.text,
                "nodes": [_explain_node(g, n) for n in s.produced]})
        elif not s.note:
            # §9.8 — the output is orphaned and a successor may redo it.
            warnings.append(
                f"step {s.ordinal} ({s.text!r}) is done with nothing recorded "
                "as produced and no note — its output may be stranded")
    if obj is not None and obj.status == "achieved":
        # §8 — a plan against an objective already won is not a resume point.
        warnings.append(
            f"{p.objective} is already achieved; this plan is stale — go to the "
            "frontier rather than resuming it")

    return {
        "plan": p.id, "title": p.title, "agent": p.agent,
        "objective": p.objective,
        "objective_label": obj.label if obj else p.objective,
        "objective_status": obj.status if obj else None,
        "crown": bool(obj.props.get("crown_jewel")) if obj else False,
        "total_steps": len(p.steps),
        "cursor": ({"ordinal": cur.ordinal, "text": cur.text,
                    "status": cur.status, "command": cur.command,
                    "note": cur.note,
                    "blocked_reason": cur.blocked_reason,
                    "implication": BLOCKED_IMPLICATION.get(cur.blocked_reason)}
                   if cur else None),
        "produced": produced,
        "resting_on": [_explain_edge(g, e)
                       for e in why(g, p.objective).get("assumptions", [])],
        "warnings": warnings,
        "stalled": False,           # SPEC-002
    }


def handoff(name, agent=None, all_agents=False) -> dict:
    """The successor brief, as data. `render.handoff` turns it into markdown.

    Resume-point first, position second: a successor reading top-down can act
    without reading the rest. Everything below the resume point is for when the
    plan needs re-judging rather than continuing.
    """
    g = store.load(name)
    live = g.active_plans()
    if agent:
        plans = [p for p in live if p.agent == agent]
    elif all_agents:
        plans = live
    elif len(live) > 1:
        raise AmbiguousHandoff(
            "several plans are active; pass an agent or --all rather than "
            "resuming an arbitrary one: "
            + ", ".join(f"{p.id} ({p.objective}"
                        + (f", {p.agent}" if p.agent else "") + ")"
                        for p in live))
    else:
        plans = live

    # Stalled first (§7.1). Dark until SPEC-002, but the ordering is here.
    resume = sorted((_resume_block(g, p) for p in plans),
                    key=lambda r: not r["stalled"])

    return {
        "engagement": name,
        "seq": g.seq,
        "agent": agent,
        "resume": resume,
        "coverage": coverage(g),
        "owned": [_explain_node(g, nid)
                  for nid, info in reach(g).items()
                  if info["cost"] == 0
                  and (g.nodes.get(nid) and g.nodes[nid].kind != "operator"
                       and not g.nodes[nid].superseded_by)],
        "alarms": {"unrealized": unrealized(g), "unmined": unmined(g),
                   "stale": stale(g), "budget_blown": budget(g)},
        "next_moves": {"unrealized": unrealized(g),
                       "queue": verification_queue(g),
                       "frontier": frontier(g)},
        "ruled_out": g.decisions,
        "changes": [asdict(c) for c in g.changes if not c.cleaned],
        "claims": [],               # SPEC-002
    }


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


# --- the tool-call trace (SPEC-004 §5.2, consumed per §5.4) -------------------

def trace(name, since: int | None = None, limit: int | None = 100) -> list:
    """The raw tool-call trail the harness appends, oldest first.

    **Evidence, not interpretation** (§2). These lines are written by a shell
    one-liner on every tool call and never fold into the graph; turning a
    command into a finding needs judgment and stays with the agent. What they
    are for is A3 — the one alarm that compares the log against an independent
    signal of activity, and therefore the only one that can tell "nothing
    happened" from "nothing was recorded".

    `since` is a position cursor: each entry's `seq` is its line number, so
    `since=40` reads "what happened after the fortieth call". `limit` takes the
    most recent N; 0 or None reads the whole trail.

    Deliberately absent from MCP. An agent re-reading its own command history is
    a context-cost trap with almost no interpretive value, and A3 already
    surfaces the only fact about it that changes a decision.
    """
    rows = store.read_trace(name)
    if since is not None:
        rows = [r for r in rows if r["seq"] > int(since)]
    if limit and int(limit) > 0:
        rows = rows[-int(limit):]
    return rows


# The §5.4 pattern set: commands that write a file or change state on the
# target. Each pairs a match with the plainest description of what it implies,
# because the proposal has to be readable as a ledger entry before anyone can
# judge whether to confirm it.
CHANGE_PATTERNS = (
    ("sed -i",   r"\bsed\b[^|;&]*?\s-i\b",     "edited a file in place"),
    ("tee",      r"\btee\b",                   "wrote to a file via tee"),
    ("cp",       r"\bcp\b\s+\S",               "copied a file onto the target"),
    ("mv",       r"\bmv\b\s+\S",               "moved or renamed a file"),
    ("useradd",  r"\b(?:useradd|adduser)\b",   "created a local account"),
    ("net user", r"\bnet\s+user\b",            "created or changed an account"),
    ("reg add",  r"\breg\s+add\b",             "wrote a registry key"),
    ("msiexec",  r"\bmsiexec\b",               "installed a package"),
    ("schtasks", r"\bschtasks\b",              "created or changed a scheduled task"),
    # Last, and narrowest: a redirect to somewhere that is not /dev/null, and
    # not an fd-qualified one (`2>`, `2>&1`). Nearly every command in a real
    # trace carries a diagnostic redirect, and a proposal list that is mostly
    # noise is a list nobody reads. The cost is a missed `2>/tmp/loot`, which
    # is the rarer case by a wide margin.
    (">",        r"(?<![0-9])>>?\s*(?!/dev/null\b)(?:'[^']+'|\"[^\"]+\"|[^\s|;&<>]+)",
     "wrote to a file"),
)


def suggest_changes(name) -> list:
    """§5.4 — propose change-ledger entries from the trace.

    **Proposals, never assertions.** Nothing is written; each entry carries the
    command it came from and the `reckon change` line that would record it, and
    a human or an agent decides. That is the same shape `ingest` and `recall`
    already have, and it is required here rather than merely polite: a pattern
    match is evidence about *relevance*, not about what actually changed on the
    target. `cp a b` in a command that failed halfway may have changed
    everything or nothing, and only the operator knows which.

    The `target` is left empty on purpose. A shell command names paths and
    hosts, not graph nodes, and guessing which node a write landed on is the
    interpretation step §2 keeps with the agent.

    A command already quoted verbatim in an existing ledger entry is dropped —
    an exact substring check and nothing cleverer, so the list shrinks as you
    confirm from it rather than repeating what you have already recorded.
    """
    import re
    g = store.load(name)
    recorded = " \n".join(f"{c.what} {c.revert_hint}" for c in g.changes)

    seen, out = {}, []
    for entry in store.read_trace(name):
        cmd = str(entry.get("cmd") or "").strip()
        if not cmd:
            continue
        if cmd in seen:                     # the same command run twice is one
            seen[cmd]["count"] += 1         # entry to confirm, not two
            continue
        for label, pattern, what in CHANGE_PATTERNS:
            if not re.search(pattern, cmd):
                continue
            if cmd in recorded:
                break                       # already in the ledger, verbatim
            proposal = {
                "pattern": label, "what": what, "cmd": cmd,
                "ts": entry.get("ts"), "seq": entry.get("seq"),
                "agent": entry.get("agent") or "", "cwd": entry.get("cwd") or "",
                "count": 1, "target": "",
                "confirm": f'reckon change <target> "{what}" '
                           f'--revert "<how>"',
            }
            seen[cmd] = proposal
            out.append(proposal)
            break                           # one proposal per command
    return out


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


def _checkpoint_path(name):
    import os
    return os.path.join(store.ENGAGEMENTS, f"{name}.checkpoint")


def last_checkpoint(name) -> int:
    """Deliberately a SEPARATE marker from `.seen`.

    `.seen` is "last time I looked" and moves whenever anyone reads `delta`;
    this is "last checkpoint". Reading the board must not silently satisfy the
    checkpoint interval, and a checkpoint must not reset every agent's personal
    read position.
    """
    import os
    p = _checkpoint_path(name)
    if not os.path.exists(p):
        return 0
    try:
        with open(p) as fh:
            return int(fh.read().strip() or 0)
    except (ValueError, OSError):
        return 0


def stamp_checkpoint(name, seq: int) -> None:
    import os
    os.makedirs(store.ENGAGEMENTS, exist_ok=True)
    with open(_checkpoint_path(name), "w") as fh:
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


# --- the checkpoint alarm set (SPEC-004 §4) -----------------------------------

# Recording-health alarms describe the INSTRUMENT; engagement-health alarms
# describe the engagement. `--strict` gates on the first group only, because a
# blown failure budget is a fact about the work, not a reason to fail a script.
RECORDING, ENGAGEMENT = "recording", "engagement"

STALE_RECORDING_MINUTES = 30

# Every alarm in §4, including the ones that cannot fire yet. Listing the dark
# ones is the point: a silently absent alarm is indistinguishable from one that
# never fires, and this file is where someone will come looking.
ALARM_REGISTRY = (
    ("A1", "stale-recording", RECORDING, True,
     "the graph is behind the work"),
    ("A2", "empty-delta", RECORDING, True,
     "nothing happened, OR nothing was recorded — A3 is what tells them apart"),
    ("A3", "unrecorded-work", RECORDING, True,
     "work provably happened and none of it was interpreted"),
    ("A4", "done-without-produced", ENGAGEMENT, False,
     "computed by handoff already; not wired into the alarm set yet"),
    ("A5", "no-decision", ENGAGEMENT, True,
     "a decision point passed unrecorded"),
    ("A6", "stalled-agent", ENGAGEMENT, False,
     "needs claim expiry from SPEC-002, which is not built"),
    ("A7", "uncleaned-changes", ENGAGEMENT, True,
     "RoE debt — informational, never blocking"),
)

DARK_ALARMS = tuple(a for a in ALARM_REGISTRY if not a[3])


def _parse_ts(value):
    """A timestamp from either file, or None if it cannot be read.

    The log writes `+00:00` and the trace's `jq` writes `Z`; both are the same
    instant and both have to compare. None rather than an exception because one
    caller is a tolerant reader over evidence written by a shell one-liner.
    """
    from datetime import datetime, timezone
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _last_authored_time(name):
    """When the log last had something written to it, or None if never.

    "Authored" means an event in the log. The checkpoint marker is a sidecar
    file and not an event, so the Stop hook stamping a checkpoint does not reset
    this clock — which is what keeps A3 honest at the end of a session.
    """
    for ev in reversed(store.read_events(name)):
        when = _parse_ts(ev.get("ts"))
        if when is not None:
            return when
    return None


def _minutes_since(when):
    from datetime import datetime, timezone
    return (datetime.now(timezone.utc) - when).total_seconds() / 60.0


def _unrecorded_calls(name, cutoff):
    """Trace entries newer than `cutoff`, the last authored event (§5.2 → A3).

    A `cutoff` of None means nothing has been recorded at all, so every call is
    unrecorded — the purest case of what A3 is for.

    An entry whose timestamp will not parse is skipped rather than counted:
    A3's whole claim is that work provably happened, and a line we cannot place
    in time proves nothing. Under-reporting is the right failure here —
    reporting on garbage is how an alarm loses its reader.
    """
    out = []
    for entry in store.read_trace(name):
        when = _parse_ts(entry.get("ts"))
        if when is None:
            continue
        if cutoff is None or when > cutoff:
            out.append((when, entry))
    return out


def alarms(name, since: int | None = None) -> list:
    """The §4 set that is currently firing, each with severity and why.

    Deterministic and computed from the log: none of these depends on an agent
    remembering to notice anything, which is the whole point — a checkpoint that
    relies on an agent recording is one that will eventually render a confident,
    hours-old picture.
    """
    g = store.load(name)
    frm = last_checkpoint(name) if since is None else since
    out = []

    # Read once and share: A1 and A3 both measure against the same instant, and
    # the log is the file this whole module is careful not to re-scan.
    last_authored = _last_authored_time(name)
    age = None if last_authored is None else _minutes_since(last_authored)
    if age is not None and age > STALE_RECORDING_MINUTES:
        out.append({
            "id": "A1", "name": "stale-recording", "group": RECORDING,
            "severity": "warn",
            "why": f"{int(age)}m since the last recorded event — the graph is "
                   "behind the work",
            "detail": {"minutes": int(age),
                       "threshold": STALE_RECORDING_MINUTES}})

    if g.seq <= frm:
        out.append({
            "id": "A2", "name": "empty-delta", "group": RECORDING,
            "severity": "warn",
            "why": "no events since the last checkpoint — either nothing "
                   "happened or nothing was recorded, and this alarm cannot "
                   "tell you which (that is A3's job)",
            "detail": {"seq": g.seq, "since": frm}})

    # A3 is the one that matters (§4.1). It is the only alarm comparing the log
    # against an INDEPENDENT signal of activity, which is what makes "quiet" and
    # "unrecorded" — identical to A2 — tell apart. No trace file, or an empty
    # one, means no evidence either way, and it stays silent rather than
    # guessing from a weaker signal.
    unrecorded = _unrecorded_calls(name, last_authored)
    if unrecorded:
        # The age is how long the graph has been behind the work: since the last
        # recorded event, or — when there has never been one — since the first
        # call nobody recorded.
        oldest = min(when for when, _e in unrecorded)
        behind = int(_minutes_since(last_authored if last_authored is not None
                                    else oldest))
        out.append({
            "id": "A3", "name": "unrecorded-work", "group": RECORDING,
            "severity": "warn",
            "why": f"{len(unrecorded)} tool call(s) since the last recorded "
                   f"event ({behind}m) — work provably happened and none of it "
                   "was interpreted; interpret before deciding",
            "detail": {"tool_calls": len(unrecorded), "minutes": behind,
                       "since": (last_authored.isoformat()
                                 if last_authored else None),
                       "last_call": max(when for when, _e in unrecorded)
                                    .isoformat()}})

    if not [d for d in g.decisions if d.get("seq", 0) > frm]:
        out.append({
            "id": "A5", "name": "no-decision", "group": ENGAGEMENT,
            "severity": "info",
            "why": "no decision recorded since the last checkpoint — a "
                   "decision point probably passed unrecorded",
            "detail": {"since": frm}})

    outstanding = [asdict(c) for c in g.changes if not c.cleaned]
    if outstanding:
        out.append({
            "id": "A7", "name": "uncleaned-changes", "group": ENGAGEMENT,
            "severity": "info",
            "why": f"{len(outstanding)} outstanding change(s) on the target — "
                   "RoE debt, owed at close",
            "detail": {"changes": outstanding}})

    return out


# --- rendering ----------------------------------------------------------------
#
# One renderer, so the three surfaces that regenerate documents — `checkpoint`,
# the `console`/`views` commands, and anything else that asks for a refresh —
# cannot drift into producing different files from the same graph. The two
# halves are separate functions only because the CLI can aim each at its own
# destination; the text itself is produced in exactly one place per artifact.

def render_console(g, name, path) -> str:
    """Write the HTML console for `g` to `path`. Returns the path written."""
    import os
    from .render.html import console as html_console
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(html_console(g, name))
    return path


def render_views(g, name, out_dir) -> list:
    """Write the six markdown views for `g` into `out_dir`. Returns the paths."""
    import os
    from .render.views import render_all
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for view, text in render_all(g, name).items():
        path = os.path.join(out_dir, f"{view}.md")
        with open(path, "w") as fh:
            fh.write(text)
        written.append(path)
    return written


def render_board(name, g=None, out_dir=None) -> list:
    """Regenerate the whole board — console plus the six views — into `out_dir`.

    Defaults to `store.OUT`, which is where ADR-002 says rendered artifacts
    live; the CLI's per-call `--out` is the only thing that moves them.
    """
    import os
    from .render.views import VIEWS
    g = store.load(name) if g is None else g
    out_dir = store.OUT if out_dir is None else out_dir
    html = render_console(g, name, os.path.join(out_dir, f"{name}.html"))
    render_views(g, name, os.path.join(out_dir, name))
    # Ordered by VIEWS rather than by what the write loop returned, so the
    # reported list is stable whatever order `render_all` yields.
    return [html] + [os.path.join(out_dir, name, f"{v}.md") for v in VIEWS]


def autorender(name) -> list:
    """Regenerate the board after a write, if `$RECKON_AUTORENDER` is on.

    **Never raises**, and that is the whole contract. Everywhere else in reckon
    a failure is loud, for the reason `api`'s docstring gives; here it must not
    be, and the reason is the same one `hooks` inverts the rule for. This runs
    as a consequence of a write the caller already made and the log already
    holds. If rendering throws, the fact is recorded either way — reporting the
    write as failed would be a lie about the log, and the caller might then
    record it twice. A broken renderer costs a stale board, never a fact.

    Returns the paths written, or `[]` when the flag is off or a render failed.
    """
    if not store.autorender_enabled():
        return []
    try:
        return render_board(name)
    except Exception as exc:
        import sys
        # stderr, never stdout: on the CLI stdout is what a caller parses, and
        # on the MCP server it is the JSON-RPC transport itself.
        print(f"reckon: autorender failed ({type(exc).__name__}: {exc}); "
              f"the write is recorded, the board is stale", file=sys.stderr)
        return []


def checkpoint(name, render=True, strict=False, dry_run=False,
               agent=None) -> dict:
    """The ritual, as one command with no judgment in it.

    delta → alarms → regenerate → stamp → brief. `strict` reports whether a
    RECORDING-health alarm fired so a caller can gate on it; it never raises,
    because a checkpoint that refuses to tell you where you are is worse than
    one that tells you it is behind.
    """
    frm = last_checkpoint(name)
    g = store.load(name)
    # An explicit `since` and stamp=False: reading the checkpoint delta must not
    # disturb any agent's personal `.seen` position (§3).
    changed = delta(name, since=frm, stamp=False)
    fired = alarms(name, since=frm)

    rendered = render_board(name, g=g) if render else []

    if not dry_run:
        stamp_checkpoint(name, g.seq)

    return {
        "engagement": name,
        "seq": g.seq,
        "since": frm,
        "events": g.seq - frm,
        "alarms": fired,
        "recording_health": [a for a in fired if a["group"] == RECORDING],
        "strict_fail": bool(strict and
                            any(a["group"] == RECORDING for a in fired)),
        "delta": changed,
        "next_moves": {"unrealized": unrealized(g),
                       "queue": verification_queue(g)},
        "coverage": coverage(g),
        "rendered": rendered,
        "stamped": not dry_run,
        "dark": [{"id": i, "name": n, "why": w}
                 for i, n, _grp, _live, w in DARK_ALARMS],
    }


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
