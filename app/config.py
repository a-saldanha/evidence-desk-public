"""Deployment settings, all from environment variables (see deploy/.env.example)."""
from __future__ import annotations
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("EVIDENCE_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
USER_HEADER = os.environ.get("EVIDENCE_USER_HEADER", "X-Auth-User")      # set by the reverse proxy after login
BUDGET_USD_PER_USER_MONTH = float(os.environ.get("EVIDENCE_BUDGET_USD", "10"))
MAX_UPLOAD_FILES = int(os.environ.get("EVIDENCE_MAX_FILES", "300"))
MAX_ZIP_UNCOMPRESSED_MB = int(os.environ.get("EVIDENCE_MAX_ZIP_MB", "500"))
MAX_FILE_MB = int(os.environ.get("EVIDENCE_MAX_FILE_MB", "50"))   # per-file cap; one huge file can OOM a small box
# "anthropic" in production. "scripted" runs the test double for smoke-testing a deployment without spending;
# the UI shows a banner whenever it is active.
LLM_BACKEND = os.environ.get("EVIDENCE_LLM", "anthropic")
DEMO_USER = os.environ.get("EVIDENCE_DEMO_USER", "")   # this user's cases are read-only showcases

# ---- showcase mode (free hosts with an ephemeral disk, e.g. Render free) ----
# A pre-processed case baked into the image is copied into DATA_DIR at start-up, so the demo is complete
# after every restart. Live uploads work but last only until the next restart.
SHOWCASE_DIR = Path(os.environ.get("EVIDENCE_SHOWCASE_DIR", Path(__file__).resolve().parents[1] / "showcase"))
PASSWORD = os.environ.get("EVIDENCE_PASSWORD", "")        # in-app login when no reverse proxy is in front
MASK_UI = os.environ.get("EVIDENCE_MASK_UI", "1") == "1"   # hide passport numbers, passwords etc. in citations
TRUST_PROXY_HEADER = os.environ.get("EVIDENCE_TRUST_PROXY_HEADER") == "1"  # only set behind Caddy, never on Render
