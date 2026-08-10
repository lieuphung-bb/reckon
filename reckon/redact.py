"""Secret masking for output that leaves the machine.

`tools/topo` rendered secrets in full and defended it with a hard rule: the page
is local-only, personal, never published, and gitignored. `reckon` inherited the
same exposure - an imported workspace pulls real tokens straight into the node
labels, and the console writes them into HTML - so it needs the same rule PLUS a
way to produce a shareable artifact.

Default stays unredacted: mid-engagement you need the actual credential, and a
masked board is useless. `--redact` is for anything that will be seen by someone
other than you.

This masks what it recognises. It is a courtesy, not a control: never treat a
redacted artifact as safe to publish without reading it.
"""

import hashlib
import re

PLACEHOLDER = "«redacted»"

# Known-prefix tokens: unambiguous, worth matching exactly.
_PATTERNS = [
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{8,}"),          # GitLab PAT
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),        # GitHub
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),                 # AWS access key id
    re.compile(r"\bhf_[A-Za-z0-9]{16,}"),               # HuggingFace
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),     # Slack
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),               # generic API key
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
               re.S),
    re.compile(r"\b[a-fA-F0-9]{32}:[a-fA-F0-9]{32}\b"),  # LM:NT pair
]

# `user:secret` / `user / secret` as they appear in cred labels. The account name
# is the useful half and stays; the secret half goes.
_PAIR = re.compile(r"(?P<user>[A-Za-z0-9._\\\-]{2,40})\s*(?P<sep>[:/])\s*"
                   r"(?P<secret>[^\s|,;]{6,})")

_KEYWORD = re.compile(
    r"(?i)\b(pass(?:word|wd)?|pwd|secret|token|apikey|api_key)\b\s*[:=]\s*"
    r"(?P<secret>[^\s|,;]{4,})")


def redact_text(s):
    if not isinstance(s, str) or not s:
        return s
    for rx in _PATTERNS:
        s = rx.sub(PLACEHOLDER, s)
    s = _KEYWORD.sub(lambda m: m.group(0).replace(m.group("secret"), PLACEHOLDER), s)

    def _pair(m):
        # Leave things that are plainly not credentials: URLs, times, ratios.
        if m.group("sep") == "/" and m.group("secret").isdigit():
            return m.group(0)
        if m.group("user").lower() in ("http", "https", "ftp", "ssh", "file"):
            return m.group(0)
        return f"{m.group('user')}{m.group('sep')}{PLACEHOLDER}"

    return _PAIR.sub(_pair, s)


def redact_obj(obj):
    """Deep-copy a structure with every string leaf masked."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    return obj


def _safe_id(node_id: str) -> str:
    """An id with any recognised secret removed, kept stable and unique."""
    masked = redact_text(node_id)
    if masked == node_id:
        return node_id
    kind, _, _rest = node_id.partition(":")
    digest = hashlib.sha256(node_id.encode()).hexdigest()[:8]
    head = re.sub(r"[^a-z0-9\-]+", "-", masked.partition(":")[2].lower()).strip("-")
    head = head.replace(re.sub(r"[^a-z0-9\-]+", "-", PLACEHOLDER.lower()).strip("-"),
                        "").strip("-")[:32]
    return f"{kind}:{head or 'redacted'}-{digest}"


def redact_graph(g):
    """Mask a loaded graph for output.

    Operates on the in-memory fold, never on the event log: stored history keeps
    the real values, and only the rendered artifact is masked.

    **Ids are remapped too.** The importer slugs a credential's label into its id,
    so a credential id carried the token in the one field masking used to skip - and the console prints ids in `data-n`, the drawer and
    the graph. Remapping has to be done consistently across nodes AND edge
    endpoints or every click breaks, so it happens here rather than at each
    render site.
    """
    remap = {nid: _safe_id(nid) for nid in g.nodes}
    remap = {k: v for k, v in remap.items() if k != v}

    for n in g.nodes.values():
        n.id = remap.get(n.id, n.id)
        n.label = redact_text(n.label)
        n.props = redact_obj(n.props)
        n.notes = [redact_text(x) for x in n.notes]
        n.source = redact_text(n.source)
        if n.superseded_by:
            n.superseded_by = remap.get(n.superseded_by, n.superseded_by)
        for req in (n.props.get("requires") or []):
            if isinstance(req, dict) and req.get("target") in remap:
                req["target"] = remap[req["target"]]
    if remap:
        g.nodes = {remap.get(k, k): v for k, v in g.nodes.items()}

    for e in g.edges.values():
        e.src = remap.get(e.src, e.src)
        e.dst = remap.get(e.dst, e.dst)
        e.id = redact_text(e.id)
        e.props = redact_obj(e.props)
    g.edges = {e.id: e for e in g.edges.values()}
    return g
