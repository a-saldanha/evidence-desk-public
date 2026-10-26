"""Real-world failure modes (offline, no API, no cost).

Two guarantees the live demo depends on:
  1. One malformed file must never abort the whole ingest — it is skipped with a reason, the rest proceed.
  2. A LlamaParse failure (timeout / 5xx / network) must fall back to the native text layer instead of raising.
"""
from __future__ import annotations
import sys, tempfile, time, types
from pathlib import Path

from ingest.pipeline import run as ingest_run
from ingest.parsers.document_parser import DocumentParser
import ingest.parsers.misc_parsers as misc_mod


def test_one_bad_file_does_not_kill_ingest():
    """A parser that raises on a single file leaves the others untouched."""
    orig = misc_mod.parse_text

    def selective(store, s):
        if s.filename == "bad.txt":
            raise RuntimeError("simulated corrupt file")
        return orig(store, s)

    misc_mod.parse_text = selective
    try:
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            inp = d / "in"; inp.mkdir()
            (inp / "bad.txt").write_text("this file blows up in its parser\n")
            (inp / "good.txt").write_text("notes: meeting with the team on 16 Sep 2026 at 14:00\n")
            store, units, issues = ingest_run([inp], d / "out", offline=True)
    finally:
        misc_mod.parse_text = orig

    bad = next(s for s in store.sources.values() if s.filename == "bad.txt")
    assert "parse error" in (bad.meta.get("skipped") or ""), "crashing file must be skipped with a reason"
    assert units, "the healthy file must still produce units"
    print("  ok: one corrupt file is skipped; the rest of the batch still ingests")


def test_llamaparse_failure_falls_back_gracefully():
    """A LlamaParse exception returns None (→ native-text fallback) with a recorded reason, within bounded time."""
    class BoomClient:
        def __init__(self, *a, **k):
            raise RuntimeError("simulated LlamaParse 503")

    fake = types.ModuleType("llama_cloud"); fake.LlamaCloud = BoomClient
    saved = sys.modules.get("llama_cloud")
    sys.modules["llama_cloud"] = fake
    try:
        with tempfile.TemporaryDirectory() as d:
            dp = DocumentParser(store=None, cache_dir=Path(d) / "cache", offline=False)
            dp.api_key, dp.offline = "fake-key", False
            t0 = time.time()
            out = dp._llamaparse("/does-not-exist.pdf", "sid123", "cost_effective")
            assert out is None, "a LlamaParse failure must return None, not raise"
            assert dp._last_parse_error and "RuntimeError" in dp._last_parse_error, "the failure reason must be recorded"
            assert time.time() - t0 < 12, "retries must be bounded (no unbounded hang)"
    finally:
        if saved is not None:
            sys.modules["llama_cloud"] = saved
        else:
            del sys.modules["llama_cloud"]
    print("  ok: LlamaParse failure degrades to None with a recorded reason, bounded time")


def test_oversized_file_is_rejected_clearly():
    """A file over the per-file cap is refused with a clear message and never written."""
    import app.casefiles as cf
    saved = cf.MAX_FILE_MB
    cf.MAX_FILE_MB = 1                                   # 1 MB cap for the test
    try:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            try:
                cf.save_upload(root, "huge.pdf", b"x" * (2 * 1024 * 1024))   # 2 MB
                assert False, "oversized file should have raised"
            except ValueError as e:
                assert "larger than 1 MB" in str(e), f"unclear message: {e}"
            assert not (root / "uploads" / "huge.pdf").exists(), "oversized file must not be written"
    finally:
        cf.MAX_FILE_MB = saved
    print("  ok: oversized upload refused with a clear message, not written")


if __name__ == "__main__":
    test_one_bad_file_does_not_kill_ingest()
    test_llamaparse_failure_falls_back_gracefully()
    test_oversized_file_is_rejected_clearly()
    print("OK")
