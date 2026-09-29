# svg master no-stretch and design-pack variant removal: design (2026-09-29)

Status: design only. No code was changed. Base: `9c426d8d5` on `serve/main-20260902`.

Scope, as decided by the user:
- **1a**: remove the non-functional design-pack variant path (`17059ccb6`). Design packs are
  authored-only and 16:9-only. A pack request for any other canvas fails at admission with one
  stable code.
- **1b**: never stretch a company-standard master. At the single projection point, project either
  with a uniform scale (same shape class, small aspect difference) or brand-only.

Relation to other documents:
- `docs/design-pack-canvas-family-design-20260929.md` (`9c426d8d5`) proposed canvas-native pack
  families. 1a declares packs 16:9-only, which **defers** that design. Its retirement of the variant
  store (§2.6) is carried out here; its S0 executor work and v3 manifest are not.
- `docs/aspect-ratio-expansion-design-20260928.md` decision 9 / §5 is superseded by 1a.

---

## 0. Corrections to the brief (verified facts)

These change what 1b fixes, so they come first.

1. **The upload UI never sends a master with svg.** The company-standard picker renders only when
   `generationStrategy === "authored"` (`upload/components/UploadPage.tsx:407, 463`). The outline
   request adds `masterPin` only under the same condition
   (`outline/hooks/usePresentationGeneration.ts:312-314`). svg + master is reachable **only through
   the API/MCP**, for example a client that omits `strategy` and sends `masterPin`: an omitted
   strategy resolves to svg because svg admission does not reject `master_pin`
   (`generation_core/admission/strategy.py:48-66`).
2. **The stretch is reachable today only on document canvases.**
   - Every non-16:9 preset rejects a master for every strategy:
     - 9:16 and 9:13 give `authored_portrait_combination_not_supported` (`authored_canvas.py:378-388`).
     - 1:1 and 21:9 are stopped earlier by `authored_canvas_stage_disabled`
       (`authored_canvas.py:360-361`; `constants/canvas_stages.json` is empty). Once a stage opens,
       they fall through to the same portrait-combination rejection.
   - On 16:9, `request_canvas_contract` requires the master canvas to equal 1280×720 exactly
     (`authored_canvas.py:107-110, 147-154`). Any other master fails there with
     `authored_canvas_contract_invalid`, so a 16:9 projection is always 1280×720 → 1280×720, which
     is scale 1.
   - The canvases where svg + master is admitted and the master is stretched are: A4 landscape and
     portrait (baseline rows `master/svg/A4-L|A4-P/admitted`), the svg source-restyle output canvas,
     and raster reconstruction (both use the source document canvas, about 1188×840). The A4 branch
     of `request_canvas_contract` (`authored_canvas.py:137-146`) ignores the master canvas, and
     `project_master_contract` then applies `scale_x ≠ scale_y`
     (`master_projection.py:63-64`). For the seeded 1280×720 master on A4 landscape (1188×840),
     that is x·0.928 and y·1.167. On A4 portrait it is x·0.656 and y·1.650.
3. **Design packs: "16:9-only" and "the canvas of their own compiled source" coincide.** Only
   `authored` packs execute (`source_native_generation.py:268-281`,
   `source_native_authored_pack_required`). Every authored pack is compiled from
   `/from-presentation`, which hard-codes the source canvas to 1280×720
   (`api/v1/ppt/endpoints/design_packs.py:252-253, 359`). PPTX packs may carry another canvas, but
   they register without being able to generate. No other ingest path exists: `ingest_design_pack`
   is called only from the design-packs router. So "16:9 (1280×720)" is the true, single pack canvas.
4. **svg + design pack is already rejected** before any canvas logic, with
   `svg_design_pack_requires_source_native_patch_target` (`svg_generation_admission.py:57`). 1a
   leaves that code unchanged.
5. **No UI can produce a non-16:9 pack request.**
   - The upload page has no design-pack selector.
   - Packs are pinned only in the public-v2 operations form
     (`public-v2/PublicV2OperationsClient.tsx:235-259, 350-363`), which has no canvas field and so
     always sends the 16:9 default.
   - `supportedCanvases` (`api/v1/ppt/endpoints/authored.py:40-70`) is per style and knows nothing
     about packs. The picker (`upload/components/CanvasSelect.tsx:146-156`) reads it and
     `hasCompanyStandard` only.

---

## 1. Current behavior (file:line at `9c426d8d5`)

### 1.1 Master → svg author chain

| Step | Where | What |
|---|---|---|
| Canvas | `authored_canvas.py:123-169` | The source-restyle branch (124-136) and the A4 branch (137-146) return the document canvas and do not consult the master. The preset branch (147-169) requires the master canvas to equal the preset size exactly. |
| Resolve + project | `svg_generation_admission.py:103-143` | Resolves the pin (`resolve_legacy_master_pin`), then `project_master_contract(contract, canvas.width_px, canvas.height_px)` (:140). A `MasterProjectionError` becomes a 422. This is the **single projection point**; `pin_svg_style` (:146-174) is its only caller. |
| Projection | `master_projection.py:52-123` | `scale_x = W/Wm`, `scale_y = H/Hm` (:63-64). Standard-layout title and body boxes are scaled per axis (:37-49). Fonts, gaps, padding and radius use `min(scale_x, scale_y)` (:77-80, 82, 85, 92, 106-107). The divider is scaled per axis (:95-99). Logo, header, footer and page number are **not projected** (:113-118). |
| Style context | `generation_core/style/build.py:140-167` | Master accent/background and heading/body font families displace preset tokens unless the request overrides them. Master sizes and geometry are not read. |
| Snapshot | `drivers/svg/project_style.py:76-78` | Adds `master_template` (the whole rule dict) and `master_pin`. The snapshot is frozen once (`pin_svg_style`) and persisted as `_internal_svg_style_snapshot` (`presentation_generation_staging.py:118`). It is never reselected on takeover. |
| Prompt | `utils/llm_calls/author_slide_svg.py:112-113` | The snapshot, minus `references` and `diagnostics`, is `json.dumps`-ed as the first block of the user prompt. `master_template` therefore reaches the model **verbatim as JSON**, including `layouts[].title_box/body_box`, `decorations`, `allowed_variations.max_columns: 1` and `validation.limitations`. |
| Rules text | `author_slide_svg.py:33-61, 76-80` | Never names `master_template`. It names `tokens.primary/background/heading_font/body_font` (which carry the master's brand via `build.py`) and appends `composition_clause(W, H)` for non-baseline canvases. |
| Page number | `services/svg_contract.py:261-277` | System-stamped at `(W-32, H-20)` on every canvas. The rules forbid the author from drawing one and reserve a 64×36 box at bottom-right (`author_slide_svg.py:54`). The master's page-number furniture is ignored on every canvas, 16:9 included. |

No quality gate, renderer or exporter reads `master_template`. The only consumers are the prompt
JSON and `build.py` (token and family overrides). Checked by grep over `servers/fastapi` for
`master_template|master_rules`: 7 files, all listed above plus tests.

### 1.2 Design-pack variant path (`17059ccb6`)

| Piece | Where | State |
|---|---|---|
| Preset admission substitution | `authored_canvas.py:320-344`, call at :375-377 | Re-pins to an approved variant. None exist. |
| A4 admission substitution | `document_canvas_capabilities.py:10, 73-81, 166-178, 217-232, 242-265` | Route `design-pack-canvas-variant` (implemented, not verified) plus re-pin |
| Variant lookup and re-pin | `design_pack/variants.py` (118 lines) | Only admission uses it |
| Re-layout drafting | `design_pack/variant_source.py` (252), `variant_store.py` (177), `scripts/design_pack_variants.py` (72) | Drafts drop frame `html`/`sourceRender`, so an approved variant fails every slide with `source_native_frame_payload_missing` (`source_native_generation.py:3403-3408`) |
| Registry | `design_pack/registry.py:27-40, 68-94, 199-210, 243-261` | `variants/` discovery, `pinnable()`, `status`, `is_variant`, variant identity/canvas checks |
| Manifest v2 | `design_pack/manifest.py:14, 18-20, 31-33, 51-66, 73-82`; re-exports `models.py:12-16, 399-411` | `canvas_id`, `variant_of`, `status`. Only `variant_store` writes v2. `ingestion.py:142-150` writes v1. |
| Route loader | `generation_route.py:91-92` | `.list()` was changed to `.pinnable()` |
| Kept refactors in the same commit | `design_pack/base.py`, `manifest.py` (split out of `models.py`), `structural_render.py`, `canvases.py` | Generic, not variant-specific |
| Tests | `tests/unit/test_design_pack_variants.py` (268), `test_design_pack_variants_admission.py` (121) | |

### 1.3 Authored path master handling (report only; authored is frozen)

- **16:9**: the master canvas must equal 1280×720 exactly (`authored_canvas.py:107-110`), so there
  is no stretch. Otherwise the request fails with `authored_canvas_contract_invalid`.
- **Presets**: every master is rejected (`authored_canvas.py:378-388`).
- **A4**: route `fixed-master-source-native` (`document_canvas_capabilities.py:193-194`), consumed
  through `document_canvas_contain`, whose plan is `min(W/w, H/h)`
  (`document_canvas_contain.py:81`). That is uniform with a letterbox, so there is no stretch.
- **One real stretch, at master creation.** `authored_vnext/master/ai_import.py:1580-1594` scales
  the 1280×720 default furniture template into a non-16:9 imported PPTX canvas with independent
  `scale_x`/`scale_y`. On a 4:3 PPTX (960×720) the 32×32 logo box becomes 24×32. This does not
  touch the svg path described here (svg does not project logo boxes), and the divider is a line.
  No change is proposed: reported only.
- **Side finding (A4 route label).** `select_a4_route` checks `has_fixed_master` before `strategy`
  (`document_canvas_capabilities.py:193-201`). An svg + master A4 request is therefore classified
  as `fixed-master-source-native`, a route whose consumers are the source-native executor, not svg.
  It is admitted only because the A4 writer stage is `candidate` (`document_canvas.py:17-19`,
  `_route_admitted`). If that route is ever marked verified, svg + master would inherit a
  verification it never earned. See open decision D8.

---

## 2. Design 1a: remove the variant path; packs are 16:9-only

### 2.1 Rule and error code

A request with `design_pack_pin` is admitted only on 16:9: `page_size is None` and
`aspect_ratio == "16:9"`. Anything else fails at admission with:

- **`design_pack_canvas_unsupported`**, HTTP 422.
- The code is stable across styles, stages and callers because the check runs **first** in
  `validate_authored_canvas_admission`, ahead of the A4 branch and the stage gate. The name matches
  the code the family design reserved, so a future family feature can narrow the rule without
  renaming it.

Precedence with the checks that already exist:
- Strategy svg + pack keeps `svg_design_pack_requires_source_native_patch_target`, because svg
  admission runs earlier on all three callers: sync `presentation_generate.py:517-520`, async via
  `require_authored_vnext_creation_admission`, and v2.
- Request-model validation errors (`raster_a4_svg_required`, the 9:13 `literal_error` on v2) still
  come first.
- The v2 `source_content_design_pack_not_supported` check still comes first on v2.

Defence in depth, at route resolution:
- `generation_route.load_pinned_design_pack` rejects a resolved pack whose canvas is not
  `wide-16x9` (`RegisteredDesignPack.canvas_id`, via `canvases.canvas_id_for_size`) with
  `GenerationRouteError("design_pack_canvas_unsupported")`.
- This catches a 4:3 PPTX pack pinned on 16:9 one step earlier. It currently fails later with
  `source_native_authored_pack_required`. That is the only behavior change this check makes.

### 2.2 Removal inventory

**Delete: 6 files (≈1,000 lines).**

| File | Lines |
|---|---|
| `servers/fastapi/services/authored_vnext/design_pack/variants.py` | 118 |
| `servers/fastapi/services/authored_vnext/design_pack/variant_store.py` | 177 |
| `servers/fastapi/services/authored_vnext/design_pack/variant_source.py` | 252 |
| `servers/fastapi/scripts/design_pack_variants.py` | 72 |
| `servers/fastapi/tests/unit/test_design_pack_variants_admission.py` | 121 |
| `servers/fastapi/tests/unit/test_design_pack_variants.py` | 268 (three tests move to a new file; see §2.4) |

**Edit: 13 files.**

| File | Change |
|---|---|
| `services/authored_canvas.py` | Delete `_admit_design_pack_variant` (:320-344) and its call (:375-377). Add the §2.1 check as the first statement of `validate_authored_canvas_admission`. `PRESET_CANVAS_IDS` stays (used by `utils/authored_style_catalogue.py:37` and `scripts/generate_canvas_registry.py:80`). |
| `services/document_canvas_capabilities.py` | Delete `DESIGN_PACK_VARIANT_ROUTE` (:10, `__all__` :321), the route policy (:73-81), the `design_pack_variant` parameter and branch in `select_a4_route` (:166, :168-178), `_a4_design_pack_variant` (:217-232), and the variant lines in `evaluate_a4_request` (:238, :242, :258, :260-265, which become `return evaluate_a4_selection(selection)`). The `/authored/canvas-capabilities` wire loses one `routes[]` entry. No Next.js code references it; `test_document_canvas.py:234-333` iterates the policies and has no fixed count. |
| `services/authored_vnext/generation_route.py` | `.pinnable()` → `.list()` (:91-92, drop the comment). Add the §2.1 canvas defence. |
| `services/authored_vnext/design_pack/registry.py` | Delete `status`, `is_variant` (:35-40), `_check_variant_canvas` (:89-94), the variant branch of `_check_identity` (inline back into `_load_package` as the pre-`17059ccb6` base check), `_variants` (:175, :182), the `variants/` glob (:199-210), `variants()` and `pinnable()` (:243-261). Keep `canvas_id`, simplified to `canvas_id_for_size(...)` for every manifest. Restore the class docstring. |
| `services/authored_vnext/design_pack/manifest.py` | Remove v2: `MANIFEST_V2`, `_V2_ONLY_FIELDS`, `PackStatus`, `VariantOf`, the `canvas_id/variant_of/status` fields, `_version_conditional`, `effective_status`, the v2 branch of `_sealed`, and the `canvases` import. `schema_version` becomes `Literal["source-native-design-pack/v1"]`. v1 dumps become exactly the pre-`17059ccb6` dumps (the fields never serialized for v1), so every on-disk seal still verifies. |
| `services/authored_vnext/design_pack/models.py` | Drop the re-exports of `MANIFEST_V2`, `VariantOf` and `PackStatus` (:12-16, `__all__` :399-411). |
| `api/v1/ppt/endpoints/presentation_generate.py` | Add `"design_pack_canvas"` to the ValueError→422 prefix filters (:541-545, :1787-1791). Without it the new code would surface as a 500. |
| `services/public_v2_orchestrator.py` | Same prefix addition (:776-778). |
| `tests/fixtures/canvas_admission_baseline.json` | Regenerate with `scripts/canvas_admission_table.py`. Expected diff: **290 rows**, all from a canvas code to `design_pack_canvas_unsupported`, and **0** rows change between admitted and rejected. Breakdown: topic 60 `authored_canvas_stage_disabled` + 50 `authored_portrait_combination_not_supported` + 60 `document_canvas_generation_combination_not_supported`; restyle (sync/async) 40 + 40 + 40. Review the diff against these counts. |
| `compatibility/upstream-byte-parity.json` | Regenerate (`node scripts/verify-upstream-byte-parity.mjs --working-tree --write`). It drops 6 `policy.localOnly` entries and adds the new test file. The Bitbucket `upstream-byte-parity-check` gate fails otherwise. |
| `docs/aspect-ratio-expansion-design-20260928.md` | Decision 9, §5 and status lines 113/119: superseded by this document, packs 16:9-only. |
| `docs/canvas-expansion-admission-spec-20260929.md` | Line 78 and the pack rows (:32, :36, A4): the new code. |
| `docs/design-pack-canvas-family-design-20260929.md` | Status line: deferred; variant retirement done here. |

`CLAUDE.md` (optional, one line): "design packs are 16:9-only (`design_pack_canvas_unsupported`)".

**Keep (other code depends on these, or they are generic):**
- `design_pack/base.py` (`PackModel`, `canonical_digest`).
- `manifest.py` as the split-out module.
- `structural_render.py`, which renders at the pack's own canvas (a correctness fix that
  `compiler.py:37` uses).
- `canvases.py` (used by `structural_render.EMU_PER_PX`, `registry.canvas_id` and the new defence)
  and `constants/canvas_registry.json` (also used by the canvas bench and registry generator).

**No change needed:**
- Next.js: no string references; the upload picker has no pack input.
- `contracts/`.
- i18n: public-v2 maps unknown codes to `failureUnknown` (`PublicV2OperationsClient.tsx:181-193`),
  and its form cannot send a non-16:9 canvas.
- The canvas bench, which uses neither packs nor masters (`scripts/canvas_bench*.py`).

### 2.3 Data migration

- **Leftover `variants/` directories are inert after removal.** Base discovery globs only
  `*/*/manifest.json` (`registry.py:187`), and base `_load_package` verifies the listed inventory
  only. No "unexpected file" check exists, so a `variants/` subtree, even a malformed one, is never
  read.
- **Pre-removal scan (read-only, per host).** Stores live under
  `APP_DATA_DIRECTORY/design-packs/users/<owner>/` (`generation_route.py:41-48`).
  - Count `*/*/*/variants/*/manifest.json`.
  - Grep base `*/*/*/manifest.json` for `source-native-design-pack/v2`.
  - A v2 **base** manifest would stop loading once v2 is removed, and one bad package raises out of
    `reload()` for the whole owner store. So if that grep finds anything, stop and report. Only
    `variant_store` could write v2, and only under `variants/`, so the expected count is 0.
- **No deletion by default** (D7). No DB or Alembic change: packs are file-backed, and route
  receipts seal pins whose meaning does not change.

### 2.4 Tests for 1a

- New `tests/unit/test_design_pack_manifest_and_render.py` keeps three tests from the deleted file:
  - v1 seal byte-identical (`:133`);
  - every on-disk v1 pack loads (`:163`);
  - structural render keeps the 16:9 bytes (`:177`).

  It adds:
  - a registry with a `variants/<canvas>/manifest.json` subtree (valid, and malformed) loads the
    base and exposes nothing else;
  - a manifest carrying `schemaVersion: …/v2` is rejected with `design_pack_manifest_invalid`.
- New `tests/unit/test_design_pack_canvas_admission.py`:
  - For each of 9:16, 9:13, 1:1, 21:9, A4-L and A4-P, with strategy authored or omitted, and
    `design_pack_pin` → `design_pack_canvas_unsupported`. This holds with the 1:1/21:9 stage both
    disabled and candidate, which proves precedence over the stage gate.
  - 16:9 is admitted.
  - svg + pack still gives `svg_design_pack_requires_source_native_patch_target`.
  - The code maps to 422 on sync, async and v2. The v2 case reuses the stub pattern of
    `test_public_v2_admission_errors.py`.
  - `load_pinned_design_pack` rejects a registered non-`wide-16x9` pack.
- Before deleting, grep `servers/fastapi/tests` for `authored_portrait_combination_not_supported`
  and `document_canvas_generation_combination_not_supported` assertions made with a pack pin, and
  update them.
- Run: the targeted files above, `test_document_canvas.py`, `test_canvas_stage_gate.py`,
  `test_public_v2_*`, `test_source_native_*`, `test_generation_core_admission_strategy.py`,
  `uv run ruff check .`, and `npm run check:upstream-byte-parity:working-tree`.

---

## 3. Design 1b: never stretch a master

### 3.1 Decision rule (at `master_projection.py`, the single projection point)

Inputs:
- master canvas `(Wm, Hm)` = `contract.canvas`;
- target `(W, H)` = the canvas `pin_svg_style` already resolves;
- target pixel density `ppi_t` (see §3.3).

Shape class: `utils/llm_calls/author_canvas_shape.classify_canvas_shape` (:24-35), with one
normalization. `baseline` is a **size** identity (exactly 1280×720), not an aspect class; its
ratio 1.778 lies in the wide-landscape interval, so `baseline` is compared as `wide-landscape`.
Without this, an A4-landscape master and a 16:9 master could never share a class.

Aspect mismatch: `m = 1 − min(rm, rt) / max(rm, rt)`, where `r = width / height`.

| Mode | Condition | Output |
|---|---|---|
| `identity` | `(Wm, Hm) == (W, H)` | Today's v1 dict, **byte-identical** (see §3.6) |
| `uniform` | same class and `m ≤ 0.05` | Master geometry under one scale `s = min(W/Wm, H/Hm)`, centered |
| `brand_only` | otherwise | Brand only; geometry dropped and recorded |

**Why 5%.**
- A uniform fit leaves a band of `(1 − q)/2` of the banded axis on each side, where `q = 1 − m`. At
  `m ≤ 0.05` that is at most **2.5% per side**: ≤ 32 px on a 1280 axis and ≤ 30 px on a 1188 axis.
  That is at most half of the registry's safe margin (64 px on presets, 60 px on A4;
  `constants/canvas_registry.json`), so the band reads as a slightly wider margin, not as an empty
  strip.
- Every realistic pair inside 5% is a real "same page" relation: the same aspect at a different
  size, or PowerPoint's A4 preset (10.83×7.5 in, 13:9) against the registry's A4 (≈2.1%).
- The nearest excluded pair is 4:3 → A4 landscape: 5.7%, 34 px per side (D3).
- The class test is kept as well, as the user asked. It never binds inside 5% except near class
  borders (0.95, 1.1, 1.9). There it stops a master composed for one class (for example a square
  grid) from being imposed where the composition clause asks for another.

**Uniform mode and the band.**
- Every box (layout slots and the divider) becomes `(ox + x·s, oy + y·s, w·s, h·s)`, with
  `ox = (W − Wm·s)/2` and `oy = (H − Hm·s)/2`.
- Fonts, `gap`, `min_gap`, `cell_padding` and `radius` are multiplied by `s`. Today's code already
  uses `min(scale_x, scale_y)`, which equals `s`.
- The band is left empty. The canvas background is the master's `tokens.background` (full-bleed:
  the author paints the whole canvas background, `author_slide_svg.py:50`), so the band is visually
  background.
- Furniture is **not** re-anchored separately in uniform mode. Moving it to the true edges would
  change the master's own furniture-to-content spacing by up to the band, which is exactly the
  relationship uniform mode exists to preserve.

**Brand-only mode.** It emits the same top-level keys as today, with:
- `layouts: []` and `decorations: []`. The divider is dropped, per the user's direction.
- `allowed_variations`: `{"model_layout": true, "min_gap": 12·s_b}`. `max_columns: 1` is
  **dropped**: it is a property of the single 16:9 standard layout, and it contradicts the 1:1
  (2×2 grid) and 21:9 (2–3 columns) composition clauses.
- Tokens unchanged. Font families unchanged. Font sizes, `cell_padding`, `radius` and `min_gap` use
  `s_b` (§3.3).
- `validation.automatic`: `["colors", "title_hierarchy"]` (`layout_geometry` removed).
- `validation.limitations`: accurate for this mode, including "Master layout geometry is not
  projected on this canvas; compose natively for it."
- `validation.dropped`: one entry per dropped part (§3.5).

In every mode, the existing checks stay first:
- `svg_master_canvas_unsupported`;
- `svg_master_layout_geometry_missing`. A furniture-only master is rejected on every canvas, as it
  is on 16:9 today, so admission of a given master never depends on the canvas.

### 3.2 Rule table: every registry canvas

Legend:
- `id`/`uni`/`brand` = mode.
- `s` = uniform scale; band = per-side band in uniform mode.
- `s_b` = brand-only size scale (§3.3).
- **Reach** = reachable today with 1b alone.
  - `yes`.
  - `P` = rejected by preset admission: `authored_portrait_combination_not_supported`, or stage
    disabled first on 1:1/21:9. D1 would open these for svg.
  - `C` = rejected by the 16:9 exact-canvas check (`authored_canvas_contract_invalid`). D4 would
    open these for svg.

The svg source-restyle and raster canvases (1188×h from the source PPTX, ≈1188×840) behave as
A4-L.

**From a 16:9 master (1280×720; the seeded catalogue master and every 13.33×7.5 in PPTX).**

| Target | Class | Mismatch | Mode | Scale | Reason recorded | Reach |
|---|---|---|---|---|---|---|
| wide-16x9 1280×720 | wide-landscape | 0 | **id** | 1 | – | yes |
| tall-9x16 720×1280 | portrait | 68.4% | brand | s_b 1.000 | `canvas_shape_class_differs` | P |
| tall-9x13 720×1040 | portrait | 61.1% | brand | s_b 1.000 | `canvas_shape_class_differs` | P |
| square-1x1 720×720 | square | 43.8% | brand | s_b 1.000 | `canvas_shape_class_differs` | P |
| ultrawide-21x9 1680×720 | ultrawide | 23.8% | brand | s_b 1.000 | `canvas_shape_class_differs` | P |
| a4-landscape 1188×840 | wide-landscape | 20.5% | brand | s_b 1.058 | `canvas_aspect_mismatch_exceeds_tolerance` | **yes** (today: x·0.928, y·1.167) |
| a4-portrait 840×1188 | portrait | 60.2% | brand | s_b 1.058 | `canvas_shape_class_differs` | **yes** (today: x·0.656, y·1.650) |

**From A4 masters.** A4 masters exist: `ai_import._canvas` rounds `sldSz` at 96 px/in.
- A 297×210 mm PPTX gives 1123×794.
- PowerPoint's "A4 Paper" preset gives 1040×720 (landscape) or 720×1040 (portrait). Note that
  720×1040 is exactly the registry's 9:13 canvas.

| Master → target | Mismatch | Mode | s / band | s_b | Reach |
|---|---|---|---|---|---|
| 1123×794 → wide-16x9 | 20.4% | brand (tolerance) | – | 0.907 | C |
| 1123×794 → a4-landscape | 0.01% | **uni** | s 1.0579, band 0.0 px | – | yes |
| 1123×794 → a4-portrait, 9:16, 9:13, 1:1, 21:9 | ≥ 29% | brand (class) | – | 0.907 / 1.058 | yes (A4-P) / P |
| 794×1123 → a4-portrait | 0.01% | **uni** | s 1.0579, band 0.0 | – | yes |
| 794×1123 → tall-9x13 | 2.1% | **uni** | s 0.9068, band 10.8 px (1.04% y) | – | P |
| 794×1123 → tall-9x16 | 20.4% | brand (tolerance) | – | 0.907 | P |
| 794×1123 → a4-landscape, 16:9, 1:1, 21:9 | ≥ 29% | brand (class) | – | | yes (A4-L) / C / P |
| 1040×720 → a4-landscape | 2.1% | **uni** | s 1.1423, band 8.8 px (1.04% y) | – | yes |
| 1040×720 → wide-16x9 | 18.8% | brand (tolerance) | – | 1.000 | C |
| 720×1040 → tall-9x13 | 0 | **id** (same size) | 1 | – | P |
| 720×1040 → a4-portrait | 2.1% | **uni** | s 1.1423, band 8.8 px (1.04% x) | – | yes |
| 960×720 (4:3) → a4-landscape | 5.7% | brand (tolerance, D3) | (s 1.1667, band 34 px) | 1.058 | yes |
| 960×720 → wide-16x9 | 25.0% | brand (tolerance) | – | 1.000 | C |

Values were computed with `classify_canvas_shape`, the registry sizes and the formulas above. The
tests pin them (§3.7).

### 3.3 Brand-only size scale `s_b`: keep physical size

`s_b = ppi_t / 96`, where:
- `96` is the master's pixel density. Both master producers work at 96 px/in:
  `ai_import.PX_PER_INCH = 96` (`ai_import.py:55, 241-247`), and the seeded 1280×720 master is a
  13.33 in slide. `CanvasContract` has no physical size, so this is a documented assumption.
- `ppi_t` is the target's pixel density: `document_canvas.width_px / (physical.width_emu / 914400)`
  when `request._document_canvas` is set (A4: 1188 px / 11.69 in ⇒ 101.6), else 96 (all presets
  are 96 px/in by the registry EMU).

The result is 1.000 on every preset and 1.058 on A4.

Rationale:
- Master type sizes are points (`semantic_styles.*.font_pt`), and the svg rules keep type constant
  across canvases (`composition_clause`: "keep the same margins rather than setting smaller type").
- Today's `min(scale_x, scale_y)` would shrink a portrait title by 44% (×0.5625) on 9:16. The
  short-side ratio would enlarge A4 type by 17%.
- The physical ratio is also consistent with uniform mode: an A4 master on A4 gets s = 1.0579 ≈ s_b.

Plumbing: `project_master(contract, W, H, *, px_per_inch=96.0)`. `_resolve_master_rules` passes
`px_per_inch` from `request._document_canvas` when present. The default keeps every existing
caller and test unchanged.

### 3.4 Furniture re-anchoring rule

Defined for all five furniture elements (`FurnitureRegistry`, `contract.py:315-345`) and applied in
brand-only mode. Per axis (x shown; y is symmetric), with master box `(x, w)` on `Wm` and target
`W`:
- **span**: both insets ≤ 10% of `Wm`. Keep both insets scaled: `x' = x·s_b`,
  `w' = W − x·s_b − (Wm − x − w)·s_b`. Only the length changes. Thickness scales by `s_b`, so the
  element is never distorted.
- **start**: center `< Wm/3`. `x' = x·s_b`.
- **end**: center `> 2·Wm/3`. `x' = W − (Wm − x)·s_b`, preserving the far-edge inset.
- **center**: otherwise. `x' = W/2 − (Wm/2 − x)·s_b`.
- Non-span size: `w' = w·s_b`, `h' = h·s_b` (uniform).
- **Clamp**: if the box would cross the canvas, shrink it to fit between the start inset and its
  mirror. Text kinds shrink width only. `logo` and `pageNumber` shrink both dimensions by one factor
  (aspect kept). Record `clamped: true`.

Worked example, test fixture master (`tests/support/authored_vnext_master_fixture.py:103-143`):
- **→ A4-L** (s_b 1.058):
  - logo top-left → (80.4, 31.8, 33.9×33.9);
  - footer start / bottom → (80.4, 801.9, 687.9×23.3);
  - divider span / bottom → (80.4, 789.2, 1027.2×1.1);
  - page number bottom-right → (1061.0, 801.9).
- **→ 9:16** (s_b 1.0):
  - footer 76 + 650 > 720 → clamped to 568 wide;
  - divider → 568;
  - page number → (600, 1244).

What becomes author-visible:

| Element | svg reality | 1b behavior |
|---|---|---|
| page number | System-stamped at `(W-32, H-20)` on every canvas (`svg_contract.py:267-277`); the master position is ignored on 16:9 too | Record only, `render: system` |
| logo, brand wordmark asset | No svg channel. The author may place only offered `source-slot-*` images (`author_slide_svg.py:44-49`); master assets are not offered | Record only, reason `svg_master_asset_channel_missing` |
| header, footer text | Renderable as `<text>`, but **not projected on 16:9 today** | Record only (D2) |
| divider | Projected on 16:9 (as a decoration) | Uniform: kept (contain transform). Brand-only: dropped per the user |

The anchored boxes are computed and recorded in the prompt-invisible receipt (§3.5), so D2 becomes
a visibility switch rather than new geometry work. Rendering header/footer only off-16:9 would make
the same master look different by canvas, which is why D2 is left open rather than decided here.

### 3.5 Receipts and diagnostics

Prompt-visible (inside `master_template`, non-identity modes only):
- `version` / `rule_version`: `svg-master-projection/v2`. Identity stays `…/v1`, byte-identical.
- `canvas_fit`: `{mode, master_canvas:{width,height}, target_canvas:{width,height}, scale, reason}`.
- `validation.dropped`: `[{part, reason}]`. Parts: `layouts.standard`, `furniture.divider`,
  `allowed_variations.max_columns`. Reasons: `canvas_shape_class_differs` or
  `canvas_aspect_mismatch_exceeds_tolerance`.

Prompt-invisible:
- A new snapshot key `master_projection_receipt`, emitted only when not identity. It is added to
  the exclusion set at `author_slide_svg.py:112` (`{"references", "diagnostics",
  "master_projection_receipt"}`). It carries the full numbers: shape classes, mismatch, tolerance,
  `s`, offsets, band axis and px, `s_b`, `px_per_inch`, and the anchored furniture records
  `{role, kind, anchor:{x,y}, box, clamped, render, reason}`.
- Plumbing: `project_master` → `_resolve_master_rules` → `adapt_svg_style(master_receipt=)` →
  `StyleContext.master_receipt` → `project_style`. The snapshot digest therefore covers it, and it
  is persisted in `_internal_svg_style_snapshot` with the job.
- Header/footer text sits here, **not** in the prompt, so the model cannot render furniture that 1b
  does not intend to render.
- Log line in `_resolve_master_rules` for every mode, identity included: `svg_master_projection`
  with `master_id`, `mode`, `Wm×Hm`, `W×H`, `mismatch` and `dropped`.

### 3.6 Prompt impact

- **`SVG_AUTHOR_RULES`, `native_author_rules()` and `composition_clause`: unchanged.** No rules-text
  change is needed. Brand-only removes geometry from the JSON, and the existing per-shape
  composition clause (portrait, square, ultrawide, wide-landscape) becomes the only composition
  authority, which is the intended behavior.
- **16:9 byte identity.** The identity branch returns the v1 dict computed as today (`x·1.0`), with
  no new keys; the receipt key is absent, so the exclusion-set change is a no-op. That holds the
  `design` JSON, the rules text (`tests/fixtures/prompt_16x9_baseline/svg_rules.txt`, unchanged)
  and the snapshot digest. The only no-master prompt path does not touch this code at all.
  `prompt_16x9_baseline/design_*_companyStandard_*` are **authored** prompts and are unaffected.
- **No golden exists today for the svg master projection.** It must be captured on `9c426d8d5`
  before any code change (§3.7 T1).
- The only prompts that change are svg + master decks on A4 and on source-restyle/raster document
  canvases. Their current prompt contains stretched boxes, and after 1b it contains none.

### 3.7 Test plan

| # | Test | Where |
|---|---|---|
| T1 | **Capture first, in its own commit on unchanged code.** Goldens are `project_master_contract(fixture, 1280, 720)` as canonical JSON, and the svg `design` string (`adapt_svg_style(..., master_rules=, master_pin=)` minus references/diagnostics, exactly as `author_slide_svg.py:112` builds it) for the `default` and `cobalt-editorial` styles. Both must pass unchanged after 1b. | `tests/fixtures/svg_master_projection/identity_*.json`, `design_16x9_*.txt`; `tests/unit/test_master_projection_golden.py` |
| T2 | One golden per (master fixture × registry canvas): 16:9, 1123×794, 794×1123, 1040×720, 720×1040 and 960×720 masters × 7 canvases = 42 files. Each asserts the §3.2 mode, reason, `s`/`s_b` and band. | same |
| T3 | No-stretch invariant, for every emitted box in every mode: `w'/w == h'/h` (1e-9), except span furniture, where only the length changes and the thickness ratio equals `s_b`. | property test over T2 outputs |
| T4 | Band ≤ 2.5% per side in uniform mode. Brand-only has `layouts == []`, `decorations == []` and no `max_columns`. Tokens and families are equal across modes. | unit |
| T5 | Boundaries: `m` exactly 0.05 is uniform, 0.0501 is brand; `baseline` is treated as wide-landscape; class-border pairs (ratios 1.09 vs 1.12) go brand-only. | unit |
| T6 | Furniture anchors: start, end, center, span, clamp (text vs logo); the §3.4 worked example numbers. | unit |
| T7 | Receipt is absent at identity and present otherwise; it is excluded from the prompt; the snapshot digest changes only for non-identity; the log line is emitted in every mode. | `test_svg_style_adapter.py`, `test_svg_generation_admission.py` |
| T8 | `px_per_inch` comes from `_document_canvas` (A4 → 1.058) and defaults to 96. | `test_svg_generation_admission.py` |
| T9 | Existing `test_master_projection.py` passes unchanged. `test_scales_geometry_to_a_different_canvas` (1280 → 2560×1440) becomes uniform with `s = 2`, so its assertion holds. | existing |
| T10 | Admission baseline unchanged by 1b: `scripts/canvas_admission_table.py` shows 0 differences. | fixture |

Live check, after unit tests. Use an isolated backend: **not** the canvas-bench backend
(:8091/:22600) and not the shared stack. Run one API request: `strategy: svg`, `pageSize: A4`,
`orientation: landscape`, `masterPin` = the seeded master. Confirm three things:
- `_internal_svg_style_snapshot.master_template.canvas_fit.mode == "brand_only"`;
- no `layouts` in the logged prompt;
- the slides compose natively for A4.

Repeat on 16:9 and diff the prompt against T1.

### 3.8 Rollout and migration

- **No flag.** The projection is deterministic, and rollback is a single revert.
- **In-flight jobs keep their frozen v1 snapshot.** `pin_svg_style` never reselects on takeover, and
  staging persists the snapshot.
- Stored decks are unaffected: nothing re-reads `master_template` after authoring.
- No DB change.
- Order:
  1. T1 goldens commit.
  2. 1b code and tests.
  3. Backend restart (no reloader).
  4. Live check.
- 1a and 1b are independent and can land in either order. 1a first is the lower-risk sequencing
  (pure deletion plus one admission check).

---

## 4. Risks

1. **Visible change for A4 master decks.**
   - Today they get the master's (stretched) title/body boxes; after 1b they compose natively with
     brand colours, fonts and table/shape styles only.
   - Users may read that as "the template is gone". It is the correct outcome of "never stretch",
     but the brand carried by svg is thin: colours and fonts, no logo, no footer text (D2, D3).
2. **Tolerance calibration.** 5% is argued from margins, not measured. 4:3 masters on A4 fall 0.7
   points outside it (D3).
3. **The `s_b` density assumption.** Masters carry no physical size. A hand-authored contract at
   another DPI would get type up to that DPI ratio off. The error is bounded and recorded in the
   receipt.
4. **Byte identity** depends on the identity branch emitting the legacy dict. T1 must land before
   the change, or the guarantee is unverifiable.
5. **1a error-code change** for API clients that matched `authored_portrait_combination_not_supported`
   or `document_canvas_generation_combination_not_supported` on pack requests. No first-party client
   can send those.
6. **Shared worktree.** `authored_canvas.py`, `document_canvas_capabilities.py` and the admission
   baseline are also touched by the canvas-expansion work (the canvas bench files are modified in
   the tree now). Coordinate, and commit with a private index.
7. **Value is limited until D1 or D4.** Today 1b fixes only A4, document-canvas and raster decks, and
   those are API-only for svg.

## 5. Open decisions for the user

| # | Decision | Recommendation |
|---|---|---|
| D1 | Open 9:16, 9:13, 1:1 and 21:9 to **svg** + master (brand-only / uniform), lifting `authored_portrait_combination_not_supported` for svg only; authored stays rejected | Yes, as a separate step after 1b. The admission diff is the svg/omitted master rows on 9:16 (30 topic) and 9:13 (20 topic); 1:1 and 21:9 stay stage-gated. The master picker is authored-only in the UI, so exposing it for svg is a further UI step. |
| D2 | Furniture visibility: (a) brand-only renders header/footer text; (b) record only; (c) render in all modes via a v2 rule with a new 16:9 golden (a knowing break of byte identity) | (b) for 1b; then (c) if a footer or header is wanted. (a) makes one master look different by canvas. |
| D3 | Tolerance 5% or 6% (6% admits 4:3 → A4-L, 34 px per side) | 5% |
| D4 | Let svg project a non-1280×720 master (4:3, A4 PPTX) onto 16:9 brand-only, instead of `authored_canvas_contract_invalid` | Yes, together with D1 (the same kind of change) |
| D5 | Delete manifest v2 (`canvas_id`, `variant_of`, `status`) or keep it for the deferred family design | Delete. The family design defines v3 and replaces `variant_of` anyway. |
| D6 | Error code `design_pack_canvas_unsupported` (needs 3 prefix-filter additions) or `authored_canvas_design_pack_unsupported` (rides the existing `authored_canvas` prefix) | The former. It is clearer, and the tests cover the 422 mapping. |
| D7 | Leftover `variants/` directories: leave inert, or delete after the scan | Leave inert. The scan reports counts. |
| D8 | svg + master on A4 is labelled route `fixed-master-source-native` (`document_canvas_capabilities.py:193`): fix the ordering now (a trivial swap so svg selects `authored-reflow-svg`) or later | Later, as its own change, because it alters the A4 capability wire and baseline rows |
| D9 | `s_b` as the physical ratio (1.058 on A4) or constant logical px (1.0) | Physical |

## 6. Self-score (0–10; for risk and complexity, higher means riskier or more complex)

| Axis | Score | Why |
|---|---|---|
| Quality | 7 | Removes a dead, misleading path and a real distortion defect at their single points, with receipts and goldens. It is capped because svg can carry little of a master's brand (no logo channel; furniture deferred), so brand-only A4 decks are "on-brand colours" rather than "the company template". |
| Risk | 3 | 1a is deletion plus one admission check, with a predicted 290-row baseline diff and 0 admission flips. 1b changes the prompt only for A4, document-canvas and raster svg + master decks, which are API-only and frozen per job. The main risk is the unmeasured tolerance. |
| Complexity | 3 | 1a: about −1,100 / +150 lines (code and tests). 1b: about +220 lines of code, about +400 lines of tests, and about 44 golden files. No DB, no flags, no UI. |

Top problems:
1. Brand-only is thin for svg. The logo has no asset channel and header/footer are deferred (D2),
   so the fix trades distortion for a weak brand presence.
2. The fix reaches only API-driven A4 and document-canvas decks until D1 or D4 open presets and
   non-16:9 masters for svg.
3. No quality measurement of brand-only output: the canvas bench runs without masters. A master axis
   should be added before D1 or D4 open more traffic.
4. The 5% tolerance and the 96 px/in master assumption are argued, not measured.
