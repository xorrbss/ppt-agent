# Native chart integrity V4 vs V5: static measurement on stored SVG pages

Scripts and raw output (same folder): run.py, probe.py, quality.py, results.json. Repo files were not modified. No browser, network or provider call was used.

## 1. Corpus

Only a few stored pages carry a chart declaration, and only 4 are real generated output.

| Source | Count | Origin |
|---|---|---|
| tests/fixtures/svg_smoke/slide-{2,3,4,5}.svg + chart-declarations.json | 4 charts | **Real generated.** The test docstring says "Real S1 smoke drawings". The declarations are stored separately and were bound with `native_binding_digest`, as `test_svg_smoke_regressions.py` does. |
| tests/unit/fixtures/svg_chart_paint_native_examples_v1.json | 7 charts (column, coincident-line, stacked-column, donut, fixed-column, separator-pie, marker-line) | Synthetic. These were made by the frozen chart builder for paint-proof testing, and the file says "synthetic fixtures". |
| Pages built by `_page()` in test_svg_native_chart_retention_v5.py | 2 correct (column, bar) + 9 hand-made attacks | Synthetic (attack fixtures). |

Other stored pages:
- svg_text_overlap/fp_21x9_2e861a9f_s01.svg is a real page with `native-chart-oee-gap` and `native-chart-` tables, but it has no stored declaration. Both revisions would see no chart, so it was excluded.
- Under docs/session-results, the chart-render-proof and runtime-input folders hold only scripts and metadata. No stored real-generated chart pages were found there.
- scripts/svg_template_eval holds no chart fixtures. The `data-native-chart` grep found no SVG files in the repo carrying the attribute itself.

Totals: 22 charts, of which 4 are real and 18 are synthetic.

## 2. Method

For each page I ran the two calls the owner named, once per revision:
- `page_native_objects(page, pt_per_px=.75, revision=REV)`, giving "V4/V5 accepted" (projection).
- `chart_drawing_evidence(page, svg, revision=REV)`, giving V5 `contentGeometry.passed` and its reason, plus V4 missing labels.

For the 10 synthetic pages with a known source string (the correct page plus the 9 attacks), I also ran `apply_requested_native_quality` as the retention gate for each revision.

## 3. Results

### Real pages (4 charts)

| Page | V4 accepted | V5 projection accepted | V5 content geometry | Reason |
|---|---|---|---|---|
| smoke slide-2 (single-series column) | yes | yes | pass | chart_content_common_cartesian_geometry |
| smoke slide-3 (2-series column, **dual axis**) | yes | yes | **fail** | chart_content_extra_bar_geometry |
| smoke slide-4 (single-series column) | yes | yes | pass | chart_content_common_cartesian_geometry |
| smoke slide-5 (2-series column, common axis, "단위: 억원" text) | yes | yes | **fail** | native_chart_content_text_unaccounted |

V4 accepted 4 of 4 and V5 geometry passed 2 of 4. Both V5 rejections are disagreements.

**Slide 3, classified (a), a true catch.**
- The drawing scales the two series on separate axes. The page labels "좌축: 매출 0~200억원" and "우축: 영업이익 0~25억원".
- The 12 profit bar is 117 px tall and the 120 revenue bar is 146 px tall.
- The declaration is one native chart with one value axis, with values 120 and 12. As a native chart the profit bars would be about 10 times shorter than drawn, so the chart would mean something different from the page.
- V4 accepts it, and the existing V4 test asserts the series names are simply "매출" and "영업이익".
- The reason code is imprecise. The 16x16 legend swatches trip "extra bar geometry" first. When I removed the swatches, the reason became `chart_content_bar_common_scale_or_baseline_mismatch`, the real underlying disagreement.
- I also tried rewriting the profit bars to a single common scale and removing the axis notes. That page still failed with extra_bar_geometry, which comes from the swatches and not the scale.
- So on slide 3 V5 is right to reject, but part of the reason is a legend-swatch weakness (see section 5).

**Slide 5, classified (b), a false rejection with a caveat.**
- Its geometry is correct. The bars follow one common scale (120 to 132 px, 12 to about 13 px), the category order is right, and the value labels are right.
- The only cause is the extra in-group text "단위: 억원" (a unit caption). Removing that one text node makes V5 pass with `chart_content_common_cartesian_geometry`.
- So the drawing does not disagree with the data. V5 rejects a correct drawing because a visible annotation is not in the declaration.
- The caveat is that a native chart rebuilt from the declaration would not show the unit. This is information loss, not a data error, and the rejection is deliberately strict.

### Synthetic builder-made pages (7 charts)

V4 and V5 both accepted all 7. V5 geometry passed all 7 (cartesian for 5, `polar_partition` for donut and separator-pie). There were no disagreements, which was expected because these were built to pass.

### Synthetic correct pages (2) and hand-made attacks (9)

- `page_native_objects` accepted all 11 under both revisions, so the projection step cannot tell them apart.
- The two correct pages (column, bar) passed V5 geometry and the V5 quality gate.
- Retention gate (`apply_requested_native_quality` plus `quality_keeps_content`): V4 kept all 9 attack pages. V5 rejected all 9, with `native_requested_chart_missing`.
- Of the 9 attacks, 8 have a V5 geometry reason: wrong bar height, swapped bar heights, a missing bar, swapped category labels, swapped value labels, values piled in one category, white-on-white value text, and an internal white cover rect.
- The ninth, value text with opacity 0, has no V5 evidence at all, so it is unproven and rejected through the quality gate.
- All 9 are (a) in the sense that V5 correctly rejects, but they are my reconstruction of the repo's own attack tests. They show that V5 does what it is designed to do. They say nothing about how often real output hits those failures.

### Classification summary (22 charts)

- (a) V5 rejects what V4 accepts, true catch: 1 real (smoke slide-3), plus 9 synthetic attacks.
- (b) V5 rejects what V4 accepts, false rejection: 1 real (smoke slide-5, over-strict on a unit caption).
- (c) V5 accepts what V4 rejects: 0. V4 rejected nothing in this corpus.
- Agreement, both accept and V5 geometry passes: 2 real (slide-2, slide-4), 7 synthetic builder pages, 2 synthetic correct pages.

## 4. Answer to the owner's question

On the only real generated charts available, V5 flagged 2 of 4. One is a genuine error that V4 lets through: a dual-axis drawing exported as a single-axis native chart. The other is a correct chart rejected for a unit caption. That is 1 real true catch in 4 real pages, so the evidence that V5 catches real errors is a single case. The 9 synthetic attacks show V5 detects wrong heights, swapped labels, hidden values and occlusion that V4 accepts, but those were written to be detected.

## 5. Limits

- **Sample size.** There are 4 real generated charts, all from one smoke run, all columns and all in Korean. There are no real pie, donut, line or stacked charts, so V5's polar and line proofs were tested only on builder-made synthetic pages. No per-chart-type rate can be claimed.
- **Synthetic share.** 18 of 22 charts are synthetic. 9 are attack fixtures that cannot say how often real output fails, and 7 are builder-made.
- **No real default-path measurement.** A real generation run would be needed, and the owner said not to run paid generation. The repo's real-capture evidence is described only in documents, and the pages are not stored.
- **Paint proof not run.** V5 also requires browser paint evidence (`paintEvidence`, `native_chart_capture_not_measured` when unmeasured). Here only the static `contentGeometry` was evaluated. V5's overall retention here is therefore optimistic: the real passes (slide-2, slide-4) still need the browser step. Occlusion cases not caught statically would be caught only there.
- **Retention gate on real pages.** I did not run `apply_requested_native_quality` on the real pages, because the original source text is not stored. The projection and geometry results stand without it.
- **Reason codes can mislead.** The legend swatch confusion on slide 3 means one real failure cause (dual axis) was reported as a different one (extra bar geometry). Slide 5's rejection is an over-strict unit-caption rule. Both would likely recur in real Korean business charts, which often carry unit captions and legends. That suggests V5 could reject a meaningful share of correct real charts.
