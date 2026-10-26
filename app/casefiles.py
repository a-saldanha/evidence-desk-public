"""Per-user case folders, case settings, safe uploads, export, deletion and the spend ledger.

Layout
  DATA_DIR/users/<user>/cases/<case>/case.json      who the documents belong to (self name, aliases, emails, tz)
                                    /uploads/        user files (WhatsApp zips are unpacked here)
                                    /ingest, graph.db, llm_cache, decisions.json, jobs/ ...
  DATA_DIR/users/<user>/ledger.json                  actual LLM spend per month
"""
from __future__ import annotations
import io, json, re, shutil, zipfile
from datetime import datetime, timezone
from pathlib import Path
from .config import DATA_DIR, MAX_ZIP_UNCOMPRESSED_MB, MAX_FILE_MB, BUDGET_USD_PER_USER_MONTH

SAFE = re.compile(r"[^A-Za-z0-9._ \-()\[\]&@+,]")


def safe_name(name: str, fallback: str = "file") -> str:
    """Strip any path, control or odd characters; never allow traversal."""
    name = Path(str(name).replace("\\", "/")).name
    name = SAFE.sub("_", name).strip(" .")
    return name[:180] or fallback


def safe_user(user: str) -> str:
    return re.sub(r"[^a-z0-9_.@-]", "_", (user or "local").lower())[:80] or "local"


def user_dir(user: str) -> Path:
    d = DATA_DIR / "users" / safe_user(user)
    (d / "cases").mkdir(parents=True, exist_ok=True)
    return d


def list_cases(user: str) -> list[str]:
    return sorted(p.name for p in (user_dir(user) / "cases").iterdir() if (p / "case.json").exists())


def case_path(user: str, case: str) -> Path:
    return user_dir(user) / "cases" / safe_name(case, "case")


def load_case(user: str, case: str) -> dict:
    p = case_path(user, case) / "case.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_case(user: str, case: str, cfg: dict) -> Path:
    root = case_path(user, case)
    (root / "uploads").mkdir(parents=True, exist_ok=True)
    clean = {"self_name": cfg.get("self_name", "").strip(),
             "self_aliases": [a.strip() for a in cfg.get("self_aliases", []) if a.strip()],
             "self_emails": [e.strip().lower() for e in cfg.get("self_emails", []) if e.strip()],
             "chat_tz": float(cfg.get("chat_tz", 5.5)),
             "created": load_case(user, case).get("created") or datetime.now(timezone.utc).isoformat()}
    (root / "case.json").write_text(json.dumps(clean, indent=1))
    return root


def save_upload(root: Path, filename: str, data: bytes) -> list[str]:
    """Saves a file; unpacks zip archives (e.g. 'WhatsApp Chat - X.zip') safely. Returns saved names."""
    up = root / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    name = safe_name(filename)
    if name.lower().endswith(".zip") and zipfile.is_zipfile(io.BytesIO(data)):
        saved, total = [], 0
        stem = safe_name(Path(name).stem, "archive")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if info.is_dir() or info.file_size == 0:
                    continue
                total += info.file_size
                if info.file_size > MAX_FILE_MB * 1024 * 1024:
                    raise ValueError(f"{name}: '{info.filename}' is larger than {MAX_FILE_MB} MB")
                if total > MAX_ZIP_UNCOMPRESSED_MB * 1024 * 1024:
                    raise ValueError(f"{name}: archive expands beyond {MAX_ZIP_UNCOMPRESSED_MB} MB")
                raw_parts = info.filename.replace("\\", "/").split("/")
                if "__MACOSX" in raw_parts or raw_parts[-1].startswith("._") or raw_parts[-1] in (".DS_Store",):
                    continue                        # macOS metadata, must be checked before sanitising the name
                inner = safe_name(info.filename)
                if (up / stem / inner).exists() and inner in [Path(x).name for x in saved]:
                    inner = f"{Path(inner).stem}-{len(saved)}{Path(inner).suffix}"   # never overwrite within one archive
                target = up / stem / inner          # keep each archive's files together; WhatsApp media names collide across chats
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(info))
                saved.append(f"{stem}/{inner}")
        return saved
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise ValueError(f"{name}: file is larger than {MAX_FILE_MB} MB")
    (up / name).write_bytes(data)
    return [name]


def list_uploads(root: Path) -> list[dict]:
    up = root / "uploads"
    if not up.exists():
        return []
    return [{"file": str(p.relative_to(up)), "size_kb": round(p.stat().st_size / 1024, 1)}
            for p in sorted(up.rglob("*")) if p.is_file()]


def remove_upload(root: Path, rel: str):
    target = (root / "uploads" / rel).resolve()
    if (root / "uploads").resolve() in target.parents and target.is_file():
        target.unlink()


def delete_case(user: str, case: str):
    """Removes everything derived from the user's documents: raw copies, parses, LLM cache, graph, jobs."""
    root = case_path(user, case)
    if root.exists():
        shutil.rmtree(root)


def export_case(root: Path) -> bytes:
    """Portable results (no raw documents): reports, events, insights, QC issues, entity decisions."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in ["report.md", "events.json", "insights.json", "decisions.json", "case.json",
                    "ingest/qc_report.md", "ingest/qc_issues.jsonl", "ingest/sources.json"]:
            p = root / rel
            if p.exists():
                z.write(p, rel)
    return buf.getvalue()


# ---------------------------------------------------------------- snapshots (save / roll back demo state)
SNAP_DIR = ".snapshots"
# Only the layer 2-4 outputs that review + rebuild change. ingest/, llm_cache/ and uploads/ are left shared,
# so a restore is instant and spends nothing: the parsed text and model cache are reused exactly as they are.
_SNAP_FILES = ["graph.db", "decisions.json", "insights.json", "events.json", "mentions.json",
               "report.md", "clock_shifts.json", "last_changes.json"]


def list_snapshots(root: Path) -> list[str]:
    d = root / SNAP_DIR
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []


def save_snapshot(root: Path, name: str) -> Path:
    """Copy the current reviewed state into <case>/.snapshots/<name>/ (overwrites a snapshot of the same name)."""
    dst = root / SNAP_DIR / safe_name(name, "snapshot")
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for rel in _SNAP_FILES:
        p = root / rel
        if p.exists():
            shutil.copy2(p, dst / rel)
    return dst


def restore_snapshot(root: Path, name: str):
    """Overwrite the live case with a saved snapshot. Files the snapshot lacks are removed so the state matches."""
    src = root / SNAP_DIR / safe_name(name, "snapshot")
    if not src.exists():
        raise ValueError(f"no snapshot named {name!r}")
    for rel in _SNAP_FILES:
        sp, dp = src / rel, root / rel
        if sp.exists():
            shutil.copy2(sp, dp)
        elif dp.exists():
            dp.unlink()


def delete_snapshot(root: Path, name: str):
    d = root / SNAP_DIR / safe_name(name, "snapshot")
    if d.exists():
        shutil.rmtree(d)


# ---------------------------------------------------------------- spend ledger
def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def spent_this_month(user: str) -> float:
    p = user_dir(user) / "ledger.json"
    return json.loads(p.read_text()).get(_month(), 0.0) if p.exists() else 0.0


def record_spend(user: str, usd: float):
    import fcntl
    p = user_dir(user) / "ledger.json"
    with open(p, "a+") as f:                             # exclusive lock so concurrent jobs can't lose each other's spend
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            d = json.loads(f.read() or "{}")
        except ValueError:
            d = {}
        d[_month()] = round(d.get(_month(), 0.0) + usd, 4)
        f.seek(0); f.truncate(); f.write(json.dumps(d))


def budget_left(user: str) -> float:
    return round(BUDGET_USD_PER_USER_MONTH - spent_this_month(user), 2)
