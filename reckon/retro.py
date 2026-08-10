"""L3 — cross-engagement mastery analytics.

This is why the store is event-sourced. Every metric here needs *state as it was
known at moment T*, which a snapshot store cannot reconstruct.

The headline metric is capability→realization latency: the gap between the moment
an objective first became satisfiable and the moment it was actually achieved.
That gap is skill left on the table — not a knowledge gap, a visibility gap.
"""

from .model import fold
from .queries import reach, _requirements_met, frontier
from . import store


def _satisfiable_at(events, upto_seq, obj_id):
    g = fold([e for e in events if e.get("seq", 0) <= upto_seq])
    obj = g.nodes.get(obj_id)
    if not obj:
        return False
    ok, _ = _requirements_met(g, obj, reach(g), max_cost=0)
    return ok


def capability_latency(name: str) -> list:
    """For each objective: when it became winnable vs when it was won."""
    events = store.read_events(name)
    if not events:
        return []
    g = fold(events)
    seqs = sorted({e.get("seq", 0) for e in events})
    # An objective can reach "achieved" two ways: an explicit set_objective, or
    # being recorded that way at creation (backfilled history). Reading only the
    # former reported five already-done objectives as left on the table.
    achieved_at = {}
    for e in events:
        if e["op"] in ("set_objective", "add_node") \
                and e["args"].get("status") == "achieved":
            achieved_at.setdefault(e["args"]["id"], e["seq"])

    out = []
    for obj in g.objectives():
        first_sat = None
        for s in seqs:
            if _satisfiable_at(events, s, obj.id):
                first_sat = s
                break
        done = achieved_at.get(obj.id)
        if first_sat is None:
            state, latency = "never satisfiable", None
        elif done is None:
            state, latency = "LEFT ON THE TABLE", g.seq - first_sat
        else:
            state, latency = "achieved", done - first_sat
        out.append({
            "objective": obj.id, "label": obj.label,
            "crown_jewel": bool(obj.props.get("crown_jewel")),
            "first_satisfiable_at": first_sat, "achieved_at": done,
            "latency": latency, "state": state,
        })
    out.sort(key=lambda x: (x["state"] != "LEFT ON THE TABLE",
                            -(x["latency"] or 0)))
    return out


def time_to_mine(name: str) -> list:
    """How long each asset sat in hand before anyone looked at it."""
    g = store.load(name)
    out = []
    for n in g.nodes.values():
        if n.kind in ("objective", "operator", "assumption") or n.superseded_by:
            continue
        if n.acquired_at is None and n.exploitation == "discovered":
            continue
        acq = n.acquired_at if n.acquired_at is not None else n.first_seen
        if n.examined_at is not None:
            out.append({"id": n.id, "label": n.label, "kind": n.kind,
                        "acquired_at": acq, "examined_at": n.examined_at,
                        "ttm": n.examined_at - acq, "state": "examined"})
        else:
            out.append({"id": n.id, "label": n.label, "kind": n.kind,
                        "acquired_at": acq, "examined_at": None,
                        "ttm": g.seq - acq, "state": "NEVER EXAMINED"})
    out.sort(key=lambda x: (x["state"] != "NEVER EXAMINED", -x["ttm"]))
    return out


def assumption_hit_rate(name: str) -> dict:
    """Am I calibrated? Hypotheses that survived vs died."""
    events = store.read_events(name)
    was_hyp, confirmed, refuted = set(), set(), set()
    for e in events:
        if e["op"] == "add_node" or e["op"] == "add_edge":
            if e["args"].get("epistemic") == "hypothesized":
                was_hyp.add(e["args"]["id"])
        if e["op"] == "set_epistemic":
            tid, st = e["args"]["id"], e["args"]["state"]
            if st == "hypothesized":
                was_hyp.add(tid)
            elif st == "verified" and tid in was_hyp:
                confirmed.add(tid)
            elif st == "refuted" and tid in was_hyp:
                refuted.add(tid)
    resolved = len(confirmed) + len(refuted)
    return {
        "hypotheses": len(was_hyp),
        "confirmed": len(confirmed),
        "refuted": len(refuted),
        "open": len(was_hyp) - resolved,
        "hit_rate": (len(confirmed) / resolved) if resolved else None,
    }


def render(name: str) -> str:
    lat = capability_latency(name)
    ttm = time_to_mine(name)
    hr = assumption_hit_rate(name)
    g = store.load(name)

    out = [f"# Retro — {name}", "", f"*{g.seq} events*", ""]

    left = [x for x in lat if x["state"] == "LEFT ON THE TABLE"]
    out += ["## Capability → realization latency", "",
            "The gap between *could win* and *did win*. A non-zero gap is a "
            "visibility failure, not a skill gap.", ""]
    if not lat:
        out.append("_no objectives_")
    else:
        out += ["| Objective | Winnable at | Achieved at | Latency | |",
                "|---|---|---|---|---|"]
        for x in lat:
            flag = "⚠" if x["state"] == "LEFT ON THE TABLE" else ""
            out.append(f"| {'★' if x['crown_jewel'] else ''}{x['label']} "
                       f"| {x['first_satisfiable_at'] if x['first_satisfiable_at'] is not None else '—'} "
                       f"| {x['achieved_at'] if x['achieved_at'] is not None else '—'} "
                       f"| {x['latency'] if x['latency'] is not None else '—'} | {flag} |")
    if left:
        out += ["", f"**{len(left)} objective(s) left on the table** — satisfiable "
                    "with access already held, never executed.", ""]

    out += ["", "## Time to mine", "",
            "How long an asset sat in hand before it was examined.", "",
            "| Asset | Kind | Acquired | Examined | Held |", "|---|---|---|---|---|"]
    if not ttm:
        out.append("| _none_ | | | | |")
    for x in ttm[:20]:
        out.append(f"| {x['label']} | {x['kind']} | {x['acquired_at']} "
                   f"| {x['examined_at'] if x['examined_at'] is not None else '**never**'} "
                   f"| {x['ttm']} |")

    out += ["", "## Assumption calibration", "",
            f"- Hypotheses raised: **{hr['hypotheses']}**",
            f"- Confirmed: **{hr['confirmed']}** · refuted: **{hr['refuted']}** "
            f"· still open: **{hr['open']}**"]
    if hr["hit_rate"] is not None:
        out.append(f"- Hit rate: **{hr['hit_rate']:.0%}** "
                   "(near 100% means hypotheses are too timid; near 0% means "
                   "they are guesses)")
    return "\n".join(out) + "\n"
