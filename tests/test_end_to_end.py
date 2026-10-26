"""End-to-end plumbing test without API keys.

What is real here: layer 1 on the real files, windowing, quote grounding, deterministic time resolution, grid
parsing, entity resolution, candidate retrieval for reconciliation, citation re-verification, the graph store.
What is simulated (clearly): (1) LlamaParse's markdown for the itinerary screenshot, injected into the parse cache
from tests/fixture_itinerary_grid.py; (2) the LLM, replaced by a ScriptedClient that answers like a model would for
three windows and with a simple rule for the judge. Run with real keys to get real model output.

    python -m tests.test_end_to_end /mnt/user-data/uploads /tmp/case
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixture_itinerary_grid import markdown as grid_markdown
from ingest.models import sha256_bytes
from reason.workspace import Workspace
from reason.llm import ScriptedClient

W = lambda text, rel=None, wd=None, date=None, st=None, et=None, allday=False: {
    "text": text, "date_text": date, "relative_day": rel, "weekday": wd, "weekday_direction": None,
    "start_time": st, "end_time": et, "all_day": allday}


def find_tag(window: str, needle: str) -> str | None:
    cur = None
    for line in window.splitlines():
        m = re.match(r"^\[(m\d+|p\d+|e)\]", line)
        if m:
            cur = m.group(1)
        if needle in line:
            return cur
    return None


def responder(system: str, prompt: str, schema: dict, name: str) -> dict:
    if name == "record_evidence":
        events = []
        t = find_tag(prompt, "I could not meet you yesterday because I fell terribly ill")
        if t:
            events += [
                {"kind": "health", "title": "Jane ill, stayed in bed", "status": "reported_state", "tentative": False,
                 "participants": ["SELF"], "location": "Tampere", "when": W("yesterday", rel=-1, allday=True),
                 "evidence": [{"ref": t, "quote": "I could not meet you yesterday because I fell terribly ill and did not leave the bed."}]},
                {"kind": "meeting", "title": "Meeting with Prof. Goyal missed", "status": "cancelled", "tentative": False,
                 "participants": ["SELF", "IIT Kharagpur Subhash Goyal"], "location": None, "when": W("yesterday", rel=-1, allday=True),
                 "evidence": [{"ref": t, "quote": "I could not meet you yesterday"}]}]
        t = find_tag(prompt, "Have a safe journey to Germany")
        t2 = find_tag(prompt, "Hotel in Tallinn, Estonia")
        if t2:
            events.append({"kind": "travel", "title": "Jane at hotel in Tallinn", "status": "reported_state", "tentative": False,
                           "participants": ["SELF"], "location": "Tallinn", "when": W("now", rel=0, allday=True),
                           "evidence": [{"ref": t2, "quote": "Hotel in Tallinn, Estonia"}]})
        t = find_tag(prompt, "QR304")
        if t and "ELECTRONIC TICKET" in prompt:
            events.append({"kind": "travel", "title": "Flight QR304 Helsinki to Doha", "status": "scheduled", "tentative": False,
                           "participants": ["SELF"], "location": "Helsinki", "when": W("09:50 19Sep2026", date="19Sep2026", st="09:50", et="16:50"),
                           "evidence": [{"ref": t, "quote": "QR304"}]})
        return {"events": events, "entities": [{"name": "Dhanunjaya", "type": "person", "identifiers": []}] if "Dhanunjaya" in prompt else []}
    if name == "judge_plan":
        cands = re.findall(r"^\[(c\d+)\] (.+?) \[status=(\w+), kind=(\w+)", prompt, re.M)
        plan_title = re.search(r"^PLAN \[[^\]]+\]: (.+?) \[", prompt, re.M).group(1)
        for tag, title, status, kind in cands:
            if status == "reported_state" and kind == "health":
                q = re.search(rf"^\[{tag}\].*?\n\s+quotes: \"(.+?)\"", prompt, re.M | re.S).group(1)
                return {"verdict": "contradicted", "confidence": "medium",
                        "summary": f"'{plan_title}' on 16 Sep is unlikely: Jane reported being ill in bed that day.",
                        "evidence_used": [{"candidate": tag, "quote": q}], "caveats": "single first-person statement"}
        for tag, title, status, kind in cands:
            if status == "scheduled" and kind == "travel" and plan_title.split()[0].lower() in title.lower():
                q = re.search(rf"^\[{tag}\].*?\n\s+quotes: \"(.+?)\"", prompt, re.M | re.S).group(1)
                return {"verdict": "modified", "confidence": "high", "summary": f"The ticket times differ from the plan for '{plan_title}'.",
                        "evidence_used": [{"candidate": tag, "quote": q}], "caveats": "grid may include airport time"}
        return {"verdict": "no_relevant_evidence", "confidence": "low", "summary": "", "evidence_used": [], "caveats": ""}
    if name == "answer":
        m = re.search(r"^\[(E\d+)\] Jane ill.*?quotes: \"(.+?)\"", prompt, re.M)
        if m:
            return {"answer": "Probably not: the plan had it on 16 Sep 14:00, but you wrote on the 17th that you were ill in bed the day before.",
                    "citations": [{"ref": m.group(1), "quote": m.group(2)}], "confidence": "medium", "open_questions": []}
        return {"answer": "No evidence found.", "citations": [], "confidence": "low", "open_questions": []}
    raise ValueError(name)


def main(uploads: str, case: str):
    case_p = Path(case)
    ws = Workspace(case_p, "Jane Smith", ["Jane"], ["jane.smith@example.com", "jane.smith@university.edu"])
    # simulate LlamaParse having parsed the screenshot (cache is read before any API call)
    png = next(Path(uploads).glob("*image*.png"))
    sid = sha256_bytes(png.read_bytes())
    cache = case_p / "ingest" / "parse_cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{sid}_agentic_latest.json").write_text(json.dumps(
        {"tier": "agentic", "version": "latest", "job_id": "fixture", "pages": [{"page": 1, "markdown": grid_markdown(), "confidence": 0.93}]}))
    ws.ingest([Path(uploads)], offline=True)
    llm = ScriptedClient(responder, cache_dir=None)
    print("layer 2:", ws.extract(llm))
    print("layer 3:", ws.build_graph())
    st = ws.store()
    plan = [e for e in st.events() if e["title"].startswith("Dhanunjay")][0]
    from reason.reconcile import candidates_for
    uidx = ws._unit_index()
    pool = [e for e in st.events() if e["start_local"] and e["evidence"] and e["status"] in ("happened", "cancelled", "reported_state", "scheduled")]
    print("\nCandidates retrieved for plan:", plan["title"], plan["start_local"])
    for c in candidates_for(plan, pool, {k: u["locator"]["source_id"] for k, u in uidx.items()}):
        print("   -", c["status"], "|", c["title"], "|", c["start_local"], "→", c["end_local"])
    print("\nlayer 4:", ws.reconcile(llm))
    (case_p / "report.md").write_text(ws.report(), encoding="utf-8")
    r = ws.qa(llm).ask("Did I attend Dhanunjaya's presentation on 16 Sep?")
    print("\nQ&A:", r["answer"], r["citations"][:1])
    assert any(i["verdict"] == "contradicted" for i in st.q("SELECT verdict FROM insights")), "expected the illness contradiction"
    print("\nOK")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
