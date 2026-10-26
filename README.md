# Evidence ingestion — layer 1 (ingest, normalise, QC)

Turns a messy dump (Gmail Takeout `.mbox`, single `.eml` downloads, WhatsApp `_chat.txt`, PDFs, receipts,
tickets, calendar invites) into **citable units** plus a **quality report**, before any LLM extraction runs.

```
pip install -r requirements.txt
export LLAMA_CLOUD_API_KEY=llx-...            # omit (or pass --offline) to use PDF text layers only
python -m ingest.pipeline ./dump --out ./ingest_out \
       --chat-alias 'Jane=jane.smith@example.com' --chat-tz 5.5
```

## Design rules

1. **Files are identified by bytes, not names.** Everything is SHA-256 addressed and copied untouched into
   `raw/`. Identical files collapse into one source with aliases (the uploaded `visa_invitation.pdf` is the
   same file as the attachment inside the mbox). Type is sniffed from content, so a misleading filename
   doesn't matter.
2. **LLMs don't parse structure that already has a spec.** Email headers, MIME, threading IDs, chat
   timestamps and ICS are parsed with deterministic code. LlamaParse is used where layout matters: PDFs,
   scans, photos, receipts.
3. **Nothing is deleted, only separated.** Quotes, forwards, signatures and gateway banners are removed from
   the text the LLM sees, but each unit keeps `raw_text` and a `Locator` (file hash + message index /
   Message-ID / line range / page + char offsets). That locator is the citation.
4. **Uncertainty is recorded, never guessed away.** Every timestamp carries `exact | assumed_tz | ambiguous | none`;
   currencies keep the raw string (`$` is stored as `USD?`); quoted copies are marked `reconstructed_from_quote`.

## Parsers

| Input | How | Notable handling |
|---|---|---|
| `.mbox` / `.eml` | stdlib `email` (policy=default) | Gmail labels + `X-GM-THRID`; text/plain preferred, HTML fallback; reply/forward/Outlook header splitting; Exchange "external sender" banners stripped; website contact-form relays (`wordpress@…`) re-attributed to the real sender; attachments re-enter the pipeline as their own sources; inline calendar invites parsed |
| WhatsApp | regex, iOS and Android formats | DD/MM vs MM/DD inferred from the whole file; no timezone in exports so a configured offset is applied and flagged; multi-line messages; calls, deleted/edited, system lines, `… omitted` media placeholders |
| PDF / image | **LlamaParse v2** (`llama-cloud` SDK) + `pdftotext` witness | Tier routing (text-layer PDFs → `cost_effective`, scans/photos → `agentic`); results cached by hash+tier+version (re-runs are free); per-page confidence; page header/footer kept separately; page-level document-type hints and segmentation (one PDF can hold several documents) |

Use `--llamaparse-version 2026-xx-xx` to pin a dated tier version once you go beyond the demo, so parses stay reproducible.

## QC checks (`qc.py`)

Duplicates (identical files, same Message-ID in `.eml` and mbox, quoted/forwarded copies of emails we already
have) · missing thread parents and whether the quote preserves them · lost messages recovered from quotes,
clustered so one missing email quoted in four replies is reported once · attachments mentioned but absent ·
chat media exported as placeholders, deletions, long silences, assumed timezone, undecidable date order ·
parser health: LlamaParse page failures, low confidence, and **numeric cross-check against the PDF text layer**
(flags pages where amounts/codes were dropped) · mixed DD/MM and MM/DD in one file · ambiguous dates ·
multi-document PDFs · currency inventory and bare `$` · passwords/cards/keys to mask before data leaves the
machine (incl. passport numbers and dates of birth in form layouts) · identity merge candidates and Gmail dot-variants ·
**resent vs revised notices** (same sender + subject: identical text is a resend; changed `Field: value` lines
are reported as old → new and the older email is marked `superseded`) · **plans vs records** (itineraries with
"assumed / verify / forecast" wording are flagged `forward_looking`) · image-only pages that need OCR ·
document boundaries inside one PDF (type change or "1 / N" page-number restart) · WhatsApp attachments linked
to files in the dump (who shared what, when) and documents referenced in chats but missing · out-of-order chat
lines · greetings that don't fit the export clock (a hint the sender was in another timezone).

## Outputs

`sources.json`, `units.jsonl` (input to layer 2), `qc_issues.jsonl`, `qc_report.md`, `raw/`, `parse_cache/`.

Layer 2 should skip units flagged `duplicate` or `copy_of_existing` for extraction (they remain valid
alternate citations), treat `only_surviving_copy` units as lower-confidence evidence, and mask
`sensitive_spans` before calling external models.

## Known limits / next steps

Outlook `.msg` is detected but needs `extract-msg`; Android WhatsApp "with media" zips need unzip + linking of
`<attached: …>` files; image-only inputs need an API key; the document-type cues in `document_parser.py`
are a stopgap for an LLM classifier.

---

# Layers 2–4: extraction, knowledge graph, reasoning

```
export ANTHROPIC_API_KEY=...                       # LLAMA_CLOUD_API_KEY too, for screenshots/scans
python -m reason.run ./uploads --case ./case --self-name "Jane Smith" --self-alias "Jane" \
       --self-email jane.smith@example.com --self-email jane.smith@university.edu
python -m reason.run --case ./case --self-name "Jane Smith" --ask "Did I attend Dhanunjaya's presentation?"
streamlit run app/streamlit_app.py                 # the demo UI
```

| Layer | Module | LLM? | What it does |
|---|---|---|---|
| 2 | `reason/extract.py` | yes | Units → windows (one email / ~40 chat messages / one document segment) with `[m12]`/`[p2]` tags. A forced tool call returns events with a **status** (planned, scheduled, happened, cancelled, reported_state), participants, place, *what the text says about time*, and verbatim quotes. Sensitive spans are masked before sending. |
| 2 | `reason/temporal.py` | no | Resolves "yesterday", "on Saturday", "16-07-2026", "11Sep2026" against the timestamp of the unit the quote came from. The LLM never computes dates. |
| 2 | `reason/grid.py` | no | Time-grid itineraries (xlsx or a screenshot parsed to a markdown table) → planned events; consecutive cells merged, TIME GAIN/LOST treated as clock shifts, "?" = tentative, "X Sir / Y Ma'am" = participant. |
| 2 | grounding (`extract.ground`) | no | Every quote is re-found in the source (exact → normalised → fuzzy ≥ 88). Unfindable quotes are dropped. |
| 3 | `reason/entities.py` | no | Auto-merge only on shared identifiers / configured self aliases; everything else becomes a scored proposal for a human. Decisions persist in `decisions.json`. |
| 3 | `reason/store.py` | no | SQLite graph: entities, events, evidence spans, participants, merge proposals, insights + review status. |
| 4 | `reason/reconcile.py` | yes | For every planned/scheduled event: gather overlapping evidence from *other* sources about the same people, ask a narrow judge (confirmed / contradicted / modified), re-verify its quotes. |
| 4 | `reason/query.py` | yes | BM25 + date retrieval over events and units; answers may only cite shown blocks; citations re-verified. |

## Tested here (and what wasn't)

`python -m tests.test_end_to_end <uploads> <case>` runs everything on the real files with two clearly
simulated parts: the LlamaParse output for the itinerary screenshot (`tests/fixture_itinerary_grid.py`) and the
LLM (`ScriptedClient`). The real deterministic machinery then does the work: grid → 36 planned events, "yesterday"
in the 17 Sep 21:52 message → 16 Sep, candidate retrieval for "Dhanunjay Sir Presentation" (16 Sep 14:00) returns
the illness report and the missed meeting from `_chat_3.txt`, the citation is re-located at line 42, and the UI
renders the insight with highlighted quotes. Real model quality (recall of events, judge calibration) can only be
measured with an API key: run it, then review a sample of events and insights by hand.

## Demo script (≈6 minutes)

1. **Upload**: drop the case folder minus the itinerary screenshot (pre-processed, cached). Show layer 1's QC:
   duplicates, recovered quotes, mixed date formats, missing chat media, sensitive data.
2. **People**: accept "Dhanunjay ≟ Dhanunjaya", reject "Saranya ≟ Murali" (same institute, different people).
3. **Live**: upload the itinerary screenshot, run the layers; only the new file costs time.
4. **Timeline**: planned lane vs happened/reported lanes.
5. **Insights**: the Dhanunjaya contradiction with both sources highlighted; accept it.
6. **Ask**: "Did I attend Dhanunjaya's presentation?"; answer with a clickable citation.

## Costs

`python -c "from reason.costs import estimate; ..."` (also printed by `reason.run` before layer 2) estimates spend
from the real windows without calling the API; `AnthropicClient.cost_usd()` reports actual spend from usage.
For this case (1,214 units → 86 extraction windows, ≈200k input tokens) the estimate is ≈ $2 at low effort,
≈ $5 at high effort, about half with the Batch API for pre-processing. Output tokens (including thinking) are
5× the input price and dominate, so effort, lean JSON and short quotes are the main levers.
The client sends `thinking: between_tools` + `effort: low` for extraction, caches the system prompt and tool
schema, and uses strict tool use with `tool_choice: auto` (Sonnet 5.5 rejects forced tool choice).

---

# Demo deployment

One server (any small VM with Docker: 2 vCPU / 4 GB is plenty), two containers: the app and Caddy, which
provides HTTPS and the login. The app port is never exposed directly.

```
cp deploy/.env.example deploy/.env                    # API keys, domain, budget
docker run --rm caddy:2.8 caddy hash-password --plaintext 'long-password'   # once per user
cp deploy/users.caddy.example deploy/users.caddy      # paste "<user> <hash>" lines
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```
Point the domain's DNS at the server first; Caddy then gets a certificate automatically. For a local trial use
`DOMAIN=localhost`.

**What a user gets**: their own cases (data is scoped by the login, `DATA_DIR/users/<user>/cases/<case>`);
uploads of .eml/.mbox/.txt/.pdf/.png/.xlsx and WhatsApp *zip* exports (unpacked safely, media linked to chat
lines); "Process everything" runs as a background worker, so the page can be reloaded or closed; data
quality, people review, timeline, insight review, cited Q&A; settings; export of results; removal of single
files and permanent case deletion (uploads, parses, model cache, graph, logs).

**Guards**: per-user monthly LLM budget (a job whose estimate exceeds the remainder is refused; actual spend
from token usage is recorded in `ledger.json`); one job per case; upload count and zip-size limits; path
sanitising; a read-only showcase login (`EVIDENCE_DEMO_USER`) for reviewers.

**Before presenting**: seed the showcase case with `python -m scripts.seed_case` (see its docstring) so every
LLM answer is cached; during the demo upload one new file live.

**Smoke test without keys**: `EVIDENCE_LLM=scripted` swaps in the test double (a banner says so).
`EVIDENCE_DATA_DIR=/tmp/dd EVIDENCE_LLM=scripted python -m tests.test_deployment <uploads>` runs case creation,
a WhatsApp zip upload, the background job, every page, Accept, rebuild, export and deletion.

**Known limits of this setup** (fine for a demo, listed in the roadmap): single instance (SQLite + local
volume, so no horizontal scaling); a job interrupted by a container restart is marked failed and must be
re-run (cached calls make that cheap); basic-auth logins rather than SSO; data encrypted only if the server disk is.
Back up the `evidence-data` volume.

---

# Free showcase on Render (one-week demo)

Render's free tier has no persistent disk and sleeps after 15 idle minutes, so the processed original dump is
**baked into the Docker image** (`showcase/`) and copied into place at every start. Live uploads work and are
processed on top of it; they last until the next restart, and **Settings → Reset to the showcase state**
restores the original between rehearsals.

1. **Process the original dump locally with real keys** (`python -m reason.run …`, see Layers 2–4), review
   people and insights in the local UI, then bake it:
   `python -m scripts.build_showcase ./case icip-trip --self-name … --self-alias … --self-email …`
   (use exactly the same owner settings as for processing, otherwise the cache won't match).
2. **Push to a private GitHub repo** (the showcase contains your documents).
3. **Render → New → Blueprint →** pick the repo (`render.yaml`), set `EVIDENCE_PASSWORD`, `ANTHROPIC_API_KEY`,
   `LLAMA_CLOUD_API_KEY`. You get `https://evidence-desk.onrender.com` with HTTPS.
4. **Before presenting**: open the URL a couple of minutes early (cold start ≈ 1 min), log in, check Insights.

**During the demo**: show QC → People → Timeline → Insights for the original dump; upload one new document
(the itinerary screenshot or an email); "Process everything" only pays for the new content (the job log shows
"N windows already cached"); the success banner and 🆕 markers show exactly what the new document added.

**Limits to state**: data resets on restart/sleep; 512 MB RAM suits one or two small live uploads, not a new
full dump; the free instance is slow to wake. `tests/test_showcase.py` exercises the whole flow.
