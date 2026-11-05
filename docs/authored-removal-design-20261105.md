# authored generation strategy removal: design (2026-11-05)

Status: **design only. No code was changed.** Base: `696be2ac` on `main`.
Implementation is handed to a separate agent (Codex). Each phase in §5 is a self-contained unit with
its own acceptance criteria; do them in order and do not merge phases into one change.

Markers: **[V]** verified in code or by count at `696be2ac`; **[I]** inference that the implementer
must confirm before relying on it.

Relation to other documents:
- `docs/svg-mode-improvement-plan-20261105.md` and
  `docs/svg-mode-quality-performance-review-20261105.md` (landed on `main` after this design's base)
  depend on authored-named modules that §4.2 keeps: `models/authored_vnext/capability_registry.py`
  (`AUTHORED_FONT_FLOOR_PX`), `authored_styles/*.yaml`, `models/authored_infographic_plan.py`. If
  that plan and this removal run in parallel, P3 must not delete or move those modules.
- Decision ids D1–D5 in this document are local to it and unrelated to the D1–D5 of the svg mode
  improvement plan.

---

## 1. Decision and why

Remove the `authored` generation strategy (LLM-written HTML/CSS per slide, `slides.html_content`).
`svg` (`svg-v1`, `slides.svg_content`) becomes the only generation path.

Why:

- **Structural.** Authored HTML reflows: one overflowing block pushes or clips its siblings, and the
  pipeline can only measure and repair afterwards (fit envelope, local-fit shrink, LLM repair loop,
  terminal fallbacks, SlideSpec). svg is fixed-coordinate, so a defect stays local, and the same
  geometry goes 1:1 into the PPTX. This is the owner's observed "breaks and overflows" problem.
- **Export.** Authored PPTX by default (`pptx_mode` unset → `fidelity`) is one full-slide PNG per
  page with nothing editable [V `services/workspace_quality_export_operation_handlers.py:126-128`].
  The opt-in hybrid mode was measured at 2,206 native of 2,916 targets, with Korean text rasterized
  when fonts are not embedded [V `docs/authored-editable-export-recovery-implementation-20260914.md`].
  Authored PDF is raster (0/32 selectable text) while svg PDF is vector (32/32)
  [V `docs/generation-core-architecture-20260925.md` §6a].
- **Already frozen.** svg became the default on 2026-09-26 and authored was declared "frozen: stays
  selectable, gets no new features" (`docs/generation-core-architecture-20260925.md`). Authored-only
  commits fell from 408 (Sep) to 19 (Oct) and 10 (Nov 1–5) [V git history].
- **No svg user loses anything.** Every capability authored has and svg lacks (§2) is already absent
  for every deck made with the default strategy since 2026-09-26.

Honest caveat for the record: there is no matched authored-vs-svg layout A/B after 2026-09-16 (the
only one, 24 responses, found no difference). The decision rests on the structural argument, the
export results and the frozen status, not on a paired overflow measurement.

### 1.1 Spec-memory

`.spec-memory/decisions/authored.generation.dual-engine-comparison.1.json` (authority: user) forbids
"Removing authored before comparison", and `generation.svg-mode-shared-authored-styles.*` still say
authored stays the default. Phase 0 records a superseding user decision (§5, P0) **before** any code
is removed.

---

## 2. What is lost, and what is not

### 2.1 Lost (accepted by this decision)

| Capability | authored | svg today | Follow-up |
|---|---|---|---|
| Element editor: text/style/geometry/image replace/crop/shape, undo/redo (`AuthoredElementEditor.tsx`, `endpoints/authored_operations.py`) | yes | none (`editor_adapter_id="svg-readonly"`) | svg roadmap, §7 |
| Whole-deck restyle (`AuthoredTemplateChanger`, `services/authored_retemplate_request.py`, `workspace_authored_template_operation_handler.py`) | yes | common-header design only | svg roadmap, §7 (first) |
| Workspace chat on a deck (header/theme/Q&A tools) | partial | refused: `legacy_presentation_frozen` [V `services/compatibility_migration/runtime.py:50-51`] | svg roadmap, §7 |
| Sync `POST /presentation/generate` | yes (local SQLite only) | 422 `svg_generation_requires_async_route` [V `endpoints/presentation_generate.py:83-84`] | retire, §5 P1 |
| Source restyle with `text="outline"` or `allow_summarization` | yes | rejected `svg_source_verbatim_required` | becomes 422, §3 |
| Quality review auto-fix (SlideSpec repair) | yes | analyze only | dropped (78% false-positive flags) |
| deck-ir engine (public v2 `generationMode`) | via authored | n/a | removed with authored |
| Arbitrary CSS font families | browser-rendered | only trusted, embedded fonts | out of scope |
| Exact-reconstruction presets (`slide_reference_images`) | yes (off by default) | not read | dropped |

### 2.2 Not lost

LLM providers (each lists both drivers or neither in `constants/provider_registry.json`), canvases
(svg is a superset: 4:3, custom mm, A4), raster reconstruction, user PPTX templates, design
references, tables/charts in restyle, all outline-stage request fields (tone, verbosity, language,
web search, TOC, slide count), primary colour/wordmark/master pin, page remake, PPTX/PDF export and
speaker notes.

---

## 3. Decisions taken in this design

The owner delegated these to the design's recommendation. Each can be overridden; the phase that
depends on it is named.

| # | Question | Decision | Phase |
|---|---|---|---|
| D1 | Port authored editing to svg before removal? | **No.** Removal and svg editing are independent tracks; svg editing is a separate roadmap (§7). | — |
| D2 | Existing authored decks | **Measurement-gated** (P0): if any authored deck owned by an external tenant was opened or exported in the last 90 days → **option C** (snapshot to PPTX+PDF, then hard remove). Otherwise → **option A** (announce, grace period, hard remove like template_v2). Option B (keep read-only view/export) is **rejected**: it keeps the hybrid export, the most fragile part. | P2 |
| D3 | Sync `/generate`, CLI `--strategy authored`, public-v2 deck-ir | **Retire all three.** If P0 finds an external caller of sync `/generate`, replace retirement with an enqueue-and-wait shim on the async route instead. | P1 |
| D4 | Requests that silently fall back to authored today | Omitted strategy **always** resolves to svg; an svg admission violation becomes a 422 with the violation's own code. No silent substitution (e.g. no default style for an unknown style id). | P1 |
| D5 | Rename shared infrastructure that carries "authored" names | **Not in this change.** Remove first, rename in a later, separate change. Persisted literals are **never** renamed (§4.3). | — |

---

## 4. Ground rules for the implementer

### 4.1 Delete by driver, not by file name

"authored" in a name does **not** mean authored-only. From the svg entry points
(`services.svg_presentation_service`, `services.svg_generation_admission`,
`services.svg_export_operation`) the module-level import closure reaches **136 authored/slidespec-
named modules** [V, appendix A]. Including function-local imports, the closure is ~1,140 modules,
i.e. effectively the whole backend [V]. There is no clean cut today; P3 creates one.

### 4.2 Keep set: shared infrastructure svg depends on (do not delete)

| Group | Modules / paths | Evidence [V] |
|---|---|---|
| Canvas contract | `services/authored_canvas.py`; NX `lib/authored-canvas.ts` (`AUTHORED_SLIDE_WIDTH_PX`) | `svg_presentation_service.py:22`, `svg_export_operation.py:16`, `svg_renderer.py:15`, `svg_generation_admission.py:208`, `workers/public_svg_pptx_convert.py:11`, `workers/public_render_worker.py:8`; NX `SvgSlidePreview.tsx:8`, `svg-template-canvas.ts:4` |
| Checkpoints | `authored_checkpoint_{fence,errors,artifacts,service,state,stage_batch,render_artifacts,publish_fence,sweep_budget}`, `authored_deck_plan_checkpoint`, `models/sql/authored_slide_checkpoint.py` (table `authored_slide_checkpoints`) | `svg_presentation_service.py:23`, `svg_presentation_checkpoint.py:19-22,587`, `drivers/svg/publish.py:23`; `AuthoredStageError` in `drivers/svg/{author.py:25,collect.py:30,render.py:18,terminal.py:42}`; `svg_checkpoint_recovery.py:6`, `svg_partial_recovery.py:24`, `svg_timeout_repurchase.py:18` |
| Slide PNG save | `services/authored_presentation_artifacts._save_slide_pngs` | `drivers/svg/publish.py:24`, `svg_document_mutations.py:8`, `svg_page_remake.py:15`, `svg_raster_renumber.py:97` |
| Job concurrency | `authored_presentation_service._AUTHORED_JOB_SEMAPHORE` (`AUTHORED_JOB_CONCURRENCY`) | `svg_presentation_service.py:48` — move it out before deleting the authored service |
| Style catalogue | `utils/authored_styles*`, `authored_style_catalogue`, `authored_style_exemplars`, `authored_style_samples`, `authored_style_svg_recovery`; data dirs (authored_styles YAML, authored_style_assets, authored_style_samples — 1,155 files); `endpoints/authored.py` style/canvas-capability/sample routes; NX `AuthoredStylePicker` (the only style picker) | `svg_generation_admission.py:101`, `svg_templates/catalog.py:16`, `drivers/svg/svg_style_adapter.py:114-118`; samples are generated with `STRATEGY = "svg"` (`servers/fastapi/scripts/authored_style_samples_runner.py:21`) |
| svg authoring | `generation_core/authoring/author_slide_svg.py` imports `author_slide` (:39); `author_slide.EXACT_RECONSTRUCTION` used by `assets/offer.py:295`, `infographic_assets.py:33`; models `authored_header_contract`, `authored_header_metrics`, `authored_illustration_plan`, `authored_infographic_plan`, `authored_chapter_plan`, `authored_visual_plan` | `drivers/svg/author.py:168`, `svg_common_header.py:13-14` |
| Renderer | `utils/slide_capture`, `utils/authored_renderer_session`, `renderer_worker_policy` (`AUTHORED_RENDERER_*`), `contracts/authored-renderer` (Chromium sandbox, built in `Dockerfile:65-88`) | `svg_renderer.py:454-570`, `workers/public_svg_pdf_export.py:76` |
| authored_vnext pieces | master pins (`models/authored_vnext/master/*`, gated by `AUTHORED_VNEXT_MASTER_ENABLED`), `NativeFontByteRegistry`, `capability_registry`, `deck.scene`, `semantic.typography`, `generation.legacy_pin`/`errors`, `deck_ir.strategy`, `utils/env_settings/authored_vnext` | `svg_generation_admission.py:6,144-145`, `generation_core/style/master_projection.py:26,78`, `svg_trusted_fonts.py:15`, `svg_templates/frame_fonts.py:15`, `svg_export_quality.py:13`, `presentation_export_capabilities.py:10` |
| Dispatch helpers | `authored_page_remake_service` (svg remake dispatches through it, :80), `workspace_authored_async_admission` (`svg_quality_review_service.py:33`), `authored_batch_policy` (`drivers/svg/quality.py:831`), `authored_generation_timing_receipt` (`drivers/svg/timing.py:41`), composition policy (`workspace_service.py:65`) | as listed |
| Export worker entry | `workers/public_export_worker.py` dispatches svg bundles first (:256-268); `public_render_worker._admit_html` used by svg PDF | as listed |
| Shared request model | `models/generate_presentation_request.py` is used by both strategies and carries many `_authored_*` private attrs | — |
| Infra with authored labels only | `provider_durable_admission`, `provider_usage_instrumentation`, `public_worker_profiles`, `constants/timeouts.py`, `document_canvas_generation`, `presentation_export_capabilities`, `presentation_export_powerpoint_qa` | — |

For mixed modules (e.g. `authored_page_remake_service`, `public_export_worker`,
`workspace_quality_export_operation_handlers`, `constants/user_messages.py`), delete only the
authored branch and keep the svg branch.

### 4.3 Persisted and wire literals: never rename

Renaming any of these breaks stored data, sealed receipts or deployment config:

- table `authored_slide_checkpoints`; column `slides.html_content` and the `slides.ui` XOR
  `html_content` check constraints (while any authored row exists);
- provider-usage `operation="authored_generation"` (emitted by svg:
  `drivers/svg/author.py:426`, `quality.py:130`, `svg_common_header_generation.py:289`,
  `svg_presentation_checkpoint.py:373,390`, `provider_durable_admission.py:100`);
- `style_source="authored"` for svg-v1 decks (`models/presentation_profiles.py:155`);
- wire field `authored_style` (svg's style id, `svg_generation_admission.py:93`);
- `GenerationRouteReceiptV1.selected_route` value `"authored-fallback"` and `engine` literal
  (`models/public_v2_contracts.py`) — old sealed receipts must keep validating;
- `"authored"` in `PresentationDriverId`, `_KNOWN_MODES`, `_KNOWN_THEME_MODES`
  (`models/presentation_profiles.py:15-28`) — needed so an authored row fails with
  `unsupported_presentation_driver` and not an earlier, different error [I];
- env var names `AUTHORED_RENDERER_*`, `AUTHORED_RENDER_*`, `AUTHORED_JOB_CONCURRENCY`,
  `AUTHORED_CLUSTER_JOB_CONCURRENCY`, `AUTHORED_CHECKPOINT_*`, `AUTHORED_VNEXT_MASTER_ENABLED`,
  `AUTHORED_LOCAL_STAGE_ASYNC_ENABLED` (svg reads them);
- Bitbucket required check name `Authored release readiness configuration`
  (`config/authored-release-required-checks.json`, verified live with `--live`) — renaming it
  requires changing the Bitbucket branch restriction in the same step; leave it.

### 4.4 Testing

- Bitbucket CI runs **no pytest** (only `ruff check .`), so every phase must be verified locally.
- Most coupling is function-local imports, so a deletion can pass import checks and fail at runtime
  on rare paths: checkpoint recovery, timeout repurchase, page remake, partial recovery. Each phase
  ends with the svg-focused subset first, then the full suite (~17 min) before merging.
- After each deletion batch: `uv run python -c "import server"` from `servers/fastapi`, then rerun
  the import-closure script (appendix A) and diff the authored-named set against the previous run.

---

## 5. Phases

### P0 — Measure and record the decision (no runtime change)

1. Run against production (read-only):
   - number of presentations whose resolved driver is `authored`, by owner/tenant;
   - of those, how many were opened or exported in the last 90 days;
   - authored generations in the last 30 days, split by `_generation_strategy_reason`
     (`explicit` vs each fallback violation code);
   - callers of sync `POST /api/v1/ppt/presentation/generate` in the last 30 days (access logs).
2. Apply D2 and D3 to the results and write them into this document as an addendum.
3. Add `.spec-memory/decisions/authored.generation.removal.1.json` (authority: user, evidence: this
   document and the owner's 2026-11-05 request) and set `supersedes` on / revise
   `authored.generation.dual-engine-comparison` and `generation.svg-mode-shared-authored-styles`.

Acceptance: numbers recorded; D2 resolved to A or C; D3 resolved to retire or shim; decision record
committed.

### P1 — Stop new authored generation

Backend:

- `models/generate_presentation_request.py`: keep `strategy: Optional[Literal["authored","svg"]]`
  for parsing, and reject `strategy == "authored"` with `ValueError("authored_strategy_removed")`,
  modelled on `registered_template_removed` (:427-430). Same in `models/public_v2_contracts.py`
  (precedent at :234). A durable job staged with authored fails on replay with this code.
- `services/generation_core/admission/strategy.py`: omitted strategy always returns svg. Delete the
  `source_content_restyle` + `page_size/orientation` → authored branch (:47-54). On
  `svg_creation_admission_violation(request)` return a 422 with that code instead of
  `("authored", violation)`. Update the docstring ("must never turn a previously-valid authored
  request into a 422" no longer holds).
- `services/svg_generation_admission.py:63-104`: remove the unreachable reasons
  (`svg_source_restyle_not_supported`, `svg_engine_conflict`, `document_canvas_selectors_invalid`,
  `source_content_format_not_supported`, `svg_source_template_selection_required` — each is already
  blocked earlier by the request model [V by the capability analysis; re-check each before
  deleting]). Keep `svg_source_verbatim_required`, `svg_design_reference_conflict` and the style
  resolution errors as 422s.
- Sync route `api/v1/ppt/endpoints/presentation_generate.py`: per D3, remove the sync handler (or
  replace with the enqueue-and-wait shim). `openai_spec.json` exposes only the sync route and is
  already broken for svg; point it at `/generate/async` + status polling.
- `services/public_generation_route.py:68-81` and public v2 contracts: drop the deck-ir engine and
  the `strategy="authored"` route; auto/free-design go to svg.
- `services/source_content/pipeline.py`: remove the authored reflow route; the svg dispatch must not
  be gated by `request.template == "authored"` [I, agent-reported at ~:1314 — locate the current
  line before editing]. Restyle that is not verbatim → 422.

Frontend (`servers/nextjs`):

- `store/slices/presentationGenUpload.ts:26` `PresentationGenerationStrategy`, `authoredVisionQa`,
  the authored branches in `upload/components/upload-page/useUploadPageState.ts` (:172, :372,
  :573, :577), `CanvasSelect.tsx`, `ConfigurationSelects.tsx`,
  `services/api/presentation-generation.ts` (:407, :759), `services/api/document-canvas-capabilities.ts`.
  Keep `authoredStyle` (it is svg's style id).
- `public-v2/PublicV2OperationsClient.tsx:261`: auto/free-design send svg.
- i18n: remove authored-strategy strings (29 Korean keys reported [I]); keep style-picker strings.

CLI / scripts:

- `scripts/ppt-agent.mjs`: remove `--strategy authored`, `--mode authored`, `--vision-qa`; always
  use the async route; change the `--template` default away from `"authored"` if the server no longer
  needs it [I].
- `scripts/benchmarks/authored-infographic/`, `scripts/bench_authored_render_policy.py` and other
  authored-only benchmarks: delete in P3.

Tests: invert tests that assert authored fallback; add tests for `authored_strategy_removed`, for
omitted strategy → svg on the former fallback inputs, and for each remaining violation → 422.

Acceptance: no request shape can start an authored generation; omitted strategy never yields
authored; frontend offers no strategy choice; CLI help has no authored option; svg generation and
export tests pass; full pytest passes.

### P2 — Existing authored decks

Common to A and C:

- Remove `"authored"` from `_DRIVERS` in `models/presentation_profiles.py` (keep the literals,
  §4.3). Then `resolve_presentation_profile` (:267-283) raises `unsupported_presentation_driver`,
  and the existing handling applies: `GET /presentation/all` returns
  `UnsupportedPresentationListItem` (`endpoints/presentation_crud.py:220`), `GET /presentation/{id}`
  returns 422, delete works. This is the template_v2 precedent (CLAUDE.md "legacy row").
- Verify the frontend list renders the unsupported marker for authored rows and that delete works.

Option A (no recent external use): announce removal with a grace period during which users export
themselves; then ship the driver removal.

Option C (recent external use):

1. Before the driver removal, a one-off script exports every authored deck through the existing
   export operation as **hybrid PPTX + PDF** (not `fidelity`, which is image-only), stores both as
   workspace artifacts linked to the presentation, and writes a manifest (presentation id → artifact
   ids, digests, failures).
2. Extend `UnsupportedPresentationListItem` with optional archived-artifact links and render a
   download action for them in the list.
3. Re-run failures; report decks that could not be snapshotted.
4. Ship the driver removal.

Acceptance: authored rows list as unsupported, open → 422, delete works; (C) every authored deck has
a manifest entry with either artifacts or a recorded failure.

### P3 — Delete authored-only code

Do this in small batches, each followed by the §4.4 checks. Order: frontend leaves → backend
endpoints → services → models → contracts/scripts/tests.

Make the cut first:

1. Move the job semaphore out of `authored_presentation_service` (§4.2) into an svg/shared module.
2. Cut import edges that pull authored-only modules into the svg closure without being needed:
   appendix A lists the 136 reached modules. Expected keep set is §4.2; everything else reached
   (e.g. `authored_admission_*`, `authored_terminal_*`, `authored_structural_repair*`,
   `authored_html_normalize*`, `authored_html_tree.*`, `authored_dom_*`, `authored_infographic.*`,
   `authored_patch_engine`, `authored_local_fit_policy`, `authored_vision_*`) is reached through a
   shared module that imports an authored-only one. Find each edge (the script records the importer
   and line), move the shared part or make the import authored-branch-only, and re-run until the
   closure equals the keep set. [I: some of these, e.g. parts of `authored_infographic` or
   `authored_dom_*` via `slide_capture`, may turn out to be genuinely shared; then add them to the
   keep set rather than forcing a cut.]

Then delete (authored-only per the analysis; confirm each against the closure):

- Backend: `services/slidespec_*` and `models/slidespec_v1.py` (no svg reach except the SQL model
  registered in `services/database.py`); authored generation in `generation_core/authoring/`
  (HTML authoring such as `author_deck.py`, `author_batch.py`, `author_slide_stream.py`,
  `author_vision_qa.py`, `author_structural_repair_*`; keep `author_slide_svg.py`,
  `author_raster_svg_layout.py`, `author_source_svg_layout.py` and whatever of `author_slide*.py`
  they import — `author_slide_svg.py:39` imports `author_slide`); authored local-fit,
  fit-envelope, repair-loop, terminal, admission, vision, structural/semantic repair modules;
  `authored_vnext` generation/package/deck_ir/semantic emission (keep the pieces in §4.2);
  `endpoints/authored_operations.py`; the retemplate request/handler; the authored branches of
  `public_export_worker`, `workspace_quality_export_operation_handlers`,
  `presentation_export_route_inputs`, `presentation_export_direct_attempt_semantic`,
  `presentation_export_projection` (+ `/{id}/export-projection`); `workers/public_export_hybrid.py`,
  `public_export_hybrid_raster.py`; `constants/presentation.py` chat-edit refusal; SlideSpec branch of
  `workspace_quality_operation_handler.py:1049-1052`; `utils/slide_capture_batch.py` `/pdf-maker`
  path (`AUTHORED_RENDERER_LEGACY_ENABLED`).
- Router `api/v1/ppt/router.py:93-117`: keep `"authored"` (it serves svg styles); remove
  `authored-vnext-generation`, `authored-vnext-package`, `authored-vnext-export`,
  `authored-semantic-emission`. `authored-vnext-master` stays if master pins stay (§4.2). Update
  `test_feature_module_registry`.
- Frontend: `lib/authored-hybrid/`, `scripts/public-hybrid-export-worker.mjs`,
  `AuthoredElementEditor.tsx`, `authored-editable-style-properties.ts`, `AuthoredTemplateChanger*`,
  `authoredTemplateChangerHelpers.ts`, `header/AuthoredHeaderActions.tsx` and
  `showAuthoredActions` in `PresentationHeader.tsx:403`, `AuthoredSlidePreview`, `ExportMenu.tsx`
  hybrid/fidelity options, the authored-vnext editor, `pdf-maker` if only authored uses it [I].
  Keep `AuthoredPageRemake*` only if svg remake uses it [I — svg remake is dispatched through
  `authored_page_remake_service`; check the UI side].
- Contracts: `contracts/authored_vnext` (except what master pins need [I]); keep
  `contracts/authored-renderer` (shared sandbox).
- Scripts/benchmarks/tests: authored-only scripts in `scripts/` and `servers/fastapi/scripts/`
  (e.g. `convert-authored-style.py`/`build-authored-style.py` — check whether they build the shared
  style catalogue before deleting [I]), authored benchmarks, authored-only tests. 264 test files
  mention authored, including 57 of 122 svg tests: **edit**, do not delete, tests that exercise svg.
- Env: remove the eight `SLIDESPEC_*` knobs and authored-only `AUTHORED_*` knobs from
  `utils/env_settings/authored_runtime.py`, `utils/get_env_programme.py`,
  `utils/get_env_concurrency.py`, `start.js`, `docker-compose.yml`, README env table — never the ones
  in §4.3.

Acceptance: svg closure (appendix A, module level) contains only the keep set; `import server`
works; svg generation, page remake, checkpoint recovery, timeout repurchase, partial recovery,
quality review, PPTX and PDF export pass; full pytest passes; `npm run test:next-ci` passes;
`ruff check .` passes.

### P4 — Database

After P2 is complete (and C's snapshots verified):

- Alembic revision dropping authored-only tables: `authored_vnext_*` (except master-pin storage if
  kept), `slidespec_*`, `authored_release_*_v3`, `authored_semantic_*`, `authored_slide_editor_event`,
  `authored_canonical_revision`, `authored_editable_hybrid_mvp_completion`,
  `authored_exact_provenance` — **only** after confirming no svg path reads them (several of these
  SQL models are in the svg closure today, appendix A, likely through model registration [I]).
- Keep `authored_slide_checkpoints`, `slides.html_content` and its constraints while authored rows
  exist. Deleting authored rows themselves is a separate owner decision, not part of this design.

Acceptance: upgrade and downgrade run on a copy of production; svg suite passes after upgrade.

### P5 — Docs and guards

- CLAUDE.md "Two generation pipelines", the four-terms list (authored, slidespec, authored-hybrid),
  the SlideSpec status paragraph, the CLI paragraph, the export bullet; AGENTS.md:18 and :160 (both
  partly wrong today: AGENTS says authored is image-per-slide, CLAUDE.md says genuinely editable —
  each is true for only one export mode). Record the removal in "What was retired" like template_v2.
- Keep `servers/fastapi/scripts/check_authored_style_samples.py` and its CI step (it guards svg's
  style data; pinned by `required-ci-contract.test.mjs:59`).
- Keep `scripts/verify-authored-release-required-checks.mjs` and the required step name (§4.3);
  update what the step checks if it validates removed files.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Runtime-only import failures on rare svg paths | §4.4: closure diff per batch, explicit tests for recovery/repurchase/remake, full pytest before merge |
| Old sealed receipts or staged jobs fail validation | Keep literals (§4.3); staged authored jobs fail with `authored_strategy_removed`, not a crash |
| An external API client relied on omitted-strategy fallback or sync `/generate` | P0 log check; D3 shim if found; named 422 codes so the failure is diagnosable |
| Deleting a module that looks authored-only but is shared | Keep set §4.2 plus closure check; when unsure, keep and note |
| Style catalogue mistaken for authored | It is svg's; never delete `utils/authored_styles*` or the data dirs |
| Doc drift | P5 is part of the definition of done |

---

## 7. Follow-up roadmap (separate from removal)

svg editing, in this order (cheapest and most valuable first):

1. **Whole-deck restyle for svg:** regenerate from the stored outline with a new style / master pin /
   svg template pin through the existing svg generation path.
2. **Workspace chat on svg decks:** admit `svg-v1` in `compatibility_migration/runtime.py`, route
   header requests to the svg common-header design op and slide-body edits to svg page remake.
3. **Text edit on svg elements** with re-measurement (overlap/clip probes) and export-gate re-check.
4. **Geometry edit** (move/resize), easier on fixed coordinates.

Later: rename shared infrastructure out of the authored namespace (§4.2), never the §4.3 literals.

---

## Appendix A — import closure tool and result

Module-level closure from the svg entry points at `696be2ac` reaches 136 authored/slidespec-named
modules:

```
models: authored_chapter_plan, authored_header_contract, authored_header_metrics,
  authored_illustration_plan, authored_infographic_plan, authored_visual_plan,
  authored_vnext.{capability_registry, deck.scene, deck.scene_digest, master.base, master.contract}
models.sql: authored_canonical_revision, authored_editable_hybrid_mvp_completion,
  authored_exact_provenance, authored_slide_checkpoint, authored_slide_editor_event,
  authored_vnext_repository, slidespec_artifact_bundle
services: authored_admission{,_capture,_checkpoint,_context,_policy,_producers,_receipt,_session,_shadow},
  authored_author_capacity_recovery, authored_batch_policy, authored_canvas,
  authored_checkpoint_{artifacts,errors,fence,html,publish_fence,render_artifacts,service,
  stage_batch,state,sweep_budget}, authored_composition_envelope_profile,
  authored_composition_policy, authored_font_signal,
  authored_generation_{observability,observability_metrics,performance,planner,preflight,
  timing_receipt,timing_receipt_e4,timing_values}, authored_header_body_extent,
  authored_illustrations, authored_infographic_assets, authored_local_fit_policy,
  authored_measured_gates, authored_presentation_artifacts, authored_provider_attempt_ledger,
  authored_provider_rate_profile, authored_quality_receipt_{helpers,v2}, authored_remake_schedule,
  authored_rollout_profile, authored_semantic_{candidate,role_policy,role_repair},
  authored_slide_checkpoint_bundle, authored_source_visibility_repair,
  authored_structural_repair{,_compare}, authored_terminal_{replay_plan,slide_fallback,
  slide_flag_only,slide_policy}, authored_title_recovery_contract,
  authored_vision_{advisory_prompt,contact_sheet}, authored_vnext.generation.scene_envelope
utils: authored_css_strip, authored_dom_{categories,fit_magnitude,footer_zone,quality,tags,
  text_escape}, authored_empty_media, authored_fit_edits, authored_font_{policy,stack},
  authored_fragmentation_gate, authored_geometry_brief, authored_header_{geometry,prompt,validation},
  authored_html_normalize{,_edits,_rules_name,_rules_title}, authored_html_tree.{*,parser_adapter,
  raw_diagnostics,receipts,rewrite_rules,serializer}, authored_image_slots,
  authored_infographic.{*,checker,contracts,css_*,icons,library,skeleton*,tokens},
  authored_patch_engine, authored_qid_namespace, authored_renderer_session,
  authored_style_{catalogue,exemplars,samples,svg_recovery}, authored_styles,
  authored_void_end_tag_normalize, env_settings.{authored_runtime,authored_vnext},
  get_env_authored_publish, llm_calls.authored_semantic_alias_canonicalizer
```

Tool (run from anywhere; `ROOT` is `servers/fastapi`; 4th arg is the output JSON, mapping each
reached module to `[importer, line]`):

```bash
python3 graph.py servers/fastapi \
  services.svg_presentation_service,services.svg_generation_admission top gen_top.json
python3 graph.py servers/fastapi services.svg_export_operation top exp_top.json
# omit "top" (pass "all") to include function-local imports
```

```python
import ast, os, sys, json
ROOT = sys.argv[1]
entries = sys.argv[2].split(',')
toplevel_only = len(sys.argv) > 3 and sys.argv[3] == 'top'

def modpath(mod):
    p = os.path.join(ROOT, *mod.split('.'))
    if os.path.isfile(p + '.py'): return p + '.py'
    if os.path.isfile(os.path.join(p, '__init__.py')): return os.path.join(p, '__init__.py')
    return None

def path2mod(p):
    r = os.path.relpath(p, ROOT)[:-3].replace(os.sep, '.')
    return r[:-9] if r.endswith('.__init__') else r

def imports(path):
    mod = path2mod(path)
    pkg = mod if path.endswith('__init__.py') else (mod.rsplit('.', 1)[0] if '.' in mod else '')
    try: tree = ast.parse(open(path, encoding='utf-8').read())
    except Exception: return []
    if toplevel_only:  # module level, including module-level if/try blocks
        nodes, stack = [], list(tree.body)
        while stack:
            n = stack.pop(); nodes.append(n)
            if isinstance(n, (ast.If, ast.Try)):
                for f in ('body', 'orelse', 'finalbody'): stack += getattr(n, f, [])
                for h in getattr(n, 'handlers', []): stack += h.body
    else:
        nodes = ast.walk(tree)
    out = []
    for n in nodes:
        if isinstance(n, ast.Import):
            out += [(a.name, n.lineno) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ''
            if n.level:
                parts = pkg.split('.') if pkg else []
                if n.level > 1: parts = parts[:len(parts) - (n.level - 1)]
                base = '.'.join(parts + ([base] if base else []))
            out.append((base, n.lineno))
            out += [(base + '.' + a.name, n.lineno) for a in n.names]
    return [(p, l) for m, l in out if (p := modpath(m))]

seen, queue = {}, []
for e in entries:
    p = modpath(e); seen[p] = None; queue.append(p)
while queue:
    p = queue.pop()
    for d, l in imports(p):
        if d not in seen:
            seen[d] = (p, l); queue.append(d)
json.dump({path2mod(k): (None if v is None else [path2mod(v[0]), v[1]]) for k, v in seen.items()},
          open(sys.argv[4] if len(sys.argv) > 4 else '/dev/stdout', 'w'), indent=0)
```

## Appendix B — sources

- `docs/generation-core-architecture-20260925.md` (svg default, authored frozen, M0 metrics)
- `docs/svg-generation-mode-design-20260916.md` §12.1 (only matched layout comparison)
- `docs/generation-quality-root-cause-and-composer-architecture-20260904.md` (authored failure rates)
- `docs/authored-editable-export-recovery-implementation-20260914.md` (hybrid native coverage)
- `docs/slidespec-production-quality-speed-analysis-20260805.md` §7 (78% false positives)
- `artifacts/authored-full-product-validation-20260910/repeatability-20260911.md`,
  `artifacts/authored-speed-decomposition-20260912/report.md`
- `.spec-memory/decisions/authored.generation.dual-engine-comparison.1.json`,
  `generation.svg-mode-shared-authored-styles.*`
