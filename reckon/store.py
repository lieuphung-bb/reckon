"""Append-only JSONL event store.

Plain text on purpose: an agent can read the state file directly and append
events with a file write, with no CLI round-trip and no shell escaping. That
ergonomic property is the difference between a tool that gets used mid-engagement
and one that doesn't.

Three properties this file is responsible for:

  * **Single-writer safety.** Phase 2 puts an MCP server alongside the CLI, so two
    processes will append to the same log. Every write takes an exclusive flock,
    so sequence numbers cannot collide and lines cannot interleave.
  * **O(1) appends.** Sequence used to be derived by parsing the whole log, which
    made a session quadratic in its own length. It now reads the tail only.
  * **Forward compatibility.** Every event carries a schema version, and loading a
    log written by a newer version fails loudly instead of silently misreading it.
"""

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from .model import fold

try:                                        # POSIX only; degrade rather than fail
    import fcntl
except ImportError:                         # pragma: no cover
    fcntl = None

# v2 (0.5.0) adds the `change`/`cleaned` ops and an optional `by` on the event
# envelope. Both are additive: a v1 log folds to an identical graph, and `by` is
# omitted entirely when there is no agent, so events keep their v1 shape.
SCHEMA_VERSION = 2

def _resolve_home(env=None) -> str:
    """The data root: `$RECKON_HOME`, else `$XDG_DATA_HOME/reckon`, else
    `~/.local/share/reckon`.

    Outside the checkout on purpose. A clone that is also the store makes
    `rm -rf` on a source directory destroy an engagement record, and leaves a
    `.gitignore` as the only thing between live credentials and a push.
    """
    env = os.environ if env is None else env
    home = (env.get("RECKON_HOME") or "").strip()
    if home:
        return os.path.expanduser(home)
    xdg = (env.get("XDG_DATA_HOME") or "").strip()
    if not xdg:
        xdg = os.path.join(env.get("HOME") or os.path.expanduser("~"),
                           ".local", "share")
    return os.path.join(os.path.expanduser(xdg), "reckon")


def _resolve_out(env=None) -> str:
    """Where rendered artifacts land: `$RECKON_OUT`, else `<home>/out`.

    Separable from the log because the console is what someone else's machine
    reads, and unseparable in the other direction: single-writer safety rests on
    `flock`, which is unreliable over `hgfs` and NFS, so the log stays local
    while the output travels.
    """
    env = os.environ if env is None else env
    out = (env.get("RECKON_OUT") or "").strip()
    return os.path.expanduser(out) if out else os.path.join(
        _resolve_home(env), "out")


_TRUTHY = ("1", "true", "yes", "on")


def autorender_enabled(env=None) -> bool:
    """Whether a successful write should regenerate the board: `$RECKON_AUTORENDER`.

    Default off, so an unset variable is exactly today's behaviour and someone
    who does not keep a console open pays nothing.

    Read at call time rather than frozen into a module constant like `OUT`,
    because this one is a per-session preference — an operator turns it on for
    the session they are watching a board in, and a test turns it on for the
    case that needs it.
    """
    env = os.environ if env is None else env
    return (env.get("RECKON_AUTORENDER") or "").strip().lower() in _TRUTHY


RECKON_HOME = _resolve_home()
ENGAGEMENTS = os.path.join(RECKON_HOME, "engagements")
# The one place the output location is resolved. Callers read `OUT`; nothing
# else rebuilds `<home>/out`, so pointing the rendered half elsewhere is one
# change rather than three that can disagree.
OUT = _resolve_out()

_TAIL_BYTES = 8192


class StoreError(RuntimeError):
    pass


class SchemaTooNew(StoreError):
    pass


def path_for(name: str) -> str:
    if not name or "/" in name or name.startswith("."):
        raise StoreError(f"invalid engagement name: {name!r}")
    return os.path.join(ENGAGEMENTS, f"{name}.jsonl")


def trace_path_for(name: str) -> str:
    """The §5.2 tool-call trace — a SEPARATE file from the event log.

    Separate on three counts, and each is load-bearing: the volume is different
    (hundreds of tool calls against dozens of events), the trust is different
    (raw evidence against interpreted claims), and mixing them would make the
    log unreadable by the human it is plain text for. Nothing here ever folds
    into the graph.
    """
    path_for(name)                          # same name, same refusal
    return os.path.join(ENGAGEMENTS, f"{name}.trace.jsonl")


def exists(name: str) -> bool:
    return os.path.exists(path_for(name))


def read_trace(name: str) -> list:
    """Every parseable trace line, oldest first, each stamped with its 1-based
    position as `seq` so a reader can say "since here".

    **Tolerant, unlike `read_events`.** The event log is written by this
    process under a lock, so a corrupt line there is a real defect and refusing
    to read is correct. The trace is written by a shell one-liner on the
    harness's schedule: a truncated final line means the machine went down
    mid-append, not that the evidence is worthless. A skipped line costs one
    tool call of resolution in an alarm whose unit is "dozens"; refusing the
    file costs A3 entirely, which is the alarm that tells "quiet" from
    "unrecorded".
    """
    path = trace_path_for(name)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append({**rec, "seq": i})
    return out


@contextmanager
def _locked(path: str):
    """Exclusive advisory lock for the duration of a write."""
    if fcntl is None:                       # pragma: no cover
        yield
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _last_seq(path: str) -> int:
    """Highest seq in the log, read from the tail. O(1) in log length."""
    if not os.path.exists(path):
        return 0
    size = os.path.getsize(path)
    if size == 0:
        return 0
    with open(path, "rb") as fh:
        read = min(size, _TAIL_BYTES)
        fh.seek(size - read)
        data = fh.read(read)
    lines = [ln for ln in data.split(b"\n") if ln.strip()]
    for ln in reversed(lines):
        try:
            return int(json.loads(ln).get("seq", 0))
        except (ValueError, TypeError):
            continue
    # A single line longer than the tail window: fall back to a full scan.
    return max((e.get("seq", 0) for e in read_events_path(path)), default=0)


def read_events_path(path: str) -> list:
    if not os.path.exists(path):
        return []
    out, newer = [], 0
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError as exc:
                raise StoreError(f"{path}:{lineno}: corrupt event line: {exc}")
            v = int(ev.get("v", 1))
            if v > SCHEMA_VERSION:
                newer = max(newer, v)
            out.append(ev)
    if newer:
        raise SchemaTooNew(
            f"{path} was written by schema v{newer}; this build understands "
            f"v{SCHEMA_VERSION}. Upgrade reckon rather than reading it partially.")
    return out


def read_events(name: str) -> list:
    return read_events_path(path_for(name))


def next_seq(name: str) -> int:
    return _last_seq(path_for(name)) + 1


def append(name: str, op: str, args: dict, by: str | None = None) -> dict:
    """Append one event under an exclusive lock. Returns the event as written."""
    return append_many(name, [{"op": op, "args": args}], by=by)[0]


def append_many(name: str, events: list, by: str | None = None) -> list:
    """Append a batch under ONE lock — a session's worth of events is atomic.

    `by` is authorship: which agent wrote this. It is what makes "the last
    authored event" answerable, so a fleet view can show an agent that has gone
    quiet. A per-event `by` overrides the batch default.
    """
    path = path_for(name)
    os.makedirs(ENGAGEMENTS, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = []
    with _locked(path):
        seq = _last_seq(path)
        with open(path, "a", encoding="utf-8") as fh:
            for e in events:
                seq += 1
                ev = {"seq": seq, "ts": ts, "v": SCHEMA_VERSION,
                      "op": e["op"], "args": e.get("args", {})}
                author = e.get("by", by)
                if author:                      # omitted when absent: v1 shape
                    ev["by"] = author
                fh.write(json.dumps(ev, sort_keys=True, ensure_ascii=False) + "\n")
                written.append(ev)
            fh.flush()
            os.fsync(fh.fileno())
    return written


def create(name: str, force: bool = False) -> str:
    path = path_for(name)
    if os.path.exists(path) and not force:
        raise StoreError(f"engagement exists: {path}")
    os.makedirs(ENGAGEMENTS, exist_ok=True)
    with _locked(path):
        with open(path, "w"):
            pass
    return path


def load(name: str):
    return fold(read_events(name))


def snapshot_at(name: str, seq: int):
    """State as it was known at `seq` — what makes the retro metrics computable."""
    return fold([e for e in read_events(name) if e.get("seq", 0) <= seq])


def list_engagements() -> list:
    if not os.path.isdir(ENGAGEMENTS):
        return []
    # `<name>.trace.jsonl` sits in the same directory and also ends in .jsonl;
    # without this it would list as an engagement called "<name>.trace".
    return sorted(f[:-6] for f in os.listdir(ENGAGEMENTS)
                  if f.endswith(".jsonl") and not f.endswith(".trace.jsonl"))
