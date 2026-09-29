# SVG generation: what runs inside the `outline` timing stage, and how to shorten it (2026-09-29)

Status: analysis and design only. No code was changed and no live generation was run.
Evidence: the bench receipts in `artifacts/canvas-bench-20260929/` (11 decks, backend pinned at
`115b9263a`, see `docs/canvas-bench-square-ultrawide-20260929.md`), the shared-stack log
`app_data/logs/backend.log`, and a read of the code paths named below.

## 1. Summary

- The svg `outline` stage is not the outline call. It is the whole pre-page part of the
  operation: everything from handler start to the end of `_prepare_generation`.
- In the bench it averaged 160 s (130 to 181 s), 50 % of wall time (45 to 60 %).
- For a prompt-only 6-slide deck that window contains **three sequential reasoning-model LLM
  calls** at the configured `gpt-6-sol` / effort `medium`: content brief, outline, and the
  deck's infographic plan. Nothing in it runs concurrently with anything else. The plan and the
  asset offer cannot be skipped by the page loop, because every page's input digest includes
  the asset manifest.
- Per-receipt cross-check: `provider.durationMillis - authoring.durationMillis` (provider time
  that is not page authoring) is 127 to 182 s, mean 149 s, against an outline stage of mean
  160 s. So the window is almost all provider time, not parsing, DB or queue time. The split
  between the three calls is **not** recoverable from the receipts (section 4).
- Ranked options (section 5): (1) tune or skip the content brief, the largest single call and
  already knob-controlled, saving an estimated 40 to 100 s; (2) lower the reasoning effort of
  the infographic-plan call, or fold it into the outline, estimated 10 to 40 s; (3) take the
  Mem0 writes off the critical path, 3 s per deck. Streaming page authoring behind the outline
  is possible but is high risk for a bounded gain and is not recommended yet.
- Before any of that, record sub-spans (section 4). The first bench after that answers what
  this document can only bound.

## 2. What runs inside the window, in order

Clock. The workspace handler pins `_authored_outline_started_at` at
`services/presentation_generation_operation_handler.py:194` (via `_begin_upstream_outline_timing`,
`:89-93`, which applies to `template == "authored"`, the value svg decks carry) after the job,
task and operation rows are loaded, and marks the active stage `outline` just after (`:197-205`). The
window closes in `services/generation_core/drivers/svg/collect.py:49-50`, right after
`_prepare_generation` returns (`timing.record_outline`, `drivers/svg/timing.py:73-84`). Queue
wait is reported separately (`queueWaitMillis`, 1.7 to 7.5 s in the bench).

Configuration in effect (from `app_data/userConfig.json`, copied into the bench backend):
`LLM=codex`, `CODEX_MODEL=gpt-6-sol`, `CODEX_REASONING_EFFORT=medium`,
`IMAGE_PROVIDER=codex_image`. `get_extra_body` (`utils/llm_config.py:451-466`) applies that one
effort to every `get_generate_kwargs` / structured call, so brief, outline and plan all run at
`medium`; with the key unset the same code would force `low`.

Handler path, `api/v1/ppt/endpoints/presentation_generate.py::generate_presentation_handler`
(`:842`), entered from the operation handler:

| # | Step | file:line | LLM? | Notes |
|---|---|---|---|---|
| 1 | Admission re-validation, reference snapshots | `presentation_generate.py:876` (`_revalidate_generation_admission_for_retry`, `:816`) | no | DB reads; ms. Source-restyle modes add `_admit_source_content_*` at `:847-861`. |
| 2 | Document parse (only when `request.files`) | `:932` -> `_load_document_context` `:554`, `services/documents_loader.py`, `liteparse_service.py` | no | Bench manuscripts are text; 0 s there. Shared-stack evidence for a .pptx: LibreOffice convert 15 s + LiteParse 1.5 s (log 09-28 12:00:08 to 12:00:24). |
| 3 | Content brief (`AUTHORED_CONTENT_BRIEF_ENABLED`, default `True`, `utils/get_env_programme.py:110-114`) | `:946-985` -> `run_content_brief_with_checkpoint` (`services/authored_content_brief_checkpoint.py:181`) -> `generate_content_brief` (`utils/llm_calls/generate_content_brief.py:98`) | **yes**: non-streaming structured call (`generate_structured_with_schema_retries`), model gpt-6-sol, effort medium | Output has only `minItems` floors, "no maxItems/maxLength caps -- depth is the goal" (`utils/get_dynamic_models.py:82-110`). Deadline `min(900, 600 + 1.0 s/kchar)` (`content_brief_policy.py:125-127`), i.e. up to 10 to 15 minutes of allowance. Input = prompt + document context. |
| 4 | Outline message build, then Mem0 `store_generation_context` (awaited) | `:990-1024` | no (local embedding) | Measured on the shared stack: 8.5 s one-time lazy init of Mem0 + 3 inserts, 2.7 s after warm-up (log 09-28 12:02:31 to 12:02:43). Awaited, on the critical path. |
| 5 | Outline LLM | `:1027-1050` -> `generate_ppt_outline` (`utils/llm_calls/generate_presentation_outlines.py:140`) | **yes**: streaming structured call, effort medium, input = prompt + brief + canvas-budget system prompt, `inactivity_timeout_seconds=120` | The stream is buffered whole (not forwarded) when the density floor applies to a document-backed deck or the ceiling is on (`generate_presentation_outlines.py:350-351`), followed by optional extra LLM condense/expand calls (`outline_density.py`). Measured: one 8-slide deck, 51 s (log 09-28 12:02:44.8 to 12:03:35.4). |
| 6 | Parse, trim to `n_slides`, `strip_outline_citations`, pin `authored_outline` (DB commit), Mem0 `store_generated_outlines` | `:1051-1130` | no | ms to about 0.3 s (Mem0 insert). |
| 7 | Hand-off: `generate_svg_presentation` acquires `_AUTHORED_JOB_SEMAPHORE` | `services/svg_presentation_service.py:45-57` | no | Zero for one deck at a time. Under concurrent decks the semaphore wait is booked to `outline` too (it is before `record_outline`). |

`_prepare_generation` (`services/svg_presentation_prepare.py:56`), called from
`drivers/svg/collect.py:49`:

| # | Step | file:line | LLM? | Notes |
|---|---|---|---|---|
| 8 | Admission, style pin, snapshot write | `:79-120` | no | One short DB transaction. |
| 9 | Source-image candidates and plan | `:135-142` | no | `collect_source_image_candidates`; local. |
| 10 | Style-image preflight, checkpoint init | `:154-162` | no | Local files, one DB write. |
| 11 | **Asset offer**: `_offer_assets` -> `resolve_offer` -> `build_asset_offer` | `:164-166`, `:215-245`, `drivers/svg/asset_slots.py:139-184`, `services/generation_core/assets/offer.py:211-265` | see below | Skipped for source-reflow and raster-reconstruction; skipped when no image provider (`asset_slots.py:156-160`). |
| 11a | Infographic plan | `offer.py:227` -> `assets/infographic_plan.py:50-91` -> `utils/llm_calls/author_infographic_plan.py:185` | **yes**: non-streaming structured call, effort medium, input = all slide contents + archetype guide + style brief | Failure returns `None`, deck continues without figures. |
| 11b | Scene illustrations | `offer.py:240-249` | image provider (`codex_image`), up to `illustration_budget=4` (`runtime/profile.py:48`), each bounded at `IMAGE_GENERATION_TIMEOUT_SECONDS=180` (`utils/get_env.py:198`) | `asyncio.gather`: scenes run concurrently with each other. |
| 11c | Icon sheets | `offer.py:253-258`, `:_icon_slots` | image provider, remaining budget | Runs **after** the scenes finish, sequentially, plus a local pack lookup first (`resolve_icons(generate=False)`). |
| 12 | Canonical source manifest, publication context | `:168-195` | no | DB + artifact store; ms to low seconds. |
| 13 | Runtime profile and semaphores | `:197-201` | no | trivial |

Order and concurrency: 2, 3, 4, 5, 6, 7, 8..13 are strictly sequential. The only concurrency in
the window is the scene fan-out in 11b. Step 11a needs the finished outline; step 5 needs the
brief from step 3; the page loop needs the offer from step 11 (each page's `input_digest`
includes `ctx.manifests[index]`, `drivers/svg/collect.py:115`), so pages cannot start
earlier without changing what is pinned.

How many provider calls: every deck in the bench has `logicalCalls - retries - 6 = 4`
(11 of 11 decks, for retry counts 1 to 5). Six are the page authoring calls, retries are extra
authoring calls, and the constant 4 is consistent with brief + outline + plan + the deck's one
vision advisory (`drivers/svg/quality.py:44-56`, `vision_advisory=True` at
`runtime/profile.py:104`, in the quality stage, after the outline window). Image-provider calls
are also recorded as provider calls (`services/image_generation_service.py:131`), and a count
that never varies suggests the bench decks made **zero** scene or icon-sheet image calls, so 11b
and 11c contributed little. This is an inference from a count, not an observation.

## 3. Where the time goes: measured

Bench receipts (`generation-attempt-timing-receipt/v2`). `P-A` is `provider.durationMillis`
minus `authoring.durationMillis`, an upper-bound-style proxy for provider time outside page
authoring; `gap` is `outline - (P-A)`.

| round / canvas / ms | outline s | P-A s | gap s | outline share of wall |
|---|---|---|---|---|
| 1 / 1:1 / 01 | 150.3 | 148.7 | 1.7 | 51 % |
| 1 / 1:1 / 02 | 153.9 | 149.4 | 4.5 | 46 % |
| 1 / 21:9 / 01 | 165.9 | 157.6 | 8.3 | 50 % |
| 1 / 21:9 / 02 | 130.3 | 126.7 | 3.6 | 47 % |
| 1 / 16:9 / 01 | 181.0 | 169.5 | 11.5 | 47 % |
| 1 / 16:9 / 02 | 180.6 | 182.3 | -1.7 | 60 % |
| 2 / 1:1 / 01 | 141.5 | 134.1 | 7.3 | 56 % |
| 2 / 1:1 / 02 | 161.7 | 150.4 | 11.3 | 50 % |
| 2 / 21:9 / 01 | 163.1 | 149.2 | 13.9 | 45 % |
| 2 / 16:9 / 01 | 157.1 | 133.2 | 23.9 | 45 % |
| 2 / 16:9 / 02 | 176.8 | 141.8 | 35.0 | 49 % |
| mean | 160.2 | 149.4 | 10.8 | 49.6 % |

Reading it, with the limits stated:

- The outline stage does not vary with canvas (1:1, 16:9, 21:9 all 130 to 181 s). It depends on
  the manuscript and on model latency, as expected for content-only calls.
- `P-A` also contains the vision advisory's provider time (quality stage) and excludes the
  non-provider part of `authoring` (validation, checkpoint writes, SVG asset flattening). The
  two errors have opposite signs, so `gap` cannot be read as "non-provider time in the outline
  window". What can be said: the window is dominated by provider calls; parse, DB, Mem0 and
  local image work are a small remainder (the negative and the 35 s gaps bracket the
  uncertainty).
- The queue wait is separate and small.

Direct per-call measurement exists for one deck only, on the shared stack (`backend.log`, source
`.pptx`, 8 slides, 09-28 12:00 to 12:03, presentation `0d1ba882`; the path before the strategy
branch is common to svg and authored):

| span | seconds | source |
|---|---|---|
| Office convert + LiteParse | 16.5 | log 12:00:08 to 12:00:24.8 |
| gap before the brief call (further parsing / context) | about 20 | brief start derived as `12:02:31.8 - 107.1 s` |
| **content brief** | **107.1** | `content_brief_policy` line: `elapsed=107.1s attempts=1` |
| Mem0 init + pre-outline store | 11.2 (8.5 of it one-time) | log 12:02:31.8 to 12:02:43.0 |
| **outline stream** | **about 51** | first codex response 12:02:44.8, `outline_density` line 12:03:35.4, median 464 chars, 8 slides |
| Mem0 post-outline store | about 0.3 | log 12:03:35.7 |

That deck is document-backed and has 8 slides, so its brief is larger than a bench deck's. It
still shows the shape: the brief is the largest single span (about 2 outlines long), and the
outline is about 50 s. It is not a bench number.

Not observable anywhere today: the infographic-plan duration, the asset-offer image time,
`_prepare_generation` local time, and the exact split of the 130 to 181 s. The bench backend
(pinned worktree, disposable PostgreSQL on :22600) was stopped and its `provider_usage_receipts`
rows and stdout logs were not retained; the shared stack has no svg deck logged after 09-28
(its last log lines on 09-29 are maintenance only).

Best estimate for a bench deck, labelled as inference: outline 45 to 60 s, brief 50 to 100 s,
plan 15 to 40 s, local 5 to 15 s, which sums to the observed 130 to 181 s. The brief is the
largest term in every plausible split.

## 4. Missing instrumentation (record these before changing behaviour)

The receipt cannot split the window because `record_outline` writes one span
(`drivers/svg/timing.py:73-84`) and the receipt has a fixed stage set. Two routes, both cheap:

1. **Zero-code for LLM calls.** `provider_usage_receipts` already stores one row per provider
   call with `job_id`, `operation`, `stage` (`content_brief`, `outline`, `illustrate`, ...),
   `latency_ms`, `input_tokens`, `output_tokens` (`models/sql/provider_usage_receipt.py`,
   index `ix_provider_usage_receipts_job_stage`). For the next bench, query it per bench job
   before the disposable database is dropped (`workspace_operations` and jobs are not RLS
   filtered; check RLS on this table with `set_config('presenton.tenant_id', ...)` as noted in
   the worker-stall memory). Note the infographic plan is recorded under stage `illustrate`
   (`asset_slots.py:178-179`), together with any image calls; split them by `model` / provider.
   I did not query the shared stack database for this analysis: it needs the owner credential
   and the shared stack holds no svg bench deck.
2. **Small code change for non-provider spans.** One INFO line per sub-span, not a receipt
   schema change, so the timing receipt stays `v2`:
   `svg_outline_span name=<x> ms=<n> presentation=<id>` for `doc_load` (`:932`), `brief`
   (already logged with `elapsed`, keep), `mem0_pre` (`:1012`), `outline_llm` with
   time-to-first-chunk and total, `outline_post` (`:1051-1130` including the density passes and
   the `authored_outline` commit), `job_semaphore_wait` (`svg_presentation_service.py:56`),
   `prepare_local` (steps 8 to 10, 12, 13), `plan_llm` (`offer.py:227`), `scenes` and
   `icon_sheets` (`offer.py:249`, `:253`, with provider-call counts already in the offer
   `receipt`). Persist the same numbers into the offer receipt (`AssetOffer.receipt`) that the
   checkpoint already carries, so a bench receipt can read them without a log.

With these, the bench harness can add an `outlineBreakdown` block per deck. Cost: about 40
lines, no behaviour change, no quality risk.

## 5. Options, ranked by expected saving against quality risk

Priority is quality first, speed second, so options that change what the model is asked to
produce carry a bench gate. "Saving" figures are estimates from section 3 and are marked; they
should be replaced by measured values after section 4.

### Option 1: content brief, per-call effort or skip (largest, knob already exists)

- What: (a) run the brief at `low` (or `none`) effort while keeping outline and authoring at
  `medium`; (b) or skip the brief for prompt-only requests (and keep it for document-backed
  ones), where its job is to add substance the document does not carry; (c) or skip it
  entirely in the svg driver.
- Expected saving: (a) 25 to 60 s (a `medium` reasoning call halving at `low` is a common
  ratio here, not measured); (b/c) the whole call, 50 to 100 s for a bench deck (107 s measured
  on one large document-backed deck). That is 15 to 30 % of a 300 to 380 s deck.
- Quality risk: medium to high. The brief exists because the outline is the only
  content-writing step (`generate_presentation_outlines.py:382-386`, the 600 to 128 characters
  per slide collapse when effort was forced low on 09-05), and the outline's grounding in
  brief facts drives density. Lowering the brief's effort is the safer half; removing it needs
  a rating. Do not assume the outline effort transfers: only the outline call was measured
  before.
- Files: for (c) no code, `AUTHORED_CONTENT_BRIEF_ENABLED=false` in the bench backend (the
  knob is read at `presentation_generate.py:946`; per the rollout-knob memory, AUTHORED_* knobs
  are applied through the DPAPI stack env and a restart, not the admin endpoint). For (a) a
  per-call override in `generate_content_brief` / `get_extra_body` (`utils/llm_config.py:451`),
  which today reads one global effort. For (b) a condition at `presentation_generate.py:946`.
- Verify: 2 manuscripts x 16:9 x 2 rounds with the knob on/off (and (a) if built), same pinned
  backend, comparing outline seconds, `outline_density` median, retries, flagged pages and a
  blind side-by-side rating of the outline text and final decks, as in the prompt-bundle
  rating. Accept only if ratings do not drop.

### Option 2: infographic plan, lower effort or fold into the outline

- What: (a) call the plan at `low` effort (it is a classification and layout choice over
  already-written slides with a small structured output); (b) later, ask the outline call to
  also emit per-slide archetype and figure nodes so a separate plan call disappears.
- Expected saving: (a) 8 to 20 s; (b) 15 to 40 s (the whole call; unmeasured, see section 4).
- Quality risk: (a) low to medium, since the plan decides which pages get figures and icons
  (`author_infographic_plan.py`); a worse plan shows as fewer or wrong figures, which the bench
  gate can see. (b) medium to high: it grows a strict outline schema and merges two prompts,
  and the plan's own guidance (`_ARCHETYPE_GUIDE`, `_GUIDANCE`) would move into the outline
  prompt, competing with the density instructions.
- Files: (a) `author_infographic_plan.py:185` plus a per-call effort hook (shared with option
  1a); (b) `generate_presentation_outlines.py`, `utils/get_dynamic_models.py` (`outline_model`),
  `assets/infographic_plan.py`.
- Verify: same bench; also compare `figurePages`, `icons`, `illustrations` in the offer
  receipt and figure/icon presence on the contact sheets.

### Option 3: take Mem0 writes off the critical path

- What: run `store_generation_context` and `store_generated_outlines`
  (`presentation_generate.py:1012`, `:1124`) as background tasks, and warm Mem0 at process start
  (`services/mem0_oss_memory.py`), instead of awaiting them in the hot path.
- Expected saving: about 3 s per deck in steady state, plus 8.5 s for the first deck after a
  process start (measured, section 3).
- Quality risk: none for output. Operational: a failure must stay non-fatal and ordering
  against `store_generated_outlines` must be kept (the outline write reads the same
  presentation id).
- Files: `presentation_generate.py`, `services/mem0_oss_memory.py`.
- Verify: `mem0_pre` and `outline_post` sub-spans before and after; no change in bench quality
  needed beyond a smoke run.

### Option 4 (not recommended yet): start page authoring while the outline streams

- What: the outline call already streams (`generate_presentation_outlines.py:380`), so the
  first pages could begin before the last slide is written.
- Expected saving: at most the outline stream time, 45 to 60 s, minus the dependent steps that
  still gate pages; realistically 20 to 40 s.
- Why it is high risk: the plan/offer is deck-level and pinned in every page's input digest, so
  pages cannot author before the offer exists (or the offer must become optional per page and
  be delivered late, changing the page prompts); the outline is buffered whole whenever the
  density floor or ceiling applies (`generate_presentation_outlines.py:350-351`); the post-processing (`strip_outline_citations`,
  trim to `n_slides`, density condense) and the `authored_outline` pin that resume relies on
  (`presentation_generate.py:1117`) all act on the complete outline; and authoring waves are
  bounded by author concurrency 3 (`runtime/profile.py:44`), so early starts help only the first
  wave. Revisit after sections 4 and options 1 to 3.

### Not worth doing (evidence says no)

- Caching per style or canvas: nothing in the window depends only on style or canvas. Brief and
  outline depend on the content, and the plan depends on the content and style brief. The
  outline stage did not vary by canvas in the bench.
- Lowering the outline call's own effort or trimming its prompt: this is the only
  content-writing step and the one measured to collapse density when effort was forced low
  (`generate_presentation_outlines.py:382-386`). Trimming its input means trimming the brief,
  which is option 1.
- Overlapping deck-level preparation (steps 8 to 10, 12, 13): they are milliseconds to a few
  seconds and depend on the outline (`count`, `roles`); the only large deck-level preparation
  is step 11, covered above.

## 6. Suggested order

1. Add the sub-span instrumentation and read `provider_usage_receipts` on the next bench
   (section 4). This turns every estimate above into a measurement and costs no quality.
2. Option 3 (safe, small) together with step 1.
3. Bench option 1(c) via the existing env knob (no code), then 1(a) if the rating says the
   brief is worth keeping.
4. Option 2(a).
5. Reassess options 2(b) and 4 with measured splits.

Expected outcome if options 1 and 2(a) hold their ratings: outline stage from about 160 s to
about 80 to 110 s, wall time per 6-slide deck from about 330 s to about 250 to 280 s. That is an
estimate to be replaced by the measured bench result.

## 7. Files read for this analysis

`services/presentation_generation_operation_handler.py`, `api/v1/ppt/endpoints/presentation_generate.py`,
`services/svg_presentation_service.py`, `services/svg_presentation_prepare.py`,
`services/generation_core/drivers/svg/{timing,collect,asset_slots,quality}.py`,
`services/generation_core/assets/{offer,infographic_plan}.py`,
`services/generation_core/runtime/profile.py`, `utils/llm_calls/{generate_presentation_outlines,generate_content_brief,author_infographic_plan}.py`,
`api/v1/ppt/endpoints/content_brief_policy.py`, `utils/llm_config.py`, `utils/get_env_programme.py`,
`models/sql/provider_usage_receipt.py`, `app_data/logs/backend.log`,
`artifacts/canvas-bench-20260929/**/*.receipt.json`.
