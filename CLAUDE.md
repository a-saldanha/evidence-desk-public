# Evidence Desk: instructions for Claude Code

You are finishing, verifying and deploying a demo of an evidence-reconstruction system: messy personal
documents (emails, WhatsApp/SMS exports, PDFs, screenshots) → cleaned, cited units → events and people in a
knowledge graph → plan-vs-evidence insights and cited Q&A, shown in a Streamlit UI. The code is written and
passes offline tests. Your job is to make it work with real APIs, produce the showcase, and deploy it.
Read `README.md` once for the architecture before you start.

## Project map

- `ingest/` layer 1: parsing, de-duplication, QC (no LLM). `ingest/pipeline.py` is the entry point.
- `reason/` layers 2–4: `extract.py` (LLM extraction + quote grounding), `temporal.py` (date resolution),
  `grid.py` (itinerary spreadsheets), `entities.py`, `store.py` (SQLite graph), `reconcile.py`, `query.py`,
  `llm.py` (Anthropic client, cache, cost tracking), `costs.py` (pre-run estimate), `workspace.py` (orchestrator),
  `run.py` (CLI).
- `app/` UI and deployment: `streamlit_app.py`, `jobs.py` (background worker), `casefiles.py`, `showcase.py`, `config.py`.
- `deploy/` Dockerfile, docker-compose + Caddy (VPS option). `render.yaml` (the chosen free deployment).
- `scripts/` `seed_case.py`, `build_showcase.py`. `tests/` offline tests; the LLM there is a scripted double.

## Inputs the user provides

- Their documents in `raw_dump/` (gitignored). One file is the itinerary spreadsheet screenshot
  (`*image*.png`); it is the **live demo file** and must NOT be part of the baked showcase.
- Keys in a local `.env` (gitignored): `ANTHROPIC_API_KEY`, `LLAMA_CLOUD_API_KEY`. Load them with
  `set -a; source .env; set +a`. Never print, log or commit keys.
- Case owner settings (use exactly these everywhere, or the LLM cache will not match):
  `--self-name "<Your Name>" --self-alias "<chat nickname>" --self-email <your@email.com> --chat-tz <UTC offset>`

## Cost discipline (the user's credits are limited; treat every API call as real money)

Built-in guards you must keep enabled and never bypass:
- **Hard spend cap**: `EVIDENCE_MAX_SPEND_USD` (in `.env`, default 10) with a persistent ledger
  (`EVIDENCE_SPEND_LEDGER`, default `./.spend_ledger.json`) counted across all runs and processes. When it is
  reached, calls stop with `SpendCapReached`. Never raise the cap, delete or reset the ledger, or change the
  guard yourself; ask the user. Report `ledger total / cap` after every step that used the API.
- **Response cache** (`<case>/llm_cache/`): every completed call is saved immediately, so an interrupted run
  resumes for free. Never delete `llm_cache/` or `parse_cache/` and never run with caching disabled.
- **Prompt caching**: the system prompt + tool schema carry a cache breakpoint; keep them byte-identical
  between calls (no timestamps or run ids in them).

How to work:
- **Model**: `claude-sonnet-5-5` (the default) at `effort: low` for extraction. Do not switch to a larger
  model or raise effort without asking.
- **Test code paths without the API**: use `EVIDENCE_LLM=scripted` / `ScriptedClient` and the offline tests.
  Spend real credits only to verify real API behaviour, on the smallest input that proves the point.
- **Estimate before spending**: run `reason.costs.estimate(..., cache_dir=...)` before any paid step; it counts
  only uncached work. Tell the user the number.
- **Changing a prompt or schema invalidates the cache for every window it touches.** Try changes on 2–3
  windows first, show the user the before/after, and only then re-run more (with an estimate).
- **No loops or blind retries**: never put paid calls in a retry loop, a watch loop or a test that runs them
  repeatedly. If a call fails, read the error and fix the cause; the client already retries transient errors.
- **Don't re-run what's done**: use `--skip-ingest` where possible; re-run only the layer that changed
  (`build_graph` and `reconcile` re-use cached judge answers).
- **Background runs**: if you start a long run in the background, write progress to a log file, check it
  periodically instead of restarting it, and never start a second run on the same case while one is going.
  If a run dies, just re-run the same command: completed calls come from the cache.
- **Q&A**: each question is a paid call. Ask at most 3 test questions; repeated identical questions are cached.
- **LlamaParse**: parse each file once (results are cached by content); use the free tier's budget only for the
  screenshot and scanned files.
- **Your own session**: the user's API credits are for the pipeline. Keep your own context lean: don't
  print whole datasets or large JSON files; read with `head`, `wc`, `jq` filters and targeted greps.

## Rules

1. **Stop and ask the user** before: any step whose estimated LLM cost exceeds $3, the first full processing
   run, creating a GitHub repo or pushing, and anything that deletes user data outside `/tmp`.
2. **No demo-specific hacks.** Never special-case names, dates, files or phrases from these documents to make
   an insight appear. Fix problems generally, add or extend a test in `tests/`, and keep all tests passing.
3. **Personal data.** The documents contain a passport number, a date of birth and passwords. The GitHub repo
   must be private. Keep `EVIDENCE_MASK_UI=1`. Do not paste document contents into commit messages or issues.
4. Keep changes small and explain each one in a sentence when you report back. Prefer editing existing modules
   over adding new frameworks.
5. If an API behaves differently from what the code assumes, check the current docs (docs.claude.com,
   developers.llamaindex.ai) before changing code.

## Phases (do them in order; report at the end of each)

### Phase 0: environment
- Python 3.11+ venv, `pip install -r requirements.txt`. Check `pdftotext`/`pdffonts` (poppler) are installed;
  if not, install them (`brew install poppler` / `apt-get install poppler-utils`) or tell the user how.
- Check Docker is available (needed for Phase 5). If it isn't, say so and continue.

### Phase 1: offline tests (no keys, no cost)
```
python -m tests.test_unfamiliar_files
python -m tests.test_end_to_end raw_dump /tmp/case_e2e
EVIDENCE_DATA_DIR=/tmp/dd EVIDENCE_LLM=scripted EVIDENCE_SHOWCASE_DIR=/tmp/none python -m tests.test_deployment raw_dump
python -m tests.test_showcase raw_dump
```
All must print `OK`. If `raw_dump` differs from what the tests expect (e.g. the screenshot is missing), report it.

### Phase 2: real-API smoke tests (cents)
These parts were written against docs but never run with real keys. Verify each on the smallest input:
- **Anthropic client** (`reason/llm.py`): one extraction call on one small window (e.g. a single email). It uses
  Sonnet 5.5 rules: strict tool + `tool_choice: auto` (forced tool choice returns 400), `thinking:
  between_tools`, `output_config.effort: low`, passed via `extra_body`. If the API rejects a parameter, fix it
  per the current docs. Confirm `client.usage`/`cost_usd()` report sensible numbers.
- **LlamaParse** (`ingest/parsers/document_parser.py`, `_llamaparse`): parse the screenshot alone and one small
  PDF. The code assumes `client.parsing.parse(upload_file=…, tier=…, version=…, expand=["markdown","text","metadata"])`
  and response fields `res.markdown.pages[i].markdown`, `res.text.pages`, `res.metadata.pages[i].confidence`,
  `res.job.id`. Fix the mapping if the SDK differs. Check that the screenshot's markdown is a table that
  `reason.grid.is_time_grid` accepts; if LlamaParse returns a different table shape, make `grid.py` handle it
  generally (and add a fixture-based test).

### Phase 3: estimate, then the full run of the original dump
- Copy `raw_dump/` to `/tmp/orig_dump/` **without** the screenshot.
- Print the estimate: ingest, then `python -c` with `reason.costs.estimate(...)`. **Ask the user** to confirm.
- Run `python -m reason.run /tmp/orig_dump --case ./case <owner settings>`. Report the stats, actual cost,
  failed windows and dropped quotes.

### Phase 4: quality review with the user
- Show the user a sample of ~30 extracted events (title, status, time, quote, source) and all merge proposals.
  Flag events that look wrong (wrong status, wrong date, paraphrased quotes). If there are systematic errors,
  improve the extraction prompt/rules in `reason/extract.py` generally, re-run (the cache makes unchanged windows free;
  changed prompts cost again, so estimate first), and compare.
- Rehearse the live step on a **copy**: `cp -r case /tmp/rehearsal`, add the screenshot to
  `/tmp/rehearsal/uploads/` (create the folder and copy the original files there if the CLI case has none), run
  all layers, and confirm what the new document adds (new events, insights; the expected highlight is a plan on
  16 Sep contradicted by the 17 Sep chat). Report the verdicts and the cost of the live step.
- **Ask the user** whether to pre-warm the live step: copying the rehearsal's `llm_cache/` and
  `ingest/parse_cache/` entries into the showcase makes the live upload instant and immune to API hiccups, but
  the job log will then show everything as cached. Do whatever they choose.

### Phase 5: bake and test the container locally
- `python -m scripts.build_showcase ./case icip-trip --uploads /tmp/orig_dump <owner settings>`
- `docker build -f deploy/Dockerfile -t evidence-desk .` then run it with `-p 8501:8501`, `-e PORT=8501`,
  `-e EVIDENCE_PASSWORD=...`, the two keys, `-e EVIDENCE_DATA_DIR=/data`. Check `/_stcore/health`, log in, and
  confirm the showcase case appears with its insights. Upload the screenshot through the UI, process, and confirm
  the 🆕 markers. Watch memory (`docker stats`): Render free gives 512 MB; report peak usage.

### Phase 6: deploy (with the user)
- **Ask**, then create a **private** GitHub repo and push (`showcase/` included; `raw_dump/`, `case/`, `.env`, `data/` excluded).
- Render must be done by the user in the dashboard: New → Blueprint → this repo; set `EVIDENCE_PASSWORD`,
  `ANTHROPIC_API_KEY`, `LLAMA_CLOUD_API_KEY`. Give them these steps.
- When they share the URL, verify it with `curl` (health endpoint) and walk them through one full rehearsal.
  If memory is exceeded on Render, report it and propose the smallest fix.

## Known limits (don't try to fix unless asked)
Single-instance design (SQLite + local files); Render free resets data on restart (the showcase is re-seeded);
Telegram/iMessage/.msg/.pptx are reported as unsupported; chat timezone is a per-case setting.
