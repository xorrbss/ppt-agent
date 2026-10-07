# Canvas gate closure plan (2026-09-30)

Status: design only. No code was changed. Five items finish the canvas expansion: recalibrating the
`verified` tier for the post-ink-fix base rate, moving portrait onto the stage gate, per-style exposure,
exposing `overlap_basis` so the metric epoch no longer depends on git ancestry, and making the bench
runner see machine standby. Each item states current behaviour with file and line, the change, size,
tests, acceptance and risks. Open decisions and a self-score close the document.

Inputs: `docs/canvas-bench-decision-rule-design-20260929.md` (rule and its Implementation section),
`docs/canvas-bench-pooled-candidate-20260929.md` (42 decks, one batch), the 42 receipts under
`artifacts/canvas-bench-20260929/pooled-candidate/` (gitignored; numbers below are read from them),
`servers/fastapi/scripts/canvas_bench*.py`, `services/authored_canvas.py`, `constants/canvas_stages.json`,
`constants/canvas_registry.json`. Simulations use the stdlib and `scripts/canvas_bench_stats.py` unchanged
(beta-binomial decks of 6 pages, one-sided 95% Newcombe on design-effect-adjusted counts, 3,000
replicates per cell); the script is reproducible from the numbers stated here.

Line references are to `servers/fastapi/` unless a path says otherwise, at `040b8c397`.

## 1. Verified-stage margin recalibration

### Current behaviour

- Tiers: `scripts/canvas_bench_assess.py:22-25` sets `candidate` at margin 0.20 (12 candidate / 18 baseline
  decks, 3 manuscripts, 1 day) and `verified` at margin 0.15 (24 / 24, 4 manuscripts, 2 days). The margins
  were derived for a 16:9 page flag rate of 0.27 under the line-box epoch (design section 4.2).
- Primary check `_primary` (`:160-173`): Newcombe bound on `p_cand - p_base` with design effect
  `1 + 5 x 0.15 = 1.75` (`scripts/canvas_bench_stats.py:16-22`); pass when the upper bound is at or below
  the margin. Secondary gates `_mean_check` (`:133-141`): retries per slide at the same margin, seconds per
  slide at 15% of the baseline mean, both needing 12 decks per arm.
- The day rule `_sample_gaps` (`:155-156`) counts distinct `createdAt` dates on the candidate arm only.
- Data the rule sees per deck: `flaggedPages` from the operation result's `quality_pages`
  (`scripts/canvas_bench_live.py:229-235`, written at `services/generation_core/drivers/svg/publish.py:146`);
  retries as `attemptNumber - 1 + providerUsage.totals.retryCalls` (`canvas_bench_live.py:156-173`).
  `flaggedSlides` is still `[]` in every receipt; per-page classification and attempts are not recorded.

### What the pooled data says now

Ink epoch, style `default`, svg, 6 slides, 42 decks:

| arm | decks | flagged pages | rate | retries per slide (deck sum) | first-pass failing pages |
|---|---|---|---|---|---|
| 16:9 | 18 | 2 / 108 | 0.019 | 7 / 108 = 0.065 (per deck: 1,1,0,0,2,0,0,0,0,0,0,0,0,0,0,1,1,1) | same 7 |
| 1:1 | 12 | 1 / 72 | 0.014 | 2 / 72 = 0.028 | 2 |
| 21:9 | 12 | 1 / 72 | 0.014 | 3 / 72 = 0.042 | 3 |

All four flags are `required_text_missing`. `logicalCalls` is `10 + retries` in 42 of 42 decks (10 calls =
outline, brief, plan, 6 pages, review), so a retry is one re-authored page: the retry count is the
first-pass failing page count, three times the flag rate but still rare.

At a base rate of 0.02 the 0.20 / 0.15 margins admit a canvas up to 8 to 11 times worse than 16:9. They no
longer decide anything; the equal-canvas pass probability is 1.00 and the fail probability of a candidate
at +0.10 (six times the baseline) is only 0.12. The re-derivation the design left open (decision 3) is due.

### Power at p = 0.02 (simulated, rho 0.15, 6 pages per deck)

Cells give `P(pass) / P(fail)` for a candidate whose true rate is `0.02 + d`. `fail` is the design's
definition (point difference at or beyond the margin and lower bound above zero).

| margin | baseline / candidate decks | d = 0 | d = +0.02 | d = +0.05 | d = +0.10 |
|---|---|---|---|---|---|
| 0.05 | 24 / 24 | 0.60 / 0.01 | 0.24 / 0.10 | 0.04 / 0.41 | 0.00 / 0.83 |
| 0.05 | 36 / 36 | 0.79 / 0.01 | 0.32 / 0.09 | 0.05 / 0.51 | 0.00 / 0.95 |
| 0.05 | 48 / 48 | 0.89 / 0.00 | 0.41 / 0.06 | 0.04 / 0.49 | 0.00 / 0.96 |
| 0.075 | 18 / 12 | 0.53 / 0.01 | 0.26 / 0.06 | 0.09 / 0.25 | 0.01 / 0.62 |
| **0.075** | **24 / 24** | **0.87 / 0.00** | 0.53 / 0.03 | 0.16 / 0.24 | 0.01 / 0.74 |
| 0.075 | 36 / 36 | 0.96 / 0.00 | 0.71 / 0.01 | 0.21 / 0.16 | 0.00 / 0.77 |
| **0.10** | **18 / 12** | **0.78 / 0.00** | 0.50 / 0.02 | 0.21 / 0.13 | 0.04 / 0.47 |
| 0.10 | 24 / 24 | 0.97 / 0.00 | 0.80 / 0.00 | 0.38 / 0.07 | 0.05 / 0.47 |
| 0.15 | 24 / 24 | 1.00 / 0.00 | 0.99 / 0.00 | 0.85 / 0.00 | 0.33 / 0.12 |

Rho sensitivity at margin 0.075, 24 / 24, d = 0: rho 0.05 gives 0.96, 0.15 gives 0.85, 0.30 gives 0.73. The
constant 0.15 is still an estimate from 13 line-box decks; with 42 ink decks the within-canvas variance of
per-deck flag counts is close to binomial (counts are 0 or 1), so 0.15 is conservative for the ink epoch.

Reading the table with rare-event arithmetic: with 24 decks per arm the effective page count is
`144 / 1.75 = 82` per arm; the one-sided Wilson upper bound of 2 flags in 82 is about 0.06, so an equal canvas
only clears a margin of 0.05 about 60% of the time and a margin of 0.075 87% of the time. A margin of 0.05
needs 48 decks per arm for the same certainty. Doubling decks buys the margin roughly a factor `sqrt(2)`,
because the bound is dominated by the candidate arm's own count.

### Alternatives to the page flag rate, and why the primary metric stays

| metric | base rate or sd | more signal? | verdict |
|---|---|---|---|
| page flag rate (`quality_pages.flagged`) | 0.019 | rare | keep as primary: it is the product-visible verdict and the only one the gate publishes |
| first-pass failing pages (= retries per slide, `logicalCalls - 10`) | 0.065 | 3x the events, but the same decks: at margin 0.075, 24 / 24, equal-canvas pass is 0.56 (0.10: 0.77) because the rate's own variance grows with it | secondary gate at margin 0.10, as today; also the only retry-cost guard |
| per-page failure kinds (`required_text_missing`, `text_overlap`, ...) | 0.019 in total; 4 of 4 one kind | no: after the ink fix the classes collapse to one | informational, recorded per page (item 4) |
| composition metric (safe-area coverage, bottom empty band from `browser_metrics` text boxes) | per-deck sd unknown | a continuous deck mean at margin 0.5 sd passes an equal canvas only 0.55 of the time at 24 / 24 (0.68 at 36 / 36); it beats the rate only if the meaningful margin is above about 0.75 sd | informational until a per-deck sd exists (compute offline from `theme.svg.quality[].browser_metrics` of the 42 decks first) |
| seconds per slide | CV 9 to 16% | decides already | unchanged |

A continuous metric is not a free lunch: the normal bound on a mean difference has half-width
`1.645 x sd x sqrt(2 / n)`, 0.47 sd at 24 per arm, so it needs the same order of decks unless the accepted
margin is large in sd units. It is worth measuring because it is the defect the visual review actually
saw on 1:1 (a 10 to 20% empty band on covers and closing slides), which no flag counts.

### Proposed change

1. `TIERS` (`canvas_bench_assess.py:22-25`): `candidate` margin 0.20 -> **0.10** (18 / 12 decks, 3
   manuscripts, 1 day, unchanged); `verified` margin 0.15 -> **0.075** (24 / 24, 4 manuscripts, 2 days,
   unchanged). In pages: at `verified` a canvas may add at most 0.45 flagged pages per 6-slide deck on top of
   the baseline's 0.11; at `candidate` 0.6. Both are absolute constants, not fractions of the measured
   baseline: a fraction of 0.02 is not decidable at any affordable size, and an absolute margin keeps the
   verdict stable when the baseline rate moves between batches.
2. Retries per slide keeps its own margin, now decoupled from the primary: `RETRIES_MARGIN = 0.10` for
   both tiers (today it reuses `spec["margin"]`, `:195`). It stays a gate (design decision 5): it is the
   first-pass metric and the provider-cost guard.
3. Batch rule: keep two calendar days for `verified`, and tighten `_sample_gaps` (`:155-156`) so that (a)
   both arms, not only the candidate, need the tier's day count, and (b) a day counts only if it holds at
   least a quarter of that arm's decks (a lone deck at 23:59 UTC must not satisfy the rule). Replacing the
   rule with "two backend process lifetimes" or "two `--batch` labels" protects less: within-day drift is
   already shared by interleaving, and the day rule exists for provider-side changes that arrive by date.
   The cost is scheduling only.
4. `METRIC_CAP_DECKS` 36 -> 48 (`:30`): at margin 0.075 the 36 / 36 point still leaves a 4% chance that an
   equal canvas has not passed; 48 is where a further top-up stops paying.
5. Receipt: `metricEpoch` semantics unchanged. Add `rule: {"version": "canvas-bench-rule/v2", "margins":
   {...}}` to the compare output (`COMPARE_SCHEMA` stays `v2`; the block is additive) so a stage file
   change can be traced to the margins that produced it.

### What the existing 42 decks satisfy

Under the proposed margins, re-run offline on the pooled receipts (same code path as `compare`):

| candidate | D [lower, upper] | candidate tier (0.10, 18 / 12) | verified tier (0.075, 24 / 24, 4 manuscripts, 2 days) |
|---|---|---|---|
| 1:1 | -0.005 [-0.060, +0.069] | **pass** (upper 0.069 <= 0.10) | hold: decks 12 / 24, baseline 18 / 24, manuscripts 3 / 4, days 1 / 2 |
| 21:9 | -0.005 [-0.060, +0.069] | **pass** | hold: same shortfall |

So the promotions already in `canvas_stages.json` (`candidate` for both) remain valid under the tighter
candidate margin; nothing is demoted. Retries per slide: 1:1 upper +0.013, 21:9 +0.030, both under 0.10.

### Matrix and hours for `verified` on 1:1 and 21:9

One further `matrix` invocation on a second calendar day, same backend commit or a later one that does
not change the flagging rules (the epoch check refuses a mix), manuscripts 01 to 04 (`--limit 4`), seed
unchanged so the plan is resumable:

```
matrix --style default --slides 6 --limit 4 \
  --canvas-decks wide-16x9=24 --canvas-decks square-1x1=24 --canvas-decks ultrawide-21x9=24 \
  --batch <day2> --out artifacts/canvas-bench-20260929/pooled-candidate
```

`plan_matrix` (`scripts/canvas_bench_matrix.py:54-69`) assigns deck `j` to manuscript `j % 4`, repeat
`j // 4 + 1`, so with 4 manuscripts the existing receipts (`01..03-r01..r06` for 16:9, `01..03-r01..r04`
for the candidates) are all "done" and the pending set is exactly:

| canvas | pending decks | composition after the run |
|---|---|---|
| 16:9 | 6 (`04-r01..r06`) | 24 decks, 6 per manuscript (top share 25%) |
| 1:1 | 12 (`01..03-r05..r06`, `04-r01..r06`) | 24 decks, 6 per manuscript |
| 21:9 | 12 (same) | 24 decks, 6 per manuscript |
| **total** | **30** | 2 days on every arm; day 2 holds 25% of 16:9 and 50% of each candidate |

At the measured 6 minutes per deck (client wall median 285 to 330 s plus render) that is **3.0 h serial**.
Manuscript 04 is confounded with day 2 on every arm equally; that is acceptable for a non-inferiority
comparison because the confound is shared, and it is stated in the report. If the day-2 16:9 share is felt
to be too thin for interleaving, `wide-16x9=30` adds `01..03-r07..r08` (6 decks, 0.6 h): 36 decks, 3.6 h.

Expected verdict if 1:1 and 21:9 are truly equal to 16:9: pass with probability 0.87 each (0.76 both). If
one holds, the top-up to 36 / 36 (12 decks per arm, 1.2 h each) raises it to 0.96.

### Files, LOC, tests, acceptance, risks

- `scripts/canvas_bench_assess.py`: constants, `_sample_gaps` (both arms, quarter-share rule), `rule`
  block (about 25 LOC). `tests/unit/test_canvas_bench.py`: update the boundary tests at
  `:89-135` and `:143-160` to the new margins; add `test_day_rule_needs_a_quarter_share_on_both_arms`,
  `test_candidate_promotions_of_20260929_still_pass_under_the_new_margins` (regression on a fixture built
  from the 42 receipts' deck metrics, no artifacts needed), and a table-driven power assertion is out of
  scope for unit tests (simulation lives in the doc).
- Acceptance: the two existing compare outputs re-evaluated with the new code print `candidate: PASS`,
  `verified: HOLD` with the four shortfalls; `canvas_stages.json` unchanged by re-running `promote`.
- Risks: a tighter candidate margin makes a future `candidate` for a genuinely equal canvas fail to appear
  22% of the time at 18 / 12 (top-up to 24 / 18 gives 0.90); rho remains a constant, so re-estimate it from
  the 72 ink decks after this run and record it in the compare output.

## 2. Portrait gates (9:16, 9:13)

### Current behaviour

- Exposure of portrait is hard-coded, outside the stage file: `services/authored_canvas.py:38`
  (`PORTRAIT_RELEASE_VERIFIED = True`) and `:325-330` (`_base_aspect_ratios_for_style`: `default` gets
  9:16 and 9:13, `kbs-company-proposal` gets 9:13, every other style gets 16:9 only).
  `supported_aspect_ratios_for_style` (`:333-336`) appends gated ratios whose stage is not `disabled`.
- The stage file may contain exactly the gated ratios (`:311`, `set(document) != set(GATED_ASPECT_RATIOS)`
  raises) and `canvas_stage` refuses any other ratio (`:319-322`); `GATED_ASPECT_RATIOS` is `("1:1", "21:9")`
  (`:61`). The bench's `gated_ratio` (`scripts/canvas_bench_rules.py:47-57`) therefore refuses to promote
  `tall-9x16` or `tall-9x13`: it cannot even record a portrait stage.
- Admission order (`:339-386`): pack pin check, A4 branch, 16:9 short-circuit, stage gate for gated ratios
  (`:360-361`), then the source-restyle branch that admits **any** style on portrait (`:364-374`), then the
  free-design topic branch that consults `supported_aspect_ratios_for_style` (`:375-386`). The admission
  fixture (`tests/fixtures/canvas_admission_baseline.json`, 524 rows mention `9:16`) pins all of this.
- Registry (`constants/canvas_registry.json`): `tall-9x16` is visible (`hidden: false`, sortOrder 2) with
  `charsFactor 1.00` (its safe area 592 x 1152 equals 16:9's); `tall-9x13` is `hidden: true` with
  `charsFactor 0.79`; `a4-portrait` is visible with `charsFactor 1.00`. So the smaller outline budget
  applies to 9:13 only, not to 9:16 or A4 portrait.
- A4 portrait is **not** on `canvas_stages.json` at all and is not per style: `a4_writer_stage()`
  (`services/document_canvas_capabilities.py:130-135`) reads two constants,
  `DOCUMENT_CANVAS_A4_RELEASE_VERIFIED = False` and `DOCUMENT_CANVAS_A4_CANDIDATE_ENABLED = True`
  (`models/document_canvas.py:21-23`), giving stage `candidate` for both A4 orientations and every style,
  combined with the route policy (`_route_admitted`, `:142-144`). Its registry alias is `page_size` +
  `orientation`, so `gated_ratio` refuses it too.
- UI: `CanvasSelect.tsx:46` lists four preset rows (`tall-9x13` is not a row); `:147-156` prefers the
  backend's `supportedCanvases` entry when present and falls back to `hasCompanyStandard` and
  `supportedAspectRatios` otherwise; `:150` enables 9:16 in source-content mode regardless of style;
  `useUploadPageState.ts:161-171` resets the style to `default` when 9:16 is chosen with another style,
  and to `default`/`kbs` for 9:13, a hard-coded copy of the backend list.

### Proposed change: portrait on the same stage file, exposure unchanged

1. `GATED_ASPECT_RATIOS = ("9:16", "9:13", "1:1", "21:9")`; delete `PORTRAIT_RELEASE_VERIFIED` and
   `_base_aspect_ratios_for_style`'s style lists; `supported_aspect_ratios_for_style` becomes
   `[16:9] + [r for r in GATED if stage(r, style) != "disabled"]`.
2. Seed `constants/canvas_stages.json` with today's exposure:
   `{"9:16": {"default": "candidate"}, "9:13": {"default": "candidate", "kbs-company-proposal": "candidate"},
   "1:1": {...}, "21:9": {...}}`. `candidate` rather than `verified` because portrait was never benched
   (the `PORTRAIT_RELEASE_VERIFIED` comment at `:36-37` describes a provider and readback acceptance, not a
   quality bench) and because the stage is invisible to users: the styles endpoint publishes only
   `enabled` and `reason` (`utils/authored_style_catalogue.py:33-44`, `api/v1/ppt/endpoints/authored.py:40-50`).
   What users see is identical either way.
3. Preserve the restyle exposure. Today the stage gate at `:360` runs before the restyle branch, and for
   1:1 / 21:9 a disabled stage rejects restyle too (`test_canvas_stage_gate.py:83-89`). For portrait the
   restyle branch admits every style today, and the fixture pins it. Adding
   `RESTYLE_UNGATED_RATIOS = ("9:16", "9:13")` and making the gate at `:360` skip a restyle request on
   those ratios keeps every fixture row byte-identical. This is the one asymmetry the migration carries
   (open decision 3); it is named in code, not hidden in a style list.
4. Bench: `gated_ratio` needs no change once the ratios are in the file. Add `tall-9x13` to the promotable
   set implicitly (the registry alias `aspect_ratio: 9:13` resolves).
5. UI: drop the hard-coded fallbacks (`CanvasSelect.tsx:153-155` `hasCompanyStandard` test;
   `useUploadPageState.ts:161-171` style reset list) in favour of the `supportedCanvases` entry, which the
   component already prefers; keep the `sourceContentMode` shortcut at `:150` (it mirrors step 3). The
   9:13 row stays absent from the picker (registry `hidden: true`, design decision 4).
6. A4 portrait: **leave on its own switch for now.** The A4 mechanism carries route, writer and consumer
   readiness (`document_canvas_capabilities.py:142-144`), not only quality, and it is not per style. The bench
   can still measure `a4-portrait` (the pilot measured `a4-landscape`: 0 / 6 flagged), and `promote` will
   refuse it with the existing clear message. Unifying A4 under a stage file keyed by registry canvas id
   instead of ratio is the right end state (open decision 4) but is a separate change touching every
   `canvas_stage(aspect_ratio, ...)` caller.

### Bench matrix for portrait

Baseline: the 16:9 `default` pool from item 1 (24 decks after the day-2 run) is reused; the pool key is
style, strategy, epoch and slide count, not canvas (`canvas_bench_assess.py:90-111`).

| step | canvases | new decks | serial hours | earns |
|---|---|---|---|---|
| P1 candidate tier, `default` | `tall-9x16=12`, `tall-9x13=12` | 24 | 2.4 | candidate for 9:16 and 9:13 (today's exposure, now evidenced) |
| P2 verified tier, `default`, second day | `+12`, `+12` | 24 | 2.4 | verified for both |
| P3 `kbs-company-proposal` 9:13 (a per-style bench, item 3) | `wide-16x9=18` in kbs + `tall-9x13=12` | 30 | 3.0 | candidate for kbs 9:13 |
| **P1 + P2** | | **48** | **4.8** | |

Notes for the report: 9:13 runs at `charsFactor 0.79`, so its outline is shorter; since three of four
flags on 16:9 were `required_text_missing` on the budget slide, a lower flag rate on 9:13 would be a budget
effect as much as a canvas effect. That does not bias non-inferiority (it can only make 9:13 look better),
and the composition metric of item 1 is the check that shorter text is not simply leaving the page empty.
`tall-9x16` has the same safe-area as 16:9, so its result is the cleanest portrait signal. `a4-portrait`
can be appended (`a4-portrait=12`, 1.2 h) as its own stratum for information.

### Files, LOC, tests, acceptance, risks

- `services/authored_canvas.py` (about -15 / +20 LOC), `constants/canvas_stages.json` (+7 lines),
  `utils/authored_style_catalogue.py` (no change), `tests/unit/test_canvas_stage_gate.py` (`:53-58` shipped
  file assertion, `:92-106` expected lists, `:109-125` reasons: `tall-9x16` on `osb` becomes
  `canvas_stage_disabled` instead of `style_not_supported`; that string is user-facing text
  `canvasReasonStageDisabled`, an intentional wording change), `tests/unit/test_public_v2_admission_errors.py`
  (references `PORTRAIT_RELEASE_VERIFIED`), `scripts/canvas_admission_table.py` + regenerate the fixture,
  frontend `CanvasSelect.tsx`, `useUploadPageState.ts` and their tests (about 30 LOC).
- Acceptance: `tests/fixtures/canvas_admission_baseline.json` regenerates with **0 admitted / rejected
  flips and 0 code changes** (the byte-identical invariant of the admission spec decision 11), only the
  `reason` strings in `supportedCanvases` move for non-default styles on portrait; the styles endpoint's
  `supportedAspectRatios` is identical for every style before and after.
- Risks: a style file that lists a stage for a style id that no longer exists is silently inert (add a
  test that every key is a packaged style id or `default`); the frontend's `sourceContentMode` shortcut and
  the backend `RESTYLE_UNGATED_RATIOS` must stay in step (one test each, asserting the same tuple).

## 3. Per-style gates

### Current behaviour

- `canvas_stages.json` is keyed by style under each ratio; only `default` has a stage. A style not in the
  file is `disabled` (`authored_canvas.py:322`), so 1:1 / 21:9 on any other style returns
  `authored_canvas_stage_disabled` (422).
- The svg prompt's style dependence: `svg_style_adapter.py:24-39` `_MAPPINGS` hold reviewed guidance for
  `default`, `osb-smart-banking`, `hyundai-culture-center`; `:84-100` refuses any other style whose brief
  has format-specific lines without a same-YAML `resolved_sections` override. `project_style.py:55-69`
  emits `style_id`, `canvas`, `mapped_guidance`, the brief lines (rewritten for the canvas by
  `canvas_brief.py:33-60`), reference images with the off-baseline caveat (`:70-83`) and `svg_overrides`
  (`layout_hints`, `common_guidance`). Of these, `layout_hints`, reference images and composition phrases in
  `mapped_guidance` ("shared left edges and a dominant evidence field", "Emerald top band with a right
  tag") shape layout; palette and type tokens do not.
- 45 style YAMLs exist (`authored_styles/`).
- Usage evidence available locally is thin: 96 QualityReceipt ledgers under `app_data/authored-quality-
  receipts` parse, a style id is recoverable in 5 (`osb-smart-banking` 4, `liquid-executive` 1) and an
  aspect ratio in 5 (9:16 x3, 16:9 x2); the backend logs carry no style field. The shared database
  (`presentation.theme->>'style'`) is the real source and was not queried (shared stack, owner credential).

### Which styles first

1. `osb-smart-banking` and `hyundai-culture-center`: the only styles with reviewed svg mappings, the only
   ones the adapter admits without an override, and (osb) the only style with local usage evidence.
2. `kbs-company-proposal`: needed for the 9:13 migration of item 2 (P3).
3. Then by usage from the database query, once run.

### Inheritance from `default`: for and against

For: the svg author is told the canvas size and the shape-rewritten brief on every style
(`canvas_brief.py`), the outline budget is style-agnostic, and the composition guidance that differs by
style is a few sentences. If a style's projection differs from `default`'s only in palette, type and
imagery tone, the canvas behaviour is the same generator under different colours, and benching it again
measures the same thing.

Against: `layout_hints`, reference images and composition words are layout inputs, and the two reviewed
styles carry exactly those (osb: connectors and table headers; hyundai: top band, right tag, numbered
hierarchy). A style with an svg source template or references composed on 16:9 has its own anchoring risk
(the reference-caveat experiment exists for that reason). And the adapter refuses unmapped styles, so a
per-style bench on most of the 45 is not even runnable on svg today.

Rule proposed: **inheritance by projection, verified once.** `style_layout_signature(style_id, canvas)`
= digest of the layout-affecting subset of `project_style` output: `layout_hints`, reference image
digests, `mapped_guidance`, and brief lines whose section is a composition section (the sections the
canvas-brief rewrite touches, R1..R9 anchors). If a style's signature equals `default`'s on that canvas,
`canvas_stage(ratio, style)` returns `default`'s stage with provenance `inherited`; otherwise the style needs
its own entry. The signature is computed at request time from the packaged catalogue (cached), so no file
lists inheriting styles by hand. Before enabling inheritance, run one candidate-tier bench (12 decks, 1.2 h,
baseline reused) on one inheriting style to check that its flag rate is indistinguishable from `default`'s
on that canvas; if it is not, inheritance is dropped and the doc records why.

Matrix and hours for a style that does not inherit (16:9 in that style is not benched either, so the
baseline is new):

| step | decks | serial hours |
|---|---|---|
| candidate tier, one style, both canvases: `wide-16x9=18`, `square-1x1=12`, `ultrawide-21x9=12` | 42 | 4.2 |
| verified top-up, second day: `+6`, `+12`, `+12` | 30 | 3.0 |
| **one style through `verified`** | **72** | **7.2** |
| **osb + hyundai through `verified`** | **144** | **14.4** |

Bench admission: the pooled run edited `canvas_stages.json` in the bench worktree so disabled stages were
admitted. Replace that with `CANVAS_STAGES_PATH` read from the environment (`authored_canvas.py:62`, one
line, default unchanged) so a bench backend points at a bench stage file and the shipped file is never
edited by hand.

### Files, LOC, tests, acceptance, risks

- `services/authored_canvas.py` (env override, 2 LOC; inheritance lookup, about 30 LOC),
  `services/generation_core/drivers/svg/project_style.py` (signature function, about 20 LOC),
  `utils/authored_style_catalogue.py` (`reason: "inherited"` is not needed; `enabled` suffices),
  tests: `test_canvas_stage_gate.py` (inherited stage, non-inheriting style stays disabled, signature
  stability golden per style), `test_svg_style_adapter.py` (signature excludes palette and type tokens).
- Acceptance: the admission fixture shows only the intended rows moving (styles whose signature equals
  `default`'s, on 1:1 / 21:9); the verification bench passes at the candidate tier.
- Risks: the signature is only as good as the section classification of brief lines; a wrongly classified
  palette line makes a style non-inheriting (safe direction), a wrongly classified layout line makes it
  inherit (unsafe). Keep the classification list explicit and tested.

## 4. `overlap_basis` exposure

### Current behaviour

- `services/svg_renderer.py:309` computes `overlaps, overlap_basis = text_overlaps(text)` and `:341` stores
  the object in the page quality: `{"version": TEXT_INK_PROBE_VERSION, "fragments": {ink: n, line_box: n},
  "pairs": [...]}` (`services/svg_text_overlap.py:83`). `SVG_RENDERER_VERSION` stays `svg-chromium/v1`
  (`:16`).
- Publication stores that quality per page in `slides[].content.svgReceipt.quality` (`publish.py:30-40`;
  `attempts` is stripped there) and as the deck list `theme.svg.quality[]` (`:108-118`). The operation
  result carries only `quality_status`, `quality_pages`, paths (`:144-151`), and the async status copies
  those (`services/presentation_generation_completion.py:20-36`).
- The bench looks for a **string** `overlap_basis` in the operation result (`scripts/canvas_bench.py:149`,
  `scripts/canvas_bench_epoch.py:43-61`), never finds one, and falls back to git ancestry
  (`resolve_run_epoch`, `:81-93`), which needs `--backend-commit` and `--ink-fix-commit` known to the local
  git and is wrong for a dirty or detached worktree.
- The bench already fetches `GET /presentation/{id}` (`canvas_bench.py:137`) and reads `theme` from it
  (`:148`).

### Proposed change

Phase A, bench only, no backend change: `find_overlap_basis` accepts the object form (returns
`version`) and the runner scans, in order, the operation result and then `presentation["theme"]["svg"]
["quality"]`; every page's version must agree, otherwise the deck is `unmeasured` for the primary metric
with reason `mixed_overlap_basis`. `observed_epoch` maps `glyph-ink/*` to `INK_EPOCH` and anything else to
`LEGACY_EPOCH`. Receipts record `overlapBasis: {"version": ..., "pages": n}`. The ancestry path stays as
the last fallback and prints a warning; `--metric-epoch` still wins when explicit.

Phase B, backend, additive and contract-safe: the svg publish result gains one deck-level block,

```
"quality_measurement": {"rendererVersion": "svg-chromium/v1", "overlapBasisVersions": ["glyph-ink/v1"],
                        "classificationRule": "page-outcome/v1"}
```

built from the per-page qualities at `publish.py:107`, and the same block goes into
`theme.svg.qualitySummary`. The backend does not know bench epochs; it states measurement facts and the
bench maps them. Keys are additive on a free-form result dict (no test under `tests/` asserts the result's
key set; `quality_pages` has no consumer test), and the timeline item schema in
`contracts/chat-first-workspace/v1` types `result` as an object. A per-page `attempts` count
(`quality.py:21-28` computes it, `publish.py:35` drops it) is added to the same summary as
`firstPassFailingPages`, which is what item 1's secondary metric measures today through `logicalCalls`.

### Files, LOC, tests, acceptance, risks

- Phase A: `scripts/canvas_bench_epoch.py` (about 25 LOC), `scripts/canvas_bench.py:149-151` (about 10),
  tests `test_overlap_basis_is_found_in_nested_results_and_maps_to_epochs` extended for the object form,
  the theme path and the mixed-version refusal.
- Phase B: `services/generation_core/drivers/svg/publish.py` (about 15 LOC), `tests/unit` svg publish test
  asserting the block and its equality with the per-page versions; the bench prefers it when present.
- Acceptance: a `matrix` run started without `--ink-fix-commit` on a detached worktree labels every deck
  `svg-overlap-ink-v1` from observed data; `compare` on the 42 existing receipts is unchanged (their
  `metricEpoch` is already set).
- Risks: a renderer that records ink for some pages and line boxes for others (the probe falls back per
  fragment, `svg_text_overlap.py:40-44`) is a `mixed` basis at pair level but one `version`; the epoch
  keys on `version`, and the fragment counts are kept in the receipt for inspection.

## 5. Bench runner sleep and stall detection

### Current behaviour

- The poll loop (`scripts/canvas_bench_live.py:62-72`) sleeps 5 s (`:22`) between status reads and only
  knows a `time.monotonic()` deadline (`--timeout`, 3,600 s). `clientWallSeconds` is `time.time()` around
  `generate` (`canvas_bench.py:113-115, 125`). The matrix runner treats only transport errors as
  infrastructure (`canvas_bench_matrix.py:25, 120-123`) and stops the run on one (exit 3).
- The backend timing receipt is built from `time.monotonic()` (`services/authored_generation_timing_
  receipt.py:12, 1157-1226`). During the 2026-09-29 standby the content-brief policy logged its 600 s
  timeout as elapsed after 3,226 s (pooled report, finding 3; the lost deck's own receipt was overwritten
  by the rerun and survives only as the `previousInvalid` note), so the backend's monotonic clock did not
  stop either: on Windows `time.monotonic()` (GetTickCount64 on the 3.11 runtime) and the asyncio loop
  keep counting through standby and simply fire late on resume. A wall-clock versus monotonic drift
  check therefore does **not** detect standby on this platform; both clocks advance.
- No power guard exists in the scripts; the session held `SetThreadExecutionState` by hand.

### Proposed change

`scripts/canvas_bench_watch.py` (new, about 120 LOC, stdlib + ctypes, no-op off Windows):

1. `SuspendProbe`: on Windows reads `QueryUnbiasedInterruptTime` (excludes sleep and hibernate) and
   `QueryInterruptTime` (includes them) at deck start and at every poll; `sleptSeconds = biased_delta -
   unbiased_delta`. Any `sleptSeconds > 30` during a deck marks it. Off Windows the probe reports `None`.
2. `PollGapMonitor`: the gap between consecutive poll iterations minus the request time; expected about
   `POLL_SECONDS`; a gap above 60 s (12 polls) marks a suspend or a frozen process. Portable, catches the
   case the kernel primitive misses (a VM pause, a debugger).
3. `BackendOverrun`: from the receipt, any stage `wallMillis` above the stage's configured timeout
   (content brief 600 s; the policy line already prints `timeout=` and `elapsed=`), or `clientWallSeconds`
   more than 3x the running median of finished decks with a floor of 900 s. This one is a cross-check and is
   informational unless it agrees with 1 or 2 (it cannot tell a slow provider from a suspend).

Decision: a deck with 1 or 2 fired is written as `invalid: {"reason": "infra_lost", "note": "<probe>:
slept 3,171 s ..."}` with a `clock` block (`sleptSeconds`, `maxPollGapSeconds`, `overrun`), the runner
**continues** with the next deck after a health probe (`GET /api/health` on the backend origin succeeds
twice, 10 s apart), and the lost deck is appended to the end of the plan so the same invocation reruns it
(`is_done` already treats an invalid receipt as pending, `canvas_bench_matrix.py:84-92`; only the ordering
changes). A transport error keeps today's stop-and-resume behaviour: the backend may be gone.

`power_guard()` context manager in `scripts/canvas_bench.py` around `cmd_run` and `cmd_matrix`: on Windows
`PowerCreateRequest` with the reason `canvas bench: <out dir>` and `PowerSetRequest(PowerRequestSystemRequired)`
so `powercfg /requests` shows who holds it; fallback to `SetThreadExecutionState(ES_CONTINUOUS |
ES_SYSTEM_REQUIRED)` on the main thread (the request is per thread and the serial runner's main thread
lives for the run); cleared in `finally` and on `KeyboardInterrupt` with `PowerClearRequest` or
`SetThreadExecutionState(ES_CONTINUOUS)`. The guard blocks the idle path into Modern Standby; it does not
block a lid close, a power button or a battery-critical sleep, which is why detection stays. The runner
logs the guard state at start, and the receipt's `clock` block records `powerGuard: "power_request" |
"execution_state" | "none"`.

### Files, LOC, tests, acceptance, risks

- `scripts/canvas_bench_watch.py` (new), `scripts/canvas_bench_live.py` (poll hooks, about 15 LOC),
  `scripts/canvas_bench_matrix.py` (continue-and-requeue, about 20 LOC), `scripts/canvas_bench.py`
  (guard, receipt `clock` block, about 15 LOC).
- Tests (`tests/unit/test_canvas_bench.py`): fake clocks injected into the probe (slept 0 vs 3,000 s);
  poll-gap monitor with a monkeypatched `time.monotonic`; runner marks, continues and requeues, and a
  second invocation reruns the marked deck keeping `previousInvalid`; guard is a no-op off Windows and
  clears on exception; `BackendOverrun` alone never invalidates.
- Acceptance: a synthetic replay of the 2026-09-29 loss (client wall 3,396 s with a 3,171 s slept
  interval inside one poll gap, content-brief elapsed 3,226 s against a 600 s timeout, as the pooled
  report records it) through the monitor yields `infra_lost` from probe 1 or 2 and `overrun: true`; the
  41 surviving receipts yield nothing.
- Risks: `QueryUnbiasedInterruptTime` needs Windows 8+ (fine); a backend that resumed but is still
  timing out the suspended deck can make the next deck slow (the health probe does not see provider state),
  so the runner waits for the lost deck's task to reach a terminal status (up to 10 minutes) before
  continuing, to keep the bench's one-deck-at-a-time invariant that ledger attribution relies on
  (`canvas_bench_live.py:179-186`).

## Order of work and total cost

1. Item 4 phase A and item 5 (bench only, no product change): about 200 LOC, run before any further deck.
2. Item 1 constants and the day-2 run for 1:1 / 21:9 `verified`: 30 decks, 3.0 h.
3. Item 2 migration (product change, fixture byte-identical), then P1 + P2: 48 decks, 4.8 h.
4. Item 4 phase B with the next backend change that touches svg publish.
5. Item 3: env override first (needed by every per-style run), inheritance rule with its one verification
   bench (12 decks, 1.2 h), then osb and hyundai if they do not inherit: 144 decks, 14.4 h.

Bench time through `verified` for 1:1, 21:9, 9:16 and 9:13 on `default`: 78 decks, 7.8 h serial over two
further calendar days. Per non-inheriting style: 7.2 h more.

## Open decisions

1. Margins 0.10 / 0.075 absolute (item 1), or 0.05 at `verified` with 48 decks per arm (+2.4 h per canvas)?
2. Portrait migrates as `candidate` (honest, invisible to users) or `verified` (matches the retired flag's
   name)? `promote` never lowers a stage, so `verified` could not be walked back by the bench.
3. Keep the restyle exemption for portrait (`RESTYLE_UNGATED_RATIOS`), extend it to 1:1 / 21:9 (looser
   than today), or gate portrait restyle too (changes fixture rows for kbs, osb, hyundai)?
4. A4 portrait: stay on the A4 switch, or re-key `canvas_stages.json` by registry canvas id so A4 and
   presets share one gate (touches every `canvas_stage` caller and the A4 route policy)?
5. Inheritance by projection signature: adopt after the one verification bench, or require a bench per
   style regardless?
6. Which usage query to run on the shared database, and by whom, to order the per-style list.
7. Item 5: on a detected suspend, continue after the lost deck's task is terminal (proposed) or stop as
   today and rely on rerun?

## Self-score

7 / 10. Strong: every number is from the 42 receipts or a stated simulation using the shipped statistics
module; the matrix plans are derived from `plan_matrix`'s actual round-robin so the pending sets are exact;
the standby analysis is grounded in the backend log evidence rather than an assumed clock model. Weak:
rho for the ink epoch is not re-estimated (the counts are almost all 0 or 1, which is why I claim 0.15 is
conservative, but I did not compute it); the composition metric is proposed without a measured sd; style
usage evidence is thin because the shared database was not queried; the inheritance signature's section
classification is described, not enumerated against the 45 YAML files; and phase B's "no consumer test
asserts the result keys" was checked by grep of `tests/`, not by running the suite.
