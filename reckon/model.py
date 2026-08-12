"""Core data model: nodes, edges, and the event fold that builds a Graph.

Two orthogonal state axes, because engagement failures come in two flavours and
conflating them is what makes a raw markdown workspace lose things:

  epistemic     unexplored -> hypothesized -> verified -> refuted
                "is this true?"      (a host whose identity was never confirmed)

  exploitation  discovered -> acquired -> examined -> exhausted
                "have I used what I hold?"   (a cred cloned but never tried)

Both axes live on nodes AND edges. A doc that says "we have analyst" cannot
distinguish *have* from *tried*; these two fields can.
"""

from dataclasses import dataclass, field, asdict

# --- vocabularies -------------------------------------------------------------

EPISTEMIC = ("unexplored", "hypothesized", "verified", "refuted")
EXPLOITATION = ("discovered", "acquired", "examined", "exhausted")

KINDS = (
    "host", "cred", "service", "artifact", "finding",
    "assumption", "objective", "technique", "operator",
)

RELS = (
    "holds", "grants-access-to", "escalates-to", "depends-on", "evidenced-by",
    "tested-against", "achieves", "applies-technique", "contains", "supersedes",
)

# Only these relations move the operator through the graph. `depends-on`,
# `evidenced-by` and friends are analytical links, not access.
ACCESS_RELS = ("holds", "grants-access-to", "escalates-to", "contains")

OPERATOR_ID = "operator:me"


@dataclass
class Node:
    id: str
    kind: str
    label: str
    epistemic: str = "unexplored"
    exploitation: str = "discovered"
    confidence: str | None = None       # A-F: SOURCE RELIABILITY, never probability
    source: str | None = None
    props: dict = field(default_factory=dict)
    status: str | None = None           # objectives: open | achieved | blocked
    first_seen: int = 0
    last_verified: int | None = None
    acquired_at: int | None = None
    examined_at: int | None = None
    superseded_by: str | None = None
    notes: list = field(default_factory=list)
    # Attempts against this target. The 2-strike failure budget is a rule you
    # otherwise have to remember mid-engagement, which is exactly when you don't.
    attempts: list = field(default_factory=list)

    @property
    def failed_attempts(self) -> int:
        return sum(1 for a in self.attempts if a.get("outcome") == "failed")

    @property
    def succeeded(self) -> bool:
        return any(a.get("outcome") == "succeeded" for a in self.attempts)

    @property
    def held(self) -> bool:
        return self.exploitation in ("acquired", "examined", "exhausted")


@dataclass
class Edge:
    id: str
    src: str
    dst: str
    rel: str
    epistemic: str = "hypothesized"
    confidence: str | None = None
    props: dict = field(default_factory=dict)   # {"privilege": "root", "rank": 3}
    first_seen: int = 0
    attempts: list = field(default_factory=list)

    @property
    def failed_attempts(self) -> int:
        return sum(1 for a in self.attempts if a.get("outcome") == "failed")

    @property
    def succeeded(self) -> bool:
        return any(a.get("outcome") == "succeeded" for a in self.attempts)

    @property
    def rank(self) -> int:
        return int(self.props.get("rank", 0))


STEP_STATUS = ("pending", "running", "done", "blocked", "skipped")

# Why an enum and not free text: the correct successor behaviour differs by
# cause, so the reason has to be machine-readable to be acted on. `refusal` is
# the row that pays for the field — a fresh session of the same model will
# refuse again, and without this the successor burns a turn rediscovering that.
# The value is the IMPLICATION, which is what a brief prints; the bare enum
# tells a reader nothing they can act on.
BLOCKED_IMPLICATION = {
    "context-exhausted": "resume as written — nothing is wrong with the step",
    "refusal": "do NOT retry as written; a fresh session of the same model will "
               "refuse again — reframe, or route to the other model",
    "timeout": "check target state first — it may have partially landed",
    "target-state": "re-verify target identity before continuing",
    "dependency": "back to the frontier; the plan itself may be wrong",
    "operator": "read the note; do not assume",
}
BLOCKED_REASONS = tuple(BLOCKED_IMPLICATION)


@dataclass
class Step:
    id: str
    ordinal: int
    text: str
    command: str | None = None
    status: str = "pending"
    blocked_reason: str | None = None
    # ★ The load-bearing field. A step records the graph nodes it created, so a
    # successor can see that step 2's output is already in the graph rather than
    # repeating it. Without this a step is cosmetic: it says work happened but
    # leaves the result stranded wherever the dead session put it.
    produced: list = field(default_factory=list)
    note: str = ""
    by: str | None = None
    seq: int = 0


@dataclass
class Plan:
    """An ordered list of steps against one objective, with a cursor.

    Deliberately not a task tracker: no dependencies, no assignees, no dates, no
    estimates. The whole model is an ordered list and a position in it, because
    the failure being solved is the procedure dying with the session, not the
    absence of project management.
    """
    id: str
    objective: str
    title: str
    steps: list = field(default_factory=list)
    superseded_by: str | None = None
    seq: int = 0

    @property
    def cursor(self):
        """Where to resume: the first running or blocked step, else the first
        pending one. A running step is the cursor rather than the next pending
        one because a session that died mid-step leaves it running, and that is
        exactly the step a successor must re-verify."""
        for s in self.steps:
            if s.status in ("running", "blocked"):
                return s
        for s in self.steps:
            if s.status == "pending":
                return s
        return None

    @property
    def active(self) -> bool:
        return not self.superseded_by


@dataclass
class Change:
    """A modification we made to the TARGET, not a thing the target has.

    Not a node, for the same reason a decision is not: nothing routes through it,
    so putting it in the access graph would pollute reachability. It answers two
    questions at once - what a successor must not re-do, and what is owed at close
    under the rules of engagement.
    """
    id: str
    target: str
    what: str
    reversible: bool = True
    revert_hint: str = ""
    cleaned: bool = False
    by: str | None = None
    seq: int = 0


@dataclass
class Graph:
    nodes: dict = field(default_factory=dict)
    edges: dict = field(default_factory=dict)
    seq: int = 0
    events: list = field(default_factory=list)
    # A decision is about the ENGAGEMENT, not a thing inside it, so it is not a
    # node - putting it in the access graph would pollute reachability with
    # something nothing can route through.
    decisions: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    plans: dict = field(default_factory=dict)

    # -- accessors -------------------------------------------------------------
    def out_edges(self, node_id: str) -> list:
        return [e for e in self.edges.values() if e.src == node_id]

    def in_edges(self, node_id: str) -> list:
        return [e for e in self.edges.values() if e.dst == node_id]

    def by_kind(self, kind: str) -> list:
        return [n for n in self.nodes.values()
                if n.kind == kind and not n.superseded_by]

    def objectives(self) -> list:
        return self.by_kind("objective")

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "nodes": {k: asdict(v) for k, v in self.nodes.items()},
            "edges": {k: asdict(v) for k, v in self.edges.items()},
            "decisions": self.decisions,
            "changes": [asdict(c) for c in self.changes],
            "plans": {k: asdict(v) for k, v in self.plans.items()},
        }

    def active_plans(self) -> list:
        return sorted((p for p in self.plans.values() if p.active),
                      key=lambda p: p.seq)


# --- fold ---------------------------------------------------------------------

def _ensure_operator(g: Graph) -> None:
    if OPERATOR_ID not in g.nodes:
        g.nodes[OPERATOR_ID] = Node(
            id=OPERATOR_ID, kind="operator", label="me",
            epistemic="verified", exploitation="exhausted",
        )


def _apply(g: Graph, ev: dict) -> None:
    op, a, seq = ev["op"], ev.get("args", {}), ev.get("seq", 0)

    if op == "add_node":
        nid = a["id"]
        if nid in g.nodes:                      # idempotent: merge, never clobber
            n = g.nodes[nid]
            n.props.update(a.get("props") or {})
            if a.get("label"):
                n.label = a["label"]
        else:
            g.nodes[nid] = Node(
                id=nid, kind=a["kind"], label=a.get("label", nid),
                props=dict(a.get("props") or {}), first_seen=seq,
                epistemic=a.get("epistemic", "unexplored"),
                exploitation=a.get("exploitation", "discovered"),
                confidence=a.get("confidence"), source=a.get("source"),
                status=a.get("status"),
            )

    elif op == "add_edge":
        eid = a["id"]
        if eid in g.edges:
            g.edges[eid].props.update(a.get("props") or {})
        else:
            g.edges[eid] = Edge(
                id=eid, src=a["src"], dst=a["dst"], rel=a["rel"],
                epistemic=a.get("epistemic", "hypothesized"),
                confidence=a.get("confidence"),
                props=dict(a.get("props") or {}), first_seen=seq,
            )

    elif op == "set_epistemic":
        tgt = g.nodes.get(a["id"]) or g.edges.get(a["id"])
        if tgt:
            tgt.epistemic = a["state"]
            if a.get("confidence"):
                tgt.confidence = a["confidence"]
            if a.get("source") and hasattr(tgt, "source"):
                tgt.source = a["source"]
            if a["state"] == "verified":
                tgt.last_verified = seq if hasattr(tgt, "last_verified") else None

    elif op == "set_exploitation":
        n = g.nodes.get(a["id"])
        if n:
            n.exploitation = a["state"]
            if a["state"] == "acquired" and n.acquired_at is None:
                n.acquired_at = seq
            if a["state"] in ("examined", "exhausted") and n.examined_at is None:
                n.examined_at = seq

    elif op == "examine":
        n = g.nodes.get(a["id"])
        if n:
            if n.exploitation in ("discovered", "acquired"):
                n.exploitation = "examined"
            if n.examined_at is None:
                n.examined_at = seq
            if a.get("outcome"):
                n.notes.append(f"[examined@{seq}] {a['outcome']}")

    elif op == "set_objective":
        n = g.nodes.get(a["id"])
        if n:
            n.status = a["status"]

    elif op == "supersede":
        old = g.nodes.get(a["old_id"])
        if old:
            old.superseded_by = a["new_id"]
            old.notes.append(f"[superseded@{seq}] {a.get('reason', '')}")

    elif op == "decide":
        g.decisions.append({
            "seq": seq, "ts": ev.get("ts"), "chose": a.get("chose", ""),
            "rejected": list(a.get("rejected") or []),
            "reason": a.get("reason", ""), "about": a.get("about"),
        })

    elif op == "attempt":
        tgt = g.nodes.get(a.get("id")) or g.edges.get(a.get("id"))
        rec = {"seq": seq, "outcome": a.get("outcome", "failed"),
               "note": a.get("note", "")}
        if tgt is not None:
            if not hasattr(tgt, "attempts") or tgt.attempts is None:
                tgt.attempts = []
            tgt.attempts.append(rec)

    elif op == "plan_add":
        pid = a.get("plan_id") or f"plan:{seq}"
        if pid not in g.plans:
            g.plans[pid] = Plan(id=pid, objective=a.get("objective", ""),
                                title=a.get("title", ""), seq=seq)

    elif op == "step_add":
        p = g.plans.get(a.get("plan_id"))
        if p:
            ordinal = int(a.get("ordinal") or len(p.steps) + 1)
            sid = a.get("step_id") or f"{p.id}#{ordinal}"
            if not any(s.id == sid for s in p.steps):
                p.steps.append(Step(id=sid, ordinal=ordinal,
                                    text=a.get("text", ""),
                                    command=a.get("command"),
                                    by=ev.get("by"), seq=seq))
                p.steps.sort(key=lambda s: s.ordinal)

    elif op == "step_state":
        p = g.plans.get(a.get("plan_id"))
        for s in (p.steps if p else []):
            if s.id != a.get("step_id"):
                continue
            s.status = a.get("status", s.status)
            # Cleared unless restated: a step that moved off `blocked` no longer
            # has a blockage, and a stale reason would misdirect a successor.
            s.blocked_reason = a.get("blocked_reason")
            if a.get("note"):
                s.note = a["note"]
            if a.get("produced"):
                s.produced = list(dict.fromkeys(
                    list(s.produced) + list(a["produced"])))
            s.by = ev.get("by") or s.by
            s.seq = seq

    elif op == "plan_supersede":
        old = g.plans.get(a.get("old_plan_id"))
        if old:
            old.superseded_by = a.get("new_plan_id")

    elif op == "change":
        # The id is derived from seq rather than carried in the event: seq is
        # assigned under the store lock, so an id minted before the write would
        # race between the CLI and the MCP server.
        g.changes.append(Change(
            id=f"chg:{seq}", target=a.get("target", ""), what=a.get("what", ""),
            reversible=bool(a.get("reversible", True)),
            revert_hint=a.get("revert_hint", ""),
            by=ev.get("by"), seq=seq))

    elif op == "cleaned":
        for c in g.changes:
            if c.id == a.get("change_id"):
                c.cleaned = True

    elif op == "note":
        n = g.nodes.get(a.get("target_id"))
        if n:
            n.notes.append(f"[{seq}] {a['text']}")


def fold(events: list) -> Graph:
    """Deterministically rebuild state from an event log. Pure."""
    g = Graph()
    _ensure_operator(g)
    for ev in events:
        _apply(g, ev)
        g.seq = max(g.seq, ev.get("seq", 0))
        g.events.append(ev)
    return g
