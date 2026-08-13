"""CLI — a thin, argparse-shaped wrapper over `reckon.api`.

Terse and non-interactive because the main caller is an agent mid-engagement.
Recording a finding should cost one line; if a routine move needs more than
three, the model is too heavy and should be cut.

No validation lives here. It lives in `api`, so the phase-2 MCP server gets the
same guarantees without duplicating a single rule.
"""

import argparse
import json
import os
import sys
from dataclasses import asdict

from . import __version__, api, hooks, retro, ingest, store
from .model import (KINDS, RELS, EPISTEMIC, EXPLOITATION, STEP_STATUS,
                    BLOCKED_REASONS, BLOCKED_IMPLICATION)
from .queries import (frontier, unrealized, unmined, stale, why,
                      verification_queue, budget)
from .redact import redact_graph, redact_obj
from .render.board import board
from .render.handoff import handoff as render_handoff, fleet as render_fleet
from .render.checkpoint import checkpoint as render_checkpoint
from .render.html import console as html_console
from .render.views import render_all, VIEWS


def _kv(pairs):
    """k=v pairs; a value that parses as JSON is kept as JSON (ints, lists, dicts)."""
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise api.ValidationError(f"malformed -p entry (want k=v): {p!r}")
        k, v = p.split("=", 1)
        try:
            out[k] = json.loads(v)
        except (ValueError, TypeError):
            out[k] = v
    return out


def _emit(args, obj, text):
    print(json.dumps(obj, indent=2, default=str) if getattr(args, "json", False)
          else text)


def _graph(args):
    g = store.load(args.name)
    return redact_graph(g) if getattr(args, "redact", False) else g


# --- writes -------------------------------------------------------------------

def cmd_new(args):
    print(api.create(args.name_pos, force=args.force))


def cmd_add(args):
    print(api.add_node(args.name, args.kind, args.label, node_id=args.id,
                       epistemic=args.state, exploitation=args.exploitation,
                       confidence=args.conf, source=args.source,
                       props=_kv(args.props), requires=args.requires,
                       crown=args.crown))


def cmd_edge(args):
    print(api.add_edge(args.name, args.src, args.rel, args.dst, edge_id=args.id,
                       epistemic=args.state, confidence=args.conf,
                       props=_kv(args.props)))


def cmd_state(args):
    api.set_epistemic(args.name, args.id, args.state, confidence=args.conf,
                      source=args.source)
    print(f"{args.id} -> {args.state}")


def cmd_hold(args):
    api.set_exploitation(args.name, args.id, args.state)
    print(f"{args.id} -> {args.state}")


def cmd_examine(args):
    api.examine(args.name, args.id, args.outcome)
    print(f"examined {args.id}")


def cmd_obj(args):
    api.set_objective(args.name, args.id, args.status)
    print(f"{args.id} -> {args.status}")


def cmd_note(args):
    api.note(args.name, args.id, args.text)
    print("noted")


def cmd_supersede(args):
    api.supersede(args.name, args.old, args.new, args.reason)
    print(f"{args.old} superseded by {args.new}")


def cmd_ref(args):
    print(json.dumps(api.add_reference(args.name, args.id, args.store,
                                       args.label, args.key)))


def cmd_apply(args):
    with open(args.file, encoding="utf-8") as fh:
        events = json.load(fh)
    print(f"applied {api.apply_events(args.name, events)} events")


def cmd_import(args):
    events = ingest.from_workspace(args.path)
    if args.fresh:
        store.create(args.name, force=True)
    n = api.apply_events(args.name, events)
    g = store.load(args.name)
    print(f"imported {n} events from {args.path} -> {args.name} "
          f"({len(g.nodes)} nodes, {len(g.edges)} edges)")


# --- reads --------------------------------------------------------------------

def cmd_board(args):
    print(board(_graph(args), args.name))


def cmd_status(args):
    print(json.dumps(api.status(args.name), indent=2, default=str))


def cmd_frontier(args):
    f = frontier(_graph(args))
    _emit(args, f, "\n".join([
        "reachable now: " + (", ".join(o["label"] for o in f["reachable_now"]) or "-"),
        "reachable if:  " + (", ".join(o["label"] for o in f["reachable_if"]) or "-"),
        "unreachable:   " + (", ".join(o["label"] for o in f["unreachable"]) or "-"),
        "undeclared:    " + (", ".join(o["label"] for o in f["undeclared"]) or "-")]))


def cmd_unrealized(args):
    u = unrealized(_graph(args))
    _emit(args, u, "\n".join(f"⚠ {o['label']} ({o['id']}) — satisfiable now, "
                             f"{o['status']}" for o in u) or "none")


def cmd_unmined(args):
    u = unmined(_graph(args))
    _emit(args, u, "\n".join(f"⚠ {o['label']} ({o['kind']}) — {o['why']}, "
                             f"{o['age_held']} events" for o in u) or "none")


def cmd_stale(args):
    s = stale(_graph(args))
    _emit(args, s, "\n".join(f"⚠ {o['label']} — {o['reason']}" for o in s) or "none")


def cmd_queue(args):
    q = verification_queue(_graph(args))
    _emit(args, q, "\n".join(f"{v['gates']}x  {v['edge']}" for v in q) or "none")


def cmd_why(args):
    w = why(_graph(args), args.id)
    lines = [f"{w.get('label', args.id)}:"]
    for s in w.get("steps", []):
        lines.append(f"  [{s['state'][:4]}] {s['from']} -{s['rel']}-> {s['to']}"
                     if "edge" in s else f"  UNREACHABLE: {s['target']}")
    if w.get("assumptions"):
        lines.append("  rests on: " + ", ".join(w["assumptions"]))
    _emit(args, w, "\n".join(lines))


def cmd_views(args):
    g = _graph(args)
    out_dir = args.out or os.path.join(store.RECKON_HOME, "out", args.name)
    os.makedirs(out_dir, exist_ok=True)
    for view, text in render_all(g, args.name).items():
        with open(os.path.join(out_dir, f"{view}.md"), "w") as fh:
            fh.write(text)
    print(f"wrote {len(VIEWS)} views -> {out_dir}")


def cmd_console(args):
    g = _graph(args)
    out = args.out or os.path.join(store.RECKON_HOME, "out", f"{args.name}.html")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as fh:
        fh.write(html_console(g, args.name))
    if not args.redact:
        print("note: secrets render in full — local use only, never publish "
              "(re-run with --redact for a shareable copy)", file=sys.stderr)
    print(out)


def cmd_decide(args):
    api.decide(args.name, args.chose, args.reason, args.rejected, args.about)
    print(f"decided: {args.chose}")


def cmd_attempt(args):
    api.attempt(args.name, args.id, args.outcome, args.note)
    g = store.load(args.name)
    blown = [b for b in budget(g) if b["id"] == args.id]
    print(f"{args.id}: {args.outcome}")
    if blown:
        print(f"⚠ BUDGET BLOWN — {blown[0]['advice']}", file=sys.stderr)


def cmd_plan_add(args):
    pid = api.plan_add(args.name, args.objective, args.title, steps=args.step,
                       plan_id=args.id, supersedes=args.supersedes,
                       agent=args.agent)
    print(pid)


def cmd_plan_supersede(args):
    api.plan_supersede(args.name, args.old, args.new, reason=args.reason)
    print(f"{args.old} superseded by {args.new}")


def _step_line(s, cursor_id=None):
    mark = {"done": "✓", "running": "▶", "blocked": "✗",
            "skipped": "–", "pending": "·"}.get(s.status, "·")
    here = " ←" if s.id == cursor_id else ""
    out = f"  {mark} {s.ordinal}. {s.text}{here}"
    if s.status == "blocked":
        out += (f"\n      blocked ({s.blocked_reason}) — "
                f"{BLOCKED_IMPLICATION.get(s.blocked_reason, '')}")
    if s.note:
        out += f"\n      note: {s.note}"
    if s.produced:
        out += f"\n      produced: {', '.join(s.produced)}"
    elif s.status == "done" and not s.note:
        out += "\n      ⚠ done with nothing produced and no note — output may be stranded"
    if s.command:
        out += f"\n      $ {s.command}"
    return out


def cmd_plan_show(args):
    g = _graph(args)
    p = g.plans.get(args.plan)
    if not p:
        raise api.ValidationError(f"unknown plan id: {args.plan}")
    cur = p.cursor
    head = f"{p.id}  {p.title}  ({p.objective})"
    if p.superseded_by:
        head += f"  — SUPERSEDED by {p.superseded_by}"
    text = "\n".join([head] + [_step_line(s, cur.id if cur else None)
                               for s in p.steps])
    _emit(args, asdict(p), text)


def cmd_plans(args):
    g = _graph(args)
    plans = list(g.plans.values()) if args.all else g.active_plans()
    rows = []
    for p in plans:
        cur = p.cursor
        done = sum(1 for s in p.steps if s.status == "done")
        where = (f"{cur.ordinal}/{len(p.steps)} {cur.text[:40]} [{cur.status}]"
                 if cur else f"{done}/{len(p.steps)} complete")
        flag = "  SUPERSEDED" if p.superseded_by else ""
        rows.append(f"{p.id}  {p.title[:34]:<34}  {where}{flag}")
    _emit(args, [asdict(p) for p in plans], "\n".join(rows) or "no plans")


def cmd_step(args):
    api.step_state(args.name, args.plan, args.step, args.status,
                   note=args.note or "", blocked_reason=args.reason,
                   produced=args.produced, agent=args.agent)
    line = f"{args.plan} step {args.step}: {args.status}"
    if args.reason:
        line += (f" ({args.reason}) — "
                 f"{BLOCKED_IMPLICATION.get(args.reason, '')}")
    print(line)


def cmd_step_add(args):
    print(api.step_add(args.name, args.plan, args.text, command=args.command,
                       ordinal=args.ordinal, agent=args.agent))


def cmd_plan_reassign(args):
    api.plan_reassign(args.name, args.plan, args.to_agent, reason=args.reason)
    print(f"{args.plan} → {args.to_agent}")


def cmd_handoff(args):
    h = api.handoff(args.name, agent=args.agent, all_agents=args.all)
    if getattr(args, "redact", False):
        h = redact_obj(h)
    text = render_handoff(h)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(args.out)
        if not getattr(args, "redact", False):
            print("note: secrets render in full — re-run with --redact for a "
                  "copy that leaves this machine", file=sys.stderr)
        return
    _emit(args, h, text)


def cmd_hook_session_start(args):
    # No error path on purpose: `hooks` already swallows everything, and this
    # command must exit 0 whatever it finds. Printing nothing is a valid result.
    text = hooks.session_start(args.name, agent=args.agent,
                               redact=getattr(args, "redact", False))
    if text:
        print(text)


def cmd_hook_stop(args):
    hooks.stop(args.name)


def cmd_hook_config(args):
    print(json.dumps(hooks.settings_fragment(
        engagement=args.name if args.pin else None), indent=2))


def cmd_checkpoint(args):
    c = api.checkpoint(args.name, render=not args.no_render,
                       strict=args.strict, dry_run=args.dry_run)
    _emit(args, c, render_checkpoint(c))
    # Exit 2 only under --strict, and only for recording health: the instrument
    # being behind is a scriptable failure, a blown failure budget is not.
    if c["strict_fail"]:
        sys.exit(2)


def cmd_alarms(args):
    rows = api.alarms(args.name)
    text = "\n".join(f"{a['id']} {a['name']:<22} {a['why']}" for a in rows) \
        or "no alarms"
    _emit(args, rows, text)


def cmd_fleet(args):
    rows = api.fleet(args.name)
    _emit(args, rows, render_fleet(rows))


def cmd_change(args):
    print(api.change(args.name, args.target, args.what,
                     reversible=not args.irreversible,
                     revert_hint=args.revert or "", agent=args.agent))


def _changes_text(rows):
    if not rows:
        return "no outstanding target changes"
    out = []
    for c in rows:
        mark = "✓ " if c["cleaned"] else "  "
        tail = f"  ↩ {c['revert_hint']}" if c["revert_hint"] else ""
        if not c["reversible"]:
            tail += "  IRREVERSIBLE"
        out.append(f"{mark}{c['id']}  {c['target']}  {c['what']}{tail}")
    return "\n".join(out)


def cmd_changes(args):
    rows = api.changes(args.name, outstanding_only=not args.all)
    if getattr(args, "redact", False):
        rows = redact_obj(rows)
    _emit(args, rows, _changes_text(rows))


def cmd_cleaned(args):
    api.mark_cleaned(args.name, args.change_id, agent=args.agent)
    print(f"{args.change_id} cleaned")


def cmd_budget(args):
    b = budget(_graph(args), limit=args.limit)
    _emit(args, b, "\n".join(f"⚠ {x['label']} ({x['id']}) — {x['advice']}"
                             for x in b) or "none")


def cmd_recall(args):
    hits = api.recall(args.name, args.id)
    _emit(args, hits, "\n".join(
        f"{'✓' if h['confirmed'] else '?'} {h['technique']} "
        f"(seen {h['seen']}x in {', '.join(h['engagements'])})"
        for h in hits) or "no history for a node like this")


def cmd_suggest(args):
    sug = api.suggestions(args.name)
    if getattr(args, "json", False):
        print(json.dumps(sug, indent=2, default=str))
        return
    if not sug:
        print("no suggestions")
        return
    for nid, hits in sug.items():
        print(f"{nid}:")
        for h in hits:
            print(f"  {'✓' if h['confirmed'] else '?'} {h['technique']} "
                  f"({', '.join(h['engagements'])})")


def cmd_delta(args):
    d = api.delta(args.name, since=args.since)
    if getattr(args, "json", False):
        print(json.dumps(d, indent=2, default=str))
        return
    if not d["events"]:
        print(f"nothing new since seq {d['from_seq']}")
        return
    print(f"# Since seq {d['from_seq']} → {d['to_seq']} ({d['events']} events)\n")
    def show(title, items, fmt):
        if items:
            print(title)
            for i in items:
                print("  " + fmt(i))
            print()
    show("★ NEWLY WINNABLE", d["newly_winnable"], lambda i: f"{i['label']} ({i['id']})")
    show("⚠ new unrealized", d["new_unrealized"], lambda i: f"{i['label']}")
    show("⚠ new unmined", d["new_unmined"], lambda i: f"{i['label']}")
    show("✓ cleared unmined", d["cleared_unmined"], lambda i: f"{i['label']}")
    show("⚠ new unverified", d["new_stale"], lambda i: f"{i['label']}")
    show("resolved", d["resolved"], lambda i: f"{i['id']}: {i['from']} → {i['to']}")
    show("new nodes", d["new_nodes"], lambda i: f"{i['kind']} {i['label']}")
    show("decisions", d["decisions"], lambda i: f"{i['chose']} — {i['reason']}")
    show("⚠ BUDGET BLOWN", d["budget_blown"], lambda i: f"{i['label']}: {i['advice']}")


def cmd_mcp(args):
    from . import mcp
    mcp.serve()


def cmd_retro(args):
    print(retro.render(args.name))


def cmd_log(args):
    for ev in store.read_events(args.name)[-args.n:]:
        a = ev.get("args", {})
        print(f"{ev['seq']:>4} {ev['op']:<18} "
              f"{a.get('id') or a.get('target_id') or a.get('old_id') or ''}")


def cmd_ls(args):
    for n in store.list_engagements():
        g = store.load(n)
        print(f"{n:<20} seq={g.seq:<5} nodes={len(g.nodes)} edges={len(g.edges)}")


def build_parser():
    p = argparse.ArgumentParser(prog="reckon", description="engagement graph")
    p.add_argument("--version", action="version", version=f"reckon {__version__}")
    p.add_argument("-e", "--name", default=os.environ.get("RECKON_CURRENT", "default"),
                   help="engagement name (or $RECKON_CURRENT)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("new"); s.add_argument("name_pos", metavar="name")
    s.add_argument("--force", action="store_true"); s.set_defaults(func=cmd_new)
    s = sub.add_parser("ls"); s.set_defaults(func=cmd_ls)

    s = sub.add_parser("add", help="add a node")
    s.add_argument("kind", choices=KINDS); s.add_argument("label")
    s.add_argument("--id"); s.add_argument("--state", choices=EPISTEMIC,
                                           default="unexplored")
    s.add_argument("--exploitation", choices=EXPLOITATION, default="discovered")
    s.add_argument("--conf"); s.add_argument("--source")
    s.add_argument("-p", "--props", nargs="*")
    s.add_argument("--requires", nargs="*", metavar="NODE@RANK",
                   help="objectives: what access satisfies this, e.g. host:dc01@3")
    s.add_argument("--crown", action="store_true")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("edge"); s.add_argument("src")
    s.add_argument("rel", choices=RELS); s.add_argument("dst")
    s.add_argument("--id"); s.add_argument("--state", choices=EPISTEMIC,
                                           default="hypothesized")
    s.add_argument("--conf"); s.add_argument("-p", "--props", nargs="*")
    s.set_defaults(func=cmd_edge)

    s = sub.add_parser("state"); s.add_argument("id")
    s.add_argument("state", choices=EPISTEMIC); s.add_argument("--conf")
    s.add_argument("--source"); s.set_defaults(func=cmd_state)

    s = sub.add_parser("hold"); s.add_argument("id")
    s.add_argument("state", choices=EXPLOITATION); s.set_defaults(func=cmd_hold)

    s = sub.add_parser("examine"); s.add_argument("id")
    s.add_argument("outcome", nargs="?", default=""); s.set_defaults(func=cmd_examine)

    s = sub.add_parser("obj"); s.add_argument("id")
    s.add_argument("status", choices=api.OBJECTIVE_STATUS)
    s.set_defaults(func=cmd_obj)

    s = sub.add_parser("note"); s.add_argument("id"); s.add_argument("text")
    s.set_defaults(func=cmd_note)

    s = sub.add_parser("supersede"); s.add_argument("old"); s.add_argument("new")
    s.add_argument("reason", nargs="?", default=""); s.set_defaults(func=cmd_supersede)

    s = sub.add_parser("ref", help="link a node to the reference layer")
    s.add_argument("id"); s.add_argument("store", choices=("neo4j", "chroma"))
    s.add_argument("label"); s.add_argument("key"); s.set_defaults(func=cmd_ref)

    s = sub.add_parser("apply"); s.add_argument("file"); s.set_defaults(func=cmd_apply)

    s = sub.add_parser("import"); s.add_argument("path")
    s.add_argument("--fresh", action="store_true"); s.set_defaults(func=cmd_import)

    for nm, fn in (("board", cmd_board), ("retro", cmd_retro),
                   ("status", cmd_status)):
        s = sub.add_parser(nm)
        if nm != "retro":
            s.add_argument("--redact", action="store_true")
        s.set_defaults(func=fn)

    for nm, fn in (("frontier", cmd_frontier), ("unrealized", cmd_unrealized),
                   ("unmined", cmd_unmined), ("stale", cmd_stale),
                   ("queue", cmd_queue)):
        s = sub.add_parser(nm); s.add_argument("--json", action="store_true")
        s.add_argument("--redact", action="store_true"); s.set_defaults(func=fn)

    s = sub.add_parser("why"); s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s.add_argument("--redact", action="store_true"); s.set_defaults(func=cmd_why)

    s = sub.add_parser("views"); s.add_argument("--out")
    s.add_argument("--redact", action="store_true"); s.set_defaults(func=cmd_views)

    s = sub.add_parser("console"); s.add_argument("--out")
    s.add_argument("--redact", action="store_true"); s.set_defaults(func=cmd_console)

    s = sub.add_parser("log"); s.add_argument("-n", type=int, default=30)
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("decide", help="record a decision and what it ruled out")
    s.add_argument("chose"); s.add_argument("--reason", default="")
    s.add_argument("--rejected", nargs="*"); s.add_argument("--about")
    s.set_defaults(func=cmd_decide)

    s = sub.add_parser("attempt", help="record an attempt (feeds the failure budget)")
    s.add_argument("id"); s.add_argument("outcome", nargs="?", default="failed",
                                         choices=api.ATTEMPT_OUTCOMES)
    s.add_argument("note", nargs="?", default=""); s.set_defaults(func=cmd_attempt)

    s = sub.add_parser("budget", help="approaches that burned the failure budget")
    s.add_argument("--limit", type=int, default=2)
    s.add_argument("--json", action="store_true")
    s.add_argument("--redact", action="store_true"); s.set_defaults(func=cmd_budget)

    # `plan` nests, because `plan add` and `plan supersede` are different verbs
    # on one noun; steps stay flat since `step done <plan> 2` is the hot path.
    s = sub.add_parser("plan", help="an ordered path to an objective")
    psub = s.add_subparsers(dest="plan_cmd", required=True)

    q = psub.add_parser("add", help="attach a plan to an objective")
    q.add_argument("objective"); q.add_argument("title")
    q.add_argument("--step", action="append", help="repeatable, in order")
    q.add_argument("--id", help="explicit plan id")
    q.add_argument("--supersedes", help="the active plan this replaces")
    q.add_argument("--agent"); q.set_defaults(func=cmd_plan_add)

    q = psub.add_parser("show", help="the steps and where the cursor is")
    q.add_argument("plan"); q.add_argument("--json", action="store_true")
    q.add_argument("--redact", action="store_true")
    q.set_defaults(func=cmd_plan_show)

    q = psub.add_parser("supersede", help="replace a plan, keeping it readable")
    q.add_argument("old"); q.add_argument("new")
    q.add_argument("--reason", default=""); q.set_defaults(func=cmd_plan_supersede)

    q = psub.add_parser("step", help="append a step to an existing plan")
    q.add_argument("plan"); q.add_argument("text")
    q.add_argument("--command"); q.add_argument("--ordinal", type=int)
    q.add_argument("--agent"); q.set_defaults(func=cmd_step_add)

    q = psub.add_parser("reassign", help="hand a plan to another agent")
    q.add_argument("plan"); q.add_argument("to_agent", metavar="agent")
    q.add_argument("--reason", default=""); q.set_defaults(func=cmd_plan_reassign)

    s = sub.add_parser("handoff", help="the successor brief — resume point first")
    s.add_argument("--agent", help="that agent's resume point only")
    s.add_argument("--all", action="store_true",
                   help="every active plan, stalled first")
    s.add_argument("--out", help="write to a file instead of stdout")
    s.add_argument("--json", action="store_true")
    s.add_argument("--redact", action="store_true"); s.set_defaults(func=cmd_handoff)

    s = sub.add_parser("fleet", help="where every agent is right now")
    s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_fleet)

    s = sub.add_parser("checkpoint",
                       help="the ritual: delta, alarms, regenerate, stamp")
    s.add_argument("--no-render", action="store_true",
                   help="alarms + delta only, skip regeneration")
    s.add_argument("--strict", action="store_true",
                   help="exit 2 if a recording-health alarm fires")
    s.add_argument("--dry-run", action="store_true",
                   help="everything, without stamping")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_checkpoint)

    s = sub.add_parser("alarms", help="the deterministic alarm set")
    s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_alarms)

    # Harness-invoked. Every one of these exits 0 whatever it finds: a hook that
    # fails loudly takes a session down with it.
    s = sub.add_parser("hook", help="harness-invoked entry points (fail open)")
    hsub = s.add_subparsers(dest="hook_cmd", required=True)

    q = hsub.add_parser("session-start",
                        help="print the resume brief, or nothing")
    q.add_argument("--agent"); q.add_argument("--redact", action="store_true")
    q.set_defaults(func=cmd_hook_session_start)

    q = hsub.add_parser("stop", help="stamp a checkpoint at session end")
    q.set_defaults(func=cmd_hook_stop)

    q = hsub.add_parser("config",
                        help="the .claude/settings.json fragment to paste")
    q.add_argument("--pin", action="store_true",
                   help="pin RECKON_CURRENT to this engagement")
    q.set_defaults(func=cmd_hook_config)

    s = sub.add_parser("plans", help="active plans and where each stands")
    s.add_argument("--all", action="store_true", help="include superseded")
    s.add_argument("--json", action="store_true")
    s.add_argument("--redact", action="store_true"); s.set_defaults(func=cmd_plans)

    s = sub.add_parser("step", help="move a step: pending|running|done|blocked|skipped")
    s.add_argument("status", choices=STEP_STATUS)
    s.add_argument("plan"); s.add_argument("step", help="ordinal or step id")
    s.add_argument("--note", default="")
    s.add_argument("--reason", choices=BLOCKED_REASONS,
                   help="required when blocking; refused otherwise")
    s.add_argument("--produced", nargs="*", help="node ids this step created")
    s.add_argument("--agent"); s.set_defaults(func=cmd_step)

    s = sub.add_parser("change", help="record a modification made to the target")
    s.add_argument("target"); s.add_argument("what")
    s.add_argument("--revert", help="the command or step that undoes it")
    s.add_argument("--irreversible", action="store_true")
    s.add_argument("--agent"); s.set_defaults(func=cmd_change)

    s = sub.add_parser("changes", help="outstanding RoE cleanup")
    s.add_argument("--all", action="store_true", help="include cleaned entries")
    s.add_argument("--json", action="store_true")
    s.add_argument("--redact", action="store_true"); s.set_defaults(func=cmd_changes)

    s = sub.add_parser("cleaned", help="mark a recorded change reverted")
    s.add_argument("change_id"); s.add_argument("--agent")
    s.set_defaults(func=cmd_cleaned)

    s = sub.add_parser("delta", help="what changed since you last looked")
    s.add_argument("--since", type=int); s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_delta)

    s = sub.add_parser("recall", help="techniques used on nodes like this before")
    s.add_argument("id"); s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_recall)

    s = sub.add_parser("suggest", help="recall for everything reachable now")
    s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_suggest)

    s = sub.add_parser("mcp", help="run the MCP server on stdio")
    s.set_defaults(func=cmd_mcp)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (api.ValidationError, store.StoreError, ingest.IngestError,
            FileNotFoundError) as exc:
        sys.exit(f"reckon: {exc}")


if __name__ == "__main__":
    main()
