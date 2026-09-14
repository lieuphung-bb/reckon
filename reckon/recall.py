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
TOKENS = ("http", "https", "smb", "ssh", "winrm", "ldap", "sql", "web",
          "api", "registry", "jenkins", "gitlab", "rag", "llm", "chat")

# A port number IS a service hint, and on a node written by `import --nmap` it is
# often the only one: the port is a prop, the service name is in the label, and
# neither used to be read. Only unambiguous, single-service ports are mapped --
# 8080 is a web port but also anything at all, so it is left to the text.
PORT_HINTS = {
    "21": "api",        "22": "ssh",       "80": "http",      "88": "smb",
    "139": "smb",       "389": "ldap",     "443": "https",    "445": "smb",
    "636": "ldap",      "1433": "sql",     "3268": "ldap",    "3269": "ldap",
    "3306": "sql",      "5432": "sql",     "5985": "winrm",   "5986": "winrm",
    "1521": "sql",      "27017": "sql",
}


def signature(node) -> tuple:
    """(kind, sorted service hints) -- the key both sides of recall match on.

    READ THE PROPS THAT ARE ACTUALLY WRITTEN. This keyed on `props["ports"]`
    (plural) and `props["role"]`, and `ports` is written by nothing: across a
    five-engagement store it appeared ZERO times, while `port` appeared 79
    times, `proto` 64 and `product` 45 -- what `import --nmap` really writes. So
    111 of 113 host/service nodes produced an EMPTY hint tuple and the signature
    collapsed to kind alone: two index keys, ('host', ()) and ('service', ()),
    every host matching all 9 host techniques and every service all 6 service
    ones. `recall` on an SSH service returned the web-app set from another box.

    Not a fallback problem -- those keys matched directly and the kind-only
    fallback below was never reached.

    The label is read too, because a hand-made service node carries no port prop
    at all and puts the service name there ("ssh OpenSSH 8.2p1 :22"). Reading it
    is what makes those nodes discriminate; the token list stays closed, so the
    label can only ever match a term already in TOKENS.
    """
    props = node.props or {}
    text = " ".join(str(props.get(k, "")) for k in
                    ("role", "ports", "proto", "product", "service", "extrainfo")).lower()
    text += " " + str(getattr(node, "label", "") or "").lower()
    hints = [t for t in TOKENS if t in text]
    port = str(props.get("port", "")).strip()
    if port in PORT_HINTS:
        hints.append(PORT_HINTS[port])
    # https implies http in the text test ("https" contains "http"), which would
    # split two nodes that are the same surface. Collapse it: a TLS web service
    # is a web service, and the distinction never mattered to a technique.
    if "https" in hints:
        hints = [h for h in hints if h != "http"]
    return (node.kind, tuple(sorted(set(hints))))


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
    hits = [{**h, "generic": False} for h in index.get(sig, [])]

    # Overlapping hints still count. A node signed ('service', ('http', 'api'))
    # and a recorded one signed ('service', ('http',)) are the same surface seen
    # at different resolutions, and requiring the tuples to be EQUAL made every
    # such pair miss -- then the fallback below handed back everything, which is
    # how a hinted query still produced an unhinted answer.
    if not hits:
        for (kind, hints), entries in index.items():
            if kind == sig[0] and hints and sig[1] and set(hints) & set(sig[1]):
                hits.extend({**e, "generic": False} for e in entries)

    # THE FALLBACK IS THE GENERIC BUCKET, NOT EVERYTHING OF THE KIND.
    #
    # This used to extend with every entry whose KIND matched, so a miss returned
    # the whole corpus for that kind: on a five-engagement store an SSH service
    # was handed the Spring-actuator and heapdump techniques recorded against a
    # web app on another box, identically to an LDAP service and an HTTPS one.
    # An answer that cannot tell two surfaces apart is one the reader learns to
    # skip -- the same failure as a term count nobody reads.
    #
    # A technique recorded against a node that carried NO hints is genuinely
    # generic and is still worth offering; one recorded against an `ldap` node is
    # a claim about LDAP and must not be offered for SSH. So the fallback is the
    # (kind, ()) bucket alone, and those hits are marked `generic` so a caller can
    # say which they are rather than presenting them as a match.
    if not hits:
        hits = [{**h, "generic": True} for h in index.get((sig[0], ()), [])]

    merged = {}
    for h in hits:
        key = h["technique_id"]
        cur = merged.setdefault(key, {**h, "seen": 0, "engagements": set()})
        cur["seen"] += 1
        cur["engagements"].add(h["engagement"])
        cur["confirmed"] = cur["confirmed"] or h["confirmed"]
        cur["generic"] = cur["generic"] and h["generic"]
    out = [{**v, "engagements": sorted(v["engagements"])} for v in merged.values()]
    out.sort(key=lambda x: (x["generic"], not x["confirmed"], -x["seen"]))
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
