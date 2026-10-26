"""Deployment smoke test (no API keys; the LLM is the scripted double, LlamaParse output for the screenshot is the
fixture). Exercises what a live deployment does: case creation, uploads incl. a WhatsApp zip, the background
worker with progress, every UI page, review actions, export and deletion.

    EVIDENCE_DATA_DIR=/tmp/dd EVIDENCE_LLM=scripted python -m tests.test_deployment /mnt/user-data/uploads
"""
from __future__ import annotations
import io, json, os, sys, time, zipfile
from pathlib import Path

assert os.environ.get("EVIDENCE_LLM") == "scripted", "run with EVIDENCE_LLM=scripted"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import casefiles as cf
from app.jobs import start_job, current_job, last_jobs, PIPELINE
from ingest.models import sha256_bytes
from fixture_itinerary_grid import markdown as grid_markdown

USER, CASE = "local", "icip-trip"


def main(uploads: str):
    up = Path(uploads)
    assert cf.safe_name("../../etc/passwd") == "passwd" and "/" not in cf.safe_name("a/../../b.txt")
    root = cf.save_case(USER, CASE, {"self_name": "Jane Smith", "self_aliases": ["Jane"],
                                     "self_emails": ["jane.smith@example.com", "jane.smith@university.edu"],
                                     "chat_tz": 5.5})
    # pick any extra WhatsApp export (not the base _chat.txt) to deliver via the zip path below
    chat_via_zip = next((f for f in sorted(up.glob("_chat*.txt")) if f.name != "_chat.txt"), None)
    assert chat_via_zip, "need a second WhatsApp chat export in the dump to exercise the zip upload path"
    for f in up.iterdir():
        if f == chat_via_zip:
            continue
        cf.save_upload(root, f.name, f.read_bytes())
    # a WhatsApp "export with media" arrives as a zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("_chat.txt", chat_via_zip.read_text(encoding="utf-8"))
        z.writestr("__MACOSX/._chat.txt", "junk")
        z.writestr("../../evil.txt", "should land inside uploads")
    saved = cf.save_upload(root, "WhatsApp Chat - Subhash Goyal.zip", buf.getvalue())
    print("zip saved as:", saved)
    assert all(".." not in s for s in saved) and not (root.parent / "evil.txt").exists()

    png = next(up.glob("*image*.png"))                 # simulate LlamaParse's markdown for the screenshot
    cache = root / "ingest" / "parse_cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{sha256_bytes(png.read_bytes())}_agentic_latest.json").write_text(json.dumps(
        {"tier": "agentic", "version": "latest", "job_id": "fixture",
         "pages": [{"page": 1, "markdown": grid_markdown(), "confidence": 0.93}]}))

    job = start_job(USER, CASE, PIPELINE)
    try:
        start_job(USER, CASE, PIPELINE); raise AssertionError("second job should be refused")
    except RuntimeError:
        pass
    seen = set()
    while current_job(USER, CASE):
        j = current_job(USER, CASE)
        if j and j["step"] not in seen:
            seen.add(j["step"]); print(f"  running: {j['step']} ({j['progress']:.0%})")
        time.sleep(0.5)
    j = last_jobs(USER, CASE, 1)[0]
    print("job:", j["state"], "|", *j["log"][-6:], sep="\n  ")
    assert j["state"] == "done", j.get("error")
    import sqlite3
    v = sqlite3.connect(root / "graph.db").execute("SELECT verdict FROM insights").fetchall()
    assert ("contradicted",) in v, v

    from streamlit.testing.v1 import AppTest
    MAIN = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")
    pages = {"Files & processing": None, "Data quality": "pages/2_Data_quality.py",
             "People": "pages/3_People.py", "Timeline": "pages/4_Timeline.py",
             "Insights": "pages/5_Insights.py", "Ask": "pages/6_Ask.py",
             "Settings & privacy": "pages/7_Settings_and_privacy.py"}
    for page, pg in pages.items():
        at = AppTest.from_file(MAIN, default_timeout=60).run()
        if pg:
            at.switch_page(pg).run()
        assert not at.exception, (page, at.exception)
        if page == "Ask":
            at.text_input[0].set_value("Did I attend Dhanunjaya's presentation on 16 Sep?")
            at.button[0].click().run()
            answer = [m.value for m in at.markdown if m.value.startswith("### ")]
            print("  Ask →", answer[:1])
        if page == "Insights":
            at.button[0].click().run()            # accept the first insight
        print(f"  page ok: {page}")
    st_ = sqlite3.connect(root / "graph.db").execute("SELECT review_status FROM insights").fetchall()
    print("review status after Accept:", st_)

    # rebuild after reviews keeps decisions
    start_job(USER, CASE, ["graph", "reconcile"])
    while current_job(USER, CASE):
        time.sleep(0.3)
    print("rebuild:", last_jobs(USER, CASE, 1)[0]["state"],
          sqlite3.connect(root / "graph.db").execute("SELECT review_status FROM insights").fetchall())

    z = zipfile.ZipFile(io.BytesIO(cf.export_case(root)))
    print("export contains:", sorted(z.namelist()))
    assert not any(n.startswith("uploads") for n in z.namelist())
    cf.delete_case(USER, CASE)
    assert not root.exists()
    print("\nOK")


if __name__ == "__main__":
    main(sys.argv[1])
