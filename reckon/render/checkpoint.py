"""The checkpoint brief — SPEC-004 §7.

**Recording health prints first, above everything.** If the instrument is behind
the work, every section below it is untrustworthy, and reading them in that state
is worse than reading nothing: a confident stale picture is how a "BLOCKED"
banner survives for weeks after the objective behind it was achieved.

The rest is decision-shaped rather than report-shaped — what changed, where you
are, what is next, what to decide — because the ritual it replaces was a decision
point, not a status meeting.
"""


def checkpoint(c: dict) -> str:
    out = [f"# Checkpoint — {c['engagement']}", "",
           f"*seq {c['seq']} · since seq {c['since']} · {c['events']} events*", ""]
    A = out.append

    health = c["recording_health"]
    if health:
        A("## ⚠ Recording health")
        A("")
        for a in health:
            A(f"- **{a['id']} {a['name']}** — {a['why']}")
        A("")
        A("> The sections below are computed from the graph. If the graph is "
          "behind the work, they are behind it too — record before deciding.")
        A("")

    d = c["delta"]
    A("## What changed")
    A("")
    wrote = False
    for label, rows, fmt in (
            ("★ newly winnable", d["newly_winnable"],
             lambda x: f"{x['label']} (`{x['id']}`)"),
            ("⚠ new unrealized", d["new_unrealized"],
             lambda x: f"{x['label']} (`{x['id']}`)"),
            ("⚠ new unmined", d["new_unmined"],
             lambda x: f"{x['label']} (`{x['id']}`)"),
            ("⚠ new unverified", d["new_stale"],
             lambda x: f"{x['label']} (`{x['id']}`)"),
            ("cleared unmined", d["cleared_unmined"],
             lambda x: f"{x['label']} (`{x['id']}`)"),
            ("resolved", d["resolved"],
             lambda x: f"`{x['id']}` {x['from']} → {x['to']}"),
            ("new nodes", d["new_nodes"],
             lambda x: f"{x['kind']} {x['label']} (`{x['id']}`)")):
        if rows:
            wrote = True
            A(f"**{label}:**")
            A("")
            for x in rows[:8]:
                A(f"- {fmt(x)}")
            if len(rows) > 8:
                A(f"- …and {len(rows) - 8} more")
            A("")
    if not wrote:
        A("_Nothing changed since the last checkpoint._")
        A("")

    cov = c["coverage"]
    A("## Where you are")
    A("")
    A(f"Objectives {cov['achieved']}/{cov['objectives_total']} achieved · "
      f"artifacts examined {cov['artifacts_examined']}/{cov['artifacts_total']}")
    A("")
    engagement = [a for a in c["alarms"] if a["group"] == "engagement"]
    if engagement:
        for a in engagement:
            A(f"- **{a['id']} {a['name']}** — {a['why']}")
        A("")

    A("## Next")
    A("")
    n = 0
    for x in c["next_moves"]["unrealized"]:
        n += 1
        A(f"{n}. **{x['label']}** (`{x['id']}`) — already satisfiable, not done")
    for q in c["next_moves"]["queue"][:3]:
        n += 1
        A(f"{n}. Test `{q['edge']}` ({q['from']} —{q['rel']}→ {q['to']}) — "
          f"one test, unlocks {q['gates']} objective(s)")
    if not n:
        A("_Nothing already satisfiable and nothing queued to verify._")
    A("")

    A("## Decide")
    A("")
    a5 = next((a for a in c["alarms"] if a["id"] == "A5"), None)
    if a5:
        A("No decision recorded since the last checkpoint (A5).")
        A("")
        A('    reckon decide "<what>" --reason "<why>" --rejected "<what else>"')
    else:
        recent = c["delta"].get("decisions") or []
        if recent:
            for dec in recent[-3:]:
                A(f"- **chose** {dec['chose']}"
                  + (f" · because {dec['reason']}" if dec.get("reason") else ""))
        else:
            A("_Decisions are current._")
    A("")

    if c["rendered"]:
        A(f"*Regenerated {len(c['rendered'])} documents.*")
    if c["dark"]:
        A("")
        A("*Not yet computed: "
          + " · ".join(f"{a['id']} {a['name']} ({a['why']})" for a in c["dark"])
          + ".*")
    return "\n".join(out).rstrip() + "\n"
