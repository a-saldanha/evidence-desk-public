"""Shared UI for the multipage Evidence desk app.

Each page (streamlit_app.py + app/pages/*.py) calls `setup()` once to run auth, case selection and build
the Workspace, then one of the render functions below. Keeping it in one module means the sidebar, auth and
helpers stay identical across pages and there is a single place to change them.
"""
from __future__ import annotations
import html, json, sys
from dataclasses import dataclass
from pathlib import Path
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import casefiles as cf                                    # noqa: E402
from app.config import (USER_HEADER, LLM_BACKEND, BUDGET_USD_PER_USER_MONTH, DEMO_USER, MAX_UPLOAD_FILES,  # noqa: E402
                        PASSWORD, MASK_UI)
from app import showcase                                            # noqa: E402
from app.jobs import start_job, current_job, last_jobs, make_llm, PIPELINE  # noqa: E402
from reason.workspace import Workspace                             # noqa: E402

CSS = """<style>
mark.cite {background:#FFE58A; padding:0 2px; border-radius:2px}
.src {color:#5A6472; font-size:0.85rem}
.verdict-contradicted {border-left:4px solid #B3261E; padding-left:12px}
.verdict-modified {border-left:4px solid #B26A00; padding-left:12px}
.verdict-confirmed {border-left:4px solid #2E6B3F; padding-left:12px}
.snippet {background:#F4F6F9; padding:10px 12px; border-radius:6px; font-size:0.92rem; white-space:pre-wrap}
</style>"""


@dataclass
class Ctx:
    USER: str
    CASE: str
    cfg: dict
    ROOT_DIR: Path
    ws: Workspace
    READ_ONLY: bool


def setup() -> Ctx:
    """Page-config, auth, sidebar case selection and Workspace. Call once at the top of every page.
    Stops the script (login / new-case form / no case) when there is nothing to render."""
    st.set_page_config(page_title="Evidence desk", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    _proxy_user = st.context.headers.get(USER_HEADER)
    if PASSWORD and not _proxy_user and not st.session_state.get("authed"):
        import hmac
        st.title("Evidence desk")
        st.caption("Demo access")
        with st.form("login"):
            pw = st.text_input("Password", type="password")
            if st.form_submit_button("Enter"):
                if hmac.compare_digest(pw.encode(), PASSWORD.encode()):
                    st.session_state["authed"] = True
                    st.rerun()
                st.error("Wrong password.")
        st.stop()
    USER = cf.safe_user(_proxy_user or ("demo" if PASSWORD else "local"))
    showcase.ensure_seeded(USER)
    READ_ONLY = bool(DEMO_USER) and USER == cf.safe_user(DEMO_USER)
    if LLM_BACKEND == "scripted":
        st.warning("Smoke-test mode: the LLM is simulated (EVIDENCE_LLM=scripted). Results are not real model output.")

    with st.sidebar:
        st.markdown(f"**Signed in as** `{USER}`")
        cases = cf.list_cases(USER)
        want = st.session_state.get("case") or next((c for c in showcase.available() if c in cases), None)
        choice = st.selectbox("Case", cases + ["＋ New case"], index=cases.index(want) if want in cases else 0)
        st.session_state["case"] = choice if choice in cases else want
        if choice == "＋ New case":
            with st.form("new_case"):
                name = st.text_input("Case name", placeholder="icip-trip")
                self_name = st.text_input("Whose documents are these?", placeholder="Full name")
                aliases = st.text_input("Their names in chats (comma separated)", placeholder="e.g. Jane")
                emails = st.text_area("Their email addresses (one per line)")
                tz = st.number_input("Chat export clock (UTC offset, the phone's timezone at export)", value=5.5, step=0.5)
                if st.form_submit_button("Create", disabled=READ_ONLY):
                    if not name.strip() or not self_name.strip():
                        st.error("Case name and owner name are required.")
                    else:
                        cf.save_case(USER, name, {"self_name": self_name, "self_aliases": aliases.split(","),
                                                  "self_emails": emails.splitlines(), "chat_tz": tz})
                        st.session_state["case"] = cf.safe_name(name)
                        st.rerun()
            st.stop()
        if not choice or choice == "＋ New case":
            st.info("Create a case to begin."); st.stop()
        CASE = choice
        cfg = cf.load_case(USER, CASE)
        ROOT_DIR = cf.case_path(USER, CASE)
        left = cf.budget_left(USER)
        st.caption(f"LLM budget left this month: ${left:.2f} of ${BUDGET_USD_PER_USER_MONTH:.0f}")

    spend_meter()                                                    # live spend tally in the sidebar
    ws = Workspace(ROOT_DIR, cfg["self_name"], cfg["self_aliases"], cfg["self_emails"], cfg["chat_tz"])
    return Ctx(USER=USER, CASE=CASE, cfg=cfg, ROOT_DIR=ROOT_DIR, ws=ws, READ_ONLY=READ_ONLY)


# --------------------------------------------------------------------------- helpers
def has(ctx: Ctx, fname: str) -> bool:
    return (ctx.ROOT_DIR / fname).exists()


def changes(ctx: Ctx) -> dict:
    p = ctx.ROOT_DIR / "last_changes.json"
    return json.loads(p.read_text()) if p.exists() else {}


def masked_text(ctx: Ctx, u: dict) -> str:
    """Same length as the original so citation offsets still line up."""
    t = u["text"]
    if MASK_UI:
        for _kind, a, b in u["meta"].get("sensitive_spans") or []:
            t = t[:a] + "•" * (b - a) + t[b:]
    return t


def snippet(ctx: Ctx, unit_id: str, start=None, end=None, quote: str | None = None, width: int = 260) -> str:
    u = ctx.ws._unit_index().get(unit_id)
    if not u:
        return ""
    t = masked_text(ctx, u)
    # A parsed spreadsheet/itinerary screenshot is stored as one big HTML <table>; the surrounding cells are
    # not readable prose, so show just the cited cell (its address is shown separately via the citation note).
    if "<table" in t.lower():
        return (f"<div class='snippet'><mark class='cite'>{html.escape(quote)}</mark></div>" if quote
                else "<div class='snippet'>(grid cell)</div>")
    if start is None and quote:
        start = t.find(quote)
        end = start + len(quote) if start >= 0 else None
    if start is None or start < 0:
        return f"<div class='snippet'>{html.escape(t[:width * 2])}</div>"
    a, b = max(0, start - width), min(len(t), end + width)
    body = html.escape(t[a:start]) + f"<mark class='cite'>{html.escape(t[start:end])}</mark>" + html.escape(t[end:b])
    return f"<div class='snippet'>{'…' if a else ''}{body}{'…' if b < len(t) else ''}</div>"


def cite_block(ctx: Ctx, unit_id, quote=None, start=None, end=None, note=None):
    st.markdown(f"<div class='src'>{html.escape(ctx.ws.unit_label(unit_id))}{' · ' + html.escape(note) if note else ''}</div>",
                unsafe_allow_html=True)
    st.markdown(snippet(ctx, unit_id, start, end, quote), unsafe_allow_html=True)


@st.fragment(run_every=2)
def job_panel(ctx: Ctx):
    job = current_job(ctx.USER, ctx.CASE)
    if job:
        st.progress(min(job["progress"], 1.0), f"{job['step'] or 'starting'} · {job['message']}")
        st.caption("Processing runs on the server; you can reload or close this page.")
        st.session_state["was_running"] = True
        return
    if st.session_state.pop("was_running", False):
        st.rerun()                                     # refresh the whole page once results land
    hist = last_jobs(ctx.USER, ctx.CASE, 1)
    if hist:
        j = hist[0]
        icon = {"done": "✅", "failed": "⚠️"}.get(j["state"], "•")
        st.caption(f"{icon} Last run {j.get('finished', j['created'])[:16].replace('T', ' ')} · {j['state']}"
                   + (f" · ${j['cost_usd']:.2f}" if j.get("cost_usd") else ""))
        if j["state"] == "failed":
            st.error(j.get("error", "failed"))
        with st.expander("Run log"):
            st.code("\n".join(j["log"][-40:]) or "(empty)")


@st.fragment(run_every=2)
def spend_meter():
    """Live running total of real model spend — every call, every operation, every process — in the sidebar.
    Reads the shared spend ledger that extraction, reconciliation and Q&A all write to, so it ticks during jobs."""
    from reason.llm import SpendLedger
    L = SpendLedger()
    total, cap = L.total(), L.cap
    try:
        calls = json.loads(L.path.read_text()).get("calls", 0)
    except Exception:
        calls = 0
    st.session_state.setdefault("spend_start", total)
    session = max(0.0, total - st.session_state["spend_start"])
    with st.sidebar:
        st.divider()
        st.metric("💸 LLM spend (live)", f"${total:.2f}",
                  delta=(f"+${session:.2f} this session" if session > 0 else None), delta_color="inverse",
                  help="Running total of every model call — extraction, conflict reconciliation and Q&A — across "
                       "all runs and processes; refreshes every 2s. Document parsing (LlamaParse) is a separate "
                       "free tier and is not counted here.")
        if cap:
            st.progress(min(total / cap, 1.0), text=f"${total:.2f} of ${cap:.0f} cap · {calls} calls")


# --------------------------------------------------------------------------- pages
def files(ctx: Ctx):
    st.title(ctx.CASE)
    st.caption(f"Documents of **{ctx.cfg['self_name']}**. Layer 1 parses and checks files without an LLM; "
               "layers 2–4 extract events, resolve people and check plans against evidence. "
               "Re-running only pays for content that changed.")
    if not ctx.READ_ONLY:
        files_ = st.file_uploader("Emails (.eml, .mbox), WhatsApp exports (.txt or .zip), PDFs, screenshots, .xlsx",
                                  accept_multiple_files=True, key=f"up_{st.session_state.get('upk', 0)}")
        if files_:
            if len(files_) + len(cf.list_uploads(ctx.ROOT_DIR)) > MAX_UPLOAD_FILES:
                st.error(f"A case can hold at most {MAX_UPLOAD_FILES} files in this demo.")
            else:
                saved = []
                for f in files_:
                    try:
                        saved += cf.save_upload(ctx.ROOT_DIR, f.name, f.getvalue())
                    except ValueError as e:
                        st.error(str(e))
                st.session_state["upk"] = st.session_state.get("upk", 0) + 1
                st.toast(f"Saved {len(saved)} file(s)")
                st.rerun()
    running = current_job(ctx.USER, ctx.CASE) is not None
    c1, c2 = st.columns([1, 1])
    if c1.button("Process everything", type="primary", disabled=running or ctx.READ_ONLY or not cf.list_uploads(ctx.ROOT_DIR),
                 width="stretch", help="Ingest → extract → people → reconcile"):
        start_job(ctx.USER, ctx.CASE, PIPELINE); st.rerun()
    if c2.button("Rebuild after reviews", disabled=running or ctx.READ_ONLY or not has(ctx, "events.json"), width="stretch",
                 help="Re-applies your people decisions and re-checks plans (cached, near-free)"):
        start_job(ctx.USER, ctx.CASE, ["graph", "reconcile"]); st.rerun()
    job_panel(ctx)
    ch = changes(ctx)
    if ch and (ch.get("events") or ch.get("insights")) and not current_job(ctx.USER, ctx.CASE):
        st.success(f"The last run added **{len(ch.get('events', []))} events** and **{len(ch.get('insights', []))} insights**. "
                   "They are marked 🆕 on the Timeline and Insights pages.")
    ups = cf.list_uploads(ctx.ROOT_DIR)
    if ups:
        st.subheader(f"Uploaded files ({len(ups)})")
        st.dataframe(ups, width="stretch", hide_index=True, height=min(400, 38 + 35 * len(ups)))
    if ctx.ws.sources():
        st.subheader("What the system found")
        st.dataframe([{"file": s["filename"], "detected as": s["detected_type"],
                       "notes": ("identical copies: " + str(len(s["aliases"])) if s["aliases"] else "") +
                                (" · extracted from an email attachment" if s.get("parent_unit_id") else "") +
                                (" · shared in chat" if s["meta"].get("shared_in_chat") else "")}
                      for s in ctx.ws.sources().values()], width="stretch", hide_index=True)


def quality(ctx: Ctx):
    st.title("Data quality")
    issues = ctx.ws.issues()
    if not issues:
        st.info("Process the case first."); st.stop()
    sev = {"error": 0, "warning": 0, "info": 0}
    for i in issues:
        sev[i["severity"]] += 1
    a, b, c = st.columns(3)
    a.metric("Errors", sev["error"]); b.metric("Warnings", sev["warning"]); c.metric("Info", sev["info"])
    order = {"error": 0, "warning": 1, "info": 2}
    for check in sorted({i["check"] for i in issues}, key=lambda k: min(order[i["severity"]] for i in issues if i["check"] == k)):
        group = [i for i in issues if i["check"] == check]
        with st.expander(f"{check.replace('_', ' ')} · {group[0]['severity']} · {len(group)}"):
            if group[0].get("suggested_action"):
                st.caption(group[0]["suggested_action"])
            for i in group[:30]:
                st.write("• " + i["message"])


def people(ctx: Ctx):
    st.title("People and organisations")
    if not has(ctx, "graph.db"):
        st.info("Process the case first."); st.stop()
    store = ctx.ws.store()
    props = store.q("SELECT * FROM merge_proposals ORDER BY status='pending' DESC, score DESC")
    pending = sum(p["status"] == "pending" for p in props)
    st.subheader(f"Possible duplicates ({pending} to review)")
    st.caption("Only hard evidence (same email or phone) merges automatically. Decisions apply on "
               "**Rebuild after reviews** and are kept for future runs.")
    for p in props:
        key = f"{p['a']}|{p['b']}"
        cols = st.columns([5, 1, 1])
        cols[0].markdown(f"**{html.escape(p['a'])}** ≟ **{html.escape(p['b'])}**  \n"
                         f"<span class='src'>score {p['score']} · {html.escape(p['reason'])} · {p['status']}</span>",
                         unsafe_allow_html=True)
        if cols[1].button("Same", key="y" + key, disabled=ctx.READ_ONLY):
            ctx.ws.decide_merge(key, True)
            store.db.execute("UPDATE merge_proposals SET status='accepted' WHERE a=? AND b=?", (p["a"], p["b"])); store.commit()
            st.rerun()
        if cols[2].button("Different", key="n" + key, disabled=ctx.READ_ONLY):
            ctx.ws.decide_merge(key, False)
            store.db.execute("UPDATE merge_proposals SET status='rejected' WHERE a=? AND b=?", (p["a"], p["b"])); store.commit()
            st.rerun()
    st.subheader("Entities")
    st.dataframe([{"name": e["name"], "type": e["type"], "self": bool(e["is_self"]),
                   "also seen as": ", ".join(json.loads(e["aliases"])[:6])} for e in store.q("SELECT * FROM entities")],
                 width="stretch", hide_index=True)


def timeline(ctx: Ctx):
    import plotly.express as px
    from datetime import datetime, timedelta
    st.title("Timeline: plans and what happened")
    if not has(ctx, "graph.db"):
        st.info("Process the case first."); st.stop()
    events = [e for e in ctx.ws.store().events() if e["start_local"]]
    lane = {"planned": "Planned", "scheduled": "Booked / scheduled", "happened": "Happened",
            "reported_state": "Reported state", "cancelled": "Cancelled", "unknown": "Unclear"}
    rows = []
    for e in events:
        s = datetime.fromisoformat(e["start_local"])
        en = datetime.fromisoformat(e["end_local"]) if e["end_local"] else s + timedelta(minutes=45)
        rows.append({"lane": lane.get(e["status"], e["status"]), "start": s, "end": en, "title": e["title"],
                     "status": e["status"], "source": ctx.ws.unit_label(e["evidence"][0]["unit_id"]) if e["evidence"] else "-",
                     "time": e["time_confidence"], "id": e["event_id"]})
    if not rows:
        st.info("No dated events yet."); st.stop()
    new_ev = set(changes(ctx).get("events", []))
    if new_ev and st.toggle(f"Only what the last run added ({len(new_ev)})"):
        rows = [r for r in rows if r["id"] in new_ev]
        if not rows:
            st.info("The last run added no dated events."); st.stop()
    for r in rows:
        r["title"] = ("🆕 " if r["id"] in new_ev else "") + r["title"]
    lo, hi = min(r["start"] for r in rows).date(), max(r["end"] for r in rows).date()
    rng = st.slider("Dates", min_value=lo, max_value=hi, value=(lo, hi)) if lo < hi else (lo, hi)
    rows = [r for r in rows if rng[0] <= r["start"].date() <= rng[1]]
    fig = px.timeline(rows, x_start="start", x_end="end", y="lane", color="status", hover_name="title",
                      hover_data={"source": True, "time": True, "lane": False, "status": False, "id": False},
                      color_discrete_map={"planned": "#9AA5B1", "scheduled": "#2F5D8A", "happened": "#2E6B3F",
                                          "reported_state": "#B26A00", "cancelled": "#B3261E", "unknown": "#C9CED6"})
    fig.update_yaxes(categoryorder="array", categoryarray=list(lane.values())[::-1], title=None)
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10), legend_title_text="")
    st.plotly_chart(fig, width="stretch")
    st.caption("Times are local wall-clock; chat times are on the export clock.")
    pick = st.selectbox("Open an event", [f"{r['start']:%d %b %H:%M} · {r['title']}" for r in rows])
    if pick:
        r = rows[[f"{x['start']:%d %b %H:%M} · {x['title']}" for x in rows].index(pick)]
        ev = next(e for e in events if e["event_id"] == r["id"])
        st.caption(f"{ev['status']} · {ev['kind']} · time: {ev['time_confidence']}"
                   + (f" ('{ev['time_expression']}')" if ev.get("time_expression") else ""))
        for x in ev["evidence"]:
            cite_block(ctx, x["unit_id"], x["quote"], x["char_start"], x["char_end"], x.get("note"))


def insights(ctx: Ctx):
    st.title("Insights to review")
    if not has(ctx, "graph.db"):
        st.info("Process the case first."); st.stop()
    store = ctx.ws.store()
    evs = {e["event_id"]: e for e in store.events()}
    show = st.segmented_control("Show", ["pending", "accepted", "rejected", "all"], default="pending")
    rows = store.q("SELECT * FROM insights ORDER BY CASE verdict WHEN 'contradicted' THEN 0 WHEN 'modified' THEN 1 ELSE 2 END")
    rows = [r for r in rows if show in (None, "all") or r["review_status"] == show]
    if not rows:
        st.info("Nothing here.")
    new_ins = set(changes(ctx).get("insights", []))
    rows.sort(key=lambda r: r["insight_id"] not in new_ins)
    for r in rows:
        plan = evs.get(r["plan_event_id"], {})
        badge = "🆕 " if r["insight_id"] in new_ins else ""
        st.markdown(f"<div class='verdict-{r['verdict']}'><h4>{badge}{r['verdict'].capitalize()} · {r['confidence']} confidence</h4>"
                    f"<p>{html.escape(r['summary'])}</p></div>", unsafe_allow_html=True)
        st.caption(f"Plan: {plan.get('title')} · {(plan.get('start_local') or '')[:16].replace('T', ' ')} · review: {r['review_status']}")
        cits = json.loads(r["citations"])
        cols = st.columns(2)
        with cols[0]:
            st.markdown("**The plan**")
            for c in [c for c in cits if c["role"] == "plan"]:
                cite_block(ctx, c["unit_id"], c["quote"], note=c.get("note"))
        with cols[1]:
            st.markdown("**The evidence**")
            for c in [c for c in cits if c["role"] == "evidence"]:
                cite_block(ctx, c["unit_id"], c["quote"], c.get("char_start"), c.get("char_end"))
        b1, b2, _ = st.columns([1, 1, 4])
        if b1.button("Accept", key="a" + r["insight_id"], disabled=ctx.READ_ONLY):
            store.review("insights", "insight_id", r["insight_id"], "accepted"); st.rerun()
        if b2.button("Reject", key="r" + r["insight_id"], disabled=ctx.READ_ONLY):
            store.review("insights", "insight_id", r["insight_id"], "rejected"); st.rerun()
        st.divider()


# --------------------------------------------------------------------------- graph
_VERDICT_COLOR = {"confirmed": "#2E6B3F", "modified": "#B26A00", "contradicted": "#B3261E"}
_STATUS_COLOR = {"planned": "#9AA5B1", "scheduled": "#2F5D8A", "happened": "#2E6B3F",
                 "reported_state": "#B26A00", "cancelled": "#B3261E", "unknown": "#C9CED6"}


def _short(s: str | None, n: int = 26) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n - 1] + "…"


def _fr_layout(node_ids, edges, iterations: int = 220) -> dict:
    """Deterministic Fruchterman-Reingold layout (circle init, no RNG) so the picture is reproducible
    across reruns — in keeping with the rest of the pipeline. Fine for the ~100-node graphs here."""
    import math
    nodes = list(node_ids)
    n = len(nodes)
    if n == 0:
        return {}
    if n == 1:
        return {nodes[0]: (0.0, 0.0)}
    pos = {nid: [math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n)] for i, nid in enumerate(nodes)}
    k = 1.0 / math.sqrt(n)                                 # ideal edge length
    temp = 0.12
    for _ in range(iterations):
        disp = {nid: [0.0, 0.0] for nid in nodes}
        for i in range(n):                                 # repulsion between every pair
            a = nodes[i]; ax, ay = pos[a]
            for j in range(i + 1, n):
                b = nodes[j]
                dx = ax - pos[b][0]; dy = ay - pos[b][1]
                d = math.hypot(dx, dy) or 1e-4
                f = (k * k / d) / d                        # k^2/d along the unit vector dx/d
                disp[a][0] += dx * f; disp[a][1] += dy * f
                disp[b][0] -= dx * f; disp[b][1] -= dy * f
        for a, b in edges:                                 # attraction along edges
            if a not in pos or b not in pos:
                continue
            dx = pos[a][0] - pos[b][0]; dy = pos[a][1] - pos[b][1]
            d = math.hypot(dx, dy) or 1e-4
            f = d / k                                       # d^2/k along the unit vector dx/d
            disp[a][0] -= dx * f; disp[a][1] -= dy * f
            disp[b][0] += dx * f; disp[b][1] += dy * f
        for nid in nodes:                                  # move, capped by cooling temperature
            dx, dy = disp[nid]
            d = math.hypot(dx, dy) or 1e-4
            pos[nid][0] += dx / d * min(d, temp)
            pos[nid][1] += dy / d * min(d, temp)
        temp = max(temp * 0.985, 0.004)
    xs = [p[0] for p in pos.values()]; ys = [p[1] for p in pos.values()]
    sx = (max(xs) - min(xs)) or 1.0; sy = (max(ys) - min(ys)) or 1.0
    return {nid: (2 * (p[0] - min(xs)) / sx - 1, 2 * (p[1] - min(ys)) / sy - 1) for nid, p in pos.items()}


@st.cache_data(show_spinner=False)
def _spring_layout(node_ids: tuple, edges: tuple, iterations: int = 220) -> dict:
    return _fr_layout(list(node_ids), list(edges), iterations)


def _components(node_ids, edges):
    """Connected components via union-find, largest first."""
    parent = {n: n for n in node_ids}

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for a, b in edges:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    comps = {}
    for n in node_ids:
        comps.setdefault(find(n), []).append(n)
    return sorted(comps.values(), key=lambda c: -len(c))


@st.cache_data(show_spinner=False)
def _packed_layout(node_ids: tuple, edges: tuple, hub_ids: tuple) -> dict:
    """Each insight is a small plan->evidence star; a single global force layout scatters them like
    confetti. Instead lay each connected component out locally (hub centred, evidence on a ring) and
    grid-pack the components so the result reads as tidy labelled clusters."""
    import math
    hubs_set = set(hub_ids)
    comps = _components(list(node_ids), list(edges))
    cells = []
    for c in comps:
        hubs = [n for n in c if n in hubs_set]
        others = [n for n in c if n not in hubs_set]
        local = {}
        if len(c) == 1:
            local[c[0]] = (0.0, 0.0)
        elif len(hubs) == 1 and others:                       # a clean star: plan in the middle
            local[hubs[0]] = (0.0, 0.0)
            for i, n in enumerate(others):
                ang = 2 * math.pi * i / len(others) - math.pi / 2
                r = 1.0 if len(others) > 1 else 0.95
                local[n] = (r * math.cos(ang), r * math.sin(ang))
        else:                                                 # shared evidence / multi-plan: local spring
            sub = [(a, b) for a, b in edges if a in c and b in c]
            local = _fr_layout(c, sub, iterations=140)
        cells.append((c, local))
    cols = max(1, int(round(math.sqrt(len(cells) * 1.7))))
    cell = 3.0
    pos = {}
    for idx, (c, local) in enumerate(cells):
        cx, cy = (idx % cols) * cell, -(idx // cols) * cell
        for n in c:
            lx, ly = local[n]
            pos[n] = (cx + lx, cy + ly)
    return pos


def _graph_fig(pos, node_rows, edge_groups, show_labels):
    """edge_groups: name -> (color, width, edges[, dash, opacity]). node_rows may carry line_color/line_width."""
    import plotly.graph_objects as go
    fig = go.Figure()
    for name, spec in edge_groups.items():
        color, width, es = spec[0], spec[1], spec[2]
        dash = spec[3] if len(spec) > 3 else "solid"
        opacity = spec[4] if len(spec) > 4 else 0.55
        xs, ys = [], []
        for a, b in es:
            if a in pos and b in pos:
                xs += [pos[a][0], pos[b][0], None]; ys += [pos[a][1], pos[b][1], None]
        if xs:
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=color, width=width, dash=dash),
                                     name=name, hoverinfo="skip", opacity=opacity))
    fig.add_trace(go.Scatter(
        x=[pos[r["id"]][0] for r in node_rows], y=[pos[r["id"]][1] for r in node_rows],
        mode="markers+text" if show_labels else "markers",
        text=[r["label"] for r in node_rows] if show_labels else None,
        textposition="top center", textfont=dict(size=9, color="#2A2F36"),
        marker=dict(size=[r["size"] for r in node_rows], color=[r["color"] for r in node_rows],
                    symbol=[r["symbol"] for r in node_rows],
                    line=dict(width=[r.get("line_width", 1) for r in node_rows],
                              color=[r.get("line_color", "white") for r in node_rows])),
        customdata=[[r["id"]] for r in node_rows], hovertext=[r["hover"] for r in node_rows],
        hoverinfo="text", showlegend=False))
    fig.update_layout(height=560, margin=dict(l=10, r=10, t=10, b=10), plot_bgcolor="white",
                      xaxis=dict(visible=False), yaxis=dict(visible=False), clickmode="event+select",
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, title_text=""))
    return fig


def _selected_node(sel):
    pts = (sel or {}).get("selection", {}).get("points", []) if sel else []
    for p in pts:
        cd = p.get("customdata")
        if cd:
            return cd[0] if isinstance(cd, (list, tuple)) else cd
    return None


def graph(ctx: Ctx):
    from collections import Counter
    st.title("Graph")
    if not has(ctx, "graph.db"):
        st.info("Process the case first."); st.stop()
    store = ctx.ws.store()
    events = {e["event_id"]: e for e in store.events()}
    mode = st.segmented_control("View", ["Plan vs evidence", "People who co-occur"], default="Plan vs evidence")
    show_labels = st.toggle("Show labels", value=True)

    if mode == "People who co-occur":
        import itertools
        people = {e["entity_id"]: e for e in store.q("SELECT * FROM entities WHERE type='person'")}
        w = Counter()
        for e in events.values():
            ids = sorted({x for x in e["entity_ids"] if x in people})
            for a, b in itertools.combinations(ids, 2):
                w[(a, b)] += 1
        if not w:
            st.info("No two people share an event yet."); st.stop()
        maxw = max(w.values())
        minw = st.slider("Minimum shared events", 1, maxw, min(2, maxw)) if maxw > 1 else 1
        edges = [(a, b) for (a, b), c in w.items() if c >= minw]
        if not edges:
            st.info("No co-occurrences at this threshold — lower the slider."); st.stop()
        deg = Counter()
        for a, b in edges:
            deg[a] += 1; deg[b] += 1
        node_ids = [p for p in people if deg[p]]
        pos = _spring_layout(tuple(node_ids), tuple(edges))
        rows = []
        for nid in node_ids:
            p = people[nid]; me = bool(p["is_self"])
            rows.append(dict(id=nid, label=_short(p["name"], 22), symbol="star" if me else "circle",
                             color="#2F5D8A" if me else "#6B4FA0", size=min(10 + 2 * deg[nid], 34),
                             hover=f"{html.escape(p['name'])}<br>{deg[nid]} connection(s)"))
        fig = _graph_fig(pos, rows, {"Shares an event": ("#B9C2CE", 1.2, edges)}, show_labels)
        sel = st.plotly_chart(fig, width="stretch", key="people_graph", on_select="rerun", selection_mode="points")
        st.caption(f"{len(node_ids)} people · a line means they appear in the same event (≥{minw}). "
                   "★ is the case owner. Click a person for details.")
        picked = _selected_node(sel)
        if picked and picked in people:
            p = people[picked]
            st.markdown(f"### {html.escape(p['name'])}")
            al = json.loads(p["aliases"]) if isinstance(p["aliases"], str) else (p.get("aliases") or [])
            if al:
                st.caption("also seen as: " + ", ".join(html.escape(a) for a in al[:8]))
            evs = [e for e in events.values() if picked in e["entity_ids"]]
            st.write(f"**Appears in {len(evs)} event(s):**")
            for e in sorted(evs, key=lambda e: e.get("start_local") or "")[:20]:
                when = (e.get("start_local") or "")[:16].replace("T", " ")
                st.write(f"• {html.escape(e['title'])} — {e['status']}" + (f" · {when}" if when else ""))
        return

    # --- Plan vs evidence (reconciliation), rendered as a live force-directed graph ---
    import streamlit.components.v1 as components
    from app import _graphhtml
    ins_all = store.q("SELECT * FROM insights")
    for i in ins_all:
        i["evidence_event_ids"] = json.loads(i["evidence_event_ids"])
    rc = Counter(i["review_status"] for i in ins_all)
    cols = st.columns(4)                                       # review progress — watch these move as you review
    cols[0].metric("Plans checked", len(ins_all))
    cols[1].metric("✓ Accepted", rc.get("accepted", 0))
    cols[2].metric("✗ Rejected", rc.get("rejected", 0))
    cols[3].metric("Pending", rc.get("pending", 0))
    f1, f2 = st.columns([3, 2])
    keep = f1.multiselect("Verdicts", ["confirmed", "modified", "contradicted"],
                          default=["confirmed", "modified", "contradicted"])
    show_rejected = f2.toggle("Show rejected (faint)", value=False,
                              help="Rejected verdicts are hidden by default; turn on to see them dimmed.")
    shown = [i for i in ins_all if i["verdict"] in keep and (show_rejected or i["review_status"] != "rejected")]
    accepted_plans = {i["plan_event_id"] for i in shown if i["review_status"] == "accepted"}
    node_ids, seen, plan_ids, links = [], set(), set(), []
    for i in shown:
        p = i["plan_event_id"]; plan_ids.add(p)
        rejected = i["review_status"] == "rejected"
        for ev in i["evidence_event_ids"]:
            if p in events and ev in events:
                links.append({"source": p, "target": ev, "verdict": i["verdict"], "rejected": rejected})
                for nid in (p, ev):
                    if nid not in seen:
                        seen.add(nid); node_ids.append(nid)
    if not node_ids:
        st.info("Nothing to show — every verdict here is rejected or filtered out."); st.stop()
    nodes = [{"id": nid, "title": events[nid]["title"], "status": events[nid]["status"],
              "kind": events[nid]["kind"], "isPlan": nid in plan_ids, "accepted": nid in accepted_plans,
              "when": (events[nid].get("start_local") or "")[:16].replace("T", " ")} for nid in node_ids]
    ins_data = [{"plan": i["plan_event_id"], "evidence": i["evidence_event_ids"], "verdict": i["verdict"],
                 "confidence": i["confidence"], "summary": i["summary"], "review": i["review_status"]} for i in shown]
    components.html(_graphhtml.recon_html(nodes, links, ins_data, show_labels, height=540),
                    height=660, scrolling=False)

    st.markdown("**Review a verdict** — click a node above to read it, then pick it here to see the cited quotes "
                "(masked) and Accept / Reject. Accepted plans get a green ring; rejected verdicts drop off the graph.")
    reviewable = [i for i in ins_all if i["verdict"] in keep]
    if reviewable:
        def _lab(i):
            icon = {"accepted": "✓", "rejected": "✗"}.get(i["review_status"], "•")
            return f"{icon} [{i['verdict']}] {i['summary'][:70]}"
        r = st.selectbox("Verdict", reviewable, format_func=_lab, key="recon_review_pick", label_visibility="collapsed")
        cur = r["review_status"]; plan = events.get(r["plan_event_id"], {})
        st.caption(f"Plan: {plan.get('title')} · {(plan.get('start_local') or '')[:16].replace('T', ' ')} · review: {cur}")
        cits = json.loads(r["citations"])
        cc = st.columns(2)
        with cc[0]:
            st.markdown("**The plan**")
            for c in [c for c in cits if c.get("role") == "plan"]:
                cite_block(ctx, c["unit_id"], c["quote"], note=c.get("note"))
        with cc[1]:
            st.markdown("**The evidence**")
            for c in [c for c in cits if c.get("role") == "evidence"]:
                cite_block(ctx, c["unit_id"], c["quote"], c.get("char_start"), c.get("char_end"))
        b = st.columns([1, 1, 1, 5])
        if b[0].button("✓ Accept", key="ga" + r["insight_id"], disabled=ctx.READ_ONLY or cur == "accepted"):
            store.review("insights", "insight_id", r["insight_id"], "accepted"); st.rerun()
        if b[1].button("✗ Reject", key="gr" + r["insight_id"], disabled=ctx.READ_ONLY or cur == "rejected"):
            store.review("insights", "insight_id", r["insight_id"], "rejected"); st.rerun()
        if b[2].button("Reset", key="gp" + r["insight_id"], disabled=ctx.READ_ONLY or cur == "pending"):
            store.review("insights", "insight_id", r["insight_id"], "pending"); st.rerun()


def ask(ctx: Ctx):
    st.title("Ask the evidence")
    if not has(ctx, "graph.db"):
        st.info("Process the case first."); st.stop()
    with st.form("ask"):
        q = st.text_input("Question", placeholder="Did I attend Dhanunjaya's presentation?")
        go = st.form_submit_button("Ask")
    if go and q.strip():
        if cf.budget_left(ctx.USER) <= 0 and LLM_BACKEND != "scripted":
            st.error("Monthly LLM budget used up."); st.stop()
        llm = make_llm(ctx.ws.llm_cache)
        with st.spinner("Searching events and documents…"):
            r = ctx.ws.qa(llm).ask(q.strip())
        if hasattr(llm, "cost_usd"):
            cf.record_spend(ctx.USER, llm.cost_usd())
        st.markdown(f"### {html.escape(r['answer'])}")
        st.caption(f"Confidence: {r['confidence']}" + (f" · {r['unverified_citations_removed']} unverifiable citation(s) removed"
                                                       if r["unverified_citations_removed"] else ""))
        for c in r["citations"]:
            cite_block(ctx, c["unit_id"], c["quote"], c["char_start"], c["char_end"])
        for oq in r["open_questions"]:
            st.write("• To check: " + oq)


def settings(ctx: Ctx):
    st.title("Settings & privacy")
    with st.form("settings"):
        self_name = st.text_input("Owner name", value=ctx.cfg["self_name"])
        aliases = st.text_input("Chat names (comma separated)", value=", ".join(ctx.cfg["self_aliases"]))
        emails = st.text_area("Email addresses", value="\n".join(ctx.cfg["self_emails"]))
        tz = st.number_input("Chat export clock (UTC offset)", value=float(ctx.cfg["chat_tz"]), step=0.5)
        if st.form_submit_button("Save", disabled=ctx.READ_ONLY):
            cf.save_case(ctx.USER, ctx.CASE, {"self_name": self_name, "self_aliases": aliases.split(","),
                                              "self_emails": emails.splitlines(), "chat_tz": tz})
            st.success("Saved. Run 'Process everything' to apply.")
    st.subheader("Your data")
    st.write("Stored on this server only: your files, their parsed text, cached model answers and the graph. "
             "Text sent to the model has passwords, passport numbers and similar values masked. "
             "Model and parsing providers process content to return results.")
    st.download_button("Download results (no raw documents)", cf.export_case(ctx.ROOT_DIR), file_name=f"{ctx.CASE}-results.zip")
    for u in cf.list_uploads(ctx.ROOT_DIR)[:200]:
        c1, c2 = st.columns([6, 1])
        c1.write(u["file"])
        if c2.button("Remove", key="rm" + u["file"], disabled=ctx.READ_ONLY):
            cf.remove_upload(ctx.ROOT_DIR, u["file"]); st.rerun()
    st.caption("Removing a file takes effect on the next 'Process everything'.")

    st.subheader("Snapshots")
    st.caption("Save the current reviewed state, or roll back to a saved one — instant and free (the model "
               "cache and parsed text are reused). Use it to rehearse the review live: snapshot the unresolved "
               "state, resolve conflicts and **Rebuild after reviews**, snapshot the result, then restore either.")
    snaps = cf.list_snapshots(ctx.ROOT_DIR)
    s1, s2 = st.columns([3, 1])
    newname = s1.text_input("New snapshot name", placeholder="unresolved-start", label_visibility="collapsed")
    if s2.button("Save snapshot", disabled=ctx.READ_ONLY or not newname.strip(), width="stretch"):
        cf.save_snapshot(ctx.ROOT_DIR, newname.strip())
        st.toast(f"Saved snapshot '{newname.strip()}'"); st.rerun()
    if snaps:
        r1, r2, r3 = st.columns([3, 1, 1])
        pick = r1.selectbox("Saved snapshots", snaps, label_visibility="collapsed")
        if r2.button("Restore", disabled=ctx.READ_ONLY, width="stretch", help="Overwrite the live case with this snapshot"):
            if current_job(ctx.USER, ctx.CASE):
                st.error("Wait for the running job to finish.")
            else:
                cf.restore_snapshot(ctx.ROOT_DIR, pick); st.success(f"Restored '{pick}'."); st.rerun()
        if r3.button("Delete", key="delsnap", disabled=ctx.READ_ONLY, width="stretch"):
            cf.delete_snapshot(ctx.ROOT_DIR, pick); st.rerun()
    else:
        st.caption("No snapshots yet.")

    if ctx.CASE in showcase.available():
        st.subheader("Showcase")
        st.write("This case was pre-processed and is built into the deployment. Resetting removes live uploads "
                 "and their results, restoring the original state (useful between rehearsals).")
        if st.button("Reset to the showcase state", disabled=ctx.READ_ONLY):
            if current_job(ctx.USER, ctx.CASE):
                st.error("Wait for the running job to finish.")
            else:
                showcase.reset(ctx.USER, ctx.CASE); st.success("Reset."); st.rerun()
    with st.expander("Delete this case"):
        st.write("Deletes every copy: uploads, parsed text, model cache, graph, reviews and logs. This cannot be undone.")
        confirm = st.text_input("Type the case name to confirm")
        if st.button("Delete permanently", type="primary", disabled=ctx.READ_ONLY or confirm != ctx.CASE):
            if current_job(ctx.USER, ctx.CASE):
                st.error("Wait for the running job to finish.")
            else:
                cf.delete_case(ctx.USER, ctx.CASE); st.rerun()
