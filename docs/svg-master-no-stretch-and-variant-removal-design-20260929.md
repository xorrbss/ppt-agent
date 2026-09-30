# svg master no-stretch and design-pack variant removal: design (2026-09-29)

Status: design only. No code was changed. Base: `901a8817f` on `serve/main-20260902` (facts re-read on
that tree; the draft was written on `9c426d8d5`, whose file:line references still hold).

**rev 2 (2026-09-29).** §3 (1b) is rewritten for the user's chosen option **B: brand-only master
projection for svg on any canvas, made worthwhile by a real logo/furniture channel.** What changed
against rev 1: (a) a system-stamped furniture channel (logo, header, footer, page number) with
canvas re-anchoring, a `reserved` block for the author and an export path proven against the
converter (§3.4–§3.7); (b) admission opens svg + master on every canvas svg supports and fixes the
A4 route misroute (§3.9; D1, D4, D8 are decided here); (c) the omitted-strategy resolution is kept
as svg with the reason stated (§3.9.3); (d) goldens-first is spelled out per file (§3.10);
(e) a master axis for the canvas bench with a paired master/no-master check and a furniture
collision metric (§3.11); (f) staged implementation with LOC (§4). §1a is carried over unchanged
except that the error code is fixed as `design_pack_canvas_unsupported` (422) by user decision (D6
closed). Facts the independent review verified are folded into §0.6–§0.9.

Scope, as decided by the user:
- **1a**: remove the non-functional design-pack variant path (`17059ccb6`). Design packs are
  authored-only and 16:9-only. A pack request for any other canvas fails at admission with one
  stable code, `design_pack_canvas_unsupported` (422).
- **1b (option B)**: never stretch a company-standard master. At the single projection point,
  project either identically, with a uniform scale (same shape class, small aspect difference), or
  brand-only; in every mode the master's furniture reaches the page through a deterministic
  system channel, never through the LLM.

Relation to other documents:
- `docs/design-pack-canvas-family-design-20260929.md` (`9c426d8d5`) proposed canvas-native pack
  families. 1a declares packs 16:9-only, which **defers** that design. Its retirement of the variant
  store (§2.6) is carried out here; its S0 executor work and v3 manifest are not.
- `docs/aspect-ratio-expansion-design-20260928.md` decision 9 / §5 is superseded by 1a.
- `docs/svg-style-reference-canvas-experiment-design-20260929.md` §4 defines the composition
  metrics (M2/M3) that §3.11 reuses for the master axis.
- `docs/canvas-expansion-admission-spec-20260929.md` §4 defines the admission fixture that §3.9.5
  changes.

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
6. **Brand-only as drafted in rev 1 carried only what `style/build.py:140-167` already lifts**
   (accent, background, heading/body family). The svg author rules never name `master_template`
   (`utils/llm_calls/author_slide_svg.py:33-62`); the master JSON is advisory in the prompt. Without
   a furniture channel, B is "company colours". The user chose B knowing this; §3.4 adds the channel.
7. **The omitted-strategy fallback table has no `master_pin` row.** `strategy.py:50-66` falls back to
   authored for `registered-template` / `design_pack_pin` and for svg structural violations;
   `svg_creation_admission_violation` (`svg_generation_admission.py:54-60`) does not list
   `master_pin`. So `omit strategy + masterPin` resolves to svg. §3.9.3 keeps this deliberately.
8. **A4 route misroute (D8).** `select_a4_route` tests `has_fixed_master` before `strategy`
   (`document_canvas_capabilities.py:193-200`), so svg + master on A4 rides
   `fixed-master-source-native`, a route whose consumers are the source-native executor. It is
   admitted only because the A4 writer stage is `candidate`. §3.9.2 fixes the ordering as part of B.
9. **No golden exists for the svg design JSON at 16:9.** Only `svg_rules.txt` is pinned
   (`tests/unit/test_author_canvas_shape.py:169-176`); the `design_*_companyStandard_*` fixtures are
   **authored** prompts. §3.10 commits svg design goldens before any code.
10. **Page numbers are stamped by the system** in `services/svg_contract.py:finalize_svg` (:247-285)
    at `(W-32, H-20)`, 12 px, `tokens.body_font`, `tokens.primary`, id `svg-system-page-number`,
    after the author's own page-number element (if any) is removed. `finalize_svg` runs in
    `drivers/svg/render.py:37-40` (topic decks only: source reflow and raster reconstruction pass
    `final = svg`, :31-32) and in `services/svg_page_remake.py:57`. The logo has no svg channel:
    the author may place only offered `source-slot-*` images (`author_slide_svg.py:44-49, 186-193`).

---

## 1. Current behavior (file:line at `9c426d8d5`)

### 1.1 Master → svg author chain

| Step | Where | What |
|---|---|---|
| Canvas | `authored_canvas.py:123-169` | The source-restyle branch (124-136) and the A4 branch (137-146) return the document canvas and do not consult the master. The preset branch (147-169) requires the master canvas to equal the preset size exactly. |
| Resolve + project | `svg_generation_admission.py:103-143` | Resolves the pin (`resolve_legacy_master_pin`), then `project_master_contract(contract, canvas.width_px, canvas.height_px)` (:140). A `MasterProjectionError` becomes a 422. This is the **single projection point**; `pin_svg_style` (:146-174) is its only caller. |
| Projection | `master_projection.py:52-123` | `scale_x = W/Wm`, `scale_y = H/Hm` (:63-64). Standard-layout title and body boxes are scaled per axis (:37-49). Fonts, gaps, padding and radius use `min(scale_x, scale_y)` (:77-80, 82, 85, 92, 106-107). The divider is scaled per axis (:95-99). Logo, header, footer and page number are **not projected** (:113-118). |
| Style context | `generation_core/style/build.py:140-167` | Master accent/background and heading/body font families displace preset tokens unless the request overrides them. Master sizes and geometry are not read. |
| Snapshot | `drivers/svg/project_style.py:76-78` | Adds `master_template` (the whole rule dict) and `master_pin`. The snapshot is frozen once (`pin_svg_style`) and persisted with the job (`svg_presentation_prepare.py:103-116`, `AuthoredGenerationJob.svg_style_snapshot`) and with the deck (`drivers/svg/publish.py:113`, `theme.svg.styleSnapshot`). It is never reselected on takeover. |
| Prompt | `utils/llm_calls/author_slide_svg.py:112-113` | The snapshot, minus `references` and `diagnostics`, is `json.dumps`-ed as the first block of the user prompt. `master_template` therefore reaches the model **verbatim as JSON**, including `layouts[].title_box/body_box`, `decorations`, `allowed_variations.max_columns: 1` and `validation.limitations`. |
| Rules text | `author_slide_svg.py:33-61, 75-79` | Never names `master_template`. It names `tokens.primary/background/heading_font/body_font` (which carry the master's brand via `build.py`) and appends `composition_clause(W, H)` for non-baseline canvases. `svg_author_rules(style_snapshot, floor)` receives the whole snapshot, so a master-conditional sentence can be appended without touching the no-master bytes. |
| Page number | `services/svg_contract.py:261-282` | System-stamped at `(W-32, H-20)` on every canvas. The rules forbid the author from drawing one and reserve a 64×36 box at bottom-right (`author_slide_svg.py:54`). The master's page-number furniture is ignored on every canvas, 16:9 included. |

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
  touch the svg path described here, and the divider is a line. No change is proposed: reported only.
- **Master resolution order in the sync endpoint.** `presentation_generate.py:533-548` runs canvas
  admission first and `_validate_authored_master_pin_admission` (:281-331) after it; the latter
  sets `request._authored_master_contract` for every authored-template request with a pin,
  svg included. A later `request_canvas_metadata(request)` (:1939) therefore re-enters the preset
  branch of `request_canvas_contract` **with** the master canvas, which is why a non-1280×720
  master, or any preset other than 16:9, fails svg with `authored_canvas_contract_invalid` even
  if admission were opened. §3.9.1 makes that branch strategy-aware.

### 1.4 Facts that decide the furniture channel (new in rev 2)

- **Master furniture model.** `models/authored_vnext/master/contract.py:300-345`: five locked
  elements (`logo`, `header`, `footer`, `divider`, `page_number`), each with `bounds` on the master
  canvas, `visibility` ∈ {always, exceptCover, coverOnly, hidden}, `text`, `asset_id`, `fill`,
  `stroke`. `brand_assets.logo` is an `AssetReference {asset_id, digest, alt_text}` and must agree
  with `furniture.logo.asset_id` (:581-588). The seeded catalogue master
  (`master/catalog_seed.py:120-222`) has the logo at (1134, 675, 78×38) bottom-right, header
  (60, 30, 300×26) top-left, footer (925, 30, 295×26) top-right, divider (60, 662, 1160×1) and the
  page number (610, 680, 60×22) bottom-centre, `exceptCover`. Its page number is **not** where the
  system stamps one, and its logo box overlaps the system's bottom-right reservation.
- **Logo bytes.** `master/assets.py:38-123`: `OwnerMasterAssetStore(root)` is content-addressed
  PNG storage (`<root>/<hh>/<sha256>.png`), max 5,000,000 bytes, `open_verified(asset_id, digest)`
  re-verifies the digest and PNG header. The owner root is
  `APP_DATA_DIRECTORY/authored_vnext/master-assets/users/<owner_scope>` (constructed in
  `authored_vnext/generation/legacy_bridge.py:500-505` and `authored_vnext/ooxml/service.py:106-109`).
- **svg image limits.** `svg_contract.validate_raster_data_url` (:60-76): PNG/JPEG only, ≤ 1536 KiB
  per image, ≤ 8192 px edge; `validate_svg` caps total data-URL bytes at 2 MiB per page (:205-207)
  and requires integer `width`/`height` on `<image>` (:213-214). A derivative is therefore needed
  for a 5 MB master asset: `services/svg_image_derivative.py:_build_raster_derivative` produces a
  verified sRGB PNG and **keeps alpha** (:83-97, PNG whenever alpha or non-photo, :105).
- **Canonical-asset decks treat every `<image>` as a slot.** On durable svg runs
  (`slot_writer_enabled`, `svg_generation_assets.py:14-16`), `screen_placement` walks every
  `<image>` and indexes `slots[href]` (:175-184); at export `svg_export_assets.build_slide_export_assets`
  (:290-304) and `validate_export_placement` (:15-58) require every `<image>` to name an offered
  slot (`svg_export_slot_not_offered`, `svg_export_placement_count_mismatch`), and
  `validate_svg(source_manifest=...)` rejects any other `href` (`svg_source_image_unknown_slot`,
  :199-203). A system-stamped logo must be exempted at these four seams (§3.6).
- **Export is already native for text and pictures.** The vendored converter turns `<text>` into
  editable text boxes and `<image href="data:image/png;base64,…">` into `<p:pic>`
  (`vendor/slide_master/svg_to_pptx/drawingml/elements.py:3115-3250`, `_decode_data_image_uri`
  :70). The `NativeObjects` sidecar carries only `table` and `chart` kinds
  (`drivers/svg/native_publish.py:48-71`, `export_projection.py:47-51`); it exists for semantics the
  SVG cannot express, which furniture does not need. PDF export renders the same SVG through
  Chromium as a vector page (`workers/public_svg_pdf_export.py:22-24`).
- **Native declarations bind to the SVG digest minus the page number.**
  `native_declarations.native_binding_digest` (:144-156) strips only `svg-system-page-number`
  before hashing; anything else stamped after authoring would make every declared table/chart
  `native_declaration_stale_svg` (`native_publish.py:90-93`). The furniture group must be stripped
  by the same function (§3.6).
- **Renderer measurements.** `svg_renderer.render_svg_with_metrics` (:228-330) measures every
  element (`metrics.elements`, with `tag`, `bbox`, `qid`, `has_owned_text`, `font_size`), exempts
  only the page-number qid from `small_body_text` (:276, :300), and flags `text_overlap` on glyph
  ink between owned-text fragments (`svg_text_overlap.py`). Header/footer text stamped by the
  system would be measured like author text.

---

## 2. Design 1a: remove the variant path; packs are 16:9-only

### 2.1 Rule and error code

A request with `design_pack_pin` is admitted only on 16:9: `page_size is None` and
`aspect_ratio == "16:9"`. Anything else fails at admission with:

- **`design_pack_canvas_unsupported`**, HTTP 422 (user decision; D6 closed).
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
- The canvas bench, which uses neither packs nor masters (`scripts/canvas_bench*.py`). (§3.11 adds
  the master axis; packs stay out.)

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

**Implementation (2026-09-29, §1a done).** Pre-removal scan of `app_data/design-packs` (53 base manifests, 1 owner store): 0 `variants/` manifests, 0 manifests with `source-native-design-pack/v2`.
- Deleted: `variants.py`, `variant_store.py`, `variant_source.py`, `scripts/design_pack_variants.py`, `test_design_pack_variants.py`, `test_design_pack_variants_admission.py`. `authored_canvas.py`, `document_canvas_capabilities.py`, `generation_route.py` and `registry.py` were restored to their pre-`17059ccb6` content (no later commit touched them), then re-edited; the `design-pack-canvas-variant` route and manifest v2 fields are gone; `registry.canvas_id` is kept.
- Added: the front-of-admission check in `validate_authored_canvas_admission` (`design_pack_canvas_unsupported`), the route-loader defence in `load_pinned_design_pack` (`DESIGN_PACK_CANVAS_ID = "wide-16x9"`), the `design_pack_canvas` prefix in the three 422 filters (`presentation_generate.py` x2, `public_v2_orchestrator.py`) and in the fixture script's mirror of them (`scripts/canvas_admission_table.py:_MAPPED_PREFIXES`; without it the fixture recorded `ERR ValueError`).
- Fixture: 290 of 3,654 rows changed, code only, 0 admitted/rejected flips: 100 from `authored_canvas_stage_disabled`, 90 from `authored_portrait_combination_not_supported`, 100 from `document_canvas_generation_combination_not_supported` (the §2.2 breakdown summed).
- Tests: new `test_design_pack_canvas_admission.py` (28) and `test_design_pack_manifest_and_render.py` (6). The async and v2 422 mappings are covered by the shared prefix filter but not by an end-to-end test (only the sync endpoint is exercised).
- Ledger: `compatibility/upstream-byte-parity.json` edited by hand (6 entries out, 2 in, `policySha256` and `localOnly` recomputed) because `--write` would also have absorbed other sessions' untracked files and switched the ledger to working-tree mode.

---

## 3. Design 1b, option B: brand-only projection with a system furniture channel

### 3.0 Shape of the change

Three layers, each with one owner:

| Layer | Owner (single point) | What it decides |
|---|---|---|
| Projection | `generation_core/style/master_projection.py` (`project_master`) | mode (identity / uniform / brand-only), scaled geometry for the prompt, furniture anchoring on the target canvas, `reserved` boxes, receipt |
| Stamping | `services/svg_contract.py` (`finalize_svg`) | draws furniture from the frozen snapshot after authoring, exactly where the page number is drawn today |
| Admission | `services/authored_canvas.py` + `document_canvas_capabilities.py` | which canvases admit svg + master (all svg supports), route labels |

The LLM never draws furniture. It is told where furniture will be (`reserved`) and nothing else.
The snapshot stays the only state: projection output (prompt-visible), furniture plan and logo
bytes (prompt-invisible) are all keys of `_svg_style_snapshot`, so job takeover, page remake and
publication read one frozen source (§1.1 Snapshot).

### 3.1 Decision rule (at `master_projection.py`, the single projection point)

Inputs:
- master canvas `(Wm, Hm)` = `contract.canvas`;
- target `(W, H)` = the canvas `pin_svg_style` already resolves;
- target pixel density `ppi_t` (§3.3).

Shape class: `utils/llm_calls/author_canvas_shape.classify_canvas_shape` (:24-34), with one
normalization. `baseline` is a **size** identity (exactly 1280×720), not an aspect class; its
ratio 1.778 lies in the wide-landscape interval, so `baseline` is compared as `wide-landscape`.
Without this, an A4-landscape master and a 16:9 master could never share a class.

Aspect mismatch: `m = 1 − min(rm, rt) / max(rm, rt)`, where `r = width / height`.

| Mode | Condition | Prompt-visible output |
|---|---|---|
| `identity` | `(Wm, Hm) == (W, H)` | Today's v1 dict, byte-identical **except** the added `reserved` key (§3.5) |
| `uniform` | same class and `m ≤ 0.05` | Master geometry under one scale `s = min(W/Wm, H/Hm)`, centred; `reserved` |
| `brand_only` | otherwise | Brand only; geometry dropped and recorded; `reserved` |

Furniture is stamped in **every** mode (§3.4). That is what makes the same master look like the
same master on 16:9 and on A4, and it is why rev 1's D2 is closed by B rather than left open.

**Why 5%.**
- A uniform fit leaves a band of `(1 − q)/2` of the banded axis on each side, where `q = 1 − m`. At
  `m ≤ 0.05` that is at most **2.5% per side**: ≤ 32 px on a 1280 axis and ≤ 30 px on a 1188 axis.
  That is at most half of the registry's safe margin (64 px on presets, 60 px on A4;
  `constants/canvas_registry.json`), so the band reads as a slightly wider margin, not as an empty
  strip.
- Every realistic pair inside 5% is a real "same page" relation: the same aspect at a different
  size, or PowerPoint's A4 preset (10.83×7.5 in, 13:9) against the registry's A4 (≈2.1%).
- The nearest excluded pair is 4:3 → A4 landscape: 5.7%, 34 px per side (D3, kept at 5%).
- The class test is kept as well, as the user asked. It never binds inside 5% except near class
  borders (0.95, 1.1, 1.9). There it stops a master composed for one class (for example a square
  grid) from being imposed where the composition clause asks for another.

**Uniform mode and the band.**
- Every box (layout slots, the divider decoration, and the furniture) becomes
  `(ox + x·s, oy + y·s, w·s, h·s)`, with `ox = (W − Wm·s)/2` and `oy = (H − Hm·s)/2`.
- Fonts, `gap`, `min_gap`, `cell_padding` and `radius` are multiplied by `s`. Today's code already
  uses `min(scale_x, scale_y)`, which equals `s`.
- The band is left empty. The canvas background is the master's `tokens.background` (full-bleed:
  the author paints the whole canvas background, `author_slide_svg.py:50`), so the band is visually
  background.
- Furniture is **not** re-anchored to the true edges in uniform mode: moving it would change the
  master's own furniture-to-content spacing by up to the band, which is exactly the relationship
  uniform mode exists to preserve.

**Brand-only mode.** It emits the same top-level keys as today, with:
- `layouts: []` and `decorations: []`. The divider is dropped, per the user's direction (it is a
  layout rule, not a brand mark; O2 records the alternative).
- `allowed_variations`: `{"model_layout": true, "min_gap": 12·s_b}`. `max_columns: 1` is
  **dropped**: it is a property of the single 16:9 standard layout, and it contradicts the 1:1
  (2×2 grid) and 21:9 (2–3 columns) composition clauses.
- Tokens unchanged. Font families unchanged. Font sizes, `cell_padding`, `radius` and `min_gap` use
  `s_b` (§3.3).
- `validation.automatic`: `["colors", "title_hierarchy", "furniture"]` (`layout_geometry` removed;
  `furniture` added because the channel is deterministic and measured).
- `validation.limitations`: accurate for this mode, including "Master layout geometry is not
  projected on this canvas; compose natively for it. Logo, header, footer and page number are
  drawn by the system inside `reserved`."
- `validation.dropped`: one entry per dropped part (§3.8).

In every mode, the existing checks stay first:
- `svg_master_canvas_unsupported`;
- `svg_master_layout_geometry_missing`. A furniture-only master is rejected on every canvas, as it
  is on 16:9 today, so admission of a given master never depends on the canvas.

### 3.2 Rule table: every registry canvas

Legend:
- `id`/`uni`/`brand` = mode.
- `s` = uniform scale; band = per-side band in uniform mode.
- `s_b` = brand-only size scale (§3.3).
- **Reach** = reachable today (rev 1 facts) / after B (§3.9). `P` = rejected by preset admission
  today (`authored_portrait_combination_not_supported`, or stage disabled first on 1:1/21:9);
  `C` = rejected by the 16:9 exact-canvas check (`authored_canvas_contract_invalid`); `G` = still
  behind the 1:1/21:9 stage gate after B.

The svg source-restyle and raster canvases (1188×h from the source PPTX, ≈1188×840) behave as
A4-L for projection; furniture is **not** stamped on them (§3.4, "where it stamps").

**From a 16:9 master (1280×720; the seeded catalogue master and every 13.33×7.5 in PPTX).**

| Target | Class | Mismatch | Mode | Scale | Reason recorded | Reach today / after B |
|---|---|---|---|---|---|---|
| wide-16x9 1280×720 | wide-landscape | 0 | **id** | 1 | – | yes / yes |
| tall-9x16 720×1280 | portrait | 68.4% | brand | s_b 1.000 | `canvas_shape_class_differs` | P / **yes** |
| tall-9x13 720×1040 | portrait | 61.1% | brand | s_b 1.000 | `canvas_shape_class_differs` | P / **yes** |
| square-1x1 720×720 | square | 43.8% | brand | s_b 1.000 | `canvas_shape_class_differs` | P / G |
| ultrawide-21x9 1680×720 | ultrawide | 23.8% | brand | s_b 1.000 | `canvas_shape_class_differs` | P / G |
| a4-landscape 1188×840 | wide-landscape | 20.5% | brand | s_b 1.058 | `canvas_aspect_mismatch_exceeds_tolerance` | **yes** (stretched x·0.928, y·1.167) / yes |
| a4-portrait 840×1188 | portrait | 60.2% | brand | s_b 1.058 | `canvas_shape_class_differs` | **yes** (stretched x·0.656, y·1.650) / yes |

**From A4 masters.** A4 masters exist: `ai_import._canvas` rounds `sldSz` at 96 px/in.
- A 297×210 mm PPTX gives 1123×794.
- PowerPoint's "A4 Paper" preset gives 1040×720 (landscape) or 720×1040 (portrait). Note that
  720×1040 is exactly the registry's 9:13 canvas.

| Master → target | Mismatch | Mode | s / band | s_b | Reach today / after B |
|---|---|---|---|---|---|
| 1123×794 → wide-16x9 | 20.4% | brand (tolerance) | – | 0.907 | C / **yes** (D4) |
| 1123×794 → a4-landscape | 0.01% | **uni** | s 1.0579, band 0.0 px | – | yes / yes |
| 1123×794 → a4-portrait, 9:16, 9:13, 1:1, 21:9 | ≥ 29% | brand (class) | – | 0.907 / 1.058 | yes (A4-P), P / yes, yes, G |
| 794×1123 → a4-portrait | 0.01% | **uni** | s 1.0579, band 0.0 | – | yes / yes |
| 794×1123 → tall-9x13 | 2.1% | **uni** | s 0.9068, band 10.8 px (1.04% y) | – | P / **yes** |
| 794×1123 → tall-9x16 | 20.4% | brand (tolerance) | – | 0.907 | P / **yes** |
| 794×1123 → a4-landscape, 16:9, 1:1, 21:9 | ≥ 29% | brand (class) | – | | yes (A4-L), C, P / yes, yes, G |
| 1040×720 → a4-landscape | 2.1% | **uni** | s 1.1423, band 8.8 px (1.04% y) | – | yes / yes |
| 1040×720 → wide-16x9 | 18.8% | brand (tolerance) | – | 1.000 | C / **yes** (D4) |
| 720×1040 → tall-9x13 | 0 | **id** (same size) | 1 | – | P / **yes** |
| 720×1040 → a4-portrait | 2.1% | **uni** | s 1.1423, band 8.8 px (1.04% x) | – | yes / yes |
| 960×720 (4:3) → a4-landscape | 5.7% | brand (tolerance, D3) | (s 1.1667, band 34 px) | 1.058 | yes / yes |
| 960×720 → wide-16x9 | 25.0% | brand (tolerance) | – | 1.000 | C / **yes** (D4) |

Values were computed with `classify_canvas_shape`, the registry sizes and the formulas above. The
tests pin them (§3.10 T2).

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

Plumbing: `project_master(contract, W, H, *, px_per_inch=96.0, logo=None)` (the `logo` argument is
§3.4.4). `_resolve_master_rules` passes `px_per_inch` from `request._document_canvas` when
present. The default keeps every existing caller and test unchanged.

### 3.4 Furniture channel

#### 3.4.1 What is stamped, where, and on which pages

| Master furniture | svg element stamped | id | Source of text/bytes |
|---|---|---|---|
| `logo` | `<image … preserveAspectRatio="xMidYMid meet" href="data:image/png;base64,…">` | `svg-system-logo` | `brand_assets.logo` → owner asset store → derivative (§3.4.4) |
| `header` | `<text>` | `svg-system-header` | `furniture.header.text` |
| `footer` | `<text>` | `svg-system-footer` | `furniture.footer.text` |
| `page_number` | `<text>` (the existing element, moved) | `svg-system-page-number` | `str(index + 1).zfill(2)` as today |
| `divider` | none in brand-only (dropped, §3.1); in identity/uniform it stays a `decorations` hint for the author as today | – | – |

All stamped elements sit in one `<g id="svg-system-furniture">` appended as the **last** child of
the root (after content, so nothing the author drew paints over it), except the page number, which
keeps its own id and top-level position so that everything keyed on `PAGE_NUMBER_ID` today keeps
working. With no master, `finalize_svg` behaves exactly as today (page number at `(W-32, H-20)`);
with a master, the page number is stamped at the master's re-anchored box (§3.4.2) with
`text-anchor` from its anchor class, in `furniture.page_number.fill` (fallback `tokens.primary`).

Visibility (`FurnitureElement.visibility`): `always` on every page; `exceptCover` on every page
except index 0; `coverOnly` only on index 0; `hidden` never. "Cover" is page index 0 (the svg
driver's role vocabulary is not needed; O3 notes the alternative).

**Where it stamps.** Exactly where the page number is stamped today: `finalize_svg` on topic decks
(`drivers/svg/render.py:37-40`) and on page remake (`svg_page_remake.py:57`). Source reflow and
raster reconstruction bypass `finalize_svg` (`render.py:31-32`) and therefore get neither a page
number nor furniture today; B keeps that (the source document owns its own chrome), and the
projection receipt records `furniture: not_stamped, reason: source_document_canvas`.

**Before stamping**, any author element whose id starts with `svg-system-` is removed (the same
defensive rule the page number has, `svg_contract.py:263-266`), and the rules forbid such ids
(§3.5).

#### 3.4.2 Re-anchoring (brand-only) and transform (uniform)

Defined for the four stamped elements. Per axis (x shown; y is symmetric), with master box
`(x, w)` on `Wm` and target `W`; `s_b` from §3.3:
- **span**: both insets ≤ 10% of the master axis. Keep both insets scaled: `x' = x·s_b`,
  `w' = W − x·s_b − (Wm − x − w)·s_b`. Only the length changes.
- **start**: centre `< Wm/3`. `x' = x·s_b`.
- **end**: centre `> 2·Wm/3`. `x' = W − (Wm − x)·s_b`, preserving the far-edge inset.
- **center**: otherwise. `x' = W/2 − (Wm/2 − x)·s_b`.
- Non-span size: `w' = w·s_b`, `h' = h·s_b` (uniform; never distorted).
- **Clamp inside the safe area of the registry canvas** (`safeArea`, 64 px presets / 60 px A4),
  not merely inside the canvas: if the box would cross the safe area on an axis, shift it inward
  to the safe-area edge; if it still does not fit, shrink it. Text kinds shrink width only.
  `logo` shrinks both dimensions by one factor (aspect kept). Record `clamped: true`.
- **Same-row separation**: after remapping, if two furniture boxes overlap (this happens when a
  start-anchored header and an end-anchored footer meet on a narrow canvas), shrink the text
  box(es) on the overlapping axis so that a 16 px gap remains; record `clamped: true` on each.
- Identity mode: no remap (`s_b = 1`, target = master). Uniform mode: the §3.1 contain transform
  `(ox + x·s, oy + y·s, w·s, h·s)`, no re-anchoring.

Geometry is rounded to 0.1 px; `<image>` `width`/`height` are rounded to integers (contract
requirement).

Worked example, **seeded catalogue master** (`catalog_seed.py:170-222`; anchor classes:
header start/start, footer end/start, logo end/end, page number center/end):

| Target (s_b) | header (x, y, w×h) | footer | logo | page number |
|---|---|---|---|---|
| a4-landscape (1.058) | (63.5, 31.7, 317.4×27.5) | (812.4, 31.7, 312.1×27.5) | (1033.6, 792.4, 83×40) → shifted up to y 759.8 (safe bottom 780) | (562.3, 797.7, 63.5×23.3) → shifted up to y 756.7 |
| a4-portrait (1.058) | (63.5, 31.7, 317.4×27.5) | (464.4, 31.7, 312.1×27.5) | (685.5, 1140.4, 83×40) → y 1088 | (388.3, 1145.7, 63.5×23.3) → y 1104.7 |
| tall-9x16 (1.0) | (60, 30, 300×26) → clamped to safe top (y 64) | (365, 30, 295×26) → y 64; header/footer gap 5 px → both shrunk to 292/287 wide for a 16 px gap | (574, 1235, 78×38) → y 1178 | (330, 1240, 60×22) → y 1194 |
| square-1x1 (1.0) | (60, 30, 300×26) → y 64 | (365, 30, 295×26) → y 64, gap fix as above | (574, 675, 78×38) → y 618 | (330, 680, 60×22) → y 634 |
| ultrawide-21x9 (1.0) | (60, 30, 300×26) → y 64 | (1325, 30, 295×26) → y 64 | (1534, 675, 78×38) → y 618 | (810, 680, 60×22) → y 634 |

Two things the example shows: the seeded master places furniture inside 30–45 px of the edge,
which is inside every registry safe margin, so the safe-area clamp shifts every element on every
non-identity canvas (recorded, not silent); and on 16:9 identity the same master keeps its
30 px header, which the author already composes around today. Whether furniture should be
clamped to the safe area or only to the canvas is O4.

The test fixture master (`tests/support/authored_vnext_master_fixture.py:103-146`) is the second
golden subject; its numbers (rev 1 §3.4 worked example) still hold for the canvas clamp; add the
safe-area rows in T6.

#### 3.4.3 Text rendering rules (deterministic)

- Font family: the snapshot's `tokens.body_font` (which `build.py:166-167` already sets to the
  master's `font_manifest.primary` unless the request overrides it), the same family the page
  number uses today.
- Font size: `font_px = min(caption.font_pt / 0.75 · s_b, h' / 1.2)` from
  `semantic_styles.caption` (seeded 12 pt → 16 px; box 26 px → 21.7 cap → 16 px). Page number uses
  the same rule (a master deck's page number is therefore 16 px, not 12 px; no-master stays 12 px).
- Baseline: `y_text = y' + 0.75·h'`; `x_text` and `text-anchor` from the x anchor class
  (start → `x'`, `start`; center → `x' + w'/2`, `middle`; end/span → `x' + w'`, `end`).
- Fit: estimated width `Σ(1.0·font_px for CJK, 0.55·font_px otherwise)`. If it exceeds `w'`, reduce
  `font_px` down to 8 px; if it still exceeds, the element is **not stamped** and the receipt
  records `{role, reason: "furniture_text_exceeds_box"}`. Text is never truncated.
- Fill: `furniture.<role>.fill`, else `tokens.text` (the master's `text_secondary` is what the
  seeded contracts set explicitly).
- `font-weight` normal (the trusted-font rule at `author_slide_svg.py:52-53` applies).

#### 3.4.4 Logo bytes: from the asset store to the page

At `_resolve_master_rules` (admission, before any provider spend), when
`contract.brand_assets.logo` is set and `furniture.logo.visibility != "hidden"`:

1. Open the owner store: `OwnerMasterAssetStore(APP_DATA_DIRECTORY/authored_vnext/master-assets/users/<owner_scope>)`,
   built by a helper extracted from `legacy_bridge.py:492-505` (`owner_master_asset_store(owner_id)`)
   so the two existing constructions and this one share the path rule.
2. `open_verified(asset_id, digest)` (re-verifies sha256 and PNG header; fenced owners raise).
   Failure → HTTP 422 `svg_master_logo_asset_unavailable` (fail closed: a published master whose
   asset is missing is corrupt data, not a reason to print a deck without its logo).
3. Derive a page-sized PNG: `svg_image_derivative._build_raster_derivative(png, semantic_kind="unknown",
   max_bytes=256·1024, max_edge=clamp(ceil(2·max(w', h')), 64, 1024), min_edge=64)`; alpha is kept
   (:83-97). Failure → 422 `svg_master_logo_derivative_failed`.
4. Store in the snapshot (prompt-invisible) as
   `master_furniture.logo = {asset_id, digest, derivative_sha256, media_type: "image/png", width, height, bytes, data_url}`.
   The 256 KiB cap keeps a page under `validate_svg`'s 2 MiB total even on non-durable decks that
   inline their own images.

The derivative is computed once per deck and frozen with the snapshot, so remake and takeover use
identical bytes; the snapshot digest covers it, so a different logo means a different style digest.

#### 3.4.5 Reserved zones for the author

`master_template.reserved` (prompt-visible, present in every mode when a master is pinned):

```json
"reserved": [
  {"id": "svg-system-header", "role": "header", "x": 63.5, "y": 31.7, "width": 317.4, "height": 27.5},
  {"id": "svg-system-logo", "role": "logo", "x": 1033.6, "y": 759.8, "width": 83, "height": 40},
  …
]
```

Boxes are the stamped boxes inflated by 8 px on each side and clipped to the canvas, one per
element that will be stamped on **any** page (visibility-independent, so the cover and the body
pages get the same instruction). The rules text gains one sentence, appended by
`svg_author_rules` only when `style_snapshot["master_template"]["reserved"]` is non-empty:

> `\nRESERVED FURNITURE (authoritative): the system draws the company logo, header, footer and page number after you. Keep every element, including backgrounds you tint, out of each box in master_template.reserved (x, y, width, height in canvas px). Never use an id that starts with svg-system-.\n`

The existing bottom-right 64×36 sentence stays (byte identity for no-master); with a master the
system page number is inside `reserved`, so the two never disagree.

### 3.5 Prompt impact

- **`SVG_AUTHOR_RULES`, `native_author_rules()` and `composition_clause`: unchanged.** The only
  rules change is the master-conditional sentence in §3.4.5, appended inside `svg_author_rules`
  after the composition clause.
- **16:9, no master: byte identity.** Neither `master_template` nor `master_furniture` exists;
  the exclusion-set change at `author_slide_svg.py:112` is a no-op; `svg_author_rules` appends
  nothing. `tests/fixtures/prompt_16x9_baseline/svg_rules.txt` and the new no-master design
  goldens (§3.10 G1) pass unchanged. `prompt_16x9_baseline/design_*_companyStandard_*` are
  **authored** prompts and are unaffected.
- **16:9 with a master (identity): a knowing, bounded change.** The design JSON gains exactly
  `master_template.reserved`; the rules gain exactly the §3.4.5 sentence. §3.10 G2 captures today's
  bytes first, and the post-change test asserts equality **after removing those two additions**,
  so any other drift fails.
- **Non-16:9 with a master**: the prompt loses the stretched `layouts`/`decorations` (brand-only)
  and gains `reserved` and `canvas_fit`.
- Prompt-invisible keys (`master_furniture`, `master_projection_receipt`) are added to the
  exclusion set `{"references", "diagnostics", "master_furniture", "master_projection_receipt"}`.

### 3.6 Export and the four `<image>` seams

**Decision: stamp furniture into the stored SVG; export rides the existing converter. No sidecar.**

Evidence (§1.4): the converter already emits native `<p:pic>` for a data-URL `<image>` and native
text boxes for `<text>`, which is exactly "native OOXML text/picture"; the `NativeObjects` sidecar
is typed for `table`/`chart` only and would need a vendored-converter extension to carry a
picture, for no gain. PDF export renders the same SVG. The M0 baseline finding (svg export is 100%
editable text) therefore extends to furniture without new export code.

Seams that enumerate `<image>` or hash the SVG, and the single rule applied to them: **an element
whose id starts with `svg-system-` is a system element, never a slot and never author content.**

| Seam | Today | Change |
|---|---|---|
| `svg_contract.validate_svg` href check (:199-207) | `source_manifest` set → every `href` must be a slot | system-id `<image>` validates its `href` as a raster data URL (the manifest-less branch) |
| `svg_contract.validate_svg` id check (:193-198) | only `svg-system-page-number` reserved | every `svg-system-*` id reserved when `allow_system_page_number=False` (author output); `finalize_svg` strips them defensively |
| `svg_generation_assets.screen_placement` (:175-184) | indexes `slots[href]` for every image | skip system ids |
| `svg_export_assets._positioned_images` / `validate_export_placement` (:15-58) / `build_slide_export_assets` (:290-304) | every image is a slot; count must equal placements | skip system ids in `_positioned_images` (one place; the three callers inherit it) |
| `native_declarations.native_binding_digest` (:144-156) | strips `svg-system-page-number` | also strips `<g id="svg-system-furniture">` |
| `svg_renderer.render_svg_with_metrics` (:276, :300) | page-number qid exempt from `small_body_text` | every `svg-system-*` qid exempt; see §3.7 for the collision failure |
| `svg_export_page_evidence` ("offered images and icons used") | counts images against offers | verify at implementation that system images are excluded (grep gate in §4 S3) |

Font embedding for PPTX (`svg_pptx_font_embedding.py`) keys on families used in the SVG; furniture
uses the same family as content, so no change.

### 3.7 Quality gate: collisions are measured, not assumed

Header/footer/page-number text stamped by the system is owned text to the renderer, so a content
fragment that overlaps it already fails `text_overlap` (glyph ink) and triggers the driver's
correction loop; that is the desired behaviour (content must not run into furniture). The logo is
an image and would not be caught. B adds one generic check at the measurement point:

- `render_svg_with_metrics`: after collecting `elements`, take the bboxes of system elements
  (`qid` of ids `svg-system-*`, including the logo image) and of every other visible element;
  if any content bbox intersects a system bbox by more than 2 px on both axes, append
  `{"kind": "furniture_collision", "qid": <content qid>, "system": <system id>}`.
- It is an ordinary failure kind: the page retries with a correction hint like every other
  measured failure, and a residual one flags the page for review through the existing
  classification (`quality/page_outcome.py`). No new gate machinery.
- It can only fire on master decks (no system elements otherwise), so 16:9-no-master pages are
  unaffected, and the bench (§3.11) measures how often it fires and what it costs in retries.

### 3.8 Receipts and diagnostics

Prompt-visible (inside `master_template`):
- `version` / `rule_version`: `svg-master-projection/v2` in every mode (the `reserved` key is new
  in identity too, so v1 bytes are not claimed).
- `reserved`: §3.4.5.
- `canvas_fit` (non-identity only): `{mode, master_canvas:{width,height}, target_canvas:{width,height}, scale, reason}`.
- `validation.dropped` (brand-only): `[{part, reason}]`. Parts: `layouts.standard`,
  `furniture.divider`, `allowed_variations.max_columns`. Reasons: `canvas_shape_class_differs` or
  `canvas_aspect_mismatch_exceeds_tolerance`.

Prompt-invisible snapshot keys (present whenever a master is pinned):
- `master_furniture`: `{version: "svg-master-furniture/v1", elements: [{id, role, kind, box:{x,y,width,height}, anchor:{x,y}, clamped, visibility, text?, fill, font_px}], logo: {…§3.4.4} | null, dropped: [{role, reason}], stamped: true | false, not_stamped_reason?}`.
- `master_projection_receipt`: shape classes, mismatch, tolerance, `s`, offsets, band axis and px,
  `s_b`, `px_per_inch`, safe area used for clamping.
- Plumbing: `project_master` → `_resolve_master_rules` → `adapt_svg_style(master_rules=,
  master_furniture=, master_receipt=)` → `StyleContext` → `project_style`. The snapshot digest
  covers all of it (`svg_style_adapter.py:107-109`), and it is persisted with the job and the deck.

Per-page receipt: `finalize_svg` returns, besides the SVG, the list of stamped ids for that page;
`render.py` records it as `payload["furniture"] = {"stamped": [...], "skipped": [{id, reason}]}`
(visibility skips and text-fit skips), which `publish.py:svg_slide_content` carries into
`svgReceipt` unchanged.

Log line in `_resolve_master_rules` for every mode, identity included: `svg_master_projection`
with `master_id`, `mode`, `Wm×Hm`, `W×H`, `mismatch`, `dropped`, `furniture_stamped`, `logo_bytes`.

### 3.9 Admission

#### 3.9.1 Open svg + master on every canvas svg supports (D1 and D4, decided)

- `validate_authored_canvas_admission` (`authored_canvas.py:347-389`), preset branch: after the
  stage gate (:360-361) and before the portrait-combination rejection, add

  > svg + master branch: `resolve_generation_strategy(request) == "svg"` and a master pin is
  > present (`master_pin` or `_authored_master_pin`) and `design_pack_pin is None` and
  > `source_mode is None` and `generation_mode in {"auto", "free-design"}` and
  > `generation_engine in {None, "authored"}` and template is `authored` and
  > `aspect_ratio in supported_aspect_ratios_for_style(style_id)` → `request_canvas_contract(request)`; return.

  Everything else keeps its current code. So 9:16 and 9:13 open now for the styles that support
  them; 1:1 and 21:9 stay behind `authored_canvas_stage_disabled` until the bench promotes a style
  (the canvas stage gates are untouched). Authored + master on presets stays rejected.
- `request_canvas_contract` (:147-154): consult `_authored_master_contract.canvas` **only when the
  resolved strategy is authored**. For svg the expected metadata is the generic preset metadata;
  the master is projected, not required to match. This single change removes the `C` rejections
  (non-1280×720 masters on 16:9, D4) and the late `authored_canvas_contract_invalid` on presets
  (§1.3, last bullet) for svg. Authored keeps its exact-size rule.
- The A4 branch (:137-146) is unchanged (already admits).
- `_validate_authored_master_pin_admission` (`presentation_generate.py:281-331`) is unchanged:
  it resolves the pin for svg too, which is harmless once the contract canvas is ignored for svg.

#### 3.9.2 Fix the A4 route ordering (D8, decided)

`select_a4_route` (`document_canvas_capabilities.py:157-201`): move the `strategy == "svg"`
selection (`authored-reflow-svg`) **above** `has_fixed_master`. The earlier combination branch
already handles svg + source content, and svg + exact reconstruction is rejected by svg admission
before this point, so the only requests the swap moves are svg + master (topic and raster) on A4:
from `fixed-master-source-native` to `authored-reflow-svg`. Both policies are implemented and the
stage is `candidate`, so admission outcomes do not change; the `/authored/canvas-capabilities`
`selection.routeId` for such a request does. `test_document_canvas.py` assertions on the route of
svg + master must be updated (grep `has_fixed_master=True` with `strategy="svg"`).

#### 3.9.3 Keep omitted-strategy resolution as svg (no authored fallback for `master_pin`)

The reviewer noted that `strategy.py:52-66` falls back to authored for `design_pack_pin` but not
for `master_pin`, which is why the stretch is reachable at all. B keeps it that way, on purpose:

1. The user chose B knowing svg carries a master as brand plus furniture, not as layout. A fallback
   would make "omit strategy + masterPin" silently run the frozen authored driver while
   "strategy: svg + masterPin" runs svg, so the same pin would produce two different products
   depending on a field the client did not send. The default must mean the default driver.
2. The authored driver is frozen and 16:9-only for masters (§1.3); routing masters there would
   re-close every canvas this design opens.
3. The fallback exists for requests svg **cannot** serve (`svg_creation_admission_violation`).
   After B, svg serves masters on every canvas it serves at all, so there is no violation to fall
   back on.

The exception table in `strategy.py`'s docstring gets one sentence saying so.

#### 3.9.4 Error codes after B

| Code | When | Status |
|---|---|---|
| `authored_canvas_stage_disabled` | svg + master on 1:1 / 21:9 while the style's stage is disabled | 422 (unchanged) |
| `authored_portrait_combination_not_supported` | svg + master on a preset the style does not support; authored + master on any preset; svg + master with restyle/raster on presets | 422 (unchanged) |
| `svg_master_canvas_unsupported` | target canvas outside 0 < W, H ≤ 4096 | 422 (unchanged) |
| `svg_master_layout_geometry_missing` | furniture-only master (no title/body slot) | 422 (unchanged) |
| `svg_master_logo_asset_unavailable` | `brand_assets.logo` set but the owner store cannot open/verify it | 422 (new) |
| `svg_master_logo_derivative_failed` | the PNG cannot be derived within limits | 422 (new) |
| `authored_vnext_master_disabled`, `authored_vnext_owner_required`, pin resolution 404/409 | unchanged | |

The two new codes are raised as `HTTPException` inside `_resolve_master_rules`, so no prefix-filter
change is needed (unlike 1a's code).

#### 3.9.5 Admission-table fixture impact (verified against `scripts/canvas_admission_table.py`)

Computed from the current `tests/fixtures/canvas_admission_baseline.json` (3,654 rows) and the
script's axes (`CANVASES` × `STRATEGIES` × `PINS` × `SOURCES` × `STYLES`, master rows carry only
`mode=auto`, `no-such-style` resolves to `default`):

- **1a**: 290 rows change code, 0 admit↔reject flips (§2.2).
- **D8**: 0 rows change (route ids are not in the fixture; outcomes are identical).
- **D4** (`request_canvas_contract`): 0 rows change (the fixture's `MASTER_PIN` is never resolved,
  so no row exercises the contract-canvas check).
- **D1**: **24 rows flip from `422 authored_portrait_combination_not_supported` to `admitted`**,
  and no row flips the other way:
  - 9:16, source `topic`, pins `master`, strategy ∈ {svg, omitted}, styles `default` and
    `no-such-style`, callers sync/async/v2: 2 × 2 × 3 = 12;
  - 9:13, same, styles `default`, `kbs-company-proposal`, `no-such-style`, callers sync/async
    (v2 rejects 9:13 at `literal_error`): 3 × 2 × 2 = 12.
  - Not flipped: restyle/raster + master on presets (source_mode set), 1:1/21:9 (stage), the
    styles that do not support the ratio, and every authored row.
- The regenerated fixture is reviewed against these counts; a different count is a finding.

### 3.10 Goldens first (own commit, on unchanged code)

All goldens are captured on the current tree before any 1b code, in
`tests/fixtures/svg_master_projection/`, by a fixture-writing test (`--write-goldens` flag pattern
of `canvas_admission_table.py`) so the bytes come from the real functions:

| # | File(s) | Content | Assertion after B |
|---|---|---|---|
| G1 | `design_16x9_nomaster_default.txt`, `design_16x9_nomaster_cobalt-editorial.txt` | `json.dumps({k: v for k, v in adapt_svg_style(style, width=1280, height=720, request=_NullRequest()).items() if k not in {"references", "diagnostics"}}, ensure_ascii=False)`, exactly as `author_slide_svg.py:112` builds it (`digest` included; it is stable while the YAML and reference images are) | **byte-identical** |
| G1r | reuse `prompt_16x9_baseline/svg_rules.txt` | `svg_author_rules(snapshot_without_master, floor)` | byte-identical (existing test) |
| G2 | `design_16x9_master_{fixture,seedlike}_{default,cobalt-editorial}.txt` | same dump with `master_rules=project_master_contract(contract, 1280, 720)`, `master_pin=` the fixture pin; `contract` = the test fixture master and a seed-like master built from `catalog_seed._base_contract` with the logo unset | equal to the golden **after** deleting `master_template.reserved` and the `svg-master-projection/v2` version strings; the rules equal `svg_rules.txt` + the §3.4.5 sentence |
| G3 | `identity_{fixture,seedlike}.json` | canonical JSON of `project_master_contract(contract, 1280, 720)` | equal after deleting `reserved`/version keys |
| G4 (T2) | `projection_<master>_<canvas>.json`, 6 masters × 7 canvases = 42 | mode, reason, `s`/`s_b`, band, and the furniture plan with anchors, clamps and reserved boxes (§3.2, §3.4.2) | exact (these are new; written by the S2 tests, reviewed by hand against the §3.4.2 table) |

Byte identity is asserted as `assert produced == fixture_bytes.decode("utf-8").replace("\r\n", "\n")`
(the CRLF guard `test_author_canvas_shape.py:97-99` uses). G1 and G1r are the guarantee the user
asked for; G2/G3 bound the knowing change to two named additions.

### 3.11 Quality measurement: a master axis for the canvas bench

The pooled bench (`scripts/canvas_bench.py`) has no master axis. Minimal support:

1. **Request.** `--master-pin-file <json>` (the `{"masterId","revision","digest"}` object the seed
   script prints; `scripts/seed_authored_vnext_master_catalog.py` gets a `--print-pin` flag) adds
   `masterPin` to `_request_body`. `--pair` runs each planned deck twice, master and no-master,
   back-to-back in seeded order (the matrix planner gets an `arm` field, as the reference
   experiment specifies for its arms).
2. **Receipt** (`canvas-bench-receipt/v2`, additive fields, no schema bump; O8): `masterPin`,
   `masterProjection` = `theme.svg.styleSnapshot.master_template.canvas_fit` plus
   `master_furniture.{stamped, dropped}` read from the presentation the client already fetches,
   and per-page `furniture` from `svgReceipt`.
3. **Paired check**, new subcommand `master-pair --runs <dir> --out <dir>`: cells are
   (canvas, style, manuscript, repeat); each has one deck per arm. Per cell it computes deltas of:
   - M1 page flag rate (existing gate evidence) and retries/seconds per slide (guards);
   - M2/M3 composition metrics from
     `docs/svg-style-reference-canvas-experiment-design-20260929.md` §4 (`U`, `LER`, edge void,
     bbox aspect gap, letterbox flag, column count), computed from the stored SVGs by a shared
     module `scripts/svg_composition_metrics.py` (the experiment's `svg_reference_metrics.py`
     and this check use one implementation; whichever lands first creates it). System elements
     (`svg-system-*`) are removed before the `full`/`text` masks are built so furniture is not
     counted as content;
   - **M6 furniture collision** (new): render the stored SVG through `render_svg_with_metrics`,
     take every non-system element bbox, and intersect with the deck's `reserved` boxes (from the
     snapshot) and with the actual system bboxes; a page collides when any intersection exceeds
     2 px on both axes. Report the page collision rate, the mean overlapped area, and which role
     collides most. This is the offline replica of §3.7, run on published pages (post-retry), so
     it measures what users see.
   - Decision: master arm is **safe** when the one-sided 95% upper bound of the flag-rate
     difference is ≤ +0.10 and no guard is worse than the bench's secondary margins; **furniture
     honoured** when the page collision rate ≤ 0.05; the paired sensitivity check is the sign-flip
     permutation the experiment design specifies. A collision rate above 0.05 with a retry rate not
     above the no-master arm means the reserved instruction is ignored and §3.7 is not firing;
     that is a defect to fix, not a number to accept.
4. **Run size**: the S option of the experiment design (1:1, 21:9, A4-L; `default`,
   `strategic-navy`; 4 manuscripts; 24 cells × 2 arms = 48 decks, ≈ 5 h serial) on an isolated
   backend with the seeded master (logo present). 1:1 and 21:9 need the bench's stage-disabled
   bypass for `default`, or run 9:16 in their place until a stage opens (O10).

### 3.12 Rollout and migration

- **No flag.** Projection and stamping are deterministic; rollback is a revert.
- **In-flight jobs keep their frozen v1 snapshot.** `pin_svg_style` never reselects on takeover;
  a v1 snapshot has no `master_furniture`, so `finalize_svg` stamps only the page number for it.
- Stored decks are unaffected: nothing re-reads `master_template` after authoring, and export
  reads the stored SVG, which for old decks has no system furniture.
- No DB change; the snapshot column is JSON.
- Order: goldens → 1a → projection → channel → admission → bench → live check (§4).

---

## 4. Implementation stages

| Stage | Files | LOC (code / tests) | Tests | Acceptance |
|---|---|---|---|---|
| **S0 goldens** | `tests/fixtures/svg_master_projection/*` (G1–G3), `tests/unit/test_master_projection_golden.py` | 0 / +130 + 8 fixture files | G1, G1r, G2, G3 as capture tests | Committed alone on unchanged code; `uv run pytest tests/unit/test_master_projection_golden.py tests/unit/test_author_canvas_shape.py` green |
| **S1 1a removal** | §2.2 list (6 deletes, 13 edits) | −1,000 / +150 | §2.4 | 290-row fixture diff, 0 flips; byte-parity gate green; §2.3 scan reported |
| **S2 projection modes** | `master_projection.py` (rewrite, ≈+230), `svg_generation_admission.py` (+45: `px_per_inch`, receipt, log), `style/context.py` (+4), `style/build.py` (+6), `drivers/svg/project_style.py` (+8), `utils/svg_style_adapter.py` (+6) | +300 / +420 + 42 G4 fixtures | T2 (42 goldens), T3 no-stretch invariant, T4 mode invariants, T5 boundaries, T6 anchors (both masters, canvas and safe-area clamp, same-row separation), T7 receipt/exclusion/digest/log, T8 `px_per_inch`, T9 existing `test_master_projection.py` (the 2560×1440 case is uniform `s = 2`), G1–G3 modulo the documented additions | All green; `master_template.reserved` present in every mode; no-master bytes unchanged |
| **S3 furniture channel** | new `drivers/svg/master_furniture.py` (+220: anchors, clamp, text fit, logo derivative, element builders), `svg_contract.py` (+45: strip `svg-system-*`, stamp group, reserved-id rule, system href rule, return stamped ids), `native_declarations.py` (+6), `svg_renderer.py` (+18: system-qid exemption, `furniture_collision`), `svg_export_assets.py` (+6), `svg_generation_assets.py` (+4), `author_slide_svg.py` (+12: exclusion set, §3.4.5 clause), `svg_generation_admission.py` (+40: asset store, derivative, two 422s), `legacy_bridge.py` (+12: helper extraction), `render.py` (+6: per-page receipt) | +370 / +460 | stamping per visibility and index; page number position with/without master; text fit and skip; logo derivative limits and alpha; system ids stripped from author output; `validate_svg` accepts a system data-URL image under a manifest and rejects author `svg-system-*` ids; `native_binding_digest` stable across stamping (declared table stays bound); renderer exemption and collision failure (fixture SVGs); export placement skips the system image (`test_svg_export_assets*`); remake stamps identically from the snapshot; a grep gate `rg -n '"image"|}image' services/svg_export*.py services/svg_generation_assets.py services/svg_export_page_evidence.py` reviewed line by line | A master deck's stored SVG carries `svg-system-furniture`; PPTX export readback shows one picture per logo page and editable header/footer text; PDF shows the logo; 16:9 no-master unchanged |
| **S4 admission** | `authored_canvas.py` (+22), `document_canvas_capabilities.py` (+4 reorder), `strategy.py` docstring (+3), `tests/fixtures/canvas_admission_baseline.json` (24 flips), `docs/canvas-expansion-admission-spec-20260929.md` rows | +30 / +140 | `test_canvas_stage_gate.py`, `test_document_canvas.py` (route swap), new `test_svg_master_canvas_admission.py` (9:16/9:13 admit for default/kbs, 1:1/21:9 stage-gated, authored + master still rejected, restyle + master rejected, non-1280×720 master admitted on 16:9 svg), `scripts/canvas_admission_table.py --check` after `--write` | Exactly 24 flips, all listed in §3.9.5 |
| **S5 bench master axis** | `canvas_bench.py` (+45), `canvas_bench_matrix.py` (+25 arm), `canvas_bench_live.py` (+20 receipt fields), `seed_authored_vnext_master_catalog.py` (+10 `--print-pin`), new `scripts/svg_composition_metrics.py` (+260), new `scripts/canvas_bench_master_pair.py` (+180) | +540 / +200 | `test_canvas_bench.py` additions: request body carries the pin, paired plan is balanced, collision metric on fixture SVGs, pair decision rule known values | `master-pair` output on the S run (§3.11.4) with the safe/honoured verdicts |
| **S6 live check + docs** | this document (status), `CLAUDE.md` one line | – | – | §4.1 |

Note: `scripts/canvas_bench_live.py` and `tests/unit/test_canvas_bench.py` carry uncommitted
edits by another session (`_settle`, `SETTLE_SECONDS`); S5 rebases on whatever lands.

### 4.1 Live check

Isolated backend (not the bench backend :8091/:22600, not the shared stack), seeded catalogue
master with the logo published. Requests through the API (`strategy: svg`, `masterPin`):

1. 16:9 → `master_template.canvas_fit` absent (identity), `reserved` present; the stored SVG has
   header, footer, logo and the page number at the master's positions; the prompt log equals G2
   plus the two documented additions.
2. A4 landscape → `canvas_fit.mode == "brand_only"`, no `layouts` in the prompt, furniture clamped
   to the safe area per §3.4.2; export PPTX → one `<p:pic>` per page with `visibility: always`,
   text boxes for header/footer; PDF renders the logo.
3. 9:16 → admitted (was rejected), brand-only, furniture per the §3.4.2 row.
4. One deck with a declared native table on a master canvas → `nativeObjects.accepted ≥ 1`
   (binding digest unaffected by stamping).
5. Retry/flag counts per deck recorded; any `furniture_collision` residual inspected by eye.

---

## 5. Risks

1. **Furniture near the edge on the seeded master.** Its furniture sits 30–45 px from the edge,
   inside every registry safe margin; the safe-area clamp moves it on every non-identity canvas.
   Correct per the rule, but a designer may want the master's own margins honoured (O4).
2. **Retry cost.** Furniture text now participates in `text_overlap`, and `furniture_collision` is
   a new failure kind; both can raise retries on master decks. Measured by S5 before any wider
   exposure; the guard margins are the bench's own.
3. **Snapshot growth.** Up to ~350 KB of base64 logo per deck in `svg_style_snapshot` and
   `theme.svg.styleSnapshot`, returned by `GET /presentation/{id}`. Same order as one reference
   image the snapshot already carries.
4. **Seams that enumerate `<image>`.** Four are identified (§3.6); the grep gate in S3 is the
   defence against a fifth. A missed seam fails export loudly (`svg_export_slot_not_offered`),
   never silently.
5. **Prompt change on 16:9 + master.** Bounded to `reserved` plus one sentence, verified by G2;
   still a change to a path with no earlier golden, hence S0 first.
6. **The `s_b` density assumption** (masters carry no physical size) and the **5% tolerance** are
   argued, not measured (rev 1 risks 2–3, unchanged).
7. **1a error-code change** for API clients that matched the old codes on pack requests. No
   first-party client can send those.
8. **Shared worktree.** `authored_canvas.py`, `document_canvas_capabilities.py`, the admission
   baseline and the bench files are also touched by the canvas-expansion work. Coordinate; commit
   with a private index.
9. **Value is API-only until the UI exposes svg + master.** The picker stays authored-only
   (§0.1); opening it is a separate UI step (O7).

## 6. Open decisions for the user

Closed by this revision: D1 (open presets for svg + master: yes), D2 (furniture: system-stamped in
every mode), D4 (non-1280×720 masters on svg 16:9: yes), D6 (`design_pack_canvas_unsupported`),
D8 (fix the A4 route ordering now). Kept from rev 1 with the same recommendation: D3 (5%), D5
(delete manifest v2), D7 (leave `variants/` inert), D9 (physical `s_b`).

| # | Decision | Recommendation |
|---|---|---|
| O1 | Stamp furniture on 16:9 identity decks (changes today's svg + master output on 16:9) or only off-16:9 | Stamp everywhere; otherwise the same master looks different by canvas |
| O2 | Divider in brand-only: dropped (user's direction) or stamped as a span line through the channel | Dropped; revisit after the S run if masters read as "unframed" |
| O3 | "Cover" = page index 0, or the svg driver's role | Index 0 (simple, matches `finalize_svg`'s inputs) |
| O4 | Clamp furniture to the registry safe area (moves the seeded master's 30 px header to 64 px) or only to the canvas | Safe area, so furniture never fights the author's own margin rule; record `clamped` |
| O5 | `furniture_collision` as a retrying failure kind, or measurement-only in the bench | Failure kind; the bench measures its cost |
| O6 | Logo derivative cap 256 KiB / 2× box edge | As specified |
| O7 | Expose svg + master in the upload picker (UI step, out of scope here) | Separate design after the S run |
| O8 | Bench receipt: additive fields on v2 or bump to v3 | Additive; `compare` ignores unknown keys |
| O9 | Furniture text font: snapshot `tokens.body_font` (respects a request override) or the master's `font_manifest.primary` raw | Snapshot font, same as the page number today |
| O10 | S run canvases while 1:1/21:9 stages are disabled: bench-only stage bypass, or substitute 9:16 | Substitute 9:16 (no product-path bypass) |

## 7. Self-score (0–10; for risk and complexity, higher means riskier or more complex)

| Axis | Score | Why |
|---|---|---|
| Quality | 7 | The distortion defect and the empty brand channel are both closed at single points, with goldens, receipts and a measured collision check; furniture is native on export without new export code. Capped because the composition metrics and the collision threshold are unvalidated, and because the seeded master's edge furniture will be moved by the clamp on every non-16:9 canvas. |
| Risk | 5 | Up from 3: the channel touches the svg contract, the renderer, four asset seams and the native-binding digest; each is small, but a missed seam breaks export for master decks. Admission opens 24 fixture rows. Everything is deterministic and revertable; nothing is flagged. |
| Complexity | 6 | 1a: about −1,100 / +150. 1b: about +1,240 code, +1,220 tests, 50 golden files, two new scripts. No DB, no flags, no UI. |

Top problems:
1. The collision check (§3.7) is new and its 2 px threshold is judgement; a false positive costs a
   retry, a false negative ships an overlap. S5's paired run is the only calibration planned.
2. The seeded master's furniture lies inside every registry safe margin, so the safe-area clamp
   (O4) rewrites its positions on every non-identity canvas; if the user wants the master's own
   margins, the clamp must become canvas-only and the author's margin sentence must yield.
3. The bench's composition metrics (`svg_composition_metrics.py`) do not exist yet and are shared
   with another design; the master axis depends on that module landing.
4. `s_b` and the 5% tolerance remain argued, not measured; A4 is the only canvas where `s_b ≠ 1`.
5. Reach stays API-only until O7; nothing in the UI can send svg + master.
