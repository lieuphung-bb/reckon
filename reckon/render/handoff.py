"""The successor brief — SPEC-003 §7.1.

One self-contained markdown document. The reader may be a different model with
no history of this engagement, so the brief never names an id it does not also
explain, and never refers to anything that lived in the dead session.

The ordering is the whole design: **resume point first, position second.** A
successor reading top-down can act on line 3 without reading the rest;
everything below exists for when the plan needs re-judging rather than
continuing. Putting position first would be the natural report shape and the
wrong one — it makes the reader re-derive the procedure before they can move.
"""

from datetime import datetime, timezone


def _node(n) -> str:
    """`label (kind, epistemic)` — never a bare id, per §9.6."""
    bits = [n.get("kind")]
    if n.get("epistemic"):
        bits.append(n["epistemic"])
    if n.get("confidence"):
        bits.append(f"conf {n['confidence']}")
    return f"`{n['id']}` — {n['label']} ({', '.join(b for b in bits if b)})"


def _resume(h, block, plural) -> list:
    out, A = [], None
    out = []
    head = f"{block['title']}"
    if plural:
        who = block["agent"] or "unassigned"
        head = f"**{who}** — {head}"
    if block["stalled"]:
        head = "⚠ STALLED · " + head
    out.append(f"### {head}")
    out.append("")
    star = "★ " if block["crown"] else ""
    out.append(f"Objective: {star}{block['objective_label']} "
               f"(`{block['objective']}`)")
    out.append("")

    cur = block["cursor"]
    if not cur:
        out.append("_Every step is finished or skipped — nothing to resume. "
                   "Re-judge against the frontier below._")
        out.append("")
    else:
        out.append(f"**Step {cur['ordinal']} of {block['total_steps']}: "
                   f"\"{cur['text']}\"**")
        out.append("")
        if cur["status"] == "blocked":
            # The implication, never the bare enum: the enum tells a reader
            # nothing they can act on.
            out.append(f"- status: **blocked ({cur['blocked_reason']})** — "
                       f"{cur['implication']}")
        elif cur["status"] == "running":
            out.append("- status: **running** — the previous session did not "
                       "finish it. Re-verify what actually landed before "
                       "continuing; do not assume it completed.")
        else:
            out.append(f"- status: {cur['status']}")
        if cur["note"]:
            out.append(f"- note: \"{cur['note']}\"")
        if cur["command"]:
            out.append(f"- next command: `{cur['command']}`")
        out.append("")

    if block["produced"]:
        out.append("**Already produced by this plan** — these are in the graph "
                   "now, with their values; do not redo the steps that made "
                   "them.")
        out.append("")
        for p in block["produced"]:
            for n in p["nodes"]:
                out.append(f"- step {p['ordinal']} → {_node(n)}")
        out.append("")

    if block["resting_on"]:
        out.append("**Resting on** — unproved links this objective's path "
                   "depends on:")
        out.append("")
        for e in block["resting_on"]:
            conf = f" (conf {e['confidence']})" if e.get("confidence") else ""
            out.append(f"- `{e['id']}` — {e['text']}{conf}")
        out.append("")

    for w in block["warnings"]:
        out.append(f"> ⚠ {w}")
        out.append("")
    return out


def handoff(h: dict) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = [f"# Handoff — {h['engagement']}", "",
           f"*seq {h['seq']} · {ts}*", ""]
    A = out.append

    A("## Resume here")
    A("")
    if not h["resume"]:
        A("_No active plan._ Nothing records an agreed procedure for this "
          "engagement, so there is no resume point — pick from **Next moves** "
          "below and record a plan before continuing.")
        A("")
    else:
        plural = len(h["resume"]) > 1
        for block in h["resume"]:
            out.extend(_resume(h, block, plural))

    cov = h["coverage"]
    A("## Position")
    A("")
    A(f"Objectives {cov['achieved']}/{cov['objectives_total']} achieved · "
      f"artifacts examined {cov['artifacts_examined']}/{cov['artifacts_total']}")
    A("")
    owned = [o for o in h["owned"]]
    if owned:
        A("**Held now (verified access):**")
        A("")
        for o in owned:
            A(f"- {_node(o)}")
        A("")
    for label, key, fmt in (
            ("⚠ Satisfiable right now, not done", "unrealized",
             lambda x: f"{x['label']} (`{x['id']}`)"),
            ("⚠ Reachable and never examined", "unmined",
             lambda x: f"{x['label']} (`{x['id']}`) — {x['why']}"),
            ("⚠ Trusted on an active path, never verified", "stale",
             lambda x: f"{x['label']} (`{x['id']}`) — {x['reason']}"),
            ("⚠ Failure budget blown", "budget_blown",
             lambda x: f"{x['label']} (`{x['id']}`) — {x['advice']}")):
        rows = h["alarms"][key]
        if rows:
            A(f"**{label}:**")
            A("")
            for x in rows:
                A(f"- {fmt(x)}")
            A("")

    A("## Next moves if the plan is abandoned")
    A("")
    n = 0
    for x in h["next_moves"]["unrealized"]:
        n += 1
        A(f"{n}. **{x['label']}** (`{x['id']}`) — already satisfiable with "
          "access in hand, not done")
    for q in h["next_moves"]["queue"][:3]:
        n += 1
        A(f"{n}. Test `{q['edge']}` ({q['from']} —{q['rel']}→ {q['to']}) — "
          f"one test, unlocks {q['gates']} objective(s)")
    if not n:
        f = h["next_moves"]["frontier"]
        if f["unreachable"]:
            A("Nothing is one test away. Unreachable without fresh discovery: "
              + ", ".join(x["label"] for x in f["unreachable"]))
        else:
            A("_Nothing queued._")
    A("")

    if h["ruled_out"]:
        A("## Already ruled out")
        A("")
        A("Do not re-propose these without new information:")
        A("")
        for d in h["ruled_out"]:
            line = f"- **chose** {d['chose']}"
            if d.get("rejected"):
                line += f" · **rejected** {', '.join(d['rejected'])}"
            if d.get("reason"):
                line += f" · because {d['reason']}"
            A(line)
        A("")

    A("## Outstanding target changes (RoE)")
    A("")
    if not h["changes"]:
        A("_Nothing outstanding._")
    else:
        A("Owed at close, and not to be re-done by a successor:")
        A("")
        for c in h["changes"]:
            line = f"- `{c['id']}` {c['target']} — {c['what']}"
            if c["revert_hint"]:
                line += f" · revert: `{c['revert_hint']}`"
            if not c["reversible"]:
                line += " · **IRREVERSIBLE**"
            A(line)
    A("")

    if h["claims"]:                                   # pragma: no cover
        A("## Live claims")
        A("")
        for c in h["claims"]:
            A(f"- {c}")
        A("")
    return "\n".join(out).rstrip() + "\n"


def fleet(rows: list) -> str:
    if not rows:
        return "no agents have authored anything yet\n"
    head = f"{'AGENT':<8}{'PLAN':<26}{'CURSOR':<34}{'STATUS':<10}{'CLAIM':<12}LAST"
    out = [head]
    for r in rows:
        status = "STALLED" if r["stalled"] else r["status"]
        out.append(
            f"{r['agent'][:7]:<8}"
            f"{(r['title'] or '—')[:25]:<26}"
            f"{(r['cursor'] or '—')[:33]:<34}"
            f"{status:<10}"
            f"{(r['claim'] or '—'):<12}"
            f"seq {r['last_seq']}")
    out.append("")
    out.append("STALLED (an agent that died mid-step) needs claim expiry from "
               "SPEC-002 and is not computed yet — CLAIM stays empty.")
    return "\n".join(out) + "\n"
