# Native chart same-capture interface, version 1

This is the chart session's concrete integration proposal for the root-owned renderer team. It does not approve current geometry or paint evidence; independent review remains blocked and no expanded check has run. The evidence boundary is the exact canonical SVG, captured viewport/device scale and emitted PNG. It does not cover another zoom/browser or final PPTX.

## Optional carrier and hash rules

Only a literal `svg-native-requirements/v5` snapshot may produce `chart_witness_manifest`; omit the keyword entirely for missing/v2/v3/v4/non-SVG paths. Use the existing render/capture/isolated-worker route, not another renderer. The only output location is `quality.browser_metrics.nativeChartPaintProof`.

- `canonicalSvgDigest`: existing `source_digest(svg)`, SHA-256-prefixed, over exact canonical source UTF-8 bytes before trusted font/furniture materialization. The render service validates this before wrapping.
- `treeDerivativeDigest`: Contract A `canonical_json_digest` of `{schema: 'svg-chart-node-tree/v1', nodes: [...]}`. Each node in ElementTree preorder is `{qid, parentQid, tag, attributes, text, tail}`; tags/attribute names retain expanded namespace names, attributes retain exact parsed values, absent text/tail is an empty string. Comments are omitted as by ordinary `ET.fromstring`. No Unicode or whitespace normalization. This derivative is checked against canonical source before font injection, and transported separately from the existing reserved canonical_svg/font/furniture authority.
- `manifestDigest`: Contract A digest of the entire manifest **excluding only its own manifestDigest** (`allow_nan=False`, sorted compact keys, `ensure_ascii=False`). All target IDs/ranges/roles are therefore bound.
- `baseMetricsDigest`: the existing metrics-digest encoder, after capabilityRegistry injection, over browser_metrics with only `nativeChartPaintProof` removed. The proof never includes its own containing metrics digest recursively.
- Proof additionally binds wrapperDigest, exact emitted/re-encoded pngDigest, viewport/deviceScale, probeVersion and resolved font/image material identities supplied by the existing trusted materialization/capture path.

## Deterministic ownership

Canonical qids are `svg-N`, where N is the zero-based preorder index of **every element**, including the SVG root and tspans, before font materialization. Parent qid is from that same tree. Wrapper/probe remapping must preserve this validated source mapping; inserted trusted font/glyph nodes need the existing authorized materialization mapping and must not silently shift source qids.

For a text/tspan node, owned text is its own direct text node plus each direct child's tail, in document order, omitting a text node only if its whole contents are whitespace. This matches `ownedTextNodes(...).map(textContent).join('')` in the existing capture probe. UTF-16 ranges index this exact owned string, zero-based, `{start, length}`, and include complete surrogate pairs. Ranges are semantic attribution only: render the entire original text run unchanged. A split/isolation optimization must demonstrate identical all-on pixels and character positions, otherwise it is unproven. A label spanning text and tspan has multiple owned-qid targets, not one fabricated concatenated glyph range.

Datum ID is `${chartId}/s${seriesIndex}/c${categoryIndex}`. Chart ID is the current digest-bound native declaration ID. A zero/degenerate datum can have no painted primitive; this is explicit, never an invented epsilon bar/slice. Every positive-measure/nondegenerate datum references the actual source primitive qid(s) returned by the family proof. Every categorical/series/unit/value label target is derived from canonical text and proven association; declaration metadata alone cannot assign a glyph range.

Each datum also carries `geometrySupport.anchor`, containing its proved canonical canvas x/y when available. Line/area datums sharing a primitive carry `geometrySupport.categoryStrip` with `minimumX` and `maximumX` at adjacent category midpoints; null bounds are unbounded. The target footprint is the isolated actual source primitive clipped to this strip, not a synthetic point or the whole shared primitive attributed to every datum. A proved baseline is included when available. Stroke-only singleton donut anchors lie on the actual stroke centerline; filled-ring anchors lie between the proved inner and outer boundaries.

Internal and external calibrated axis ticks and unit titles are included as glyph targets using their own canonical qids. A multi-series name needs actual visible legend/direct-label association before it is assigned datum ownership. Same-colored opaque center/sector paint cannot establish a visible donut hole. `proved-donut-hole` and `native-series-layer-order` authorize only the proved actual native composition, never arbitrary same-group overpaint or discarded denominator pixels.

The manifest establishes declaration-to-drawing geometry, not original-source fidelity. The consumer must retain the separate strict requested-source, category, series, value and unit checks in `native_requirements.py`; self-declared geometry cannot override them.

### Numerical support graph extension

Machine definitions are in `svg-chart-support-graph-v1.schema.json`; synthetic canonical SVG, bound declarations and actual builder manifests are in `svg-chart-support-graph-examples-20261108.json`. Each datum's `visibilityObligations` names real value/base boundary segments, canonical category-local incident curve pieces, or a polar outer arc. A region is the actual isolated native-alpha footprint intersected with the closed physical-one-pixel neighborhood of those segments/ellipse after the captured SVG CTM, then clipped by the datum category strip. It is never an extent box used as ink. Multiple segments in one region form a union. Each required obligation must have own-original contribution or an explicitly listed, sufficiently proved alternative. Quantization alone is not such a witness.

Bar/column `contributionRegions` additionally define the actual baseline-to-end center profile (`interior-profile`) and the two baseline-to-end side profiles (`incident-sides`) with the same native-alpha intersection rule. Grid and stack alternatives reference those region IDs explicitly; boolean requirement flags alone do not satisfy them. `geometryDigest` hashes the support immediately before alternatives are added and before `geometryDigest` itself is inserted; rebuilding this digest removes `geometryDigest` and replaces every obligation's alternatives by an empty list. The full manifest digest also binds the completed alternative graph. `unitKey` includes actual datum-specific value-label units and proved paired series units; conflicting unit evidence yields null and creates no borrowing edge.

Equivalence is restricted to one chart/category/value/canonical-unit/common-axis and identical canonical local support geometry. Different collinear vertex spellings normalize through the already verified category-local curve pieces while the actual source qids and ink masks remain unchanged. Its `equivalenceClasses` require joint original contribution: removing one of two identical same-colored traces may change no pixel, while removing the whole proved class exposes its actual contribution. Every witness chain must end in own or joint original contribution; mutual borrowing cannot satisfy a class. An equal point cannot lend visibility to a differently valued incident segment.

Stack alternatives identify exact cumulative boundaries, grouping and both actual contributing interiors, and require the original boundary to remain distinguishable through an actual divider or adjacent fill contrast. A same-colored vanished seam is not proved merely by two interiors. Grid alternatives identify an exact endpoint value at the actual calibrated centerline, complete bound tick glyphs (including scientific notation/tspan ownership), unit/title glyphs, and the datum's own interior/incident-side region IDs. An arbitrary wide grid containing a different endpoint supplies no alternative. `localOverlaps` permits only actual alpha intersections with the listed native occluder within the exact datum support, keeping covered pixels in the original denominator.

These are sufficient-witness requirements, not a universal claim for every degenerate or fully hidden arbitrary SVG. Missing evidence stays capture-unproven and cannot authorize fresh retention or be relabeled as an unsupported chart form. The existing admission default remains v4 until root independently accepts the complete selected source state.

## Manifest shape (synthetic illustration)

```json
{
  "schema": "svg-native-chart-witness/v1",
  "requirementsRevision": "svg-native-requirements/v5",
  "canonicalSvgDigest": "sha256:<exact-source>",
  "treeDerivativeDigest": "sha256:<canonical-node-tree>",
  "charts": [{
    "id": "native-chart-sales",
    "groupQid": "svg-1",
    "chartType": "column",
    "datums": [{
      "id": "native-chart-sales/s0/c0",
      "seriesIndex": 0,
      "categoryIndex": 0,
      "value": "30",
      "primitiveQids": ["svg-4"],
      "geometryRole": "bar-profile"
    }],
    "glyphs": [{
      "qid": "svg-6",
      "ownedTextDigest": "sha256:<UTF-8-owned-text>",
      "ranges": [{"start": 0, "length": 2}],
      "role": "value",
      "datumIds": ["native-chart-sales/s0/c0"]
    }],
    "compositionGroup": {
      "id": "native-chart-sales/composition",
      "primitiveQids": ["svg-4"],
      "glyphTargets": [{"qid": "svg-6", "start": 0, "length": 2}],
      "relationships": []
    }
  }],
  "nodeBindings": [{"qid": "svg-4", "parentQid": "svg-1", "tag": "rect"}],
  "manifestDigest": "sha256:<manifest-without-this-field>"
}
```

`nodeBindings` contains every referenced qid, its source parent/tag, and an `attributesDigest` over its exact canonical attributes; glyph bindings also contain ownedTextDigest. The illustration elides that attributes digest and other datums for readability, not as an optional validation rule.

Geometry roles are `bar-profile`, `stack-segment`, `line-trace`, `area-boundary`, `polar-sector`, `polar-ring-sector`, and explicit `zero`/`degenerate`. A role does not authorize a probe to ignore native footprint pixels. Family proof establishes source value↔geometry before the manifest is eligible; renderer paint proof does not substitute for that math.

## Expected footprint and legitimate composition

Expected geometry is the server-verified authored geometry, with exact category/series/value/unit matching, common axes/scale and type-specific topology already established. It is **not** a synthesized ideal chart, source-page skeleton, alternate chart form or metadata-only geometry.

Expected native-alpha footprint is the entire isolated native fill/stroke/glyph paint footprint at the same capture resolution, before foreign paint. Count same-colored/background pixels in that footprint; RGB difference alone cannot remove them from the denominator. The final composite must retain the required chart content, with expected/actual mask digests and complete pixel counts; missing/unknown ranges cannot become passed. Font/image resolution and clipping are evaluated by the existing Chromium capture.

The composition group is server-derived from the canonical family proof. Relationships may include an exactly associated value glyph on its own datum, a marker on its proved curve point, actual calibrated axis/grid furniture, a proved donut hole, and native series layer order within the same mathematically proved chart. This preserves legitimate line intersections/area-layer composition. No source JSON can add relationships, and unrelated white rectangles/images or arbitrary same-group paint are not authorized merely by ancestry. Every original native component retains its isolated footprint denominator; pixels covered by an authorized native component are explicitly accounted as ownedCovered, never discarded as non-native or silently omitted. A datum/glyph that makes no visible semantic contribution must not borrow visible metadata elsewhere.

The renderer team should return, under the one proof key: `schema`, `probeVersion`, the source/tree/manifest/wrapper/PNG/base-metrics digests, viewport/deviceScale, material identities, `complete`, and per target qid/range/datum: expected native footprint mask digest/count, retained/altered/ownedCovered mask digests/counts, clipped/foreign-occluder qids and unresolved ranges. Aggregate passed requires completeness and the target contract; finite samples, corners, elementFromPoint and hide-one RGB differences cannot authorize it. Whether captured-resolution tolerance is needed must be an explicit common probe-version contract, not a chart-specific allowlist.

## Existing hook points

1. SVG driver: canonical `final` and current payload/declarations exist in `drivers/svg/render.py` before its existing render/`validate_and_measure_svg` call. Call proposed `native_chart_evidence.build_chart_witness_manifest(payload, final, revision=...)`; only v5 passes the optional carrier to the existing call. Root owns shared renderer/capture/worker plumbing. No call has been added to shared renderer files by this session.
2. `services/svg_renderer.py::render_svg_with_metrics`: validate canonical source/tree/manifest digests before wrapper/font handling; carry through the existing capture and bind the exact final PNG and post-capabilityRegistry metrics. Renderer must keep the carrier absent off-path.
3. Existing SVG driver post-render `apply_requested_native_quality(rendered.quality, payload, final, ...)` consumes the proof with the same manifest digest and exact source. No new top-level proof alias.
4. Remake: `_native_projection` is the existing synchronous pre-render geometry gate; do not require a render receipt there. The two existing renders in `services/svg_page_remake.py` now have v5-only `_apply_v5_rendered_native_quality` calls, which block strict content failures. Supply the same manifest before each render, then consume the one bound browser_metrics proof after it.

State transitions: invalid source/data geometry/association is strict failed; valid static geometry awaiting the same capture is pending; incomplete/mismatched paint proof after capture is strict unproven for existing quality/recovery, never labelled unsupported valid syntax. Complete, bound data and paint evidence may authorize content retention; native editability and final PPTX/release remain separate.

## Final local support interfaces

Root approved every field and condition in `svg-chart-local-support-contract-request-20261108.json`. The machine graph schema and seven actual-builder examples contain the resulting explicit alternatives. `marker-on-datum` consumes `markerBoundary` covering the entire demanded target boundary alpha with the exact later same-datum marker's independently original paint. This includes source value-anchor obligations and incident pieces; a nonempty intersection alone never authorizes support.

`bound-stack-value-glyphs` is a seam-only alternative with both exact complete value/unit glyph sets, both actual interior-profile contributions and independently terminated first-baseline/last-total support references. `cumulativeValue` is the existing `str(Decimal amount)` string, including the existing percent-stack fraction convention. The current builder also emits a consistent `shared-stack-interface` sibling first; its presence is not a second authority requirement. Consumers check consistency if present and independently validate the bound alternative's payload values, units, geometry and actual receipt references. Endpoint roots cannot terminate recursively in another value-glyph alternative.

Actual polar fill adjacency is derived from exact rational nonzero-fill interior disjointness and a positive shared radial boundary with opposite filled sides, plus the existing rounded center/radius/partition/contour proof. The exact support/unit/order bindings permit `native-alpha-intersection-in-datum-support/v1` with `occluderPaint=all` only for that proved relationship. Each sector's own original outer-arc contribution remains mandatory. Thick styling receives no arbitrary cap, and this contract makes no universal perceived-area claim.

Final proof requires `paintDomain=captured-viewport/v1`. Native mask intersections, pair-normal geometry, native color contrast and actual font/image shaping remain root renderer attestations. The pure consumer independently checks exact identities, mask/count consistency, all referenced actual targets/regions and nonrecursive support roots; synthetic receipt tests do not recompute browser pixels.

The additive preservation-path gate in `render.py` passes the actual capture with `post_render=True` only for literal-v5 chart declarations. Its internal default-false `declared_chart_only` argument retains all source chart literal checks while leaving source/raster table authority with the existing preservation path. Normal author/remake and every legacy default retain their prior argument behavior.
