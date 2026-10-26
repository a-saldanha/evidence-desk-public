# Decisions log

A running record of the meaningful choices behind this system: what was chosen, what the
alternatives were, the reasoning and tradeoffs, and what was deliberately cut. Newest build
activity is appended to the **Build log** at the bottom as work happens.

---

## D0 — Problem framing (owner: product)

> Who this is for, the specific questions they want answered, and what is intentionally out of
> scope. Owned and written by the product lead; the engineering decisions below serve it.

---

## D1 — Determinism first, LLM only where judgment is required

**Chosen:** Parse, de-duplicate, resolve timestamps, and resolve entities-by-identifier with
plain deterministic code. The LLM is used in exactly three places: extracting events+quotes
from a text window, judging plan-vs-evidence in reconciliation, and answering questions.

**Alternatives:** Hand the whole archive to one big LLM call ("summarise this"); or an agent
that decides each step.

**Reasoning / tradeoff:** Everything deterministic is unit-testable, reproducible, and free.
It also shrinks the surface where the model can hallucinate. The cost is more code than a single
prompt, and a hard boundary to maintain (e.g. the model is forbidden from computing dates — see D3).

**Cut:** end-to-end "LLM does everything"; any LLM involvement in parsing structured formats
(email MIME, ICS, chat timestamps) that already have a spec.

## D2 — Every value is grounded to a source span, or it is dropped

**Chosen:** Each extracted event carries verbatim quotes; each quote is re-located in the source
text (exact → whitespace-normalised → fuzzy ≥ 88%) before it is stored. Unfindable quotes are
dropped; an event left with none is flagged `ungrounded` for human review. Q&A may only cite the
blocks it was shown, and each citation is re-verified the same way.

**Alternatives:** Trust the model's self-reported citations; or return free-text answers with no spans.

**Reasoning / tradeoff:** Grounding turns an assertion into a checkable claim and is what makes the
UI able to show *where* a fact came from. Tradeoff: fuzzy matching needs a threshold (88%) that can
rarely drop a legitimately-paraphrased quote; we accept false-negatives over ungrounded facts.

## D3 — Temporal resolution is a pure module; the LLM never computes a date

**Chosen:** The model reports only *what the text says about time* ("yesterday", "on Saturday",
"16-07-2026"). A deterministic resolver turns that into a date against the timestamp of the unit the
quote came from.

**Reasoning:** LLM date arithmetic is a classic silent-error source. Separating "what was said" from
"what date that is" makes the second half testable (`tests/test_temporal.py`) and correct by
construction.

## D4 — Content-addressed ingest; nothing deleted, only separated

**Chosen:** Every file is identified by SHA-256 of its bytes (identical files collapse, type sniffed
from content not filename). Quotes, forwards and signatures are separated from the text the LLM sees,
but each unit keeps `raw_text` and a `Locator` (file hash + message index / line range / page +
char offsets) — that locator *is* the citation. Uncertainty is recorded, never guessed away
(timestamps carry `exact | assumed_tz | ambiguous | none`).

**Cut:** trusting filenames; destructive cleaning that would make a span un-citable.

## D5 — SQLite graph + content-addressed file store + persisted human decisions

**Chosen:** The knowledge graph lives in SQLite; raw files in a content-addressed store; human merge
decisions in `decisions.json`, re-applied on every rebuild.

**Alternatives:** A single rebuilt-from-scratch JSON bundle (simpler, no durable human state); a
hosted graph DB (heavier).

**Reasoning / tradeoff:** Human review must survive a re-run, so decisions are durable and replayed.
SQLite keeps the demo single-instance and zero-config. Tradeoff: single-instance only (no horizontal
scale); acceptable for this scope.

## D6 — Cost treated as a safety-critical resource

**Chosen:** A hard dollar spend cap enforced by a **file-locked ledger counted across every process and
run**; a dry-run estimator before any paid step; disk response-cache so an interrupted run resumes for
free; prompt-cache breakpoint on the system+tool prefix.

**Reasoning:** A runaway loop or a second concurrent job physically cannot overspend. Tradeoff: the
cap/ledger must never be reset casually; raising it is a deliberate act.

## D7 — LlamaParse with cost-tier routing + a native-text witness

**Chosen:** Text-layer PDFs go to the cheap tier; scans/photos to the agentic tier. The PDF's own
`pdftotext` layer is kept as an independent witness and cross-checked for dropped numbers/codes.
Results cached by (hash, tier, version).

**Reasoning:** Amounts, dates and booking codes are exactly what the graph will cite, so a parser that
silently drops them is dangerous; the witness catches that. Tradeoff: two parses to reconcile.

## D8 — Offline-by-default test harness (`ScriptedClient`)

**Chosen:** The full pipeline (layers 1, 3, 4 real; the model swapped for a scripted double) runs with
zero keys and zero cost, plus deployment smoke tests over every page and the job lifecycle.

**Tradeoff / known gap:** the scripted double proves the deterministic glue, not the real model's
output quality — so real-API behaviour is verified separately (see Build log, 2026-10-21) and model
quality is reviewed by hand on a sample.

---

## Build log

### 2026-10-21 — Verified the real LLM core end-to-end

A prior run had already produced real output (410 events, 39 insights, 258 paid calls, $2.53 on the
ledger). Confirmed the path still works today with **one** live Q&A call (≈ $0.03): it synthesised
across a PDF itinerary, WhatsApp and email, respected event status (scheduled booking vs. confirmed
arrival), grounded every citation to a source span, and surfaced a contradiction unprompted. Cost
tracking is accurate; ledger at $2.56 / $10 cap.

### 2026-10-21 — Resilience: one bad input must not break the batch

**Changed:**
- `ingest/parsers/document_parser.py`: wrapped the LlamaParse call in a bounded retry (3 attempts,
  exponential backoff) that, on any timeout / 429 / 5xx / network error, **returns `None` so the parser
  falls back to the PDF's native text layer** instead of raising. The failure reason is recorded on the
  source (`document.parse_error`) and surfaced in the QC report so the user sees *why* a file degraded.
- `ingest/pipeline.py`: wrapped the per-file parse dispatch in try/except, so a single malformed file is
  **skipped with a reason** and the rest of the batch still ingests (previously one bad file aborted the
  whole run).

**Why:** a live demo fails on a corrupt upload or a transient API hiccup, not on the happy path. These
were the two gaps that turn one bad input into a total ingest failure.

**Alternatives considered:** adding an explicit `timeout=` to the SDK call (not done — the kwarg isn't
confirmed for this SDK version; bounded retries + graceful fallback cover the same demo risk without a
guessed parameter). **Cut:** retry-with-timeout on the parse call pending an SDK-docs check.

**Tests:** `tests/test_resilience.py` — (1) a parser that raises on one file leaves the others intact;
(2) a LlamaParse failure returns `None` with a recorded reason in bounded time. Full offline suite
(`test_end_to_end`, `test_unfamiliar_files`, `test_temporal`) still green.

**Known gaps (not yet addressed, ranked by demo risk):** no explicit per-file upload size cap in the UI
(Streamlit's global `maxUploadSize` applies); the global spend-cap *check* and *add* are not atomic
across processes (bounded overshoot of one in-flight call per concurrent worker; already mitigated by
one-job-per-case + the monthly budget check); SQLite not in WAL mode.

### 2026-10-21 — Upload guards + per-user ledger atomicity (WAL tried & reverted)

**Changed (in the working copy):**
- `app/config.py` + `app/casefiles.py`: added a per-file size cap (`EVIDENCE_MAX_FILE_MB`, default 50),
  enforced in `save_upload` for both direct uploads and individual zip members, with a clear user-facing
  message. (The file-count limit at `_ui.py:203` and Streamlit's global 200 MB `maxUploadSize` already existed;
  this closes the single-huge-file and oversized-zip-member paths that could OOM a 512 MB box.)
- `app/casefiles.py`: `record_spend` now takes an exclusive file lock (mirroring the global ledger in
  `reason/llm.py`), so concurrent jobs cannot lose each other's recorded monthly spend.

**Tried and reverted — SQLite WAL mode.** Switching `graph.db` to WAL looked like a cheap crash-safety win, but
it broke `test_deployment`: `build_graph` rebuilds by `unlink()`-ing `graph.db` and recreating it, and snapshots
copy `graph.db` as a plain file — WAL's separate `-wal`/`-shm` sidecars get orphaned by both, producing
`no such table: insights` after a rebuild. Keeping WAL would mean teaching every file-level `graph.db` operation
to carry its sidecars. Not worth it: SQLite's default rollback journal is already crash-safe for our single-writer
case (a killed worker rolls back its incomplete transaction; `jobs.py` then marks the job failed and a re-run
resumes from cache). Reverted; `test_deployment` green again.

**Tests:** extended `tests/test_resilience.py` — oversized upload refused with a clear message and not written.
**Cut:** making the global spend-cap `check`+`add` fully atomic — the bounded overshoot (one in-flight call per
worker) is already contained by one-job-per-case and the monthly budget check.

**Closed from the previous known-gaps list:** per-file size cap · per-user ledger atomicity. (SQLite WAL was
considered and intentionally **not** adopted — see above.)
