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

RECKON_HOME = os.environ.get("RECKON_HOME", os.path.expanduser("~/projects/reckon"))
ENGAGEMENTS = os.path.join(RECKON_HOME, "engagements")

_TAIL_BYTES = 8192


class StoreError(RuntimeError):
    pass


class SchemaTooNew(StoreError):
    pass


def path_for(name: str) -> str:
    if not name or "/" in name or name.startswith("."):
        raise StoreError(f"invalid engagement name: {name!r}")
    return os.path.join(ENGAGEMENTS, f"{name}.jsonl")


def exists(name: str) -> bool:
    return os.path.exists(path_for(name))


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
    return sorted(f[:-6] for f in os.listdir(ENGAGEMENTS) if f.endswith(".jsonl"))
