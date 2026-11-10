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
- **B5 contracts, scripts, tests, env.** `contracts/authored_vnext` except master
  parts; delete authored-only tests and edit svg tests; remove `SLIDESPEC_*` and
  authored-only `AUTHORED_*` knobs (settings registry included, never §4.3
  names); shrink `scripts/structural-layering-allowlist.json` with each
  deletion (it fails on entries whose file no longer exists); regenerate
  `servers/fastapi/tests/svg_unit_subset.txt`.
- **B6 final.** Full pytest, `test:next-ci`, `ruff`, Next build, and the
  fake-LLM harness (`servers/fastapi/scripts/fake_llm_e2e/`) for svg
  generation, remake, checkpoint recovery and PPTX/PDF export. The harness
  does not reach the function-local-only paths, so the static check and the
  explicit unit tests above remain the main guard.

One PR per batch (or sub-batch) to GitHub `main`, mirrored to Bitbucket after
merge.

## What users lose

The element editor, whole-deck restyle and workspace chat exist only for
authored decks, so they disappear for new decks with P1 (svg decks never had
them). The svg editing roadmap in §7 of the design is the replacement and is
a separate track.
