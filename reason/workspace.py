"""One workspace = one case. Holds layer-1 output, the LLM cache, human decisions and the graph.

    ws = Workspace("case_dir", self_name="Jane Smith", self_aliases=["Jane"], self_emails=[...])
    ws.ingest([Path("uploads")])         # layer 1 (no LLM)
    ws.extract(llm)                      # layer 2
    ws.build_graph()                     # layer 3 (entity resolution + store)
    ws.reconcile(llm)                    # layer 4a
    ws.qa(llm).ask("Did I attend Dhanunjaya's presentation?")   # layer 4b
Every step can be re-run; LLM calls are cached, so a re-run after a human decision is instant.
"""
from __future__ import annotations
import json, shutil
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from ingest.pipeline import run as ingest_run
from .store import GraphStore, Event, Evidence
from .extract import run_extraction, configure_self
from .entities import collect_surfaces, resolve_entities, save_decision
from .reconcile import reconcile
from .query import QA
from .llm import LLMClient


class Workspace:
    def __init__(self, root, self_name: str, self_aliases=(), self_emails=(), chat_tz: float = 5.5):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ingest_dir = self.root / "ingest"
        self.self_cfg = {"name": self_name, "aliases": list(self_aliases), "addresses": list(self_emails)}
        self.chat_tz = chat_tz
        configure_self(self_name, self_aliases, self_emails)
        self.log: list[str] = []

    # ---------------------------------------------------------------- helpers
    @property
    def llm_cache(self) -> Path:
        return self.root / "llm_cache"

    @property
    def decisions_path(self) -> Path:
        return self.root / "decisions.json"

    def units(self) -> list[dict]:
        p = self.ingest_dir / "units.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []

    def sources(self) -> dict:
        p = self.ingest_dir / "sources.json"
        return {s["source_id"]: s for s in json.loads(p.read_text())} if p.exists() else {}

    def issues(self) -> list[dict]:
        p = self.ingest_dir / "qc_issues.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []

    def unit_label(self, uid: str) -> str:
        u, s = self._unit_index().get(uid), None
        if not u:
            return uid
        s = self.sources().get(u["locator"]["source_id"], {})
        loc = u["locator"]
        if loc.get("line_start"):
            where = f"line {loc['line_start']}"
        elif loc.get("page"):
            where = f"page {loc['page']}"
        else:
            where = (u["meta"].get("subject") or "email")[:60]
        when = (u["meta"].get("local_time") or u.get("timestamp_utc") or "")[:16].replace("T", " ")
        who = (u.get("author") or {}).get("display") or ""
        return " · ".join(x for x in [s.get("filename", "?"), where, when, who] if x)

    def _unit_index(self):
        if not hasattr(self, "_uidx"):
            self._uidx = {u["unit_id"]: u for u in self.units()}
        return self._uidx

    def store(self) -> GraphStore:
        return GraphStore(self.root / "graph.db")

    # ---------------------------------------------------------------- layer 1
    def ingest(self, inputs: list[Path], offline: Optional[bool] = None):
        import os
        offline = (not os.environ.get("LLAMA_CLOUD_API_KEY")) if offline is None else offline
        aliases = {a: (self.self_cfg["addresses"] or [self.self_cfg["name"]])[0] for a in self.self_cfg["aliases"]}
        shutil.rmtree(self.ingest_dir / "raw", ignore_errors=True)       # removed uploads leave no copies behind
        store, units, issues = ingest_run(inputs, self.ingest_dir, offline=offline, tz_hours=self.chat_tz, chat_aliases=aliases)
        cache = self.ingest_dir / "parse_cache"
        if cache.exists():                                                 # drop parses of files no longer present
            for f in cache.glob("*.json"):
                if f.name.split("_", 1)[0] not in store.sources:
                    f.unlink()
        if hasattr(self, "_uidx"):
            del self._uidx
        self.log.append(f"layer 1: {len(store.sources)} sources, {len(units)} units, {len(issues)} QC issues")
        return store, units, issues

    # ---------------------------------------------------------------- layer 2
    def extract(self, llm: LLMClient, progress=None) -> dict:
        res = run_extraction(llm, self.units(), self.sources(), progress=progress)
        (self.root / "events.json").write_text(json.dumps([asdict(e) for e in res.events], ensure_ascii=False, indent=1, default=str))
        (self.root / "mentions.json").write_text(json.dumps(res.mentions, ensure_ascii=False, indent=1))
        (self.root / "clock_shifts.json").write_text(json.dumps(res.clock_shifts, indent=1))
        self.log.append(f"layer 2: {res.stats}")
        return res.stats

    def _load_events(self) -> list[Event]:
        raw = json.loads((self.root / "events.json").read_text())
        return [Event(**{**e, "evidence": [Evidence(**x) for x in e["evidence"]]}) for e in raw]

    # ---------------------------------------------------------------- layer 3
    def build_graph(self) -> dict:
        db_path = self.root / "graph.db"
        old_reviews = {}
        if db_path.exists():                                  # keep human review decisions across rebuilds
            old = GraphStore(db_path)
            old_reviews = {r["insight_id"]: (r["review_status"], r["reviewer_note"]) for r in old.q("SELECT * FROM insights")}
            old.db.close()
            db_path.unlink()
        st = self.store()
        events = self._load_events()
        mentions = json.loads((self.root / "mentions.json").read_text())
        surfaces = collect_surfaces(self.units(), mentions, events)
        entities, s2e, proposals = resolve_entities(surfaces, self.self_cfg, self.decisions_path)
        for e in entities:
            st.add_entity(e["entity_id"], e["type"], e["name"], e["is_self"], e["aliases"], e["identifiers"])
        for p in proposals:
            a, b = p["key"].split("|", 1)
            st.db.execute("INSERT INTO merge_proposals VALUES (?,?,?,?,?)", (a, b, p["score"], p["reason"], p["status"]))
        linked = 0
        for ev in events:
            st.add_event(ev)
            ids = set()
            for p in ev.participants:
                key = "self" if p.strip().upper() == "SELF" or p.strip().lower() == "self" else p.strip().lower()
                if key in s2e:
                    ids.add(s2e[key])
            for eid in ids:
                st.link(ev.event_id, eid); linked += 1
        st.commit()
        self._old_reviews = old_reviews
        stats = {"entities": len(entities), "merge_proposals": len(proposals), "events": len(events), "participant_links": linked}
        self.log.append(f"layer 3: {stats}")
        return stats

    def decide_merge(self, pair_key: str, accepted: bool):
        save_decision(self.decisions_path, pair_key, "accepted" if accepted else "rejected")

    # ---------------------------------------------------------------- layer 4
    def reconcile(self, llm: LLMClient, progress=None) -> dict:
        st = self.store()
        events = st.events()
        uidx = self._unit_index()
        insights, stats = reconcile(llm, events, {k: u["text"] for k, u in uidx.items()},
                                    {k: u["locator"]["source_id"] for k, u in uidx.items()}, progress)
        st.db.execute("DELETE FROM insights")
        old = getattr(self, "_old_reviews", {})
        for ins in insights:
            if ins["insight_id"] in old:
                ins["review_status"], ins["reviewer_note"] = old[ins["insight_id"]]
            st.add_insight(ins)
        st.commit()
        (self.root / "insights.json").write_text(json.dumps(insights, ensure_ascii=False, indent=1))
        self.log.append(f"layer 4: {stats}")
        return stats

    def qa(self, llm: LLMClient) -> QA:
        return QA(llm, self.store().events(), self._unit_index(), self.unit_label)

    # ---------------------------------------------------------------- report
    def report(self) -> str:
        st = self.store()
        by_status = st.q("SELECT status, COUNT(*) n FROM events GROUP BY status ORDER BY n DESC")
        L = ["# Layers 2–4 report", "", *[f"- {l}" for l in self.log], "",
             "## Events by status", "", ", ".join(f"{r['status']}: {r['n']}" for r in by_status), "",
             "## Insights (plan vs evidence)", ""]
        events = {e["event_id"]: e for e in st.events()}
        for r in st.q("SELECT * FROM insights ORDER BY verdict"):
            plan = events.get(r["plan_event_id"], {})
            L.append(f"### {r['verdict'].upper()} ({r['confidence']}): {plan.get('title')} — {(plan.get('start_local') or '')[:16]}")
            L.append(r["summary"])
            for c in json.loads(r["citations"]):
                L.append(f"- [{c['role']}] \"{c['quote'][:160]}\" — {self.unit_label(c['unit_id'])}"
                         + (f" ({c['note']})" if c.get("note") else ""))
            L.append("")
        L += ["## Entity merge proposals (need a human)", ""]
        for p in st.q("SELECT * FROM merge_proposals ORDER BY score DESC"):
            L.append(f"- [{p['status']}] '{p['a']}' ≟ '{p['b']}' (score {p['score']}): {p['reason']}")
        return "\n".join(L)
