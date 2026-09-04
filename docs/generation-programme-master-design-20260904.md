# Generation programme: master design (2026-09-04)

Status: **for review by the product owner, who is also the sole blind rater
(D12). No development starts until this document is accepted.**

This document supersedes the *product* direction of
`docs/generation-quality-root-cause-and-composer-architecture-20260904.md`
sections 6 (phases 2 and 3) and restates the programme around three decisions
taken on 2026-09-04:

1. **Quality first, speed second.** "Quality" means the PPTX the user opens:
   the authored engine's output with the infographic library is the bar, and
   the bar is to be held or raised, never traded for speed.
2. **Development runs on the Codex personal token; operations run on an
   organisation API key.** The provider is a per-environment setting, and
   nothing in the pipeline may assume one or the other.
3. **The authored engine stays the designer for ordinary generation.** The
   Composer stays for verbatim restyle, where the authored engine has never
   produced a deck. The Composer's fit machinery is reused *inside* the
   authored path to replace prose repair.

Everything below is grounded in evidence gathered on 2026-09-04 and cited
inline; items that could not be verified in this session are marked
**[verify]** and listed again in section 10 as pre-development checks.

---

## 1. Evidence baseline

### 1.1 The authored engine is good and not slow

| Measurement | Value | Source |
|---|---|---|
| Authored + infographic library, sync API, 10 slides, `default` style | **144.6 s wall**; author 619 thread-s; render 11.3 s; vision QA off; output is designed (cycle diagram with icons, 2×2 matrix with sidebar — viewed) | `artifacts/infographic-fixture-run-20260903/F1-stage12/{result.json,backend-lines.log,slide-05.png,slide-07.png}` |
| Authored, 51+ slide decks, Aug 2026, 60 decks | median **14.5 s/slide** (~248 slides/h); authoring 95 % of wall; effective parallelism ~7.2 | `app_data/logs/backend.log*` `[authored] done` lines (memory `generation-timing-evidence-sources`) |
| Reference-grade authored deck | 10 slides, bespoke figures per slide (org chart, five-column process, KPI callout, isometric scene) | `output/hyundai-reference-90plus-20260901/candidate-montage.png` (viewed) |

### 1.2 The worker lane — the path the UI uses — fails nine times in ten

157 attempt receipts, `app_data/authored-quality-receipts/job-*-attempt-timing.json`
(schema `receipts.{id}.{outcome,terminalError,stages,providerUsage,wallMillis}`):

| Outcome | Count |
|---|---|
| succeeded | **16** |
| failed | 134 |
| cancelled | 7 |

Terminal errors, grouped by cause:

| Group | Codes (count) | Cause |
|---|---|---|
| Verbatim restyle route | `source_content_browser_readback_failed` 19, `source_native_browser_quality_failed` 6, `source_native_document_quality_failed` 4, `authored_source_verbatim_capacity_exceeded` 4, `authored_source_image_visibility_mismatch` 3, `source_native_composition_unavailable` 3 — **44 failed, 0 succeeded** | the model rewrites source text; `services/source_content/pipeline.py:170-217` turns a restyle into an ordinary authored request with `source_mode=None` |
| Authored gates | `authored_composition_safety_gate_failed` 9, `authored_slide_capacity_exceeded` 4, `authored_vqa_residual_dom_issues` 3 — 16 | a measured layout defect is sent back to the model as prose; the LLM repair round succeeded 3 times in 24 |
| **Renderer admission** | `css_property_is_not_allowed__break-inside__avoid` 6, `html_attribute_is_not_allowed__hidden` 5 — **11** | one forbidden CSS property or attribute fails the whole deck |
| Provider / infrastructure | `authored_slide_author_failed` 9, `presentation_generation_failed` 7, `http_error_500` 4, `authored_manifest_publication_failed` 3 — 23 | personal-token 429s; in-process workers; SQLite locks |

Stage time (worker lane): provider p50 **418 s**, authoring p50 76 s,
chromiumReview p50 67 s, outline p50 13 s, publication p50 5 s. Wall p90 802 s.

### 1.3 Where the worker lane's time goes

`services/authored_batch_policy.py` — hard-coded constants, no environment knob:

```python
MAX_SLIDES_PER_AUTHOR_CALL = 3
INITIAL_WAVE_CONCURRENCY = 1
STEADY_WAVE_CONCURRENCY = 2
```

`advance_authored_batch_policy` sets `permanently_serial=True` on the first
500/503/timeout (`is_authored_provider_overload`) and **never restores** for
the rest of the job. A 20-slide deck is 7 calls in ~4 waves at 170–240 s per
call: ~13 minutes. The same 20 slides on the sync path, per-slide with
provider concurrency 5–8 (`AUTHORED_AUTHOR_CONCURRENCY`, process-wide
ThreadPoolExecutor `_AUTHOR_EXECUTOR` in `utils/llm_calls/author_slide.py:163`;
cap 5..8, default 5, `services/authored_generation_performance.py:9-11`), take ~3 minutes. The policy was added
(commit `ec66b4e9c`) to tame the personal token's 429s, not for speed.

Provider usage over 648 calls: **9.4k in / 3.2k out tokens per call**, p50
latency **81–88 s per call**, retry rate **15 %** (96/648). A successful deck
(n=4) uses ~7.5 calls, ~40k in / ~17k out; failed decks use *more* (p50 14
calls). Success rate 10 % therefore means ~90 % of spend is discarded,
whichever billing model is in force.

### 1.4 What already exists and is switched off

- **Best-of-N CSS candidates** (programme stage 4): implemented —
  `services/authored_quality_loop.py`, `authored_quality_loop_stage.py`, wired at
  `services/authored_presentation_pipeline.py:1147` — **off by default**
  (`AUTHORED_QUALITY_LOOP_ENABLED`; `AUTHORED_QUALITY_LOOP_CANDIDATES` default 3).
  The 2026-09-03 memory recorded stage 4 as "not started"; that is stale.
- **Deterministic repair**: `_deterministic_repair_candidate`
  (`services/authored_presentation_generation.py:755`) already composes text
  paint order, off-canvas width, blank-field clipping, owner-text capacity and
  closing capacity into one pass with one recapture — before the LLM repair
  cycle (`max_repair_cycles` from `services/authored_composition_policy.py`).
- **Composer** (`services/composer/`, phase 0): a working fit ladder with
  estimate-first, Chromium confirmation, and a terminal rung. Verbatim
  43/43 slides published, 0 provider calls, 223 s — but visually below the
  source deck (section 1.5).

### 1.5 The Composer's verbatim output is below parity

Blind rating pack `artifacts/composer-blind-rating-20260904/` is built
(A = floor, B = source deck, C = Composer r12-43, D = Hyundai target; 43/43/43/10
PNGs) but **no rating has been done** — `sheets/rating_sheet_main.csv` is
empty. The only number on disk is the pixel prefilter (92.67), which its own
JSON marks `"gate": false … must not be treated as one`.

Viewed directly (page 6, page 11, page 13): C is clearly above A (real title
band, sans type, footer band) and clearly below B (card grid lost to three
text columns; numbers `02`/`03` orphaned at the bottom left; running header
inside the body; chapter divider not recognised, half the page empty; rung-5
page with title and footer overlapping). Three of those four defects are
plan defects — grouping and role — that no figure library fixes.

### 1.6 Provider facts

- Codex OAuth: token carries `CODEX_TOKEN_EXPIRES`; refresh exists only as
  `POST /api/v1/ppt/codex/auth/refresh` (`api/v1/ppt/endpoints/codex_auth.py:252`);
  `utils/llm_config._get_codex_access_token` performs **no automatic refresh**.
  Reasoning-effort is applied for codex only (`llm_config.py:416-423`).
- OpenAI API: `LLMProvider.OPENAI` is a config-only path
  (`llm_config.py:147-156`, `OPENAI_API_KEY`, `OPENAI_MODEL`,
  `api_type=COMPLETIONS`). `.env` `CODEX_API_KEY` / `CODEX_BASE_URL` are dead
  (no accessor in `utils/get_env.py`).
- `PUBLIC_PROVIDER_PRICING_CATALOG` and `_REVISION` default `null`
  (`constants/settings_registry.json:695-712`), so `estimatedCostMicrounits`
  in receipts is currently meaningless.

---

## 2. Goals and acceptance

| Goal | Metric | Baseline | Target |
|---|---|---|---|
| G1 Quality (ordinary) | blind rating of the 10-slide fixture, five axes, single expert rater with the D12 consistency check | `F1-stage12` output (pinned) | **no axis below baseline**; craft ≥ baseline |
| G2 Quality (verbatim) | blind rating on `deck-43` against the 7b tiers | Composer r12-43, unrated | ≥ parity (source deck) on legibility, hierarchy, consistency on every slide |
| G3a Reliability (ordinary, generation-class) | worker-lane ordinary attempts reaching `published`, infrastructure-class terminal errors (provider 5xx, publication, lease) excluded until D's phase-1 items land | 16 / 113 (14 %) | **≥ 90 %** over 20 consecutive runs, ≥ 5 distinct inputs |
| G3b Reliability (ordinary, all causes) | same, every terminal error counted | 16 / 113 | ≥ 90 % after D phase-1 |
| G4 Speed | wall time, 20-slide ordinary deck, worker lane, one deck in flight | ~13 min (batch policy) | **≤ 7 min** on the token profile (≤ 5 min once A6 lands); **≤ 3 min** on the API profile |
| G5 Cost | tokens per *successful* deck | ~40k in / 17k out (n=4) | no regression; discarded spend falls with G3 |
| G6 Export | hybrid PPTX native text share | 0 / 642 on Composer decks (A6 FAIL) | ≥ 95 % of text elements native on both engines |

G4 arithmetic (why 7, not 5, on the token profile): 20 per-slide calls at the
measured 81–88 s each, at the profile's concurrency of 5, is four waves ≈
5.5 min of authoring alone, plus outline (p50 13 s) and the render/quality
stages (~15–20 s wall; the receipt's 67 s is per-slide work *summed*, see
E4). Only shorter calls (A6) or higher concurrency
(the API profile) get under 5 minutes; a target that ignores this would be
missed on the first measurement.

G1 is the gate that overrides the others: a change that speeds the pipeline
up and lowers G1 is rejected.

---

## 3. Architecture decision

```text
ordinary generation                     verbatim restyle
────────────────────                    ────────────────
outline (LLM)                           manifest (pptx.py, enriched)
   │                                        │
infographic plan (LLM, deck-scoped)     classify v0 (deterministic)
   │                                        │
author slides (LLM writes HTML,         plan overlay (LLM, typed ops)   ─┐
  pastes finished skeletons)                │                           │ C2
   │                                    figure library (server)         │
   ├─ admission NORMALISE, then gate        │                           │
   ├─ deterministic fit LADDER          fit ladder (existing)          ─┘
   │    (Composer machinery, no LLM)        │
   │  (no quality loop here: A5)       -- or, if C1 wins its fixture --
   │                                    author page around bound
   ▼                                    fragments (LLM), same ladder    C1
publication → Scene IR → hybrid PPTX / PDF   (shared, both engines)
```

Principles kept from the Composer architecture (4.1): content is data
(source text is never model-written on the verbatim route), fit is solved
before rendering and confirmed after, gates are assertions, one IR for
export. Principle 2 ("layout is code") is **narrowed**: on the ordinary route
the model designs the page and code fits it; on the verbatim route code
designs and fits.

What is withdrawn: Composer migration phases 2 ("ordinary authored on Deck
IR") and 3 ("retire authored"). The phase-2 gate asked the Composer to *tie*
the authored engine on quality with a fixed figure registry; viewed against
`candidate-montage.png` and `F1-stage12`, that tie is not credible, and
goal 1 forbids the trade.

---

## 4. Workstream A — authored reliability and speed

All items keep the designer untouched. Each is flag-gated with the current
behaviour as the default until its own measurement passes.

### A1. Batch policy: recover instead of degrade permanently

`services/authored_batch_policy.py`:

- Replace `permanently_serial: bool` with a cooldown state:
  `(concurrency_cap, cooldown_until, consecutive_successes)`. On overload:
  halve the cap (floor 1) and set a cooldown with exponential backoff
  (base 30 s, ×2, cap 5 min). On `N` consecutive successes after the
  cooldown (N = 3): restore one step, up to the profile cap.
- Move the three constants to registered knobs
  (`constants/settings_registry.json` first, then `utils/get_env.py`):
  `AUTHORED_BATCH_SLIDES_PER_CALL`, `AUTHORED_BATCH_WAVE_CONCURRENCY`,
  `AUTHORED_BATCH_RECOVERY_SUCCESSES`.
- Defaults come from the **provider rate profile** (B1): token profile keeps
  today's conservative values; API profile uses per-slide calls
  (`SLIDES_PER_CALL=1`) at the sync path's proven concurrency.
- Add 429 / rate-limit to the backoff trigger set. Verified: `_OVERLOAD_CODES` is
  `{provider_circuit_open, public_provider_bridge_exception, public_provider_call_timeout}`
  plus the 500/503/timeout regexes — **a 429 is not an overload signal today**;
  it is an ordinary failed call, retried by the caller, and never slows the
  wave. That is why the personal token's 429s show up as 15 % retries rather
  than as a downgrade.

Callers to update: `services/authored_presentation_pipeline.py`,
`services/public_generation_deadline_policy.py`, `utils/llm_calls/author_deck.py`.

Expected: worker-lane 20-slide wall ~13 min → ~3–5 min on the token profile.

### A2. Admission: normalise the harmless, then gate

New `utils/authored_html_normalize.py`, called immediately before
`admit_authored_html` on every authored document, with an explicit
whitelist:

| Finding | Normalisation |
|---|---|
| `break-inside`, `page-break-*`, `orphans`, `widows` | strip the declaration |
| `hidden` attribute on an element with **no** bound source text | remove the element |
| `hidden` attribute on an element **with** bound text | strip the attribute; the gate re-measures |
| anything else the policy rejects | still fails admission — the policy remains the security boundary |

Every rule fired is recorded in the quality receipt (`normalizations[]`), so
the prompt can be corrected when a rule fires often. Expected: the 11
admission failures become 0; deck failure for a one-line CSS slip ends.

### A3. Codex token: automatic refresh

In `utils/llm_config._get_codex_access_token`: if `CODEX_TOKEN_EXPIRES` is
within 10 minutes and a refresh token is stored, call the same
`refresh_access_token` the endpoint uses, persist through the endpoint's
storage path, single-flight (one refresh per process at a time), and fall
back to today's 400 on failure. Removes the unattended-run failure mode of
the development profile.

### A4. Deterministic fit ladder replaces LLM repair

`_deterministic_repair_candidate` becomes a ladder driven by the Composer's
`fit_ladder` shape — **Chromium-confirmed at every rung**, because authored
HTML carries no plan to estimate from; bounded to four renders per slide
(~1 s each, `render` was 11 s for 10 slides); receipt trail:

| Rung | Change | Mechanism (existing code) |
|---|---|---|
| 0 | today's composed pass | `_deterministic_repair_candidate` |
| 1 | figure density variant | `.ig.n3…n6` class swap — the quality loop's CSS candidates |
| 2 | uniform type scale-down on owned text to the design floor | `composition_capacity.largest_fitting_text_scale`, `scene_compiler._fit_text_style` |
| 3 | margin / gap compaction | **figure roots only** (`.ig` archetype containers, whose CSS the server owns) plus the existing capacity reducers. Verified: the model's own page CSS has no named containers to hook — `author_slide_rules.py` speaks of a "content box" and pixel widths, not classes |
| terminal | **one automatic remake** of the slide through the existing `/slides/{i}/remake/preview` path with a lower-density hint (one bounded call); if that is terminal too, the deck **publishes with the slide flagged** and the editor offers its remake action | today: `authored_composition_safety_gate_failed` fails the deck |

The LLM repair cycle is set to `max_repair_cycles=0` for the ordinary
profile behind `AUTHORED_LLM_REPAIR_ENABLED` (default **true** until A4's
fixture measurement passes; then flipped). VQA repair follows the same flag.

Terminal behaviour (D7, resolved 2026-09-04): a published deck with one
flagged slide beats a failed deck, and one fresh authoring call for that slide
has a far better prior than prose repair (3 of 24). Per-slide remake exists:
`api/v1/ppt/endpoints/authored_operations.py:320-335` (`/remake/preview`,
`/remake/publish`) and the editor's `AuthoredPageRemake`.

Expected: most of the 16 gate failures resolve without a provider call;
provider spend on repair (24 runs, 3 successes) goes to zero.

### A5. Quality loop: user-triggered after generation, never on the
generation path

Decision (D14, restating the earlier product decision): the quality loop
runs only when the user asks for it on a finished deck, through the
existing **"고품질 검수"** operation
(`POST /{id}/quality-review`, `services/authored_quality_review_service.py`,
today vision QA plus closed-operation fixes). Generation renders every
slide exactly once.

Why it cannot sit on the generation path: the loop's cost is real even in
its cheapest form. Best-of-N (`services/authored_quality_loop.py`) renders
two or three CSS variants per figure slide in Chromium behind the same
render semaphore (default 4), so render work per figure slide is ×2–3; the
design critique (`AUTHORED_DESIGN_CRITIQUE_ENABLED`, separate flag) adds a
vision model call per figure or DOM-flagged slide, measured at +80 % wall
in the 2026-09-02 experiment. Both work against G4 for every deck, while
the user wants them for some decks.

Work:

- move the call at `authored_presentation_pipeline.py:1151` out of the
  authoring stage: `apply_slide_quality_loop` becomes a step of the
  quality-review operation (deck scope or `ReviewScope` slide subset), run
  after vision QA on the persisted slide HTML, publishing through the same
  version path the review already uses;
- the generation pipeline no longer reads `AUTHORED_QUALITY_LOOP_ENABLED`;
  the flag gates whether the review offers the loop, and
  `AUTHORED_QUALITY_LOOP_CANDIDATES` keeps its meaning;
- rung 1 of the A4 ladder still uses the same density-variant CSS swap for
  a slide that *fails* fit — that is a repair of one slide, not a search
  over every slide, and stays on the generation path;
- receipts (E4) record `qualityLoop.trigger = review` with the added
  render and critique milliseconds, so the review's own wall is measured
  separately from generation.

Measurement: blind-rate `F1-stage12` vs. the same deck after the review
with the loop; the review ships the loop if G1 improves. The generation
wall targets (G4) are measured with the loop absent by construction.

### A6. Prompt trimming (token-constrained throughput)

With concurrency capped by the token profile, the remaining throughput lever
is shorter calls. Measure the prompt composition
(`utils/llm_calls/author_slide_prompt.py`, `author_slide_rules.py`); once A4
owns fit, remove the capacity/overflow prose the rules carry for the model's
benefit. The target is set *after* the prompt composition is measured (share of rule
prose by section) — not before; it ships only on a passing A/B with G1
unchanged.

---

## 5. Workstream B — provider split (dev token / prod API)

### B1. Provider rate profile

`AUTHORED_PROVIDER_RATE_PROFILE = auto | token | api` (registered knob).
`auto` derives from `LLM_PROVIDER`: `codex` → `token`, everything else →
`api`. The profile sets the A1 defaults and the `AUTHORED_AUTHOR_CONCURRENCY`
default; explicit knobs override. No code path may read the provider name to
make a rate decision — only the profile.

The profile also owns the concurrency **ceiling**. Today
`services/authored_generation_performance.py` hard-clamps every provider to
5..8 (`AUTHORED_PROVIDER_CONCURRENCY_MAX = 8`), so an API key could never be
used above 8 without a code change. `token` keeps 8; `api` takes its maximum
from a knob (default 16). `authored_provider_concurrency(provider)` is routed
through the profile, not replaced.

### B2. Model parity fixture

Run the 10-slide fixture on both providers with the same model family and
reasoning effort; compare blind rating, wall, tokens. Three gaps, two verified in code:

- reasoning effort is codex-only (`get_extra_body`, `llm_config.py:411-426`)
  but is consumed for every provider through `utils/llm_utils.py:315`, so the
  OpenAI branch is a one-place extension (`reasoning_effort` for reasoning
  models);
- `OPENAI_MODEL` defaults to **`gpt-4.1`** (`settings_registry.json:33-35`), a
  non-reasoning model: an operations deployment that forgets to set it
  silently drops quality. The B4 checklist sets it explicitly;
- model-name parity on the API side is a runtime fact the fixture itself
  checks; it is not a pre-development item.

### B3. Pricing catalog for operations

Populate `PUBLIC_PROVIDER_PRICING_CATALOG` + `_REVISION` for the production
model so `estimatedCostMicrounits` is real; add a per-deck cost line to the
receipt and a daily spend total to the weekly report (E3).

### B4. Secrets and switching

Per-environment `LLM_PROVIDER` and key via env / `userConfig.json`
(`CAN_CHANGE_KEYS`). Development machines never hold the production key.
Switching is configuration; a deployment checklist in `RUNNING-LOCAL.md`
names the four variables.

### B5. Ledger note

The personal token is licensed for the account holder; serving other users
through it is a policy risk. Recorded so the multi-user launch (goal stated
2026-08-19) is gated on B1–B4, not on optimism.

---

## 6. Workstream C — verbatim restyle

The authored engine has produced **0 verbatim decks in 44 attempts**; the
Composer produces 43/43 but below parity. Two candidates, one decision gate.

### C1. Authored page around bound fragments (fixture first)

Hypothesis: the verbatim failures come from the model *rewriting text*, not
from the model *designing pages*. The infographic skeleton contract already
proves the model can paste finished markup verbatim
(`utils/authored_infographic/contracts.py`, static `checker.py`). Extend
that contract from "the figure" to **"every source block"**:

- the server renders each source block as a finished, bound fragment
  (`data-source-block-id`, `data-qid`, exact runs) using the Composer's
  `render_text` primitives;
- the model composes the page *around* the fragments — layout, chrome,
  figure choice, emphasis — and may not edit, drop or duplicate one; the
  static checker enforces byte identity;
- the infographic plan for a verbatim page returns **node → source block
  references**, not labels (`models/authored_infographic_plan.py`,
  `utils/llm_calls/author_infographic_plan.py`); `coerce_infographic_plan`
  does not clip bound nodes; title and furniture blocks are bound fragments
  too, so the model's freedom is archetype placement, arrangement and chrome;
- the archetype library gains elastic slots (chip / card / row) — in **one**
  place, section 6.5;
- the A4 ladder and the existing readback gates run unchanged.

Fixture: `deck-43` and `deck-35`. Size: **4–5 days** — the plan-model change,
the fragment contract and the elastic slots are each real work. This is the higher-ceiling
path because the designer is the same one that produced
`candidate-montage.png`.

### C2. Composer with LLM plan overlay (the guaranteed path)

`docs/composer-archetype-library-phase1-design-20260904.md` as amended: the
deterministic classifier produces plan v0; a batched LLM call returns closed
operations (`attach`, `set_role`, `set_page`, `group`, `set_figure`,
`set_emphasis`) validated against the same partition invariants; the figure
library renders. Zero-call mode stays as the flag-off fallback. This path
cannot invent structure and will look like "template + figures"; its ceiling
is parity, and that is its role.

### C3. Decision gate

Blind rating on `deck-43`: C1, C2, source deck (B), floor (A). Rule:

- whichever candidate is ≥ parity on legibility, hierarchy and consistency
  on every slide ships as the verbatim default;
- if both pass, C1 is the default and C2 the fallback when C1 fails a gate;
- if neither passes, C2 continues to phase-1 completion and C1 is recorded
  with its failure mode.

### C4. Export (A6)

Hybrid PPTX export of verbatim decks is unexercised (0/642 native when
forced). `render_export_policy.py` from the phase-1 design lands first so
both C1 and C2 markup is checked against
`docs/authored-native-figure-contract-20260903.md` by construction; then the
export reader is exercised on each candidate. G6 applies to both.

### 6.5 One archetype library (D9)

C1 renders figures through `utils/authored_infographic/` (20 archetypes, CSS,
checker); the phase-1 design would have built a second, Composer-native set
under `services/composer/figures/`. Two libraries would diverge within weeks.
Decision: **one library**, `utils/authored_infographic/`, extended with

- elastic slots (chip / card / row) chosen from measured text length;
- a pluggable node-text renderer: a node's text is either a plan label
  (ordinary route, escaped) or a bound source fragment (verbatim, via
  `services/composer/render_text.render_bound_text`), so the QID-namespace
  objection of the phase-1 design (its section 1.2) is met by injection, not
  duplication;
- a height model per archetype, as the phase-1 design requires, so the
  ladder never discovers a figure's size by rendering it.

C2 renders its figures through the same library; the `services/composer/figures/`
modules of the phase-1 plan become thin adapters (plan → node references),
not a second implementation. The phase-1 document's module plan is amended
accordingly (its section 0, item 5).

---

## 7. Workstream D — worker-lane infrastructure (separate track)

Named so the reliability target (G3) is not mistaken for a generation
problem alone. From `authored-capacity-and-service-launch` and the programme
spec's known-bug list:

- workers run in-process (`api/lifespan.py`); a restart kills in-flight jobs
  and leaves zombie `running` / `awaiting_resume` rows;
- SQLite single file at 9.5 GB, 141 `database is locked` errors on heavy days;
  production profile is PostgreSQL (`docs/runtime-settings-multiworker-sync.md`);
- lease heartbeat naive/aware datetime TypeError on SQLite; resumed attempts
  mis-flagged as retries (`retry_requires_an_initial_request`).

Scope for this programme, split so G3 does not depend on a track that never
starts: **phase 1** fixes the two lease bugs (small, and they are the
excluded class of G3a today); **phase 3** moves production to the PostgreSQL
profile with externalised workers (`get_workspace_operation_worker_mode()`).
Everything else in that list is out of scope here.

---

## 8. Workstream E — measurement

- **E1 Fixture benchmark.** `F1-stage12` (10 slides) is the pinned quality
  baseline. Extend to 50 slides (programme stage 6). Every change runs it
  through the *worker lane* and records: outcome, wall, tokens, ladder rung
  histogram, normalisations fired, quality-loop variant, `fallbackElementIds`.
- **E2 Blind rating.** `scripts/composer_blind_rating_pack.py` generalised to
  accept any candidate directory. **Single expert rater** (D12): the product
  owner, who has authored many proposal decks, rates alone; the 7b protocol's
  "three raters, median" is replaced by one rating with two integrity checks —
  the script keeps `key.json` sealed until the sheet is submitted, and every
  sheet carries **five duplicated slides** shown under a second anonymous id;
  a round counts only if the rater agrees with themself within one point on
  at least four of the five. Five axes, 1–5, per slide, unchanged. This is
  the only quality **gate** — applied at phase exits and to any change of
  prompts or library CSS. Per-change iteration uses E1's
  automated metrics plus an optional vision-model *advisory* score that never
  gates; a full rating round per commit would make rating the programme's
  bottleneck. **The existing pack is rated before any development**, so
  every later change has a "before".
- **E3 Weekly report.** Success rate, wall p50/p90, tokens per successful
  deck, spend (B3), rung histogram, top terminal errors — from receipts.
- **E4 Receipts.** Add `providerRateProfile`, `normalizations[]`,
  `fitLadder.trail`, `qualityLoop.variant`, `costMicrounits` (real), and a
  **`wallMillis` per stage next to the existing work sum**: today `render`
  and `quality` are `sum(result[5|6] …)` over slides
  (`authored_presentation_pipeline.py:1436-1437`) and the log's `author=` is
  a thread-second sum too, so a reader takes 67 s of summed per-slide work for
  67 s of wall. Render already runs behind `AUTHORED_SLIDE_RENDER_CONCURRENCY`
  and the process-wide `AUTHORED_RENDER_CONCURRENCY` (default 4,
  `utils/slide_capture.py:2006`); it is not a speed lever, and the receipt
  should stop suggesting it is.

---

## 9. Sequence

| Phase | Weeks | Work | Exit measurement |
|---|---|---|---|
| 0 | 0 | rate the existing blind pack (E2); commit the three design documents | quality "before" on disk |
| 1 | 1–2 | A1, A2, A3, B1, E1, E4, D lease fixes | worker-lane success on the fixture ≥ 80 %; 20-slide wall ≤ 7 min (token) |
| 2 | 2–4 | A4 (ladder, LLM repair off behind flag), A5 moved into the quality review and measured, C1 fixture, B2 parity fixture | G1 holds with A4 on; the review with A5 rates ≥ generation-only; C3 decision taken |
| 3 | 4–6 | A6 prompt trim, chosen verbatim path to parity, C4 export for it, B3 catalog, D production profile | G2, G4 (API profile), G6 |
| 4 | 6+ | multi-user launch prerequisites (B4, B5, remaining D) | G3 over 20 consecutive live runs |

Every phase is mergeable on its own; every new behaviour ships behind a flag
whose default is today's behaviour until the phase's exit measurement passes.

### 9.1 Phase 1 measurement, 2026-09-04

Phase 1 shipped A1, A2, A3, B1, D's lease fixes, E1 and E4, plus one defect
this measurement itself uncovered (below). Measured on an isolated backend
(SQLite, token profile, image generation on, warm icon pack) with
`node scripts/generation-benchmark.mjs`; evidence in
`artifacts/e1-benchmark-20260904/`.

| Exit criterion | Target | Measured | Verdict |
|---|---|---|---|
| Worker-lane success, 20-slide fixture | ≥ 80 % | 2 / 3 (67 %) | not met |
| Wall, 20-slide deck | ≤ 420 s | p50 790 s, p90 981 s | not met |

The baseline both numbers are measured against is 16/157 succeeded and a
13-minute 20-slide deck, so the lane went from mostly-failing to mostly-working
and roughly held its wall. Neither target is met, and both misses are
informative rather than surprising:

- The single failure is `authored_composition_safety_gate_failed` — a measured
  layout defect returned to the model as prose. That is precisely the failure
  class **A4** replaces with the deterministic ladder in phase 2; no phase-1
  item addressed it.
- The wall is dominated by provider latency: 774–814 s of the 790–981 s wall,
  over 10–11 calls. The token profile's batch shape (3 slides per call,
  1 then 2 concurrent waves) is the ceiling, and it is deliberate — it exists
  to keep the personal token under its rate limit. The levers left are **A6**
  (shorter calls) and the **API profile** (B1, `AUTHORED_PROVIDER_RATE_PROFILE=api`),
  both phase 3. G4's 7-minute figure is therefore a phase-3 number on the
  token profile, not a phase-1 one; this document's own arithmetic (section 2)
  predicted ~5.5 min of authoring alone at concurrency 5, and the token
  profile does not run at concurrency 5.

**Defect found by the measurement (fixed in phase 1).** The same 10-slide
fixture first died at 2 097 s with `public_deadline_exceeded` while only
417 s of that was provider time. E4's new per-stage `wallMillis` located the
missing 27 minutes in image generation: all three `AsyncOpenAI` image clients
(`services/image_generation_service.py` ×2,
`services/authored_icon_pack/provider.py` ×1) were constructed with
`max_retries=0` but **no timeout**, so the SDK's own ten-minute default
applied, twice (`OPENAI_IMAGE_MAX_ATTEMPTS = 2`), behind the process-wide
image lock — one hung picture could hold every picture and the deck's whole
deadline. Fixed with a registered `IMAGE_GENERATION_TIMEOUT_SECONDS`
(default 180, clamped 5–600) applied to every image client; a picture that
exceeds it degrades to the HTML-only figure. Same fixture after the fix:
447 s, published. This defect plausibly accounts for part of the
`presentation_generation_failed` / `http_error_500` group in section 1.2.

Images remain part of the product on this route: they are icons inside
infographic figures and `scene` cutouts, not standalone illustrations, so
they belong in every measurement. Their cost here is ~67 s per 10-slide deck
with a warm pack; a cold pack costs a further ~2.4 min once per style.

---

## 10. Pre-development verification checklist

Items marked **[verify]** above, to be closed during design review:

1. ~~`_OVERLOAD_CODES` includes 429?~~ **Closed: it does not** (A1 amended).
2. ~~executor line and concurrency cap~~ **Closed:** `author_slide.py:163`,
   cap 5..8 default 5 (`authored_generation_performance.py:9-11`).
3. ~~container classes for compaction~~ **Closed:** none exist on the model's
   page; rung 3 limited to figure roots (A4 amended).
4. ~~per-slide regeneration~~ **Closed:** `/slides/{i}/remake/preview|publish`
   (`authored_operations.py:320-335`), editor `AuthoredPageRemake`.
5. ~~reasoning param / model parity~~ **Closed as design:** one-place
   extension in `get_extra_body`; `OPENAI_MODEL` default `gpt-4.1` trap
   recorded; name parity is a B2 runtime check.
6. ~~uncommitted Composer tree~~ **Closed:** `services/composer/` and
   `services/source_content/` are committed (last `4a7fe6752`); only the
   three design documents are uncommitted.
7. The blind pack's parity PDF renders the source deck faithfully
   (LibreOffice conversion). **Partially closed:** pages 6 and 11 of B viewed
   on 2026-09-04 and faithful (card grid, dark divider); full-deck check
   remains part of E2.

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Model parity between codex and API is not exact; prod quality differs from dev | B2 fixture before the first production deployment; G1 measured on the API profile separately |
| A4 ladder has limited reach on model-authored CSS; some slides still flag | terminal rung publishes and flags; count is reported (E3); A6 prompt trim reduces overflow at the source |
| C1 fails readback the way authored verbatim did | the static checker makes fragment edits a *contract* failure caught before render; C2 remains the fallback |
| Personal-token limits slow the measurement loop during development | A1 recovery; fixture runs serialised; E1 nightly rather than per-commit if needed |
| Shared worktree: concurrent sessions edit the same files | phase 0 of section 9; check `git status` before every long edit (memory `shared-worktree-concurrent-sessions`) |
| Quality regression hidden by an unrated change | E2 at phase exits and for prompt / library-CSS changes; E1 metrics on every change; vision advisory flags drift between ratings |
| Two archetype libraries drift apart | D9: one library, two text sources; adapters only in `services/composer/figures/` |
| G4 missed because the token profile cannot exceed concurrency 5 | target restated to 7 min with the arithmetic shown; A6 is the only path to 5 |

---

## 12. Decisions recorded

| # | Decision | Date |
|---|---|---|
| D1 | Quality (the PPTX the user opens) over speed; authored+library output is the bar | 2026-09-04 |
| D2 | Development on the Codex personal token; operations on an organisation API key; provider is per-environment configuration | 2026-09-04 |
| D3 | Authored engine remains the designer for ordinary generation; Composer phases 2–3 withdrawn | 2026-09-04 |
| D4 | Composer fit machinery reused inside authored to replace LLM repair (A4) | 2026-09-04 |
| D5 | Verbatim: C1 fixture before further Composer figure work; C3 decides | 2026-09-04 |
| D6 | Admission failures for whitelisted findings become normalisations, not deck failures | 2026-09-04 |
| D7 | Ladder-terminal slides get one automatic remake, then publish flagged rather than failing the deck | 2026-09-04 — resolved (A4) |
| D9 | One archetype library (`utils/authored_infographic/`) with a pluggable node-text renderer; Composer figures are adapters | 2026-09-04 |
| D10 | Human blind rating at phase exits and prompt/CSS changes; automated metrics per change | 2026-09-04 |
| D11 | G4 token-profile target is 7 min, 5 min only after A6 | 2026-09-04 |
| D12 | Blind rating by a single expert rater (the product owner) with sealed key and five duplicated slides per sheet as the consistency check | 2026-09-04 |
| D13 | `chromiumReview` is not a workstream: the receipt's 67 s is summed per-slide work; E4 records wall per stage | 2026-09-04 |
| D14 | The quality loop (best-of-N, critique) runs only from the user-triggered quality review after generation; the generation path renders each slide once | 2026-09-04 |
| D8 | The existing blind pack is rated before development starts | 2026-09-04 |

## 13. Related documents

- `docs/generation-quality-root-cause-and-composer-architecture-20260904.md`
  — root cause (sections 2–3 stand for the verbatim route); section 6
  phases 2–3 withdrawn by this document (see its section 6c).
- `docs/composer-archetype-library-phase1-design-20260904.md` — verbatim
  figure library; scope narrowed and selection amended (see its section 0).
- `docs/authored-infographic-plan-20260903.md`,
  `docs/authored-native-figure-contract-20260903.md` — the library and
  export contract the ordinary route relies on.
- `output/infographic-experiment-20260902/NEXT-SESSION-PROMPT.md` — the
  six-stage programme; stage 4 is absorbed into A5 (as a post-generation
  review step, D14) and stage 6 into E here.

---

## 14. Flags and rollback

| Flag | Default now | Flipped when | Rollback |
|---|---|---|---|
| `AUTHORED_BATCH_SLIDES_PER_CALL` / `_WAVE_CONCURRENCY` / `_RECOVERY_SUCCESSES` | today's constants (3 / 1→2 / n.a.) | phase 1 exit | set the old values |
| `AUTHORED_PROVIDER_RATE_PROFILE` | `auto` (= `token` on codex) | never forced | `token` |
| `AUTHORED_HTML_NORMALIZE_ENABLED` | **true** on merge (whitelist only) | — | `false` → today's admission failures return |
| `CODEX_TOKEN_AUTO_REFRESH` | true | — | `false` → manual refresh endpoint |
| `AUTHORED_FIT_LADDER_ENABLED` | false | A4 fixture passes G1 | `false` → single deterministic pass |
| `AUTHORED_LLM_REPAIR_ENABLED` | true | after the ladder flag flips | `true` restores prose repair |
| `AUTHORED_TERMINAL_REMAKE_ENABLED` | false | with the ladder | `false` → publish flagged without remake |
| `AUTHORED_QUALITY_LOOP_ENABLED` | false (exists) | A5 rating passes; gates the quality review only, never generation | `false` → review runs vision QA alone |
| `COMPOSER_RESTYLE_ENABLED` | false (exists) | C3 chooses C2 | `false` → authored verbatim (0/44) |
| `AUTHORED_VERBATIM_FRAGMENTS_ENABLED` (C1) | false | C3 chooses C1 | `false` |

Every flag is registered in `constants/settings_registry.json` and read
through `utils/get_env.py`; none is read at request time from disk.

## 15. Design review record (2026-09-04)

Self-review against the evidence in section 1, scored 1–10 per axis before
and after the amendments applied in this revision.

| Axis | Before | Finding | After |
|---|---|---|---|
| Evidence | 9 | every claim cited; verbatim "0 succeeded" rests on the 2026-09-04 `workspace_operations` query, receipts do not tag route | 9 |
| Goals & gates | 5 | G4 token ≤ 5 min ignored the arithmetic (5.5 min authoring alone at concurrency 5); G3 counted verbatim and infra failures it could not address | 8 |
| Architecture | 8 | sound; a second figure library was implied by C1 + C2 | 9 |
| WS-A | 7 | rung 3 had no hook (model writes free CSS); "estimate-first" does not transfer to authored HTML; A6 target unmeasured | 8 |
| WS-B | 7 | concurrency hard-clamped 5..8; `OPENAI_MODEL` default `gpt-4.1`; reasoning param codex-only — all three found in this review | 9 |
| WS-C | 6 | C1 omitted the plan-model change (nodes as block references); library duplication; 2–3 days optimistic | 8 |
| WS-D | 5 | declared separate while G3 depended on it | 7 |
| WS-E | 6 | three raters per change would bottleneck the programme | 8 |
| Sequence & risk | 7 | no flag/rollback matrix; `chromiumReview` (67 s) unexamined — resolved: it is a summed work figure, not wall (D13) | 8 |
| **Overall** | **6.7** | | **8.2** |

Remaining known weaknesses after this revision:

- C1 is a hypothesis with a 4–5 day fixture behind it; if the model edits
  fragments despite the checker, C3 falls back to C2 and the verbatim ceiling
  is parity, not the montage.
- ~~`chromiumReview` undesigned~~ closed (D13): the stage runs in parallel
  already; its receipt figure is a work sum, not wall.
- G1 rests on one rater. The duplicated-slide check catches inconsistency,
  not bias; the vision advisory between rounds is the only independent
  signal. Until the first rating exists, every quality claim in this document
  is unproven.
