"""The value proposition. Pure functions over a Graph — no IO, no printing.

Kept side-effect free so an MCP server is a thin wrapper rather than a refactor.

One algorithm underpins the whole board: Dijkstra where an edge costs 0 if it is
verified and 1 if it is merely hypothesized. Then

    dist == 0   reachable NOW      (every edge on the path is verified)
    dist >= 1   reachable IF       (the hypothesized edges ARE the assumptions)
    no path     unreachable        (needs discovery, not verification)

The hypothesized edges on a winning path are exactly the verification queue,
and ordering them by how many objectives they gate ranks the cheapest next test.
"""

import heapq
import re

from .model import ACCESS_RELS, OPERATOR_ID

INF = float("inf")


# --- traversal ----------------------------------------------------------------

def _edge_cost(edge) -> float:
    if edge.epistemic == "refuted":
        return INF
    return 0 if edge.epistemic == "verified" else 1


def reach_pareto(g, source: str = OPERATOR_ID) -> dict:
    """Dijkstra keeping every NON-DOMINATED (cost, rank) way to reach each node.

    One entry per node is not enough. A host is routinely reachable at
    (cost 0, rank 1) over the network *and* at (cost 1, rank 3) with a credential
    nobody has tested — and an objective needing rank 3 is `reachable-if`, not
    unreachable. Keeping only the cheapest entry discarded the privileged path and
    reported such objectives as needing fresh discovery, which is the opposite of
    the truth: they need one test.

    A pair (c1, r1) dominates (c2, r2) when c1 <= c2 and r1 >= r2 — cheaper and no
    less privileged. Only non-dominated pairs are kept, so the set stays small.

    Returns {node_id: [ {"cost", "rank", "path", "assumptions"}, ... ]}.
    """
    best = {}

    def dominated(nid, cost, rank):
        return any(e["cost"] <= cost and e["rank"] >= rank
                   for e in best.get(nid, ()))

    pq = [(0, 0, source, [])]
    while pq:
        cost, negrank, nid, path = heapq.heappop(pq)
        rank = -negrank
        if dominated(nid, cost, rank):
            continue
        entries = [e for e in best.get(nid, [])
                   if not (cost <= e["cost"] and rank >= e["rank"])]
        entries.append({"cost": cost, "rank": rank, "path": path,
                        "assumptions": [eid for eid in path
                                        if g.edges[eid].epistemic == "hypothesized"]})
        best[nid] = entries
        for e in g.out_edges(nid):
            if e.rel not in ACCESS_RELS:
                continue
            c = _edge_cost(e)
            if c == INF:
                continue
            # A `contains` edge inherits the privilege you already hold on the
            # parent; an access-granting edge sets it explicitly.
            nrank = rank if e.rel == "contains" and "rank" not in e.props else e.rank
            heapq.heappush(pq, (cost + c, -nrank, e.dst, path + [e.id]))
    return best


def reach(g, source: str = OPERATOR_ID) -> dict:
    """The single best way to reach each node: cheapest first, then most privileged.

    This is the DISPLAY view — what you have right now. Requirement checking uses
    `reach_pareto`, because "could I get admin there if one assumption held" is a
    different question from "what do I have there today".
    """
    return {nid: min(entries, key=lambda e: (e["cost"], -e["rank"]))
            for nid, entries in reach_pareto(g, source).items()}


def _requirements_met(g, obj, reach_map: dict, max_cost: int = 0):
    """Is every `requires` entry satisfied at <= max_cost? Returns (bool, unmet).

    `reach_map` may be either shape: the pareto sets from `reach_pareto`, or the
    single-best map from `reach`. Requirement checks want the pareto set, since a
    privileged-but-conditional path is exactly what makes an objective
    reachable-if rather than unreachable.
    """
    reqs = obj.props.get("requires") or []
    if not reqs:
        return False, ["<no requires declared>"]
    unmet = []
    for r in reqs:
        tgt, need = r.get("target"), int(r.get("min_rank", 0))
        got = reach_map.get(tgt)
        entries = got if isinstance(got, list) else ([got] if got else [])
        if not any(e["cost"] <= max_cost and e["rank"] >= need for e in entries):
            unmet.append(f"{tgt}@rank>={need}")
    return (not unmet), unmet


# --- the queries --------------------------------------------------------------

def frontier(g) -> dict:
    """Objectives partitioned by how far they are from what is already verified.

    `undeclared` is kept separate from `unreachable` on purpose. "I cannot get
    there" and "nobody has said what getting there requires" are different
    problems: the first needs an exploit, the second needs one line of input.
    Merging them let a freshly imported workspace look hopeless when it was
    merely unannotated.
    """
    r = reach_pareto(g)
    now, cond, un, undecl = [], [], [], []
    for obj in g.objectives():
        if obj.status == "achieved":
            continue
        if not (obj.props.get("requires") or []):
            undecl.append({"id": obj.id, "label": obj.label,
                           "crown_jewel": bool(obj.props.get("crown_jewel"))})
            continue
        ok, unmet = _requirements_met(g, obj, r, max_cost=0)
        if ok:
            now.append({"id": obj.id, "label": obj.label,
                        "crown_jewel": bool(obj.props.get("crown_jewel"))})
            continue
        ok_soft, unmet_soft = _requirements_met(g, obj, r, max_cost=99)
        if ok_soft:
            assumptions = []
            for req in obj.props.get("requires") or []:
                entries = r.get(req.get("target")) or []
                need = int(req.get("min_rank", 0))
                viable = [e for e in entries if e["rank"] >= need]
                if viable:
                    assumptions += min(viable, key=lambda e: e["cost"])["assumptions"]
            cond.append({
                "id": obj.id, "label": obj.label,
                "crown_jewel": bool(obj.props.get("crown_jewel")),
                "assumptions": sorted(set(assumptions)),
            })
        else:
            un.append({"id": obj.id, "label": obj.label, "unmet": unmet_soft})
    return {"reachable_now": now, "reachable_if": cond, "unreachable": un,
            "undeclared": undecl}


def unrealized(g) -> list:
    """Objectives I can ALREADY satisfy but have not achieved.

    Root on a host already held, with the objective that needs it still open.
    """
    r = reach_pareto(g)
    out = []
    for obj in g.objectives():
        if obj.status == "achieved":
            continue
        ok, _ = _requirements_met(g, obj, r, max_cost=0)
        if ok:
            out.append({
                "id": obj.id, "label": obj.label,
                "crown_jewel": bool(obj.props.get("crown_jewel")),
                "status": obj.status or "open",
                "why": [g.edges[e].id for e in
                        min(r.get(obj.props["requires"][0]["target"]) or [{"path": [], "cost": 0}],
                            key=lambda e: e["cost"])["path"]],
            })
    out.sort(key=lambda o: (not o["crown_jewel"], o["id"]))
    return out


def unmined(g) -> list:
    """Assets I can reach RIGHT NOW and have never examined, oldest first.

    Two shapes, one failure:
      - acquired but never tried  (a credential held, never used on the app it opens)
      - reachable but never read  (a share denied to a service account, never
                                   re-tried once admin made it readable)

    Reachability is re-evaluated every call, so an asset that was legitimately
    out of reach earlier resurfaces the moment new access makes it gettable.
    That resurfacing is the point: 'denied once' is not 'denied forever'.
    """
    r = reach(g)
    out = []
    for n in g.nodes.values():
        if n.superseded_by or n.kind in ("objective", "operator", "assumption",
                                         "technique", "finding"):
            continue
        if n.exploitation in ("examined", "exhausted"):
            continue
        # Reachability alone only raises an alarm for things whose CONTENT is the
        # point - a share, a store, a credential, an endpoint. Flagging every
        # reachable host instead produced 12 alarms where 4 mattered, and a board
        # that cries wolf is a board nobody reads. A host still alarms once it is
        # explicitly marked `acquired`.
        reachable = (n.id in r and r[n.id]["cost"] == 0
                     and n.kind in ("artifact", "cred", "service"))
        if n.exploitation == "acquired" or reachable:
            since = (n.acquired_at if n.acquired_at is not None else n.first_seen)
            out.append({"id": n.id, "kind": n.kind, "label": n.label,
                        "age_held": g.seq - since,
                        "acquired_at": since,
                        "why": "acquired, never examined" if n.exploitation == "acquired"
                               else "reachable now, never examined"})
    out.sort(key=lambda x: -x["age_held"])
    return out


def stale(g) -> list:
    """Things being trusted without verification.

    A target host sits on the working path with its identity never actually
    confirmed, so hours of work land on an orphaned box.
    """
    r = reach(g)
    on_path = set()
    for info in r.values():
        for eid in info["path"]:
            e = g.edges[eid]
            on_path.add(e.src)
            on_path.add(e.dst)

    out = []
    for nid in sorted(on_path):
        n = g.nodes.get(nid)
        if not n or n.superseded_by:
            continue
        # Identity only matters where being wrong sends work to the wrong place.
        # An unread artifact is an `unmined` finding, not a trust failure.
        if n.kind not in ("host", "service", "cred"):
            continue
        if n.epistemic in ("unexplored", "hypothesized"):
            out.append({"id": n.id, "label": n.label, "kind": n.kind,
                        "epistemic": n.epistemic,
                        "reason": "on an active path but never verified"})
        elif n.epistemic == "verified" and n.last_verified is not None:
            if g.seq - n.last_verified > 25:
                out.append({"id": n.id, "label": n.label, "kind": n.kind,
                            "epistemic": n.epistemic,
                            "reason": f"verified at seq {n.last_verified}, "
                                      f"{g.seq - n.last_verified} events ago"})
    return out


def coverage(g) -> dict:
    objs = g.objectives()
    by_status = {}
    for o in objs:
        by_status[o.status or "open"] = by_status.get(o.status or "open", 0) + 1
    arts = [n for n in g.nodes.values()
            if n.kind == "artifact" and not n.superseded_by]
    examined = [a for a in arts if a.exploitation in ("examined", "exhausted")]
    return {
        "objectives_total": len(objs),
        "by_status": by_status,
        "achieved": by_status.get("achieved", 0),
        "artifacts_total": len(arts),
        "artifacts_examined": len(examined),
        "artifact_examined_ratio": (len(examined) / len(arts)) if arts else None,
    }


def why(g, obj_id: str) -> dict:
    """Explain the winning path to an objective, and what it rests on."""
    obj = g.nodes.get(obj_id)
    if not obj:
        return {"error": f"no such node: {obj_id}"}
    r = reach_pareto(g)
    steps, assumptions = [], []
    for req in obj.props.get("requires") or []:
        entries = r.get(req.get("target")) or []
        need = int(req.get("min_rank", 0))
        viable = [e for e in entries if e["rank"] >= need] or entries
        if not viable:
            steps.append({"target": req.get("target"), "status": "UNREACHABLE"})
            continue
        got = min(viable, key=lambda e: e["cost"])
        for eid in got["path"]:
            e = g.edges[eid]
            steps.append({
                "edge": e.id, "rel": e.rel,
                "from": e.src, "to": e.dst,
                "state": e.epistemic,
                "privilege": e.props.get("privilege"),
            })
        assumptions += got["assumptions"]
    return {"objective": obj.id, "label": obj.label,
            "steps": steps, "assumptions": sorted(set(assumptions))}


def verification_queue(g) -> list:
    """Hypothesized edges ranked by how many objectives they gate.

    This is the cheapest-next-test list: verify the edge that unblocks the most.
    """
    f = frontier(g)
    gate_count = {}
    for item in f["reachable_if"]:
        for eid in item["assumptions"]:
            gate_count[eid] = gate_count.get(eid, 0) + 1
    out = []
    for eid, count in gate_count.items():
        e = g.edges[eid]
        out.append({"edge": eid, "rel": e.rel, "from": e.src, "to": e.dst,
                    "gates": count, "confidence": e.confidence})
    out.sort(key=lambda x: -x["gates"])
    return out


# --- feature 4: the failure budget -------------------------------------------

DEFAULT_BUDGET = 2


def budget(g, limit: int = DEFAULT_BUDGET) -> list:
    """Targets that have burned the failure budget without succeeding.

    `feedback_docs_first_failure_budget`: after two dead ends on one approach,
    re-scope the whole approach rather than tweak-and-retry. That is a rule you
    have to remember mid-engagement, which is precisely when you don't - so
    count it instead. Tweak-and-retry feels like progress, which is why it needs
    an external alarm rather than self-discipline.
    """
    out = []
    for target in list(g.nodes.values()) + list(g.edges.values()):
        if getattr(target, "superseded_by", None):
            continue
        failed = getattr(target, "failed_attempts", 0)
        if failed >= limit and not getattr(target, "succeeded", False):
            out.append({
                "id": target.id,
                "label": getattr(target, "label", target.id),
                "failed": failed,
                "notes": [a.get("note", "") for a in target.attempts
                          if a.get("outcome") == "failed"],
                "advice": f"{failed} failed attempts, no success — re-scope the "
                          "approach rather than retry it",
            })
    out.sort(key=lambda x: -x["failed"])
    return out


# --- coverage floor: standard recon minimum per surface ----------------------
#
# The failure this closes (fries 2026-09-07): an agent did a WEAK version of a
# standard step -- twelve hand-typed vhost guesses -- then recorded "no hidden
# vhost" and moved on. The real vhost (the intended entry) was never in its
# list. reckon's other alarms could not see it: `unmined` needs a node to
# EXIST, and a surface never enumerated produces no node, so nothing flagged the
# absence. This is the mirror of a false blocker sent up -- a false negative
# held DOWN -- and the whole verification apparatus is triggered by the agent
# COMMUNICATING, so a silent wrong negative in the graph tripped none of it.
#
# This is a FLOOR, not a procedure. It asserts only that a surface of a known
# type must carry evidence of that type's standard recon before it counts as
# covered -- never HOW (tool, wordlist, order), never an OUTCOME. An earned
# negative ("vhost-enum ran, none found", recorded with method=vhost-enum)
# clears it exactly like a positive result: the floor demands the ATTEMPT be
# recorded, so it can never force an invented finding on an unusual target, and
# it stays an ALARM (a question to answer) rather than a gate that halts work.
# The table is data on purpose: raising coverage is an edit here, not new code.
# It is deliberately coarse -- only genuinely standard-of-done methods, whose
# ABSENCE reliably costs offtrack effort -- because a floor that cries wolf on
# N/A steps is a board nobody reads (see `unmined`).

# surface tag -> the recon methods whose absence means "not yet covered"
SURFACE_RECON = {
    "web":  ("vhost-enum", "content-discovery", "tech-fingerprint"),
    "dns":  ("subdomain-enum", "zone-transfer"),
    "smb":  ("share-enum", "user-enum"),
    "ldap": ("anon-bind-enum",),
}

# The agent tags an artifact/finding with props.method; these aliases let it use
# the natural name of whatever it ran and still satisfy the floor. Lowercased.
_METHOD_ALIASES = {
    "vhost-enum": ("vhost", "vhost-enum", "vhost-fuzz", "vhost-brute",
                   "gobuster-vhost", "ffuf-vhost", "ffuf-host", "virtual-host"),
    "content-discovery": ("content-discovery", "dirbrute", "dir-brute", "dirbust",
                          "dir-bust", "gobuster-dir", "ffuf-dir", "feroxbuster",
                          "content-brute", "dirb"),
    "tech-fingerprint": ("tech-fingerprint", "whatweb", "wappalyzer",
                         "fingerprint", "tech-id", "httpx", "nikto"),
    "subdomain-enum": ("subdomain-enum", "subdomain", "dnsenum", "gobuster-dns",
                       "ffuf-dns", "amass", "subfinder"),
    "zone-transfer": ("zone-transfer", "axfr", "dig-axfr"),
    "share-enum": ("share-enum", "smbclient", "smbmap", "enum4linux", "shares",
                   "enum4linux-ng"),
    "user-enum": ("user-enum", "rid-cycling", "lookupsid", "enum4linux-users",
                  "ridenum"),
    "anon-bind-enum": ("anon-bind-enum", "ldapsearch-anon", "anon-bind",
                       "ldap-anon", "ldapsearch"),
}
_ALIAS2CANON = {a: canon for canon, al in _METHOD_ALIASES.items() for a in al}


def _canon_method(m: str) -> str:
    return _ALIAS2CANON.get(m, m)


def _service_host(g, s):
    """The host node id a service sits on: its `requires` target, else props.host."""
    for req in (s.props or {}).get("requires") or []:
        t = req.get("target")
        if t and str(t).startswith("host:"):
            return t
    h = (s.props or {}).get("host")
    return f"host:{h}" if h else None


def _service_surfaces(s) -> list:
    """Which SURFACE_RECON tags apply to this service, by port then product.

    The web test deliberately EXCLUDES http-framed-but-not-a-web-app services --
    RPC-over-HTTP (ncacn_http, :593) and WinRM (:5985/5986) both carry "http" in
    their nmap product yet a vhost/content sweep against them is nonsense. A
    floor that fired there would be the exact cry-wolf `unmined` warns about.
    """
    p = str((s.props or {}).get("port", ""))
    text = f"{s.label or ''} {(s.props or {}).get('product', '')}".lower()
    tags = []
    not_web = p in ("593", "5985", "5986") or \
        any(x in text for x in ("ncacn", "winrm", "wsman", "rpc", "msrpc"))
    web_port = p in ("80", "443", "8080", "8443", "8000", "8888", "8081",
                     "8008", "3000")
    if not not_web and (web_port or "http" in text):
        tags.append("web")
    if p == "53" or "domain" in text or "dns" in text:
        tags.append("dns")
    if p in ("139", "445") or "microsoft-ds" in text or "netbios" in text \
            or "smb" in text:
        tags.append("smb")
    if p in ("389", "636", "3268", "3269") or "ldap" in text:
        tags.append("ldap")
    return tags


def _hosts_evidenced(g, n) -> set:
    """Host node ids a method-tagged node speaks for: its own props.host, plus any
    host (or service mapped to its host) one edge hop away, either direction."""
    hosts = set()
    h = (n.props or {}).get("host")
    if h:
        hosts.add(f"host:{h}")

    def absorb(nid):
        m = g.nodes.get(nid)
        if not m or m.superseded_by:
            return
        if m.kind == "host":
            hosts.add(m.id)
        elif m.kind == "service":
            sh = _service_host(g, m)
            if sh:
                hosts.add(sh)

    for e in g.out_edges(n.id):
        absorb(e.dst)
    for e in g.in_edges(n.id):
        absorb(e.src)
    return hosts


def _ports_evidenced(g, n) -> set:
    """(host_id, port) pairs a method-tagged node NAMES, for per-listener surfaces.

    Web recon (vhost/content/tech) is per web listener -- a sweep of :80 says
    nothing about :443 -- so clearing a web alarm requires evidence that names
    the port: an explicit `port` prop (paired with the node's host), or a link to
    a specific service node (which carries its own host+port). A host-linked tag
    with no port clears nothing per-listener; that is deliberate -- it is what
    stops one sweep from silently covering every web port on the host.
    """
    pairs = set()
    port = (n.props or {}).get("port")
    if port is not None:
        for h in _hosts_evidenced(g, n):
            pairs.add((h, str(port)))

    def absorb(nid):
        m = g.nodes.get(nid)
        if m and not m.superseded_by and m.kind == "service":
            sh, sp = _service_host(g, m), (m.props or {}).get("port")
            if sh and sp is not None:
                pairs.add((sh, str(sp)))

    for e in g.out_edges(n.id):
        absorb(e.dst)
    for e in g.in_edges(n.id):
        absorb(e.src)
    return pairs


# Per-listener surfaces: web recon differs per web port, so its coverage is
# tracked per (host, port). Everything else (one logical DNS/SMB/LDAP service
# across its ports) stays host-level -- per-port there would only cry wolf.
_WEB_SURFACES = ("web",)

# Breadth floor: a wordlist sweep must have tried at least this many candidates
# to count as a sweep rather than a spot-check. The number is deliberately low --
# every real wordlist clears it by an order of magnitude, so it never cries wolf
# on legitimate work, but it catches a hand-list (fries 2026-09-07: twelve typed
# names recorded as "the web namespace is closed"). Methods absent from this map
# are single operations (an AXFR attempt, one anonymous bind) with no breadth to
# speak of -- they clear on presence. The agent records `count` on the tag; a
# breadth-gated method with no count, or too small a count, stays unswept.
SWEEP_MIN_BREADTH = {
    "vhost-enum": 200,
    "content-discovery": 200,
    "subdomain-enum": 200,
}


def _int(v) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def unswept(g) -> list:
    """Established surfaces whose standard-recon FLOOR is not yet evidenced.

    A web/dns/smb/ldap service that is verified-up but carries no method-tagged
    evidence for one of its surface's standard recon steps. Absence-driven, so it
    fires even when the agent never marked the surface closed -- the exact hole
    `unmined` cannot see, because a surface never enumerated has no node.

    Coverage is scoped to match the recon: WEB (vhost/content/tech) is per
    listener -- a sweep of :80 does NOT clear :443, so evidence must NAME the port
    (a `port` prop, or a link to the service). DNS/SMB/LDAP are one logical
    service across their ports, so a host-level tag clears them. An earned
    negative ("ran it, found nothing") clears exactly like a hit. See
    SURFACE_RECON and _ports_evidenced above.
    """
    # value maps hold the MAX candidate count seen per method, so a later, wider
    # sweep supersedes an earlier thin one. Non-breadth methods carry count 0 and
    # clear on presence (their floor is 0).
    present_host = {}   # host id         -> {canonical method: max count}
    present_port = {}   # (host id, port) -> {canonical method: max count}

    def _record(store, key, method, count):
        slot = store.setdefault(key, {})
        slot[method] = max(slot.get(method, 0), count)

    for n in g.nodes.values():
        if n.superseded_by:
            continue
        m = (n.props or {}).get("method")
        if not m:
            continue
        canon = _canon_method(str(m).lower())
        count = _int((n.props or {}).get("count"))
        for hid in _hosts_evidenced(g, n):
            _record(present_host, hid, canon, count)
        for key in _ports_evidenced(g, n):
            _record(present_port, key, canon, count)
    out = []
    for s in g.by_kind("service"):
        if s.epistemic != "verified":   # only established surfaces; no wolf on first contact
            continue
        host = _service_host(g, s)
        port = str((s.props or {}).get("port", ""))
        for surface in _service_surfaces(s):
            per_listener = surface in _WEB_SURFACES
            have = (present_port.get((host, port), {}) if per_listener
                    else present_host.get(host, {}))
            where = f":{port}" if per_listener else ""
            for method in SURFACE_RECON[surface]:
                floor = SWEEP_MIN_BREADTH.get(method, 0)
                seen = have.get(method)              # None = never attempted
                if seen is not None and seen >= floor:
                    continue                          # swept at adequate breadth
                thin = seen is not None               # attempted, but below floor
                if thin:
                    detail = (f"only {seen} candidate(s), below the {floor} floor"
                              if seen else f"no candidate count recorded "
                              f"(floor {floor})")
                    why = (f"{surface} surface{where}: {method} is not an adequate "
                           f"sweep — {detail}. Widen it and record count>={floor}; "
                           f"a thin or uncounted sweep is not a swept surface.")
                else:
                    why = (f"{surface} surface{where} established but no {method} "
                           f"evidence — standard recon floor unmet; run it (an "
                           f"earned negative counts) and record method={method}"
                           + (f" port={port}" if per_listener else "")
                           + (f" count>={floor}" if floor else ""))
                out.append({
                    "id": f"{s.id}#unswept:{method}",
                    "service": s.id, "label": s.label, "host": host,
                    "surface": surface, "method": method, "port": port,
                    "thin": thin, "count": (seen or 0), "min_breadth": floor,
                    "why": why,
                })
    out.sort(key=lambda x: (x["host"] or "", x["service"], x["method"]))
    return out


def blocked_but_unswept(g) -> list:
    """The terminal negative, un-earned: no objective is winnable right now AND
    standard recon is still incomplete.

    A "blocked / every path is credential-gated" conclusion drawn in this state
    is likely a COVERAGE gap wearing a credential wall's clothes -- the missing
    sweep looks exactly like a locked door. Fires only while BOTH hold, and
    clears the moment either a win opens up or the recon floor is satisfied; once
    recon is genuinely complete and there is still no win, a credential gate is a
    legitimate conclusion and this stays silent. feedback_agent_false_blocker,
    lifted from per-finding to engagement scope.
    """
    fr = frontier(g)
    if fr["reachable_now"]:
        return []                       # a win is available; not blocked
    seen_paths = fr["reachable_if"] + fr["unreachable"]
    if not seen_paths:
        return []                       # nothing declared to be blocked ON
    gaps = unswept(g)
    if not gaps:
        return []                       # recon done; a gate here is earned
    return [{
        "id": "blocked-but-unswept",
        "open_objectives": len(seen_paths),
        "unswept": len(gaps),
        "why": f"No objective is winnable right now and {len(gaps)} standard-recon "
               f"gap(s) remain — before concluding blocked or credential-gated, "
               f"earn those negatives. A missing or thin sweep is indistinguishable "
               f"from a wall.",
    }]


# --- exploitation-axis floor: a held credential never tried on a present surface -
#
# The mirror of `unswept`, one axis over. `unswept` guards the EPISTEMIC axis (a
# surface enumerated but never swept); this guards the EXPLOITATION axis (a
# primitive HELD but never APPLIED to a present surface). The failure it closes
# (fries 2026-09-08): a documented credential was tested only via Kerberos/AD,
# refused on six AD surfaces, and written off as dead -- while the surface it was
# actually for (a web-app login it authenticates to fine) was never tried. Executor
# and verifier made the same move: they varied the INSTRUMENT (impacket AES -> RC4)
# but not the SURFACE, and counted six refusals on one credential store as
# thoroughness.
#
# The crux is surface KIND. Refusals correlate WITHIN a credential-validation domain
# and are independent ACROSS them: SMB/LDAP/Kerberos/WinRM/RPC/RDP all check the same
# AD/NTLM/Kerberos key, so six of them collapse to ONE tried-cell; each web app, each
# SSH host, each direct DB validates on its OWN store, so each is its own cell.
# "Refused on N surfaces of the same kind" is therefore ONE earned negative, not N,
# and a whole untried KIND is a door never opened. Unknown kinds get their own bucket,
# never folded into ad-auth: under-counting coverage is the failure this exists to
# catch, so the safe error is an extra alarm, never a hidden gap.

# credential-validation domains. ad-auth is the ONE that collapses (shared AD store).
_AD_AUTH_PORTS = {"88", "135", "139", "445", "389", "636", "3268", "3269",
                  "464", "5985", "5986", "3389"}
_AD_AUTH_TOKENS = ("kerberos", "ldap", "microsoft-ds", "netbios", "smb", "cifs",
                   "winrm", "wsman", "msrpc", "ncacn", "ms-wbt", "rdp",
                   "active directory", "kpasswd")
_SSH_TOKENS = ("ssh", "openssh")
_FTP_TOKENS = ("ftp", "vsftpd", "proftpd", "pure-ftpd")
# direct DB engines, each its own store (SQL/native logins, not AD). mssql is
# deliberately in NEITHER this map NOR ad-auth: its auth is ambiguous (Windows OR SQL
# login), so it gets its own bucket rather than being wrongly collapsed either way.
_DB_PORTS = {"5432": "postgres", "3306": "mysql", "1521": "oracle",
             "27017": "mongodb", "6379": "redis", "5984": "couchdb"}
_DB_TOKENS = ("postgres", "postgresql", "mysql", "mariadb", "mongodb", "mongod",
              "redis", "oracle", "couchdb")


def _cred_surface_kind(g, s) -> str | None:
    """A service's credential-validation domain, as a collapse KEY -- or None if
    authenticating to it is not a distinct door.

    ad-auth collapses to one key (shared AD/NTLM/Kerberos store); everything else is
    keyed per host (or per listener), because each validates independently. Order
    matters: the AD ports/tokens are checked first, so the "http" that WinRM and
    RPC-over-HTTP also carry never miscolours them as a web app. See the block above.
    """
    p = str((s.props or {}).get("port", ""))
    text = f"{s.label or ''} {(s.props or {}).get('product', '')}".lower()
    host = _service_host(g, s) or (f"host:{(s.props or {}).get('host')}"
                                   if (s.props or {}).get("host") else s.id)
    if p in _AD_AUTH_PORTS or any(t in text for t in _AD_AUTH_TOKENS):
        return "ad-auth"
    if p == "22" or any(t in text for t in _SSH_TOKENS):
        return f"ssh:{host}"
    if p == "1433" or "ms-sql" in text or "mssql" in text:
        return f"mssql:{host}:{p}"        # own bucket: ambiguous AD-or-SQL auth
    if p in _DB_PORTS or any(t in text for t in _DB_TOKENS):
        return f"db:{host}:{p}"
    if p == "21" or any(t in text for t in _FTP_TOKENS):
        return f"ftp:{host}:{p}"
    not_web = any(x in text for x in ("ncacn", "winrm", "wsman", "rpc", "msrpc"))
    web_port = p in ("80", "443", "8080", "8443", "8000", "8888", "8081",
                     "8008", "3000", "5000")
    if not not_web and (web_port or "http" in text):
        return f"webapp:{host}:{p}"
    return None


def _held_creds(g) -> list:
    return [n for n in g.nodes.values()
            if n.kind == "cred" and not n.superseded_by and n.held]


def _present_cred_kinds(g) -> dict:
    """Every VERIFIED-present credential-validation surface-kind -> the service nodes
    that realise it. Only verified surfaces count -- no wolf on first contact, same
    rule as `unswept`."""
    kinds = {}
    for s in g.by_kind("service"):
        if s.epistemic != "verified":
            continue
        k = _cred_surface_kind(g, s)
        if k:
            kinds.setdefault(k, []).append(s)
    return kinds


def _cred_tried_kinds(g, cred):
    """(surface-kinds this cred has an attempt against, whether any attempt SUCCEEDED).

    An attempt is a `tested-against` edge cred -> target: the edge existing means the
    cred was tried, its epistemic (verified=success, refuted=fail) or recorded
    attempts carry the outcome. A service target classifies precisely; a host target
    with no port clears ad-auth ONLY when that host actually has an ad-auth surface
    (a cred-vs-host test is an AD auth test) -- record tested-against the SERVICE for
    a precise webapp/db/ssh cell."""
    tried, won = set(), False
    for e in g.out_edges(cred.id):
        if e.rel != "tested-against":
            continue
        tgt = g.nodes.get(e.dst)
        if not tgt or tgt.superseded_by:
            continue
        if tgt.kind == "service":
            k = _cred_surface_kind(g, tgt)
        elif tgt.kind == "host":
            host_kinds = {_cred_surface_kind(g, s) for s in g.by_kind("service")
                          if s.epistemic == "verified"
                          and _service_host(g, s) == tgt.id}
            k = "ad-auth" if "ad-auth" in host_kinds else None
        else:
            k = None
        if k:
            tried.add(k)
        if e.epistemic == "verified" or getattr(e, "succeeded", False):
            won = True
    return tried, won


# --- segment occupancy: a door on a floor we are already standing on ---------------
#
# The failure this closes (fries 2026-09-11). Com held command execution in two
# containers on a docker bridge, observed two more on the same bridge, entered neither,
# and then computed "containers are sealed / the filesystem is unreachable" over the two
# it had entered. `untried` could not see it: that alarm is about a CREDENTIAL with an
# untried surface kind, and this was a HOST nobody had tried at all. `unmined` could not
# see it either, because for ~500 graph events one of those containers had no node.
#
# The alarm is deliberately gated on occupancy. A host somewhere on the internet is not
# interesting; a host on a segment where we already hold access is a door on a floor we
# are standing on, and "exhausted" stated over a subset of that floor is an unearned
# negative every time.

_ADDR_RE = re.compile(r"(?<![0-9.])((?:[0-9]{1,3}\.){3}[0-9]{1,3})(?![0-9.])")

_ENTERED = ("acquired", "examined", "exhausted")


def _host_addr(n) -> str | None:
    """The IPv4 a host node stands for, from its id or label, or None.

    Read from both because ids carry suffixes for re-spawned targets
    (`host:10.129.244.72-i4`) while labels carry prose.
    """
    for text in (n.id, n.label or ""):
        m = _ADDR_RE.search(text)
        if m and all(int(o) <= 255 for o in m.group(1).split(".")):
            return m.group(1)
    return None


def _segment(addr: str) -> str:
    """The /24. Coarse on purpose: a bridge, a lab subnet and a VLAN are all /24-ish in
    practice, and a wrong-but-coarse grouping still puts the untried door on the board.
    """
    return addr.rsplit(".", 1)[0]


def _entry_attempted(g, host) -> bool:
    """Whether entry into this host was ever attempted, so a failure clears the alarm.

    Same earned-negative rule as `untried`: a recorded `tested-against` edge counts
    whatever its outcome. Trying and failing is knowledge; never trying is the hole.
    """
    return any(e.rel == "tested-against" for e in g.in_edges(host.id))


def unentered(g) -> list:
    """Hosts on a segment we already occupy that nobody has ever tried to enter.

    Fires per host, only while we hold access SOMEWHERE on its /24, and only for hosts
    still at `discovered` with no `tested-against` edge. Clears on entry or on a
    recorded attempt. A refuted host does not exist, so it never fires.

    Retired hosts are a real source of noise here and the cure is in the recorder, not
    in this query: supersede a host node when its instance is replaced, and it drops out
    like any other superseded node.
    """
    by_seg: dict = {}
    for h in g.by_kind("host"):
        if h.superseded_by or h.epistemic == "refuted":
            continue
        if h.id == OPERATOR_ID:
            continue
        addr = _host_addr(h)
        if not addr:
            continue
        by_seg.setdefault(_segment(addr), []).append((h, addr))

    out = []
    for seg, members in sorted(by_seg.items()):
        held = [h for h, _ in members if h.exploitation in _ENTERED]
        if not held:
            continue                      # not inside this segment: not our business
        for h, addr in members:
            if h.exploitation in _ENTERED or _entry_attempted(g, h):
                continue
            out.append({
                "id": f"{h.id}#unentered", "host": h.id, "label": h.label,
                "addr": addr, "segment": f"{seg}.0/24",
                "held_on_segment": [x.id for x in held],
                "why": (f"host '{h.label}' sits on {seg}.0/24, where access is already "
                        f"held on {len(held)} host(s), and it has never been entered or "
                        f"even attempted. Any claim that this segment is exhausted, "
                        f"sealed or unreachable is computed over the hosts you DID "
                        f"enter — enter it, or record an attempt and earn the negative."),
            })
    out.sort(key=lambda x: x["host"])
    return out


def _objective_targets(g) -> set:
    """Node ids open objectives route through: their `requires` targets, plus the
    services sitting on a required host (a foothold service is 'adjacent' to the host
    objective it can raise privilege on). Drives the objective-adjacent emphasis."""
    ids, req_hosts = set(), set()
    for obj in g.objectives():
        if obj.status == "achieved":
            continue
        for r in obj.props.get("requires") or []:
            t = r.get("target")
            if not t:
                continue
            ids.add(t)
            if str(t).startswith("host:"):
                req_hosts.add(t)
    if req_hosts:
        for s in g.by_kind("service"):
            if _service_host(g, s) in req_hosts:
                ids.add(s.id)
    return ids


def untried(g) -> list:
    """Held credentials tried-and-failed on one surface kind while another present
    surface kind was never tried at all -- the exploitation-axis mirror of `unswept`.

    Fires only on the DANGEROUS state: a credential that has been tried somewhere, has
    NOT succeeded anywhere, and has a present surface KIND with no attempt -- because
    that is exactly when 'this credential is dead' gets concluded on an unearned
    negative. A held credential nobody has tried yet is `unmined`'s job, not this one;
    a credential that already worked somewhere is live, so an untried other-kind is
    opportunity, not a false wall. Surface kinds collapse per credential store, so six
    AD refusals are ONE tried-cell and the untried web door still fires. One row per
    stuck credential, listing the untried kinds -- bounded, never per-cell wolf.
    """
    present = _present_cred_kinds(g)
    if not present:
        return []
    obj_targets = _objective_targets(g)
    out = []
    for c in _held_creds(g):
        tried, won = _cred_tried_kinds(g, c)
        if won or not tried:            # live cred, or never tried -> unmined's job
            continue
        untried_kinds = [k for k in present if k not in tried]
        if not untried_kinds:
            continue

        def adj(k):
            return any(s.id in obj_targets for s in present[k])

        untried_kinds.sort(key=lambda k: (not adj(k), k))
        cells = [{"kind": k, "objective_adjacent": adj(k),
                  "services": [{"id": s.id, "label": s.label} for s in present[k]]}
                 for k in untried_kinds]
        tried_labels = sorted(tried)
        out.append({
            "id": f"{c.id}#untried", "cred": c.id, "label": c.label,
            "tried_kinds": tried_labels, "untried_kinds": untried_kinds,
            "cells": cells,
            "why": (f"credential '{c.label}' was tried on {tried_labels} and failed, "
                    f"but {len(untried_kinds)} present surface-kind(s) were never "
                    f"tried: {', '.join(untried_kinds)}. Refusals within one credential "
                    f"store are ONE negative, not proof the credential is dead — try "
                    f"it on the surface it may actually be for (record a tested-against "
                    f"edge) before concluding refused."),
        })
    out.sort(key=lambda x: x["cred"])
    return out


def blocked_but_untried(g) -> list:
    """No objective is winnable right now AND a held credential has a present surface
    kind it was never tried against -- the exploitation-axis twin of
    `blocked_but_unswept`. 'Every path is credential-gated' is suspect while a
    credential you already hold has an untried door. Fires only while stuck (no
    reachable_now) and clears the moment a win opens or every held cred's present
    surface kinds carry an attempt.
    """
    fr = frontier(g)
    if fr["reachable_now"]:
        return []
    if not (fr["reachable_if"] + fr["unreachable"]):
        return []
    present = _present_cred_kinds(g)
    if not present:
        return []
    stuck = []
    for c in _held_creds(g):
        tried, won = _cred_tried_kinds(g, c)
        if won:
            continue
        untried_kinds = [k for k in present if k not in tried]
        if untried_kinds:
            stuck.append({"cred": c.id, "label": c.label,
                          "untried_kinds": untried_kinds})
    if not stuck:
        return []
    return [{
        "id": "blocked-but-untried", "creds": len(stuck), "detail": stuck,
        "why": (f"No objective is winnable right now, yet {len(stuck)} held "
                f"credential(s) have present surface-kinds never tried against them. "
                f"Before concluding blocked or credential-gated, try each held "
                f"credential on the surface it may be for — a door you hold the key to "
                f"but never opened is not a wall."),
    }]


# --- feature 1: the delta board ----------------------------------------------

def _alarm_ids(g):
    return ({o["id"] for o in unrealized(g)},
            {u["id"] for u in unmined(g)},
            {s["id"] for s in stale(g)},
            {o["id"] for o in frontier(g)["reachable_now"]},
            {u["id"] for u in unswept(g)},
            {u["id"] for u in untried(g)},
            {u["id"] for u in unentered(g)})


def delta(before, after) -> dict:
    """What changed between two folds of the same log.

    The compass property: under information overload you do not want state, you
    want the CHANGE in state. A 60-node board becomes three lines, and stays
    three lines however large the engagement grows.
    """
    b_unreal, b_unmined, b_stale, b_now, b_unswept, b_untried, b_unent = _alarm_ids(before)
    a_unreal, a_unmined, a_stale, a_now, a_unswept, a_untried, a_unent = _alarm_ids(after)

    def label(nid):
        n = after.nodes.get(nid)
        return n.label if n else nid

    # unswept/untried ids are synthetic (service#unswept:method, cred#untried), not
    # node ids, so give them a human label from the live computation, not `label()`.
    unswept_after = {u["id"]: f"{u['method']} — {u['label']}"
                     for u in unswept(after)}
    untried_after = {u["id"]: f"{u['label']} — untried: {', '.join(u['untried_kinds'])}"
                     for u in untried(after)}

    def uswlabel(nid):
        return unswept_after.get(nid, nid)

    def untlabel(nid):
        return untried_after.get(nid, nid)

    unentered_after = {u["id"]: f"{u['label']} — on {u['segment']}, never entered"
                       for u in unentered(after)}

    def unentlabel(nid):
        return unentered_after.get(nid, nid)

    newly_winnable = [{"id": i, "label": label(i)} for i in a_now - b_now]
    resolved = []
    for eid, e in after.edges.items():
        old = before.edges.get(eid)
        if old is not None and old.epistemic != e.epistemic and \
                e.epistemic in ("verified", "refuted"):
            resolved.append({"id": eid, "from": old.epistemic, "to": e.epistemic})
    for nid, n in after.nodes.items():
        old = before.nodes.get(nid)
        if old is not None and old.epistemic != n.epistemic and \
                n.epistemic in ("verified", "refuted"):
            resolved.append({"id": nid, "from": old.epistemic, "to": n.epistemic})

    return {
        "from_seq": before.seq,
        "to_seq": after.seq,
        "events": after.seq - before.seq,
        "new_nodes": [{"id": i, "label": after.nodes[i].label,
                       "kind": after.nodes[i].kind}
                      for i in set(after.nodes) - set(before.nodes)],
        "newly_winnable": newly_winnable,
        "new_unrealized": [{"id": i, "label": label(i)} for i in a_unreal - b_unreal],
        "new_unmined": [{"id": i, "label": label(i)} for i in a_unmined - b_unmined],
        "cleared_unmined": [{"id": i, "label": label(i)} for i in b_unmined - a_unmined],
        "new_stale": [{"id": i, "label": label(i)} for i in a_stale - b_stale],
        "new_unswept": [{"id": i, "label": uswlabel(i)} for i in a_unswept - b_unswept],
        "cleared_unswept": [{"id": i, "label": uswlabel(i)} for i in b_unswept - a_unswept],
        "new_untried": [{"id": i, "label": untlabel(i)} for i in a_untried - b_untried],
        "cleared_untried": [{"id": i, "label": untlabel(i)} for i in b_untried - a_untried],
        "new_unentered": [{"id": i, "label": unentlabel(i)} for i in a_unent - b_unent],
        "cleared_unentered": [{"id": i, "label": unentlabel(i)} for i in b_unent - a_unent],
        "resolved": resolved,
        "decisions": [d for d in after.decisions if d["seq"] > before.seq],
        "budget_blown": budget(after),
    }
