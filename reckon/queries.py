"""The value proposition. Pure functions over a Graph — no IO, no printing.

Kept side-effect free so an MCP server is a thin wrapper rather than a refactor.

One algorithm underpins the whole board: Dijkstra where an edge costs 0 if it is
verified and 1 if it is merely hypothesized. Then

    dist == 0   reachable NOW      (every edge on the path is verified)
    dist >= 1   reachable IF       (the hypothesized edges ARE the assumptions)
    no path     unreachable        (needs discovery, not verification)

The hypothesized edges on a winning path are exactly the verification queue,
and ordering them by how many objectives they gate ranks the cheapest next test.
"""

import heapq

from .model import ACCESS_RELS, OPERATOR_ID

INF = float("inf")


# --- traversal ----------------------------------------------------------------

def _edge_cost(edge) -> float:
    if edge.epistemic == "refuted":
        return INF
    return 0 if edge.epistemic == "verified" else 1


def reach_pareto(g, source: str = OPERATOR_ID) -> dict:
    """Dijkstra keeping every NON-DOMINATED (cost, rank) way to reach each node.

    One entry per node is not enough. A host is routinely reachable at
    (cost 0, rank 1) over the network *and* at (cost 1, rank 3) with a credential
    nobody has tested — and an objective needing rank 3 is `reachable-if`, not
    unreachable. Keeping only the cheapest entry discarded the privileged path and
    reported such objectives as needing fresh discovery, which is the opposite of
    the truth: they need one test.

    A pair (c1, r1) dominates (c2, r2) when c1 <= c2 and r1 >= r2 — cheaper and no
    less privileged. Only non-dominated pairs are kept, so the set stays small.

    Returns {node_id: [ {"cost", "rank", "path", "assumptions"}, ... ]}.
    """
    best = {}

    def dominated(nid, cost, rank):
        return any(e["cost"] <= cost and e["rank"] >= rank
                   for e in best.get(nid, ()))

    pq = [(0, 0, source, [])]
    while pq:
        cost, negrank, nid, path = heapq.heappop(pq)
        rank = -negrank
        if dominated(nid, cost, rank):
            continue
        entries = [e for e in best.get(nid, [])
                   if not (cost <= e["cost"] and rank >= e["rank"])]
        entries.append({"cost": cost, "rank": rank, "path": path,
                        "assumptions": [eid for eid in path
                                        if g.edges[eid].epistemic == "hypothesized"]})
        best[nid] = entries
        for e in g.out_edges(nid):
            if e.rel not in ACCESS_RELS:
                continue
            c = _edge_cost(e)
            if c == INF:
                continue
            # A `contains` edge inherits the privilege you already hold on the
            # parent; an access-granting edge sets it explicitly.
            nrank = rank if e.rel == "contains" and "rank" not in e.props else e.rank
            heapq.heappush(pq, (cost + c, -nrank, e.dst, path + [e.id]))
    return best


def reach(g, source: str = OPERATOR_ID) -> dict:
    """The single best way to reach each node: cheapest first, then most privileged.

    This is the DISPLAY view — what you have right now. Requirement checking uses
    `reach_pareto`, because "could I get admin there if one assumption held" is a
    different question from "what do I have there today".
    """
    return {nid: min(entries, key=lambda e: (e["cost"], -e["rank"]))
            for nid, entries in reach_pareto(g, source).items()}


def _requirements_met(g, obj, reach_map: dict, max_cost: int = 0):
    """Is every `requires` entry satisfied at <= max_cost? Returns (bool, unmet).

    `reach_map` may be either shape: the pareto sets from `reach_pareto`, or the
    single-best map from `reach`. Requirement checks want the pareto set, since a
    privileged-but-conditional path is exactly what makes an objective
    reachable-if rather than unreachable.
    """
    reqs = obj.props.get("requires") or []
    if not reqs:
        return False, ["<no requires declared>"]
    unmet = []
    for r in reqs:
        tgt, need = r.get("target"), int(r.get("min_rank", 0))
        got = reach_map.get(tgt)
        entries = got if isinstance(got, list) else ([got] if got else [])
        if not any(e["cost"] <= max_cost and e["rank"] >= need for e in entries):
            unmet.append(f"{tgt}@rank>={need}")
    return (not unmet), unmet


# --- the queries --------------------------------------------------------------

def frontier(g) -> dict:
    """Objectives partitioned by how far they are from what is already verified.

    `undeclared` is kept separate from `unreachable` on purpose. "I cannot get
    there" and "nobody has said what getting there requires" are different
    problems: the first needs an exploit, the second needs one line of input.
    Merging them let a freshly imported workspace look hopeless when it was
    merely unannotated.
    """
    r = reach_pareto(g)
    now, cond, un, undecl = [], [], [], []
    for obj in g.objectives():
        if obj.status == "achieved":
            continue
        if not (obj.props.get("requires") or []):
            undecl.append({"id": obj.id, "label": obj.label,
                           "crown_jewel": bool(obj.props.get("crown_jewel"))})
            continue
        ok, unmet = _requirements_met(g, obj, r, max_cost=0)
        if ok:
            now.append({"id": obj.id, "label": obj.label,
                        "crown_jewel": bool(obj.props.get("crown_jewel"))})
            continue
        ok_soft, unmet_soft = _requirements_met(g, obj, r, max_cost=99)
        if ok_soft:
            assumptions = []
            for req in obj.props.get("requires") or []:
                entries = r.get(req.get("target")) or []
                need = int(req.get("min_rank", 0))
                viable = [e for e in entries if e["rank"] >= need]
                if viable:
                    assumptions += min(viable, key=lambda e: e["cost"])["assumptions"]
            cond.append({
                "id": obj.id, "label": obj.label,
                "crown_jewel": bool(obj.props.get("crown_jewel")),
                "assumptions": sorted(set(assumptions)),
            })
        else:
            un.append({"id": obj.id, "label": obj.label, "unmet": unmet_soft})
    return {"reachable_now": now, "reachable_if": cond, "unreachable": un,
            "undeclared": undecl}


def unrealized(g) -> list:
    """Objectives I can ALREADY satisfy but have not achieved.

    Root on a host already held, with the objective that needs it still open.
    """
    r = reach_pareto(g)
    out = []
    for obj in g.objectives():
        if obj.status == "achieved":
            continue
        ok, _ = _requirements_met(g, obj, r, max_cost=0)
        if ok:
            out.append({
                "id": obj.id, "label": obj.label,
                "crown_jewel": bool(obj.props.get("crown_jewel")),
                "status": obj.status or "open",
                "why": [g.edges[e].id for e in
                        min(r.get(obj.props["requires"][0]["target"]) or [{"path": [], "cost": 0}],
                            key=lambda e: e["cost"])["path"]],
            })
    out.sort(key=lambda o: (not o["crown_jewel"], o["id"]))
    return out


def unmined(g) -> list:
    """Assets I can reach RIGHT NOW and have never examined, oldest first.

    Two shapes, one failure:
      - acquired but never tried  (a credential held, never used on the app it opens)
      - reachable but never read  (a share denied to a service account, never
                                   re-tried once admin made it readable)

    Reachability is re-evaluated every call, so an asset that was legitimately
    out of reach earlier resurfaces the moment new access makes it gettable.
    That resurfacing is the point: 'denied once' is not 'denied forever'.
    """
    r = reach(g)
    out = []
    for n in g.nodes.values():
        if n.superseded_by or n.kind in ("objective", "operator", "assumption",
                                         "technique", "finding"):
            continue
        if n.exploitation in ("examined", "exhausted"):
            continue
        # Reachability alone only raises an alarm for things whose CONTENT is the
        # point - a share, a store, a credential, an endpoint. Flagging every
        # reachable host instead produced 12 alarms where 4 mattered, and a board
        # that cries wolf is a board nobody reads. A host still alarms once it is
        # explicitly marked `acquired`.
        reachable = (n.id in r and r[n.id]["cost"] == 0
                     and n.kind in ("artifact", "cred", "service"))
        if n.exploitation == "acquired" or reachable:
            since = (n.acquired_at if n.acquired_at is not None else n.first_seen)
            out.append({"id": n.id, "kind": n.kind, "label": n.label,
                        "age_held": g.seq - since,
                        "acquired_at": since,
                        "why": "acquired, never examined" if n.exploitation == "acquired"
                               else "reachable now, never examined"})
    out.sort(key=lambda x: -x["age_held"])
    return out


def stale(g) -> list:
    """Things being trusted without verification.

    A target host sits on the working path with its identity never actually
    confirmed, so hours of work land on an orphaned box.
    """
    r = reach(g)
    on_path = set()
    for info in r.values():
        for eid in info["path"]:
            e = g.edges[eid]
            on_path.add(e.src)
            on_path.add(e.dst)

    out = []
    for nid in sorted(on_path):
        n = g.nodes.get(nid)
        if not n or n.superseded_by:
            continue
        # Identity only matters where being wrong sends work to the wrong place.
        # An unread artifact is an `unmined` finding, not a trust failure.
        if n.kind not in ("host", "service", "cred"):
            continue
        if n.epistemic in ("unexplored", "hypothesized"):
            out.append({"id": n.id, "label": n.label, "kind": n.kind,
                        "epistemic": n.epistemic,
                        "reason": "on an active path but never verified"})
        elif n.epistemic == "verified" and n.last_verified is not None:
            if g.seq - n.last_verified > 25:
                out.append({"id": n.id, "label": n.label, "kind": n.kind,
                            "epistemic": n.epistemic,
                            "reason": f"verified at seq {n.last_verified}, "
                                      f"{g.seq - n.last_verified} events ago"})
    return out


def coverage(g) -> dict:
    objs = g.objectives()
    by_status = {}
    for o in objs:
        by_status[o.status or "open"] = by_status.get(o.status or "open", 0) + 1
    arts = [n for n in g.nodes.values()
            if n.kind == "artifact" and not n.superseded_by]
    examined = [a for a in arts if a.exploitation in ("examined", "exhausted")]
    return {
        "objectives_total": len(objs),
        "by_status": by_status,
        "achieved": by_status.get("achieved", 0),
        "artifacts_total": len(arts),
        "artifacts_examined": len(examined),
        "artifact_examined_ratio": (len(examined) / len(arts)) if arts else None,
    }


def why(g, obj_id: str) -> dict:
    """Explain the winning path to an objective, and what it rests on."""
    obj = g.nodes.get(obj_id)
    if not obj:
        return {"error": f"no such node: {obj_id}"}
    r = reach_pareto(g)
    steps, assumptions = [], []
    for req in obj.props.get("requires") or []:
        entries = r.get(req.get("target")) or []
        need = int(req.get("min_rank", 0))
        viable = [e for e in entries if e["rank"] >= need] or entries
        if not viable:
            steps.append({"target": req.get("target"), "status": "UNREACHABLE"})
            continue
        got = min(viable, key=lambda e: e["cost"])
        for eid in got["path"]:
            e = g.edges[eid]
            steps.append({
                "edge": e.id, "rel": e.rel,
                "from": e.src, "to": e.dst,
                "state": e.epistemic,
                "privilege": e.props.get("privilege"),
            })
        assumptions += got["assumptions"]
    return {"objective": obj.id, "label": obj.label,
            "steps": steps, "assumptions": sorted(set(assumptions))}


def verification_queue(g) -> list:
    """Hypothesized edges ranked by how many objectives they gate.

    This is the cheapest-next-test list: verify the edge that unblocks the most.
    """
    f = frontier(g)
    gate_count = {}
    for item in f["reachable_if"]:
        for eid in item["assumptions"]:
            gate_count[eid] = gate_count.get(eid, 0) + 1
    out = []
    for eid, count in gate_count.items():
        e = g.edges[eid]
        out.append({"edge": eid, "rel": e.rel, "from": e.src, "to": e.dst,
                    "gates": count, "confidence": e.confidence})
    out.sort(key=lambda x: -x["gates"])
    return out


# --- feature 4: the failure budget -------------------------------------------

DEFAULT_BUDGET = 2


def budget(g, limit: int = DEFAULT_BUDGET) -> list:
    """Targets that have burned the failure budget without succeeding.

    `feedback_docs_first_failure_budget`: after two dead ends on one approach,
    re-scope the whole approach rather than tweak-and-retry. That is a rule you
    have to remember mid-engagement, which is precisely when you don't - so
    count it instead. Tweak-and-retry feels like progress, which is why it needs
    an external alarm rather than self-discipline.
    """
    out = []
    for target in list(g.nodes.values()) + list(g.edges.values()):
        if getattr(target, "superseded_by", None):
            continue
        failed = getattr(target, "failed_attempts", 0)
        if failed >= limit and not getattr(target, "succeeded", False):
            out.append({
                "id": target.id,
                "label": getattr(target, "label", target.id),
                "failed": failed,
                "notes": [a.get("note", "") for a in target.attempts
                          if a.get("outcome") == "failed"],
                "advice": f"{failed} failed attempts, no success — re-scope the "
                          "approach rather than retry it",
            })
    out.sort(key=lambda x: -x["failed"])
    return out


# --- feature 1: the delta board ----------------------------------------------

def _alarm_ids(g):
    return ({o["id"] for o in unrealized(g)},
            {u["id"] for u in unmined(g)},
            {s["id"] for s in stale(g)},
            {o["id"] for o in frontier(g)["reachable_now"]})


def delta(before, after) -> dict:
    """What changed between two folds of the same log.

    The compass property: under information overload you do not want state, you
    want the CHANGE in state. A 60-node board becomes three lines, and stays
    three lines however large the engagement grows.
    """
    b_unreal, b_unmined, b_stale, b_now = _alarm_ids(before)
    a_unreal, a_unmined, a_stale, a_now = _alarm_ids(after)

    def label(nid):
        n = after.nodes.get(nid)
        return n.label if n else nid

    newly_winnable = [{"id": i, "label": label(i)} for i in a_now - b_now]
    resolved = []
    for eid, e in after.edges.items():
        old = before.edges.get(eid)
        if old is not None and old.epistemic != e.epistemic and \
                e.epistemic in ("verified", "refuted"):
            resolved.append({"id": eid, "from": old.epistemic, "to": e.epistemic})
    for nid, n in after.nodes.items():
        old = before.nodes.get(nid)
        if old is not None and old.epistemic != n.epistemic and \
                n.epistemic in ("verified", "refuted"):
            resolved.append({"id": nid, "from": old.epistemic, "to": n.epistemic})

    return {
        "from_seq": before.seq,
        "to_seq": after.seq,
        "events": after.seq - before.seq,
        "new_nodes": [{"id": i, "label": after.nodes[i].label,
                       "kind": after.nodes[i].kind}
                      for i in set(after.nodes) - set(before.nodes)],
        "newly_winnable": newly_winnable,
        "new_unrealized": [{"id": i, "label": label(i)} for i in a_unreal - b_unreal],
        "new_unmined": [{"id": i, "label": label(i)} for i in a_unmined - b_unmined],
        "cleared_unmined": [{"id": i, "label": label(i)} for i in b_unmined - a_unmined],
        "new_stale": [{"id": i, "label": label(i)} for i in a_stale - b_stale],
        "resolved": resolved,
        "decisions": [d for d in after.decisions if d["seq"] > before.seq],
        "budget_blown": budget(after),
    }
