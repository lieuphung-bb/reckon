"""Feature 3 — MCP server over stdio. The landing surface for an agent's output.

Without this, everything the agent produces reaches the graph only if a human
retypes it, and mid-engagement is exactly when typing is most expensive. With it,
recording is a tool call made while doing the work rather than a chore either
party has to remember.

Stdlib only: JSON-RPC 2.0 over stdin/stdout, no SDK. Every validation rule
already lives in `api`, so this file is a dispatch table, not logic - which is
the whole reason `api` was split out of the CLI in the first place.

Run:  reckon mcp          (or: python3 -m reckon mcp)
"""

import json
import sys
import traceback

from . import api, store
from .model import STEP_STATUS, BLOCKED_REASONS

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "reckon", "version": __import__("reckon").__version__}

_S = {"type": "string"}
_ENG = {"engagement": {**_S, "description": "engagement name; defaults to $RECKON_CURRENT"}}


def _tool(name, description, props, required):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object",
                            "properties": {**_ENG, **props},
                            "required": required}}


TOOLS = [
    # --- reads: what an agent should call before advising anything
    _tool("status", "Full engagement status: coverage, frontier, unrealized, "
                    "unmined, stale, verification queue, blown failure budgets, "
                    "recent decisions. Call this first.", {}, []),
    _tool("delta", "What changed since the last look. Prefer this over status "
                   "when resuming: it is fixed-size however large the engagement.",
          {"since": {"type": "integer", "description": "seq; omit for since-last-look"}},
          []),
    _tool("board", "The human-readable board as markdown.", {}, []),
    _tool("why", "Explain the path to an objective and what it rests on.",
          {"objective": _S}, ["objective"]),
    _tool("recall", "Techniques previously applied to nodes like this one, from "
                    "your own past engagements. Suggestions, never facts.",
          {"node": _S}, ["node"]),

    # --- writes: record as a side effect of doing the work
    _tool("add_node", "Record a thing: host, cred, service, artifact, finding, "
                      "assumption, objective, technique.",
          {"kind": _S, "label": _S, "id": _S,
           "epistemic": {**_S, "description": "unexplored|hypothesized|verified|refuted"},
           "exploitation": {**_S, "description": "discovered|acquired|examined|exhausted"},
           "confidence": {**_S, "description": "A-F, SOURCE RELIABILITY not probability"},
           "requires": {"type": "array", "items": _S,
                        "description": "objectives only, e.g. ['host:dc01@3']"},
           "crown": {"type": "boolean"}},
          ["kind", "label"]),
    _tool("add_edge", "Record a relationship. Mark it hypothesized until tested — "
                      "that is what builds the verification queue.",
          {"src": _S, "rel": _S, "dst": _S,
           "epistemic": _S, "confidence": _S,
           "rank": {"type": "integer", "description": "0 reach 1 app 2 shell 3 admin"},
           "privilege": _S},
          ["src", "rel", "dst"]),
    _tool("set_state", "Promote or kill a hypothesis on a node or edge.",
          {"id": _S, "state": _S, "confidence": _S}, ["id", "state"]),
    _tool("examine", "Mark an asset actually examined. The ONLY thing that clears "
                     "an unmined alarm — do not call it for a glance.",
          {"id": _S, "outcome": _S}, ["id"]),
    _tool("set_objective", "open | achieved | blocked.",
          {"id": _S, "status": _S}, ["id", "status"]),
    _tool("attempt", "Record an attempt. Two failures with no success blows the "
                     "budget and should trigger re-scoping the approach.",
          {"id": _S, "outcome": {**_S, "description": "failed|succeeded"},
           "note": _S}, ["id"]),
    _tool("decide", "Record what was chosen, what was ruled out, and why.",
          {"chose": _S, "reason": _S,
           "rejected": {"type": "array", "items": _S}, "about": _S}, ["chose"]),
    _tool("note", "Free narrative attached to a node.", {"id": _S, "text": _S},
          ["id", "text"]),
    _tool("checkpoint", "★ THIS is what to call when the operator says "
                        "\"update checkpoint\". One command: delta since the "
                        "last checkpoint, the deterministic alarm set, "
                        "regenerated documents, and a decision-shaped brief. "
                        "Do NOT reconstruct a checkpoint by sweeping documents "
                        "by hand — the documents are derived from the graph and "
                        "cannot drift from it. Read the recording-health "
                        "section first: if it fires, the rest of the picture is "
                        "behind the work.",
          {"render": {"type": "boolean",
                      "description": "regenerate console + views (default true)"},
           "dry_run": {"type": "boolean",
                       "description": "everything, without stamping"}},
          []),
    _tool("alarms", "The deterministic alarm set on its own: what is wrong with "
                    "the recording and with the engagement, computed from the "
                    "log rather than from anyone noticing.", {}, []),
    _tool("handoff", "★ CALL THIS FIRST when resuming or picking up an "
                     "engagement. Returns the resume brief: where in the "
                     "agreed plan you are, what earlier steps already produced "
                     "(so you do not redo them), why the last one stopped and "
                     "what that implies, plus position, next moves and "
                     "outstanding target changes. Starting with `status` "
                     "instead gives you position without procedure, and you "
                     "will re-derive a path that already exists. Pass your own "
                     "agent id.",
          {"agent": {**_S, "description": "your agent id; returns only your "
                                          "resume point"}},
          []),
    _tool("plan_add", "Attach an ordered plan to an objective, so the procedure "
                      "survives this session. One active plan per objective.",
          {"objective": _S, "title": _S,
           "steps": {"type": "array", "items": _S, "description": "in order"},
           "supersedes": {**_S, "description": "the active plan this replaces"},
           "agent": _S},
          ["objective", "title"]),
    _tool("step_state", "Move a step. On `done`, pass `produced` with the node "
                        "ids the step created — a step whose output is not in "
                        "the graph leaves a successor to redo the work. On "
                        "`blocked`, a reason is REQUIRED: "
                        "context-exhausted|refusal|timeout|target-state|"
                        "dependency|operator, because the correct next move "
                        "differs by cause.",
          {"plan": _S, "step": {**_S, "description": "ordinal or step id"},
           "status": {**_S, "description": "|".join(STEP_STATUS)},
           "note": _S, "blocked_reason": {**_S, "description": "|".join(BLOCKED_REASONS)},
           "produced": {"type": "array", "items": _S}, "agent": _S},
          ["plan", "step", "status"]),
    _tool("change", "Record a modification you made to the TARGET (a file dropped, "
                    "an account added). Two readers depend on it: whoever resumes "
                    "this engagement and must not re-do it, and whoever runs the "
                    "cleanup at close. Give a revert hint whenever one exists.",
          {"target": {**_S, "description": "node id where the change landed"},
           "what": _S, "revert_hint": _S,
           "reversible": {"type": "boolean"}, "agent": _S},
          ["target", "what"]),
]


def _eng(args):
    import os
    return args.get("engagement") or os.environ.get("RECKON_CURRENT", "default")


def dispatch(tool: str, args: dict):
    name = _eng(args)
    if tool == "status":
        return api.status(name)
    if tool == "delta":
        return api.delta(name, since=args.get("since"))
    if tool == "board":
        from .render.board import board
        return board(store.load(name), name)
    if tool == "why":
        return api.explain(name, args["objective"])
    if tool == "recall":
        return api.recall(name, args["node"])
    if tool == "add_node":
        return api.add_node(name, args["kind"], args["label"],
                            node_id=args.get("id"),
                            epistemic=args.get("epistemic", "unexplored"),
                            exploitation=args.get("exploitation", "discovered"),
                            confidence=args.get("confidence"),
                            requires=args.get("requires"),
                            crown=bool(args.get("crown")))
    if tool == "add_edge":
        props = {}
        if args.get("rank") is not None:
            props["rank"] = args["rank"]
        if args.get("privilege"):
            props["privilege"] = args["privilege"]
        return api.add_edge(name, args["src"], args["rel"], args["dst"],
                            epistemic=args.get("epistemic", "hypothesized"),
                            confidence=args.get("confidence"), props=props)
    if tool == "set_state":
        return api.set_epistemic(name, args["id"], args["state"],
                                 confidence=args.get("confidence"))
    if tool == "examine":
        return api.examine(name, args["id"], args.get("outcome", ""))
    if tool == "set_objective":
        return api.set_objective(name, args["id"], args["status"])
    if tool == "attempt":
        return api.attempt(name, args["id"], args.get("outcome", "failed"),
                           args.get("note", ""))
    if tool == "decide":
        return api.decide(name, args["chose"], args.get("reason", ""),
                          args.get("rejected"), args.get("about"))
    if tool == "note":
        return api.note(name, args["id"], args["text"])
    if tool == "checkpoint":
        from .render.checkpoint import checkpoint as render
        return render(api.checkpoint(name,
                                     render=args.get("render", True),
                                     dry_run=bool(args.get("dry_run"))))
    if tool == "alarms":
        return api.alarms(name)
    if tool == "handoff":
        # `plan_reassign` is deliberately absent from TOOLS: an agent taking
        # over another agent's plan unprompted is how two agents end up running
        # the same steps against one target. Operator-facing, CLI only.
        from .render.handoff import handoff as render
        return render(api.handoff(name, agent=args.get("agent")))
    if tool == "plan_add":
        return api.plan_add(name, args["objective"], args["title"],
                            steps=args.get("steps"),
                            supersedes=args.get("supersedes"),
                            agent=args.get("agent"))
    if tool == "step_state":
        return api.step_state(name, args["plan"], args["step"], args["status"],
                              note=args.get("note", ""),
                              blocked_reason=args.get("blocked_reason"),
                              produced=args.get("produced"),
                              agent=args.get("agent"))
    if tool == "change":
        return api.change(name, args["target"], args["what"],
                          reversible=bool(args.get("reversible", True)),
                          revert_hint=args.get("revert_hint", ""),
                          agent=args.get("agent"))
    raise api.ValidationError(f"unknown tool: {tool}")


def handle(msg: dict):
    """One JSON-RPC request -> one response dict, or None for a notification."""
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO}}
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            result = dispatch(params.get("name"), params.get("arguments") or {})
            text = result if isinstance(result, str) else json.dumps(
                result, indent=2, default=str)
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}]}}
        except Exception as exc:
            # A tool error is reported IN the result, not as a protocol error:
            # the agent should see "unknown node id: host:TYPO" and correct it,
            # not have the transport fail underneath it.
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "isError": True,
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}]}}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        try:
            response = handle(msg)
        except Exception:                      # never die on one bad message
            traceback.print_exc(file=sys.stderr)
            response = {"jsonrpc": "2.0", "id": msg.get("id"),
                        "error": {"code": -32603, "message": "internal error"}}
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
