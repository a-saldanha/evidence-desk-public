"""Turn a case you processed locally (with real API keys) into the showcase baked into the image.

    # 1. process the original dump locally, e.g.
    python -m reason.run ./uploads --case ./case --self-name "Jane Smith" --self-alias "Jane" \
        --self-email jane.smith@example.com --self-email jane.smith@university.edu
    # 2. bake it
    python -m scripts.build_showcase ./case icip-trip --self-name "Jane Smith" --self-alias "Jane" \
        --self-email jane.smith@example.com --self-email jane.smith@university.edu
    # 3. commit showcase/ to your PRIVATE repo and deploy

The LLM and parse caches travel with it, so re-processing the original files on the server is free; only files
uploaded live cost API calls.
"""
import argparse, json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("case_dir", type=Path)
ap.add_argument("name")
ap.add_argument("--self-name"); ap.add_argument("--self-alias", action="append", default=[])
ap.add_argument("--self-email", action="append", default=[]); ap.add_argument("--chat-tz", type=float, default=5.5)
ap.add_argument("--uploads", type=Path, help="original files, if the case has no uploads/ folder (CLI runs)")
a = ap.parse_args()

src = a.case_dir
for need in ("graph.db", "events.json", "ingest/units.jsonl"):
    if not (src / need).exists():
        sys.exit(f"{src} is not a processed case (missing {need}). Process it first.")
sys.path.insert(0, str(ROOT))
from app.config import SHOWCASE_DIR
dst = SHOWCASE_DIR / a.name
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst, ignore=shutil.ignore_patterns("jobs", "ledger.json", "last_changes.json",
                                                        "snapshot_before.json", "__pycache__"))
up = dst / "uploads"
if not up.exists() or not any(up.rglob("*")):
    if not a.uploads:
        sys.exit("The case has no uploads/ folder: pass --uploads <folder with the original files>.")
    shutil.copytree(a.uploads, up, dirs_exist_ok=True)
cfg_path = dst / "case.json"
cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
if a.self_name:
    cfg.update(self_name=a.self_name, self_aliases=a.self_alias, self_emails=[e.lower() for e in a.self_email],
               chat_tz=a.chat_tz)
if not cfg.get("self_name"):
    sys.exit("Pass --self-name/--self-alias/--self-email (exactly as used when processing, or the cache won't match).")
cfg_path.write_text(json.dumps(cfg, indent=1))
size = sum(p.stat().st_size for p in dst.rglob("*") if p.is_file()) / 1e6
n_cache = len(list((dst / "llm_cache").glob("*.json"))) if (dst / "llm_cache").exists() else 0
print(f"showcase/{a.name}: {size:.1f} MB, {n_cache} cached LLM answers")
if n_cache == 0:
    print("WARNING: no LLM cache; re-processing on the server would call the API for every window.")
print("This folder contains your personal documents: keep the repository PRIVATE and set EVIDENCE_PASSWORD.")
