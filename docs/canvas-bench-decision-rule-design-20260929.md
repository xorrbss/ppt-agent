# Canvas bench decision rule: design (2026-09-29)

Status: design only. No code was changed. Author's data: every `*.receipt.json` under
`artifacts/canvas-bench-20260929/` (pilot plus `sq-uw/round1`, `sq-uw/round2`; 13 decks, all
6 slides, style `default`, strategy svg). Simulation and statistics were run in a scratch
directory with the Python stdlib only; the numbers below are reproducible from the receipts.

Related: `docs/canvas-bench-pilot-20260929.md`, `docs/canvas-bench-square-ultrawide-20260929.md`,
`docs/aspect-ratio-expansion-design-20260928.md` section 8. At the time of writing there is no
`svg-text-overlap-false-positives-design-20260929.md`; section 3.7 makes the rule valid whether
or not that fix lands.

## Implementation (2026-09-29)

Implemented with the section 8 defaults accepted: margins 0.20 / 0.15, rho 0.15 constant, retries kept as a
gate, serial runs. Code: `servers/fastapi/scripts/canvas_bench_stats.py` (Wilson, Newcombe, design effect),
`canvas_bench_assess.py` (pooling, tiers, verdicts `pass`/`fail`/`hold`, `canvas-bench-compare/v2`),
`canvas_bench_epoch.py`, `canvas_bench_matrix.py` (unattended resumable `matrix` runs), `canvas_bench_rules.py`
(promotion from the pooled `earnedStage`), tests in `tests/unit/test_canvas_bench.py`. Manuscripts 03
(retail loyalty) and 04 (clinical data security) were added beside 01 and 02.

- **Metric epoch.** Receipts carry `metricEpoch`; a receipt without one is `svg-overlap-linebox-v1`
  (all pilot and sq-uw data). The text_overlap ink-box fix records `overlap_basis` and does not bump
  `SVG_RENDERER_VERSION`, so the version cannot separate the epochs. A run labels its decks
  `svg-overlap-ink-v1` or `svg-overlap-linebox-v1` by: (1) an `overlap_basis` found in the generation
  operation's published result, per deck, stored as `overlapBasis`; (2) `--metric-epoch` explicit;
  (3) `--ink-fix-commit` being an ancestor of `--backend-commit` (`git merge-base --is-ancestor`). With none
  of these the run refuses to start. `compare` pools one epoch (default: the newest candidate deck's) and lists
  the other-epoch decks under `excluded`. Gap: whether the backend exposes `overlap_basis` in the operation
  result is unverified, so the ancestry or explicit path is the reliable one until it is.
- **Data gaps.** `gate.flags.flaggedSlides` is still empty, so the primary metric uses deck-level
  `flaggedPages` over `slideCount`; per-page classifications and attempts remain unrecorded. Per-manuscript
  guard (open decision 6) stays informational (no rule), `fail` only blocks promotion.
- **Semantics chosen where the design was open.** Hard gates (PPTX size, generation and export failures among
  candidate decks) give `fail` regardless of sample size; otherwise an insufficient sample, more than 10%
  unmeasured decks, or any secondary gate that cannot be decided gives `hold`. A secondary gate fails only when
  its lower bound is beyond the margin. `promote` never lowers a stage: a lower earned stage prints "stays" and
  exits 0. `localFitUnresolved` is informational (not gated) and `not_applicable` for svg.
- **Re-evaluation of sq-uw** with the new `compare`: 1:1 D +0.19 [-0.09, +0.44], 21:9 D -0.04 [-0.29, +0.25];
  both `hold` (insufficient sample), earned stage `disabled`, as section 6 predicted.

## 1. The current rule and why it fails

`canvas_bench_rules.compare()` compares one candidate deck set with the same-round 16:9 deck
set (2 decks per side, one per manuscript). It passes when every defect aggregate
(`local_fit_unresolved`, `gate_blockers`, `export_failures`, `generation_failures`) is <= the
baseline total, `per_slide_seconds` and `retries` are <= 1.15 x the baseline total, and every
PPTX size matches. `promotion_stage()` gives `candidate` for one passing compare and `verified`
for two consecutive passing compares.

What the data says about that rule:

1. **`gate_blockers` is a step function of one thing.** In all 11 svg decks measured on the
   round commit, blockers = 3 exactly when flagged pages >= 1, else 0. `authoring`,
   `geometryFont` and `visualReview` fail together. (The pilot A4 deck shows 3 blockers with 0
   flagged pages: the pilot commit predates the wiring, so the value was not even a stable
   function of quality then.) The metric therefore has two values per deck and adds nothing
   beyond "any page flagged".
2. **The baseline swings as much as the effect we look for.** 16:9 decks flagged 3, 0 (round
   1), 1, 2 (round 2), 2 (pilot) pages of 6. Same-round baseline blocker totals were 3 and 6.
   The candidate limit is the baseline total, so the limit itself moved 2x between rounds.
3. **Retries are the flag count in disguise.** Across all 13 decks the correlation of retries
   with flagged pages is 0.92, retries minus flagged pages is 0 to 2 (median 1), and the timing
   receipt's `logicalCalls` equals 10 + retries in 13 of 13 decks. Retries are provider retry
   calls counted per deck (`attempts - 1` is always 0 here); the receipts carry no per-page
   attribution. Gating flags and retries separately double-counts one signal.
4. **The rule has almost no discriminating power.** Simulated with a beta-binomial per-deck
   flag count calibrated on these decks (page rate 0.27, intra-deck correlation 0.15) and
   2 decks per side: a candidate identical to 16:9 passes one round 54% of the time and earns
   `verified` (two consecutive passes) 29% of the time. A candidate whose true page flag rate is
   0.46 (the observed 1:1 rate) still passes a round 28% of the time and earns `verified` 8% of
   the time. The gap between "same" and "19 points worse" is 29% vs 8%. A verdict from this rule
   is mostly luck, in both directions.
5. **What it said on the sq-uw data:** 1:1 round 1 FAIL, round 2 FAIL, 21:9 round 1 FAIL,
   round 2 not run. Under the null model above, an equal canvas fails a given round 46% of the
   time, so those FAILs are not evidence of a defect either.

## 2. Data available and gaps

Per deck, in `canvas-bench-receipt/v1`:

| available | where |
|---|---|
| slide count, generation ok, export ok, PPTX `p:sldSz` vs registry | `slideCount`, `generation`, `export` |
| total, per-slide seconds; validity of the timing receipt | `timing` (`wallMillis`; `rc_invalid` in the pilot only) |
| stage durations: outline, authoring, chromiumReview, publication | `timing.receipt.stages` (valid in the round-1/2 decks) |
| provider retry calls, logical calls | `retries`, `timing.receipt.logicalCalls` |
| flagged and passed page counts | `gate.flags.flaggedPages`, `gate.flags.passedPages`, `gate.flagCount` |
| blocker list and v3 dimension states | `gate.blockers`, `gate.v3Dimensions` |
| client wall time (missing on the recovered deck `92dfbce7`) | `clientWallSeconds` |

Not available, and needed by the design:

- **Flagged page indices and classifications.** `gate.flags.flaggedSlides` is an empty list in
  every deck, even where `flaggedPages` is 4. Classification (`text_overlap` etc.) was read from
  the live backend during the session and is not in the artifacts. The design does not depend
  on it for the verdict, but the receipt should carry it (section 6).
- **Per-page attempts/retries.** Only per-deck totals exist.
- **Round/batch identity and a metric epoch.** Rounds can only be inferred from the directory
  name; nothing records which flagging rules produced the count.
- **A record of why a deck is missing.** Round-2 21:9 manuscript 02 was lost to a machine
  restart and appears only in prose.
- **Only two manuscripts** exist (`tests/fixtures/canvas_bench_manuscripts`).

## 3. Noise from the existing 16:9 decks

16:9 baseline: 5 decks (pilot 1, round 1 two, round 2 two), 30 pages. The pilot ran on commit
`28daa284e`, the rounds on `115b9263a`.

| metric | values per deck | mean | spread |
|---|---|---|---|
| flagged pages of 6 | 2, 3, 0, 1, 2 | 8/30 = 0.267 per page | Wilson 95% [0.142, 0.444]; per-deck rate sd 0.19 (1.14 pages). Without the pilot: 6/24 = 0.25 |
| retries per slide | 3, 3, 1, 3, 2 (per deck) | 0.40 | sd 0.15 |
| seconds per slide | 55.9, 63.6, 49.8, 57.8, 59.5 | 57.3 | sd 5.1 (CV 9%) |

Other numbers that shape the design:

- **Outline is a fixed cost.** 11 decks with a valid stage receipt: outline 160 s, sd 16 s
  (130 to 181 s), about 47% of wall time, independent of canvas. Total per-slide seconds is
  therefore a low-variance metric (CV 9%); the post-outline part alone would be noisier
  (CV about 21%), so the rule keeps the product-visible total.
- **Deck clustering.** Flag counts per deck are overdispersed relative to a binomial: across
  all 13 decks the variance is 2.03 against 1.25 (ratio 1.62), i.e. an intra-deck correlation
  of about 0.12 (`(ratio - 1) / (6 - 1)`). Within-canvas only: 1.85 vs 1.25. Six pages of one
  deck are not six independent draws. n = 13 makes this estimate rough; the design uses 0.15
  (design effect 1 + 5 x 0.15 = 1.75) and re-estimates it once 40+ decks exist.
- **Manuscript x canvas structure.** 1:1 manuscript 02 flagged 4 of 6 twice; 16:9 manuscript 02
  flagged 0 and 2. Manuscripts are a clustering level too, which is why the rule requires a
  minimum number of manuscripts.
- **Candidate decks, for reference:** 1:1 3, 4, 0, 4 flagged (11/24 = 0.46, Wilson [0.28, 0.65]);
  21:9 2, 1, 1 (4/18 = 0.22, [0.09, 0.45]); A4 0/6.

## 4. Proposed rule

### 4.1 Unit, pool and metrics

- **Primary metric: page flag rate** = flagged pages / pages, pooled over decks, where a page is
  flagged by the gate's final classification (`gate.flags.flaggedPages`). Unit of inference is
  the deck (design effect, below). Retries and blockers are not primary metrics.
- **Baseline is a pool, not a same-round pair.** All 16:9 decks with the same `style`,
  `strategy`, `metricEpoch` and slide-count profile, across all rounds. Candidate decks are
  pooled the same way across rounds. Interleaving (section 5) keeps time drift shared.
- **Tiers replace "two consecutive passes"**, because the sample, not the number of looks,
  decides:

| | `candidate` | `verified` |
|---|---|---|
| margin delta on page flag rate | 0.20 | 0.15 |
| candidate decks (pooled) | >= 12 | >= 24 |
| baseline decks (pooled) | >= 18 | >= 24 |
| manuscripts (each side) | >= 3, no manuscript > 40% of decks | >= 4, same balance |
| batches (separate runs, different calendar days) | >= 1 | >= 2 |

### 4.2 Statistic

Non-inferiority on the difference of page flag rates, `D = p_cand - p_base`.

- Each arm's rate uses the **Wilson score interval** on design-effect-adjusted counts
  (`k / deff`, `n / deff`, `deff = 1 + (m - 1) x rho`, m = pages per deck, rho = 0.15). The
  difference bound is **Newcombe's hybrid score interval** (method 10), one-sided 95%
  (z = 1.645).
- Why this and not the alternatives: Wilson/Newcombe behave at small and zero counts (A4 has 0
  of 6), need only `sqrt`, and match the "simple, explainable" brief; exact Clopper-Pearson
  needs a beta inverse and is conservative at these sizes; a deck-level bootstrap is unstable
  with 5 to 12 decks per side and a skewed 0..1 metric; a deck-level t test needs a t
  quantile table. `canvas_bench_rules.py` stays pure stdlib.
- **Pass:** upper bound of `D` <= delta. **Fail:** `D >= delta` and the lower bound > 0 (worse
  than the baseline with evidence, and by more than the margin). **Inconclusive:** anything
  else; run more decks, up to a cap of 36 candidate decks per tier, after which the stage is
  simply not earned.

Justification of the margins: at the pooled baseline rate of 0.27, 0.20 is +74% relative and
0.15 is +55%, i.e. "no more than about 1.2 (candidate) or 0.9 (verified) extra flagged pages
per 6-slide deck on top of the baseline's 1.6". Smaller margins are not decidable at an
affordable size (section 5). The verified margin is the smallest one the sample plan can pass
with >= 70% probability when the canvas is truly equal.

### 4.3 Secondary gates

- **Retries per slide** (deck mean): same margin delta (a retry per slide moves like a flagged
  page, given r = 0.92), tested with the normal upper bound on the difference of deck means,
  `D + 1.645 x sqrt(s_b^2 / n_b + s_c^2 / n_c)`, requiring n >= 12 per arm (a t quantile at
  df 20 would be 4% wider). It is kept because it also catches provider-retry cost that does not
  come from flagged pages.
- **Seconds per slide** (deck mean of `timing.perSlideSeconds`): upper bound of the difference
  <= 0.15 x baseline mean (the current 1.15 factor, now applied to a bound rather than a
  point). With CV 9% and n = 12 vs 24 the half-width is about 3 s against a 8.6 s margin, so
  this gate decides. Decks with an invalid timing receipt fall back to `wallMillis` (as the
  pilot did) but only if both arms use the same source.
- **Hard gates, no statistics:** PPTX size mismatch = 0; generation and export failures = 0
  among candidate decks unless the operator records the deck as `invalid` (below). With
  n decks and zero failures the one-sided 95% upper bound of the failure rate is about 3/n
  (25% at 12, 12.5% at 24), which is the same order as the margins.

### 4.4 `unmeasured` and `not_applicable`

- `not_applicable` is declared per strategy in one table (svg: `localFitUnresolved`;
  authored: none), not inferred from absence in both arms. A value absent where the table says
  it must exist is `unmeasured`.
- A deck missing a metric is **excluded from that metric's pool** and counted in
  `unmeasuredDecks`. If more than 10% of decks on either side are unmeasured for the primary
  metric, the verdict is `inconclusive` with reason `too_many_unmeasured`. An unmeasured
  candidate never counts as a pass.
- A receipt may carry `invalid: {reason: "infra_lost", note: ...}` set by the operator for
  runs killed by the environment (the lost 21:9 deck). Invalid decks are listed, excluded from
  all pools, and do not count towards minimum samples. No other reason is accepted; product
  failures stay in the denominator.

### 4.5 Promotion

`promotion_stage()` is computed from the pooled assessment, not from a compare sequence:
`verified` if the verified tier passes, `candidate` if the candidate tier passes, else no
change. Fail or inconclusive never lowers a stage (unchanged from `apply_promotion`, which
already refuses downgrades). If a `verified` canvas later fails a fresh verified-tier
assessment, that is a manual demotion decision.

### 4.6 Metric epoch

Receipts gain `metricEpoch`, a string that changes when the gate's flagging rules or the
svg quality classification change. Pools never mix epochs. When the text_overlap false-positive
fix ships, the epoch bumps, the 16:9 baseline is re-measured, and the margins can be re-derived
from the new baseline rate (open decision 3). With a lower baseline rate (0.10) the same plan
still works: at 24 vs 24 decks and a 0.10 margin the equal-canvas pass probability is 67%,
and at 0.20 with 12 vs 12 it is 89%. The metric itself is valid either way because it counts
flagged pages under the current gate, not a hand-picked defect class; per-class counts are
recorded but are informational only (no excluding a class after seeing the result).

## 5. Sample plan and cost

Simulation (beta-binomial decks of 6 pages, rho 0.15, 1,500 to 4,000 replicates; probabilities
of `pass` for a candidate whose true rate is baseline + d, baseline 0.27):

Candidate tier (delta 0.20):

| baseline / candidate decks | d = 0 | d = 0.05 | d = 0.10 | d = 0.20 |
|---|---|---|---|---|
| 12 / 12 | 0.65 | 0.45 | 0.27 | 0.06 |
| 18 / 12 (plan) | 0.73 | 0.50 | 0.27 | 0.06 |
| 24 / 18 | 0.85 | 0.62 | 0.36 | 0.05 |

Verified tier (delta 0.15):

| baseline / candidate decks | d = 0 | d = 0.05 | d = 0.10 | d = 0.15 | d = 0.19 (observed 1:1) |
|---|---|---|---|---|---|
| 24 / 24 (plan) | 0.72 | 0.43 | 0.18 | 0.05 | 0.01 pass, 0.72 fail |
| 30 / 30 | 0.79 | 0.47 | 0.19 | 0.05 | 0.02 pass, 0.73 fail |
| 40 / 40 | 0.89 | 0.59 | 0.22 | 0.06 | |

The false-pass rate at the margin is 5% by construction. The price of a decision-grade rule is
that certifying a small margin needs tens of decks: at 6 slides per deck, verifying at 0.15
with >= 85% power needs about 40 decks per arm, so the plan takes the 24 / 24 point (72%)
and accepts that a truly equal canvas needs a top-up about a quarter of the time. Adding
decks helps slowly because pages in a deck are correlated (design effect 1.75): more decks, not
longer decks, is what buys information, and longer decks would only amortise the fixed outline
cost.

**Cost per deck:** mean client wall 334 s (12 decks; 265 to 393 s), plus render and harness,
so 6 minutes per deck serial, one deck at a time (the bench requires a quiet backend; memory
pressure killed a run last time). Concurrency 2 would halve wall time but is untested here and
would inflate seconds per slide; if used, interleave arms so contention is shared.

| step | new decks | serial hours |
|---|---|---|
| 2 new manuscripts (fixtures, one-off; no generation) | 0 | about 1 h authoring |
| Candidate tier, 1:1 only: baseline 13 (existing 5 reused if the epoch has not changed) + 12 candidate | 25 | 2.5 |
| Candidate tier, 1:1 and 21:9: baseline 13 + 12 + 12 | 37 | 3.7 |
| Verified tier top-up for both: baseline +6, each canvas +12 | 30 | 3.0 |
| **Both canvases, both tiers** | **67** | **6.7** (about 3.5 h at concurrency 2) |

If the text_overlap fix lands first, the epoch bumps and the existing 5 baseline decks stop
counting: add 5 decks (0.5 h) to each line that includes baseline.

**The smallest matrix that can decide:** 4 manuscripts x 3 repeats for 16:9 (12 decks) and 4 x 3
for the candidate (12 decks), run interleaved and in randomised order, plus 6 more 16:9 decks
(18 baseline). That decides the candidate tier for one canvas in 30 decks, or 25 if the
existing 5 are reusable, about 2.5 hours. Start with 1:1: it is the only canvas whose data
suggests a real difference, and a "fail" verdict at the candidate tier arrives only when the
effect is large (about 0.20 or more), which is exactly the case worth knowing early.

## 6. Re-evaluation of the sq-uw data under the proposed rule

Baseline pool: the 5 existing 16:9 decks (8/30, 0.267). Deff 1.75, delta 0.20, one-sided 95%.
The pilot deck is included; excluding it (6/24) changes no verdict.

| canvas | decks / pages | flags | D (candidate - baseline) | 95% one-sided bounds of D (cluster-adjusted; unadjusted) | retries per slide, D and upper | seconds per slide, D and upper | verdict |
|---|---|---|---|---|---|---|---|
| 1:1 | 4 / 24 | 11 | +0.19 | [-0.09, +0.44]; [-0.02, +0.39] | +0.23, +0.51 | -7.0 s, -0.7 s | insufficient sample (needs 12 candidate, 18 baseline, 3 manuscripts; has 4 / 5 / 2) |
| 21:9 | 3 / 18 | 4 | -0.04 | [-0.29, +0.25]; [-0.23, +0.18] | -0.07, +0.04 | -3.5 s, +4.3 s | insufficient sample (3 / 5 / 2) |
| A4 | 1 / 6 | 0 | -0.27 | [-0.47, +0.20]; [-0.42, +0.06] | not computable (1 deck) | not computable | insufficient sample (1 deck) |

Reading: no canvas can be called passing or failing. The 1:1 point estimate is worse
(0.46 vs 0.27), but even without the design effect its lower bound is below zero, so "1:1 is
worse" is not established either; the existing FAILs were noise-level. 21:9 is indistinguishable
from 16:9, and 1:1 seconds per slide are lower than 16:9's with the bound below zero. The exports
are 100% size-correct on all 13 decks; that hard gate passes.

## 7. Code changes needed (not made)

`servers/fastapi/scripts/canvas_bench_rules.py`
- Constants: `TIERS = {"candidate": {...}, "verified": {...}}` (margin, min decks, min
  manuscripts, min batches), `RHO = 0.15`, `Z_ONE_SIDED = 1.645`, `NOT_APPLICABLE = {"svg":
  {"localFitUnresolved"}, ...}`, `METRIC_CAP_DECKS = 36`.
- New pure functions: `deck_metrics(receipt)`, `pool(receipts, canvas)` (applies `invalid`,
  epoch, style, strategy filters), `wilson(k, n, z)`, `newcombe_diff_bounds(...)`,
  `mean_diff_upper(base, cand, z)`, `assess(baseline, candidate, tier)` returning
  `{"verdict": "pass"|"fail"|"inconclusive", "reasons": [...], "sample": {...}, "checks": [...]}`.
- `aggregate()` and `_check()`/`_TOLERANCE_CHECKS` are superseded; `compare()` becomes a thin
  wrapper that builds `canvas-bench-compare/v2` (`COMPARE_SCHEMA` bump), keeps a `v1` reader for
  old outputs. `compare_markdown()` prints the sample table and the bounds.
- `promotion_stage(assessments, canvas_id, style_id)` derives the stage from the highest tier
  passed; the "foreign compare" and "no compare" refusals stay. `apply_promotion` is unchanged.

`servers/fastapi/scripts/canvas_bench.py`
- `run`: add `--batch`, `--metric-epoch`; write `metricEpoch`, `batch` into the receipt and an
  optional `--invalid <reason>` / a small `invalidate` subcommand that stamps `invalid` on a
  receipt. Add a `matrix` (or `run --interleave`) mode that takes several canvases, the
  manuscripts and repeats and runs them in randomised interleaved order with resumable receipts
  (a killed run currently restarts the whole canvas).
- `compare`: accept several `--baseline`/`--candidate` directories (glob), add `--tier`; the
  JSON name includes the tier. `promote`: read the v2 assessments.
- `_load_receipts`: accept `canvas-bench-receipt/v1` and v2.

`servers/fastapi/scripts/canvas_bench_live.py`
- `gate_evidence`/`timing_evidence`: populate `pages: [{index, flagged, classification,
  attempts}]` (today `flaggedSlides` is `[]` even when `flaggedPages` is 4; where the per-page
  data is read from needs to be located in the presentation/timeline payload, it is not in the
  timing receipt). Record `providerUsage` per call if the receipt has it.

`servers/fastapi/tests/unit/test_canvas_bench.py`
- Replace the tolerance tests with: Wilson and Newcombe against known values; deff scaling;
  pass/fail/inconclusive boundaries at the margin; minimum sample gating (decks, manuscripts,
  batches); interleave order; `not_applicable` table vs `unmeasured`; `invalid` exclusion and
  the `too_many_unmeasured` verdict; epoch mixing refused; tier promotion history; property
  test that the pooled verdict is order-independent; regression fixture built from the 13
  receipts asserting the section 6 verdicts.

Also add 2 manuscripts under `tests/fixtures/canvas_bench_manuscripts`, and update the two
bench docs' rule description.

## 8. Open decisions for the user

1. **Margins:** 0.20 (candidate) and 0.15 (verified) absolute page flag rate. Tighter margins
   need roughly 40+ decks per arm; is a canvas that flags up to 0.9 extra pages per 6-slide deck
   acceptable at `verified`?
2. **Cost:** 2.5 h to decide 1:1 at the candidate tier and about 6.7 h for both canvases through
   `verified` (serial). Approve running 2 decks concurrently (about half the wall time, seconds
   metric becomes contended) or stay serial?
3. **Sequence with the text_overlap fix:** wait for it and re-baseline (existing 5 baseline
   decks stop counting, margins may be re-derived from the lower rate), or measure now on the
   current epoch? Should margins be absolute constants or a fraction of the measured baseline
   rate?
4. **Two new manuscripts** (need >= 4 for `verified`). Who writes them, and should they cover
   different document types (table-heavy, narrative)?
5. **Retries as a gate:** keep (as designed) or demote to informational, given it moves with the
   flag count (r = 0.92)?
6. **Per-manuscript guard:** 1:1 manuscript 02 flagged 4 of 6 twice. Add a rule that no single
   manuscript may be worse than the baseline by > 2 x delta, or leave it informational?
7. **Rho:** 0.15 is estimated on 13 decks. Accept as a constant now and re-estimate after the
   first 40 decks, or require the bench to estimate it per assessment?
8. **`fail` semantics:** a `fail` currently changes nothing beyond blocking promotion. Should it
   also open an investigation ticket or record a blocker in `canvas_stages.json`?
