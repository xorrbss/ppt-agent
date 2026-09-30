# Canvas expansion — admission spec (rev 2, 2026-09-29)

**Status 2026-09-29 (design packs are 16:9-only).** Every `pack` row on a non-16:9 canvas below (9:16, 9:13, 1:1, 21:9, A4-L, A4-P) now returns `design_pack_canvas_unsupported` (422), decided before the A4 branch and the stage gate; the old codes in those rows (`authored_canvas_stage_disabled`, `authored_portrait_combination_not_supported`, `document_canvas_generation_combination_not_supported`) no longer apply to pack pins. The fixture `tests/fixtures/canvas_admission_baseline.json` changed 290 of 3,654 rows, all code-only, with 0 admitted/rejected flips. "A4 + design pack" under section 6 is no longer a pending workstream: it is out of scope by decision (see `docs/svg-master-no-stretch-and-variant-removal-design-20260929.md` section 2).


Workstream spec under `docs/aspect-ratio-expansion-design-20260928.md`. Scope: which (canvas × style × strategy × pins × source mode) combinations the generation endpoints admit today, and the rule for the new canvases. The baseline table was produced by **executing** the real admission code; an independent reviewer reproduced all 360 rows (0 mismatches). Rev 2 folds in that review (64/100): request-boundary Literals the rule missed, axes the enumeration missed, caller differences, and the fact that svg source restyle ignores the requested aspect ratio.

## 1. How the baseline was produced

`SimpleNamespace` requests over the product of

- canvas: `16:9`, `9:16`, `9:13`, `A4-L` (`page_size="A4"`, `orientation="landscape"`), `A4-P`
- style: `default`, `kbs-company-proposal`, `osb-smart-banking`, `hyundai-culture-center`
- strategy: omitted, `svg`, `authored`
- pins: `free` (`generation_mode="auto"`), `pack` (`generation_mode="registered-template"` + `design_pack_pin`), `master` (`master_pin`)
- source: `topic` (`source_mode=None`), `restyle` (`source_content_restyle`, verbatim policy, pptx export)

run in the sync endpoint's order (`api/v1/ppt/endpoints/presentation_generate.py`): `require_svg_creation_admission` then `validate_authored_canvas_admission(request, style_id=…)` with `style_id` resolved as `:534-538` does. `template="authored"`, `generation_engine=None` (the model default is `"authored"`, `models/generate_presentation_request.py:224`; both give identical results — verified).

Known limits of the enumeration (to be added to the fixture in §4): `source_mode="raster_reconstruction"`; a style with `reference_mode="exact_reconstruction"` (`authored_styles/exact-osb-last.yaml`, env-gated); `generation_engine="deck-ir"` (only reachable through the v2 orchestrator's `effective_generation_engine`); `generation_mode` ∈ {`brand-style`, `fidelity-first`}; `_authored_master_pin` (behaves as `master_pin`). Spot checks by the reviewer: `raster_reconstruction` is admitted on A4+svg (but `select_a4_route` classifies it as a non-source route, `document_canvas_capabilities.py:202`) and rejected on 9:16; an exact-reconstruction style is rejected on svg and on A4 (`document_canvas_fixed_source_not_supported`); `deck-ir` is rejected on 9:16 and A4; `brand-style`/`fidelity-first` are admitted on 16:9 only.

## 2. Baseline: what is admitted today

`OK` = admitted. Columns: default / kbs-company-proposal / osb-smart-banking / hyundai-culture-center. Rows identical for the omitted and `authored` strategies are shown once.

| canvas | strategy | pins | source | default | kbs | osb | hyundai |
|---|---|---|---|---|---|---|---|
| 16:9 | -/authored | free, pack, master | topic, restyle | OK | OK | OK | OK |
| 16:9 | svg | free, master | topic | OK | OK | OK | OK |
| 16:9 | svg | free, master | restyle | OK | `svg_source_template_unsupported` | OK | `svg_source_template_unsupported` |
| 16:9 | svg | pack | any | `svg_design_pack_requires_source_native_patch_target` (all) | | | |
| 9:16 | -/authored/svg | free | topic | OK | `authored_portrait_combination_not_supported` | same | same |
| 9:16 | -/authored | free | restyle | OK | OK | OK | OK |
| 9:16 | svg | free | restyle | OK* | `svg_source_template_unsupported` | OK* | `svg_source_template_unsupported` |
| 9:16 | any | pack, master | any | blocked (`authored_portrait_combination_not_supported`; svg+pack: `svg_design_pack_…`; svg+master+restyle on kbs/hyundai: `svg_source_template_unsupported`) | | | |
| 9:13 | -/authored/svg | free | topic | OK | OK | `authored_portrait_combination_not_supported` | same |
| 9:13 | -/authored | free | restyle | OK | OK | OK | OK |
| 9:13 | svg | free | restyle | OK* | `svg_source_template_unsupported` | OK* | `svg_source_template_unsupported` |
| 9:13 | any | pack, master | any | blocked (as for 9:16) | | | |
| A4-L, A4-P | -/authored | free, master | topic, restyle | OK | OK | OK | OK |
| A4-L, A4-P | -/authored | pack | any | `document_canvas_generation_combination_not_supported` (all) | | | |
| A4-L, A4-P | svg | free, master | topic | OK | OK | OK | OK |
| A4-L, A4-P | svg | free, master | restyle | `document_canvas_generation_combination_not_supported` | `svg_source_template_unsupported` | `document_canvas_…` | `svg_source_template_unsupported` |
| A4-L, A4-P | svg | pack | any | `svg_design_pack_requires_source_native_patch_target` (all) | | | |

`OK*` — admitted, but **not a 9:16/9:13 deck**: for `strategy="svg"` + `source_content_restyle`, `_admit_source_content_generation` (`presentation_generate.py:548`) binds `request._document_canvas` to the source document's own canvas (`services/svg_source_binding.py:45-46`), and `request_canvas_contract` (`services/authored_canvas.py:104-116`) then ignores `aspect_ratio`. The deck takes the source's canvas.

Facts this table settles:

1. **16:9 admits everything** on the authored driver. On svg, design packs are never admitted on any canvas; source restyle needs a style with an svg source template (`default`, `osb-smart-banking`).
2. **Portrait presets have two branches** (`services/authored_canvas.py:288-314`): free-design topic decks consult `supported_aspect_ratios_for_style()`; free-design source restyle is admitted on every style without it (`:290-302`). Pins block both presets everywhere.
3. **A4 admits masters but not design packs**: `design_pack_pin` forces `generation_mode="registered-template"` (`generate_presentation_request.py:319-322`), which `select_a4_route` rejects (`document_canvas_capabilities.py:157-170`).
4. Explicit `strategy="svg"` + A4 + restyle is rejected; the omitted strategy is routed to authored (`generation_core/admission/strategy.py:56-61`).
5. The three callers differ in their inputs, not in the function: sync (`presentation_generate.py:520-527` pre-check on `template`, `:532` normalisation, `:534-538` style resolution), async (`:1748-1764`, no normalisation/pre-check), v2 orchestrator (`services/public_v2_orchestrator.py:804-809`: raw style id, `generation_mode` overridden to free-design, engine may be `deck-ir`; `api/v2/contracts.py:132` Literal has no `9:13`). Any new canvas must be admitted identically through all three, and the fixture (§4) covers all three.
6. UI (`ConfigurationSelects.tsx:232-233`): `sourceContentMode ||` enables 9:16/9:13 regardless of style **and** of `hasCompanyStandard` (looser than branch 2, which still rejects pins); for svg source restyle the select is hidden (`:247-251`).

## 3. Rule for the new canvases (`1:1`, `21:9`)

**Do not refactor admission.** Both preset branches and the A4 route policy stay. Adding a canvas is a checklist, every item verified by the fixture:

1. Widen **every** closed literal that carries the aspect ratio — this is where rev 1 failed: `services/authored_canvas.py:18` (`AuthoredAspectRatio`) and `:23-27` (sizes); `models/generate_presentation_request.py:162`; `api/v1/ppt/endpoints/presentation_crud.py:378` (`/presentation/create`, which the web client hits first); `api/v2/contracts.py:132`; the styles response model (`api/v1/ppt/endpoints/authored.py:52`) — otherwise `/authored/styles` 500s the moment `supported_aspect_ratios_for_style()` returns a new value; frontend `upload/type.ts:132` and the unions listed in the decisions doc §6. A test greps these files for the old 3-way literal and fails if any survives.
2. `GATED_ASPECT_RATIOS = {"1:1", "21:9"}`. In `validate_authored_canvas_admission`, before the source-restyle branch: `if request.aspect_ratio in GATED_ASPECT_RATIOS and canvas_stage(request.aspect_ratio, style_id) == "disabled": raise ValueError("authored_canvas_stage_disabled")`. All three callers map `authored_canvas*` ValueErrors to 422 (`:541`, `:1760`, orchestrator `:809`) — verified.
3. `supported_aspect_ratios_for_style()` additionally returns each gated ratio whose stage for the style is `candidate` or `verified`.
4. UI: the `sourceContentMode ||` shortcut must not apply to gated ratios; for svg source restyle the picker shows the source canvas as a locked row (today's hidden-select behaviour, made explicit) because the request's aspect ratio is ignored there (`OK*` rows).
5. Pins remain blocked for the new canvases (branch 3, unchanged). A4 rows are untouched by this spec.

Result: every existing row of §2 is preserved; `1:1`/`21:9` are admitted exactly where `9:16` is admitted **and** the stage allows it, through all three callers.

## 4. Regression fixture (the table as a test)

- `scripts/canvas_admission_table.py` (checked in) enumerates the §1 axes **plus** the known limits listed there, builds each request with `GeneratePresentationRequest.model_validate(...)` so request-boundary Literals are exercised (a combination the model rejects is recorded as `422 <pydantic error>` — that is a row, not a skip), and runs the three caller paths (sync pre-check + normalisation + resolution; async; v2 orchestrator's input shaping). Output: `servers/fastapi/tests/fixtures/canvas_admission_baseline.json`.
- `tests/unit/test_canvas_admission_baseline.py` asserts the current code reproduces the fixture. The test pins its environment: the packaged style catalogue only (`resolve_authored_style` falls back to `default` for unknown ids, `utils/authored_styles.py:657-664` — unknown ids are enumerated deliberately), `AUTHORED_EXACT_*` unset, `SUPPORTED_TEMPLATE_IDS` as shipped.
- Adding the new canvases adds rows under a separate key; changing an existing row is a test failure.

## 5. Stages

Runtime file `servers/fastapi/constants/canvas_stages.json` (shipped like `constants/settings_registry.json`; the repo-root `contracts/` tree is **not** copied into the Docker image, so it cannot host runtime data): `{"1:1": {"<style_id>": "disabled|candidate|verified"}, "21:9": {...}}`, keyed by canonical style id (the v2 orchestrator resolves raw ids first). Initial content: every style `disabled`. Written by the bench tool's `promote` command; reviewed like any constant change. Existing flags (`PORTRAIT_RELEASE_VERIFIED`, `DOCUMENT_CANVAS_A4_*`, `A4_ROUTE_POLICIES`) are not touched.

## 6. Out of scope

- Relaxing pins for non-16:9 presets; A4 + design pack (pack-variant workstream).
- Persisted canvas form (unchanged: A4 → `theme.documentCanvas`; presets → `theme.authored_vnext.canvas`).
- The v2 contract's missing `9:13` (pre-existing; listed so it is not mistaken for a regression).
