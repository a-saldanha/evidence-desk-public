"""Self-contained force-graph HTML for the reconciliation view, embedded via st.components.v1.html.

The force-graph library is bundled under app/assets/ so the page works offline and inside the baked
Docker image (no CDN call at runtime). Inspection in the embedded panel shows model-written summaries only;
the verbatim (and UI-masked) cited quotes stay in the Streamlit review controls below the graph.

Clicking small moving nodes is fiddly, so the panel also carries a type-to-filter list of every node:
pick a row to jump to it (centre + highlight + detail). Nodes also have an enlarged click hit-area and are
pinned once the layout settles, so direct clicking is reliable too.
"""
from __future__ import annotations
import json
from pathlib import Path

_LIB = Path(__file__).resolve().parent / "assets" / "force-graph.min.js"

VERDICT = {"confirmed": "#2E6B3F", "modified": "#B26A00", "contradicted": "#B3261E"}
STATUS = {"planned": "#9AA5B1", "scheduled": "#2F5D8A", "happened": "#2E6B3F",
          "reported_state": "#B26A00", "cancelled": "#B3261E", "unknown": "#C9CED6"}
_STATUS_LABEL = {"planned": "planned", "scheduled": "booked", "happened": "happened",
                 "reported_state": "reported state", "cancelled": "cancelled", "unknown": "unclear"}


def _status_swatches() -> str:
    return " ".join(f'<span class="sw" style="background:{c}"></span>{_STATUS_LABEL[k]}' for k, c in STATUS.items())


def recon_html(nodes, links, insights, show_labels: bool = True, height: int = 560) -> str:
    """Return a fully self-contained HTML document rendering the plan-vs-evidence graph."""
    lib = _LIB.read_text() if _LIB.exists() else ""
    lib_tag = f"<script>{lib}</script>" if lib else '<script src="https://unpkg.com/force-graph"></script>'
    data = json.dumps({"nodes": nodes, "links": links, "insights": insights}).replace("</", "<\\/")
    return (_TEMPLATE
            .replace("__LIB__", lib_tag)
            .replace("__DATA__", data)
            .replace("__LABELS__", "true" if show_labels else "false")
            .replace("__SWATCHES__", _status_swatches())
            .replace("__H__", str(height)))


_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
__LIB__
<style>
  * { box-sizing:border-box; }
  body { margin:0; font:13px/1.4 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; color:#1F2933; }
  .legend { padding:8px 12px; border:1px solid #E4E7EB; border-radius:8px; margin:0 0 8px; background:#FBFCFD; }
  .legend .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:2px 0; font-size:12px; color:#3E4A59; }
  .legend b { color:#1F2933; font-weight:600; margin-right:2px; }
  .sw { display:inline-block; width:11px; height:11px; border-radius:50%; margin:0 3px 0 8px; vertical-align:middle; border:1px solid #fff; box-shadow:0 0 0 1px #d8dde3; }
  .ln { display:inline-block; width:18px; height:0; border-top:3px solid; margin:0 4px 0 8px; vertical-align:middle; }
  .shape { font-size:13px; }
  .ring { display:inline-block; width:11px; height:11px; border-radius:50%; border:2.5px solid #2E6B3F; margin:0 4px 0 8px; vertical-align:middle; }
  #wrap { display:flex; gap:10px; height:__H__px; }
  #graph { flex:1; border:1px solid #E4E7EB; border-radius:8px; overflow:hidden; background:#FBFCFD; position:relative; }
  #graph .hint { position:absolute; bottom:8px; left:10px; font-size:11px; color:#5A6472; background:rgba(255,255,255,.85); padding:2px 7px; border-radius:4px; }
  #side { width:310px; border:1px solid #E4E7EB; border-radius:8px; padding:10px 12px; display:flex; flex-direction:column; min-height:0; }
  #q { width:100%; padding:6px 9px; border:1px solid #D2D8DF; border-radius:6px; font-size:12.5px; outline:none; }
  #q:focus { border-color:#2F5D8A; }
  #list { margin-top:6px; max-height:160px; overflow-y:auto; border-bottom:1px solid #EEF1F4; }
  .r2 { display:flex; align-items:center; gap:6px; padding:4px 5px; border-radius:5px; cursor:pointer; font-size:12px; }
  .r2:hover { background:#EEF3F8; }
  .r2.sel { background:#E1ECF7; }
  .r2 .sh { width:14px; text-align:center; color:#5A6472; }
  .r2 .tt { flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .cnt { font-size:11px; color:#8A94A0; margin:4px 2px 0; }
  #detail { flex:1; overflow-y:auto; margin-top:8px; min-height:0; }
  #detail .empty { color:#5A6472; margin-top:24px; text-align:center; font-size:12px; }
  #detail h2 { font-size:14px; margin:0 0 2px; }
  #detail .meta { color:#5A6472; font-size:11.5px; margin-bottom:10px; }
  .verdict { border-left:4px solid #ccc; padding:6px 10px; border-radius:4px; background:#F7F9FB; margin:9px 0 2px; font-size:12.5px; }
  .verdict.confirmed { border-color:#2E6B3F; } .verdict.modified { border-color:#B26A00; } .verdict.contradicted { border-color:#B3261E; }
  .vlabel { font-weight:600; text-transform:capitalize; }
</style></head><body>
<div class="legend">
  <div class="row"><b>Shapes</b> <span class="shape">◆</span> plan — an intended or booked event &nbsp;&nbsp; <span class="shape">●</span> evidence — what another source shows happened</div>
  <div class="row"><b>Line colour = verdict</b> <span class="ln" style="border-color:#2E6B3F"></span>confirmed <span class="ln" style="border-color:#B26A00"></span>modified <span class="ln" style="border-color:#B3261E"></span>contradicted &nbsp;·&nbsp; <span class="ring"></span> green ring = you accepted this verdict</div>
  <div class="row"><b>Node colour = event status</b> __SWATCHES__</div>
</div>
<div id="wrap">
  <div id="graph"><div class="hint">drag · scroll to zoom · click a node — or search it in the panel →</div></div>
  <div id="side">
    <input id="q" placeholder="&#128269;  Search events to jump to&#8230;" autocomplete="off">
    <div class="cnt" id="cnt"></div>
    <div id="list"></div>
    <div id="detail"><div class="empty">Click a node, or pick one from the list above, to see its plan-vs-evidence verdict(s).</div></div>
  </div>
</div>
<script>
const DATA = __DATA__;
const VERDICT = { confirmed:"#2E6B3F", modified:"#B26A00", contradicted:"#B3261E" };
const STATUS  = { planned:"#9AA5B1", scheduled:"#2F5D8A", happened:"#2E6B3F", reported_state:"#B26A00", cancelled:"#B3261E", unknown:"#C9CED6" };
let showLabels = __LABELS__, hoverId = null, selectedId = null, pinned = false;
const byId = Object.fromEntries(DATA.nodes.map(n => [n.id, n]));
const adj = {}; DATA.nodes.forEach(n => adj[n.id] = new Set());
DATA.links.forEach(l => { adj[l.source].add(l.target); adj[l.target].add(l.source); });

const el = document.getElementById("graph");
const G = ForceGraph()(el)
  .graphData(DATA).nodeId("id").backgroundColor("#FBFCFD")
  .linkColor(l => l.rejected ? "rgba(150,160,170,.35)"
                 : (hoverId && !(l.source.id===hoverId||l.target.id===hoverId)) ? "rgba(150,160,170,.12)" : VERDICT[l.verdict])
  .linkWidth(l => (l.source.id===hoverId||l.target.id===hoverId) ? 2.8 : (l.rejected ? 1 : 1.8))
  .linkLineDash(l => l.rejected ? [3,3] : null)
  .nodePointerAreaPaint((node, color, ctx) => {          // enlarged, easy-to-hit click area
    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(node.x, node.y, 10, 0, 2*Math.PI); ctx.fill();
  })
  .nodeCanvasObject((node, ctx, scale) => {
    const dim = hoverId && node.id!==hoverId && !adj[hoverId].has(node.id);
    const r = node.isPlan ? 6 : 4.2;
    ctx.globalAlpha = dim ? 0.22 : 1;
    ctx.fillStyle = STATUS[node.status] || "#C9CED6";
    ctx.strokeStyle = node.accepted ? "#2E6B3F" : "#ffffff";
    ctx.lineWidth = (node.accepted ? 2.4 : 1.3) / scale;
    ctx.beginPath();
    if (node.isPlan) { ctx.moveTo(node.x, node.y-r); ctx.lineTo(node.x+r, node.y); ctx.lineTo(node.x, node.y+r); ctx.lineTo(node.x-r, node.y); ctx.closePath(); }
    else { ctx.arc(node.x, node.y, r, 0, 2*Math.PI); }
    ctx.fill(); ctx.stroke();
    if (node.id === selectedId) {                         // selection halo
      ctx.globalAlpha = 1; ctx.strokeStyle = "#1F6FEB"; ctx.lineWidth = 2.2/scale;
      ctx.beginPath(); ctx.arc(node.x, node.y, r + 3.5, 0, 2*Math.PI); ctx.stroke();
    }
    if (showLabels && node.isPlan && scale > 0.55) {
      ctx.globalAlpha = dim ? 0.3 : 0.92; ctx.fillStyle = "#2A2F36";
      ctx.font = (10/scale) + "px -apple-system,Segoe UI,sans-serif"; ctx.textAlign = "center";
      const t = node.title.length > 30 ? node.title.slice(0,29) + "\\u2026" : node.title;
      ctx.fillText(t, node.x, node.y - r - 3/scale);
    }
    ctx.globalAlpha = 1;
  })
  .onNodeHover(n => { hoverId = n ? n.id : null; el.style.cursor = n ? "pointer" : "default"; })
  .onNodeClick(n => pick(n.id, false))
  .onEngineStop(() => { if (!pinned) { pinned = true; DATA.nodes.forEach(n => { n.fx = n.x; n.fy = n.y; }); } })
  .d3VelocityDecay(0.4);

// --- tighter clustering: cap repulsion range, short links, a gentle pull to centre ---
G.d3Force("charge").strength(-52).distanceMax(105);
G.d3Force("link").distance(26).strength(0.7);
const GRAV = 0.05;
G.d3Force("gravity", (alpha) => { for (const n of DATA.nodes) { n.vx += -n.x * GRAV * alpha; n.vy += -n.y * GRAV * alpha; } });

function size() { G.width(el.clientWidth).height(el.clientHeight); }
size(); window.addEventListener("resize", size);
setTimeout(() => G.zoomToFit(400, 40), 600);
function repaint(){ try { G.d3ReheatSimulation(); } catch(e){} }   // forces a redraw even when pinned

function esc(s){ const d=document.createElement("div"); d.textContent = s==null?"":s; return d.innerHTML; }

// --- searchable node list (reliable path to every node, no clicking required) ---
const ordered = DATA.nodes.slice().sort((a,b) => (b.isPlan-a.isPlan) || a.title.localeCompare(b.title));
const listEl = document.getElementById("list"), cntEl = document.getElementById("cnt"), qEl = document.getElementById("q");
function renderList(){
  const q = qEl.value.trim().toLowerCase();
  const rows = ordered.filter(n => !q || n.title.toLowerCase().includes(q));
  cntEl.textContent = rows.length + " of " + ordered.length + " events";
  listEl.innerHTML = rows.map(n =>
    `<div class="r2 ${n.id===selectedId?'sel':''}" data-id="${esc(n.id)}" title="${esc(n.title)}">`
    + `<span class="sh" style="color:${STATUS[n.status]||'#5A6472'}">${n.isPlan?'\\u25C6':'\\u25CF'}</span>`
    + `<span class="tt">${esc(n.title)}</span></div>`).join("");
  listEl.querySelectorAll(".r2").forEach(r => r.onclick = () => pick(r.dataset.id, true));
}
qEl.addEventListener("input", renderList);
renderList();

function pick(id, center){
  const node = byId[id]; if (!node) return;
  selectedId = id;
  if (center && node.x != null) { G.centerAt(node.x, node.y, 600); G.zoom(2.8, 600); }
  showDetail(node); renderList(); repaint();
}
function showDetail(node){
  const rel = DATA.insights.filter(i => i.plan===node.id || i.evidence.includes(node.id));
  let h = `<h2>${esc(node.title)}</h2><div class="meta">${esc(node.status)} \\u00b7 ${esc(node.kind)}${node.when?" \\u00b7 "+esc(node.when):""}</div>`;
  if (!rel.length) h += `<div class="empty">No verdict involves this event.</div>`;
  for (const i of rel) h += `<div class="verdict ${i.verdict}"><span class="vlabel">${esc(i.verdict)}</span> \\u00b7 ${esc(i.confidence)} confidence${i.review&&i.review!=="pending"?" \\u00b7 "+esc(i.review):""}<br>${esc(i.summary)}</div>`;
  h += `<div class="meta" style="margin-top:12px">Cited quotes (masked) are in the review panel below the graph.</div>`;
  document.getElementById("detail").innerHTML = h;
}
</script></body></html>"""
