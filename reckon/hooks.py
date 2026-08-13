"""Hook contracts — SPEC-004 §5.1 and §5.3.

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
    """
    env = {"RECKON_CURRENT": engagement} if engagement else {}
    frag = {
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command",
                            "command": SESSION_START_COMMAND}]}],
            "Stop": [
                {"hooks": [{"type": "command", "command": STOP_COMMAND}]}],
        }
    }
    if env:
        frag["env"] = env
    return frag
