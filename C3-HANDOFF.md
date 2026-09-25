# C3 assets — handoff (2026-09-26)

Branch `core/c3-20260926` from `integrate/generation-core` 76d2429ab. Local commits only.

| commit | what |
|---|---|
| fe5d8b1c7 | `git mv` of the shared asset producers into `services/generation_core/assets/` (`icon_pack/`, `infographic_assets.py`, `infographic_plan.py`, `illustration_prompt.py`); old paths are `sys.modules` alias facades (the icon-pack facade pre-registers its 8 submodules), so old and new imports are the same module objects. 12 renames, 19+/19- lines (imports + the library `__file__` depth). |
| 276be09ad | `drivers/svg/embed_assets.py` (+ `icon_flatten.py`, `icon_geometry.py`): icon SVG -> contract subset, `replace_vector_icon_images`, `rasterize_icon`, `apply_vector_icon_quality`. |
| a4296c051 | `generation_core/assets/offer.py` (AssetOffer), `drivers/svg/asset_slots.py` (SVG projection, persistence), wiring in `svg_presentation_prepare.py`, `drivers/svg/author.py`, `render.py` (2-line hook agreed with c1), `svg_generation_assets.py`, `author_slide_svg.py`; profile timeout default. |
| 3e98a9d0d | an unusable scene picture drops its slot instead of failing the deck. |

Note: `test_generation_core_asset_offer.py` has 9 tests (count updated after 3e98a9d0d).

## Slot schema

`AssetSlot(page_index, role: scene|figure-icon, kind: source|illustration|icon, alt, path,
media_type: image/png|image/jpeg|image/svg+xml, sha256, width, height, bbox_hint
(x, y, w, h normalized), concept, origin: cutout|illustration|library|cached|generated)`.
`AssetOffer(slots, receipt)`; receipt: status, budget, timeoutSeconds, scenesPlanned,
figurePages, illustrations, icons, providerCalls, timeouts, failures[{page, reason}],
transparentProvider, icon{requested, resolved, sheetsSpent, origins}.

SVG projection (`asset_slots.project_offer`):
- raster (illustration, pack PNG icon, icon over budget): stored under the owner image
  directory `svg-asset-offer/<sha256>.<ext>`, `ImageAsset` id = uuid5(ns, presentation:sha256),
  joined to `planned[page]` as a `SourceImageCandidate` with
  `source_locator.assetOffer=<kind>` -> identical path to source images (manifest,
  screen placement, `sourceImages` offered/used/omitted, export). The manifest gives them
  `selected_supporting` even when instructions demand every source image preserved, and
  `selectionReason=asset_offer_<kind>`; the model sees `kind`.
- vector icon: model-facing entry `{data_url: 128px PNG preview, description, kind: icon,
  vector: FlattenedIcon}` under a `source-slot-*` handle; `author_slide_svg` replaces the
  `<image>` in place with the flattened `<g id="asset-source-slot-...">`; receipt
  `vector_icons_used`; per-page `payload.assetOffer = {slots: {id: {kind, role, origin,
  mode: raster|flattened|rasterized, points}}, vectorIcons: {offered, used}}`; render hook
  adds `quality.assetOffer` and `asset_icon_omitted` failures.
- persistence: page-0 checkpoint key `assetOfferPlan` (app-data-relative paths, traversal
  checked on load); a resume replays it; an attempt sealed before offers existed resumes
  with no offer (`sealed_without_offer`).
- no offer for: source reflow, raster reconstruction, exact reconstruction, instructions
  refusing pictures, non-durable jobs, no image provider / `DISABLE_IMAGE_GENERATION`
  (then no plan call either).

## Budget and fallback

- Flattening: `ICON_POINT_BUDGET = 192` points/icon, tolerance 0.25. Bundled library (300
  icons): min 4, median 43, p90 77, max 151 -> 0 over budget, 0 rasterized. Over budget ->
  PNG 256 px via `rasterize_icon`, recorded as `mode=rasterized` and
  `receipt.iconsRasterized`.
- Provider guard (`runtime/profile.py`): `illustration_budget` (default 4) image calls per
  deck (scene = 1, icon sprite sheet = 1; scenes first, sheets get the rest);
  `illustration_timeout_seconds` per call, default now the authored
  `IMAGE_GENERATION_TIMEOUT_SECONDS` (180) unless `SVG_ILLUSTRATION_TIMEOUT_SECONDS` is set
  (agreed with c4).

## Deviations (approved by ppt-agent-fe)

- `authored_illustration_stage.py` did not move: it rewrites slide HTML and the SVG driver
  does not call it (rule 1).
- Profile timeout default changed from 60 s to the authored image knob.

## Pre-existing failures (L2)

Same 6 fail on 76d2429ab without any C3 change (run once on an extracted base tree):
- `test_authored_icon_pack_service.py::test_resolve_icons_generates_missing_then_serves_from_cache` — `{'generated','library'} != {'generated'}` (library lookup added after the test)
- `...::test_resolve_icons_degrades_on_provider_failure` — `['감지'] != ['감지','예측']` (예측 now resolves from the library)
- `...::test_resolve_icons_rejects_an_opaque_sheet_without_caching` — library resolves 예측, expected none
- `...::test_resolve_icons_drops_a_cell_that_carries_its_concept_name` — `[] != ['예측']`, same cause
- `test_authored_infographic_assets.py::test_the_context_is_withheld_when_the_deck_may_not_spend_a_transparent_png` — `TypeError` in `get_images_directory` (APP_DATA_DIRECTORY unset in the test)
- `test_authored_illustration_plan_wiring.py::test_the_plan_round_trips_through_the_persisted_theme` — `AuthoredStageError: Authored slide render failed`

## Verification

- Focused pytest (illustration/infographic/icon_pack/svg_*/image_generation/gpt_image/
  local_fit_image/generation_core/raster wire): 837 passed, 6 failed (above).
- New tests: `test_svg_embed_assets.py` 21, `test_generation_core_asset_offer.py` 9,
  `test_author_slide_svg_asset_offer.py` 3, runtime profile 3 — all pass.
- Offline: a flattened icon converts through the vendored svg_to_pptx into a native group
  (8 freeform + 5 auto shapes, 0 pictures).
- Touched files ruff-clean and <= 350 lines.

## Live check (V stack 8100 @ 3e98a9d0d, 2026-09-26, concurrent_harness=true)

Image provider `codex_image` (no alpha output → no cutouts, no icon sheets; library icons
only), style `hyundai-culture-center` (SVG-mapped, has an illustration prompt), 10 pages,
`export_as=pptx`.

- Run A `811ad3a9-5dd7-4304-b02e-9acbbbcee09a` (content that invites a scene): plan
  figures=9 scenes=1. Offer: 1 illustration (1 provider call, 0 timeouts, 0 failures) +
  7 library icons (44 concepts requested, 7 resolved). SVG: page 3 `<image>` of the
  illustration slot (sourceImages offered 1 / used 1 / omitted 0); pages 5 and 8 carry 4
  and 3 flattened `<g id="asset-source-slot-…">` groups (vectorIcons offered 7 / used 7 /
  omitted 0; 7 flattened, 0 rasterized). Contract validation passed (pages rendered and
  published). Chained PPTX export succeeded (1.2 MB): slide 3 has 1 picture, slides 5/8
  have 4/3 native groups of freeforms, 0 pictures for icons.
- Run B `c3869d38-e3d4-45ef-9add-f611fd4da7da` (generic content): plan figures=9 scenes=0,
  icons 0/45 by exact library alias → offer resolved with no slots, deck otherwise
  normal; published review_required, so no export was chained; a manual `export.pptx`
  command succeeded.
- Every page of both decks carries `font_unresolved` (and some `text_overlap`) quality
  flags, including pages with no offer slot and deck B with no offer at all — not
  introduced by C3; not investigated here.

## Still open

- gpt-image-2 transparent cutout and icon-sheet generation are covered by unit tests only;
  V has no OpenAI credential. **Needs one verification on the shared stack (5000) after
  merge.**
- Without an alpha-capable provider, icons come only from the bundled library, which
  matches plan node labels by exact normalized alias: 0/45 on a generic deck, 7/44 when
  the content uses library words. Improving recall (token/substring matching) would be a
  change to the moved `icon_pack/library.py`, which authored also calls — a product
  decision, not done here.
- Illustrations follow the plan's `scene` pages only (same rule as authored); a deck
  whose plan has no scene gets no illustration.

