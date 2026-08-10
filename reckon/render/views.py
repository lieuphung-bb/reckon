"""The six views, all generated from one graph.

The point is not that markdown is nice. It is that six files derived from a
single source *cannot disagree with each other*, which is the failure mode that
made the hand-maintained workspace go stale (a doc saying BLOCKED for weeks
after the objective had actually been achieved).
"""

from ..queries import frontier, unrealized, unmined, stale, coverage, \
    verification_queue, reach, why
from .board import board, mermaid

VIEWS = ("topology", "assumptions", "attack_brief", "recon", "threat_model", "plan")


def topology(g, name):
    return board(g, name)


def assumptions(g, name):
    out = [f"# Assumptions — {name}", "",
           "Confidence is **source reliability** (A–F), never probability. "
           "A low-confidence item drives a verification task; it never enters the plan.",
           "", "## Register", "",
           "| Item | Hypothesis | Conf | Source | Epistemic |", "|---|---|---|---|---|"]
    rows = [n for n in g.nodes.values()
            if not n.superseded_by
            and (n.kind == "assumption" or n.epistemic == "hypothesized")]
    if not rows:
        out.append("| _none_ | | | | |")
    for n in sorted(rows, key=lambda x: x.id):
        out.append(f"| `{n.id}` | {n.label} | {n.confidence or '—'} "
                   f"| {n.source or '—'} | {n.epistemic} |")

    out += ["", "## Hypothesized edges (what a path rests on)", "",
            "| Edge | Link | Conf |", "|---|---|---|"]
    hyp = [e for e in g.edges.values() if e.epistemic == "hypothesized"]
    if not hyp:
        out.append("| _none_ | | |")
    for e in sorted(hyp, key=lambda x: x.id):
        out.append(f"| `{e.id}` | {e.src} →{e.rel}→ {e.dst} | {e.confidence or '—'} |")

    out += ["", "## Verification queue (ordered by objectives gated)", "",
            "| Edge | Link | Gates |", "|---|---|---|"]
    vq = verification_queue(g)
    if not vq:
        out.append("| _none_ | | |")
    for v in vq:
        out.append(f"| `{v['edge']}` | {v['from']} →{v['rel']}→ {v['to']} "
                   f"| {v['gates']} |")

    st = stale(g)
    if st:
        out += ["", "## ⚠ Trusted without verification", ""]
        for s in st:
            out.append(f"- `{s['id']}` — {s['reason']}")
    return "\n".join(out) + "\n"


def attack_brief(g, name):
    f = frontier(g)
    ur = unrealized(g)
    out = [f"# Attack brief — {name}", "", f"*Version: seq {g.seq}*", ""]

    out += ["## Decision", ""]
    if ur:
        out.append(f"**Do this first: {ur[0]['label']}** — already satisfiable "
                   f"with access in hand, not yet achieved.")
    elif f["reachable_now"]:
        out.append(f"**Next: {f['reachable_now'][0]['label']}** — reachable with "
                   "verified access.")
    elif f["reachable_if"]:
        item = f["reachable_if"][0]
        out.append(f"**Next: verify** {', '.join('`'+a+'`' for a in item['assumptions'])}"
                   f" — unlocks {item['label']}.")
    elif f.get("undeclared"):
        out.append(f"**{len(f['undeclared'])} objectives have no declared "
                   "requirements** — the frontier cannot be computed until they do. "
                   "That is an annotation gap, not a dead end.")
    else:
        out.append("**No path. Recon is not done** — the current access tier is not "
                   "exhausted, or discovery is needed.")

    out += ["", "## Position", ""]
    cov = coverage(g)
    out.append(f"- Objectives: {cov['achieved']}/{cov['objectives_total']} achieved")
    out.append(f"- Artifacts examined: {cov['artifacts_examined']}/{cov['artifacts_total']}")
    out.append(f"- Reachable now: {len(f['reachable_now'])} · "
               f"reachable if: {len(f['reachable_if'])} · "
               f"unreachable: {len(f['unreachable'])}")

    out += ["", "## Objectives", "", "| Objective | Distance | Rests on |",
            "|---|---|---|"]
    for o in f["reachable_now"]:
        out.append(f"| {'★' if o['crown_jewel'] else ''}{o['label']} | **now** | — |")
    for o in f["reachable_if"]:
        out.append(f"| {'★' if o['crown_jewel'] else ''}{o['label']} | if | "
                   + ", ".join(f"`{a}`" for a in o["assumptions"]) + " |")
    for o in f["unreachable"]:
        out.append(f"| {o['label']} | unreachable | needs: "
                   + ", ".join(o["unmet"]) + " |")

    if g.decisions:
        out += ["", "## Decision log", "",
                "Why a path was chosen and what it ruled out — the reasoning that "
                "otherwise lives in scrollback and gets re-litigated.", "",
                "| Seq | Chose | Rejected | Because |", "|---|---|---|---|"]
        for d in g.decisions:
            out.append(f"| {d['seq']} | {d['chose']} "
                       f"| {', '.join(d['rejected']) or '—'} "
                       f"| {d['reason'] or '—'} |")
    return "\n".join(out) + "\n"


def recon(g, name):
    """The event log as narrative. Free, because the log is the source of truth."""
    out = [f"# Recon log — {name}", "",
           "Chronological. History is never deleted; a refuted conclusion is "
           "superseded in place.", ""]
    for ev in g.events:
        a = ev.get("args", {})
        tgt = a.get("id") or a.get("target_id") or a.get("old_id") or ""
        detail = a.get("outcome") or a.get("text") or a.get("state") \
            or a.get("status") or a.get("rel") or ""
        out.append(f"- `{ev['seq']:>3}` **{ev['op']}** {tgt} — {detail}")
    out += ["", "## Notes by node", ""]
    for n in sorted(g.nodes.values(), key=lambda x: x.id):
        if n.notes:
            out.append(f"### {n.label} (`{n.id}`)")
            for note in n.notes:
                out.append(f"- {note}")
            out.append("")
    return "\n".join(out) + "\n"


def threat_model(g, name):
    out = [f"# Threat model — {name}", "", "## Component inventory", ""]
    for kind in ("host", "service", "cred", "artifact", "technique", "finding"):
        items = [n for n in g.by_kind(kind)]
        if not items:
            continue
        out += [f"### {kind}", "", "| Node | Epistemic | Exploitation | Conf |",
                "|---|---|---|---|"]
        for n in sorted(items, key=lambda x: x.id):
            out.append(f"| {n.label} (`{n.id}`) | {n.epistemic} "
                       f"| {n.exploitation} | {n.confidence or '—'} |")
        out.append("")

    out += ["## Crown jewels (ranked by validated access)", ""]
    f = frontier(g)
    crown = [o for o in f["reachable_now"] + f["reachable_if"]
             if o.get("crown_jewel")]
    if not crown:
        out.append("_none declared_")
    for o in crown:
        dist = "reachable now" if o in f["reachable_now"] else "reachable if"
        out.append(f"- ★ {o['label']} — {dist}")

    out += ["", "## Escalation paths", ""]
    for e in sorted(g.edges.values(), key=lambda x: x.id):
        if e.rel in ("escalates-to", "grants-access-to"):
            out.append(f"- `{e.src}` →{e.rel}→ `{e.dst}` "
                       f"({e.epistemic}{', ' + str(e.props.get('privilege')) if e.props.get('privilege') else ''})")
    out += ["", mermaid(g)]
    return "\n".join(out) + "\n"


def plan(g, name):
    out = [f"# Plan — {name}", "", "Derived from the frontier. Ordered: things "
           "already winnable first, then the cheapest unlock.", ""]
    ur = unrealized(g)
    if ur:
        out += ["## Do now (already satisfiable)", ""]
        for i, o in enumerate(ur, 1):
            out.append(f"{i}. {'★' if o['crown_jewel'] else ''}{o['label']} "
                       f"(`{o['id']}`)")
        out.append("")
    um = unmined(g)
    if um:
        out += ["## Mine what is already held", ""]
        for i, u in enumerate(um, 1):
            out.append(f"{i}. Examine {u['label']} (`{u['id']}`) — held "
                       f"{u['age_held']} events")
        out.append("")
    vq = verification_queue(g)
    if vq:
        out += ["## Then verify (highest unlock first)", ""]
        for i, v in enumerate(vq, 1):
            out.append(f"{i}. `{v['edge']}` — {v['from']} →{v['rel']}→ {v['to']} "
                       f"(gates {v['gates']})")
        out.append("")
    f = frontier(g)
    for o in f["reachable_if"]:
        out += [f"## Path — {o['label']}", ""]
        w = why(g, o["id"])
        for s in w["steps"]:
            if "edge" in s:
                out.append(f"- [{'x' if s['state'] == 'verified' else ' '}] "
                           f"{s['from']} →{s['rel']}→ {s['to']} ({s['state']})")
        out.append("")
    return "\n".join(out) + "\n"


RENDERERS = {
    "topology": topology, "assumptions": assumptions, "attack_brief": attack_brief,
    "recon": recon, "threat_model": threat_model, "plan": plan,
}


def render_all(g, name: str) -> dict:
    return {v: RENDERERS[v](g, name) for v in VIEWS}
