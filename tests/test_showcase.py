"""Showcase flow: bake a processed case, start fresh (as after a Render restart), log in, add one document
live, see what changed, reset. LLM = scripted double; screenshot parse = fixture.

    python -m tests.test_showcase /mnt/user-data/uploads
"""
import json, os, shutil, sys, tempfile, time
from pathlib import Path
TMP = Path(tempfile.mkdtemp())
os.environ.update(EVIDENCE_DATA_DIR=str(TMP / "data"), EVIDENCE_SHOWCASE_DIR=str(TMP / "showcase"),
                  EVIDENCE_LLM="scripted", EVIDENCE_PASSWORD="letmein")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reason.workspace import Workspace
from reason.llm import ScriptedClient
from test_end_to_end import responder
from fixture_itinerary_grid import markdown as grid_markdown
from ingest.models import sha256_bytes

SELF = dict(self_name="Jane Smith", self_aliases=["Jane"],
            self_emails=["jane.smith@example.com", "jane.smith@university.edu"])


def main(uploads):
    up = Path(uploads)
    png = next(up.glob("*image*.png"))
    # 1. "local" processing of the original dump (without the screenshot)
    orig = TMP / "orig"
    orig.mkdir()
    for f in up.iterdir():
        if f != png:
            shutil.copy(f, orig / f.name)
    case = TMP / "localcase"
    ws = Workspace(case, SELF["self_name"], SELF["self_aliases"], SELF["self_emails"])
    ws.ingest([orig], offline=True)
    llm = ScriptedClient(responder, cache_dir=ws.llm_cache)
    ws.extract(llm); ws.build_graph(); ws.reconcile(llm)
    # 2. bake
    args = ["build_showcase", str(case), "icip-trip", "--uploads", str(orig), "--self-name", SELF["self_name"],
            "--self-alias", "Jane"] + sum([["--self-email", e] for e in SELF["self_emails"]], [])
    sys.argv = args
    import runpy
    runpy.run_module("scripts.build_showcase", run_name="__main__")

    # 3. a fresh server start: log in, the case is there
    from streamlit.testing.v1 import AppTest
    app = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")
    at = AppTest.from_file(app, default_timeout=60); at.run()
    assert at.title[0].value == "Evidence desk" and not at.sidebar.radio, "login screen expected"
    at.text_input[0].set_value("wrong"); at.button[0].click().run()
    assert any("Wrong password" in e.value for e in at.error)
    at.text_input[0].set_value("letmein"); at.button[0].click().run()
    assert at.sidebar.selectbox[0].value == "icip-trip", at.sidebar.selectbox[0].value
    print("logged in; showcase seeded:", at.sidebar.selectbox[0].value)

    # 4. live: add the screenshot and process
    from app import casefiles as cf
    from app.jobs import start_job, current_job, last_jobs, PIPELINE
    root = cf.case_path("demo", "icip-trip")
    before_ins = json.load(open(root / "insights.json"))
    cf.save_upload(root, png.name, png.read_bytes())
    cache = root / "ingest" / "parse_cache"; cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{sha256_bytes(png.read_bytes())}_agentic_latest.json").write_text(json.dumps(
        {"tier": "agentic", "version": "latest", "job_id": "fixture", "pages": [{"page": 1, "markdown": grid_markdown()}]}))
    start_job("demo", "icip-trip", PIPELINE)
    while current_job("demo", "icip-trip"):
        time.sleep(0.5)
    j = last_jobs("demo", "icip-trip", 1)[0]
    print("live run:", j["state"], "|", [l for l in j["log"] if "Changed by" in l or "Estimated" in l])
    assert j["state"] == "done", j.get("error")
    ch = json.load(open(root / "last_changes.json"))
    print(f"insights before: {len(before_ins)} → new: {len(ch['insights'])}, new events: {len(ch['events'])}")
    assert ch["insights"], "the screenshot should produce a new insight"

    at.switch_page("pages/5_Insights.py").run()
    heads = [m.value for m in at.markdown if "<h4>" in m.value]
    print("insights page:", [h[h.index("<h4>") + 4:h.index("</h4>")] for h in heads])
    assert "🆕" in heads[0]
    at.switch_page("pages/4_Timeline.py").run()
    assert not at.exception, at.exception
    at.switch_page("pages/7_Settings_and_privacy.py").run()
    reset_btn = [b for b in at.button if b.label == "Reset to the showcase state"][0]
    reset_btn.click().run()
    assert not (root / "last_changes.json").exists() and not (root / "uploads" / png.name).exists()
    print("reset ok; uploads back to", len(cf.list_uploads(root)), "files")
    print("\nOK")


if __name__ == "__main__":
    main(sys.argv[1])
