"""Hook contracts — SPEC-004 §5.1, §5.2 and §5.3.

A fourth entry point, alongside the CLI, the MCP server and `api`. What makes it
its own module is not the logic — there is barely any — but a guarantee the
other three deliberately do not make.

**Everywhere else in reckon, a failure is loud.** `reckon state host:TYPO
verified` refuses with a reason, because a tool whose purpose is catching what
you missed must never quietly miss. Hooks invert that, and the inversion is the
whole point: these run on the harness's schedule, not the agent's, so a hook that
fails loudly takes a session down with it. A broken reckon, a missing engagement,
a corrupt log, a half-written config — none of them may stop a session from
starting or stopping.

So every function here swallows everything and returns empty. That is a
deliberate exception to the house rule, confined to this file, and it is why the
file exists rather than the two calls living in `cli`.

The distinction that motivates the whole spec applies here too: a rule in a
prompt asking an agent to fetch its own handoff is probabilistic and fails
silently. A hook fires whether or not anything remembers — which is precisely the
remembering that does not happen when a session dies unexpectedly.

reckon defines the CONTRACT: what runs, what it may print, and that it must fail
open. Installing it belongs to the operator, in `.claude/settings.json`, so the
two can change independently.
"""

from . import api, store

# The commands the harness runs. `|| true` is belt-and-braces: the functions
# below already fail open, but the shell still has to survive reckon being
# absent from PATH entirely, which no amount of Python can catch.
SESSION_START_COMMAND = (
    'reckon hook session-start --agent "$RECKON_AGENT" 2>/dev/null || true')
STOP_COMMAND = "reckon hook stop >/dev/null 2>&1 || true"

# --- the PostToolUse trace (§5.2) --------------------------------------------
#
# The one hook that is NOT a reckon command, and the four constraints below are
# why. It runs on *every* tool call, so a ~100 ms interpreter start would make
# the harness feel broken; it appends to a separate file from the event log; it
# takes no lock, which is only safe because the line is capped under PIPE_BUF;
# and nothing it writes is ever folded into the graph.
#
# Escaping is the whole problem: the harness hands us a JSON object on stdin and
# a command inside it can carry quotes, backslashes and newlines. `jq` does the
# decode, the truncation and the single-line encode in one pass, and its ~10 ms
# start is nothing like the interpreter spawn the constraint exists to avoid.
#
# **With no jq, we write nothing and exit 0.** An absent trace is a state A3
# already handles — it stays quiet, which is the honest reading of "no evidence
# either way". A hand-rolled escaper that gets one input wrong instead writes a
# corrupt line into a file the reader must treat as evidence, and an alarm
# reporting on garbage is worse than one that is honestly silent. Failing open
# beats failing dirty, which is this file's rule.
#
# The size cap is enforced *inside* jq rather than by piping through `cut`,
# because a JSON line truncated from the outside stops being JSON. `cmd` is cut
# to 1500 characters first; the loop then shortens it by a quarter at a time
# until the encoded line fits, which terminates because the shrink is strictly
# decreasing and the line without any `cmd` at all always fits.
_TRACE_JQ = (
    'if type!="object" then empty else '
    '{ts:(now|todate),'
    'tool:(.tool_name//""|tostring|.[0:80]),'
    'cmd:((if (.tool_input|type)=="object" then '
    '(.tool_input.command//.tool_input.file_path//.tool_input.path//"") '
    'else "" end)|tostring|.[0:1500]),'
    # UNCONFIRMED against a live harness: whether a Bash call arrives with
    # `exit_code`, `exitCode` or neither has not been checked on a real install,
    # so this may be recording 0 for everything. Left as it is deliberately —
    # more guessed fallbacks would not make it more true, and a wrong value here
    # is cosmetic: A3 counts calls and never reads `exit`. It settles on the
    # first real install, which is the right time to settle it.
    'exit:((if (.tool_response|type)=="object" then '
    '(.tool_response.exit_code//.tool_response.exitCode//0) else 0 end)'
    '|if type=="number" then . else 0 end),'
    'cwd:(.cwd//""|tostring|.[0:300]),'
    'agent:($a|.[0:80])}'
    '|until((tojson|utf8bytelength)<=3990;.cmd|=.[0:(length*3/4|floor)]) end')

# `case` first: the engagement name comes from the environment, where nothing
# validates it, and `store.path_for` would refuse the same three shapes. An
# empty or absent RECKON_CURRENT is simply nothing to trace, not an error.
# No `mkdir`: if the engagement exists its directory does, and a fork saved here
# is a fork saved on every tool call.
POST_TOOL_USE_COMMAND = (
    'case "${RECKON_CURRENT:-}" in \'\'|.*|*/*) exit 0;; esac; '
    'command -v jq >/dev/null 2>&1 && '
    'jq -c --arg a "${RECKON_AGENT:-}" \'' + _TRACE_JQ + '\' '
    '>> "${RECKON_HOME:-$HOME/projects/reckon}/engagements/'
    '$RECKON_CURRENT.trace.jsonl" 2>/dev/null || true')


TRACE_DEPENDENCY = "jq"


def trace_precondition() -> str:
    """"" if the trace can be written, else the warning to print. Never raises.

    The fallback in `POST_TOOL_USE_COMMAND` is right — with no jq, writing
    nothing beats writing a corrupt line into a file the reader must treat as
    evidence. What is not right is nobody being told. The hook would no-op on
    every tool call, A3 would never fire, and a permanently silent alarm reads
    exactly like a well-recorded engagement.

    That inverts the whole argument (§1: every mechanism is chosen so the
    failure is *loud* instead). A3 is the loud-failure mechanism, so its own
    precondition cannot be the quietest thing here. This is what makes it
    audible, at the one moment it is cheap to fix: installing the hook.
    """
    try:
        import shutil
        if shutil.which(TRACE_DEPENDENCY):
            return ""
        return (
            f"warning: {TRACE_DEPENDENCY} is not on PATH, so the PostToolUse "
            "hook will write no trace at all.\n"
            "  Without a trace, A3 unrecorded-work can never fire, and "
            "checkpoint cannot tell a quiet\n"
            "  stretch from an unrecorded one — the alarm stays silent and "
            "silence reads like health.\n"
            f"  fix: install {TRACE_DEPENDENCY} (apt install {TRACE_DEPENDENCY}"
            "), then re-run this command.")
    except Exception:
        return ""


def session_start(name, agent=None, redact=False) -> str:
    """The resume brief, for injection at session start. Never raises.

    A new session begins already holding the cursor, what prior steps produced,
    why the last one stopped and what is owed — without anyone having to
    remember to ask, which is exactly the ask that gets forgotten when the
    previous session died mid-step.

    Returns "" when there is nothing to say or anything at all goes wrong.
    """
    try:
        if not name or not store.exists(name):
            return ""
        agent = agent or None          # an unset $RECKON_AGENT arrives as ""
        try:
            h = api.handoff(name, agent=agent)
        except api.AmbiguousHandoff:
            # Several plans live and nobody named. Refusing is right for an
            # operator at a prompt, who can then name one; for a starting
            # session it would mean beginning with nothing, so show them all
            # and let the reader pick. A brief with three resume points beats
            # no brief.
            h = api.handoff(name, all_agents=True)
        if redact:
            from .redact import redact_obj
            h = redact_obj(h)
        from .render.handoff import handoff as render
        return render(h)
    except Exception:
        return ""


def stop(name):
    """Stamp a checkpoint at session end. Never raises.

    Guarantees a stamped marker and a final alarm evaluation even when a session
    ends abruptly, so the NEXT SessionStart brief reflects where the work
    actually stopped rather than whenever someone last ran a checkpoint by hand.

    Deliberately `render=False`: this runs on every session end, and regenerating
    the console and six views on the way out is latency the operator did not ask
    for. Returns the checkpoint dict, or None if anything went wrong.
    """
    try:
        if not name or not store.exists(name):
            return None
        return api.checkpoint(name, render=False)
    except Exception:
        return None


def settings_fragment(engagement=None) -> dict:
    """The `.claude/settings.json` fragment an operator pastes in.

    Emitted rather than installed: reckon does not write to the harness's
    configuration, because a tool that edits the config that invokes it is a
    tool you cannot reason about when it misbehaves.

    `PostToolUse` matches `*` on purpose. The trace is an independent signal of
    *activity*, and narrowing it to Bash would silently agree that reading forty
    files is not work — which is precisely the judgment A3 exists to avoid
    making.
    """
    env = {"RECKON_CURRENT": engagement} if engagement else {}
    frag = {
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command",
                            "command": SESSION_START_COMMAND}]}],
            "PostToolUse": [
                {"matcher": "*",
                 "hooks": [{"type": "command",
                            "command": POST_TOOL_USE_COMMAND}]}],
            "Stop": [
                {"hooks": [{"type": "command", "command": STOP_COMMAND}]}],
        }
    }
    if env:
        frag["env"] = env
    return frag
