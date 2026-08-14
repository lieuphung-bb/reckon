"""The reference layer seam, and its first resolver: a file.

The engagement graph is the LIVE layer: small, mutating hourly, owned here. The
reference layer is persistent, curated, and lives outside this tool. This module
is the boundary between them, and it is deliberately implementation-free: the
core must never import a database driver or an MCP client.

**A source is one of two kinds, and that asymmetry is the whole design.**

    deterministic  resolves a stable id to a canonical name. The engagement
                   graph stores only the reference, so a fact arrives with the
                   reliability of a curated taxonomy and may be recorded as
                   VERIFIED. A Neo4j lookup is one; a markdown id table is
                   another, and they are the SAME role, not two tiers of it.
                   How much of the triple a backend needs is its own business:
                   Neo4j keys on (label, key), a file on the key alone.

    semantic       no stable ids to join on. It is a retrieval surface, never a
                   join target. A similarity hit is weak evidence about
                   relevance, so it enters as a HYPOTHESIS with low confidence
                   and spawns a verification task - never as a fact.

That rule is `confidence = source reliability` applied to a retrieval mechanism
rather than to a person, and it is what stops a vector search from quietly
promoting itself into the plan.

**Why a file resolves first.** The deterministic half needs exactly one thing — a
source of stable ids with canonical names — and a markdown table
supplies that as well as a graph does, without trading away "stdlib only, no
services" for a few hundred entries. The path is configuration, defaulting to
none, so with nothing set the core behaves exactly as it did with `NullResolver`.

**reckon ships the reader, never the corpus.** A source is something the operator
points `$RECKON_REFERENCES` at. Vendoring one in would buy an obligation to keep
someone else's catalog current.
"""

import os
import re
from typing import Protocol, runtime_checkable

# Stores the engagement graph may point at without any configuration. Extend
# deliberately: every entry here is a trust decision, not a connection string.
# Configured file sources add their own names alongside these (see known_stores).
STORES = ("neo4j", "chroma")

# Node labels the Neo4j reference graph keys on, with their unique property.
# Taken from the live schema's uniqueness constraints, so a reference that does
# not name one of these cannot resolve.
NEO4J_KEYS = {
    "CVE": "Name",
    "CWE": "Name",
    "CAPEC": "Name",
    "CPE": "uri",
    "CVSS_3": "Name",
    "CVSS_2": "Name",
    "CWE_VIEW": "ViewID",
    "CAPEC_VIEW": "ViewID",
    "Mitigation": "Description",
    "Detection_Method": "Method",
    "Consequence": "Scope",
}

# Retrieval hits are never better than this, however convincing the text looks.
RETRIEVAL_CONFIDENCE = "D"


class ReferenceError(ValueError):
    pass


def make_reference(store: str, label: str, key: str, resolver=None) -> dict:
    """Validate and normalise a reference to the external layer.

    Validation is the point. A reference that names an id its source does not
    contain is decorative — it renders as provenance and resolves to nothing
    forever. So a store that can be asked is asked HERE, at write time, and a
    miss is refused loudly rather than recorded quietly.

    `resolver` defaults to the configured one; pass it only to validate against
    a specific source.
    """
    resolver = get_resolver() if resolver is None else resolver
    resolved = tuple(resolver.stores())
    if store not in STORES and store not in resolved:
        raise ReferenceError(
            f"unknown store {store!r}; expected one of "
            f"{tuple(sorted(set(STORES) | set(resolved)))}")
    if not key:
        raise ReferenceError("reference key must not be empty")
    if store == "neo4j" and label not in NEO4J_KEYS:
        raise ReferenceError(
            f"unknown neo4j label {label!r}; expected one of "
            f"{sorted(NEO4J_KEYS)}")
    if store in resolved and not resolver.resolve(store, label, key):
        raise ReferenceError(
            f"{store} has no {key!r} — check the id, or the source "
            f"$RECKON_REFERENCES points at")
    return {"store": store, "label": label, "key": key}


@runtime_checkable
class Resolver(Protocol):
    """The one seam every reference source comes through.

    Read-only by design. Engagement learnings do not flow back automatically;
    promotion into the curated layer stays an explicit, human-reviewed step, so a
    messy live engagement can never pollute reference knowledge.

    Implement all three and a new source — a file, a graph, an MCP service —
    needs no change in `api`, the CLI or the console. `stores()` is what makes
    that hold: it is how validation and rendering learn which names this resolver
    answers for, without either of them naming a backend.
    """

    def stores(self) -> tuple:
        """Store names this resolver answers deterministically for."""

    def resolve(self, store: str, label: str, key: str) -> dict:
        """(store, label, key) -> {"title", "summary", "link", ...} or {}.

        Never raises: an unknown store, an unknown id and an unreadable source
        are all the same answer to the caller — nothing known.
        """

    def search(self, query: str, limit: int = 5) -> list:
        """Semantic retrieval -> [{"text", "score", "source"}], or []."""


class NullResolver:
    """Default. Returns nothing, so the core runs identically with no reference
    layer configured — which is how reckon ships out of the box."""

    def stores(self) -> tuple:
        return ()

    def resolve(self, store: str, label: str, key: str) -> dict:
        return {}

    def search(self, query: str, limit: int = 5) -> list:
        return []


# --- the file resolver --------------------------------------------------------

# An INDEX ROW, and nothing else. Exactly two cells, and the first is a single
# code span holding one whitespace-free token:
#
#     | `AML.T0003` | Search Victim-Owned Websites |     <- an id
#     | Reconnaissance of a target's public site | ... | <- prose, not an id
#
# Both rules carry weight, because a real source is a document, not a data file:
# it also contains prose tables whose first column is a sentence, and wider
# tables that happen to start with a code span. Ingesting either would mint ids
# that resolve to nothing and validate references that should have been refused.
# Requiring the code span rejects the sentence; requiring exactly two columns
# rejects the wide table. A row that is genuinely an id entry costs nothing to
# write in that shape.
_ROW = re.compile(r"^\|([^|]*)\|([^|]*)\|\s*$")
_ID_CELL = re.compile(r"^`([^`\s]+)`$")

# The environment names sources as `store=path`, separated like $PATH:
#
#     RECKON_REFERENCES="atlas=~/ref/atlas.md:owasp=~/ref/owasp.md"
#
# `store` is the name that appears in `reckon ref <node> <store> <label> <key>`,
# so it should read as one: `atlas`. Two delimiters for two concepts, and no
# third: an env format is sticky once published, and anything not guessable from
# one example gets guessed wrong.
REFERENCES_ENV = "RECKON_REFERENCES"


def parse_index(text: str) -> dict:
    """Markdown -> {id: canonical name}, keeping only rows shaped like ids.

    First occurrence wins: a source may well name an id again in prose further
    down, and the index table is the definition.
    """
    index = {}
    for line in text.splitlines():
        row = _ROW.match(line.strip())
        if not row:
            continue
        cell = _ID_CELL.match(row.group(1).strip())
        name = row.group(2).strip()
        if cell and name:
            index.setdefault(cell.group(1), name)
    return index


class FileResolver:
    """Deterministic lookup against markdown id tables the operator points at.

    This occupies the same role a graph would: a fact the engagement graph can
    point at, citable and resolvable by its id. It is not the lesser
    half of a semantic store — `search` returns [] because retrieval is a
    separate capability that does not exist yet, and an empty list is the honest
    answer rather than a stub to fill in.

    Sources are parsed on first use and cached. A node render asks for every
    reference it draws, so re-reading per call would make the console's cost
    scale with the drawer.
    """

    def __init__(self, sources: dict):
        # {store: path}
        self._sources = dict(sources)
        self._index = {}

    def stores(self) -> tuple:
        return tuple(self._sources)

    def index_for(self, store: str) -> dict:
        """The parsed source, loading it once. Unreadable degrades to empty.

        A configured path that has gone missing is a configuration problem, not
        a crash: every reference into it simply stops resolving, and refuses at
        write time, which is visible without taking the tool down.
        """
        if store not in self._sources:
            return {}
        if store not in self._index:
            path = self._sources[store]
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    self._index[store] = parse_index(fh.read())
            except OSError:
                self._index[store] = {}
        return self._index[store]

    def resolve(self, store: str, label: str, key: str) -> dict:
        """Keyed on the id alone; `label` is carried, never matched.

        A markdown index has one kind of thing in it, so a label to check
        against would be config that can only be wrong — and a mismatch would
        be indistinguishable from an id that is genuinely absent. The label is
        still recorded in the triple as provenance; it just is not a condition
        here. Neo4j is the store where one backend holds many labels, and its
        validation is unchanged.
        """
        if store not in self._sources:
            return {}
        name = self.index_for(store).get(key)
        if not name:
            return {}
        return {"title": name, "source": self._sources[store]}

    def search(self, query: str, limit: int = 5) -> list:
        return []


def sources_from_env(env=None) -> dict:
    """$RECKON_REFERENCES -> {store: path}.

    Lenient, like every other read: a malformed entry is skipped rather than
    taken as grounds to refuse to start. The loud half is at write time, where
    `reckon ref` into an unconfigured store names the stores that do exist.
    """
    env = os.environ if env is None else env
    raw = (env.get(REFERENCES_ENV) or "").strip()
    sources = {}
    for entry in raw.split(os.pathsep):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        name, _, path = entry.partition("=")
        store = name.strip()
        path = os.path.expanduser(os.path.expandvars(path.strip()))
        if not store or not path or store in STORES:
            continue                    # never let a file shadow a wired store
        if not os.path.isabs(path):
            from . import store as _store          # late: config, not a cycle
            path = os.path.join(_store.RECKON_HOME, path)
        sources[store] = path
    return sources


def resolver_from_env(env=None):
    """The configured resolver, or `NullResolver` when nothing is configured."""
    sources = sources_from_env(env)
    return FileResolver(sources) if sources else NullResolver()


_RESOLVER = None


def get_resolver():
    """The process-wide resolver, built from the environment on first use."""
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = resolver_from_env()
    return _RESOLVER


def set_resolver(resolver=None):
    """Install a resolver, or pass None to re-read the environment.

    This is the whole wiring story for a second backend: build it, install it.
    Nothing in `api`, the CLI or the console names a resolver class.
    """
    global _RESOLVER
    _RESOLVER = resolver
    return resolver


def known_stores() -> tuple:
    """Every store a reference may name right now, wired plus configured."""
    return tuple(sorted(set(STORES) | set(get_resolver().stores())))


def retrieval_to_events(hits: list, about: str, kind: str = "technique") -> list:
    """Chroma hits -> events, as HYPOTHESES.

    `about` is the engagement node the search was run for; each hit becomes a
    hypothesized node linked to it, carrying low confidence and the retrieval
    score. Nothing here can mark anything verified — that requires a test against
    the target, not a cosine distance.
    """
    events = []
    for i, hit in enumerate(hits):
        text = (hit.get("text") or "").strip()
        if not text:
            continue
        nid = f"{kind}:retrieved-{abs(hash(text)) % 10**8}"
        events.append({"op": "add_node", "args": {
            "id": nid, "kind": kind, "label": text[:80],
            "epistemic": "hypothesized",
            "confidence": RETRIEVAL_CONFIDENCE,
            "source": f"chroma:{hit.get('source', '?')}",
            "props": {"score": hit.get("score"), "rank": None,
                      "retrieved_for": about},
        }})
        events.append({"op": "add_edge", "args": {
            "id": f"e:retrieved-{i}-{nid}", "src": about, "dst": nid,
            "rel": "applies-technique", "epistemic": "hypothesized",
            "confidence": RETRIEVAL_CONFIDENCE}})
    return events
