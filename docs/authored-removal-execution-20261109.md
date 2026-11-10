# authored removal: execution (2026-11-09)

Owner decision 2026-11-09: remove the `authored` generation strategy. This
reverses, for authored, the 2026-11-07 freeze of non-SVG modes
(`docs/codex-brief-svg-freeze-quality-20261107.md`). The phase plan is
`docs/authored-removal-design-20261105.md` §5, with the changes below. Its
keep set (§4.2) and never-rename literals (§4.3) stand; line numbers in it
are stale and must be re-located. Decision record:
`.spec-memory/decisions/authored.generation.removal.1.json`.

## Changes to the 2026-11-05 design

| Item | 2026-11-05 design | Now |
|---|---|---|
| P0 production queries | Run, then pick D2 option A or C | Not run (no production access). D2 was first option C (snapshots); on 2026-11-10 the owner changed it to **option A without a grace period**: no snapshot or backup, the driver is removed and an authored deck behaves like a removed Template V2 deck (listed as unsupported, opening it is a 422, delete works). |
| D3 sync `/generate` | Retire, or shim if an external caller exists | Keep the route. With authored gone every request is svg, which it already refuses with `422 svg_generation_requires_async_route`. No shim. |
| P1 rejection point | Pydantic validator on the request model | The admission boundary (each entry point and the staged-job replay). The authored code stays until P3, so internal authored tests keep running until then. |
| Order | P0 → P5 | P1 first. P2 and P3 follow after P1 lands. |

## Phase status

| Phase | Status |
|---|---|
| P0 decision record | Done (this document and the spec-memory record) |
| P1 stop new authored generation | Done (merged 2026-11-10) |
| P2 existing decks (option A) | Done (merged 2026-11-10): authored rows resolve to `unsupported_presentation_driver` |
| P3 authored-only code | In progress |
| P4 database | Not started |
| P5 docs and guards | Not started (CLAUDE.md records the decision only) |

## P3 plan (2026-11-10)

Drafted on `main` at `a353838fe`, then reviewed independently and revised. It
replaces the cut-first steps of design §5 P3 where they differ. V = verified in
the code, I = inferred and still to be checked inside the batch named.

### Measurement corrections

- Appendix A's `graph.py` misses imports executed by package `__init__.py`
  files. With them, the module-level closure from
  `services.svg_presentation_service`, `services.svg_generation_admission` and
  `services.svg_export_operation` is 626 modules, 175 authored/slidespec-named
  (not 523/136); with function-local imports 1,239/419 [V]. Most of the gap
  comes from `services/authored_vnext/generation/__init__.py`, which re-exports
  `AuthoredVNextGenerationPipeline`, `mutations` and the scene compiler, plus
  `models/authored_vnext/deck/__init__.py` and `deck_ir/__init__.py`. Keeping
  `generation.legacy_pin`/`errors`, `deck.scene` or `deck_ir.strategy` keeps
  the whole vNext generation package unless those `__init__` files shrink.
- `svg_generation_admission.py:162-163` imports the vNext master-pin code
  function-locally, so `import server` cannot catch its deletion [V].
- The closure's first-importer record hides other importers. Every edge
  decision uses the full importer list per module.

### Keep-set corrections (§4.2)

Add (all used by svg at runtime, mostly through function-local imports) [V]:

| Module | svg user |
|---|---|
| `utils.authored_font_policy` | `svg_renderer.py:566`, every render |
| `utils.authored_image_slots` | `svg_partial_recovery`, svg prepare, asset slots |
| `services.authored_source_images` | `svg_presentation_prepare` |
| `utils.authored_ocr_quality` | `svg_templates/body_render_mask`, `icon_pack.checks` |
| `utils.authored_dom_quality` (`ensure_authored_qids`) | common render path and metric cache key in `slide_capture` [I: confirm it runs for svg] |
| `services.authored_vision_advisory_prompt`, `services.authored_vision_contact_sheet` | svg collect, quality, export visual review |
| `llm_calls.author_prompt_profile`, `author_prompt_split`, `author_slide_effort`, `author_prompt_cache_key`, `author_canvas_shape` | svg authoring, timeout repurchase |
| `authoring.author_rules_v10`, `authoring.author_slide_images` | svg authoring |
| `authored_vnext.deck_ir.integration`, `.grammar` | shared generation operation handler |
| `authored_vnext.narrative_ir` | `workspace_operation_runner`, stored content-integrity receipts |
| `authored_vnext.generation.effective_style` | `document_canvas_scene_layout` |
| `authored_vnext.{generation,ooxml}.account_lifecycle` | `account_deletion_controller`; keep until P4 drops the tables [I] |

`services.authored_generation_timing_receipt` stays in the keep set: besides
`drivers/svg/timing.py`, the shared `presentation_generation_operation_handler`,
`presentation_generation_attempt_timing` and `workspace_generation_timing_*`
use `GenerationTimingRecorder` and the stored-receipt readers [V].

`lib/authored-hybrid` is not authored-only on the frontend: `schema` is used by
`lib/authored-canvas.ts`, `PresentationRender.tsx`, `ThemePanel` and
`public-v2/operation-client.ts`; `bundled-fonts-manifest` by
`svg-preview-fonts.ts`; `security.ts` by `lib/export-presentation-route.ts`;
`pptx-archive` by `ooxml-vnext` [V].

New delete candidates not in the design: `lib/ooxml-vnext`,
`/api/internal/ooxml-vnext` and backend `authored_vnext/integration`, used only
by authored-vnext export [V].

### Done criterion per batch

"Closure equals the keep set" is not reachable and `import server` misses
function-local imports. Instead, each batch:

1. Fixes its delete set D as an explicit file list before deleting.
2. Statically proves that no remaining non-test module imports anything in D in
   any scope: module level, `TYPE_CHECKING`, function-local,
   `importlib.import_module` strings, and package `__init__` re-exports.
3. Statically proves that every remaining `from X import name` resolves.
4. Runs `uv run python -c "import server"`, `ruff check .`, the svg unit subset
   and the explicit unit tests of the rare svg paths: master-pin admission, svg
   template fonts, source remeasure, partial recovery, timeout repurchase, body
   render mask, chart verification follow-up, checkpoint recovery, page remake.

The full pytest suite and `npm run test:next-ci` run before each PR merges.

### Batches

- **B0 baseline (no code change).** Fix the measurement tool (`__init__` edges,
  all importers), save the closure and the static-check output, record the
  svg-subset and full-pytest failure baseline, and adopt the keep set above.
  Done 2026-11-10 except the full-pytest baseline:
  `servers/fastapi/scripts/authored_removal_imports.py` (`closure`, `check`)
  and `docs/session-results/authored-removal-p3-b0-20261110/`.
- **B1a move only.** Move `_AUTHORED_JOB_SEMAPHORE` to a shared module (env name
  `AUTHORED_JOB_CONCURRENCY` unchanged). Move `_save_slide_pngs` out of
  `authored_presentation_artifacts` (it depends only on hashing, atomic writes
  and path helpers [V]) and repoint the five svg importers. Point
  `drivers/svg/publish.py` at `generation_core.style.build.authored_title`
  directly (artifacts only re-exports it). Move the `author_slide` pieces svg
  uses (`_AUTHOR_EXECUTOR`, `_apply_anthropic_authoring_reasoning`,
  `_authoring_extra_body`, `_authoring_output_profile`,
  `_wait_for_author_rate_slot`, `Brand`, `EXACT_RECONSTRUCTION`,
  `normalize_reference_mode`) to a shared module; their users include
  `author_slide_svg`, `author_raster_svg_layout`, `author_source_svg_layout`,
  `runtime/profile.py`, `style/build.py:15`, `assets/offer.py`,
  `infographic_assets.py`. Update the 17 test files that reference moved
  symbols, including the two string `patch()` targets of
  `services.authored_presentation_artifacts._save_slide_pngs`, which would
  otherwise stop patching silently.
  Done 2026-11-10, names unchanged so existing monkeypatches keep reaching the
  code: `services/presentation_job_concurrency.py`
  (`PRESENTATION_JOB_SEMAPHORE`), `services/slide_image_sets.py` (the four
  image-set helpers), `generation_core/authoring/author_call_runtime.py`
  (executor, rate gate, reasoning/output settings), `generation_core/style/brand.py`;
  `EXACT_RECONSTRUCTION` and `normalize_reference_mode` are imported from
  `utils.llm_calls.author_slide_effort`/`author_slide_options`, where they
  were already defined. The old modules re-export every moved name. Tests
  retargeted: the two string patch targets, and the patches of
  `is_codex_selected`, `get_llm_provider` and the rate constants that the
  moved functions now read from `author_call_runtime`. No svg module imports
  `authored_presentation_artifacts`, `authored_presentation_service` or
  `author_slide` any more, but the authored-named closure is unchanged (175):
  those modules are still reached through the checkpoint seam
  (`authored_checkpoint_*` → `authored_admission_*` → `author_slide`) and
  `authored_generation_timing_receipt` → `authored_terminal_slide_fallback`
  → `authored_presentation_artifacts`, which is B1b's work. Outside svg,
  `presentation_generation_pipeline.py:84` (the authored strategy branch),
  `scripts/canvas_admission_table.py` and
  `evaluation/hybrid_review/LIVE_BENCHMARK.py` still import them (B3-B5).
  The moved code changed the source state of three verifier identities, so the
  new keys are listed as reviewed states of their current revisions in
  `services/verifier_revisions.py` (whole-page, partial-recovery code, page-render
  code). Verified against the B0 baseline: `ruff check .`, `import server`,
  `check` with no new unresolved names, and the svg unit subset with no new
  failure except the Chrome measurement tests in
  `test_svg_common_header_space_advance.py`/`test_svg_chart_paint_chromium.py`,
  which also fail on `main` on this host when run alone (21 of 50 there, 18 here,
  a different set each run).
- **B1b cuts that need judgement.** (a) Checkpoint seam:
  `authored_checkpoint_state`, `stage_batch` and `service` import
  `authored_admission_checkpoint` (→ `authored_admission`, `author_slide`),
  `admission_receipt`, `provider_attempt_ledger`, `generation_observability`,
  `slide_checkpoint_bundle` and `checkpoint_html` at module level, and svg
  calls `initialize_checkpoints` and `fail_stage`. Decide per import whether
  the HTML-admission branch of `admit_checkpoint_write` is dead for svg [I:
  svg passes no HTML and the gate defaults to shadow] before moving or
  removing it. (b) Shrink the vNext package `__init__` files so the kept
  pieces stop pulling the pipeline. (c) Make the `slide_capture_fit` imports
  of `authored_fit_edits`/`authored_fragmentation_gate` authored-branch-only;
  svg passes neither `fit` nor `fit_policy` (`svg_renderer.py:623`) [V].
  (d) Split `authored_page_remake_service`, through which svg remake
  dispatches. (e) `presentation_export_route_inputs` →
  `authored_local_fit_policy`, `composition_policy`,
  `public_generation_deadline_policy`, `get_env` → `get_env_authored_publish`,
  `renderer_worker_policy` → `env_settings.authored_runtime`.
  Analysed 2026-11-10 [V]: svg never reaches the HTML admission branch at
  run time. It calls only `initialize_checkpoints`, `fail_stage` and
  `progress_for_task` (the non-authored branch), none of which calls
  `get_checkpoint`, `admit_checkpoint_write` or `enforce_receipts`; the edges
  are imports. Split into three PRs: B1b-1 service seams, B1b-2 render and
  authoring edges (capture/fit, `resolve_style_primary`, `authoring_model`,
  `reference_parts`), B1b-3 the vNext `__init__` files and the page remake split.
  Kept as shared and renamed later: the checkpoint fence, errors, publish fence,
  provider attempt ledger, observability and artifacts modules,
  `authored_qid_namespace`, `authored_dom_tags`, `authored_local_fit_policy`,
  `authored_provider_rate_profile`, `renderer_worker_policy`,
  `env_settings/authored_runtime`.
  **B1b-1 done 2026-11-10**, names unchanged and re-exported from the old
  modules: `services/generation_checkpoint_rows.py` (`STAGES`,
  `initialize_checkpoints`, `fail_stage`, `progress_for_task`, plus
  `fail_stage_committed`, the same committed write the facade makes),
  `services/checkpoint_artifact_sweep.py` (the dispatcher's interval-gated
  sweep), `services/remake_caps.py` (`MAX_REMAKES_PER_SLIDE`,
  `MAX_TERMINAL_REMAKES_PER_DECK`, read by the timing receipt and the public
  deadline policy), `services/generation_receipt_storage.py` (receipt root,
  manifest path/lock/digest, `public_attempt_id`; the timing receipt imports
  the QualityReceipt v2 digest only inside the authored-only
  `create_generation_timing_receipt`), `services/stage_error_diagnostics.py`
  (the owner-capacity diagnostic validator `AuthoredStageError.as_safe_dict`
  runs on every page failure), `services/workspace_recovery_revision.py`, and
  `publish_flagged_allowed` in `generation_core/quality/terminal_policy.py`.
  svg callers (`drivers/svg/collect.py`, `publish.py`,
  `svg_presentation_checkpoint`, the cleanup journal, `provider_idempotency`,
  the progress endpoint, the job dispatcher) no longer import
  `authored_checkpoint_service`/`_state`; `presentation_export_route_inputs`
  hashes with `sha256_prefixed`; `workspace_service` lost an unused import.
  Not split, unlike the design: `authored_checkpoint_artifacts` (its imports
  are light, so `checkpoint_root`/`atomic_write` stay there until B4).
  Svg module-level closure: 175 → 54 authored-named modules (626 → 412
  modules; `author_slide`, `authored_admission*`, `authored_terminal_slide_fallback`,
  `authored_composition_policy` and `authored_quality_receipt_v2` are gone
  from it); the function-local closure is unchanged until B1b-2/B1b-3. Tests
  retargeted: the two string patches of `fail_stage`, the sweeper's interval
  and dispatcher patches, the shared checkpoint database helper (it also
  redirects the rows module), the attempt-timing v1 support module. New
  reviewed keys for the partial-recovery and page-render code identities.
  **B1b-2 done 2026-11-10**, the same move-and-re-export pattern:
  `utils/render_fit_floor.py` (`fit_font_floor_px`, `page_number_max_font_px`,
  from the fragmentation gate), `utils/slide_capture_fit_class.py` (`FIT_CLASS`,
  from the in-render fit script), `services/generation_core/assets/style_primary.py`
  (`resolve_style_primary`, from `author_slide_design_system`),
  `services/generation_core/authoring/reference_images.py` (`reference_parts` and
  the two image byte caps, from `author_slide_images`) and
  `utils/llm_calls/author_model_pin.py` (`authoring_model` and the profile
  context variable `author_prompt_profile` now shares). `slide_capture`, the
  pixel and glyph probes take `ensure_authored_qids` from
  `authored_qid_namespace`; `slide_capture_fit` imports the two fitter scripts
  and `authored_fit_edits` only where a fit is requested; the svg author, the
  source/raster svg layouts, the timeout repurchase modules and the asset offer
  import the new modules. Deviation from the design: `resolve_author_prompt_profile`
  stays in `author_prompt_profile` (its profile carries the prompt sections);
  the pin module makes the same two settings reads, so an invalid profile name
  is still refused. Svg module-level closure: 54 → 46 authored-named modules
  (412 → 393; `authored_dom_quality`, the fragmentation gate, `authored_fit_edits`,
  `authored_local_fit_policy` and the DOM escape/category/magnitude/empty-media
  modules are gone from it; the vision advisory pair stays until its own cut).
  The function-local closure is still 1250/420: the strategy branch and the vNext
  packages reach the whole authored pipeline until B1b-3 and later batches. Tests
  retargeted: the `authoring_model` patches in
  `test_svg_timeout_repurchase_incident.py` and `test_workspace_service.py`. New
  reviewed keys for all four svg code identities (common header, whole page,
  partial recovery, page render); no new module joined their file lists, since
  none took code out of a listed file.
  **B1b-3 done 2026-11-10.** (a) The `__init__` files of
  `services/authored_vnext/{generation,master,ooxml,integration}` are
  docstrings only; nothing in production imported their re-exports, and
  `test_authored_vnext_generation_planning.py` imports `generation.outline`
  and `generation.title_fit` directly. Importing one submodule (svg reads
  `generation.effective_style`/`errors`/`legacy_pin` and `master.digests`/`assets`)
  no longer loads the generation pipeline, the master service, the OOXML adapter
  or the SQL repository. (b) `services/page_remake_core.py` holds the
  format-neutral half of `authored_page_remake_service`, names and payloads
  unchanged and re-exported: `PREVIEW_KIND`, `AuthoredPageRemakeError`,
  `_canonical_digest`, `authored_page_source_digest`, `_set_task`, `_load_deck`,
  `_failed_preview_breadcrumb`, `_run_preview_background`,
  `queue_authored_page_remake_preview`, `remake_http_error`, and the two
  dispatchers `run_authored_page_remake_preview`/`publish_authored_page_remake`,
  which load the deck, send svg pages to `svg_page_remake` and authored pages
  to the HTML halves left in the authored service (`_preview_authored_html`,
  `_publish_authored_html`, imported where needed). `svg_page_remake`, the
  preview and publish workspace handlers, the authored operations endpoint and
  the remake history import the core; `svg_page_remake` takes `version_service`
  and `_json_cas_predicate` from their own modules. Module-level closure of the
  core plus `svg_page_remake`: 310 modules, 54 authored-named, against 737/303
  for the authored remake service. The workspace remake handlers still import
  the authored service (legacy bridge, candidate body validation) until the
  authored branch is deleted. Svg closure: top 393/46 (unchanged), all
  1250/420 → 1243/408. Tests retargeted: the three `_set_task` patches in
  `test_svg_page_remake.py`, the `async_session_maker` patches around
  `_run_preview_background` in `test_authored_page_remake.py` and the warning
  contract regression test, the background-failure test, and the admission
  patch in `test_authored_page_remake_target_scope.py`. No verifier identity
  changed.
- **B2 frontend (parallel with B1).** Delete the authored-vnext generation and
  workbench UI, `components/authored-vnext`, `lib/authored-vnext/{flow,workbench}`,
  the element editor, template changer, header actions and the ExportMenu
  hybrid/fidelity options. Keep `authored-vnext/master-setup` (master pins
  stay). **Do not** touch `public-hybrid-export-worker.mjs`, `scripts/lib` or
  `lib/authored-hybrid` here. Verify with lint, `test:next-ci`, the i18n gate
  and a webpack build.
  Done (2026-11-10): deleted the vNext flow and workbench pages (only
  `authored-vnext/master-setup` remains under that route), `components/authored-vnext`,
  `lib/authored-vnext/{flow,workbench}`, `lib/authored-editor`, the element
  editor, the template changer, `AuthoredHeaderActions`, the emission capability
  status, `MasterLayoutBadge`, the Hybrid coverage/history dialogs and the
  never-opened `ExportQualityDialog`, with their API clients
  (`authored-emission-capability`, `hybrid-export-history`) and tests. The
  header, export menu and More menu lost their `isAuthoredDeck` branches: the
  export menu offers PDF and one PPTX button, and every export command carries
  only `format` (svg decks never had a master pin in that context, so the
  commands svg decks send are unchanged). `masterPresentation.ts` keeps the pin
  readers the svg remake uses and drops the Workbench bridge, the quality
  advisory and the friendly-name helpers. The vNext e2e pieces went too:
  `cypress/e2e/authoredVnextLiveRoute.cy.ts`, the local happy-path script and
  config, `test:authored-vnext-local-e2e`, and the authored leg of
  `run-workspace-e2e.mjs` (its `run-authored-live-e2e.mjs` and the vNext env it
  added to the child processes). Messages: removed the namespaces only deleted
  screens used and every `authoredVnext` key except `master`, plus the export
  menu fidelity/hybrid and More-menu workbench keys. `PresentationHeader.cy.tsx`
  now mounts svg and template decks. Kept for later batches:
  `scripts/start-authored-vnext-dev.ps1` (it also launches master setup and
  export validation, and `test_isolated_public_workers.py` reads its worker
  limits; B5), `lib/ooxml-vnext`, `/api/internal/ooxml-vnext`,
  `lib/presentation-export-quality` and `public-export-admission`'s `pptx_mode`
  (B3), and the authored notice in `PresentationPage.tsx`. Checks on Windows:
  tsc, the i18n gate, structural layering, webpack build and the header
  component spec pass; `eslint .` reports the 12 existing `react-hooks/refs`
  errors in `WorkspacePartialRecovery.tsx`; the Next node suite fails only the
  CRLF-sensitive `requestVerbosity` source test, and `test:next-ci` stops at the
  Windows-only SBOM difference and the CRLF `uv.lock` match, none of them in
  touched files.
- **B3 backend endpoints and export.** `authored_operations.py`, retemplate, the
  four router modules (+ `test_feature_module_registry`), `/export-projection`,
  the authored branch of `public_export_worker`, `workers/public_export_hybrid*.py`,
  the chat-edit refusal, the SlideSpec branch of the quality handler and the
  `slide_capture_batch` `/pdf-maker` path. In the same PR, redefine
  `services/public_export_worker_bundle.py` for svg only and delete the hybrid
  worker, `scripts/lib` pieces and the authored-only part of `lib/authored-hybrid`.
  The bundle reads those files as required inputs of the export identity
  digest that svg exports share [V]; deleting them earlier would make every
  bundle fail with `worker_entrypoint_missing` and become unattested without
  any error. Redefining it changes the export identity digest: **needs the
  owner's approval before B3 starts.** Also `lib/ooxml-vnext`,
  `/api/internal/ooxml-vnext`, `authored_vnext/integration`.
  **B3 done 2026-11-10.** Backend: deleted `authored_operations.py` (its svg
  routes, the svg quality review, the review readiness and the three page-remake
  preview/history/publish routes, moved unchanged in path, payload and function
  name to `endpoints/presentation_page_operations.py`, feature module
  `presentation-page-operations`), the retemplate route, request model and
  service, the unused `workspace_authored_template_confirmation_service`, the
  vNext `generation`, `package` and `semantic` routers, `/export-projection`
  with `presentation_export_projection`, `workers/public_export_hybrid*.py` and
  `workers/public_export_bundle.py`, the chat-edit refusal (with
  `page_remake_slide_index` in chat turns, agent results and workspace chat
  results; stored results keep the key and `WorkspaceOperationCard.tsx` still
  reads it), the analyze-and-fix branch of `WorkspaceQualityReviewOperationHandler`
  (it now refuses every mode but `analyze_only`, which runs the svg review) and
  `utils/slide_capture_batch.py` with `slide_capture.capture_slides`.
  `workers/public_export_worker.py` runs svg export bundles only and refuses any
  other schema with `public_export_bundle_schema_unsupported`;
  `workspace_authored_template_operation_handler` routes `design.apply` for the
  common header to the svg header handler and refuses the rest with
  `authored_strategy_removed`. `public_export_worker_bundle.py` is
  `public-export-worker-bundle/v2`: the export worker entrypoint, the svg worker,
  converter and readback, `svg_contract`, `raster_redesign_pptx`, the svg export
  scene, projection and native wire, the native-object package and
  `vendor/slide_master`, all required; the Node assembler, `scripts/lib` and
  `lib/authored-hybrid` left it. Correction to [V] above: svg exports do not
  read this bundle. Their exporter identity is the converter digest
  (`svg_evidence`, `svg_export_identity`); only the authored controller path
  (`render_and_export_authored_htmls`, now unreachable) used the bundle, so the
  digest change moves no svg identity. Frontend: deleted
  `scripts/public-hybrid-export-worker.mjs`, all of `scripts/lib`, the `(export)`
  route group with `/pdf-maker`, `lib/export-presentation-route.ts`,
  `lib/presentation-export-quality.ts`, `/api/rasterize-icon` (only `/pdf-maker`
  used it), the files they alone imported (`useHtmlChartInjection`,
  `chart-browser`, `tailwind-browser`, `presentation-snapshot-integrity`,
  `presentation-export-strategy`, `presentation-export-font-quality`,
  `app-data-directory`) and 46 `lib/authored-hybrid` modules with their tests and
  fixtures. `public-export-admission` sends only `format` (plus an explicit
  `font_embedding: false`): `pptxMode` other than `fidelity` and
  `fontEmbedding: true` are 400, and its derived idempotency keys no longer
  carry the mode, so a retry that spans this deploy admits a new operation.
  Kept, with the reason (each needs its own structural change, not a
  workaround): `lib/authored-hybrid`'s Chrome renderer files
  (`browser-source`, `chrome-*`, `renderer-isolation`, `security*` and their
  imports, plus `html-instrumentation`, `font-policy` and `observation` for the
  renderer-isolation tests) because `contracts/authored-renderer/attestation.py`
  digests them into the renderer image's source-tree digest and
  `verify_contract.py` pins their isolation tokens, and svg rendering runs in
  that image (rewrite the renderer contract in B5); `services/authored_vnext/integration`,
  hence `lib/ooxml-vnext` and `/api/internal/ooxml-vnext`, because its SQL
  repository is the master-pin repository the kept master router and
  `generation/legacy_pin` (read by svg) use and its export half backs
  `legacy_bridge`, which the master apply route, the page-remake handler and the
  export handler call (split the repository from the export half first); the
  vNext `export` router, unmounted, because
  `scripts/public_service_malicious_corpus_evidence.py` drives it for the
  public-service release gate's cross-owner evidence (retarget or retire that
  evidence with the scripts in B5). The authored controller branches that are
  now unreachable stay for B4: `render_and_export_authored_htmls`, the authored
  branch of the export handler, `public_v2_orchestrator`'s release-review
  enqueue and the raster schema in the trusted wire. Checks on Windows: `ruff`,
  `import server`; import checker `check` over the 27 deleted Python files: 0
  hits, 0 new unresolved; closure top 393/46 (unchanged), all 1,171/338 (was
  1,243/408); structural layering passes; reviewed keys for the common-header,
  partial-recovery and page-render identities (only `slide_capture.py` changed);
  svg unit subset 219 failed against the B0 list of 257, no new failure after
  `verify_contract.py` dropped `slide_capture_batch.py`; the fake-LLM harness
  (isolated port and app data) generated a 5-page svg deck and the export worker
  produced its PPTX and PDF. Next: tsc, webpack build and `eslint` on the
  touched files pass; the Node suite fails only the CRLF `requestVerbosity`
  test.
- **B4 services, in sub-batches.** Clearly authored-only trees (`slidespec_*`,
  vNext emission/package/ooxml) can go as soon as they pass the static check,
  without waiting for B1 [I: P2 made authored unreachable]. Then HTML
  authoring modules, the rest of `authored_vnext` and the remaining
  `authored_*` services. SQL models stay until P4.
  **B4a done 2026-11-10.** Deleted 13 Python files and 11 authored-only unit
  tests (the delete set is the fixpoint of `authored_removal_imports.py check`,
  repeated until 0 source hits): `services/slidespec_candidate_selection.py`,
  the six `scripts/slidespec_*` benchmark/replayer tools, and the vNext
  `semantic` modules `editable_mvp_completion`, `editable_mvp_completion_codec`,
  `gate0_structural_v3`, `lifecycle`, `native_capabilities`, `qualification_v3`.
  Kept, because a kept module still imports them: the other `slidespec_*`
  services, `slidespec_repair_context`, `utils/slidespec_*`, the SlideSpec
  `utils/llm_calls` and `models/slidespec_v1` (`services/svg_page_remake.py`
  imports `slidespec_artifact_store`, and the authored quality review service,
  a B4b deletion, imports the rest); the vNext `ooxml`, `deck_ir`,
  `narrative_ir` packages and the rest of `semantic` (imported by
  `account_deletion_controller`, `integration/worker`, `public_v2_orchestrator`,
  `workspace_operation_runner`, `workspace_service`, the svg font modules via
  `semantic.typography`, and the authored services of B4b), with every package
  `__init__`; they become deletable once B4b/B4c remove those importers or move
  the shared pieces. Tests: the deleted modules left the module lists of
  `test_datetime_utils_as_utc`, `test_sha256_expression_migration` and
  `test_no_bare_timeout_literals`; `test_evidence_lattice_active_bundle` and
  its slidespec fixture stay. Closure unchanged (top 393/46, all 1,171/338);
  structural layering passes (26 entries, none removed); no svg file changed,
  so no reviewed keys; the svg subset list lost the 4 deleted tests (502
  files). Checks: `ruff`, `import server`, targeted pytest 269 passed.
  **B4b done 2026-11-10.** Deleted the authored generation branch and 189
  source files: the authored presentation service and everything only it
  reached (`authored_presentation_*`, the admission capture/legacy/publication
  stages, the terminal-slide fallback, repair loops, local-fit evidence and
  export, illustrations, author batch/deck/patch/vision QA, structural repair),
  the authored quality review service and its telemetry, the HTML halves of
  `authored_page_remake_service`, every `slidespec_*` service, `utils/slidespec_*`,
  `models/slidespec_v1.py`, the SlideSpec `llm_calls`, five pure facades
  (`authored_flag_classification`, `authored_terminal_slide_flag_only`,
  `authored_vision_advisory`, `authored_infographic_assets`,
  `authored_deck_infographic_plan`; their tests now import the
  `generation_core` modules they aliased) and the authored-only scripts.
  Moved unchanged: `_json_cas_predicate` to `services/json_cas_predicate.py`
  (the only piece `svg_page_remake` used from `slidespec_artifact_store`; one
  clean split), the release-review comparison tokens to
  `services/release_review_policy_metadata.py` (the checker policy digest is
  byte-identical), `AUTHORED_TEMPLATE` to the strategy module. svg page remake
  now lives in `services/page_remake_core.py` (svg-only; an HTML or
  master-pinned deck is refused with
  `authored_page_remake_unsupported_presentation`). Tests: 87 test files
  deleted, 80 edited by dropping only the tests that reach a deleted module,
  and the four orphaned `tests/fixtures/slidespec_*` fixtures.
  Kept for B4c because `deck_ir.product_adapter` and other B4c importers still
  reach them: `author_slide*`, `authored_admission` and its checkpoint chain,
  `authored_quality_receipt_v2`, `authored_composition_policy`, the export
  handler's authored branch, the orchestrator's release review,
  `legacy_bridge`, and the semantic republish code of the page and batch
  remake handlers. Closure top 393/46 (unchanged), all 1,024/217 (from
  1,171/338); structural layering passes (26 entries); svg reviewed keys added
  for the page-render and partial-recovery code (the pipeline lost its authored
  branch only); the svg subset list is 448 files. Checks: `ruff`, `import
  server`, targeted pytest 1,633 passed, 2 failures that also fail on
  `origin/main` (`test_hangul_allow_list_only_shrinks` on
  `constants/presentation.py`, and a header-context budget test that only
  runs when the private `delivery-backups` artifact is present); the fake-LLM
  harness generated a 5-page svg deck with its PPTX export, and a page remake
  (`remeasure_source`) previewed and published (revision 1 to 2), identical to
  `origin/main` (an `author` remake fails `svg_contract_canvas_mismatch` on
  both, a fake-LLM limitation).
  **B4c done 2026-11-11.** Deleted 94 source files: the vNext `ooxml` and
  `narrative_ir` packages; `deck_ir` except `strategy`, `grammar`,
  `style_profiles`, `canonical` and `contracts`; `semantic` except
  `canonical` and `typography`; `authored_vnext/generation` pipeline,
  `legacy_bridge`, `core_bridge`, montage, scene envelope/checkpoint,
  mutations, quality wire, gate0e probes, outline and title fit;
  `authored_admission*` (policy kept), the authored checkpoint service, state
  and stage batch; `author_slide*` (the svg author, effort, options, rules
  and reference-image modules stay); `source_content/placeholder`;
  `authored_canonical`, `authored_semantic_candidate`,
  `presentation_export_direct_attempt_semantic`; the orphans this left
  (`models/authored_visual_plan`, `authored_checkpoint_render_artifacts`,
  `authored_evidence_ledger`, `authored_header_body_extent`,
  `authored_terminal_slide_policy`, `author_call_spans`,
  `utils/authored_header_prompt`, `utils/authored_html_normalize`, six
  author-only `llm_calls`); and `scripts/authored_admission_fixture_replay.py`.
  `public_export_operation` lost the authored render/export path
  (`render_and_export_authored_htmls`, the canonical execution, the hybrid
  semantic helpers, `_resource_summary`), 1,693 to 252 lines. Edited, no svg
  behaviour change: the svg generation strategy receipt keeps its digests
  (the kept `deck_ir` modules feed `capability_preflight`) but no engine
  capability is offered any more, so with the non-default
  `DECK_IR_GENERATION_ENABLED` on, `availableCapabilities` is now empty; a
  staged deck-ir operation fails `authored_strategy_removed`; a source-native
  export without a content-integrity receipt fails
  `content_integrity_receipt_required` (no producer is left); the page and
  batch remake handlers no longer republish an authored semantic bundle; the
  export handler is svg-only (anything else fails
  `unsupported_presentation_driver`); the non-isolated Hybrid publication
  branch fails `workspace_export_semantic_binding_required`; the OOXML worker
  transport refuses scene payloads; the master router lost the authored-only
  `POST /presentations/{presentation_id}/apply` (`api-wire.json`
  regenerated). Kept for B5: the vNext export half (`integration` export
  service, artifact store, payload, admission policy, HTTP worker,
  `lib/ooxml-vnext`, `/api/internal/ooxml-vnext`) that the unmounted export
  router still imports; the frontend `masterClient.applyToPresentation` call
  in `OutlinePage` (reached only for a deck with `theme.authored_vnext` deck
  CAS, which only authored decks carry and which no longer open) with its
  endpoint and TS types, and the Python `ApplyMasterToPresentation*` models;
  `scripts/public-service-crash-recovery.py` (its function-local
  `authored_checkpoint_service` import now dangles; unusable since P1) and
  `scripts/authored_admission_shadow_report.py`; the orphan models
  `models/authored_vnext/semantic/{editable_mvp_completion,lifecycle,native_capabilities,release_evidence_v3,release_profile_v3}`
  and `utils/env_settings/authored_release`. Kept because svg-reachable code
  imports them: `authored_admission_policy`, `authored_composition_policy`,
  `authored_quality_receipt_v2`/`v3_pipeline`, the orchestrator release
  review and the `authored_vnext/generation` modules that
  `svg_generation_admission`, the pipeline and account deletion import
  (`legacy_pin`, `scene_compiler`, `account_lifecycle` and their helpers).
  Not addressed here (left for B5):
  `workspace_service._verified_authored_local_fit_checkpoint_recovery` and the
  trusted wire raster schema.
  For P4, SQL models without any service: `authored_capability_receipt`,
  `authored_release_evidence_v3`, `slidespec_publication_attempt`; registered
  in `database.py`/owner scope only: `authored_editable_hybrid_mvp_completion`,
  `authored_canonical_revision`; `authored_semantic_bundle` is read only by
  `authored_vnext/generation/sql_repository` (via `legacy_pin`). Tests: 40
  test/support files deleted, 73 edited (only the tests reaching deleted code
  dropped; tests of kept code retargeted to the shared modules, e.g. the
  checkpoint sweeper, the svg checkpoint progress test and the infographic and
  style tests; the two trusted-worker tests drop only their
  `_resource_summary` assertions). Closure top 378/37 (from 393/46), all
  907/138 (from 1,024/217); structural layering passes (25 entries,
  `utils/authored_html_normalize.py` dropped); reviewed keys added to the
  whole-page and partial-recovery ledgers (`vision_advisory` reads the byte
  caps from `reference_images`, which defined them); the svg subset list is
  424 files. Checks: `ruff`, `import server`, `check` with no new unresolved
  names, targeted pytest 1,895 passed, 2 failures that also fail on `origin/main` (`test_hangul_allow_list_only_shrinks`, and `test_every_literal_workspace_error_code_is_registered`, whose unregistered `authored_page_remake_unsupported_presentation` literal came with B4b); the fake-LLM harness generated 5-page svg
  decks with PPTX and PDF exports and published a `remeasure_source` page
  remake (revision 1 to 2); master router tests pass.
  **P4 done 2026-11-11.** Alembic revision `d1a5c7e9b3f2` (down revision
  `c5e8a1d3b7f9`, new `HEAD_REVISION`) drops 19 tables: `authored_canonical_revisions`,
  `authored_capability_receipts_v2`, `authored_editable_hybrid_mvp_completions_v1`,
  `authored_exact_provenance`, the ten `authored_*_v3` release/evidence/gate0 tables
  (`authored_release_artifacts`, `authored_release_nonces`, `authored_ci_evidence_identities`,
  `authored_com_qualification_jobs`, `authored_evidence_graph_roots`,
  `authored_evidence_bundle_completions`, `authored_gate0_structural_{traces,subjects,events,completions}`),
  `authored_semantic_artifacts`, `authored_emission_bundles`,
  `authored_semantic_export_bindings`, `slidespec_artifact_bundles` and
  `slidespec_publication_attempts`. Their seven SQL model modules were deleted (and
  three classes of `authored_semantic_bundle`), with the registrations in `services/database.py`
  (create_all list), `services/database_owner_scope.py`, `alembic/env.py` and the
  `AuthoredExactProvenance` delete in `PresentationDeletionService`. `downgrade()` recreates
  every table from the original shape (column order, defaults, checks, uniques, indexes; on
  PostgreSQL the controller grants and the owner RLS policy of `b5a0d3e7c9f1`).
  Kept, because kept code still reads or writes them: `authored_slide_checkpoints`;
  `authored_slide_editor_events` (the mounted `POST /presentation/{id}/editor-events` endpoint,
  with its service and weekly-report script, still writes it, and
  `test_authored_driver_removal` pins its 422 on an unsupported deck: dropping it means
  removing that endpoint, a separate decision); `authored_deck_emission_manifests` and
  `authored_deck_emission_active` (read by `authored_vnext/generation/sql_repository`
  through `legacy_pin`, which svg admission imports; the pointer references the manifests);
  every `authored_vnext_*` table (master-pin storage; the vNext export half, account deletion's
  `cancel_authored_vnext_exports_for_account` and the generation-operation fence still use
  them, so they leave with B5). `slides.html_content` and its check constraints are
  unchanged. `migrations.py` schema inference (for databases without an Alembic version
  row) no longer needs `authored_exact_provenance` or the SlideSpec tables to recognize
  a head schema: the workspace schema and the later markers are the evidence
  (`_authored_exact_provenance_revision`, `_post_slidespec_schema_revision`). Verification:
  SQLite, fresh file DB at `c5e8a1d3b7f9` with rows in 12 of the dropped tables plus an
  authored and an svg presentation: upgrade drops exactly the 19 tables and leaves the other 72
  tables and the presentations untouched, `PresentationDeletionService.tombstone` deletes both
  presentations, downgrade restores a snapshot identical to the pre-upgrade `sqlalchemy.inspect`
  output, a second upgrade matches the first. PostgreSQL 15 (throwaway cluster initdb'ed in the
  scratch directory on port 21950, removed afterwards; the live stack was not touched): the same
  cycle with rows in 8 dropped tables, `inspect` snapshots identical, and `pg_class` RLS flags,
  `pg_policies`, ACLs and grants identical after the downgrade. New test
  `test_drop_authored_only_tables_migration`; `test_owner_rls_migration` lists
  `authored_canonical_revisions` as removed after its migration. Closure top 374/33, all 903/134;
  svg subset 425 files.
- **B5 contracts, scripts, tests, env.** `contracts/authored_vnext` except master
  parts; delete authored-only tests and edit svg tests; remove `SLIDESPEC_*` and
  authored-only `AUTHORED_*` knobs (settings registry included, never §4.3
  names); shrink `scripts/structural-layering-allowlist.json` with each
  deletion (it fails on entries whose file no longer exists); regenerate
  `servers/fastapi/tests/svg_unit_subset.txt`.
  **B5 done 2026-11-11.** Deleted (131 files): the orphan non-SQL models
  `models/authored_vnext/semantic/{editable_mvp_completion,lifecycle,native_capabilities,release_evidence_v3,release_profile_v3}`,
  `utils/env_settings/authored_release`; the mounted but uncalled editor
  event write path (`POST /presentation/{id}/editor-events`, feature module
  `authored-editor-events`, its service and request model; nothing in the
  frontend called it; the `authored_slide_editor_events` table and SQL model
  stay for a follow-up migration, and its table tests stay); the authored
  local-fit checkpoint hydration retry in `workspace_service`
  (`_verified_authored_local_fit_checkpoint_recovery`; it admitted only failed
  authored `template.apply`/`design.apply` operations with
  `authored_local_fit_geometry_unresolved`, which nothing produces); the
  `ApplyMasterToPresentation*` models; `contracts/authored_vnext/{deck,deck_ir,narrative_ir,semantic}`
  (nothing reads them; `deck_ir`'s generator only exported kept modules);
  the Next.js Chrome renderer in `lib/authored-hybrid` (`browser-source` and
  its parts, `chrome-*`, `cdp-pipe`, `capture-*`, `css-paint-browser`,
  `shadow-effect-browser`, `export-admission`, `font-policy`,
  `html-instrumentation`, `observation`, `renderer-isolation`, `security*`,
  `windows-cleanup-coordinator` and their tests; nothing outside the folder
  imported them); scripts `start-authored-vnext-dev.ps1` (and
  `dev:authored-vnext:windows`), `authored_admission_shadow_report.py`,
  `authored-vnext-quality/`, `authored-hybrid-evidence-retention.mjs` (and its
  `test:next-ci` entry), `authored-release-readiness-operations.mjs`,
  `authored_deck_richness*`, `authored_infographic_weekly_report.py`,
  `authored_live_series_provider_usage.py` with its runner, and the tests of
  all of these. Frontend: `OutlinePage`'s `masterClient.applyToPresentation`
  branch with `masterApplyCas.ts`, the client method, endpoint and request
  type; `WorkspaceChatPanel` always sends `analyze_only` (the
  `analyze_and_fix` branch and `SLIDESPEC_WORKSPACE_REPAIR_ENABLED` are gone);
  the unused quality-review types and normalizers in `authored.ts` and the
  four `authored-quality-normaliz*` files; `WorkspaceOperationCard`'s
  `page_remake_slide_index` offer (nothing writes it since B3); the orphaned
  Chart.js and datalabels files in `public/vendor`. The trusted wire no longer
  stages a non-svg export bundle (raster PNG slides) or `sourceSlides`; the
  export worker still refuses such a bundle with
  `public_export_bundle_schema_unsupported`. Renderer contract: the seven
  Next.js sources left `attestation.py`'s source-tree digest, and
  `verify_contract.py` lost the four checks that pinned
  `renderer-isolation.ts` and `contract.json` its `next_execution_boundary`;
  the renderer image never contained them (it copies only the FastAPI
  import closure), the Python worker isolation checks are unchanged, and the
  source-tree digest changes as with any source change. Knobs removed (zero
  readers left): the eight `SLIDESPEC_*` and two `NEXT_PUBLIC_SLIDESPEC_*`
  (compose, Dockerfile build args), and 59 `AUTHORED_*` knobs (release and
  gate-0 evidence, semantic bundle/hybrid, editable MVP, vNext
  generation/compatibility, quality loop, design critique, retain checkpoint
  HTML, visual story plan, style anchor, illustration share/concurrency, VQA,
  header repair, text-scale rung, repair calls, streaming, wave barrier,
  in-render fit sample, evidence retention, slide pipeline concurrency, test
  failure hooks), 16 of them from the settings registry. Kept: every §4.3
  name; `AUTHORED_RENDERER_LEGACY_ENABLED` (the renderer policy, slide capture,
  `invoke.py` and the contract still read it); `AUTHORED_RESUME_MAX_ATTEMPTS`
  (read by the pipeline); `AUTHORED_VNEXT_OOXML_WORKER_TOKEN` (see below).
  **Not done, owner decision needed (release gate):**
  `scripts/public-service-release-gate.mjs` takes its cross-owner evidence
  from `servers/fastapi/scripts/public_service_malicious_corpus_evidence.py`,
  which drives the unmounted vNext export router; several surviving routes
  could carry the same cross-owner property (workspace artifact download plus
  operation cancel, the master router, presentation CRUD), so the router,
  `integration` export half, `lib/ooxml-vnext`, `/api/internal/ooxml-vnext`,
  `contracts/authored_vnext/{export,ooxml}`, `lib/authored-hybrid/pptx-archive`
  and `AUTHORED_VNEXT_OOXML_WORKER_TOKEN` stay. The gate's crash-recovery
  runner `scripts/public-service-crash-recovery.py` generates an authored deck
  (unusable since P1, with a dangling `authored_checkpoint_service` import);
  retargeting it to svg or retiring that evidence is the same decision, so it
  stays, as do the controlled-load scripts the gate's product evidence uses.
  Also kept: `bench_authored_render_policy.py` (benchmarks the shared
  `slide_capture` cache), `authored_fit_replay_*` (a DOM-category contract
  test imports it), `authored_infographic_fit_*` and
  `authored_run_contact_sheet*` (generic or kept-code tools),
  `benchmarks/authored-infographic`. Import checker `check` over the 25
  deleted Python files: 0 hits, 0 new unresolved; closure top 374/33, all
  897/128 (from 378/37 and 907/138 before B5 and P4). Structural layering
  passes (25 entries, none for deleted files); reviewed key added to the
  partial-recovery ledger (`workspace_service`); the svg subset list is 417
  files. Checks: `ruff`, `import server`, `verify_contract.py`, master
  generator `--check`, `test:settings-registry`,
  `verify-authored-release-required-checks.mjs`, targeted pytest 1,015
  passed with 2 failures, one fixed (a stale internal env name) and one that
  fails on `origin/main` too (`test_runtime_capabilities_error_registry_and_privacy_are_exact`,
  the registered `authored_page_remake_unsupported_presentation` missing from
  the contract fixture); `required-ci-contract.test.mjs` fails only the CRLF
  `uv.lock` match; Next: tsc, `check:i18n`, webpack build pass, the Node
  suite fails only the CRLF `requestVerbosity` test. The fake-LLM harness
  (port 18761, fresh app data) generated a 5-page svg deck and exported PPTX
  and PDF.
  **Release gate done 2026-11-11.** The owner chose to move the release
  gate's evidence off authored code (option 1). Commit `bdbe73558` retargets
  `servers/fastapi/scripts/public_service_malicious_corpus_evidence.py` to
  the workspace routes (owner B's artifact download, timeline and
  recovery-plan reads and operation cancel/retry are denied with 404 and the
  registered code; owner A's download and cancel succeed) and
  `scripts/public-service-crash-recovery.py` to an svg deck and the svg
  checkpoint writer; neither imports vNext export code any more. That
  unblocked deleting what stayed only for the decision: the unmounted vNext
  `export` router (`api/v1/ppt/endpoints/authored_vnext/export/`); the export
  half of `services/authored_vnext/integration` (`export_service`,
  `artifact_store`, `export_admission_policy`, `export_payload`, the HTTP
  `worker`) and the export-only repository parts that split out cleanly: the
  `sql_repository_jobs` and `sql_repository_publication` mixins, the
  composition mixin's `record_render_receipt`, the facade's
  `get_active_export_evidence`, its retry wrappers for the deleted methods,
  the async facade (only the export service used it), and the export-only
  types (`ExportArtifactInput`, `PublicationConflictError`,
  `SourceDigestMismatchError`, the request-hash and digest helpers);
  `contracts/authored_vnext/{export,ooxml}` (and the export line in
  `.gitattributes`); the Next.js `lib/ooxml-vnext` (including its
  master-compiler, master-inventory and master-readback modules: nothing
  outside the folder imported any of it, the kept master router and service
  never call Next.js, and no script, Dockerfile, package script or
  `compatibility/slide-master` file names it) and `/api/internal/ooxml-vnext`;
  `lib/authored-hybrid/pptx-archive` (only those two used it); the knobs
  `AUTHORED_VNEXT_OOXML_WORKER_TOKEN` and `AUTHORED_VNEXT_EXPORT_ENABLED`
  (zero readers left; neither was in the settings registry, `start.js`,
  compose, README or `proxy.ts`, so only `utils/env_settings/authored_vnext`,
  `tests/support/internal_env_names.py` and a live-harness env line changed,
  and `NEXT_INTERNAL_URL` left that module's `ENV_NAMES` because only the
  worker URL read it there). Tests: deleted
  `test_authored_vnext_ooxml_capability_binding`,
  `test_authored_vnext_export_integration`,
  `test_authored_vnext_export_repository_boundary`,
  `test_authored_vnext_ooxml_contract` and the Next.js tests in the deleted
  folders; edited by dropping only what reached deleted code:
  `test_authored_master_style_composition_focused` (two export replay
  tests), `test_authored_vnext_sql_repository` (three export-job tests and
  their helpers), `test_authored_vnext_export_contract_drift` (the three
  export-contract tests; the deck payload validation and migration tests
  stay), `test_color_contrast_routing`, `test_env_settings_authored_vnext`,
  the `WorkerError` cases of `test_api_error_exception_codes` and
  `test_generation_failure_contracts`, the module lists of
  `test_sha256_expression_migration`, the OOXML fixture exemption in
  `tests/authored-canvas-literals.test.mjs`, and
  `required-ci-contract.test.mjs` now pins the remaining
  `bundled-fonts-manifest` test instead of `pptx-archive`. Kept, with the
  reason: the master-pin SQL repository (`sql_repository`,
  `sql_repository_composition`, `sql_repository_types`) used by the master
  router, `generation/sql_repository`, `generation/legacy_pin` and the
  catalog seed script; every `authored_vnext_*` table and SQL model,
  including the export job, artifact receipt and render receipt tables
  (dropping them is a migration; account deletion's
  `cancel_authored_vnext_exports_for_account` still fences any existing
  export-job rows); `AUTHORED_VNEXT_OOXML_PACKAGE_ENABLED` (read by the
  workspace maintenance settings); and the `proxyClientMaxBodySize: "16mb"`
  in `next.config.mjs`, whose comment cites the deleted internal worker
  route, because lowering it changes every proxied request (a separate
  decision). `legacy_bridge` was already deleted in B4c; nothing live
  called the export half. Import checker `check` over the 14 deleted Python
  files: 0 source hits, 0 test hits, 0 new unresolved; closure top 374/33
  (unchanged), all 895/126 (from 897/128). Structural layering passes (25
  entries, none removed); the svg subset list is 412 files. Checks: `ruff`,
  `import server`, `verify_contract.py`, master generator `--check`,
  `test:settings-registry`, `verify-authored-release-required-checks.mjs`;
  `required-ci-contract.test.mjs` fails only the CRLF `uv.lock` match;
  targeted pytest (master API, catalog and assets, account deletion, SQL
  repository, env settings, internal env names, lifespan, svg admission,
  workspace runner and publication tests; 466 run) passed except
  `test_header_v2_metadata_preserves_exact_values_provenance_and_admitted_input`,
  the header-context budget test that only runs with the private
  `delivery-backups` artifact (the B4b known failure). Next: tsc, webpack
  build and `check:i18n` pass, `eslint` reports only the 12 known
  `WorkspacePartialRecovery.tsx` errors, and the Node suite (446 tests)
  fails only the CRLF `requestVerbosity` test.
- **B6 final.** Full pytest, `test:next-ci`, `ruff`, Next build, and the
  fake-LLM harness (`servers/fastapi/scripts/fake_llm_e2e/`) for svg
  generation, remake, checkpoint recovery and PPTX/PDF export. The harness
  does not reach the function-local-only paths, so the static check and the
  explicit unit tests above remain the main guard.

  **P5 done 2026-11-11.** Docs only. `CLAUDE.md`: the "Two generation
  pipelines" section is now "Generation: svg is the only strategy" (what
  `strategy` still parses, the 422 `authored_strategy_removed`, the one-entry
  driver registry, authored decks as unsupported/delete-only); authored,
  SlideSpec and authored-hybrid moved into the retired list with what was
  kept and why; the SlideSpec status paragraph and knob bullet, the CLI and
  export/callback/Chart.js sentences were corrected. `AGENTS.md` lost its
  authored-as-live statements; comments in `scripts/start-local-stack.ps1` and a
  banner on `docs/authored-vnext-manual-test-handoff.md` mark the deleted
  launcher. No code changed. The release-gate decision, the unmounted vNext
  export router and the `authored_slide_editor_events` drop stay open (the
  first two were closed later the same day, see "Release gate done
  2026-11-11" under B5).

**Follow-ups done 2026-11-11** (owner: "tables may be deleted, data need not
be kept"; one agent per worktree, svg unit subset run serially by the
orchestrator):

- #182: the server-filled `SlideOutlineModel.chapter` (added in `878e036cc`) is
  dropped from the outline response schema; Codex strict structured output had
  rejected every outline call with `invalid_json_schema`. A real Codex 5-page
  svg deck then generated, exported PPTX and PDF, and remade a page.
- #184: `next.config.mjs` keeps `proxyClientMaxBodySize: "16mb"`; its comment
  no longer cites the deleted `/api/internal/ooxml-vnext` route.
- #185: `WorkspacePartialRecovery.tsx` mirrors its attempt ref into state for
  render; `npm run lint` has 0 errors.
- #186: `servers/fastapi/assets/icon_families/** -text` (indexed bytes, 3 of
  them CRLF, are checked out exactly), CRLF-agnostic reads in
  `required-ci-contract.test.mjs` and `requestVerbosity.test.mjs`, and the
  unused `/api/upload-image` route removed. The Windows svg unit subset went
  from 214 to 21 failures, none outside the B0 list.
- #187: Alembic revision `e7b2d4f6a8c1` drops eight tables no code read any
  more: `authored_vnext_export_jobs`, `_export_artifact_receipts`,
  `_render_receipts`, `_quality_decisions`, `_generation_operations`,
  `authored_deck_emission_manifests`, `authored_deck_emission_active`,
  `authored_slide_editor_events`. Its `downgrade()` recreates them empty (the
  downgrades of `a6b2c4d8e0f3` and `b5a0d3e7c9f1` need them). Removed with them:
  the models, the account-deletion fences, `generation/account_lifecycle.py`,
  `generation/sql_publication.py`, most of `generation/sql_repository.py` and
  `AUTHORED_VNEXT_OOXML_PACKAGE_ENABLED`; the workspace-maintenance storage
  error is now `authored_vnext_master_storage_unavailable`. Kept:
  `authored_slide_checkpoints`, `authored_deck_plan_checkpoints` and the six
  master-pin tables. The master pin is a live feature, not a leftover: the
  upload page's company-standard picker (`NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED`
  defaults on) sends `masterPin`, and svg admission resolves it through
  `legacy_pin` into the style (`generation_core/style/build.py:163-188`), so a
  pin changes palette, fonts and template rules. Removing it is a product
  decision. Round trips verified on SQLite and a throwaway PostgreSQL 15.

**Release-gate runners, not yet PASS** (branches pushed, not merged, pending
an owner decision on where this evidence should run):

- `fix/malicious-corpus-runtime-20261111` (`7f67c9024`): `_worker_boundary_checks`
  had never passed (its hard-coded environment lacked three values the validator
  required since both were written in `c52e2a7ce`); it now derives the
  isolation contract from `services/public_worker_profiles.py` and observes 18
  accept/reject cases per role. The end-to-end run then stops in the HTML corpus:
  `_run_render_job` predates the trusted-executor dispatcher (`d78d5a307`) and
  calls `execute_isolated_worker_job` without `presentation_id`. Its
  network-none and safe-HTML gate checks also need the Docker-based renderer
  harness (`docs/experiments/authored-vnext/04-renderer-security`), which is
  authored-renderer evidence; Docker is not available on the Windows host.
- `fix/crash-recovery-live-20261111` (`81d6864a1`): lease expiry now follows
  the operation lease (a workspace-linked job's lease columns stay NULL), and
  the token-leak scan skips files shorter than the token (Windows `.lock`
  files) and no longer overwrites `failureDetail`; the second fix is not yet
  re-run. In three real Codex runs the crash, lease expiry, reclaim, outbox
  events and the successor's `run.status` with `checkpoint_reuse_count` all
  happened, but the successor failed with `public_provider_result_unknown`:
  the runner kills the worker at the first rendered checkpoint while other
  pages' provider calls are `reserved`, and svg recovery refuses to resume
  over a paid call with an unknown result
  (`services/svg_presentation_checkpoint.py:447,496`).

One PR per batch (or sub-batch) to GitHub `main`, mirrored to Bitbucket after
merge.

## What users lose

The element editor, whole-deck restyle and workspace chat exist only for
authored decks, so they disappear for new decks with P1 (svg decks never had
them). The svg editing roadmap in §7 of the design is the replacement and is
a separate track.
