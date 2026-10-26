"""Evidence desk: demo deployment UI (multipage).   streamlit run app/streamlit_app.py

This entry page is "Files & processing". The other sections live in app/pages/*.py and each has its own URL
(/Data_quality, /People, /Timeline, /Insights, /Ask, /Settings_and_privacy). All shared setup (auth, case
selection, the Workspace and helpers) is in app/_ui.py so every page behaves identically.

Users are identified by the reverse proxy (header EVIDENCE_USER_HEADER); locally everyone is 'local'.
Processing runs in background workers (app/jobs.py), so reloading or closing the page is safe.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import _ui                                               # noqa: E402

ctx = _ui.setup()
_ui.files(ctx)
