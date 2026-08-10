"""Feature 5 — technique recall from your OWN history.

The *techniques* stream, sourced from engagements you have actually run rather
than an external corpus. Deliberately weaker than the Neo4j/Chroma reference
layer and available now: it answers "last time I stood somewhere like this, what
worked?" using only `applies-technique` edges you recorded yourself.

Why this and not the KB first: a curated corpus tells you what is *possible*;
your own history tells you what *worked for you, on targets like this one*. The
second is a much smaller and much more relevant set, and it costs no integration.

Recall is a SUGGESTION, never a fact. Hits come back as candidates for a
hypothesis, in the same spirit as `reference.retrieval_to_events` - a past
success on a similar node is evidence about relevance, not about this target.
"""

from collections import defaultdict

from . import store

# How a node is matched against history. Deliberately coarse: an exact match
# would only ever hit the same engagement, and a fuzzy one would suggest
# everything. Kind plus a service/port hint is the useful middle.
def signature(node) -> tuple:
    kind = node.kind
    props = node.props or {}
    ports = str(props.get("ports", ""))
    role = str(props.get("role", "")).lower()
    hints = []
    for token in ("http", "https", "smb", "ssh", "winrm", "ldap", "sql", "web",
                  "api", "registry", "jenkins", "gitlab", "rag", "llm", "chat"):
        if token in role or token in ports.lower():
            hints.append(token)
    return (kind, tuple(sorted(set(hints))))


def build_index(exclude: str | None = None) -> dict:
    """{signature: [{technique, engagement, evidence}]} across all engagements."""
    index = defaultdict(list)
    for name in store.list_engagements():
        if exclude and name == exclude:
            continue
        try:
            g = store.load(name)
        except Exception:                      # a corrupt log must not break recall
            continue
        for e in g.edges.values():
            if e.rel != "applies-technique" or e.epistemic == "refuted":
                continue
            src, dst = g.nodes.get(e.src), g.nodes.get(e.dst)
            if not src or not dst:
                continue
            index[signature(src)].append({
                "technique": dst.label,
                "technique_id": dst.id,
                "engagement": name,
                "confirmed": e.epistemic == "verified",
            })
    return dict(index)


def recall(g, node_id: str, exclude: str | None = None) -> list:
    """Techniques previously applied to nodes that look like this one."""
    node = g.nodes.get(node_id)
    if not node:
        return []
    index = build_index(exclude=exclude)
    sig = signature(node)
    hits = list(index.get(sig, []))

    # Fall back to kind-only when the hinted signature finds nothing, so a host
    # with no recorded role still gets the broader answer rather than silence.
    if not hits:
        for (kind, _hints), entries in index.items():
            if kind == sig[0]:
                hits.extend(entries)

    merged = {}
    for h in hits:
        key = h["technique_id"]
        cur = merged.setdefault(key, {**h, "seen": 0, "engagements": set()})
        cur["seen"] += 1
        cur["engagements"].add(h["engagement"])
        cur["confirmed"] = cur["confirmed"] or h["confirmed"]
    out = [{**v, "engagements": sorted(v["engagements"])} for v in merged.values()]
    out.sort(key=lambda x: (not x["confirmed"], -x["seen"]))
    return out


def suggestions(g, engagement: str | None = None, limit: int = 3) -> dict:
    """Recall for every node currently reachable and not yet exhausted.

    Scoped to what you can actually act on: suggesting a technique for a host you
    cannot reach is noise, and noise is what this whole tool exists to remove.
    """
    from .queries import reach

    r = reach(g)
    out = {}
    for nid, info in r.items():
        node = g.nodes.get(nid)
        if not node or node.kind in ("operator", "objective") or node.superseded_by:
            continue
        if info["cost"] > 0 or node.exploitation == "exhausted":
            continue
        hits = recall(g, nid, exclude=engagement)[:limit]
        if hits:
            out[nid] = hits
    return out
