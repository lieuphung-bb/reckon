"""Self-contained HTML console: reckon's derive layer feeding topo's renderer.

The board / drawer / chain-graph / filter-chip shape is ported from
tools/topo/topo.py (own prior work). What changed is where the data comes from:
topo rendered facts asserted in YAML, this renders CONCLUSIONS derived from the
event graph - reachability by Dijkstra, objectives satisfiable now, assets
reachable and never examined, the verification queue.

Offline and dependency-free: one file, no CDN, no fonts, no network.
"""

import html as _html
import json
import re

from ..queries import (frontier, unrealized, unmined, stale, coverage, reach,
                       verification_queue, why, budget)
from ..recall import suggestions as _suggestions
from ..render.views import RENDERERS


def esc(s):
    return _html.escape("" if s is None else str(s))


def _jsarg(v):
    """A JS string literal safe inside a double-quoted HTML attribute.

    json.dumps emits double quotes, so onclick="view("board")" terminated the
    attribute early and every view button was dead in a browser while still
    parsing fine in Python.
    """
    return _html.escape(json.dumps(v), quote=True)


# --- markdown -> html (only what the six views emit) --------------------------

def md2html(md: str) -> str:
    out, i, lines = [], 0, md.splitlines()
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            lvl = min(len(m.group(1)) + 1, 5)
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if ln.strip().startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip()):
            hdr = [c.strip() for c in ln.strip().strip("|").split("|")]
            out.append("<table><thead><tr>"
                       + "".join(f"<th>{inline(h)}</th>" for h in hdr)
                       + "</tr></thead><tbody>")
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells)
                           + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue
        if re.match(r"^\s*[-*]\s+", ln):
            out.append("<ul>")
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                out.append("<li>" + inline(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                           + "</li>")
                i += 1
            out.append("</ul>")
            continue
        if re.match(r"^\s*\d+\.\s+", ln):
            out.append("<ol>")
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                out.append("<li>" + inline(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                           + "</li>")
                i += 1
            out.append("</ol>")
            continue
        if ln.strip().startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(esc(lines[i]))
                i += 1
            i += 1
            out.append("<pre>" + "\n".join(buf) + "</pre>")
            continue
        if ln.strip():
            out.append(f"<p>{inline(ln)}</p>")
        i += 1
    return "\n".join(out)


def inline(s: str) -> str:
    """Inline markdown. Every rule must produce BALANCED tags or not fire at all.

    Engagement prose is full of identifiers with underscores, dotted addresses and stray backticks, so a
    permissive `_(.+?)_` emitted orphan </i> tags. Italics now need non-word
    boundaries, and code spans only convert in pairs.
    """
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    if s.count("`") % 2 == 0:
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    else:
        s = s.replace("`", "")
    s = re.sub(r"(?<![\w\\])_([^_\s][^_]*?)_(?![\w])", r"<i>\1</i>", s)
    return s


# --- access classification (the visual language) ------------------------------

def access_class(info):
    """reckon reachability -> topo's colour vocabulary, plus `cond` for reachable-if."""
    if info is None:
        return "no-route"
    if info["cost"] > 0:
        return "cond"
    r = info["rank"]
    return "owned" if r >= 3 else ("app" if r >= 1 else "reachable")


CSS = """
:root{--bg:#0f1216;--panel:#171b22;--panel2:#1d222b;--ink:#e7ecf3;--mut:#8a97a8;
--line:#2a313c;--owned:#1f8a5b;--app:#c79320;--reach:#3f6ea6;--noroute:#4a525e;
--cond:#7c5cc4;--obj:#c23b52;--star:#ffd45e;--accent:#7fa6cf;--warn:#e0603a;}
@media(prefers-color-scheme:light){:root{--bg:#f5f7fa;--panel:#fff;--panel2:#eef2f7;
--ink:#1a2230;--mut:#5a6675;--line:#d7dee7;}}
*{box-sizing:border-box}
body{margin:0;font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--bg);color:var(--ink)}
header{display:flex;align-items:center;gap:14px;padding:10px 16px;border-bottom:1px solid var(--line);
position:sticky;top:0;background:var(--bg);z-index:5;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:600}
.sub{color:var(--mut)}
.star{color:var(--star)}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{cursor:pointer;border:1px solid var(--line);border-radius:12px;padding:2px 9px;color:var(--mut);
background:var(--panel);user-select:none}
.chip.on{color:var(--ink);border-color:var(--accent)}
.chip.owned.on{background:var(--owned);border-color:var(--owned);color:#fff}
.chip.app.on{background:var(--app);border-color:var(--app);color:#20160a}
.chip.reachable.on{background:var(--reach);border-color:var(--reach);color:#fff}
.chip.cond.on{background:var(--cond);border-color:var(--cond);color:#fff}
.chip.v.on{background:var(--accent);border-color:var(--accent);color:#0f1216}
main{display:flex;gap:0}
.pane{flex:1;padding:14px;overflow:auto;display:none}
.pane.show{display:block}
#pane-board{gap:12px;overflow-x:auto;align-items:flex-start}
#pane-board.show{display:flex}
.zone{min-width:220px;flex:1}
.zone>summary{cursor:pointer;font-weight:600;padding:6px 8px;border:1px solid var(--line);
border-radius:8px 8px 0 0;background:var(--panel2);list-style:none}
.zone>summary::-webkit-details-marker{display:none}
.zbody{border:1px solid var(--line);border-top:none;border-radius:0 0 8px 8px;padding:8px;display:flex;
flex-direction:column;gap:7px;background:var(--panel)}
.card{border:1px solid var(--line);border-left-width:4px;border-radius:6px;padding:6px 8px;cursor:pointer;background:var(--panel2)}
.card:hover{border-color:var(--accent)}
.card.owned{border-left-color:var(--owned)}
.card.app{border-left-color:var(--app)}
.card.reachable{border-left-color:var(--reach)}
.card.cond{border-left-color:var(--cond)}
.card.no-route,.card.unknown{border-left-color:var(--noroute);opacity:.75}
.card.obj{border-left-color:var(--obj)}
.card .n{font-weight:600}
.card .m{color:var(--mut);font-size:11px}
.card .warn{color:var(--warn);font-size:11px;font-weight:600}
.card.hide{display:none}
#drawer{width:360px;border-left:1px solid var(--line);padding:14px;overflow-y:auto;height:calc(100vh - 52px);
position:sticky;top:52px;background:var(--panel)}
#drawer h2{font-size:14px;margin:0 0 2px}
#drawer .lead{color:var(--mut);margin-bottom:10px}
.sec{border-top:1px solid var(--line);margin-top:10px;padding-top:8px}
.sec>b{color:var(--accent);font-size:11px;letter-spacing:.04em;text-transform:uppercase;cursor:pointer;
display:block}
.sec.collapsed .body{display:none}
.sec>b:before{content:"▾ ";color:var(--mut)}
.sec.collapsed>b:before{content:"▸ "}
.pill{display:inline-block;border-radius:10px;padding:0 7px;font-size:10px;margin:2px 3px 0 0}
.pill.owned{background:var(--owned);color:#fff}.pill.app{background:var(--app);color:#20160a}
.pill.reachable{background:var(--reach);color:#fff}.pill.cond{background:var(--cond);color:#fff}
.pill.no-route,.pill.unknown{background:var(--noroute);color:#fff}
.pill.val{background:#274c37;color:#8fe3b4}.pill.unval{background:#5a3a1e;color:#f0c088}
.pill.warn{background:var(--warn);color:#fff}
.kv{margin:4px 0}.kv .k{color:var(--mut)}
.alarm{border-left:3px solid var(--warn);padding:4px 8px;margin:6px 0;background:var(--panel2)}
ul{margin:4px 0;padding-left:16px}li{margin:2px 0}
.empty{color:var(--mut);text-align:center;margin-top:40px}
table{border-collapse:collapse;margin:6px 0;font-size:12px;width:100%}
th,td{border:1px solid var(--line);padding:3px 6px;text-align:left;vertical-align:top}
th{background:var(--panel2);color:var(--mut);font-weight:600}
pre{background:var(--panel2);border:1px solid var(--line);border-radius:4px;padding:8px;overflow-x:auto}
code{background:var(--panel2);border-radius:3px;padding:0 3px}
.node{cursor:pointer}
.node rect{stroke:var(--line);stroke-width:1.5;fill:var(--panel2)}
.node .nt{font-weight:600;fill:var(--ink);font-size:11px}
.node .nm{fill:var(--mut);font-size:9px}
.node.owned rect{stroke:var(--owned);stroke-width:2.5}
.node.app rect{stroke:var(--app);stroke-width:2}
.node.reachable rect{stroke:var(--reach)}
.node.cond rect{stroke:var(--cond);stroke-dasharray:4 3}
.node.obj rect{stroke:var(--obj);stroke-width:2.5;fill:#2a1620}
.node:hover rect{stroke:var(--accent)}
.edge{fill:none;stroke-width:2}
.edge.verified{stroke:var(--accent)}
.edge.hypothesized{stroke:var(--cond);stroke-dasharray:5 4}
.edge.refuted{stroke:var(--noroute);stroke-dasharray:1 4}
.legend{display:flex;gap:16px;flex-wrap:wrap;margin:2px 0 12px;color:var(--mut)}
.legend i{display:inline-block;width:22px;height:0;vertical-align:middle;margin-right:5px;border-top-width:3px;border-top-style:solid}
"""

JS = """
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
let filters={owned:1,app:1,reachable:1,cond:1,'no-route':1,warnonly:0};

function sec(title, body, collapsed){
  if(!body) return '';
  return `<div class="sec${collapsed?' collapsed':''}"><b onclick="this.parentNode.classList.toggle('collapsed')">${title}</b><div class="body">${body}</div></div>`;
}

function drawNode(id){
  const n=N[id]; if(!n) return;
  const d=document.getElementById('drawer');
  let alarms='';
  (n.alarms||[]).forEach(a=>{alarms+=`<div class="alarm">⚠ ${esc(a)}</div>`;});
  const path=(n.path||[]).map(p=>`<li>${esc(p.from)} <span class="k">→${esc(p.rel)}→</span> ${esc(p.to)} <span class="pill ${p.state==='verified'?'val':'unval'}">${esc(p.state)}</span></li>`).join('');
  const props=Object.entries(n.props||{}).map(([k,v])=>`<div class="kv"><span class="k">${esc(k)}</span> ${esc(v)}</div>`).join('');
  const opens=(n.opens||[]).map(o=>`<li>${esc(o.to)} <span class="k">${esc(o.rel)}</span> <span class="pill ${o.state==='verified'?'val':'unval'}">${esc(o.state)}</span></li>`).join('');
  const opened=(n.openedBy||[]).map(o=>`<li>${esc(o.from)} <span class="k">${esc(o.rel)}</span> <span class="pill ${o.state==='verified'?'val':'unval'}">${esc(o.state)}</span></li>`).join('');
  const objs=(n.objectives||[]).map(o=>`<li>${o.crown?'★ ':''}${esc(o.label)} <span class="pill ${o.status==='achieved'?'val':'warn'}">${esc(o.status)}</span></li>`).join('');
  const notes=(n.notes||[]).map(x=>`<li>${esc(x)}</li>`).join('');
  // Reference layer (phase 2): the engagement graph stores only (store,label,key).
  // Unresolved they still read as provenance; with a resolver wired they gain titles.
  const rec=(n.recall||[]).map(r=>`<li>${r.confirmed?'✓':'?'} <b>${esc(r.technique)}</b> <span class="k">seen ${r.seen}x — ${esc((r.engagements||[]).join(', '))}</span></li>`).join('');
  const att=(n.attempts||[]).map(a=>`<li><span class="pill ${a.outcome==='succeeded'?'val':'unval'}">${esc(a.outcome)}</span> ${esc(a.note||'')}</li>`).join('');
  const refs=(n.references||[]).map(r=>`<li><code>${esc(r.store)}</code> ${esc(r.label)} <b>${esc(r.key)}</b>${r.title?' — '+esc(r.title):''}</li>`).join('');
  d.innerHTML=`<h2>${n.crown?'★ ':''}${esc(n.label)}</h2>`
    +`<div class="lead">${esc(n.kind)}${n.zone?' · '+esc(n.zone):''}${n.ip?' · '+esc(n.ip):''}</div>`
    +`<div><span class="pill ${n.acc}">${esc(n.acc)}</span>`
    +`<span class="pill ${n.epistemic==='verified'?'val':'unval'}">${esc(n.epistemic)}</span>`
    +`<span class="pill ${n.exploitation==='examined'||n.exploitation==='exhausted'?'val':'unval'}">${esc(n.exploitation)}</span>`
    +(n.rank?`<span class="pill app">rank ${n.rank}</span>`:'')+`</div>`
    +alarms
    +sec('how i get here', path?`<ul>${path}</ul>`:'')
    +sec('objectives needing this', objs?`<ul>${objs}</ul>`:'')
    +sec('it opens', opens?`<ul>${opens}</ul>`:'', true)
    +sec('opened by', opened?`<ul>${opened}</ul>`:'', true)
    +sec('worked before (own history)', rec?`<ul>${rec}</ul>`:'')
    +sec('attempts', att?`<ul>${att}</ul>`:'')
    +sec('reference layer', refs?`<ul>${refs}</ul>`:'')
    +sec('detail', props, true)
    +sec('notes', notes?`<ul>${notes}</ul>`:'', true);
  document.querySelectorAll('.card').forEach(c=>c.style.outline='');
  const el=document.querySelector(`[data-n="${CSS.escape(id)}"]`); if(el) el.style.outline='2px solid var(--accent)';
}

function visible(n){
  if(!n) return false;
  if(filters.warnonly && !(n.alarms||[]).length) return false;
  // Objectives are goals, not positions. Their acc is always 'no-route' because
  // no edge points AT a goal, so filtering them by access class hid every
  // objective the moment "no route" was unticked.
  if(n.kind==='objective') return true;
  return !!filters[n.acc];
}
function applyFilters(){
  document.querySelectorAll('.card').forEach(c=>{
    c.classList.toggle('hide', !visible(N[c.dataset.n]));});
}
function toggleChip(k,el){filters[k]=!filters[k];el.classList.toggle('on',!!filters[k]);applyFilters();}
function view(v){
  sessionStorage.setItem('eng_pane', v);
  ['board','chain','brief','assumptions','threat_model','plan','recon'].forEach(x=>{
    const el=document.getElementById('pane-'+x); if(el) el.classList.toggle('show',x===v);
    const b=document.getElementById('vb-'+x); if(b) b.classList.toggle('on',x===v);});
}
// Auto-reload, ported from tools/topo. The page is a static artifact: it does
// NOT re-derive itself. Re-running `reckon console` rewrites the file, and with
// this on the open tab picks it up on the next tick - which is what makes it
// usable as a live board mid-engagement instead of a snapshot you keep
// forgetting to regenerate. Persisted, so it survives the reload it causes.
let ar = localStorage.getItem('eng_ar')==='1';
function setAR(on){
  ar=on; localStorage.setItem('eng_ar', on?'1':'0');
  const b=document.getElementById('arbtn'); if(b) b.classList.toggle('on',on);
  if(window._art) clearInterval(window._art);
  if(on) window._art=setInterval(()=>location.reload(), 5000);
}
window.addEventListener('load',()=>{
  // Restore the pane the reload interrupted, or the board on a cold open.
  view(sessionStorage.getItem('eng_pane') || 'board');
  setAR(ar);
});
"""


def _mermaidless_svg(g, r, nodes_meta):
    """Layered SVG: column = path length from the operator, so the picture reads
    left-to-right as 'how far from what I already hold'."""
    depth = {}
    for nid, info in r.items():
        depth[nid] = len(info["path"])
    for nid in g.nodes:
        depth.setdefault(nid, max(depth.values(), default=0) + 1)

    cols = {}
    for nid, d in depth.items():
        n = g.nodes.get(nid)
        if not n or n.superseded_by:
            continue
        cols.setdefault(d, []).append(nid)

    CW, RH, BW, BH = 250, 62, 190, 40
    pos, height = {}, 0
    for d in sorted(cols):
        for i, nid in enumerate(sorted(cols[d])):
            pos[nid] = (40 + d * CW, 30 + i * RH)
            height = max(height, 30 + i * RH + BH + 30)
    width = 60 + (max(cols) + 1) * CW if cols else 400

    parts = [f'<svg viewBox="0 0 {width} {max(height,200)}" width="{width}" '
             f'height="{max(height,200)}" xmlns="http://www.w3.org/2000/svg">']
    for e in g.edges.values():
        if e.src not in pos or e.dst not in pos:
            continue
        x1, y1 = pos[e.src]
        x2, y2 = pos[e.dst]
        x1 += BW
        y1 += BH / 2
        y2 += BH / 2
        mx = (x1 + x2) / 2
        parts.append(f'<path class="edge {esc(e.epistemic)}" '
                     f'd="M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}"/>')
    for nid, (x, y) in pos.items():
        n = g.nodes[nid]
        meta = nodes_meta.get(nid, {})
        cls = meta.get("acc", "no-route")
        if n.kind == "objective":
            cls = "obj"
        label = (n.label[:25] + "…") if len(n.label) > 26 else n.label
        sub = n.kind + (f" · {meta.get('rank')}" if meta.get("rank") else "")
        parts.append(
            f'<g class="node {cls}" onclick="drawNode({_jsarg(nid)})">'
            f'<rect x="{x}" y="{y}" width="{BW}" height="{BH}" rx="6"/>'
            f'<text class="nt" x="{x+9}" y="{y+17}">{esc(label)}</text>'
            f'<text class="nm" x="{x+9}" y="{y+31}">{esc(sub)}</text></g>')
    parts.append("</svg>")
    return "".join(parts)


def console(g, name: str) -> str:
    r = reach(g)
    f, ur, um, st = frontier(g), unrealized(g), unmined(g), stale(g)
    cov, vq = coverage(g), verification_queue(g)

    bl = budget(g)
    bl_ids = {b["id"]: b["advice"] for b in bl}
    try:
        sug = _suggestions(g, limit=3)
    except Exception:
        sug = {}
    um_ids = {u["id"] for u in um}
    st_ids = {s["id"]: s["reason"] for s in st}
    ur_ids = {u["id"] for u in ur}

    # objectives indexed by the node they require
    needs = {}
    for obj in g.objectives():
        for req in obj.props.get("requires") or []:
            needs.setdefault(req.get("target"), []).append(
                {"label": obj.label, "status": obj.status or "open",
                 "crown": bool(obj.props.get("crown_jewel"))})

    meta = {}
    for nid, n in g.nodes.items():
        if n.superseded_by or n.kind == "operator":
            continue
        info = r.get(nid)
        acc = access_class(info)
        alarms = []
        if nid in ur_ids:
            alarms.append("satisfiable RIGHT NOW and not done")
        if nid in um_ids:
            alarms.append("reachable and never examined")
        if nid in st_ids:
            alarms.append(st_ids[nid])
        if nid in bl_ids:
            alarms.append(bl_ids[nid])
        if n.kind == "objective" and n.status != "achieved":
            hit = [x for x in f["reachable_now"] if x["id"] == nid]
            if hit:
                alarms.append("reachable now")
        meta[nid] = {
            "id": nid, "label": n.label, "kind": n.kind,
            "zone": n.props.get("zone", ""), "ip": n.props.get("ip", ""),
            "acc": acc, "rank": info["rank"] if info else 0,
            "epistemic": n.epistemic, "exploitation": n.exploitation,
            "crown": bool(n.props.get("crown_jewel")),
            "status": n.status, "props": n.props, "notes": n.notes,
            "alarms": alarms,
            "objectives": needs.get(nid, []),
            "references": n.props.get("references") or [],
            "recall": sug.get(nid, []),
            "attempts": n.attempts,
            "path": [{"from": g.edges[e].src, "to": g.edges[e].dst,
                      "rel": g.edges[e].rel, "state": g.edges[e].epistemic}
                     for e in (info["path"] if info else [])],
            "opens": [{"to": e.dst, "rel": e.rel, "state": e.epistemic}
                      for e in g.out_edges(nid)],
            "openedBy": [{"from": e.src, "rel": e.rel, "state": e.epistemic}
                         for e in g.in_edges(nid)],
        }

    # ---- board grouped by zone
    zones = {}
    for nid, m in meta.items():
        if m["kind"] == "objective":
            zones.setdefault("objectives", []).append(m)
        else:
            zones.setdefault(m["zone"] or m["kind"], []).append(m)

    board = []
    for zname in sorted(zones, key=lambda z: (z == "objectives", z)):
        items = sorted(zones[zname], key=lambda m: (m["kind"], m["label"]))
        board.append(f'<details class="zone" open><summary>{esc(zname)} '
                     f'<span class="sub">{len(items)}</span></summary><div class="zbody">')
        for m in items:
            cls = "obj" if m["kind"] == "objective" else m["acc"]
            warn = (f'<div class="warn">⚠ {esc(m["alarms"][0])}</div>'
                    if m["alarms"] else "")
            board.append(
                f'<div class="card {cls}" data-n="{esc(m["id"])}" '
                f'onclick="drawNode({_jsarg(m["id"])})">'
                f'<div class="n">{"★ " if m["crown"] else ""}{esc(m["label"])}</div>'
                f'<div class="m">{esc(m["kind"])}'
                f'{" · " + esc(m["ip"]) if m["ip"] else ""}'
                f'{" · " + esc(m["status"]) if m["status"] else ""}</div>{warn}</div>')
        board.append("</div></details>")

    # ---- doc panes from the six views (single source: views.py)
    panes = {}
    for key in ("assumptions", "attack_brief", "threat_model", "plan", "recon"):
        panes[key] = md2html(RENDERERS[key](g, name))

    stats = (f"{cov['achieved']}/{cov['objectives_total']} objectives · "
             f"now {len(f['reachable_now'])} · if {len(f['reachable_if'])} · "
             f"unreachable {len(f['unreachable'])} · "
             f"⚠ {len(ur)} unrealized · {len(um)} unmined · {len(st)} unverified"
             + (f" · {len(bl)} budget-blown" if bl else ""))

    chips = "".join(
        f'<span class="chip {k} on" onclick="toggleChip({_jsarg(k)},this)">{lbl}</span>'
        for k, lbl in (("owned", "owned"), ("app", "app"), ("reachable", "reachable"),
                       ("cond", "reachable-if"), ("no-route", "no route")))
    chips += ('<span class="chip v" onclick="toggleChip(\'warnonly\',this)">'
              '⚠ alarms only</span>')

    vbtns = "".join(
        f'<span class="chip" id="vb-{k}" onclick="view({_jsarg(k)})">{lbl}</span>'
        for k, lbl in (("board", "board"), ("chain", "chain"),
                       ("brief", "brief"), ("assumptions", "assumptions"),
                       ("threat_model", "threat model"), ("plan", "plan"),
                       ("recon", "recon")))

    svg = _mermaidless_svg(g, r, meta)
    legend = ('<div class="legend">'
              '<span><i style="border-top-color:var(--accent)"></i>verified</span>'
              '<span><i style="border-top-color:var(--cond);border-top-style:dashed"></i>hypothesized</span>'
              '<span>column = steps from what you already hold</span></div>')

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>reckon — {esc(name)}</title><style>{CSS}</style></head><body>
<header>
  <h1>reckon · {esc(name)}</h1>
  <span class="sub">{esc(stats)}</span>
  <div class="chips">{vbtns}</div>
  <div class="chips">{chips}</div>
</header>
<main>
  <div id="pane-board" class="pane show">{''.join(board)}</div>
  <div id="pane-chain" class="pane">{legend}{svg}</div>
  <div id="pane-brief" class="pane">{panes['attack_brief']}</div>
  <div id="pane-assumptions" class="pane">{panes['assumptions']}</div>
  <div id="pane-threat_model" class="pane">{panes['threat_model']}</div>
  <div id="pane-plan" class="pane">{panes['plan']}</div>
  <div id="pane-recon" class="pane">{panes['recon']}</div>
  <aside id="drawer"><div class="empty">click a node</div></aside>
</main>
<script>const N={json.dumps(meta)};</script>
<script>{JS}</script>
</body></html>"""
