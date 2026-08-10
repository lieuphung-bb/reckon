"""The board: one screen answering 'where am I, what can I do, what did I miss'."""

from ..queries import (frontier, unrealized, unmined, stale, coverage,
                       reach, verification_queue, budget)
from ..model import OPERATOR_ID

TICK = {"verified": "✓", "hypothesized": "?", "unexplored": "·", "refuted": "✗"}


def _held(g):
    r = reach(g)
    rows = []
    for nid, info in r.items():
        n = g.nodes.get(nid)
        if not n or n.kind == "operator" or n.superseded_by:
            continue
        if info["cost"] == 0:
            rows.append((n, info))
    rows.sort(key=lambda x: (x[0].kind, x[0].id))
    return rows


def board(g, name: str = "engagement") -> str:
    f = frontier(g)
    ur, um, st = unrealized(g), unmined(g), stale(g)
    cov = coverage(g)
    out = []
    A = out.append

    A(f"# Board — {name}")
    A("")
    A(f"*seq {g.seq} · objectives {cov['achieved']}/{cov['objectives_total']} achieved"
      f" · artifacts examined {cov['artifacts_examined']}/{cov['artifacts_total']}*")
    A("")

    # --- the two alarms come FIRST: they are things already winnable / already held
    if ur:
        A("## ⚠ UNREALIZED — satisfiable right now, not done")
        A("")
        A("| Objective | Crown | Status |")
        A("|---|---|---|")
        for o in ur:
            A(f"| {o['label']} (`{o['id']}`) | {'★' if o['crown_jewel'] else ''} "
              f"| {o['status']} |")
        A("")

    if um:
        A("## ⚠ UNMINED — reachable, never examined")
        A("")
        A("| Asset | Kind | Held for (events) | Why |")
        A("|---|---|---|---|")
        for u in um:
            A(f"| {u['label']} (`{u['id']}`) | {u['kind']} | {u['age_held']} "
              f"| {u['why']} |")
        A("")

    bl = budget(g)
    if bl:
        A("## ⚠ BUDGET BLOWN — stop retrying, re-scope")
        A("")
        A("| Target | Failed | What to do |")
        A("|---|---|---|")
        for b in bl:
            A(f"| {b['label']} (`{b['id']}`) | {b['failed']} | {b['advice']} |")
        A("")

    if st:
        A("## ⚠ UNVERIFIED — trusted on an active path")
        A("")
        A("| Node | State | Why it matters |")
        A("|---|---|---|")
        for s in st:
            A(f"| {s['label']} (`{s['id']}`) | {s['epistemic']} | {s['reason']} |")
        A("")

    # --- position
    A("## Owned (verified access)")
    A("")
    held = _held(g)
    if not held:
        A("_nothing verified yet_")
    else:
        A("| Node | Kind | Priv | Epistemic | Exploitation |")
        A("|---|---|---|---|---|")
        for n, info in held:
            A(f"| {n.label} (`{n.id}`) | {n.kind} | {info['rank'] or ''} "
              f"| {TICK.get(n.epistemic, '')} {n.epistemic} | {n.exploitation} |")
    A("")

    A("## Frontier")
    A("")
    A("**Reachable now:** " + (", ".join(
        f"{'★' if o['crown_jewel'] else ''}{o['label']}" for o in f["reachable_now"])
        or "_none_"))
    A("")
    if f["reachable_if"]:
        A("**Reachable if:**")
        A("")
        for o in f["reachable_if"]:
            A(f"- {'★' if o['crown_jewel'] else ''}{o['label']} — needs "
              + ", ".join(f"`{a}`" for a in o["assumptions"]))
        A("")
    if f["unreachable"]:
        A("**Unreachable (needs discovery):** "
          + ", ".join(o["label"] for o in f["unreachable"]))
        A("")
    if f.get("undeclared"):
        A(f"**Requirements undeclared ({len(f['undeclared'])}):** "
          + ", ".join(f"{'★' if o['crown_jewel'] else ''}{o['label'][:40]}"
                      for o in f["undeclared"][:8])
          + ("…" if len(f["undeclared"]) > 8 else ""))
        A("")
        A("*Declare what each needs (`reckon add objective … --requires host:x@3`) "
          "and the frontier becomes computable.*")
        A("")

    vq = verification_queue(g)
    if vq:
        A("## Verification queue (cheapest unlock first)")
        A("")
        A("| Edge | Relation | Gates |")
        A("|---|---|---|")
        for v in vq:
            A(f"| `{v['edge']}` | {v['from']} →{v['rel']}→ {v['to']} | {v['gates']} |")
        A("")

    A(mermaid(g))
    return "\n".join(out)


def mermaid(g) -> str:
    """Graph picture. Solid = verified, dotted = hypothesized, refuted omitted."""
    lines = ["## Graph", "", "```mermaid", "graph LR"]
    seen = set()
    for e in g.edges.values():
        if e.epistemic == "refuted":
            continue
        for nid in (e.src, e.dst):
            if nid in seen:
                continue
            seen.add(nid)
            n = g.nodes.get(nid)
            if not n:
                continue
            label = f"{n.label}"
            if n.kind == "objective":
                shape = f'{_sid(nid)}{{{{"★ {label}"}}}}' if n.props.get("crown_jewel") \
                        else f'{_sid(nid)}[["{label}"]]'
            elif n.kind == "operator":
                shape = f'{_sid(nid)}(("{label}"))'
            else:
                shape = f'{_sid(nid)}["{label}"]'
            lines.append(f"  {shape}")
        arrow = "-->" if e.epistemic == "verified" else "-.->"
        lines.append(f"  {_sid(e.src)} {arrow}|{e.rel}| {_sid(e.dst)}")
    lines += ["```", ""]
    return "\n".join(lines)


def _sid(node_id: str) -> str:
    return node_id.replace(":", "_").replace("-", "_").replace(".", "_")
