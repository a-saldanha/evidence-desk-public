# Two Takes on One Idea: EventGraph vs. Evidence Desk

A comparison of two projects that pursue the **same underlying idea** with very different execution.

- **Project A — "EventGraph"** · `zamp/knowledge-graph`
- **Project B — "Evidence Desk"** · `zamp_v2/evidence_ingest` (the `v2` is a strong hint: this is the later re-attempt)

---

## 1. The shared idea

Both projects solve the same problem:

> Take one person's **messy, multi-channel personal archive** (emails, WhatsApp/SMS, PDFs, scans, calendar exports) and reconstruct it into a **cited knowledge graph of events and people**, then let the user **query it** — with every fact grounded back to the exact source text.

Both agree on the same non-negotiables:
- The hard problem is **not retrieval** — it's **de-duplication, cross-source entity resolution, and relevance scoping**.
- **Determinism first, LLM second**: parse and dedup with plain code; use the LLM only where judgment is genuinely required.
- **Grounding by default**: no fact appears without a citation to a source span.
- **Cost-conscious LLM use**: cheap models, content-hash caching, dry-run estimates.
- **Single-event / single-person scope**, single-instance deployment, password-gated demo.

So the disagreement is entirely about **how to execute**, not **what to build**.

---

## 2. Side-by-side at a glance

| Dimension | **A · EventGraph** (`knowledge-graph`) | **B · Evidence Desk** (`evidence_ingest`) |
|---|---|---|
| **Shape** | Web app: FastAPI backend + Next.js frontend | Pipeline + Streamlit multipage app |
| **Headline feature** | Interactive **2D force-directed graph** UI | **Plan-vs-evidence reconciliation** ("you planned X, the chat shows Y") |
| **Pipeline framing** | ingest → dedup → resolve → graph → query | 4 explicit layers: ingest → extract → graph → reconcile/ask |
| **LLM models** | Haiku 4.5 (extract) + Sonnet 4.6 (resolve/query) | Sonnet 5.5 for everything, `effort: low` |
| **PDF/scan parsing** | LlamaParse (optional) | LlamaParse v2 with **cost-tier routing** (cheap for text PDFs, agentic for scans) |
| **Storage** | JSON snapshot (`bundle.json`), rebuilt each run | **SQLite** graph + content-addressed file store + JSON |
| **Entity resolution** | LLM resolver over candidate blocks + confidence tiers + review queue | Auto-merge **only** on shared identifiers; everything else → scored human-review proposals |
| **Quote grounding** | Verify every claim verbatim, drop if absent | Exact → normalized → **fuzzy ≥88%**, labels match type, keeps event as "ungrounded" |
| **Temporal handling** | dateutil + custom tz map | **Dedicated layer**: LLM reports raw phrase ("yesterday"), code resolves the date |
| **Cost control** | Dry-run estimate, per-chunk cache, session/daily caps | **Hard spend cap + atomic file-locked ledger** across all runs, pre-run estimator, prompt caching |
| **QC / data quality** | Dedup diagnostics, ingest report | **First-class QC layer**: ~15 issue types (missing threads, ambiguous dates, resend-vs-revision, sensitive-data masking…) |
| **Testing** | **124 pytest tests**, real client but cached | Offline end-to-end with a **scripted LLM double** + deployment smoke tests |
| **Deployment** | Docker → Vercel (frontend) + Railway (volume) | Docker + Caddy (VPS) **or** Render free-tier blueprint with baked **showcase** |
| **Visual output** | Graph canvas, 7 tabs | Plotly timeline (planned vs happened vs reported), insights, cited Q&A |

---

## 3. The major differences that matter

### 3.1 Architecture: web app vs. layered pipeline
**A** is built like a **product**: a FastAPI service with real endpoints (`/api/ask`, `/api/replay`, SSE job progress) and a separate React SPA that talks to it. Clean client/server split, proper web framework.

**B** is built like an **instrument**: four sharply-separated layers (`ingest/`, `reason/`, `app/`) where each layer is independently runnable from the CLI and the UI is a thin Streamlit shell over a background-job worker. The boundaries between *deterministic* and *stochastic* code are more rigorously enforced (e.g. temporal resolution is its own pure module; the LLM is forbidden from computing dates at all).

→ **A optimizes for interaction; B optimizes for correctness and reproducibility.**

### 3.2 The "reasoning" step is fundamentally different
This is the biggest conceptual divergence.

- **A stops at a queryable graph.** Its intelligence is *retrieval*: preset money/timeline/who queries plus free-form NL answered over a BFS neighborhood. It tells you *what's in the archive*.
- **B adds a reconciliation layer (Layer 4a)** that A simply doesn't have. For every *planned* event it deterministically pulls overlapping evidence **from other sources**, then asks an LLM judge: **confirmed / contradicted / modified / no-evidence**, with calibrated confidence. It tells you *where the plan and reality diverged* — the "you planned to meet on the 16th but the chat on the 17th says it was cancelled" insight.

→ **B does something with the graph that A doesn't attempt.** That reconciliation is the entire point of the "Evidence Desk" framing and is its strongest differentiator.

### 3.3 Storage & reproducibility
**A** keeps everything in a single `bundle.json` and rebuilds from scratch each run — simple, but no incremental state and it scales poorly. **B** uses **SQLite** for the graph plus a **content-addressed file store** and persisted **merge decisions** (`decisions.json`) that are re-applied on re-run, so human review survives rebuilds. B's model is more durable; A's is simpler.

### 3.4 Cost discipline
Both cache by content hash and estimate before spending. But **B goes markedly further**: a **hard dollar spend cap** enforced by a **file-locked ledger counted across every process and run**, so a runaway loop or a second concurrent job physically cannot overspend. A relies on softer session/daily limits. B also routes LlamaParse to a cheap tier for text-layer PDFs and reserves the expensive agentic tier for scans. **B treats credits like a safety-critical resource; A treats them responsibly but less defensively.**

### 3.5 UI & "demo-ability"
Here **A wins**. A force-directed, clickable entity graph with neighbor-highlighting is simply more impressive and more explorable than Streamlit tabs + a Plotly timeline. A's Next.js frontend is a nicer artifact to show off. B counters with a **baked "showcase" mode** for free ephemeral hosting (pre-warmed cache so a live upload is instant and demo-proof) — a more *operationally* clever demo story, but a less *visually* striking one.

### 3.6 Testing philosophy
**A** has more raw coverage — **124 tests** — running the real Anthropic client against cached responses. **B** has fewer tests but a cleaner isolation story: a **`ScriptedClient` double** means the entire pipeline (layers 1, 3, 4 fully real) runs offline with **zero keys and zero cost**, plus dedicated **deployment smoke tests** that exercise every page and the job lifecycle. A has breadth; B has a stricter offline guarantee.

### 3.7 Grounding rigor
Both verify quotes. **B is slightly more sophisticated**: a three-stage match (exact → normalized → fuzzy ≥88%) that *records how it matched* and downgrades an event to "ungrounded" rather than silently dropping it. **A is stricter/binary**: verbatim or dropped. B preserves more signal; A is more conservative.

---

## 4. Where each one is genuinely better

**EventGraph (A) is better at:**
- **Front-end & visualization** — the interactive graph is the standout; much better "wow" factor.
- **Web architecture** — a real API/SPA split that could grow into a multi-user product.
- **Test breadth** — 124 tests is a lot of surface area covered.
- **Simplicity of mental model** — one bundle, rebuild, done.

**Evidence Desk (B) is better at:**
- **The actual reasoning payoff** — plan-vs-evidence reconciliation is a capability A lacks entirely.
- **Correctness discipline** — stricter determinism boundaries, a dedicated temporal layer, durable merge decisions.
- **Cost safety** — a hard, cross-process spend cap is a meaningfully stronger guarantee.
- **Data quality** — QC is a first-class layer (~15 issue types) rather than a diagnostic afterthought.
- **Ingest robustness** — more parser edge cases (Exchange headers, DD/MM inference per file, LlamaParse tiering, sensitive-data masking).
- **Operational demo story** — baked showcase that survives API hiccups and ephemeral disks.

---

## 5. Verdict

**Evidence Desk (`evidence_ingest`, Project B) is the better-executed project for the stated problem** — and the `v2` lineage shows: it reads like the mature second attempt that internalized the lessons of the first.

It wins on the dimensions that define this problem's difficulty: it **does more with the graph** (reconciliation, not just retrieval), it is **more rigorous about determinism and grounding**, it has **first-class data-quality and cost-safety machinery**, and it has a **more durable storage/decision model**. Those are the hard, differentiating parts — and they're exactly where B invested.

**EventGraph is not the weaker idea — it's the stronger front-end.** If the goal were a polished, explorable, potentially multi-user web product, A's FastAPI + Next.js + force-directed-graph foundation is the better starting point, and its 124-test suite is reassuring. B's Streamlit UI is functional but plain, and its single-instance/ephemeral constraints are real.

**Bottom line:**
- Want the system that most convincingly **turns a messy archive into trustworthy, cited, plan-vs-reality insight**? → **Evidence Desk (B).**
- Want the system that is most immediately **impressive to demo and most extensible as a web product**? → **EventGraph (A).**

The strongest system would be **B's reasoning core and cost/QC discipline behind A's graph UI and API architecture** — the two projects are nearly complementary, which is unsurprising given one is the evolution of the other.

---

### One caveat on this assessment
This comparison is based on a structural read of both codebases (architecture, modules, tests, docs), **not** on running either system against identical input and scoring output quality. "Better execution" here means better engineering and a more complete answer to the problem; a head-to-head on *extraction accuracy* against the same archive would be the way to confirm it empirically.
