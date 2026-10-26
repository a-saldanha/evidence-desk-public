# Evidence Desk → Startup Project Round: suggestions & plan

A read of the assignment against the project as it stands, and concrete ways to position and improve it to score well. **No code changes here — this is a plan.**

---

## 0. TL;DR

- **Pick Problem #1** ("turn messy documents into structured, queryable data"). Evidence Desk *is* this — but interpret it ambitiously: not just extraction, but **a cited timeline + cross-source reconciliation + grounded Q&A**. That interpretation is itself a product-thinking win the rubric explicitly rewards.
- **Your "above and beyond" is already built** — lean into it, don't add features. The hard sub-problems most people skip (cross-source dedup, entity resolution, **fuzzy quote grounding**, **relative-date resolution**, **plan-vs-evidence reconciliation**) are the depth they're looking for. The job is to make them *visible and legible*, not to build more.
- **Three things you must not skip:**
  1. **Privacy** — the current demo data derives from real personal documents (passport, DoB, passwords). Do **not** submit that in a repo evaluators read. Swap in a **synthetic demo corpus**. (§8)
  2. **`decisions.md`** — required, heavily weighted, and you have a goldmine of real decisions to document. (§6)
  3. **The "they'll test it live" problem** — evaluators get a URL and will upload *their own* messy files. Plan for cost, cold starts, and graceful failure on arbitrary input. (§7)

---

## 1. How Evidence Desk maps to the rubric

| Their criterion | Where you stand | The move |
|---|---|---|
| **Problem framing** | Strong — layered, scoped, deterministic-first | Make the interpretation explicit (§2). State what you cut and why. |
| **Product thinking** | Strong but *implicit* | Name the user and the job-to-be-done up front. "Reconstruct what actually happened, with receipts." |
| **UX decisions** | Functional, a bit bare | Fix first-run / empty states / error moments (§5). This is your biggest cheap win. |
| **Code quality** | Clean, well-separated layers | Already good. Add a short architecture note in the README. |
| **Tests** | Offline e2e + scripted LLM double + smoke tests | Good foundation; deepen on the *hard parts* (§4, §9). |
| **Documentation** | README + CLAUDE.md (internal) | Rewrite README for a stranger; add `decisions.md` (§6). |
| **Setup experience** | Docker + render.yaml + venv | Make it **one-shot**: `docker compose up` with a seeded demo and no keys required to *look* (§7). |
| **Velocity** | N/A (existing) | Frame the 5-day slice honestly in `decisions.md` (§6, §10). |
| **Above & beyond** | **This is your strength** | Own reconciliation + grounding; surface them loudly (§3). |

---

## 2. Problem framing — write this down (feeds `decisions.md` and README)

**The problem (one paragraph):** People accumulate a messy, multi-channel paper trail — emails in two formats, WhatsApp exports, PDFs, scanned receipts, screenshots. Reconstructing "what actually happened" means fighting duplicates, the same person under five names, relative dates ("yesterday"), and claims that contradict each other across sources. Evidence Desk turns that pile into a **cited timeline of events and people**, and flags where the **plan diverged from reality**.

**Who it's for:** anyone who needs to reconstruct a timeline from their own records with receipts — the immediate persona is a traveler/claimant/"what did I actually agree to" case, but the engine is general.

**What you deliberately left out (scope discipline — say this):** single-user, single-instance; no multi-tenant accounts; a fixed set of formats (email/chat/PDF/xlsx) with unsupported ones *reported, not silently dropped*; no generic NL-to-SQL — fixed, predictable retrieval instead.

> The assignment rewards *interpretation under ambiguity*. The strongest version of Problem #1 isn't "extract fields from a PDF" — it's "reconcile contradictory evidence across sources and cite every claim." That's the framing to lead with.

---

## 3. The "above and beyond" play — own the hard sub-problem

Pick **one** depth story and make it unmissable. You have two genuinely hard, already-solved candidates. I'd lead with **reconciliation**, backed by **grounding**.

### 3a. Plan-vs-evidence reconciliation (lead with this)
This is the thing *nobody else's #1 submission will have*. Most people stop at extraction. You take the extra, hard step: for each planned event, deterministically retrieve overlapping evidence from *other* sources, then judge confirmed / contradicted / modified with calibrated confidence and cited quotes.

**How to surface it (so an evaluator *gets it* in 30 seconds):**
- Put a **"Contradictions found"** number on the landing/overview, not buried in a tab.
- The hero demo moment: *"You planned a meeting on 16 Sep; the chat on 17 Sep shows it was cancelled."* Make that the first thing they see.
- In `decisions.md`, write the **candidate-retrieval** design (time overlap ±4h for tz, shared participants, top-k) and *why it's deterministic* (so the LLM can't cherry-pick). That paragraph alone demonstrates depth.

### 3b. Quote grounding (the "real world, not happy path" story)
Every extracted claim is re-verified against the source: exact → normalized → fuzzy ≥88%, and if it still fails the event is kept but marked **ungrounded** rather than silently trusted. This is exactly the "handle bad data / degrade gracefully" the rubric lists.

**Surface it:** show a small "grounding" indicator per fact (exact / fuzzy / ungrounded), and a QC stat like "344 of 347 facts grounded; 3 dropped." That honesty *is* the above-and-beyond signal.

### Don't dilute
Do **not** add the entity-relationship graph, more pages, or a nicer theme. The rubric explicitly says breadth and polish are *not* what they want. Depth on one hard thing beats ten shallow things.

---

## 4. Handling the real world (directly targets their "above and beyond" bullets)

You already have a lot of this — the win is to **make it visible** and close a few gaps. Map each to their own words:

| Their "real world" bullet | What you have | Gap to close |
|---|---|---|
| Malformed / partial documents | QC layer, `test_unfamiliar_files`, unsupported-format *reporting* | Make the **QC report a first-class, legible page** — "here's what was wrong with your data." Evaluators uploading junk will see it handle junk. |
| Rate limits / timeouts | Client retries transient errors; cache resumes interrupted runs | Add a visible "degraded" state: if the API is slow/over budget, say so clearly instead of spinning. |
| Concurrent users | Background job worker; per-user data dirs | Document the single-instance limit honestly in `decisions.md`; don't pretend it scales. |
| Bad data degrades gracefully | Grounding drops bad quotes; dedup; tz assumptions flagged | Surface the **assumptions** (assumed timezone, ambiguous date) as visible warnings, not silent. |
| Sensible errors / observability | Job logs, spend ledger | Add the **live spend meter** (already built) + a run log an evaluator can read. Observability = trust. |

**The framing to use:** "I didn't build the happy path — I built the QC layer that tells you *why* your messy data is messy, and grounded every claim so you can trust or distrust each one individually." That's a sentence that wins this criterion.

---

## 5. End-to-end journey & UX (your biggest cheap win)

The rubric calls out the **first-run experience, empty state, and the moment something goes wrong** explicitly. This is where a functional-but-bare Streamlit app loses points. Concrete, low-effort fixes:

1. **First run / empty state.** A brand-new case is intimidating. Add: a one-line "what is this," a **"Try the sample case"** button (uses the baked demo), and a dead-simple "drop files here → press Process" with an example of what to drop.
2. **The "it's working" moment.** During processing, the job panel should narrate *plainly* ("Reading 35 files… extracting events… checking plans against evidence"). You have the job log — make its headline human.
3. **The "something went wrong" moment.** If a file can't be parsed, if the budget is hit, if the API 429s — say it in one plain sentence with a next step, not a stack trace. (Audit each `st.error`.)
4. **The payoff.** After processing, land them on the **Insights** (contradictions) and **Timeline**, not the file list. Lead with the "aha," not the plumbing.
5. **Small touches that make someone smile.** The 🆕 markers on new events, the "N of M facts grounded" stat, the contradiction count. You have these — put them where they'll be seen.
6. **Masking as a feature, not a footnote.** "We saw a passport number and a password in your files and masked them" is a *delightful* trust moment. Surface it.

> UX here isn't visual polish — it's "did you think about the person." A sample button + a human empty state + plain error messages will move this score more than any amount of CSS.

---

## 6. `decisions.md` (required, high-signal — don't phone this in)

This file is where your judgment shows. You have an unusually rich set of **real** decisions with real tradeoffs. Document these, each as *decision / alternatives / reasoning / what you cut*:

1. **Deterministic-first, LLM-second.** Parse, dedup, temporal, grounding, candidate retrieval are all code; the LLM only extracts and judges. *Alternative:* end-to-end LLM. *Why:* reproducibility, testability, cost, and you can unit-test the hard parts without the API.
2. **SQLite + local files over a database/vector store.** *Alternative:* Postgres + a vector DB. *Why:* query patterns are relational + full-text, not semantic search; one datastore for a time-boxed build; the corpus is small. (This is almost exactly their example of a *good* decisions entry.)
3. **Streamlit over a Next.js/React frontend.** *Alternative:* separate SPA + API. *Why:* one app, one deploy, one auth; ships the whole pipeline in a single container; the UX cost is acceptable for the scope. *Cut:* a rich interactive graph frontend (prototyped, judged not worth the second-stack tax).
4. **Fixed retrieval over NL-to-SQL/Cypher.** *Why:* predictable, honest about limits, no hallucinated queries.
5. **Quote grounding thresholds (exact→normalized→fuzzy ≥88%).** *Why:* verify every claim; keep-but-flag rather than drop, so nothing is silently trusted *or* silently lost. Document *why 88%*.
6. **Relative dates resolved in code, never by the LLM.** *Why:* date arithmetic is deterministic; the model only reports the raw phrase. Removes a whole class of hallucinated dates.
7. **Reconciliation candidate retrieval is deterministic** (time overlap + shared people + top-k), the LLM only judges. *Why:* prevents cherry-picking; makes the judgment auditable.
8. **Cost discipline as a design constraint:** hard spend cap + file-locked ledger, content-addressed response cache, prompt caching. *Why:* credits are real money; an interrupted run resumes free. *Tradeoff:* prompts must stay byte-identical.
9. **Auto-merge only on hard identifiers; everything else is human-reviewed.** *Why:* false merges are worse than misses; conservative ER with a review queue.
10. **Privacy: UI masking + no raw docs in the repo.** *Why:* real documents contain sensitive data; masking is on by default.
11. **Single-instance, ephemeral-on-free-tier.** *Why:* right-sized for the scope; the showcase re-seeds on restart. *Cut:* multi-tenant, horizontal scale.

Also include the **self-chosen-problem brief** style intro even for #1: the problem, **the hard part** (reconciling contradictory cross-source claims with citations), **the slice** you ship, and the one failure mode you handle (ungrounded/malformed input).

> Be honest about what existed before vs. what you did in the window. The rubric values judgment and honesty; a candid "here's the slice I hardened and why" reads better than pretending it was all five days.

---

## 7. "They will test it live" — the deployment plan

Evaluators get a URL and will upload *arbitrary* files. Plan for it:

1. **Seed a sample case** so the app is impressive with **zero upload and zero API key** — they can see the full thing (timeline, contradictions, cited Q&A) immediately. You already have the showcase mechanism; make the sample the landing experience.
2. **Let them upload without burning you.** Real uploads cost real API money. Options: (a) a sensible **spend cap** on the deployed instance so a flood of uploads can't drain credits; (b) a clear note that uploads are processed live; (c) consider a **"scripted/demo mode"** fallback banner if the budget is exhausted, so the app still *demonstrates* rather than erroring.
3. **Cold start.** Render free spins down after idle → slow first load and data reset. Either accept it (note it), add a cheap keep-alive ping, or use a minimal paid tier for the eval window. At minimum, the showcase re-seeds so it's never empty.
4. **Robust upload path.** This is where "handles the real world" is tested for real. Make sure arbitrary/huge/corrupt/empty files produce a clean QC message, not a 500. (Your QC + unfamiliar-files test already cover a lot — verify on a few nasty inputs.)
5. **Keys + budget on the deployed env.** For uploads to actually process, the deployed instance needs the API keys and a non-zero budget set as env vars.

---

## 8. Privacy / demo-data hygiene (must-do before submitting a repo)

The current case is built from your **real** ICIP trip documents, which contain a **passport number, DoB, and passwords**. For a submission evaluators will read and run:

- **Do not commit** `raw_dump/`, `case/`, `.env`, `data/`, or a showcase baked from personal data.
- **Build a synthetic demo corpus** — a small, fictional but *realistically messy* set (a couple of emails in two formats, a WhatsApp export with a relative date, a PDF receipt, an itinerary screenshot, one duplicate, one contradiction). This is better anyway: it's shareable, relatable, and lets you *design* the demo to show off dedup + a contradiction + grounding on purpose.
- If you keep any real derived data, **keep the repo private** and invite the evaluators — but a synthetic corpus is the cleaner call and removes the risk entirely.
- Bonus: a synthetic corpus with a *known* planted contradiction makes a crisp test fixture *and* a crisp demo. Two birds.

> This is also a **product/judgment** signal: "I built a synthetic demo set so the tool can be evaluated without exposing real PII" is exactly the kind of range they say they're looking for.

---

## 9. Tests — deepen on the hard parts (not coverage theater)

They say "tests that catch real problems, not token coverage." You have the right skeleton (offline e2e, scripted LLM, smoke tests). Add targeted tests on the sub-problems that are *actually hard*:

- **Grounding:** a fact whose quote was lightly paraphrased → asserts it's downgraded to fuzzy/ungrounded, not accepted verbatim.
- **Temporal:** "yesterday" at 17 Sep 21:52 → resolves to 16 Sep; DD/MM vs MM/DD ambiguity → flagged.
- **Dedup:** same email in `.eml` and `.mbox` → one canonical with aliases.
- **Reconciliation:** a plan + a contradicting evidence event in another source → verdict = contradicted with the right cited quote.
- **Robustness:** corrupt/empty/unsupported file → clean QC issue, pipeline continues (you have `test_unfamiliar_files` — make sure it asserts *graceful* behavior, not just no-crash).
- **The planted-contradiction fixture** from §8 doubles as an end-to-end "golden" test.

Each of these maps to a sentence in `decisions.md`: "I tested the failure, not the happy path."

---

## 10. Suggested priority order (highest score-per-hour first)

1. **`decisions.md`** — required, high weight, and mostly *writing down* what you already decided (§6). Do this first; it also clarifies your framing.
2. **Synthetic demo corpus + reseed the showcase from it** (§8). Unblocks a public repo and makes the demo crisp. Privacy-critical.
3. **First-run UX: sample button, human empty state, land on Insights** (§5). Biggest UX score-per-hour.
4. **README rewrite for a stranger** + one-shot setup (§1, §7). Setup-experience points.
5. **Surface the depth:** contradiction count + grounding stat on the landing/overview (§3). Makes "above and beyond" legible.
6. **Harden the live-upload path & error messages** on 3–4 nasty inputs (§4, §7).
7. **Add the targeted hard-part tests** (§9).
8. **Deployment dry-run as an evaluator** — fresh browser, upload a junk file, confirm it degrades gracefully and the sample case shines (§7).

Everything above is polish/hardening/surfacing of existing work — no new subsystems.

---

## 11. Things to deliberately CUT (and say so in `decisions.md`)

Cutting well is a scored signal. Explicitly leave out:
- The entity-relationship graph (you already decided this — too much info for the page; note it).
- Multi-tenant accounts / horizontal scale.
- Generic NL-to-SQL querying.
- More file formats beyond the core set (report unsupported ones, don't half-support them).
- Visual theming / extra pages.

Each cut, with a one-line "why it was right to leave out for now," demonstrates scoping judgment — which they weight as highly as what you built.

---

## 12. The one-sentence pitch to open your README/decisions with

> *"Most 'structured data from documents' tools stop at extraction; the hard part is that real records are duplicated, contradictory, and written in relative time — so Evidence Desk reconstructs a **cited** timeline across sources and tells you exactly where the plan and reality diverged, with every claim verifiable against its source span."*

That sentence frames the problem, names the hard part, and signals depth — which is the whole game here.
