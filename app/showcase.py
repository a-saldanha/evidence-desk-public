"""Showcase cases baked into the image (see scripts/build_showcase.py)."""
from __future__ import annotations
import json, shutil
from pathlib import Path
from .config import SHOWCASE_DIR
from . import casefiles as cf

SKIP = {"jobs", "ledger.json"}


def available() -> list[str]:
    if not SHOWCASE_DIR.exists():
        return []
    return sorted(p.name for p in SHOWCASE_DIR.iterdir() if (p / "case.json").exists())


def ensure_seeded(user: str) -> list[str]:
    """Copy any showcase case the user doesn't have yet. Cheap no-op once seeded."""
    seeded = []
    for name in available():
        dst = cf.case_path(user, name)
        if not (dst / "case.json").exists():
            shutil.copytree(SHOWCASE_DIR / name, dst, ignore=shutil.ignore_patterns(*SKIP), dirs_exist_ok=True)
            seeded.append(name)
    return seeded


def reset(user: str, name: str):
    """Back to the baked-in state: removes live uploads and their results."""
    cf.delete_case(user, name)
    shutil.copytree(SHOWCASE_DIR / name, cf.case_path(user, name), ignore=shutil.ignore_patterns(*SKIP))
