"""Shared test helpers.

Only one lives here, and it is here because the rule it encodes is subtle
enough that stating it twice would mean stating it differently.
"""

import json
from datetime import datetime, timedelta, timezone

from reckon import store


def append_trace(name, *cmds, at=None, tool="Bash", exit_code=0, agent="a1",
                 cwd="/home/kali"):
    """Append trace lines exactly as the §5.2 `PostToolUse` hook would.

    The default stamp is **one second after the newest event in the log**, not
    "now". A3 compares the trace against the last authored event at second
    resolution, and a test that runs in under a millisecond would otherwise
    write "activity" bearing the same timestamp as the recording it is meant to
    postdate — passing or failing on how long the setup took. Pass `at` for the
    cases that need an explicit position on the clock.
    """
    if at is None:
        latest = None
        for ev in store.read_events(name):
            try:
                when = datetime.fromisoformat(str(ev.get("ts", "")))
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            latest = when if latest is None else max(latest, when)
        now = datetime.now(timezone.utc)
        at = now if latest is None else max(now, latest + timedelta(seconds=1))
    stamp = at.strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(store.trace_path_for(name), "a", encoding="utf-8") as fh:
        for cmd in cmds:
            fh.write(json.dumps({"ts": stamp, "tool": tool, "cmd": cmd,
                                 "exit": exit_code, "cwd": cwd,
                                 "agent": agent}) + "\n")
    return stamp
