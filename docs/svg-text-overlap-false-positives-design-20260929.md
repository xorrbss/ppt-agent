# svg `text_overlap` false positives: measure glyph ink, not font line boxes

Date: 2026-09-29. Status: **design only, no product code changed.** Scope: svg decks
(`strategy=svg`, driver `svg-v1`). Follows the observation in
`docs/canvas-bench-square-ultrawide-20260929.md` ("Most flags look like false positives").

## 1. Summary

- `text_overlap` compares the rectangles that `Range.getClientRects()` returns for each text node.
  For SVG `<text>`/`<tspan>` in Chromium, each rectangle is the font's full **hhea ascent + descent**.
  That is neither the glyph ink nor the author's line pitch. For the bundled default face, Noto Sans KR,
  the rectangle is **1.448 em** tall, so two text runs stacked at any pitch under about 1.45 em
  intersect even when their glyphs are far apart.
- Offline replay of all 36 round-1 bench pages through the unchanged product measurement
  reproduced 12 flagged pages (receipts recorded 13). Every flagged page's only failure was
  `text_overlap`. The 12 pages contain **20 flagged pairs**. A pixel ground truth (each run rendered
  alone, painted-pixel masks intersected) finds **0 of 20 pairs share an ink pixel**. The nearest ink
  between the two runs is 8 to 28 px (median 12.5 px). All 20 are false positives.
- Recommended fix: decide overlap on **glyph ink boxes** (per-run `actualBoundingBox*` from the same
  Chromium font stack), measured in the probe and decided at the one existing point,
  `services/svg_renderer.py:305-319`. The rule falls back to today's line box whenever ink cannot be
  established. On the evidence set, this clears 20/20 bench false positives and 4/4 near-miss
  controls, and keeps 6/6 synthetic true collisions.

## 2. Current behavior (file:line on `serve/main-20260902` @ `708c7799e`)

### 2.1 What geometry is compared

| step | where | what |
|---|---|---|
| owned text nodes | `servers/fastapi/utils/slide_capture_font_paint.py:103-112` | Text nodes whose nearest `[data-qid]` ancestor is the element. Every SVG node gets a `data-qid` (`services/svg_renderer.py:204-205`), so each `<tspan>` is its own element and the parent `<text>` owns only its direct text. |
| fragment rects | `slide_capture_font_paint.py:114-127` | `range.selectNodeContents(textNode); range.getClientRects()`. These are client-space, axis-aligned rects, one per text chunk. |
| probe output | `servers/fastapi/utils/slide_capture.py:786`, `:834` (`parent_qid`), `:874` (`text_fragments: textRects`) | |
| overlap decision | `servers/fastapi/services/svg_renderer.py:305-319` | For every pair of text-owning elements, skip only a **direct parent/child** pair (`:307`). The pair collides if **any** fragment pair intersects by `dx > 0.5 and dy > 0.5` px (`:314`). Result: one failure `{"kind": "text_overlap", "pairs": [[qa, qb], ...]}` (`:319`). |

So the answers to "what is compared" are:

- **Not glyph ink.** The rects are the font's ascent+descent cell. No ink or outline measurement is involved.
- **Not `getBBox()`.** The rects come from `Range.getClientRects()`, which yields the same cell geometry.
- **Not CSS `line-height`.** SVG text ignores it. The height is the face's vertical metrics, which
  Chromium rounds to whole pixels. Every measured overlap in the bench was an integer: 1, 2, 3, 4, 6, 7 or 9 px.
- **Sibling tspans of the same `<text>` are compared against each other.** They share a `parent_qid`
  but are not parent/child of each other, so `:307` does not skip them.
- **No font-size-relative tolerance.** The fixed threshold is 0.5 px on both axes.

The Noto Sans KR variable face bundled for svg (`utils/svg_template_fonts.py:14,16`,
`servers/nextjs/public/fonts/noto-sans-kr/NotoSansKR-Variable.ttf`) has `hhea` ascent 1160 and
descent −288, `OS/2` typo 880 / −120, and `USE_TYPO_METRICS` off (UPM 1000). Chromium therefore sizes each
fragment at 1.448 em, while CJK ink sits inside the 1.0 em typo box. Two consequences follow:

- Any two runs whose baselines are closer than about 1.448 em × (the relevant sizes) are flagged.
  A title at a normal 1.1 to 1.3 em pitch is always flagged.
- A small label directly above a large number is flagged even with a generous visual gap, because
  the large number's cell carries 0.28 em of empty space above the digits. The synthetic control N3
  (16 px label, 64 px number, 7 px visible gap) overlaps by 19 px of line box.

The svg fallback page already works around this: `services/generation_core/drivers/svg/fallback.py:44-47`
had to widen body leading to 1.6 because 1.4 "makes adjacent glyph boxes intersect, which the
renderer measures as `text_overlap` on every body line". The author prompt gives the model no pitch rule
(`utils/llm_calls/author_slide_svg.py:37`: "separate text elements or immediate tspan children with
explicit x/y for multiline text").

### 2.2 What a `text_overlap` failure does (classification)

There is no advisory tier for measured svg failures. Every entry in `quality.failures` blocks.

1. `svg_renderer.py:346`: `geometry_passed = passed = not failures`.
2. First response: `generation_core/drivers/svg/render.py:161-166` treats any failure as grounds for
   the **one paid model correction** (`RETRY` with `repair_hint`). A false positive therefore spends a provider
   call and author latency. It also feeds the bench's `retries` metric; this was not separately quantified.
3. Source-reflow decks: `render.py:187-190` turns `passed is not True` into the fatal
   `svg_source_quality_failed`, and `publish.py:57-58` enforces the same at publication.
4. Classification: `generation_core/drivers/svg/quality.py:21-41` → `quality/page_outcome.py:54-55`
   (`measured_failure`) → `:58-65` residual flag `reason="quality:text_overlap"` →
   `quality/flag_classification.py:104-124` (`needs_review`: a residual is always review) →
   **`review_required`**, `verified=False`.
5. Publication: `drivers/svg/publish.py:108-116` puts the page in `reviewPages` and sets
   `qualityStatus="review_required"`.
6. Export readiness. One flagged page fails three domains together:
   - `authoring`: `services/svg_export_page_evidence.py:68-69` needs `passed is True`.
   - `geometryFont`: `:72-74` needs `geometry_passed is True`.
   - `visualReview`: `services/svg_export_visual_review.py:55-57,75-77` requires `reviewPages` to be empty.

   This is the "0 or 3 blockers" bimodality the bench observed.

The authored path met the same geometry and patched it narrowly: `utils/authored_dom_quality.py:1065`
(`adjacent_inline_text_lines`) exempts same-parent `span` lines with equal font and x origin. That
exemption would not cover label-plus-number pairs, and it is the kind of path-specific rule this
design avoids.

## 3. Evidence

### 3.1 Method

The shared stack's PostgreSQL (:21000) was **not running** during this work: nothing listened on the
port, consistent with the machine restart recorded in the bench doc. It was not restarted, so nothing
was read from or written to it. Instead:

1. **Replay.** All 36 round-1 bench SVGs (`artifacts/canvas-bench-20260929/sq-uw/round1/*/*/svg/sNN.svg`,
   6 decks × 6 pages, 720×720 / 1280×720 / 1680×720) were run through the unchanged
   `services.svg_renderer.render_svg_with_metrics` (dev-mode reusable Chrome, scratch
   `APP_DATA_DIRECTORY`). This is the same probe and the same decision code as generation.
2. **Pixel truth.** For each flagged pair, the page was rendered twice through the same renderer:
   shapes removed, white background, only run A (then only run B) kept and painted black. Ink is any
   pixel with a channel below 250, so antialiasing counts. A pair is a **true collision iff the two masks
   share a pixel**. The gap is the Chebyshev distance between the masks.
3. **Controls.** Ten synthetic 1280×720 pages in Noto Sans KR went through the same pipeline:
   - six true collisions (TP1 to TP6);
   - four tight-but-clean near misses (N1 to N4).
4. **Candidates** (section 4) were scored on the measured fragments. For the offline ink estimate,
   glyph outline bounds at the element's `wght` came from fontTools. The baseline was derived from the
   measured fragment, and the estimate was then checked against pixel truth.

### 3.2 Page-level reproduction

| deck (round 1) | canvas | flagged in receipt | flagged on replay | failure kinds on replay |
|---|---|---|---|---|
| `638d2f1a` | 1:1 | 3 | 3 (s00, s01, s02) | `text_overlap` only |
| `864bc673` | 1:1 | 4 | 4 (s00, s01, s02, s04) | `text_overlap` only |
| `2e861a9f` | 21:9 | 2 | 2 (s00, s01) | `text_overlap` only |
| `92dfbce7` | 21:9 | 1 | 1 (s00) | `text_overlap` only |
| `e314d3e8` | 16:9 | 3 | 2 (s00, s05) | `text_overlap` only |
| `f5edebd7` | 16:9 | 0 | 0 | — |
| **total** | | **13** | **12** | |

The one-page gap on `e314d3e8` cannot be attributed without the DB. The receipt keeps only counts
(`gate.flags`), and the bench backend ran `115b9263a`. It could be a terminal/fallback page or a
floor-dependent failure. It does not affect the pair analysis below.

### 3.3 Pair-level classification (all 20 flagged pairs)

Box dy is the line-box overlap the product measured. The pixel gap is the distance between the ink of the two runs.

| page | pair | kind | sizes (px) | box dy | box dy / min size | pixel gap | true collision |
|---|---|---|---|---|---|---|---|
| 1:1 `638d2f1a` s00 | svg-4/svg-5 | 2-line title | 35/35 | 3 | 0.086 | 16 | no |
| 1:1 `638d2f1a` s00 | svg-9/svg-10 | label + big number | 15/62 | 4 | 0.267 | 24 | no |
| 1:1 `638d2f1a` s01 | svg-29/svg-30 | label + big number | 14/43 | 2 | 0.143 | 15 | no |
| 1:1 `638d2f1a` s01 | svg-30/svg-31 | big number + caption | 43/14 | 4 | 0.286 | 9 | no |
| 1:1 `638d2f1a` s01 | svg-33/svg-34 | big number + caption | 43/14 | 4 | 0.286 | 12 | no |
| 1:1 `638d2f1a` s02 | svg-3/svg-4 | 2-line title | 32/32 | 4 | 0.125 | 13 | no |
| 1:1 `864bc673` s00 | svg-4/svg-5 | 2-line title | 39/39 | 1 | 0.026 | 19 | no |
| 1:1 `864bc673` s01 | svg-10/svg-11 | label + big number | 13/29 | 4 | 0.308 | 11 | no |
| 1:1 `864bc673` s01 | svg-12/svg-13 | label + big number | 13/29 | 4 | 0.308 | 11 | no |
| 1:1 `864bc673` s01 | svg-18/svg-19 | label + big number | 13/29 | 4 | 0.308 | 12 | no |
| 1:1 `864bc673` s01 | svg-20/svg-21 | label + big number | 13/29 | 4 | 0.308 | 12 | no |
| 1:1 `864bc673` s02 | svg-4/svg-5 | 2-line title | 30/30 | 4 | 0.133 | 12 | no |
| 1:1 `864bc673` s04 | svg-3/svg-4 | 2-line title | 32/32 | 6 | 0.188 | 11 | no |
| 21:9 `2e861a9f` s00 | svg-4/svg-5 | 2-line title | 55/55 | 9 | 0.164 | 21 | no |
| 21:9 `2e861a9f` s01 | svg-13/svg-14 | label + big number | 17/58 | 6 | 0.353 | 16 | no |
| 21:9 `92dfbce7` s00 | svg-4/svg-5 | 2-line title | 60/60 | 3 | 0.050 | 28 | no |
| 16:9 `e314d3e8` s00 | svg-4/svg-5 | 3-line title | 51/51 | 7 | 0.137 | 21 | no |
| 16:9 `e314d3e8` s00 | svg-5/svg-6 | 3-line title | 51/51 | 7 | 0.137 | 20 | no |
| 16:9 `e314d3e8` s05 | svg-34/svg-35 | tspan lines, same `<text>` | 17/17 | 1 | 0.059 | 8 | no |
| 16:9 `e314d3e8` s05 | svg-43/svg-44 | tspan lines, same `<text>` | 17/17 | 1 | 0.059 | 8 | no |

Counts:

- **Pairs:** 20 flagged, **0 true collisions, 20 false positives**.
  - 9 multi-line title pairs (separate `<text>` per line);
  - 9 label/caption next to a big number;
  - 2 tspan lines inside one `<text>`.
- **Pages:** 12 flagged, 12 false; none would stay flagged for another reason.
  - 7 are title-only;
  - 3 are KPI-only;
  - 1 has both (`638d2f1a` s00);
  - 1 is tspan-only (`e314d3e8` s05).

Line-box overlap ranged over 1 to 9 px (median 4). Visual inspection of the replay renders (for example
`864bc673` s01 KPI rows and `e314d3e8` s00 three-line cover title) shows clean typography.

### 3.4 Synthetic controls (all flagged by the current rule)

| case | construction | box dy | pixel truth |
|---|---|---|---|
| TP1 | 48 px title, 2 lines at 0.75 em pitch | 34 | collision (254 px shared) |
| TP2 | 40 px Latin, `Typography jumps` over `ÅNGSTRÖM ÉLAN` at 1.0 em | 18 | collision (16 px) |
| TP3 | 16 px label baseline inside a 72 px number | 24 | collision (343 px) |
| TP4 | 32 px run overflowing horizontally into a neighbour on the same baseline | 46 | collision (499 px) |
| TP5 | two tspans of one `<text>` at 0.57 em pitch | 24 | collision (300 px) |
| TP6 | duplicated title offset by (4, 3) px | 49 | collision (1672 px) |
| N1 | 48 px title at 1.15 em pitch | 15 | clean, gap 10 |
| N2 | two tspans of one `<text>` at 1.2 em pitch | 6 | clean, gap 8 |
| N3 | 16 px label 60 px above a 64 px number | 19 | clean, gap 7 |
| N4 | the TP2 Latin pair at 1.25 em pitch | 8 | clean, gap 4 |

## 4. Candidates, measured

Evaluated on 30 pairs: 20 bench and 10 synthetic. "Flags on clean pairs" counts pairs that pixel truth
says do not collide but the rule still flags (24 of them: 20 bench plus 4 near misses).

| candidate | flags on clean pairs (bench / all) | true collisions kept | bench pages cleared | verdict |
|---|---|---|---|---|
| current: font line box, 0.5 px | 20/20 · 24/24 | 6/6 | 0/12 | over-reports |
| **A. glyph ink box, same 0.5 px rule** | **0/20 · 0/24** | **6/6** | **12/12** | **recommended** |
| B. skip pairs inside the same `<text>` | 18/20 · 21/24 | 5/6 (misses TP5) | 1/12 | rejected: misses a real collision, barely helps |
| C. shrink boxes to the em (typo) box | 0/20 · 0/24 | 5/6 (misses TP2) | 12/12 | rejected: Latin descenders and accents extend past the em box |
| D. line-box depth must exceed 0.4 × smaller size | 0/20 · 1/24 (N3) | 6/6 | 12/12 | rejected: threshold fitted to this sample (bench max 0.353 vs TP2 0.450) and still flags N3 |

Notes on each:

- **A** was computed offline from glyph outlines: fontTools `BoundsPen` at the run's `wght`. The vertical
  ink is the union of glyph bounds. The horizontal ink is the fragment trimmed by the first glyph's left
  side bearing and the last glyph's right side bearing. The baseline comes from the measured fragment.
  Its verdict matched pixel truth on **30/30** pairs. It is still conservative, because a string-level
  ink box can intersect where pixels do not. N4, for example, had a 0.6 px ink-box gap against a 4 px
  pixel gap. Per-glyph boxes would be tighter, but this sample did not need them.
- **B** only addresses the 2 tspan pairs. Most multi-line titles are separate `<text>` elements, so
  structure is not the signal. It also exempts real same-block collisions (TP5).
- **C** is font-metric-only and cheap, but it encodes a CJK assumption. TP2 shows Latin ink
  crossing the em box by 0.06 px and still touching.
- **D** is a tolerance, not a measurement. Its margin comes from this sample, and a new face or size
  mix would move it.

## 5. Recommendation

Decide `text_overlap` on glyph ink, at the one point that decides it today.

1. **Measure (probe, additive).** In `slide_capture_font_paint.py`, next to `ownedTextRects`, add
   `ownedTextInkRects(node)`. Emit it from `slide_capture.py` as a new field `text_ink_fragments`
   beside `text_fragments`, which stays unchanged for every existing consumer, including the authored
   checks. For each owned text node:
   - If any fallback condition below holds, emit `null` for that node.
   - Otherwise, set `ctx.font` from the computed `font-style font-weight font-size font-family` and
     `ctx.letterSpacing` from the computed letter-spacing, then take `m = ctx.measureText(text)`.
   - Scale: `k = rect.height / (m.fontBoundingBoxAscent + m.fontBoundingBoxDescent)`.
   - Baseline: the first character's start position (`SVGTextContentElement.getStartPositionOfChar`
     mapped through `getScreenCTM()`). Using the rect's rounded ascent instead would introduce up to
     0.5 px of error.
   - Ink box: from `rect.x - m.actualBoundingBoxLeft*k`, `rect.x + m.actualBoundingBoxRight*k`,
     `baseline ∓ m.actualBoundingBoxAscent/Descent*k`, inflated by half the stroke width when the text
     is stroked.

   The canvas uses the same `@font-face` the page loaded, so fallback glyphs are measured with the
   face that painted them.

   Emit `null` (fall back to the line box, which keeps today's behavior and fails closed) whenever ink
   cannot be established soundly:
   - the node yields more than one client rect;
   - the screen CTM is not axis-aligned;
   - `textPath`, `textLength`/`lengthAdjust`, or multi-value `x`/`y`/`dx`/`dy`/`rotate` is present;
   - `text-decoration` is not `none`, or a filter or other non-trivial paint applies;
   - the font is not loaded;
   - **self-check:** `|m.width·k − rect.width| > 1 px`, meaning canvas shaping disagrees with the SVG layout.
2. **Decide (single point).** In `services/svg_renderer.py:305-319`, compare
   `text_ink_fragments[i]` where non-null and `text_fragments[i]` otherwise. Keep the same 0.5 px rule,
   the same failure kind, and the same pair output. Record `overlap_basis` in the quality dict (e.g.
   `"glyph-ink/v1"`), with per-pair `basis` (`ink` | `line_box`) so a receipt shows which geometry decided.
   No other module changes. Classification, retries, source-reflow fatality and the export domains
   all keep reading `failures` / `passed` as today.
3. **Do not** add per-layout exemptions such as titles, KPIs or tspans, a size-relative tolerance, or a
   second overlap check elsewhere.

Expected effect on the bench sample: 12 of 13 recorded flagged pages clear. The 13th is unexplained.
The three export blockers disappear for every deck whose flags were only these. First-response
corrections triggered solely by these pairs stop being bought.

## 6. Risks

- **Canvas/SVG shaping divergence.** Kerning, ligatures or font fallback could differ between
  `measureText` and SVG layout. The mitigation is the advance-width self-check with line-box fallback,
  so a divergence can only reproduce today's behavior, never hide a collision.
- **Conservative residue.** A string-level ink box can still flag pairs whose pixels do not touch, for
  example a descender under one end of a line and an accent over the other. This is acceptable. It is
  the safe direction and can be tightened later with per-glyph boxes if data shows it.
- **Very tight but legal leading.** Ink boxes 0.5 to 2 px apart pass, as they do in print. If the product
  also wants a minimum visual gap between runs, that is a separate style policy. It is not the collision
  gate and needs a user decision (section 8).
- **Evidence versioning.** `svg_export_page_evidence.py:53-60` rejects quality whose `renderer_version`
  differs from `SVG_RENDERER_VERSION`, returning `svg_*_evidence_missing`. Bumping
  `SVG_RENDERER_VERSION = "svg-chromium/v1"` would therefore make **every existing svg deck
  unexportable** until re-measured. Keeping the version and adding `overlap_basis` avoids that, at the
  cost of mixed verdict semantics across old and new decks (section 8).
- **Metrics size.** The per-element array grows by one rect per text node in `browser_metrics`, which is
  stored in `slides.content`. This is small next to the existing fragments.
- **Existing verdicts are not re-evaluated.** Decks already published keep `review_required` until
  re-measured.

## 7. Test plan

Fixtures come from real pages plus the synthetic controls above, checked into
`servers/fastapi/tests/fixtures/svg_text_overlap/`.

- **False positives (must pass after the fix):**
  - `638d2f1a` s00, s01, s02;
  - `864bc673` s00, s01, s02, s04;
  - `2e861a9f` s00, s01;
  - `92dfbce7` s00;
  - `e314d3e8` s00, s05.

  All 12 are copied from `artifacts/canvas-bench-20260929/sq-uw/round1/**/svg/`.
- **True positives (must stay `text_overlap` and name the pair):** TP1 to TP6.
- **Near misses (must pass):** N1 to N4.
- **Fallback cases (must use the line box, i.e. today's verdict):**
  - a node with multi-value `x`;
  - a rotated `<g transform>`;
  - `textLength`;
  - an unloaded family (expect `font_unresolved` as today).

Tests:

1. **Chrome-backed decision test**, modeled on `tests/unit/test_raster_redesign_layout_gate.py:61`.
   Run each fixture through `render_svg_with_metrics` and assert:
   - the failure kind set;
   - the exact `text_overlap` pairs for TP pages;
   - `overlap_basis` / per-pair `basis`.
2. **Pixel-truth oracle test.** For the TP and FP fixtures, assert that the rule's verdict equals
   ink-mask intersection: render each run alone, as in section 3.1. This keeps the gate honest against
   pixels, not against its own geometry.
3. **Pure unit test of the decision** in `svg_renderer` with synthetic `browser_metrics`:
   - ink present → ink used;
   - ink `null` → line box used;
   - mixed per-fragment;
   - the direct parent/child skip unchanged.
4. **Probe unit test.** `text_fragments` is byte-identical before and after, so the authored consumers
   are untouched: `authored_dom_quality.py`, `authored_dom_text_escape.py`,
   `authored_dom_footer_zone.py`, `slide_capture_pixel_probe.py` and `slide_capture_glyph_probe.py`.
5. **Regression.** Run the existing svg suites (`tests/unit/test_svg_*`, `test_raster_redesign_*`) and
   `test_svg_export_page_evidence.py`. Then run `uv run ruff check .`.
6. **Live check** (per the "defect reports need live path" practice). Regenerate one 1:1 and one 16:9
   bench deck on the fixed backend. Confirm that `reviewPages` holds no `text_overlap`-only page and that
   the export receipt's three domains pass for decks with no other failure.

## 8. Decisions needed from the user

1. **Renderer evidence version.** Choose one:
   - keep `SVG_RENDERER_VERSION` and add `overlap_basis` (recommended; existing decks stay exportable);
   - bump the version and re-measure existing svg decks (a backfill job would be needed).
2. **Existing flagged decks.** Leave them `review_required`, or run a re-measure pass that updates
   `quality` / `reviewPages` for published svg decks?
3. **Tight-leading style signal.** Should pairs whose line boxes overlap but whose ink does not be
   recorded as a **non-blocking advisory**, e.g. `quality.advisories: text_line_box_overlap` with ink
   gap? Or should they be dropped entirely? The gate itself should not block them either way.
4. **Authored path.** Replace the narrow `adjacent_inline_text_lines` exemption
   (`utils/authored_dom_quality.py:1065`) with the same ink basis in a later change, or leave authored
   alone? It is frozen (generation-core programme), so the default here is to leave it.
5. **Fallback page leading.** Once the fix lands, should `fallback.py:44-47` return to a normal 1.4
   leading, or keep 1.6?
