# SVG chart integrity and safe retention

## Outcome at the checked source state

Owned implementation, independent static review and the selected focused check are complete: **482 passed in 28.60 seconds, exit 0**. Full supported native-composition acceptance remains pending root-owned actual renderer integration, browser receipts and runtime-budget evidence. This is not admission promotion, real-browser acceptance, publication approval or final PPTX/release validation. Admission remains v4; no commit was created. Starting baseline: `8d6d21fb8611fe6b6312fc2d4ba5186406aa05d6`.

The owner superseded the earlier bounded bar-only follow-up. The earlier 231-test result is historical and does not validate the expanded source. Current exact raw/LF source hashes and verifier source states are in [svg-chart-current-source-state-20261108.json](svg-chart-current-source-state-20261108.json).

## Owned changes

Paths below are relative to `servers/fastapi/`:

- `services/generation_core/drivers/svg/native_chart.py`
- `services/generation_core/drivers/svg/native_chart_evidence.py`
- `services/generation_core/drivers/svg/native_requirements.py`
- `services/generation_core/drivers/svg/native_declarations.py`
- `services/generation_core/drivers/svg/prose_chart_literal.py`
- `services/generation_core/drivers/svg/publish.py`
- `services/generation_core/drivers/svg/render.py`
- `services/svg_page_remake.py`
- New `services/generation_core/drivers/svg/chart_content_cartesian.py`
- New `services/generation_core/drivers/svg/chart_content_polar.py`
- New `services/generation_core/drivers/svg/chart_content_paint.py`
- New `tests/unit/test_svg_native_chart_retention_v5.py`
- New `tests/unit/test_svg_chart_content_cartesian.py`
- New `tests/unit/test_svg_chart_content_paint.py`

Repository-root owned artifacts are this report, the current-source inventory, `svg-chart-legacy-byte-parity-20261107.json`, `svg-chart-render-manifest-contract-20261108.md`, `svg-chart-support-graph-v1.schema.json`, `svg-chart-support-graph-examples-20261108.json` and the root-approved `svg-chart-local-support-contract-request-20261108.json`, all under `docs/session-results/`. The seven machine-readable examples contain synthetic actual-builder SVG/declaration/manifest outputs only, with admitted 1280×720 dimensions and declared Arial; materializing them is not a browser or test acceptance result.

## Exact revision and guidance contract

`REQUIREMENTS_VERSION_V5` is `svg-native-requirements/v5`. Only that literal admitted snapshot revision enables these chart paths; no authoring/validation code reads a live selector environment variable. `ADMITTED_REQUIREMENTS_REVISION` remains v4. Root retains admission selection, the default-off `SVG_NATIVE_CHART_INTEGRITY_V5` setting and environment registration, policy/replay pinning, verifier ledgers and final integration.

Admission integration must pin snapshot `native_requirements_revision` to that exact literal only when the root selector admits it; absent/default admission remains v4. The renderer receives only optional `chart_witness_manifest`; post-render consumers receive the actual capture plus `post_render=True`, with no new proof alias. The source/raster additive gate alone also passes internal `declared_chart_only=True`.

The new v5 guidance preserves the requested chart family, category/series associations, signed/zero values and units, and requires truthful quantitative geometry and visible name-to-mark/value-to-datum associations. It does not convert unsupported requests, invent data or advertise unsupported acceptance. Correct numeric text alone cannot prove data drawing. Safe editability-only preservation separately needs data and captured-paint proof; it establishes neither native editability nor release readiness. V2/v3/v4/unpinned prompt bytes remain unchanged. This resolves historical report item 5 with fresh v5-only fidelity guidance, rather than the superseded bar-only limitation.

## Static proof and retention boundaries

| Existing end-to-end form | Implemented static proof |
|---|---|
| Bar/column, single/multiple series, signed/zero values | Common zero baseline/scale, actual category/series positions, actual lengths and visible name binding; optional labels remain exact when displayed |
| Stacked/percentStacked bar/column | Existing positive-segment contract, cumulative connectivity, absolute/normalized scale, series association and explicit shared-boundary support |
| Fixed value axis | Existing clustered/coherent-grid-v2 contract, complete actual grid continuity, local/sibling tick and title identity, scientific numbers and common data scale |
| Line/area | Common affine values, full actual linear subpath/domain coverage, canonical category-local supports, separate value anchors and incident segments, optional markers and visible series binding |
| Pie/donut | One-series existing exporter contract, nonnegative parts/positive total, zero-part handling, common center/radii, actual partition/winding/chord/area checks and category/share ownership |
| Admitted styling | Existing solid fill/stroke, outline/rounded bars, opacity, tspan/spacing, translation and preserved positive uniform scale/matrix behavior; no new CSS/curve/rotation support |

The generic SVG model does not make negative/all-zero polar shares mathematically valid. Multi-series pie/donut is not an existing end-to-end capability: the exporter rejects it. Line/area grouping is not exposed by the current declaration. No absolute values, epsilon wedges, invented baselines or forced family conversion repair those contracts.

Every v5 declaration is checked, including accepted native objects; a safe duplicate cannot mask an unsafe accepted chart. Source categories, series, numeric occurrences, association and units remain independently strict. Geometry/data mismatch remains a content-integrity failure. Missing/invalid render evidence is `measurement_incomplete` with `scope=native_chart_paint`, not an unsupported chart form or proof of misleading source geometry. A genuine editability-only failure can use the existing `native_requested_chart_not_editable` retention class only after captured proof succeeds. Other page-content failures remain content failures.

Pre-render geometry can be pending, with `requestedNativeObjects.contentRetentionProven=false`. Actual driver/remake post-render calls supply `post_render=True` and the actual `SvgRenderResult`; even an empty/damaged quality dictionary must enter the strict consumer. The root-authorized additive source-reflow/raster gate checks literal-v5 declared charts after existing preservation checks; it does not replace those authorities. Missing/older revisions and pages without chart declarations do not resolve new canvas metadata or change quality. Unbound glyph metrics cannot move source geometry. This static fallback does not authorize retention because the independent post-render paint gate still fails.

## Capture and numerical support graph contract

The only optional renderer keyword is `chart_witness_manifest`, omitted entirely for earlier revisions and missing/no-chart paths. The only proof location is `quality.browser_metrics.nativeChartPaintProof`. Source/parsed-tree/manifest digests, canonical source qids, complete owned UTF-16 ranges and datum-local support are documented in [the concrete contract](svg-chart-render-manifest-contract-20261108.md) and [machine definitions](svg-chart-support-graph-v1.schema.json).

The pure consumer rehashes actual PNG and wrapper bytes, checks emitted raster dimensions/DPR, reconstructs private runtime context including authorized pre-font text projection, and verifies source/render/metrics/manifest identity plus exact target/chart/datum/range links. `paintDomain=captured-viewport/v1` is mandatory. Native masks retain their original denominator and disjoint classification, including explicit `quantizedAtCapture`; quantization is not physical exposure. Direct success requires every advertised native boundary obligation's original-color contribution, not a whole-qid count. Demanded glyph clusters need native ink and original contribution. Selected-font evidence is currently aggregate target evidence, explicitly `perClusterFontAttributionVerified=false`.

Numerical support alternatives are bounded to proved native relationships: same-category/value/canonical-unit/common-axis equivalence, adjacent cumulative stack interfaces, an exact calibrated endpoint value, an exactly associated marker, or complete uniquely segment-owned value glyphs on an otherwise invisible stack seam. Joint equivalent-class member boundaries bind each member's native-alpha contribution; stack `nativeContrast`, normal-interior profiles and explicit physical `samplePairs` exclude unrelated texture/corners; calibrated grid intersections bind actual tick/title and datum interior/side contributions. Actual native raster intersections and normal placement are root renderer attestations, not independently recomputed from pixels by this consumer.

`marker-on-datum` requires an explicit `markerBoundary` witness covering the entire demanded boundary alpha with independently original-contributing same-datum marker paint. `bound-stack-value-glyphs` requires both complete value/unit glyph sets, both actual interior-profile contributions and nonrecursive actual first-baseline/last-total roots, terminating only in own-original or exact calibrated-line evidence. Visible series-name/order association and cumulative common-scale geometry remain separate. Cumulative values use the exact existing Decimal string convention. Numeric text never selects its own segment: center labels bind uniquely inside actual geometry; outside-end labels use category-local ordered one-to-one endpoint association and never authorize the seam-glyph alternative.

Polar adjacency now uses exact rational nonzero-fill topology (disjoint actual interiors and positive-length shared boundary with opposite filled sides), plus the existing rounded radial geometry/contour/partition contract. It binds common basis/unit, distinct actual source/datum IDs, later DOM order and exact support digests. Only that server-derived relationship permits actual neighboring native fill/stroke intersections; a same group, same color, equal numbers, angle match or center contact grants nothing. Each sector still needs its own original outer-arc contribution. This proves captured native composition; it does not claim every angular boundary remains independently visible or that thick native styling preserves perceived area ratios.

## Independent review and check

Implementation used bounded Sol high ownership and independent read-only Astra high review. Review challenged truthfulness, series/label reassignment, real subpath and area/grid continuity, axis glyph provenance, canonical unit pairing, collinear equivalence, degenerate polar topology, actual outline profiles, capture/context binding and pre/post-render separation. Latest review conditionally accepted exact polar topology with independent outer-arc contribution and found two legitimate outside-end stack-label regressions; category-local ordered association corrected both before the selected check. The additive source/raster gate was independently reviewed. Reviewer ran no checks and did not approve root browser/runtime/release acceptance.

Exact selected command, from this worktree's `servers/fastapi`, using the existing shared environment and `PYTHONDONTWRITEBYTECODE=1`:

```powershell
& 'C:/project/PPT-agent/ppt-agent/servers/fastapi/.venv/Scripts/python.exe' -m pytest tests/unit/test_svg_chart_content_cartesian.py tests/unit/test_svg_chart_content_paint.py tests/unit/test_svg_native_chart_retention_v5.py tests/unit/test_svg_native_chart_guidance_v4.py tests/unit/test_svg_native_chart_retention_v4.py::test_v4_keeps_drawn_content_when_only_native_geometry_is_unprojectable tests/unit/test_svg_native_chart_v4.py::test_v4_title_proof_preserves_category_value_and_geometry_checks C:/Users/ibiz/AppData/Local/Temp/svg-chart-v5-parity-1107/test_legacy_chart_parity.py -q --tb=short
```

Historical first expanded run: **391 passed / 10 failed in 31.11s**. Relevant corrections addressed stale bounded expectations, absent rejected proof handling, valid text outlines and unbound/empty post-render measurement; reviewed repeat was **402 passed in 29.61s**. After root approved the new exact paint/support interfaces and additive preservation gate, implementation and independent review completed before one justified same-selection check: **482 passed in 28.60s, exit 0**. That final selection covers actual topology, same-color stack label association, the exact typed consumer receipts, source/raster final-gate scope and legacy parity. No full suite, lint, build, typecheck, E2E, live/provider/DB/COM or final-artifact check was run.

The isolated baseline/current differential comparison again matched **104 canonical result rows, 160906 bytes**, across unpinned/v2/v3/v4 derivation, guidance, native projection, quality, retention, repair, publication and remake. Both SHA-256 digests were `0a2eb5b3483d0aa8972044eaa8140ce4e813f6d9273e3c1ef87441647034b4a7`. This is compatibility proof for the named corpus, not every possible old input. Existing pinned guidance digest checks and the exact v4 wrong-geometry retention case passed.

## Remaining integration and unmeasured work

1. Root owns final renderer integration and actual browser acceptance of the published exact support witnesses and regenerated actual-builder examples. Final static review found a remaining producer predicate restricting marker alternatives to incident edges, although the approved builder/consumer contract also includes value anchors; synchronize that exact predicate with the same entire-boundary coverage conditions. The historical 57 passed / 2 failed producer fixture state preceded the actual polar-adjacency correction and does not validate the current renderer or these source files. No automatic correction of source or receipt hashes is authorized.
2. Root must measure one existing worker capture under its current runtime budget after semantic alignment. The communicated 688-second fixture check is not evidence that the existing worker timeout is satisfied; no cap increase was requested here. Actual source-profile transforms, font/shaping cases, all supported compositions and final PPTX remain unmeasured here.
3. Root must review the checked source-state transitions and include the new Cartesian/polar/paint helpers plus witness/consumer dependencies in the appropriate whole-page, common-header, partial-recovery and page-render inventories before pinning v5 policy/replay evidence. Current verifier states and raw/LF hashes are recorded; no verifier revision, ledger hash, environment registry or admission file was edited.
4. Default fresh admission stays v4. Root alone owns the default-off v5 selector/env registration, replay policy, ledger integration, final publication/release and artifact validation. Missing, foreign-altered or unproven measured evidence remains strict; source-only/synthetic receipt tests do not establish browser or export correctness.

Precise ledger review targets are `svg_export_whole_page_verifier._FILES` / `whole_page_verifier_identity`, `svg_common_header_export_evidence.COMMON_HEADER_VERIFIER_FILES` / `digest`, and `svg_partial_recovery.CODE_IDENTITY_FILES` / `code_identity` / `page_render_identity`, with the corresponding root-owned verifier ledgers. Review transitive chart witness/consumer/geometry dependencies, including all three new chart helpers, before recording any fresh v5 effective policy. The source-state JSON records each existing inventory's baseline/current raw and reviewed keys, historical anchor, resolved identity and changed sources; those observations do not certify an expanded closure or authorize mapping changed code onto an old anchor.

All non-SVG engines, entry points, stored data, dedicated tests and CI files remain untouched. No removed engine was resurrected; no source skeleton, fixed-slot refill or customer text was used. Shared mixed-callsite changes are literal-v5 gated, and the named legacy corpus is the directly measured compatibility evidence. No local stack, DB, authentication, provider, merge/push, deploy or worktree deletion was performed. The workspace is preserved for integration.
