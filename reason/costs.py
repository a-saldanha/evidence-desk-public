"""Pre-run cost estimate (no API calls). Assumptions are explicit so they can be checked against real usage,
which AnthropicClient records in `client.usage` / `client.cost_usd()`."""
from __future__ import annotations
import json
from .extract import build_windows, SYSTEM, EXTRACTION_SCHEMA, self_description
from .llm import PRICES, BATCH_DISCOUNT

CHARS_PER_TOKEN = 3.6
ASSUME = {  # output tokens per call incl. thinking, low vs high effort
    "extract_out": (700, 2500), "judge_in": 2300, "judge_out": (500, 1500), "plans_per_100_events": 25,
    "events_per_window": 3, "qa_in": 7000, "qa_out": (600, 1500)}


def _cached(w, cache_dir, model) -> bool:
    import hashlib
    system = SYSTEM.format(self_desc=self_description())
    key = hashlib.sha256(json.dumps([model, system, w.text, EXTRACTION_SCHEMA], sort_keys=True).encode()).hexdigest()
    return (cache_dir / f"{key}.json").exists()


def estimate(units, sources, model="claude-sonnet-5-5", questions=20, cache_dir=None) -> dict:
    """With cache_dir, windows whose answer is already cached are excluded (and reconcile is scaled down)."""
    p = PRICES.get(model, PRICES["claude-sonnet-5-5"])
    wins = build_windows(units, sources)
    total_windows = len(wins)
    if cache_dir is not None and cache_dir.exists():
        wins = [w for w in wins if not _cached(w, cache_dir, model)]
    fixed = (len(SYSTEM.format(self_desc=self_description())) + len(json.dumps(EXTRACTION_SCHEMA))) / CHARS_PER_TOKEN + 300
    content = sum(len(w.text) for w in wins) / CHARS_PER_TOKEN
    n = len(wins)
    plans = int(n * ASSUME["events_per_window"] * ASSUME["plans_per_100_events"] / 100) + (40 if n else 0)  # + grid plans
    out = {"windows": n, "windows_cached": total_windows - n, "input_tokens": int(content + fixed * n), "plans_judged": plans}
    for i, label in enumerate(("low_effort", "high_effort")):
        ext = (content * p["in"] + fixed * p["cache_read"] * n + fixed * p["cache_write"]
               + n * ASSUME["extract_out"][i] * p["out"]) / 1e6
        judge = plans * (ASSUME["judge_in"] * p["in"] + ASSUME["judge_out"][i] * p["out"]) / 1e6
        qa = questions * (ASSUME["qa_in"] * p["in"] + ASSUME["qa_out"][i] * p["out"]) / 1e6
        out[label] = {"extraction": round(ext, 2), "reconcile": round(judge, 2), f"qa_{questions}_questions": round(qa, 2),
                      "total": round(ext + judge + qa, 2), "total_with_batch_preprocessing": round((ext + judge) * BATCH_DISCOUNT + qa, 2)}
    return out
