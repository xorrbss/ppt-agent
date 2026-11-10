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
- **B2 frontend (parallel with B1).** Delete the authored-vnext generation and
  workbench UI, `components/authored-vnext`, `lib/authored-vnext/{flow,workbench}`,
  the element editor, template changer, header actions and the ExportMenu
  hybrid/fidelity options. Keep `authored-vnext/master-setup` (master pins
  stay). **Do not** touch `public-hybrid-export-worker.mjs`, `scripts/lib` or
  `lib/authored-hybrid` here. Verify with lint, `test:next-ci`, the i18n gate
  and a webpack build.
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
