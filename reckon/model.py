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
        }


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
