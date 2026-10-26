import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import _ui                                               # noqa: E402

ctx = _ui.setup()
_ui.ask(ctx)
