# SVG outline-stage speed options: quality A/B execution plan (2026-09-30)

Status: design only. No code changed, nothing run, shared stack untouched. This plan executes the two
options from `docs/svg-outline-stage-speed-analysis-20260929.md` that need a quality gate before
adoption: option 1 (content brief off or lower effort) and option 2(a) (infographic plan at lower
effort). Options 3 (Mem0, already landed: `mem0_pre_ms` is 0) and 4 are out of scope.

## 1. Evidence and facts re-verified

### 1.1 Measured (42 pooled decks)

Source: `docs/canvas-bench-pooled-candidate-20260929.md` and the `svg_outline_span` lines in
`artifacts/canvas-bench-20260929/pooled-candidate/backend-logs/generation-20260929-105137.err.log`
(44 lines for 42 decks; the surplus is presumably the invalidated and recovered decks, not checked).
One line per deck, for example
`svg_outline_span presentation=<id> ... content_brief_ms=125625 outline_total_ms=25329 prepare_local_ms=1406 plan_llm_ms=37000 ...`.

| span | median | range | share of outline stage (median 166 s) |
|---|---|---|---|
| `content_brief_ms` | 82.7 s | 53.7 to 127.1 s | about 50 % |
| `plan_llm_ms` | 49.0 s | 32.5 to 81.2 s | about 30 % |
| `outline_total_ms` | 33.0 s | 19.9 to 75.0 s | about 20 % |
| `prepare_local_ms` | 1.3 s | 0.8 to 18.5 s | 1 % |
| Mem0, semaphore, icon sheets, post | 0 | 0 to 0.2 s | 0 |

Outline stage is 58 % of the 285 s median deck wall. The plan call is larger than the analysis
document's 15 to 40 s estimate, so option 2 is worth more than first thought (10 to 25 s at half the
call).

### 1.2 Knob and code facts (read from code, not run)

| fact | where |
|---|---|
| The brief knob is `AUTHORED_CONTENT_BRIEF_ENABLED`, default `True`, read by `is_authored_content_brief_enabled()` | `utils/get_env_programme.py:110-114` |
| The brief runs in the shared pipeline body **before** the strategy branch, so it applies to svg (the `content_brief_ms` values above prove it) | `services/presentation_generation_pipeline.py:938-967` (moved from `presentation_generate.py`) |
| With the knob off, `outline_context = additional_context`; a brief failure is already tolerated (log and continue), so "no brief" is an exercised path | same, `:936-967` |
| The knob is process-wide (env / settings snapshot). It is not a request field, so per-request switching does not exist today | `get_env_programme._bool_setting` via `get_setting_env` |
| One effort value applies to every LLM call: `get_extra_body()` reads `CODEX_REASONING_EFFORT` (bench backend: `medium`; unset would force `low`) and returns `{"reasoning": {"effort": ...}}` | `utils/llm_config.py:452-466` |
| `get_generate_kwargs` calls `get_extra_body()` with **no argument**, so there is no per-call override today; the brief (`generate_content_brief.py:98`, via `generate_structured_with_schema_retries`) and the plan (`author_infographic_plan.py:185`, same helper) take no effort parameter | `utils/llm_utils.py:303-330`, `:777` |
| Legal effort strings accepted by `get_extra_body`: `minimal, none, low, medium, high, xhigh`. Whether `gpt-6-sol` accepts `none`/`minimal` is unverified and must be smoked | `utils/llm_config.py:462` |
| The plan is called inside the `plan_llm` span in `offer.py:227` through `resolve_infographic_plan` -> `generate_infographic_plan`; it is the only text-LLM call in the offer (scenes and icon sheets are image calls) | `services/generation_core/assets/offer.py:227`, `infographic_plan.py:50-91` |
| The outline is the only other content-writing step; an earlier forced-`low` outline collapsed to 128 chars/slide from 600 (comment at `generate_presentation_outlines.py:383-386`) | so the outline effort stays `medium` in every arm |
| The density floor (`expand_thin_outline`) applies to **document-backed** decks only, so a prompt-only deck with no brief has no safety net against thin outlines | `utils/llm_calls/outline_density.py`, analysis doc row 5 |
| Bench manuscripts are 880 to 1,198 bytes, prompt-only text (`tests/fixtures/canvas_bench_manuscripts/01..04`). The 107 s brief measured on the shared stack was for a document-backed deck | see 8, threat T1 |
| The bench-only request field `bench_reference_condition` and the `CANVAS_BENCH_ENABLED` gate from `docs/svg-canvas-brief-rewrite-and-experiment-plan-20260929.md` Part 2 are **design only**: grep of `models/generate_presentation_request.py`, `services/svg_generation_admission.py` and `constants/internal_env_names.py` finds neither | so the machinery is shared work, not existing |
| Durable payload path: `staging.py:118` writes `_internal_svg_style_snapshot` at admission; `:322` pops it on worker rehydration; excluded request fields are dropped by `model_dump` | `services/presentation_generation_staging.py:92,118,322` |
| `AUTHORED_CONTENT_BRIEF_ENABLED` does not appear in `constants/internal_env_names.py` (grep for "brief" is empty); new knobs must be registered wherever the test-enforced names contract requires | check before adding |

## 2. Arms

All arms keep outline and page authoring at the configured `medium`. Only the two stage calls change.

| id | brief | plan | how |
|---|---|---|---|
| A baseline | on, medium | medium | today |
| B brief-off | off | medium | brief stage skipped |
| C brief-low | on, `low` | medium | per-call effort override on the brief |
| D plan-low | on, medium | `low` | per-call effort override on the plan |
| E brief-low + plan-low | on, `low` | `low` | both |

Conditional extra arm F (only if C still costs more than 45 s median, decided after the smoke):
brief effort `minimal` (or `none`, if the provider accepts it). Not in the base matrix.

Expected saving per deck (estimates from 1.1, to be replaced by the run): B up to 83 s (median brief),
about 29 % of 285 s; C 35 to 45 s if `low` halves the call (a ratio the analysis called common but
unmeasured); D 10 to 25 s; E about 45 to 70 s.

## 3. Arm switch

### 3.1 What exists and what it costs

- **Existing env knob** covers only B, and only per process. Switching it means restarting the backend
  and, per the rollout-knob memory, going through the DPAPI store. Running arms as separate backend
  sessions would put each arm in a different time window, which is exactly the provider-drift threat
  (T3) that interleaved blocks avoid. Not recommended, except as a fallback for B alone.
- **Per-request switching** needs a bench-only request field. Reuse the Part 2 transport
  (`exclude=True` field, `CANVAS_BENCH_ENABLED` 422 gate, arm recorded and cross-checked from receipts),
  but **not** its carrier: Part 2 rides the frozen `_internal_svg_style_snapshot` because its consumer
  is the style adapter. Here the consumers are the pipeline body (brief) and `svg_presentation_prepare`
  (plan), not the style, and writing the arm into the style snapshot would change its digest and every
  page's input digest for a non-style reason.

### 3.2 Design

1. `models/generate_presentation_request.py`: `bench_speed_arm: Optional[Literal["baseline","brief_off",
   "brief_low","plan_low","brief_low_plan_low"]] = Field(default=None, exclude=True)`.
2. Admission (same place as the Part 2 gate): a non-null value with `CANVAS_BENCH_ENABLED != "true"` ->
   422 `bench_field_disabled`. If Part 2 lands first, the gate is shared; if this lands first, it
   introduces the gate and Part 2 reuses it.
3. `presentation_generation_staging.py`: write `payload["_internal_bench_speed_arm"]` beside
   `_internal_svg_style_snapshot` (`:118`) and pop it beside it (`:322`) into
   `request._bench_speed_arm`. It survives worker takeover and restart.
4. Effort override: a `ContextVar` `llm_reasoning_effort_override` in `utils/llm_config.py`, read first
   by `get_extra_body()` for the codex provider, plus a context manager
   `with reasoning_effort("low"):`. No signature changes in the call chain. The value must be one of the
   six legal strings. Verify in the smoke that the override reaches the brief through
   `run_content_brief_with_checkpoint`, which may run the call under a timeout wrapper: a `ContextVar`
   is copied into tasks created after it is set, so the manager must be entered **outside** that
   helper, around the call at `pipeline:946`.
5. Brief hook: in the pipeline body, `brief_off` skips the `if is_authored_content_brief_enabled()`
   block (`:938`); `brief_low`/`brief_low_plan_low` wrap the call in `reasoning_effort("low")`.
6. Plan hook: wrap the `_offer_assets` call in `svg_presentation_prepare.py` (`:164-166`) in
   `reasoning_effort("low")` for `plan_low`/`brief_low_plan_low`. The offer's only text-LLM call is the
   plan (image calls do not read `get_extra_body`), so the wider scope is equivalent; confirm by
   reading `offer.py` once more at build time.
7. Receipts: the runner records the requested arm. The backend records the applied arm and effort per
   stage into the `svg_outline_span` line (append `brief_effort=` and `plan_effort=` fields).
   `provider_usage_receipts` is already keyed by `stage` (`content_brief`, `illustrate`) but is not
   known to record the effort, so the log line is the evidence. The runner fails the deck
   (`invalid: arm_mismatch`) when the log line's applied arm disagrees with the request.

Tests (at build time): default path unchanged when the field is null; override precedence in
`get_extra_body` (override beats `CODEX_REASONING_EFFORT`, absent override unchanged, other providers
ignore it); staging round trip; gate 422; arm mismatch detection in the runner.

Removal after the experiment: the field, the gate (if not kept by Part 2), the staging key. The
context manager stays, because the production knobs (section 9) use it.

## 4. Matrix

- **Canvases**: `a4-landscape` (product default from the UI, `ui-default-canvas-is-a4` memory) and
  `wide-16x9` (baseline shape, API and CLI default, and the corpus with the 18 existing baseline decks).
  Not in `constants/canvas_stages.json` for these two, which lists only 1:1 and 21:9 as `candidate`;
  confirm a4-landscape admission for each style with `scripts/canvas_admission_table.py` before the run
  (not verified here).
- **Styles**: `default` (the pooled corpus style) and `strategic-navy` (exists in
  `authored_styles/`, was the second pilot style in the Part 2 design and has reviewed exemplars;
  D1 below asks the user to confirm).
- **Manuscripts**: `01` to `04`.
- **Cells**: 2 canvases x 2 styles x 4 manuscripts = 16 cells; **each cell runs all 5 arms once** in a
  seeded order that balances arm positions (5 arms: 16 cells give 3 or 4 uses of each of 5 cyclic
  rotations; use Latin-square rotation), blocks shuffled. **80 decks**, 6 slides each.
- Pairing: within a cell, the same manuscript, canvas, style and time window; the outline is **not**
  fixed (it cannot be for B, C, E, since the brief feeds it). Every arm therefore carries its own
  outline variance; the noise floor is measured in 6.1.

Human panels (a "3-deck cell" is one screen with three decks X/Y/Z, arm order blinded):

- Panel 1, brief axis: A, B, C. 16 screens.
- Panel 2, plan axis and combination: A, D, E. 16 screens.

A appears in both panels, so its two ratings are used as a within-experiment consistency check, and
panel 1 also carries the C-versus-B comparison. E versus C (plan effect at low brief) is read from the
metrics, not from a panel.

## 5. Metrics

### 5.1 Primary: time

Per deck, from the timing receipt and the `svg_outline_span` line (runner reads the backend log,
keyed by `presentation` id; new option `--backend-log`):

- total backend wall; `outline` stage seconds (receipt); `content_brief_ms`, `plan_llm_ms`,
  `outline_total_ms`, `prepare_local_ms`;
- **downstream check**: authoring seconds per slide and retries per slide, because a thinner outline
  can change page authoring time in either direction, so the net saving is the wall figure, not the
  brief figure.

Estimator: per-cell paired difference arm minus A; median and mean with a paired bootstrap 90 %
interval (the bench already has cluster-adjusted machinery in `canvas_bench_stats.py`; extend it with
the paired form). Outliers: reject standby or resume runs by a wall-time check (finding 3 of the pooled
report), since 3,396 s decks otherwise poison a median.

### 5.2 Guards

- **Page flag rate** and retries per slide (existing epoch `svg-overlap-ink-v1`, commit must include
  `e8475ee6f`). Baseline is about 0.02 (2 of 108 at 16:9), so this guard is weak; it is a floor
  check, not a quality measure. Also generation and export failure counts and `plan_unavailable` or
  brief-skipped rates per arm (a lower effort could raise plan parse failures, which silently returns
  a figure-free deck).

### 5.3 Density and "content got thinner"

All computed offline from stored data, by a new `scripts/canvas_bench_speed_metrics.py`. Text is the
visible text of `slides.svg_content` (`<text>`/`<tspan>` nodes, whitespace-normalised), the same
notion the outline-density module uses for characters (`usable_chars`, `outline_density.py:66`), so the
numbers line up with the backend's `outline_density` log line.

| id | metric | source | direction |
|---|---|---|---|
| D1 | usable characters per content page (cover excluded), and whitespace-token words per page | stored SVG | higher is denser |
| D2 | outline characters per slide, median (pinned outline) | `presentation_generation_jobs.authored_outline` (`models/sql/presentation_generation_job.py:84`); the backend also logs the median | higher is denser |
| D3 | outline items per slide (bullets / lines) | same outline text | higher |
| D4 | **numeric-fact recall**: distinct numeric facts of the manuscript (integers, decimals, percentages, currency and Korean units such as 억, 만, 개월, dates; normalised by removing thousands separators and unit spacing) found in the deck text, as a fraction | manuscript vs stored SVG | higher is more faithful |
| D5 | numeric facts per page (distinct normalised tokens) | stored SVG | higher |
| D6 | **novel numbers**: numeric tokens in the deck absent from the manuscript, per deck | stored SVG | report both ways (see below) |
| D7 | figure pages, icons, illustrations | offer receipt (`figurePages`, `icons`, `illustrations`) | for D and E: fewer means plan-low hurt |
| D8 | pages with a table, chart or multi-column figure archetype | plan archetypes in the offer receipt | for D and E |

D6 needs a warning. The manuscripts are about 1 KB, so almost all of the content the brief adds is
knowledge the model brings itself: novel figures. Removing or weakening the brief will reduce D6 and
D5, which the scorecard would read as "thinner", yet it also removes unsupported numbers from a deck.
Neither direction is a defect by itself, so D6 is reported and read by the judges, and it is not in the
automatic rule; D4 (fidelity to the source) is.

Metric noise floor before any arm is judged (6.1): the 16:9 default-style corpus already holds 18
baseline decks (manuscripts 01 to 03, repeats r01 to r06) with stored SVG. Running D1 to D5 on it gives
the within-arm spread for free, and the tolerance in section 7 is set from it, not guessed.

### 5.4 Blind human pairwise

- Two judges (the user plus one Korean-fluent colleague; D6), independently, on the static panel pages
  (`canvas_bench_render.cell_page` style, three 2x3 contact sheets per screen, labelled X/Y/Z, no
  arm or file names).
- Two questions: Q1 "which deck carries more useful content" (rank with ties), Q2 "which would you
  present" (rank with ties). Also a free-text field per screen for "thin"/"padded" remarks.
- Output: per-panel pairwise outcomes (A vs B, A vs C, B vs C; A vs D, A vs E, D vs E), exact one-sided
  sign test on non-tied cells, Cohen's kappa between judges. Answer sheets are unblinded by script only
  after both are in. Effort: 32 screens x 2 questions about 65 to 70 minutes per judge, in parallel.
- Power: at n = 16 non-tied cells a one-sided p < 0.05 needs 12 of 16, and the test cannot prove "no
  loss". Its job here is to catch a large, visible loss; the smaller loss is bounded by D1 to D5.

## 6. Runner changes (`scripts/canvas_bench*.py`)

The existing runner takes one style per invocation and canvases as `CANVAS=N` counts
(`canvas_bench.py` `matrix`, `plan_matrix`); no experiment mode exists in code (Part 2's is design
only). Reuse the Part 2 plan wherever it fits, so the two experiments share one tool:

- `canvas_bench_matrix.py`: `Deck` gains `style` and `arm`; `plan_experiment(canvases, styles,
  manuscripts, arms, seed)` builds one block per cell with a Latin-square arm order and shuffled block
  order (the same function Part 2 specifies, with `arms` a parameter); receipt path
  `out/<canvas>/<style>/<manuscript>-<arm>.receipt.json`; resumable as today.
- `canvas_bench.py`: subcommand `experiment` with `--styles`, `--arms`, `--backend-log`;
  `_request_body` (`:66`) adds `bench_speed_arm`; receipts gain `arm`, `armObserved`, `spans`
  (the parsed log line), `backendCommit` (refuse to pool decks from different commits).
- `canvas_bench_speed_metrics.py` (new): D1 to D8 per deck and per page; frozen constants
  (numeric-token regex, unit table, normalisation) committed with a 20-page hand check of the numeric
  extractor on the existing decks before any arm deck is scored.
- `canvas_bench_render.py` and a `canvas_bench_blind.py` shared with Part 2 for panel pages and the
  blind key and unblind step.
- `canvas_bench_stats.py`: paired difference with bootstrap interval, sign test.
- Standby and outlier check: reject a deck whose wall exceeds the cell median by more than 3x, and hold
  the process-scoped `SetThreadExecutionState` request as in the pooled run.
- Tests: plan balance (each arm equally often in each position within one from perfect), request body,
  log-line parser (against a saved real line), arm mismatch, numeric extractor on synthetic Korean and
  English strings, paired statistics against known values.

Coordination: `canvas_bench_live.py` and `tests/unit/test_canvas_bench.py` carried another session's
edits during the Part 2 design; re-check `git log` for those two files before starting.

## 7. Decision rule

All quantities are paired per cell versus A. "Loss" thresholds Y are provisional; section 5.3's noise
floor fixes them before the run and they are committed before any arm deck is unblinded.

**Saving gate (per arm X):** median paired wall saving >= 30 s (about 10 %) and the lower bound of the
90 % paired interval > 0. For B, whose risk is larger, require >= 60 s.

**No-loss gate (all must hold):**
1. D1 and D2: paired ratio X/A with the interval's lower bound >= 0.90 (a 10 % thinner deck is a loss;
   tighten if the noise floor allows), and D3 not below 0.90.
2. D4 numeric-fact recall: difference X - A with the lower bound >= -0.05 (5 points).
3. Page flag rate: upper bound of (p_X - p_A) <= +0.05; failures and `plan_unavailable` rate not above A.
4. Human: on non-tied cells of Q1 and Q2, A preferred over X with one-sided exact p > 0.10 in both
   questions (for n = 16 without ties, A preferred in fewer than 11 cells), and neither judge's own
   ranking shows A over X significantly at p <= 0.05. Remarks flagging "thin" or "padded" are read and
   reported by the panel owner.
5. Plan arms (D, E) additionally: D7/D8 figure and icon counts >= 0.90 of A.

| result | action |
|---|---|
| E passes both gates | adopt E (brief low, plan low) as default; then evaluate F with a small follow-up only if C is still slow |
| C passes, D does not | adopt C alone; leave the plan at `medium` |
| D passes, C does not | adopt D alone |
| B passes and saves at least 20 s more than C with C also passing | adopt B **for prompt-only requests only** (section 9), never for document-backed decks |
| B loses on density, C passes | C is the answer; B is closed |
| nothing passes the no-loss gate | keep A; record which metric failed; the outline is the only content-writing stage, so depth needs the brief |
| a quality metric is inside the tolerance but the human panel is split | do not adopt on this run; add one repeat of the affected arm (+16 decks) |

## 8. Threats to validity

- **T1 prompt-only bench versus the product mix.** All four manuscripts are prompt-only, about 1 KB.
  The brief matters most where the input is thinnest; document-backed decks get a density floor and a
  large source, and the 107 s brief was measured on such a deck. A win here is **not** evidence for
  document-backed decks. Either scope the adoption (section 9) or add a document-backed phase.
- **T2 outline variance.** Outlines differ per deck, so every arm carries sampling noise. Mitigation:
  the noise floor from the 18 existing baseline decks, one repeat kept in reserve, cluster by
  manuscript in the statistics.
- **T3 provider drift and time of day.** Arms interleaved in blocks, one serial backend, one commit,
  refuse to pool otherwise (the pooled run's 16:9 tail showed a 10 s per slide drift).
- **T4 effort semantics.** `low` on `gpt-6-sol` may not behave like a halved call, and `minimal`/`none`
  may be rejected. The smoke checks that the override reaches the request and that `content_brief_ms`
  moves; the actual saving is read from spans, not assumed.
- **T5 net saving smaller than the brief.** Without a brief the outline may run longer or produce
  more density retries; hence the wall and downstream metrics (5.1), not just `content_brief_ms`.
- **T6 weak guards.** Baseline flag rate is about 0.02, so the guard cannot detect small regressions,
  and n = 16 limits the human test. The decisive protection is the paired density measures; the human
  panel catches only a large visible loss.
- **T7 numeric extractor validity.** Korean units and formatting variants will mis-tokenise; hence the
  hand check and frozen constants before scoring, and D6 kept out of the automatic rule.
- **T8 arm E confounding.** E differs from A by two changes; E versus C isolates the plan effect only
  through metrics.
- **T9 style and canvas coverage.** Two canvases and two styles; per-canvas or per-style splits at 8
  cells are descriptive only. A result that reverses on one cell group blocks that group.
- **T10 judges.** Two judges, one of them the user; kappa reported, a third for disagreements only.
- **T11 bench-only field leaks into production.** The 422 gate and the removal step in section 3 cover it.

## 9. Rollout if adopted

1. Production knobs, not the bench field: `AUTHORED_CONTENT_BRIEF_EFFORT` and
   `AUTHORED_INFOGRAPHIC_PLAN_EFFORT` (empty means "inherit `CODEX_REASONING_EFFORT`", so the default
   behaviour is unchanged until the value is set), read through `utils/get_env_programme.py` beside the
   existing brief knob and registered in whatever the names contract requires
   (`constants/internal_env_names.py` is test-enforced; the existing brief knob is not listed there, so
   confirm the right registry first). The override uses the same `reasoning_effort()` manager.
2. Default change is a config default, applied on the shared stack through the DPAPI store and a
   restart, one knob at a time, with the previous value recorded for rollback (rollout-knob memory).
3. If only B passed: a condition at `pipeline:938` (brief skipped when there is no document context),
   never global.
4. Receipts: add `contentBrief.effort/status` and `infographicPlan.effort/status` to the offer receipt
   and keep the `svg_outline_span` line fields; watch, per week, median outline stage seconds, the
   `outline_density` median chars per slide, page flag rate, `plan_unavailable` rate and the reviews
   backlog. A drop of median chars per slide of more than 10 % against the A baseline from this
   experiment is the rollback trigger.
5. Do not touch `constants/canvas_stages.json`; the stage tiers are unrelated.
6. Expected result if E holds: outline stage from 166 s to about 95 to 120 s, deck wall from 285 s to
   about 220 to 240 s. This is an estimate to be replaced by the run.

## 10. Hours

Per-deck wall estimates (from 285 s baseline median and the section 2 savings): A 285, B about 205,
C about 245, D about 270, E about 230 seconds; sum about 1,235 s per cell.

| stage | count | time |
|---|---|---|
| Build (field, override, runner, metrics, tests) | - | about 2 developer-days (shares the blind and panel tooling with Part 2) |
| Noise floor and extractor hand check on existing 18 baseline decks | - | about 0.5 h machine, 1 h review |
| Smoke: 5 arms x 1 cell, log line, override and arm cross-check | 5 | about 0.4 h |
| Full run | 80 | about 5.5 h (16 cells x 1,235 s + 80 x 6 s client) or 6.7 h at the 5 min/deck rule of thumb; **budget 6.7 h, plus 20 % retry margin to about 8 h** |
| Metrics, panels | - | about 0.4 h |
| Human panels | 2 judges | about 70 minutes each, parallel |
| Optional repeat of the arms that are borderline | up to 48 | about 4 h |

Total machine time: 7 to 8 h serial on the isolated backend, split over two sessions if resumed
(runs are resumable).

## 11. Open decisions

- **D1** Second style: `strategic-navy` (default proposal) or another reviewed style.
- **D2** Adoption scope: prompt-only requests only (recommended for B), or all requests if a
  document-backed phase is added; and whether to add that phase (4 to 6 document manuscripts, at least
  a .pptx and a PDF) before any default changes.
- **D3** Thresholds: 30 s (60 s for B) saving, 0.90 density ratio, -0.05 recall, +0.05 flag rate,
  p > 0.10 human. Confirm after the noise floor is measured.
- **D4** Switch mechanism: bench-only request field (recommended) or a backend per arm (only viable
  for B).
- **D5** Extra arm F (`minimal`/`none`) before or only after the smoke.
- **D6** Second judge, and a third for ties.
- **D7** Build order versus the Part 2 reference-image experiment: they share the runner, the gate and
  the panel tooling, so build once. Which experiment runs first.
- **D8** Whether the brief output size (output tokens per stage from `provider_usage_receipts`) is
  recorded as an extra depth signal, at the cost of querying the disposable database before it is
  dropped.

## 12. Self-score

**7 / 10.** The spans, knob location, absence of a per-call override, and the pipeline order are read
from code and logs, and the transport for the arm is anchored on the real staging path. Nothing is
built or run.

1. Both effort savings are unmeasured (`low` versus `medium` on `gpt-6-sol` has no data); the estimate
   of C and E rests on an assumed halving.
2. Bench manuscripts are about 1 KB prompt-only; the result cannot be extended to document-backed
   decks, which is where the 107 s brief came from.
3. Quality tolerances are provisional and the human test at n = 16 is weak; the decisive guard is the
   density and numeric-recall set, whose extractor is unvalidated for Korean until the hand check.
4. Whether `none`/`minimal` are legal for `gpt-6-sol`, and whether a4-landscape is admitted for
   `strategic-navy`, are not verified.
5. It depends on Part 2 machinery that exists only as a design, so build order is a real coupling.
