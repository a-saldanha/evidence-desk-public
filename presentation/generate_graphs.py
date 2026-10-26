"""
Generate before/after comparison graph images for the presentation.

    python presentation/generate_graphs.py <path/to/graph.db>

The graph.db is found at:
    <EVIDENCE_DATA_DIR>/users/<user>/cases/<case>/graph.db

Outputs (in presentation/):
    people_before.png       people_after.png
    insights_before.png     insights_after.png
    comparison_people.png   comparison_insights.png
"""
from __future__ import annotations
import json, math, sqlite3, sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch
import numpy as np

DB  = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("graph.db")
OUT = Path(__file__).parent

# ── palette ──────────────────────────────────────────────────────────────────
C = dict(
    self         = "#2563EB",
    person       = "#7C3AED",
    edge_faint   = "#D1D5DB",
    edge_strong  = "#6B7280",
    confirmed    = "#16A34A",
    contradicted = "#DC2626",
    modified     = "#D97706",
    pending      = "#CBD5E1",
    plan_node    = "#1E40AF",
    evid_node    = "#64748B",
    bg           = "#FAFAFA",
    ring         = "#16A34A",
)

# ── Fruchterman–Reingold — tuned for spread ───────────────────────────────────
def fr_layout(nodes: list, edges: list[tuple],
              iterations: int = 350, k_scale: float = 2.2) -> dict[str, tuple]:
    n = len(nodes)
    if n == 0: return {}
    if n == 1: return {nodes[0]: (0.0, 0.0)}
    # circle init — wider radius improves initial spread
    pos = {nid: [math.cos(2*math.pi*i/n) * 1.2,
                 math.sin(2*math.pi*i/n) * 1.2]
           for i, nid in enumerate(nodes)}
    k = k_scale / math.sqrt(n)   # larger k → more repulsion → nodes spread more
    temp = 0.20
    for _ in range(iterations):
        disp = {nid: [0.0, 0.0] for nid in nodes}
        for i in range(n):
            a = nodes[i]; ax_, ay_ = pos[a]
            for j in range(i+1, n):
                b = nodes[j]
                dx = ax_ - pos[b][0]; dy = ay_ - pos[b][1]
                d  = math.hypot(dx, dy) or 1e-4
                f  = k*k / d
                disp[a][0] += dx/d*f; disp[a][1] += dy/d*f
                disp[b][0] -= dx/d*f; disp[b][1] -= dy/d*f
        for a, b in edges:
            if a not in pos or b not in pos: continue
            dx = pos[a][0]-pos[b][0]; dy = pos[a][1]-pos[b][1]
            d  = math.hypot(dx, dy) or 1e-4
            f  = d*d / k
            disp[a][0] -= dx/d*f; disp[a][1] -= dy/d*f
            disp[b][0] += dx/d*f; disp[b][1] += dy/d*f
        for nid in nodes:
            dx, dy = disp[nid]
            d = math.hypot(dx, dy) or 1e-4
            pos[nid][0] += dx/d * min(d, temp)
            pos[nid][1] += dy/d * min(d, temp)
        temp = max(temp * 0.982, 0.002)
    # normalise to [-1, 1]
    xs = [p[0] for p in pos.values()]; ys = [p[1] for p in pos.values()]
    sx = (max(xs)-min(xs)) or 1;       sy = (max(ys)-min(ys)) or 1
    return {nid: ((p[0]-min(xs))/sx*2 - 1,
                  (p[1]-min(ys))/sy*2 - 1)
            for nid, p in pos.items()}


# ── load ─────────────────────────────────────────────────────────────────────
def load():
    con = sqlite3.connect(str(DB)); con.row_factory = sqlite3.Row
    entities  = {r["entity_id"]: dict(r) for r in con.execute("SELECT * FROM entities")}
    events    = {r["event_id"]:  dict(r) for r in con.execute("SELECT * FROM events")}
    proposals = [dict(r) for r in con.execute("SELECT * FROM merge_proposals")]
    insights  = [dict(r) for r in con.execute("SELECT * FROM insights")]
    ep        = [(r["event_id"], r["entity_id"])
                 for r in con.execute("SELECT * FROM event_participants")]
    con.close()
    ev_ents: dict[str, set] = defaultdict(set)
    for eid, entid in ep: ev_ents[eid].add(entid)
    cooc: dict[tuple, int] = defaultdict(int)
    for ev_id, ent_ids in ev_ents.items():
        ps = sorted(e for e in ent_ids if e in entities and entities[e]["type"] == "person")
        for a, b in combinations(ps, 2):
            cooc[(a, b)] += 1
    return entities, events, proposals, insights, cooc


# ── merge simulation (union-find on entity names) ─────────────────────────────
ACCEPT_PAIRS = [
    ("dhanunjay", "dhanunjaya"),
    ("dhanunjaya", "dhanunjaya devalraju"),
    ("dhanunjay",  "dhanunjaya devalraju"),
    ("dhanunjaya", "iit mandi phd? icip dhanunjaya"),
    ("dhanunjay",  "iit mandi phd? icip dhanunjaya"),
    ("atanas gotchev", "gotchev atanas"),
    ("reshu", "reshu ma'am"),
    ("munish", "munish daroch"),
    ("nilakanmani", "nilakanmani manimaran"),
    ("iit madras phd rag saranya", "saranya"),
    ("iit kharagpur subhash goyal", "subhash goyal"),
]

def build_id_map(entities):
    name2id = {e["name"].lower(): eid for eid, e in entities.items()}
    parent  = {eid: eid for eid in entities}
    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb
    for na, nb in ACCEPT_PAIRS:
        ia, ib = name2id.get(na), name2id.get(nb)
        if ia and ib: union(ia, ib)
    return {eid: find(eid) for eid in entities}


# ════════════════════════════════════════════════════════════════════════════
# PEOPLE GRAPH
# ════════════════════════════════════════════════════════════════════════════
def _short(name: str, n=20) -> str:
    """Truncate & title-case for labels."""
    parts = name.split()
    # skip chat-style prefixes like "iit mandi phd?"
    clean = [p for p in parts if not p.endswith("?") and len(p) > 1]
    label = " ".join(clean[:2]).title()
    return label[:n] + ("…" if len(label) > n else "")


def draw_people_ax(ax, entities, cooc, id_map=None, title=""):
    persons = {eid: e for eid, e in entities.items() if e["type"] == "person"}

    if id_map:
        # collapse to canonical, keep longest/best name per group
        canon: dict[str, dict] = {}
        for eid, e in persons.items():
            cid = id_map[eid]
            if cid not in canon or len(e["name"]) > len(canon[cid]["name"]):
                canon[cid] = e
        persons = canon
        new_cooc: dict[tuple, int] = defaultdict(int)
        for (a, b), w in cooc.items():
            ca, cb = id_map[a], id_map[b]
            if ca != cb:
                key = tuple(sorted([ca, cb]))
                new_cooc[key] += w
        cooc = new_cooc

    # drop isolates
    connected = {n for pair in cooc for n in pair} & set(persons)
    persons   = {eid: e for eid, e in persons.items() if eid in connected}
    edges     = [(a, b) for (a, b) in cooc if a in persons and b in persons]

    pos = fr_layout(list(persons), edges, iterations=400, k_scale=2.5)

    deg: dict[str, int] = defaultdict(int)
    for a, b in edges: deg[a] += 1; deg[b] += 1
    max_deg = max(deg.values(), default=1)
    max_w   = max(cooc.values(), default=1)

    # edges
    for a, b in edges:
        if a not in pos or b not in pos: continue
        w = cooc.get((a,b), cooc.get((b,a), 1))
        ax.plot([pos[a][0], pos[b][0]], [pos[a][1], pos[b][1]],
                color=C["edge_faint"], lw=0.5 + 2.5*(w/max_w),
                zorder=1, solid_capstyle="round", alpha=0.7)

    # nodes
    for nid, e in persons.items():
        if nid not in pos: continue
        x, y   = pos[nid]
        is_self = bool(e["is_self"])
        d       = deg[nid]
        size    = 120 + 280 * (d / max_deg)
        color   = C["self"] if is_self else C["person"]
        marker  = "*" if is_self else "o"
        ax.scatter(x, y, s=size, c=color, marker=marker,
                   edgecolors="white", linewidths=1.5, zorder=3)

    # labels — top 16 by degree + self
    label_ids = sorted(persons, key=lambda x: -deg[x])[:16]
    label_ids = list(dict.fromkeys(
        [n for n, e in persons.items() if e["is_self"]] + label_ids))
    for nid in label_ids:
        if nid not in pos: continue
        x, y = pos[nid]
        e = persons[nid]
        label = "You" if e["is_self"] else _short(e["name"])
        fs = 9 if e["is_self"] else 7.5
        fw = "bold" if e["is_self"] else "normal"
        ax.text(x, y + 0.08, label, fontsize=fs, ha="center", va="bottom",
                fontweight=fw, color="#111827", zorder=5)

    n_isolated_total = len([p for p in
                             {eid for eid, e in entities.items() if e["type"]=="person"}
                             - connected])
    subtitle = f"{len(persons)} people · {len(edges)} connections"
    if n_isolated_total:
        subtitle += f"  ({n_isolated_total} isolated not shown)"
    ax.set_title(f"{title}\n{subtitle}", fontsize=11, pad=10, fontweight="bold")
    pad = 0.18
    ax.set_xlim(-1-pad, 1+pad); ax.set_ylim(-1-pad, 1+pad)
    ax.axis("off")
    ax.set_facecolor(C["bg"])


def people_legend(ax, merged=False):
    patches = [
        mpatches.Patch(color=C["self"],   label="You (case owner)"),
        mpatches.Patch(color=C["person"], label="Other person"),
    ]
    if not merged:
        patches += [mpatches.Patch(facecolor="none", edgecolor="#9CA3AF",
                                   label="Duplicate names = separate nodes")]
    ax.legend(handles=patches, loc="lower left", fontsize=9,
              framealpha=0.85, frameon=True)


# ════════════════════════════════════════════════════════════════════════════
# INSIGHTS GRAPH  — bipartite layout
# Plans  (■) on the LEFT column   x = -0.7
# Evidence (●) on the RIGHT column  x = +0.7
# Edges coloured by verdict
# ════════════════════════════════════════════════════════════════════════════
def draw_insights_ax(ax, events, insights, reviewed=False, title=""):
    VCOL = {"confirmed":   C["confirmed"],
            "modified":    C["modified"],
            "contradicted": C["contradicted"]}

    # build node sets
    plan_ids:  list[str] = []
    evid_ids:  list[str] = []
    seen_p, seen_e = set(), set()
    links = []
    for ins in insights:
        pid  = ins["plan_event_id"]
        eids = json.loads(ins["evidence_event_ids"])
        if pid not in seen_p:
            seen_p.add(pid); plan_ids.append(pid)
        for eid in eids:
            if eid not in seen_e:
                seen_e.add(eid); evid_ids.append(eid)
            links.append((pid, eid, ins["verdict"], ins["review_status"]))

    # sort plans by number of links for a tidier picture
    plan_deg: dict[str, int] = defaultdict(int)
    for pid, eid, v, r in links: plan_deg[pid] += 1
    plan_ids = sorted(plan_ids, key=lambda x: -plan_deg[x])
    evid_deg: dict[str, int] = defaultdict(int)
    for pid, eid, v, r in links: evid_deg[eid] += 1
    evid_ids = sorted(evid_ids, key=lambda x: -evid_deg[x])

    np_  = len(plan_ids)
    ne_  = len(evid_ids)
    p_ys = np.linspace(0.92, -0.92, np_)   if np_ > 1 else [0.0]
    e_ys = np.linspace(0.92, -0.92, ne_)   if ne_ > 1 else [0.0]

    p_pos = {pid: (-0.70, float(p_ys[i])) for i, pid in enumerate(plan_ids)}
    e_pos = {eid: ( 0.70, float(e_ys[i])) for i, eid in enumerate(evid_ids)}
    pos   = {**p_pos, **e_pos}

    accepted_plans = {ins["plan_event_id"] for ins in insights
                      if reviewed and ins["review_status"] == "accepted"}

    # draw edges as straight lines (with slight x-jitter to reduce overlap)
    rng = np.random.default_rng(42)
    for pid, eid, verdict, review in links:
        if pid not in pos or eid not in pos: continue
        color   = VCOL.get(verdict, C["pending"]) if reviewed else C["pending"]
        rejected = reviewed and review == "rejected"
        lw      = 0.9 if reviewed else 0.6
        alpha   = 0.15 if rejected else (0.55 if reviewed else 0.35)
        style   = (0, (3, 4)) if rejected else "solid"
        jx = rng.uniform(-0.02, 0.02)
        x0, y0 = pos[pid][0]+jx, pos[pid][1]
        x1, y1 = pos[eid][0]+jx, pos[eid][1]
        ax.plot([x0, x1], [y0, y1], color=color, lw=lw,
                alpha=alpha, linestyle=style, zorder=1)

    # plan nodes
    for pid in plan_ids:
        x, y   = pos[pid]
        ring_c = C["ring"] if pid in accepted_plans else "white"
        ring_w = 2.5      if pid in accepted_plans else 0.8
        ax.scatter(x, y, s=90, c=C["plan_node"], marker="s", zorder=3,
                   edgecolors=ring_c, linewidths=ring_w)

    # evidence nodes
    for eid in evid_ids:
        x, y = pos[eid]
        ax.scatter(x, y, s=45, c=C["evid_node"], marker="o", zorder=3,
                   edgecolors="white", linewidths=0.6)

    # column headers
    ax.text(-0.70, 1.01, f"Plans  ({np_})",
            ha="center", va="bottom", fontsize=10, fontweight="bold", color=C["plan_node"])
    ax.text( 0.70, 1.01, f"Evidence  ({ne_})",
            ha="center", va="bottom", fontsize=10, fontweight="bold", color=C["evid_node"])

    # stats subtitle
    if reviewed:
        n_acc = sum(1 for i in insights if i["review_status"] == "accepted")
        n_rej = sum(1 for i in insights if i["review_status"] == "rejected")
        n_c   = sum(1 for i in insights if i["verdict"] == "contradicted")
        n_m   = sum(1 for i in insights if i["verdict"] == "modified")
        n_cf  = sum(1 for i in insights if i["verdict"] == "confirmed")
        sub = f"{len(insights)} insights · {n_acc} accepted · {n_rej} rejected\n{n_c} contradicted · {n_m} modified · {n_cf} confirmed"
    else:
        n_pend = sum(1 for i in insights if i["review_status"] == "pending")
        sub = f"{len(insights)} insights · {n_pend} pending review · all verdicts hidden"

    ax.set_title(f"{title}\n{sub}", fontsize=11, pad=10, fontweight="bold")
    ax.set_xlim(-1.10, 1.10); ax.set_ylim(-1.08, 1.12)
    ax.axis("off"); ax.set_facecolor(C["bg"])


def insights_legend(ax):
    patches = [
        mpatches.Patch(color=C["confirmed"],    label="Confirmed"),
        mpatches.Patch(color=C["contradicted"], label="Contradicted"),
        mpatches.Patch(color=C["modified"],     label="Modified"),
        mpatches.Patch(facecolor=C["plan_node"], label="Plan event (■)"),
        mpatches.Patch(facecolor=C["evid_node"], label="Evidence event (●)"),
        mpatches.Patch(facecolor="none", edgecolor=C["ring"], linewidth=2,
                       label="Accepted plan (green ring)"),
    ]
    ax.legend(handles=patches, loc="lower center", fontsize=8.5,
              framealpha=0.85, ncol=2)


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    print("Loading …")
    entities, events, proposals, insights, cooc = load()
    id_map = build_id_map(entities)

    # simulate all insights fully reviewed for the "after" state
    reviewed_insights = [dict(i, review_status="accepted"
                              if i["review_status"] == "pending" else i["review_status"])
                         for i in insights]

    SZ   = (13, 13)   # individual
    CSZ  = (22, 11)   # comparison side-by-side
    DPI  = 160

    # ── 1. People BEFORE ─────────────────────────────────────────────────────
    print("People before …")
    fig, ax = plt.subplots(figsize=SZ, facecolor=C["bg"])
    draw_people_ax(ax, entities, cooc, id_map=None,
                   title="People Co-occurrence — Before Resolution")
    people_legend(ax, merged=False)
    fig.tight_layout(pad=1.5)
    fig.savefig(OUT/"people_before.png", dpi=DPI, bbox_inches="tight", facecolor=C["bg"])
    plt.close(); print("  → people_before.png")

    # ── 2. People AFTER ──────────────────────────────────────────────────────
    print("People after …")
    fig, ax = plt.subplots(figsize=SZ, facecolor=C["bg"])
    draw_people_ax(ax, entities, cooc, id_map=id_map,
                   title="People Co-occurrence — After Resolution")
    people_legend(ax, merged=True)
    fig.tight_layout(pad=1.5)
    fig.savefig(OUT/"people_after.png", dpi=DPI, bbox_inches="tight", facecolor=C["bg"])
    plt.close(); print("  → people_after.png")

    # ── 3. Insights BEFORE ───────────────────────────────────────────────────
    print("Insights before …")
    fig, ax = plt.subplots(figsize=SZ, facecolor=C["bg"])
    draw_insights_ax(ax, events, insights, reviewed=False,
                     title="Plan vs Evidence — Before Review")
    fig.tight_layout(pad=1.5)
    fig.savefig(OUT/"insights_before.png", dpi=DPI, bbox_inches="tight", facecolor=C["bg"])
    plt.close(); print("  → insights_before.png")

    # ── 4. Insights AFTER ────────────────────────────────────────────────────
    print("Insights after …")
    fig, ax = plt.subplots(figsize=SZ, facecolor=C["bg"])
    draw_insights_ax(ax, events, reviewed_insights, reviewed=True,
                     title="Plan vs Evidence — After Review")
    insights_legend(ax)
    fig.tight_layout(pad=1.5)
    fig.savefig(OUT/"insights_after.png", dpi=DPI, bbox_inches="tight", facecolor=C["bg"])
    plt.close(); print("  → insights_after.png")

    # ── 5. Comparison: People ────────────────────────────────────────────────
    print("Comparison people …")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=CSZ, facecolor=C["bg"])
    draw_people_ax(ax1, entities, cooc, id_map=None,
                   title="BEFORE — Unresolved Duplicates")
    people_legend(ax1, merged=False)
    draw_people_ax(ax2, entities, cooc, id_map=id_map,
                   title="AFTER — Duplicates Merged")
    people_legend(ax2, merged=True)
    fig.suptitle("Entity Resolution: People Co-occurrence Graph",
                 fontsize=14, fontweight="bold", y=1.01)
    fig.tight_layout(pad=1.5)
    fig.savefig(OUT/"comparison_people.png", dpi=DPI,
                bbox_inches="tight", facecolor=C["bg"])
    plt.close(); print("  → comparison_people.png")

    # ── 6. Comparison: Insights ──────────────────────────────────────────────
    print("Comparison insights …")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=CSZ, facecolor=C["bg"])
    draw_insights_ax(ax1, events, insights, reviewed=False,
                     title="BEFORE — Pending Review")
    draw_insights_ax(ax2, events, reviewed_insights, reviewed=True,
                     title="AFTER — Reviewed & Colour-coded")
    insights_legend(ax2)
    fig.suptitle("Insight Review: Plan vs Evidence Graph",
                 fontsize=14, fontweight="bold", y=1.01)
    fig.tight_layout(pad=1.5)
    fig.savefig(OUT/"comparison_insights.png", dpi=DPI,
                bbox_inches="tight", facecolor=C["bg"])
    plt.close(); print("  → comparison_insights.png")

    print(f"\nDone. Files in {OUT.resolve()}")


if __name__ == "__main__":
    main()
