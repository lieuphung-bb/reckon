"""Parse an engagement workspace (markdown) into events.

Deliberately conservative, in the spirit of tools/topo/scaffold.py: assert only
what was parsed deterministically. Judgment stays with the operator. A row this
cannot read is skipped, never guessed at, and never crashes the run.

Real workspaces do NOT share a table schema. Four seen in the wild:

    A | Panel | IP | Host / Role | Seg | OS | Ports | Access / notes |
    B | Host / IP (env var) | Role | Zone | Access | Creds |
    C | Host / IP (env var) | Zone | Role | Access | Creds |
    D | .last | Host | Zone | Role / note | Reached | proof.txt |

so columns are matched by HEADER SYNONYM, never by position.
"""

import os
import re


class IngestError(ValueError):
    """A workspace this importer cannot read. Raised loudly rather than
    returning a half-parsed graph that looks complete."""

# --- column synonyms ----------------------------------------------------------

COLS = {
    "host":   ("host", "host / role", "host / ip", "host/ip", "name", "target"),
    "ip":     ("ip", "address", ".last", "panel", "last-octet"),
    "zone":   ("zone", "seg", "segment", "subnet", "network", "prefix"),
    "role":   ("role", "role / note", "role/note", "notes", "note", "service"),
    "access": ("access", "access / notes", "reached", "reach from kali",
               "status", "state"),
    "creds":  ("creds", "cred", "credential", "credentials"),
    "ports":  ("ports", "port", "open services"),
    "source": ("source", "from", "origin", "found"),
    "scope":  ("scope", "grants", "opens", "valid for"),
    "verified": ("verified", "valid", "confirmed"),
}

SECTION = {
    "hosts": ("host", "inventory", "asset"),
    "creds": ("cred", "credential", "loot", "password"),
    "tasks": ("chain checklist", "checklist", "kill chain", "graded", "objective",
              "task"),
    "zones": ("zone",),
}

# --- access-string heuristics -------------------------------------------------

_ADMIN = ("root", "system", "administrator", "domain admin", " da ", "pwn3d",
          "admin", "nt authority", "sudo")
_SHELL = ("shell", "rce", "winrm", "ssh as", "session", "foothold", "meterpreter",
          "interactive")
_SOME = ("r/w", "read", "smb", "auth", "login", "authenticated", "bind", "app")
_REACH = ("reachable", "routed", "reach", "open", "pivot", "via ")
_DENY = ("denied", "no route", "no-route", "blocked", "failed", "rejects",
         "access_denied", "void", "broken")
_UNTOUCHED = ("unenumerated", "not accessed", "not enumerated", "unknown",
              "untested", "none", "—", "-", "")


def classify_access(text: str):
    """Access prose -> (epistemic, exploitation, rank). Conservative by design."""
    t = (text or "").strip().lower()
    if not t or t in _UNTOUCHED:
        return "unexplored", "discovered", 0
    if any(k in t for k in _DENY) and not t.startswith("✅"):
        # a denial is a real observation: verified knowledge that this is shut
        if not any(k in t for k in ("✅", "shell", "pwn3d")):
            return "verified", "discovered", 0
    got = "✅" in t or "[+]" in t
    if any(k in t for k in _ADMIN):
        return ("verified" if got or "pwn3d" in t else "hypothesized"), "acquired", 3
    if any(k in t for k in _SHELL):
        return ("verified" if got else "hypothesized"), "acquired", 2
    if any(k in t for k in _SOME):
        return ("verified" if got else "hypothesized"), "acquired", 1
    if any(k in t for k in _REACH):
        return "verified", "discovered", 0
    return "hypothesized", "discovered", 0


# --- markdown table parsing ---------------------------------------------------

def _clean(cell: str) -> str:
    s = re.sub(r"\*\*|`|~~", "", cell or "").strip()
    return re.sub(r"\s+", " ", s)


def parse_tables(lines):
    """Yield (headers[], rows[[...]]) for every markdown table in `lines`."""
    i, out = 0, []
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip()):
            headers = [_clean(c).lower() for c in line.strip("|").split("|")]
            rows, i = [], i + 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [_clean(c) for c in lines[i].strip().strip("|").split("|")]
                if cells and any(cells):
                    rows.append(cells)
                i += 1
            out.append((headers, rows))
        else:
            i += 1
    return out


def col_index(headers, key):
    for want in COLS.get(key, ()):
        for idx, h in enumerate(headers):
            if h == want:
                return idx
    for want in COLS.get(key, ()):          # substring fallback
        for idx, h in enumerate(headers):
            if want in h:
                return idx
    return None


def sections(text):
    """Split markdown into {heading: [lines]} for '##'/'###' headings."""
    out, cur, buf = {}, "_preamble", []
    for line in text.splitlines():
        m = re.match(r"^#{2,3}\s+(.*)$", line)
        if m:
            out.setdefault(cur, []).extend(buf)
            cur, buf = m.group(1).strip().lower(), []
        else:
            buf.append(line)
    out.setdefault(cur, []).extend(buf)
    return out


def classify_section(heading):
    h = heading.lower()
    for kind, keys in SECTION.items():
        if any(k in h for k in keys):
            return kind
    return None


# --- id helpers ---------------------------------------------------------------

def _slug(kind, text):
    s = re.sub(r"[^a-z0-9.\-_]+", "-", (text or "").lower()).strip("-")
    return f"{kind}:{s[:48] or 'unknown'}"


HOSTNAME_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9\-]{2,}\d{0,3})\b")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def host_identity(row, headers):
    """Best-effort (label, ip) for a host row."""
    hi, ii = col_index(headers, "host"), col_index(headers, "ip")
    host = row[hi] if hi is not None and hi < len(row) else ""
    ip = row[ii] if ii is not None and ii < len(row) else ""
    if not IP_RE.search(ip):
        m = IP_RE.search(host) or IP_RE.search(" ".join(row))
        ip = m.group(0) if m else ip
    label = host or ip
    # "198.51.100.10=10.99.10.13 (SITE_PUB)" -> keep something readable
    m = re.search(r"\b([A-Z][A-Z0-9\-]{3,})\b", label)
    if m:
        label = m.group(1)
    elif IP_RE.search(label):
        label = IP_RE.search(label).group(0)
    return label.strip()[:48], (IP_RE.search(ip).group(0) if IP_RE.search(ip) else "")


# --- the importer -------------------------------------------------------------

TASK_RE = re.compile(r"^\s*[-*]\s*\[([ x~])\]\s*(.+)$")


def from_workspace(path: str) -> list:
    """Read <path>/topology.md (+ siblings) -> event list."""
    events = []
    topo = os.path.join(path, "topology.md")
    if not os.path.exists(topo):
        raise IngestError(f"no topology.md in {path}")
    with open(topo, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    secs = sections(text)

    host_ids, host_labels, first_owned = {}, {}, None

    # Classify each table by its HEADER SIGNATURE, not by the heading above it.
    # CL4 puts host tables under `### DMZ (...)` subsections, and M11 files its
    # chain checklist under `## Cred / chain checklist`; heading-based routing
    # silently dropped both.
    host_tables, cred_tables = [], []
    for heading, lines in secs.items():
        is_cred_heading = classify_section(heading) == "creds"
        for headers, rows in parse_tables(lines):
            has_id = (col_index(headers, "host") is not None
                      or col_index(headers, "ip") is not None)
            looks_cred = (is_cred_heading
                          or col_index(headers, "creds") is not None
                          or col_index(headers, "verified") is not None)
            if looks_cred and not (has_id and col_index(headers, "access") is not None):
                cred_tables.append((headers, rows))
            elif has_id:
                host_tables.append((headers, rows))

    # ---- hosts
    if True:
        for headers, rows in host_tables:
            for row in rows:
                label, ip = host_identity(row, headers)
                if not label or label.lower() in ("host", "zone", "—", "-"):
                    continue
                nid = _slug("host", label)
                if nid in host_ids:
                    continue
                zi, ri, ai, pi = (col_index(headers, k)
                                  for k in ("zone", "role", "access", "ports"))
                get = lambda i: row[i] if i is not None and i < len(row) else ""
                epi, expl, rank = classify_access(get(ai))
                props = {k: v for k, v in
                         (("ip", ip), ("zone", get(zi) or "unzoned"),
                          ("role", get(ri)), ("ports", get(pi)),
                          ("access_text", get(ai))) if v}
                # Hosts stay `discovered`. Exploitation is about unread CONTENT,
                # and a doc's access column says nothing about whether the box was
                # enumerated - inferring `acquired` here made every owned host an
                # UNMINED alarm. Only creds and artifacts carry that signal.
                events.append({"op": "add_node", "args": {
                    "id": nid, "kind": "host", "label": label,
                    "epistemic": epi, "props": props}})
                host_ids[nid] = rank
                if len(label) > 3:
                    host_labels[nid] = label
                if rank > 0:
                    events.append({"op": "add_edge", "args": {
                        "id": f"e:op-{nid}", "src": "operator:me", "dst": nid,
                        "rel": "grants-access-to",
                        "epistemic": "verified" if epi == "verified" else "hypothesized",
                        "props": {"rank": rank, "privilege": get(ai)[:60]}}})
                    if first_owned is None and rank >= 2:
                        first_owned = nid
                elif epi == "verified":
                    events.append({"op": "add_edge", "args": {
                        "id": f"e:op-{nid}", "src": "operator:me", "dst": nid,
                        "rel": "grants-access-to", "epistemic": "verified",
                        "props": {"rank": 0, "privilege": "network reach"}}})

    # ---- creds
    if True:
        for headers, rows in cred_tables:
            ci = col_index(headers, "creds")
            if ci is None:
                ci = col_index(headers, "host")
            if ci is None:
                ci = 0
            for row in rows:
                name = row[ci] if ci < len(row) else ""
                name = name.split("(")[0].strip()
                # The PRINCIPAL is the identifying half; the secret half must not
                # reach the node id, which is printed in data-n, the drawer and
                # the graph and was the one field masking used to skip.
                principal = re.split(r"[:/=]", name, maxsplit=1)[0].strip() or name
                if not name or name.lower() in ("cred", "—", "-", "none"):
                    continue
                nid = _slug("cred", principal)
                vi, si, sci = (col_index(headers, k)
                               for k in ("verified", "source", "scope"))
                get = lambda i: row[i] if i is not None and i < len(row) else ""
                vtext = get(vi)
                verified = "✅" in vtext or "yes" in vtext.lower()
                events.append({"op": "add_node", "args": {
                    "id": nid, "kind": "cred", "label": name[:48],
                    "epistemic": "verified" if verified else "hypothesized",
                    "source": get(si)[:80] or None,
                    "props": {"scope": get(sci)[:80], "verified_text": vtext[:60]}}})
                events.append({"op": "set_exploitation",
                               "args": {"id": nid, "state": "acquired"}})
                events.append({"op": "add_edge", "args": {
                    "id": f"e:hold-{nid}", "src": "operator:me", "dst": nid,
                    "rel": "holds",
                    "epistemic": "verified" if verified else "hypothesized"}})
                # a cred whose Verified column shows a positive test HAS been used
                if verified and ("✅" in vtext):
                    events.append({"op": "examine", "args": {
                        "id": nid, "outcome": vtext[:100]}})

    # ---- objectives: scan checkbox lines DOCUMENT-WIDE.
    # A `- [x]` line is unambiguous wherever it sits, and relying on the heading
    # lost every M11 objective to a section titled "Cred / chain checklist".
    for heading, lines in secs.items():
        superseded = "superseded" in heading
        for line in lines:
            m = TASK_RE.match(line)
            if not m:
                continue
            mark, body = m.group(1), _clean(m.group(2))
            # A task id must survive a decorated line. `★ T7 proof.txt ...` used to
            # take "★" as the token, strip it to empty, and fall back to a junk id -
            # so the ACHIEVED T7 landed under a synthetic name while a superseded
            # open T7 kept `obj:t7` and the board reported it as still open.
            mt = re.search(r"\bT-?(\d+[a-z]?)\b", body)
            if mt:
                tid = "t" + mt.group(1)
            else:
                tok = re.sub(r"[^A-Za-z0-9]", "", body.split()[0] if body else "")
                tid = tok[:12] or f"task{len(events)}"
            nid = _slug("obj", tid)
            status = {"x": "achieved", " ": "open", "~": "open"}[mark]
            crown = "★" in line or "⭐" in line
            props = {"crown_jewel": True} if crown else {}
            # Requirements are only asserted when the task text NAMES a host we
            # know. Synthesising "requires the first owned host" made every open
            # objective look satisfiable-right-now, including CL2 T10, which is
            # genuinely blocked. No claim beats a confident wrong one.
            named = [hid for hid, lbl in host_labels.items()
                     if re.search(r"\b" + re.escape(lbl) + r"\b", body, re.I)]
            if named:
                props["requires"] = [{"target": h, "min_rank": 1} for h in named[:2]]
            events.append({"op": "add_node", "args": {
                "id": nid, "kind": "objective", "label": body[:90],
                "status": status, "props": props}})
            if superseded:
                events.append({"op": "note", "args": {
                    "target_id": nid, "text": "from a superseded checklist"}})
    return events
