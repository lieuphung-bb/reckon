"""The reference layer seam — phase 2.

The engagement graph is the LIVE layer: small, mutating hourly, owned here. The
reference layer is persistent, curated, and already exists elsewhere (a Neo4j
CVE/CWE/CAPEC graph, a ChromaDB technique KB). This module is the boundary
between them, and it is deliberately implementation-free: the core must never
import a database driver or an MCP client.

**The two stores are not symmetric, and that asymmetry is the design.**

    Neo4j     deterministic. Resolve by (label, key). The engagement graph stores
              only the pair, so a fact arrives with the reliability of a curated
              taxonomy and may be recorded as VERIFIED.

    ChromaDB  semantic only, no stable ids to join on. It is a retrieval surface,
              never a join target. A similarity hit is weak evidence about
              relevance, so it enters as a HYPOTHESIS with low confidence and
              spawns a verification task - never as a fact.

That rule is `confidence = source reliability` applied to a retrieval mechanism
rather than to a person, and it is what stops a vector search from quietly
promoting itself into the plan.
"""

from typing import Protocol, runtime_checkable

# Stores the engagement graph may point at. Extend deliberately: every entry here
# is a trust decision, not a connection string.
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


def make_reference(store: str, label: str, key: str) -> dict:
    """Validate and normalise a reference to the external layer."""
    if store not in STORES:
        raise ReferenceError(f"unknown store {store!r}; expected one of {STORES}")
    if store == "neo4j" and label not in NEO4J_KEYS:
        raise ReferenceError(
            f"unknown neo4j label {label!r}; expected one of "
            f"{sorted(NEO4J_KEYS)}")
    if not key:
        raise ReferenceError("reference key must not be empty")
    return {"store": store, "label": label, "key": key}


@runtime_checkable
class Resolver(Protocol):
    """What phase 2 must implement, behind the existing MCP services.

    Read-only by design. Engagement learnings do not flow back automatically;
    promotion into the curated layer stays an explicit, human-reviewed step, so a
    messy live engagement can never pollute reference knowledge.
    """

    def resolve(self, store: str, label: str, key: str) -> dict:
        """(store, label, key) -> {"title", "summary", "link", ...} or {}."""

    def search(self, query: str, limit: int = 5) -> list:
        """Semantic retrieval -> [{"text", "score", "source"}]. Chroma only."""


class NullResolver:
    """Default. Returns nothing, so the core runs identically with no reference
    layer wired — which is exactly how phase 1 ships."""

    def resolve(self, store: str, label: str, key: str) -> dict:
        return {}

    def search(self, query: str, limit: int = 5) -> list:
        return []


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
