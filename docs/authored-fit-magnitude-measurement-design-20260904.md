# Authored fit magnitude: instrument, replay, decide the A4 ladder depth

Date: 2026-09-04. Status: design, approved for implementation.
Parent: `generation-programme-master-design-20260904.md` (workstream A, item
A4; section 9.1 records the phase-1 miss this design answers).

## 1. Why this exists

Phase 1 left one deck in three failing on
`authored_composition_safety_gate_failed`. A4 proposes a deterministic fit
ladder (density swap, uniform text scale-down, gap compaction, one remake)
to replace the prose LLM repair (3 successes in 24 runs). Whether the ladder
is a good investment depends on a number nobody has: **how far a failing
slide misses**. If most failures fit after a small scale-down, the ladder is
the product; if most need more than the 15 px body floor allows, re-authoring
with less content is the product and the ladder is an accessory.

Today that number is thrown away at the gate. `inspect_authored_dom`
(`utils/authored_dom_quality.py`) reduces every overflow, off-canvas and
clipping finding to a category, a binary severity (`high` iff scroll exceeds
client by more than 10 %) and a fixed sentence. The raw browser metrics carry
`scroll_width/height`, `client_width/height` and the bounding box against
the 1280×720 viewport for every qid-bearing element, so the magnitude exists
at the moment of judgement and is simply not recorded.

The review of the first plan (same day) found three defects that this design
corrects:

1. `composition_capacity.largest_fitting_text_scale` is an **estimator**
   (characters per line × character area over a slot envelope), not a
   measurement. It cannot be the bucketing instrument. Buckets are decided by
   re-rendering in Chromium at fixed scale steps.
2. Text scale is one axis. An `.ig` figure, a fixed-height container or a
   grid row that overflows does not respond to font size. The distribution
   must carry **what kind of element** overflows.
3. The stored corpus (157 attempts) mostly predates the infographic library
   (2026-09-03) and the phase-1 fixes. Only decks produced by current code
   are a valid sample; the sample must be topped up.

Sequencing decision: the re-author fallback (a deck publishes with a flagged
slide instead of failing, D7) is needed whatever the distribution says, so
it proceeds in parallel; the measurement decides only how deep the ladder
goes.

## 2. Scope

In scope:

- **M1** — magnitude fields on `DomQualityIssue` and a per-slide fit summary
  in the DOM report; persisted into the slide-quality receipt and the
  timing receipt. Product code; the ladder reads the same fields at runtime.
- **M2** — a text-scale applier for authored HTML (rewrites absolute
  `font-size`/`line-height` px values by a factor, figure roots excluded).
  Product code; A4 rung 2 needs it. The master design named
  `scene_compiler._fit_text_style` for this rung; that helper styles Composer
  scene nodes and does not apply to authored HTML, so the applier is new.
- **M3** — offline replay script over stored checkpoint HTML: render at
  scale steps, classify, bucket, report.
- **M4** — sample policy and top-up.
- **M5** — decision rule that fixes the A4 ladder's shape.

Out of scope: the ladder itself, the remake fallback, any change to gate
verdicts. **The gate decides exactly what it decides today**; M1 only records
more about the decision.

## 3. M1 — record magnitude at the gate

### 3.1 Issue fields

`DomQualityIssue` gains one optional field:

```python
magnitude: Mapping[str, float | str] | None = None
```

Filled for three categories; `None` for the rest (unchanged behaviour):

| category | keys | meaning |
|---|---|---|
| `overflow` | `ratio_x`, `ratio_y` | `scroll_width / client_width`, `scroll_height / client_height` (1.0 = fits); `excess_px_x`, `excess_px_y` the pixel difference |
| `off_canvas` | `overshoot_px_left/top/right/bottom` | pixels beyond each canvas edge, 0 when inside |
| `clipping` | `hidden_fraction` | share of the element's box outside its clipping ancestor |
| all three | `element_kind` | `text`, `figure`, `figure_part`, `container`, `image` (see 3.2) |

`to_dict` emits `magnitude` only when present, so existing receipts, tests
and consumers that compare issue dicts are unaffected. Severity and detail
strings do not change.

### 3.2 Element kind

Decided from the compact DOM map already built by `build_compact_dom_map`:

- `figure` — element carries the `ig` class (archetype root, server-owned CSS)
- `figure_part` — descendant of a figure root (`ig-label`, `ig-desc`,
  `ig-num`, `ig-ic`, …)
- `image` — `img`
- `text` — element whose own bound text is non-empty and whose children are
  inline only
- `container` — everything else (grid/flex/block with block children)

This classification is the answer to defect 2: a `container` or `figure`
overflow is not a text-scale problem.

### 3.3 Per-slide fit summary

`DomQualityReport` gains `fit_summary: Mapping[str, Any] | None`:

```json
{
  "worst_overflow_ratio": 1.23,
  "worst_off_canvas_px": 41,
  "overflow_kinds": {"text": 2, "container": 1},
  "content_bbox_fill": 0.71
}
```

`content_bbox_fill` is the union area of owned-content bounding boxes over
the canvas area (1280×720); it is the under-fill signal for the opposite
question (whether `grow_step` earns its place). Computed from boxes already
in the metrics; no extra probe.

### 3.4 Persistence

- The authored quality-gate receipt (`build_authored_quality_gate_receipt`,
  `residualIssues`, stored under `presentation.theme["authoredQualityGate"]`)
  serialises issues with `to_dict`, so magnitude appears there with no
  schema change beyond the optional key. (`deck-<sha>-slide-quality.json`
  does not carry issue dicts at all; the first draft of this section was
  wrong about that.)
- `job-<sha>-attempt-timing.json` (E4) gains, per slide that failed the
  composition gate, `fitSummary` from 3.3, so a failed deck's misses can be
  read without opening the quality receipt.

### 3.5 Tests

- Unit: three synthetic metric payloads (text overflow 1.05, container
  overflow 1.4, off-canvas right 30 px) assert exact magnitude values and
  element kinds; a payload with no geometry issue asserts `magnitude` absent
  and `fit_summary.worst_overflow_ratio == 1.0`.
- Regression: every existing `inspect_authored_dom` test still passes
  unchanged (verdict parity is the contract).

## 4. M2 — text-scale applier

`utils/authored_text_scale.py`:

```python
def scale_authored_text(html: str, factor: float, *, floor_px: float = 15.0)
    -> ScaledHtml  # (html, scaled_declarations, floored_declarations)
```

- Rewrites `font-size: <n>px` and `line-height: <n>px` in inline `style`
  attributes and in `<style>` blocks authored by the model.
- Skips rules whose selector targets `.ig` roots or `ig-*` parts, and skips
  the server-injected infographic stylesheet: the `<style>` block that
  contains `LIBRARY_MARKER` (`.ig-lib-v1{display:none}`,
  `utils/authored_infographic/library.py`). Figure density is rung 1's job,
  not rung 2's.
- Never writes a body value below `floor_px`; records how many declarations
  hit the floor, so a "scaled to 0.80" candidate that actually stopped at the
  floor on most text is visible as such.
- Pure function; no browser.

Percentages, `em`, `rem` and `clamp()` are left untouched and counted as
`unscaled_declarations`. If replay shows the model uses relative units on a
material share of text, the applier grows; the count tells us whether that
is needed.

## 5. M3 — offline replay

`servers/fastapi/scripts/authored_fit_replay.py`, following the pattern of
`scripts/composer_offline_eval_*.py` (DB access with owner/tenant context set
through `utils.owner_context`, renders via
`utils.slide_capture.render_html_to_png_with_metrics`).

### 5.1 Input

Rows of `authored_slide_checkpoints`, selected by `--job-id` list or
`--since <iso>`; HTML is `illustrated_html` when present, else
`author_html`. Icon images are inlined as `data:` URIs by
`services/authored_icon_pack/inline.py`, so the stored HTML renders
self-contained; the script asserts no `src="/app_data/…"` or `http(s)://`
image references remain and skips (and lists) any slide that has them.

The script runs against the shared PostgreSQL or the bench SQLite through
`DATABASE_URL`; it never starts the API, never touches workers, never calls
a provider.

### 5.2 Per slide

1. Render at scale 1.0; run `capacity_violations` and `inspect_authored_dom`
   exactly as the pipeline does (same `SlideCapacity`, same reference mode,
   rebuilt from the job's `request_payload` the way
   `restyle-readback-verification` describes). Record verdict and M1 fields.
   Slides that pass are kept for the under-fill distribution.
2. For a failing slide, render again at scales **0.95, 0.90, 0.85, 0.80**
   using M2, stopping at the first scale that passes both gates. Four
   renders maximum, ~1 s each.
3. Classify the slide:

| bucket | definition |
|---|---|
| `fits_at_0.95` | passes at 0.95 |
| `fits_at_0.90` / `fits_at_0.85` / `fits_at_0.80` | first passing scale |
| `floor_bound` | a passing scale exists only because the applier reports floored declarations on ≥ 50 % of body text, i.e. the page fits by violating the design floor |
| `non_text` | worst overflow kind at 1.0 is `figure`, `figure_part`, `container` or `image` and no scale passes |
| `unfixable_by_scale` | text-kind overflow and 0.80 still fails |

4. Write one JSON row per slide (job, slide index, role, archetype if any,
   verdict at 1.0, M1 fit summary, bucket, first passing scale, floored
   count, render millis) and the PNG at 1.0 and at the first passing scale
   for the blind look.

### 5.3 Output

`artifacts/authored-fit-replay-<date>/`: `slides.jsonl`, `summary.json`,
`summary.md` with the bucket table, the overflow-kind table, and the
under-fill histogram of `content_bbox_fill` over passing slides.

### 5.4 Cost

Failing slide: ≤ 5 renders. A 60-slide sample with 20 % failures is about
90 renders, a few minutes on one Chromium. No provider calls.

## 6. M4 — sample

### 6.1 What the stores actually hold (verified 2026-09-04)

- `authored_slide_checkpoints.author_html` is the model's output after the
  author stage, **before** any repair; `illustrated_html` is overwritten at
  the render stage with the repaired HTML
  (`services/authored_checkpoint_state.py`, stage `render`).
- On publication both columns are nulled and the private render directory
  is removed (`services/authored_presentation_artifacts.py` →
  `clear_checkpoint_html`, `cleanup_checkpoint_artifacts`). A published
  deck keeps only its final, post-repair HTML in `slides.html_content`.
- Therefore **pre-repair HTML survives only for failed or in-flight jobs.**
- The shared PostgreSQL holds 667 checkpoint rows with HTML since 09-02
  across 20 jobs, and **none** of them carries the infographic library
  stylesheet: the last row is 2026-09-03 14:42 UTC, before the library
  reached the worker. The whole corpus is legacy for this study.
- The phase-1 bench SQLite holds 40 rows with HTML, all from the three
  failed jobs; the four published decks have only `slides.html_content`.

### 6.2 Inputs, by source

| source | HTML | what it measures |
|---|---|---|
| failed jobs, checkpoints | `author_html` | pre-repair miss at the gate: the ladder's real input |
| published decks, `slides.html_content` | final | residual miss after repair (should be ~0) and the under-fill distribution |
| bench runs with retention on | `author_html` of every slide, kept after publish | pre-repair miss for decks that went on to publish: the bulk of the sample |

`author_html` predates the illustrate stage, so `ig-ic` icon slots hold
their fixed-size placeholders instead of the inlined `data:` PNG. The
library CSS sizes the slot, not the image, so geometry is expected to be
identical; the replay verifies this on the failed jobs where both
`author_html` and the render-stage `illustrated_html` exist and reports
any verdict difference.

### 6.3 Retention knob

Registered knob `AUTHORED_RETAIN_CHECKPOINT_HTML` (default `false`): when
true, publication skips `clear_checkpoint_html` and leaves
`author_html`/`illustrated_html` in place (artifacts are still cleaned).
Bench-only; the default keeps today's storage behaviour. Registered in
`constants/settings_registry.json` first, accessor in `utils/get_env.py`.

### 6.4 Selection and size

Valid rows are those produced by current code, proved by the artifact, not
the clock (memory `worker-code-version-attribution`). The library
stylesheet is injected only into figure slides
(`finalize_infographic_slide_html` returns a quiet page untouched), so the
criterion is **per job**: a job is current code when at least one of its
slides contains `LIBRARY_MARKER`; every slide of that job is then valid.
Jobs with no such slide are reported as a separate "legacy" bucket and
excluded from the decision.

Population for the decision: slides with **any** geometry issue at first
render (overflow, off-canvas, clipping), not only slides that failed the
deck. Every such slide is what the ladder faces; the deck-level failure is
only the case where the existing repairs also failed. The 2026-09-02 run
flagged 23 of 90 slides, so ~25 % of slides are expected in the population.

Minimum: **100 current-code slides with pre-repair HTML and ≥ 20 flagged.**
Today's yield is 40 (bench failed jobs). The top-up therefore runs on the
isolated bench backend (`scratchpad/start-bench.sh`, token profile, images
on) with the retention knob on: three to five 20-slide generations from the
E1 fixtures, 40–60 minutes, provider calls consumed. This is the one
non-free step and is stated as such.

## 7. M5 — decision rule

Let *S* = share of failing current-code slides in `fits_at_0.95` ∪
`fits_at_0.90` ∪ `fits_at_0.85` ∪ `fits_at_0.80` that are **not**
`floor_bound`, and *N* = share in `non_text`.

| finding | A4 shape |
|---|---|
| *S* ≥ 60 % | ladder as designed (rungs 0–3), remake as terminal rung |
| 30 % ≤ *S* < 60 % | ladder rungs 0–2 only (no gap compaction); remake-with-less-content is the second rung, not the last |
| *S* < 30 % | remake-with-less-content is the primary repair; keep rung 1 (density swap) only if `non_text` is dominated by `figure` |
| *N* ≥ 40 % regardless | rung 1 (figure density) and container-side rules go first; text scale is demoted |

Under-fill: if ≥ 25 % of passing slides have `content_bbox_fill < 0.55`,
`grow_step` (scale-up to fill) enters the ladder as rung −1; otherwise it is
left out of A4.

The rule is written before the data so the data cannot pick the rule.

## 8. Parallel track — remake fallback (not gated on this)

Independently of M1–M5, the deck-level terminal behaviour changes from
"fail the deck" to "publish with the slide flagged and queue one automatic
remake with a lower-density hint" through the existing
`run_authored_page_remake_preview` / `publish_authored_page_remake` path
(`services/authored_page_remake_service.py`), which already accepts an
instruction string. That is D7 from the master design and does not depend
on the distribution. It is listed here so the two tracks are not confused:
the fallback fixes the deck-failure symptom; the measurement decides what
runs before the fallback.

## 9. Sequence and effort

| step | depends on | effort |
|---|---|---|
| M1 magnitude fields + tests | — | half day |
| M2 text-scale applier + tests | — | half day |
| M3 replay script | M1, M2 | half day |
| retention knob (§6.3) | — | 1 hour; lands after D7's edits to `get_env.py` and the registry to avoid a clash |
| M4 sample top-up on bench | retention knob | 1 hour wall, provider calls |
| M5 decision, recorded in the master design as D15 | M3 output | 1 hour |
| remake fallback (D7) | — | parallel, own estimate in A4 |

M1 and M2 are independent and run as two agents; M3 follows.

## 10. Risks

- **Replay fidelity.** Offline Chromium must match the pipeline's: same
  fonts under `APP_DATA_DIRECTORY`, same viewport, same probe script. The
  replay asserts verdict parity for the phase-1 decks against their stored
  quality receipts before any bucket is trusted; a mismatch stops the study.
- **Scale applier coverage.** If the model writes relative units, 0.80 may
  not mean 0.80. The `unscaled_declarations` count is reported next to every
  bucket for that reason.
- **Small sample.** 20 failures give ±20 pp on *S*. The decision thresholds
  are wide apart on purpose; a result near a boundary is reported as such
  and the top-up is repeated once, not argued over.
- **RLS.** Checkpoint rows are owner-scoped; the script sets owner and
  tenant context per the `worker-stall-inspection` note or sees nothing.

## 11. Result and decision (D15), 2026-09-05

Measured with M3 (`scripts/authored_fit_replay.py`) over a snapshot of the
phase-1 bench database after the M4 top-up (retention knob on):
10 current-code jobs, 198 rows replayed (98 pre-repair checkpoint rows,
100 published-final rows), 2 rows skipped (`data-illust-prompt` rejected by
renderer admission), 0 provider calls. Evidence:
`artifacts/authored-fit-replay-20260904/` (summary, per-slide rows, the
three defect renders). Verdict parity: all 6 decks with a bound
quality-gate receipt agreed with the replay; the two gate-failed decks'
`last_error` was reproduced on exactly one slide each (15 and 1).

| quantity | value |
|---|---|
| pre-repair slides with overflow / off-canvas / clipping | **0 / 98** |
| pre-repair slides with any geometry defect | 3 / 98 (3 %), all `overlap` |
| ladder population for M5 | 3 |
| S (fits by text scale) | 0 % |
| N (non-text overflow) | 0 % |
| under-fill `content_bbox_fill < 0.55` among passing slides | 185 / 195 (95 %) |

The three overlaps, seen in their renders: an org-chart centre node laid
over the top node's figure; an oversized stat label placed inside a
roadmap row; and a funnel slide the pixel probe flags at medium severity
that looks clean to the eye (a likely false positive). None is a text-fit
problem; text scale cannot touch any of them, and M1 records no magnitude
for `overlap` because the category has none of the three geometry
measures.

**Decision D15.** By the rule in section 7 (S < 30 %):

1. The A4 **fit ladder is withdrawn** for the ordinary authored route. The
   defect it targets does not occur under current code in this sample.
   M1 (magnitude at the gate) and M2 (text-scale applier) stay as product
   code and keep measuring; the ladder returns to the plan only if a later
   replay shows an overflow population.
2. **The remake-then-flag fallback (D7) is A4's primary repair.** Its
   first bench run exposed that the bounded repair round's residual-defect
   exception bypassed the fallback (deck 8ae5d152 failed on the branch D7
   had left fail-closed); that branch is being closed as part of D7.
3. `grow_step` is **not** added. The 95 % figure is an artefact of the
   metric: `content_bbox_fill` is ink area (union of owned-content boxes),
   and full-looking slides score 0.15–0.25 on it. An under-fill decision
   needs a layout-area metric and the blind rating, not this number.
4. Overlap needs its own instrument. The next measurement adds overlap
   magnitude (occluded fraction, which pair) to M1 so a deterministic
   separator can be judged against remake; until then remake is the
   repair.

Sample caveat: 98 pre-repair slides across one fixture family and one
style. The 0/98 result is strong for this route but is not evidence about
verbatim restyle (`source_content_restyle`) or other styles; those need
their own replay with the retention knob on.

### 11.1 D7 on the bench: three paths to the same failure code

Running D7 on the bench found that `authored_composition_safety_gate_failed`
is raised from several sites in `services/authored_presentation_generation.py`
and the fallback originally covered one. Closed on 2026-09-05, all governed by
one predicate (`terminal_fallback_active`: knob on, not verbatim, not exact,
ordinary profile) in `services/authored_terminal_slide_policy.py`:

| site | trigger | now |
|---|---|---|
| final gate residual after the bounded repair | slide still defective | one remake, else publish flagged |
| bounded round raises `AuthoredVqaResidualDomIssuesError` (deck 8ae5d152) | round fixed some slides, not all | round state recovered at the reviser call; residual slides reach the fallback; every candidate is re-captured as one (html, png, metrics) triple before admission |
| `SceneRepairBudgetExhausted` on the claim loop (deck da98fa75, attempt 2 after a lost lease) | the slide's single durable repair was spent by an earlier attempt | slide declared terminal, dropped from the round, fed to the fallback explicitly |

Left fail-closed deliberately: a repair candidate that violates the semantic
boundary (meaning changed, not layout), the two "telemetry unavailable"
raises (retryable; a remake cannot replace a missing probe), and
provider/capture exceptions inside the round (state not bound to evidence).
Open decisions: the per-deck remake budget (`MAX_TERMINAL_REMAKES_PER_DECK =
4`, a constant) as a knob; whether `authored_slide_capacity_exceeded` also
publishes flagged; a resumed-job integration test next to
`tests/integration/test_authored_checkpoint_resume_flow.py`.

Bench caveat: deck da98fa75 lost its first attempt to a SQLite
`database is locked` heartbeat failure in the bench's own process. The
isolated SQLite bench measures speed and quality; reliability is measured on
the PostgreSQL stack only.
