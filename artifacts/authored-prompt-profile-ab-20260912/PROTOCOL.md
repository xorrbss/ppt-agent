# Author-prompt profile A/B: `current` vs `august-0819` — protocol

- Date: 2026-09-12. Prepared offline; **nothing here has been run.** No code under
  `servers/` changes. Execution waits for the coordinator's signal (stack restart after the
  E-auth fallback, then after the enforce series).
- What is compared: the authoring prompt profile introduced by `f42a45356`
  (`docs/authored-author-prompt-profiles-design-20260912.md`), at a fixed model.
- Why this shape: the 2026-09-06 blind rounds (`prompt-bundle` A/B, 13-7 then 12-3 against
  the bundle) established the rubric, the fixture and the harness; this protocol reuses all
  three so the result is comparable with that record.

## 1. Target

| item | value | source |
|---|---|---|
| path | non-verbatim, ordinary authored generation (prompt in, no source document) | design §1 |
| fixture | `fixture-24.json` in this directory: 24 slides, Korean, `hyundai-culture-center` style, verbosity `standard`, TOC + title slide, `vision_qa: false`, export PPTX | copy of the 2026-09-05/06 series fixture `fixture-aug24.json`, the one both blind rounds used |
| decks per arm | 3 | orchestrator instruction |
| slides rated | 24 × 3 = 72 pairs per rater | — |
| body-slide subset for density medians | exclude indexes 1, 2, 5, 9, 13, 16, 19 (TOC and chapter covers) | series page convention |

The fixture is a prompt, not a document, so every deck is a fresh authoring; deck-to-deck
variance is real and is why there are three per arm rather than one.

## 2. Arms

| arm | `AUTHORED_AUTHOR_PROMPT_PROFILE` | `AUTHORED_AUTHOR_PROMPT_MODEL` | `AUTHORED_STYLE_ANCHOR_ENABLED` | everything else |
|---|---|---|---|---|
| **A `current`** | `current` | `gpt-5.6-sol` | `true` (default) | registry defaults |
| **B `august-0819`** | `august-0819` | `gpt-5.6-sol` | **`false`** (design §5.3: the anchor gate must not judge slides against a contract they were never handed) | registry defaults |

Both arms pin the same model, so the only thing that moves is the prompt text and order.
Both arms leave `AUTHORED_PROMPT_BUNDLE_ENABLED`, `AUTHORED_EXEMPLAR_ENABLED`,
`AUTHORED_COMPOSITION_ENVELOPE_*`, `AUTHORED_INFOGRAPHIC_PLAN_ENABLED` and
`AUTHORED_VISUAL_STORY_PLAN_ENABLED` at their defaults (all off).

**Known confound, stated up front.** Arm A keeps the style anchor on because that is
production; arm B turns it off because the design requires it. So A/B measures "today's
pipeline" against "the August prompt without the anchor", not the prompt alone. The anchor
adds up to 6 400 prompt characters and one extra author call per deck. If the coordinator
wants the prompt isolated, add arm **A′** (`current`, anchor `false`) as a third arm; the
protocol below runs unchanged with three arms, at +3 decks. The decision rule in §6 is
written for the two-arm comparison the orchestrator asked for.

**After the series**, restore the defaults: `{"AUTHORED_AUTHOR_PROMPT_PROFILE": "current",
"AUTHORED_AUTHOR_PROMPT_MODEL": null, "AUTHORED_STYLE_ANCHOR_ENABLED": true}` and confirm with
a `GET`. `august-0819` is an experiment profile, never a default (design §5.3).

## 3. Setting the knobs — the sanctioned path

From `artifacts/authored-live-series-20260915/RUNBOOK.md` §2, verbatim in substance: these are
DB-backed runtime settings, written **only** through the admin settings endpoint
(`servers/fastapi/api/v1/admin/router.py`), which merges a partial JSON body under an
optimistic-concurrency revision and publishes the committed snapshot; other processes
converge through the revision-poll monitor (`docs/runtime-settings-multiworker-sync.md`).
No restart is needed for a knob to take effect, and this series does **not** restart the
stack — the stack is owned by `scripts/start-local-stack.ps1` and is mid-series for others.

```
base = http://localhost:5000            # local single-origin proxy; DISABLE_AUTH=true locally
GET  {base}/api/v1/admin/provider-settings            # read, redacted; also confirms CAN_CHANGE_KEYS != "false"
PUT  {base}/api/v1/admin/provider-settings            # partial body, merged
     body A: {"AUTHORED_AUTHOR_PROMPT_PROFILE": "current",
              "AUTHORED_AUTHOR_PROMPT_MODEL": "gpt-5.6-sol",
              "AUTHORED_STYLE_ANCHOR_ENABLED": true}
     body B: {"AUTHORED_AUTHOR_PROMPT_PROFILE": "august-0819",
              "AUTHORED_AUTHOR_PROMPT_MODEL": "gpt-5.6-sol",
              "AUTHORED_STYLE_ANCHOR_ENABLED": false}
GET  {base}/api/v1/admin/provider-settings            # confirm; save the body as provenance
```

Rules: restate the full arm row on every `PUT` rather than relying on merge to carry values;
save every `GET` response (redacted, non-secret) as `<deck-dir>/00-provider-settings.json`;
if the endpoint returns 403 (`CAN_CHANGE_KEYS` false) stop and escalate, do not write the DB.
The registry validates the enum, so a misspelled profile fails at the `PUT`, not silently.

## 4. Running a deck

`POST {base}/api/v1/ppt/presentation/generate/async` with `fixture-24.json` as the body
(the series' `run_fixture.py` does exactly this and polls
`GET {base}/api/v1/async-tasks/status/{task_id}`; a copy is not needed — `curl` and the
status endpoint suffice). Per deck, save:

| file | content |
|---|---|
| `00-provider-settings.json` | the `GET` after the `PUT`, before the request |
| `01-request.json` | the exact body sent |
| `02-accepted.json` | the async acceptance (task id, presentation id) |
| `03-final-status.json` | the terminal status; carries `data.quality_gate_receipt` with `flaggedSlides`, `reviewPages`, `residualIssues`, `qualityPass` |
| `slides/sNN.png`, `slides/sNN.metrics.json` | rendered from stored `slides.html_content` by `render_live_deck.py` (read-only, Chromium only) |
| `metrics.json` | `deck_metrics.py` output (§5.2) |

**Order: interleaved, A B A B A B**, one deck at a time, knob `PUT` + `GET` before each.
Interleaving costs nothing (no restart) and guards against provider drift over the hour or
two the series takes; running sequentially would let a slow afternoon land entirely on one
arm. Decks run one at a time so `provider_usage_receipts` and the attempt-timing receipt for
each job are not contaminated by a concurrent deck's calls.

Naming: `decks/A1`, `decks/B1`, `decks/A2`, … in this directory; the mapping deck → arm is
written to `decks/manifest.json` and is what the rating page's key is built from.

## 5. Evaluation

### 5.1 Blind paired rating (primary)

Same rubric and harness as 2026-09-06 (`rating/template.html`, copied from that session):

- Pairs: slide *i* of deck A*k* against slide *i* of deck B*k*, for k = 1..3, i = 0..23 →
  72 pairs. Sides randomised per pair; the key is hidden until each pair is rated; keys
  `1` / `2` / `3` = left / same / right.
- Question, unchanged from 9/6: *which slide would you rather ship as this page of this
  proposal?* One rater (the owner), optionally a second for agreement.
- Images: both decks' PNGs embedded as data URIs (`rating/images.json` → page build), so
  each page is self-contained. The 9/6 page carried one 24-pair deck pair at 6.2 MB; three
  pairs in one page would exceed the 16 MB artifact limit, so build **one page per deck
  pair** (`rating/pair-1.html`, `pair-2.html`, `pair-3.html`), rated in a fixed order, with
  the sides re-randomised per page.
- **Persistence rule** (lesson from 9/6, whose per-slide verdicts were lost when the page
  was deleted): the page stores each verdict in the artifact db **and** the rater exports
  the full table as JSON before closing; that file is committed as
  `rating/ratings-<rater>-<date>.json`. No result is reported from the page alone.

### 5.2 Automatic metrics (secondary), per deck and per arm

Computed by `deck_metrics.py` from the final status, the rendered metrics and the receipts:

| metric | definition | source |
|---|---|---|
| review pages | `len(review_pages_for(classify_flagged_slides(flaggedSlides, residualIssues)))` — the reviewer-facing count, not the raw flag count | `services/authored_flag_classification.py`; `03-final-status.json` |
| raw flags / residual issues / qualityPass | as recorded | same |
| visible characters per body slide (median) | slide HTML with `<style>`/`<script>` bodies removed, whitespace collapsed | `deck_metrics.visible_chars` (the 2026-09-07 correction: tag-stripping alone counts CSS as text) |
| text leaves per body slide (median) | elements whose `owned_text` is non-empty in the DOM metrics | `slides/sNN.metrics.json` |
| minimum and median declared type size (px) | `computed_style.fontSize` over text-owning elements | same |
| blocking defects on the published bytes | `blocking_safety_issues(inspect_authored_dom(html, metrics))` categories | the gate the replay used |
| author calls, retries, failed calls, input/output/cached tokens, latency p50/p90, per stage | one row per logical call in `provider_usage_receipts` (`job_id` = `presentation_generation_jobs.id`, resolved from the status task id) | read-only DB query in `deck_metrics.py` |
| deck wall | terminal status `updated_at − created_at`, plus the `[authored] done … total=Xs` log line when the log is at hand | `03-final-status.json`, `app_data/logs/backend.log*` |
| attempt-timing stages | **not available on this path**: the `job-*-attempt-timing.json` files belong to the workspace lane (0 of 171 matched a generation job on 2026-09-12); read only if one happens to exist | `app_data/authored-quality-receipts/` |

Medians over body slides exclude the TOC and chapter-cover indexes listed in §1.

### 5.3 What is reported

One table per arm (3 decks, mean and range), the paired-rating tally, and the sign test.
Per-deck files stay in `decks/`; the summary goes to `RESULT.md` beside this file.

## 6. Decision rule, fixed before running

- Primary: sign test on non-tie pairs, one-sided, α = 0.05, as on 9/6. Ties are reported,
  not counted. With 72 pairs and, say, 20 ties, 33-19 is the rough threshold.
- Secondary gate: an arm that wins the rating but publishes **more review pages** per deck
  (mean over 3) than the other is reported as "preferred, at a review cost", not as the
  winner; the owner decides.
- Any deck that fails to publish (terminal error) is rerun once with the same knobs; a
  second failure is a result for that arm, recorded, and the pair count shrinks.
- The token/latency table is informational: `august-0819` is expected to lose prompt
  caching by design (design §6), and that is priced, not judged.

## 7. Estimate

From the 2026-09-08 measurements (`gpt-5.6-sol`, concurrency 8): ~143 s per author call,
~26 s wall per slide, repair adding roughly a third on a 20-slide deck (run 52: 1 766 s
with repair, 484 s of it repair).

| step | per deck | series |
|---|---|---|
| knob `PUT` + `GET` | < 1 min | 6 min |
| generation, 24 slides incl. repair and export | 25-35 min | 6 decks, sequential: **2.5-3.5 h** |
| PNG render + metrics (read-only) | 2-3 min | 15-20 min, can trail the runs |
| rating page build | — | 10 min |
| blind rating, 72 pairs | — | 30-45 min of the owner's time |
| summary | — | 30 min |

Arm B may be slower per call (no cacheable prefix) and has one fewer call per deck (no
anchor); the estimate absorbs both. Provider spend: ~6 × 24 author calls plus repairs,
≈ 200-250 calls.

## 8. Order of work

1. Coordinator signal received; confirm the stack is idle for this lane (no other
   generation in flight — the receipts must not interleave).
2. `GET` provider settings → save as `decks/00-pre-series-settings.json`.
3. For k in 1..3: `PUT` A → `GET` → run A*k* → `PUT` B → `GET` → run B*k*.
4. Restore defaults → `GET` → save as `decks/99-post-series-settings.json`.
5. `render_live_deck.py` and `deck_metrics.py` per deck (read-only).
6. Build `rating/index.html` from the template with the six decks' PNGs; publish; the owner
   rates; export `rating/ratings-*.json`.
7. `RESULT.md` with §5.3; commit; SHA to the coordinator.

Steps 2-4 spend provider quota and write runtime settings: coordinator-authorised only.
Steps 5-7 are read-only and this lane can do them unprompted once the decks exist.
