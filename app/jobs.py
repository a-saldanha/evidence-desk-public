"""Background jobs: each run is a detached worker process, so closing the tab or reloading the page never
stops processing. Status lives in <case>/jobs/<job_id>.json and the UI polls it.

    start_job(user, case, ["ingest", "extract", "graph", "reconcile"])   -> job_id
    python -m app.jobs <user> <case> <job_id>                            (the worker, started by start_job)
"""
from __future__ import annotations
import json, os, subprocess, sys, time, traceback, uuid
from datetime import datetime, timezone
from pathlib import Path

from .casefiles import case_path, load_case, budget_left, record_spend
from .config import LLM_BACKEND

PIPELINE = ["ingest", "extract", "graph", "reconcile"]
ROOT = Path(__file__).resolve().parents[1]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def jobs_dir(user, case) -> Path:
    d = case_path(user, case) / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(path: Path, data: dict):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)                                   # atomic: the UI never reads half a file


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def current_job(user, case) -> dict | None:
    """The running job, or None. A worker that died without finishing is marked failed here."""
    d = jobs_dir(user, case)
    lock = d / "running.lock"
    if not lock.exists():
        return None
    info = json.loads(lock.read_text())
    st = job_status(user, case, info["job_id"])
    if st and st["state"] in ("queued", "running") and not _alive(info.get("pid")) and time.time() - info["t"] > 5:
        st.update(state="failed", error="worker process exited unexpectedly", finished=_now())
        _write(d / f"{info['job_id']}.json", st)
    if not st or st["state"] not in ("queued", "running"):
        lock.unlink(missing_ok=True)
        return None
    return st


def job_status(user, case, job_id) -> dict | None:
    p = jobs_dir(user, case) / f"{job_id}.json"
    return json.loads(p.read_text()) if p.exists() else None


def last_jobs(user, case, n=5) -> list[dict]:
    files = sorted(jobs_dir(user, case).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:n]
    return [json.loads(p.read_text()) for p in files]


def start_job(user, case, steps: list[str]) -> str:
    if current_job(user, case):
        raise RuntimeError("A job is already running for this case.")
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = jobs_dir(user, case)
    _write(d / f"{job_id}.json", {"job_id": job_id, "state": "queued", "steps": steps, "step": None,
                                   "progress": 0.0, "message": "starting", "log": [], "created": _now(),
                                   "cost_usd": 0.0, "llm_backend": LLM_BACKEND})
    proc = subprocess.Popen([sys.executable, "-m", "app.jobs", user, case, job_id], cwd=str(ROOT),
                            stdout=open(d / f"{job_id}.log", "w"), stderr=subprocess.STDOUT,
                            start_new_session=True, env={**os.environ, "PYTHONPATH": str(ROOT)})
    _write(d / "running.lock", {"job_id": job_id, "pid": proc.pid, "t": time.time()})
    return job_id


# ---------------------------------------------------------------- worker side
class Reporter:
    def __init__(self, path: Path):
        self.path, self.state, self._t = path, json.loads(path.read_text()), 0.0

    def update(self, force=False, **kw):
        self.state.update(kw)
        if force or time.time() - self._t > 0.7:            # throttle disk writes
            _write(self.path, self.state)
            self._t = time.time()

    def log(self, msg: str):
        self.state["log"].append(f"{datetime.now():%H:%M:%S} {msg}")
        self.update(force=True, message=msg)


def make_llm(cache_dir: Path):
    if LLM_BACKEND == "scripted":
        sys.path.insert(0, str(ROOT / "tests"))
        from tests.test_end_to_end import responder
        from reason.llm import ScriptedClient
        return ScriptedClient(responder, cache_dir=None)
    from reason.llm import default_client
    return default_client(cache_dir)


def _graph_ids(root: Path) -> dict:
    db = root / "graph.db"
    if not db.exists():
        return {"events": [], "insights": [], "entities": []}
    import sqlite3
    con = sqlite3.connect(str(db))
    try:
        return {"events": [r[0] for r in con.execute("SELECT event_id FROM events")],
                "insights": [r[0] for r in con.execute("SELECT insight_id FROM insights")],
                "entities": [r[0] for r in con.execute("SELECT entity_id FROM entities")]}
    finally:
        con.close()


def run_worker(user: str, case: str, job_id: str):
    from reason.workspace import Workspace
    from reason.costs import estimate
    rep = Reporter(jobs_dir(user, case) / f"{job_id}.json")
    rep.update(force=True, state="running", started=_now())
    llm = None
    try:
        cfg = load_case(user, case)
        root = case_path(user, case)
        ws = Workspace(root, cfg["self_name"], cfg["self_aliases"], cfg["self_emails"], cfg["chat_tz"])
        steps = rep.state["steps"]
        before = _graph_ids(root)
        (root / "snapshot_before.json").write_text(json.dumps(before))
        needs_llm = bool({"extract", "reconcile"} & set(steps))
        if needs_llm:
            llm = make_llm(ws.llm_cache)
        for i, step in enumerate(steps):
            base, span = i / len(steps), 1 / len(steps)
            prog = lambda k, n, label=step: rep.update(progress=base + span * k / max(n, 1), message=f"{label}: {k}/{n}")
            rep.update(force=True, step=step, progress=base)
            if step == "ingest":
                rep.log("Layer 1: parsing, de-duplicating and checking files")
                _, units, issues = ws.ingest([root / "uploads"])
                rep.log(f"{len(ws.sources())} sources → {len(units)} units, {len(issues)} QC issues")
            elif step == "extract":
                e = estimate(ws.units(), ws.sources(), cache_dir=ws.llm_cache, questions=0,
                             model=getattr(llm, "model", "claude-sonnet-5-5"))
                est = e["low_effort"]["total"]
                left = budget_left(user)
                rep.log(f"{e['windows_cached']} windows already cached, {e['windows']} new → estimated cost ≤ ${est:.2f}; "
                        f"budget left this month ${left:.2f}")
                if LLM_BACKEND != "scripted" and est > left:
                    raise RuntimeError(f"Estimated cost ${est:.2f} exceeds the remaining budget ${left:.2f}. "
                                       "Remove files or raise EVIDENCE_BUDGET_USD.")
                rep.log("Layer 2: extracting events with quotes")
                st = ws.extract(llm, progress=prog)
                rep.log(f"{st['events_grounded']} grounded events from {st['windows']} windows "
                        f"({st['quotes_dropped']} unverifiable quotes dropped, {st['failed_windows']} failed windows)")
            elif step == "graph":
                rep.log("Layer 3: resolving people and building the graph")
                st = ws.build_graph()
                rep.log(f"{st['entities']} entities, {st['merge_proposals']} merge proposals to review")
            elif step == "reconcile":
                rep.log("Layer 4: checking plans against evidence")
                st = ws.reconcile(llm, progress=prog)
                rep.log(f"{st['insights']} insights from {st['judged']} checked plans")
            if llm is not None and hasattr(llm, "cost_usd"):
                rep.update(force=True, cost_usd=round(llm.cost_usd(), 4))
        (root / "report.md").write_text(ws.report(), encoding="utf-8")
        after = _graph_ids(root)
        changes = {k: sorted(set(after[k]) - set(before[k])) for k in after}
        changes["finished"] = _now()
        (root / "last_changes.json").write_text(json.dumps(changes))
        if any(before.values()):
            rep.log(f"Changed by this run: +{len(changes['events'])} events, +{len(changes['insights'])} insights, "
                    f"+{len(changes['entities'])} people/organisations")
        rep.update(force=True, changes={k: len(v) for k, v in changes.items() if k != "finished"})
        rep.log("Done")
        rep.update(force=True, state="done", progress=1.0, finished=_now())
    except Exception as e:
        rep.state["log"].append(traceback.format_exc()[-1500:])
        rep.update(force=True, state="failed", error=str(e), finished=_now())
    finally:
        if llm is not None and hasattr(llm, "cost_usd"):
            record_spend(user, llm.cost_usd())
        (jobs_dir(user, case) / "running.lock").unlink(missing_ok=True)


if __name__ == "__main__":
    run_worker(*sys.argv[1:4])
