"""Run every layer on a folder of documents.

    export ANTHROPIC_API_KEY=...            (LLAMA_CLOUD_API_KEY optional, for scans/images)
    python -m reason.run ./uploads --case ./case --self-name "Jane Smith" \
        --self-alias "Jane" --self-email jane.smith@example.com --self-email jane.smith@university.edu
    python -m reason.run --case ./case --ask "Did I attend Dhanunjaya's presentation?" --self-name "Jane Smith"
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from .workspace import Workspace
from .llm import default_client


def bar(label):
    def f(i, n):
        sys.stderr.write(f"\r{label}: {i}/{n}"); sys.stderr.flush()
        if i == n:
            sys.stderr.write("\n")
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="*", type=Path)
    ap.add_argument("--case", type=Path, required=True)
    ap.add_argument("--self-name", required=True)
    ap.add_argument("--self-alias", action="append", default=[])
    ap.add_argument("--self-email", action="append", default=[])
    ap.add_argument("--chat-tz", type=float, default=5.5)
    ap.add_argument("--model", default=None)
    ap.add_argument("--ask", default=None)
    ap.add_argument("--skip-ingest", action="store_true")
    a = ap.parse_args()

    ws = Workspace(a.case, a.self_name, a.self_alias, a.self_email, a.chat_tz)
    llm = default_client(ws.llm_cache, a.model)
    if a.ask:
        r = ws.qa(llm).ask(a.ask)
        print(r["answer"], f"\n(confidence: {r['confidence']})")
        for c in r["citations"]:
            print(f"  - \"{c['quote'][:140]}\" — {c['where']}")
        return
    if a.inputs and not a.skip_ingest:
        ws.ingest(a.inputs)
    from .costs import estimate
    print("estimated cost (USD):", json.dumps(estimate(ws.units(), ws.sources())["low_effort"]))
    print("layer 2:", ws.extract(llm, progress=bar("extracting")))
    print("layer 3:", ws.build_graph())
    print("layer 4:", ws.reconcile(llm, progress=bar("reconciling")))
    (a.case / "report.md").write_text(ws.report(), encoding="utf-8")
    spent = (f", this run ≈ ${llm.cost_usd():.2f}, all runs ${llm.ledger.total():.2f} of ${llm.ledger.cap:.2f} cap"
             if hasattr(llm, "cost_usd") else "")
    print(f"LLM calls: {llm.calls} (cache hits {llm.cache_hits}){spent} -> {a.case}/report.md")


if __name__ == "__main__":
    main()
