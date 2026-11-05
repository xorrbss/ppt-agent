# authored generation strategy removal: design (2026-11-05, rev 2)

Status: **design only. No code was changed.** Base: `6b095226` on `main` (rev 1 was written on
`696be2ac`; every fact below was re-read on `6b095226`).

**rev 2 (2026-11-05).** Rewritten after an independent three-way review (factual accuracy,
completeness, executability) scored rev 1 at 52/100 and found that following its P3 deletion list
would break svg in at least seven places. Appendix C lists every rev 1 claim that changed. Main
changes: (a) a module-level keep list plus a "mixed file" table that says which branch of each
shared file goes; (b) `template="authored"`, `generation_engine` and five more values added to the
literals that must never change; (c) D3 revised by the owner: the sync route **and the MCP server**
are removed; (d) CI gates that would turn `main` red are handled inside the phase that breaks them;
(e) P0 is an owner/ops task with queries and an addendum template; (f) P3 split into seven
revertible PRs with exact checks; (g) rollback, in-flight jobs, snapshot retention and user messages
are specified; (h) after a second verification pass found seven more svg dependencies in the
rev 2 draft, **deletion is computed, not listed**: a file may be deleted only when a checked-in
reachability tool reports it unreachable from the application roots (§4.1). The lists in §4.2,
§4.3 and P3 are known dependencies and candidates, not the authority.

Who does what:
- **P0 is an owner/ops task.** It needs read access to production data and logs, which a coding
  agent does not have. A coding agent may prepare the queries but must not invent results.
- **P1–P5 are coding-agent tasks**, in order, one PR per numbered unit, each merged only when its
  checks pass. Do not merge units.

Markers: **[V]** verified on `6b095226`; **[I]** inference the implementer confirms before relying
on it. **Line numbers are hints; symbols and quoted code are authoritative.** `main` moves quickly:
re-locate every line before editing.

Relation to other documents:
- `docs/svg-mode-improvement-plan-20261105.md` and `docs/svg-mode-quality-performance-review-20261105.md`
  depend on modules kept here: `models/authored_vnext/capability_registry.py` (`AUTHORED_FONT_FLOOR_PX`),
  `authored_styles/*.yaml`, `models/authored_infographic_plan.py`, and `author_rules_v10.py` (#133).
  Their D1–D5 are unrelated to this document's.
- Precedents: the template_v2 removal (CLAUDE.md "What was retired") and the design-pack removal
  (`registered_template_removed`, 2026-11-03).

---

## 1. Decision and why

Remove the `authored` generation strategy (LLM-written HTML/CSS per slide, `slides.html_content`).
`svg` (`svg-v1`, `slides.svg_content`) becomes the only generation path.

- **Structural.** Authored HTML reflows: one overflowing block pushes or clips its siblings, and the
  pipeline can only measure and repair afterwards (fit envelope, local-fit shrink, LLM repair loop,
  terminal fallbacks, SlideSpec). svg is fixed-coordinate, so a defect stays local, and the same
  geometry goes 1:1 into the PPTX. This is the owner's observed "breaks and overflows" problem.
- **Export.** Authored PPTX with `pptx_mode` unset is `fidelity`: one full-slide PNG per page,
  nothing editable [V `services/workspace_quality_export_operation_handlers.py`, `pptx_mode`
  default]. The opt-in hybrid mode measured 2,206 native of 2,916 targets
  (`docs/authored-editable-export-recovery-implementation-20260914.md`). Authored PDF is raster
  (0/32 selectable text), svg PDF is vector (32/32) (`docs/generation-core-architecture-20260925.md` §6a).
- **Already frozen.** svg became the default on 2026-09-26 and authored was declared frozen
  ("stays selectable, gets no new features", same document). Authored-only commits fell from 408
  (Sep) to 19 (Oct) and 10 (Nov 1–5) [git history, path-matched, approximate].
- **No svg user loses anything.** Every capability authored has and svg lacks (§2.1) is already
  absent for every deck made with the default strategy since 2026-09-26. (This is true by
  construction: it says the loss falls only on users who explicitly choose authored, not that the
  features are worthless; §7 ports the valuable ones to svg.)

Caveat for the record: there is no matched authored-vs-svg layout A/B after 2026-09-16 (the only
one, 24 responses, found no difference). The decision rests on the structural argument, the export
results and the frozen status, not on a paired overflow measurement.

---

## 2. What is lost, and what is not

### 2.1 Lost (accepted)

| Capability | Where | svg today | Follow-up |
|---|---|---|---|
| Element editor (text/style/geometry/image replace/crop/shape, undo/redo) | `AuthoredElementEditor.tsx`; canonical patch routes in `endpoints/authored_operations.py`; `authored-editor-events` router | none (`editor_adapter_id="svg-readonly"`) | §7 |
| Whole-deck restyle (`template.apply`, and non-header `design.apply`) | `AuthoredTemplateChanger`; `services/authored_retemplate_request.py`; `workspace_authored_template_confirmation_service.py` (route `/template-applications` in `endpoints/workspace.py`); the HTML path of `WorkspaceAuthoredTemplateOperationHandler` after its `common_header` early return | common-header design only | §7 (first) |
| Workspace chat on a deck | authored: header/theme/Q&A tools | refused `legacy_presentation_frozen` (`services/compatibility_migration/runtime.py`) | §7 |
| Sync `POST /presentation/generate` | `endpoints/presentation_generate.py` | 422 `svg_generation_requires_async_route` | removed (D3) |
| **MCP server** (`mcp_server.py`, built from `openai_spec.json`, opt-in `ENABLE_MCP_SERVER`) | exposes only the sync route, so it is already broken for svg | — | removed (D3) |
| Source restyle with `text="outline"` or `allow_summarization` (API-only; the UI always sends verbatim) | authored reflow | 422 `svg_source_verbatim_required` | named 422 (D4) |
| Quality review `analyze_and_fix` (SlideSpec repair) | `workspace_quality_operation_handler.py`, `analyze_and_fix` path | `analyze_only` | dropped (78% false-positive flags) |
| deck-ir engine (public v2 `generationMode`) | reachable only through authored | n/a | removed |
| Compatibility upgrade fork (legacy → authored) | `services/compatibility_migration/upgrade.py` | n/a | removed |
| Arbitrary CSS font families | browser-rendered | trusted, embedded fonts only | out of scope |

### 2.2 Not lost

LLM providers (each lists both drivers or neither in `constants/provider_registry.json`), canvases
(svg is a superset: 4:3, custom mm, A4), raster reconstruction, user PPTX templates, design
references, tables/charts in restyle, all outline-stage request fields, primary colour/wordmark/
master pin, page remake (single and batch), svg quality review, PPTX/PDF export, speaker notes, and
style reference images (svg reads a style's `slide_reference_images` as content-role references
when it has no `reference_images`, `drivers/svg/svg_style_adapter.py:80-81` [V]).

---

## 3. Decisions

**Confirmed by the owner on 2026-11-05.** D3 was revised the same day after the review found the
MCP dependency.

| # | Decision | Phase |
|---|---|---|
| D1 | Do not port authored editing before removal. Removal and svg editing are independent tracks (§7). | — |
| D2 | Existing authored decks: **measurement-gated** in P0. If any authored deck owned by an external tenant (§5 P0 defines it) was opened or exported in the last 90 days → **option C** (snapshot to PPTX+PDF, then hard remove). Otherwise → **option A** (announce, 30-day grace period, hard remove). Option B (keep read-only view/export) is rejected: it keeps the hybrid export, the most fragile part. | P2 |
| D3 | **Remove the sync `POST /presentation/generate` route and the MCP server** (`mcp_server.py`, `openai_spec.json`, its process, proxy routes and dependency). Remove CLI `--strategy authored`/`--mode authored`/`--vision-qa` and public-v2 deck-ir. No enqueue-and-wait shim (an svg deck takes ~4 min, beyond the proxy budget). | P1 |
| D4 | Omitted strategy **always** resolves to svg. An svg admission violation is a 422 with the violation's own code. No silent substitution (e.g. no default style for an unknown style id). | P1 |
| D5 | No renaming of shared infrastructure in this change. Persisted and wire literals (§4.4) are never renamed. | — |

Default assumptions taken by the design (override in the P0 addendum if wrong): grace period 30
days; announcement via an in-app notice on the workspace list plus the release note; rollback is
`git revert` of the phase PR (§4.7).

---

## 4. Ground rules

### 4.1 Deletion is computed, not listed

"authored" in a name does not mean authored-only, and the coupling is too deep to enumerate by
hand: two review passes each found svg dependencies the previous list missed. So the rule is:

1. **Cut references first.** A P3 PR removes references: a route, a `workspace_operation_catalog.py`
   entry, a router feature module, or the authored branch of a mixed file (§4.3).
2. **Then delete only what became unreachable.** `scripts/authored_reachability.py` (committed in
   P3a, tool in appendix A) computes the import closure in `all` mode (function-local imports
   included) from the **application roots**: `server`, `api.main`, every `workers/*.py`, and every
   handler module named in `workspace_operation_catalog.py` (string dispatch). A backend file may be
   deleted only if it is not in that closure after the PR's reference removals. Its `--check` mode
   takes the PR's deleted files (`git diff --name-only --diff-filter=D origin/main`) and fails if any
   of them is still reachable.
3. **Never delete** `alembic/versions/*` (history), or anything in §4.4.
4. **Frontend:** there is no closure tool; before deleting a file under `servers/nextjs`, `rg` its
   importers (path and alias forms) and require `npm --prefix servers/nextjs run build` and
   `npm --prefix servers/nextjs run test:ci-node` to pass.

Measured at `6b095226` [V]: from those roots the closure holds 1,443 modules; of the 491
authored/slidespec-named source modules (tests and scripts excluded), only 32 are unreachable, and
most of those are Alembic revisions. **Nothing meaningful is deletable today; every deletion follows
a reference cut.** For review, the svg-only closure (all 82 `services/svg_*.py`,
`generation_core/drivers/svg`, `workers/public_svg_*.py`, `workers/public_render_worker`,
`utils/authored_renderer_worker`, module level) reaches 145 authored-named modules (appendix A).

### 4.2 Known dependencies svg uses (keep)

This list records dependencies found so far, so that reviewers can tell a wrong cut from a correct
one. It is not exhaustive; the reachability check (§4.1) and the svg tests are the authority.

| Group | Keep | Evidence [V] |
|---|---|---|
| Canvas | `services/authored_canvas.py`; NX `lib/authored-canvas.ts` and the parts of `lib/authored-hybrid/` it and svg import (§4.3) | `svg_presentation_service.py`, `svg_export_operation.py`, `svg_renderer.py`, `svg_generation_admission.py`, `workers/public_svg_pptx_convert.py`, `workers/public_render_worker.py`; NX `authored-canvas.ts:3` imports `./authored-hybrid/schema.ts` |
| Checkpoints | `authored_checkpoint_{artifacts,errors,fence,html,publish_fence,render_artifacts,service,stage_batch,state,sweep_budget}`, `authored_deck_plan_checkpoint`, `authored_slide_checkpoint_bundle`; SQL `authored_slide_checkpoint`, `authored_deck_plan_checkpoints` | `svg_presentation_checkpoint.py`, `drivers/svg/publish.py`, `AuthoredStageError` in `drivers/svg/{author,collect,render,terminal}.py`; `svg_checkpoint_recovery.py`, `svg_partial_recovery.py`, `svg_timeout_repurchase.py`, `svg_timeout_repurchase_incident.py` (`AuthoredSlideCheckpoint`) |
| Slide PNGs, titles | `authored_presentation_artifacts` (`_save_slide_pngs`, image-set paths, the `authored_title` function), `authored_presentation_contracts` (`AUTHORED_TEMPLATE`) | `drivers/svg/publish.py`, `svg_document_mutations.py`, `svg_page_remake.py`, `svg_raster_renumber.py` |
| Generation core shared with svg | `presentation_generation_pipeline`, `presentation_generation_staging` (its `template="authored"` block writes svg's `INTERNAL_CANVAS_KEY`), `presentation_generation_operation_handler`, `services/authored_local_fit_policy` (staging, module level), `authored_local_fit_deadline` (operation handler) | staging :27, :164; operation handler :65 |
| Export core shared with svg | `public_export_operation` (svg imports `execute_isolated_export_operation`), `workspace_artifact_publication_service`; `presentation_export_route_inputs` and `presentation_export_direct_attempt_semantic` stay until P3c removes their call sites (§4.3) | `svg_export_operation.py:18`, `public_export_operation.py:44`, `workspace_artifact_publication_service.py:49` |
| Remake and quality locks | `authored_page_remake_service` (svg remake dispatch), `slidespec_artifact_store._json_cas_predicate` (used by svg remake through it), `authored_quality_review_service._ANALYZE_AND_FIX_LOCKS` (deck mutex for svg batch publish), `authored_quality_ocr` (readiness route) | `svg_page_remake.py:418`, `authored_page_remake_service.py:65-79`, `workspace_batch_remake_operation_handler.py:20,232` |
| Job control | the job semaphore (move out of `authored_presentation_service` in P3a), `authored_generation_progress_poller`, cluster gate `_is_authored_payload` in `presentation_generation_job_service.py` (svg payloads carry `template="authored"`, so svg jobs count against `AUTHORED_CLUSTER_JOB_CONCURRENCY`) | `svg_presentation_service.py` semaphore use; operation handler progress poller |
| Style catalogue | `utils/authored_styles*`, `authored_style_{catalogue,exemplars,samples,svg_recovery}`, the data dirs (authored_styles YAML, authored_style_assets, authored_style_samples), `endpoints/authored.py` style/canvas/sample routes and the `"authored"` router module, NX `AuthoredStylePicker`; `scripts/build-authored-style.py` (builds the YAML svg uses); `servers/fastapi/scripts/check_authored_style_samples.py` and `authored_style_samples_runner.py` (`STRATEGY = "svg"`) | `svg_generation_admission.py`, `svg_templates/catalog.py`, `drivers/svg/svg_style_adapter.py` |
| svg authoring | `generation_core/authoring/`: `author_slide_svg.py`, `author_raster_svg_layout.py`, `author_source_svg_layout.py`, `author_rules_v10.py`, `source_id_alias.py` (7 svg importers), `author_slide_images.py`, and `author_slide.py` with what it imports at module level (`author_slide_prompt`, `author_slide_stream`, `author_call_spans`, `author_slide_fallback`) until P3a moves the five private helpers svg uses (`_AUTHOR_EXECUTOR`, `_apply_anthropic_authoring_reasoning`, `_authoring_extra_body`, `_authoring_output_profile`, `_wait_for_author_rate_slot`) into a shared module; `utils/llm_calls/{author_prompt_split,author_slide_effort,author_prompt_profile}`, `author_prompt_cache_key`, `author_canvas_shape`; models `authored_{header_contract,header_metrics,illustration_plan,infographic_plan,chapter_plan,visual_plan}` | `author_slide_svg.py:46-52`, `drivers/svg/author.py`, `svg_common_header.py`, `assets/offer.py`, `infographic_assets.py` |
| Images, fonts | `utils/authored_image_slots`, `authored_source_images`, `utils/authored_font_policy`, `utils/authored_font_stack` | `svg_presentation_prepare.py`, `drivers/svg/asset_slots.py`, `svg_renderer.py` (function-local) |
| Vision review | `authored_vision_advisory_prompt`, `authored_vision_contact_sheet` | module-level imports of `generation_core/quality/vision_advisory.py`, which `svg_presentation_service.py` imports |
| Renderer | `utils/slide_capture` (and the `authored_dom_*` it imports at module level), `utils/authored_renderer_session`, `utils/authored_renderer_worker` (image entrypoint, `Dockerfile`), `utils/renderer_warmup` (`lifespan.py`), `/metrics/authored-render-runtime` (`api/main.py`), `renderer_worker_policy`, `contracts/authored-renderer` | `svg_renderer.py`, `workers/public_svg_pdf_export.py`; `drivers/svg/collect.py` reads `is_authored_renderer_framed_protocol_enabled` from `utils/get_env_programme.py` |
| authored_vnext pieces | `models/authored_vnext/{capability_registry,master/*,deck/scene*,narrative_ir_contracts,semantic/*}`; `services/authored_vnext/semantic/{canonical,typography,query,remake}`; `services/authored_vnext/generation/{scene_envelope,errors,legacy_pin,master_pin,sql_repository,effective_style,scene_compiler,legacy_bridge}`; `services/authored_vnext/integration/sql_repository`; `master.digests`; **`deck_ir.strategy`** (seals, validates and rebinds the route receipt on every public-v2 generation, svg included); `NativeFontByteRegistry`; `utils/env_settings/authored_vnext`; the `authored-vnext-master` router module | `svg_generation_admission.py:156-157`, `presentation_generation_pipeline.py:88-102`, `workspace_page_remake_operation_handler.py:16-24`, `workspace_quality_export_operation_handlers.py:25-28`, `public_v2_orchestrator.py:1067`, `workspace_service.py:2405`, `svg_classification_remeasurement.py:145`, `generation_core/style/master_projection.py`, `svg_trusted_fonts.py`, `svg_templates/frame_fonts.py`, `svg_export_quality.py`, `presentation_export_capabilities.py` |
| Dispatch and ops | `workspace_authored_async_admission`, `workspace_authored_preview_operation_handlers` (svg previews), `authored_batch_policy`, `authored_generation_timing_receipt`, `provider_idempotency` (`outcome_scope="authored"`) | `svg_quality_review_service.py`, `drivers/svg/quality.py`, `drivers/svg/timing.py`, `author_slide_svg.py` |
| Infra with authored labels only | `provider_durable_admission`, `provider_usage_instrumentation`, `public_worker_profiles`, `constants/timeouts.py`, `document_canvas_generation`, `presentation_export_capabilities`, `presentation_export_powerpoint_qa`, `utils/env_settings/authored_runtime` (svg-read knobs only), `utils/get_env_programme.py`, `utils/get_env_concurrency.py` | — |
| Shared request model | `models/generate_presentation_request.py` (both strategies; many `_authored_*` private attrs) | — |

When a P3 PR finds another dependency, add it to this table in the same PR. When unsure, keep the
module and say why in the PR.

Pinned digests: `services/verifier_revisions.py` pins source digests of `author_slide_svg.py`,
`author_source_svg_layout.py`, `source_id_alias.py`, `svg_author_output_view.py`,
`utils/authored_image_slots.py` and `semantic/typography.py`; any edit to them (including the P3a
helper move) must re-pin and keep `test_svg_verifier_revisions.py` green.

### 4.3 Mixed files: what goes, what stays

These files serve both pipelines. Delete only the authored part; move the svg part first when the
file itself goes.

| File | Delete | Keep / move |
|---|---|---|
| `api/v1/ppt/endpoints/authored_operations.py` | canonical patch/undo/redo routes, `/retemplate` | `remake/preview`, `remake/previews`, `remake/publish` (NX calls them in `presentation-generation.ts`), `/{id}/svg-quality-review`, `/quality-review/readiness` (no NX caller [V]; keep for API clients unless P0 shows none) |
| `services/workspace_authored_template_operation_handler.py` | everything after the `common_header` early return (serves both `template.apply` and non-header `design.apply`; there is no separate `template.apply` branch); `template.apply` catalog entry; `authored_retemplate_request.py`; the function-local import in `workspace_authored_template_confirmation_service.py:272` and route `/template-applications` (`endpoints/workspace.py:562`) | the `common_header` early return → svg header handler; `design.apply` catalog entry |
| `services/workspace_quality_operation_handler.py`, `authored_quality_review_service.py` | `analyze_and_fix` (SlideSpec) path | `analyze_only` → `execute_svg_quality_review`; **move** `_ANALYZE_AND_FIX_LOCKS` (svg batch publish mutex) and `authored_quality_ocr` before deleting the rest |
| `services/authored_page_remake_service.py` | HTML-slide branches | svg dispatch; first move `_json_cas_predicate` out of `slidespec_artifact_store` and decouple its module-level imports of `authored_local_fit_policy` and `authored_vnext.semantic.{query,remake}` |
| `services/authored_batch_remake_service.py`, `workspace_batch_remake_operation_handler`, `authored-batch-remake` router module | HTML-slide branches | svg targets (`target.is_svg()`) |
| `workers/public_export_worker.py`, `workspace_quality_export_operation_handlers.py` | authored fidelity/hybrid branches, `pptx_mode` | svg dispatch (first branch); `_admit_html` in `public_render_worker` used by svg PDF |
| `public_export_operation.py`, `workspace_artifact_publication_service.py` | the call sites into `presentation_export_route_inputs` and `presentation_export_direct_attempt_semantic` (around `:977`, `:851`, `:2087`) | everything else; only then can those two modules become unreachable |
| `/{id}/export-projection` + `presentation_export_projection` | only if P3b removed `pdf-maker` (it calls the route: `pdf-maker/page.tsx:10` → `export-presentation-route.ts:175`) | otherwise keep |
| NX `lib/authored-hybrid/` | capture (`browser-source.ts`), assembler, `native-table.ts`, `native-chart.ts` | `schema` and what it imports (`local-fit-browser.ts`), `bundled-fonts*` and what the collector pulls in (`google-fonts-*`, `font-policy`, `security-data`), `security`, `pptx-archive`; importers `svg-preview-fonts.ts:19` (#118), `svg-template-canvas.ts`, `authored-canvas.ts`, `PresentationRender.tsx`, `ThemePanel/index.tsx:7`, `export-presentation-route.ts`, `ooxml-vnext/{adapter-publication,adapter,security}.ts`, `public-v2/operation-client.ts`. Re-run `rg` on every file before deleting it |
| `constants/presentation.py` | chat-edit refusal constants | only together with their importers `services/chat/{service.py:21,memory_layer.py:14,outline_context.py:22}` |
| `services/compatibility_migration/__init__.py` | the `upgrade` re-export (:20) | `admission` (svg publish imports `compatibility_migration.admission`, `drivers/svg/publish.py:145`) |
| `services/generation_core/admission/strategy.py` (`resolve_generation_strategy`) | the authored return values | the function itself: it has 11 callers, including svg's `authored_canvas.request_canvas_contract` and `document_canvas_capabilities.py:200`; raise the removal 422 at the admission boundaries (request validation, generate/create endpoints), not inside it |
| `services/presentation_generation_pipeline.py` | authored path after `if request.template == AUTHORED_TEMPLATE:` | the svg dispatch inside the same gate |
| `services/generation_provider_admission.py` | `return "authored"` branch | svg resolution; replace the trailing raise with the removal code |
| `services/presentation_generation_staging.py` | — | rehydration of old payloads without a strategy sets `strategy="authored"`; P1 makes that fail cleanly (§4.6) |
| `services/workspace_cleanup_journal_service.py` | — | the `presentation.mode == "authored"` image-tree cleanup; svg decks also write under `images/authored/` [I: svg image trees may already leak on delete — check and fix in P2 by keying on the tree, not the mode] |
| `api/v1/ppt/endpoints/presentation_crud.py`, `presentation_create_canvas.py` | `strategy="authored"` on create | svg create |

### 4.4 Persisted and wire literals: never rename

- `template="authored"` (request default, `generate_presentation_request.py`; required by svg source
  modes; read by `authored_checkpoint_state.py` from `request_payload["template"]`; dispatch gate in
  `presentation_generation_pipeline.py`; sent by NX `presentation-generation.ts`). **Keep the CLI
  `--template` default.**
- `generation_engine` default `"authored"` and `effective_generation_engine` returning `"authored"`
  for svg (`public_generation_route.py`).
- `"authored"` in `PresentationDriverId`, `ContentModel`, `GenerationStrategy`, `StyleSource`,
  `_KNOWN_MODES`, `_KNOWN_THEME_MODES` (`models/presentation_profiles.py`), `presentation.mode` and
  `theme.mode` values; `style_source="authored"` for svg-v1.
- Table `authored_slide_checkpoints`, `authored_deck_plan_checkpoints`; column `slides.html_content`
  and the `slides.ui` XOR `html_content` constraints while any authored row exists.
- Provider-usage `operation="authored_generation"` (emitted by svg: `drivers/svg/author.py`,
  `quality.py`, `asset_slots.py`, `svg_common_header_generation.py`, `svg_presentation_checkpoint.py`,
  `svg_timeout_repurchase.py`, `workspace_svg_header_retry.py`, `provider_durable_admission.py`).
- Wire field `authored_style` (svg's style id); `GenerationRouteReceiptV1.selected_route`
  `"authored-fallback"` and the `engine` literal (`models/public_v2_contracts.py`); the sealed
  `"legacy-authored"` strategy and idempotency prefix (`workspace_authored_async_admission.py:148`,
  `deck_ir.strategy`).
- Tables svg master pins read: `authored_vnext_publication_pointers`,
  `authored_vnext_master_revisions` (`generation/sql_repository.py:182-198`, `get_master_head`),
  `authored_vnext_entity_heads` (`integration/sql_repository.py:162`).
- Image path `images/authored/<id>/sets/...` (read by share authorisation, `presentation_share.py`).
- Conversation `tool_profile` values (`models/conversation.py`); version strings
  `"authored-render-runtime/v2"`, `"authored-vision-advisory-v2"`.
- Env names svg reads: `AUTHORED_RENDERER_*`, `AUTHORED_RENDER_*`, `AUTHORED_JOB_CONCURRENCY`,
  `AUTHORED_CLUSTER_JOB_CONCURRENCY`, `AUTHORED_CHECKPOINT_*`, `AUTHORED_VNEXT_MASTER_ENABLED`,
  `AUTHORED_LOCAL_STAGE_ASYNC_ENABLED`, `AUTHORED_OCR_TESSDATA_PATH` (svg readiness OCR).
- Bitbucket required check name `Authored release readiness configuration`
  (`config/authored-release-required-checks.json`); renaming needs a branch-restriction change.

`_DRIVER_IDS` is **not** a literal to keep: P2 removes `"authored"` from both `_DRIVERS` and
`_DRIVER_IDS` together, because `models/presentation_profiles.py` raises
`presentation_driver_registry_incomplete` at import when they differ [V].

### 4.5 CI and tests

What runs today [V]:
- Bitbucket `fastapi-svg-unit-subset` runs `svg_unit_subset.py --check` and pytest over
  `tests/svg_unit_subset.txt` (474 test files, 217 authored/slidespec-named) — added by #128. rev 1's
  "CI runs no pytest" is wrong.
- Required step `Workspace chat-first browser E2E` runs `runAuthoredLiveE2e`
  (`servers/nextjs/scripts/run-workspace-e2e.mjs`, import at :26, call at :3286).
- Root `npm run test:next-ci` (`package.json`) runs, among others,
  `scripts/benchmarks/authored-infographic/run-benchmark.test.mjs`,
  `scripts/authored-vnext-controlled-load-retry-audit.test.mjs`,
  `scripts/authored-hybrid-evidence-retention.test.mjs`,
  `scripts/verify-authored-release-required-checks.test.mjs`, `test:settings-registry`,
  `check:structural-layering`.
- `scripts/required-ci-contract.test.mjs` pins `check_authored_style_samples` (:59),
  `tests/unit/test_authored_canvas_defaults.py` (:138) and requires
  `lib/authored-hybrid/{mode,pptx-archive,cleanup}.test.mjs` to exist (:999-1010).
- `scripts/structural-layering-allowlist.json` fails when a listed file disappears;
  `servers/fastapi/pyproject.toml` `[tool.ruff.lint.per-file-ignores]` has 33 entries naming
  authored files; `constants/settings_registry.json` has 59 `AUTHORED_*` entries (generated env
  code, `npm run generate:settings-registry`); `services/verifier_revisions.py` pins svg authoring
  source digests (§4.2).

Standard check set for every phase PR, from the repository root (run all, paste the summary into
the PR):

```bash
(cd servers/fastapi && uv sync --frozen && uv run ruff check . && uv run python -c "import server")
(cd servers/fastapi && uv run python scripts/svg_unit_subset.py --check)
(cd servers/fastapi && uv run pytest $(cat tests/svg_unit_subset.txt) -n 4 --dist loadfile -q)
(cd servers/fastapi && uv run pytest -n 4 -q)       # full suite, ~17 min, before merge
npm ci && npm ci --prefix servers/nextjs && npm run test:next-ci
npm --prefix servers/nextjs run build               # any PR that touches servers/nextjs
python3 scripts/authored_reachability.py --check    # from P3a on
```

When a phase deletes or renames a file: regenerate `tests/svg_unit_subset.txt`
(`svg_unit_subset.py --write`), drop it from `structural-layering-allowlist.json` and ruff
per-file-ignores, and update `required-ci-contract.test.mjs` if it pins it — in the same PR.
Edit, do not delete, tests that exercise svg (71 of 146 svg-named test files mention authored [V]).

### 4.6 Error envelope, in-flight work and user messages

- **Request validation:** `strategy="authored"` raises `ValueError("authored_strategy_removed")` in
  the request models, which surfaces as the existing 422 validation envelope, like
  `registered_template_removed` (`generate_presentation_request.py`, `public_v2_contracts.py`). Add
  a test that asserts the code appears in the response `detail`.
- **Staged and queued work:** a staged job whose payload rehydrates to authored (explicitly, or via
  the no-strategy legacy rule in `presentation_generation_staging.py`) must fail terminally with
  `authored_strategy_removed` as a non-retryable error, not loop. A queued workspace operation
  whose handler is gone fails with `unsupported_operation_type` (`workspace_operation_runner.py`).
  Both need a user-visible message.
- **User messages** (add to `constants/user_messages.py` and NX i18n ko/en):
  - `authored_strategy_removed`: "이 생성 방식은 종료되었습니다. 기본(svg) 방식으로 다시 생성해 주세요."
  - unsupported authored deck (list item and open): "종료된 생성 방식(authored)으로 만든 발표자료입니다. 열거나 편집할 수 없습니다." plus, under option C, a download action for the archived PPTX/PDF.
  - Today authored rows would show the generic "지원하지 않는 발표자료 형식"
    (`DevaxWorkspaceShell.tsx`, `ko.json`); make the authored case specific.

### 4.7 Rollback

- **P1 and P2** are each one self-contained PR; rollback is `git revert` plus redeploy. No feature
  flag: a flag would keep the authored path alive and add a registry entry for one release, while a
  revert is equally fast to deploy and leaves no residue.
- **Drain before P3c/P3d:** after P1 ships, wait until no authored generation, export or remake
  operation is queued or running (P0 query 3 re-run) before merging the PRs that delete their
  handlers.
- **P3** PRs are each revertible on their own (§5 P3 order).
- **P4** has no real downgrade: a downgrade recreates empty tables. Take and verify a database
  backup before the upgrade; that backup is the rollback.

---

## 5. Phases

### P0 — Measure and record the decision (owner/ops; no code change)

Definitions: **external tenant** = an owner account that is not an operator/admin account of this
deployment. **Opened** = a `GET /api/v1/ppt/presentation/{id}` in the access log; **exported** = a
workspace `export.pptx`/`export.pdf` operation.

Queries (read-only; adapt names to the deployment):

1. Authored decks: `SELECT owner_id, count(*) FROM presentations WHERE mode = 'authored' GROUP BY owner_id;`
   (confirm `mode` is the persisted driver selector via `resolve_presentation_profile` [I]).
2. Recent use: export operations on those presentation ids in the last 90 days (workspace operation
   tables), plus `GET /api/v1/ppt/presentation/<id>` hits in nginx/access logs for the same ids.
3. In-flight: queued or running operations and generation jobs whose payload has
   `strategy='authored'` or `template='authored'` with no svg strategy (re-run before P3c/P3d).
4. Fallback mix: authored generations in the last 30 days grouped by
   `_generation_strategy_reason` (`explicit` vs each fallback code).
5. Callers of sync `POST /api/v1/ppt/presentation/generate` and of `/mcp` in the last 30 days
   (access logs).

Write the results as an addendum at the end of this document:

```
## Addendum P0 (YYYY-MM-DD, by <name>)
authored decks: <n> across <owners> owners (<external> external)
opened/exported by external tenants in 90 days: <n>  → D2 = A | C
in-flight authored operations: <n>
fallback reasons (30 days): explicit <n>, <code> <n>, ...
sync /generate callers: <list>; /mcp callers: <list>
overrides of §3 defaults: <none | ...>
```

Spec-memory: add `.spec-memory/decisions/authored.generation.removal.1.json` (authority: user,
evidence: this document and the owner's 2026-11-05 decisions) superseding
`authored.generation.dual-engine-comparison` and `generation.svg-mode-shared-authored-styles`. Then
triage the other authored/slidespec/hybrid/deck-ir decisions (107 files match by name [V]): mark
each superseded, still-valid (it constrains a kept module), or retired, in one table appended to
the new decision's evidence. A coding agent may prepare this triage; the owner approves it.

Gate: P1 starts only after the addendum and the decision record are merged.

### P1 — Stop new authored generation (one PR, plus P1b for MCP)

Backend:
- Request models: keep `strategy: Optional[Literal["authored","svg"]]` for parsing; reject
  `"authored"` with `authored_strategy_removed` (§4.6) in `generate_presentation_request.py` and
  `public_v2_contracts.py`.
- `services/generation_core/admission/strategy.py`: `resolve_generation_strategy` keeps its
  signature (11 callers, including svg canvas code, §4.3) and stops returning authored: omitted
  strategy returns svg; delete the `source_content_restyle` + `page_size/orientation` → authored
  branch; for an svg admission violation it returns svg with that reason, and the **admission
  boundaries** (request validation and the generate/create endpoints) raise the 422 with the
  violation code. Update the docstring.
- `services/svg_generation_admission.py`: for each violation reason, first add a test proving
  whether the request model already blocks it; delete only reasons the test proves unreachable
  (candidates: `svg_source_restyle_not_supported`, `svg_engine_conflict`,
  `document_canvas_selectors_invalid`, `source_content_format_not_supported`,
  `svg_source_template_selection_required`). Keep the test.
- `services/generation_provider_admission.py`: remove the `return "authored"` branch (§4.3).
- Remove the sync route in `api/v1/ppt/endpoints/presentation_generate.py` and its tests.
- `presentation_crud.py` create and `presentation_create_canvas.py`: reject `strategy="authored"`.
- `services/compatibility_migration/upgrade.py`: remove the legacy → authored upgrade fork and its
  endpoint, and the `upgrade` re-export in `compatibility_migration/__init__.py` (keep `admission`,
  which svg publish imports).
- `services/public_generation_route.py` and public v2: drop the deck-ir route and
  `strategy="authored"`; auto/free-design go to svg. Keep `effective_generation_engine`'s literal
  (§4.4).
- Staging: §4.6 terminal failure for rehydrated authored payloads, with a test.

Frontend (`servers/nextjs`): `store/slices/presentationGenUpload.ts` (`PresentationGenerationStrategy`,
`authoredVisionQa`); authored branches in `upload/components/upload-page/useUploadPageState.ts`
(6 sites matching `generationStrategy === "authored"`); `CanvasSelect.tsx`, `ConfigurationSelects.tsx`;
`services/api/presentation-generation.ts` strategy types; `services/api/document-canvas-capabilities.ts`;
`public-v2/PublicV2OperationsClient.tsx` (auto/free-design send svg). Keep `authoredStyle` and
`template="authored"`. i18n: remove strategy-choice strings, add §4.6 messages.

CLI: `scripts/ppt-agent.mjs` — remove `--strategy authored`, `--mode authored`, `--vision-qa`;
always use the async route; keep `--template` default; update `scripts/ppt-agent.test.mjs`.

CI in the same PR: replace `runAuthoredLiveE2e` in `run-workspace-e2e.mjs` with an svg live check
or remove it (keep the required step name and its other checks); regenerate `svg_unit_subset.txt`.

**P1b — remove the MCP server:** `servers/fastapi/mcp_server.py`, `openai_spec.json`,
`tests/unit/test_mcp_server_startup.py`, the `mcp_server` part of
`tests/unit/test_document_canvas_header_clients.py`, `fastmcp` in `pyproject.toml`/`uv.lock` (no
other importer [V]), the `shouldStartMcpServer`/`appmcpPort` block in `start.js`, the
`/_mcp_auth_check`, `/mcp/` and `/mcp` locations in `nginx.conf`, the `/mcp` route in
`scripts/presenton-local-proxy.mjs` (+ its test), the MCP assertions in
`tests/unit/test_runtime_startup_security.py` (nginx MCP blocks and `start.js`, around :32-80),
`ENABLE_MCP_SERVER` docs in RUNNING-LOCAL.md, AGENTS.md, CLAUDE.md and `README.md` (around :169),
and the `fastmcp` entries in `compliance/release-sbom.spdx.json` and `NOTICE` (regenerate with the
repository's SBOM/NOTICE scripts so `check:release-sbom` and `check:notice` pass). `openai_spec.json`
is read only by `mcp_server.py`, nothing else imports `fastmcp`, and `uvicorn` stays available
through `fastapi[standard]` [V].

Acceptance (commands in §4.5, plus):
- tests: `strategy="authored"` → 422 `authored_strategy_removed` on generate, async generate, create,
  public v2; each former fallback input with omitted strategy → svg or its named 422; rehydrated
  authored staged job → terminal failure, no retry;
- `rg -n 'strategy.*"authored"' servers/nextjs/app servers/nextjs/store` finds no strategy choice;
- `node scripts/ppt-agent.mjs --help` lists no authored option;
- `test:next-ci`, svg subset and full pytest pass; required E2E step passes.

### P2 — Existing authored decks (one PR; before P3c)

- `models/presentation_profiles.py`: remove `"authored"` from `_DRIVERS` **and** `_DRIVER_IDS`;
  keep the literals (§4.4). Check `_canonical_driver_id` and `presentation_profile_persistence.py`,
  which also read the registry. Result: authored rows list as `UnsupportedPresentationListItem`,
  `GET /presentation/{id}` returns 422 `unsupported_presentation_driver`, delete works.
- Cleanup: make the image-tree cleanup independent of `presentation.mode` (§4.3) so deleting an
  authored or svg deck removes its `images/authored/<id>` tree; test both.
- §4.6 messages on list and open.

Option A: ship the in-app notice and release note; after the 30-day grace period (during which the
hybrid export still works, so P3c waits) ship this PR.

Option C (before this PR):
1. A one-off script exports every authored deck through the existing export operation as **hybrid
   PPTX + PDF**, falling back to fidelity PPTX for a deck whose hybrid export fails, and records a
   manifest (presentation id → artifact ids, digests, mode used, failures).
2. Store the snapshots as archived artifacts **exempt from `export_retention_service`**
   (`DEFAULT_EXPORT_RETENTION_DAYS = 7` would otherwise delete them) and serve them through a
   download route that does not call `resolve_presentation_profile`.
3. Extend `UnsupportedPresentationListItem` with optional archived-artifact links; render a download
   action.
4. Re-run failures; report decks that could not be snapshotted to the owner.

Acceptance: tests for unsupported list item, 422 on open, delete with image-tree cleanup, `import
server`; (C) every authored deck has a manifest entry, a download works after the driver removal,
and a retention sweep does not delete the snapshots.

### P3 — Delete authored-only code (seven PRs, in order)

Every P3 PR has the same shape: **(1) cut references** (routes, catalog entries, router modules,
authored branches of §4.3 files), **(2) run `authored_reachability.py`**, **(3) delete only the
files it reports unreachable** and that are named as candidates below. A candidate that is still
reachable stays, and the PR records why. The content column lists candidates, not a mandate.

| PR | Content | Depends on |
|---|---|---|
| **P3a** | No deletions. Commit `scripts/authored_reachability.py` (appendix A tool: application roots, `all` mode, `--check` against the PR's deleted files) with a unit test. Move out of authored-only modules what svg needs from them: the job semaphore (`authored_presentation_service`), the five `author_slide.py` helpers (§4.2), `_json_cas_predicate`, `_ANALYZE_AND_FIX_LOCKS`. Re-pin `verifier_revisions.py`. | P2 |
| **P3b** | Frontend leaves: `AuthoredElementEditor.tsx`, `authored-editable-style-properties.ts`, `AuthoredTemplateChanger*`, `authoredTemplateChangerHelpers.ts`, `header/AuthoredHeaderActions.tsx` and `showAuthoredActions` in `PresentationHeader.tsx`, `AuthoredSlidePreview`, the hybrid/fidelity options in `ExportMenu.tsx`, the authored-vnext editor, `pdf-maker` if no svg route renders through it (check `PdfMakerPage` importers first). Keep `AuthoredPageRemake*` if the svg remake UI uses it (check importers). | P3a |
| **P3c** | Export backend. Cut: authored branches of `public_export_worker.py` and `workspace_quality_export_operation_handlers.py`; the call sites in `public_export_operation.py` and `workspace_artifact_publication_service.py` (§4.3); `/{id}/export-projection` only if P3b removed `pdf-maker`. Candidates: `workers/public_export_hybrid.py`, `public_export_hybrid_raster.py`, `public_export_hybrid_evidence`, `presentation_export_route_inputs`, `presentation_export_direct_attempt_semantic`, `presentation_export_projection`; NX `scripts/public-hybrid-export-worker.mjs` and the capture/assembler part of `lib/authored-hybrid/` (§4.3). Update `required-ci-contract.test.mjs:999-1010` and `authored-hybrid-evidence-retention.test.mjs`. | P2 done, grace period over (A) or snapshots verified (C), drain (§4.7) |
| **P3d** | Authored HTML generation. Cut: the HTML branches of the §4.3 files (pipeline, page/batch remake, template handler, quality handler); `endpoints/authored_operations.py` canonical routes and `/retemplate`; `template.apply` catalog entry and `/template-applications`; `authored-editor-events` router module; chat-edit refusal constants with their chat importers. Candidates: `generation_core/authoring/author_deck.py`, `author_batch.py`, `author_vision_qa.py`, `author_structural_repair_*`, `author_slide_{patch,design_system}.py` (and `author_slide*.py`, `author_call_spans.py` only if P3a moved the helpers so `author_slide.py` no longer needs them); authored admission/terminal/fit-envelope/repair-loop/structural/semantic-repair modules; `authored_retemplate_request.py`. **Not** `authored_local_fit_policy` or `authored_local_fit_deadline` (svg staging and the generation handler use them). | P3a, drain |
| **P3e** | SlideSpec. Cut: the `analyze_and_fix` path (locks and OCR already moved in P3a/§4.3). Candidates: `services/slidespec_*` (after `_json_cas_predicate` moved), `models/slidespec_v1.py`; `SLIDESPEC_*` env and `NEXT_PUBLIC_SLIDESPEC_*` build args (`Dockerfile`, `docker-compose.yml`). | P3d |
| **P3f** | authored_vnext. Cut: router modules `authored-vnext-generation`, `authored-vnext-package`, `authored-vnext-export`, `authored-semantic-emission` (keep `authored`, `authored-vnext-master`); re-run the generic `test_feature_module_registry`. Candidates: authored_vnext generation/package/export/semantic-emission modules **not** in §4.2 (`errors`, `legacy_pin`, `master_pin`, `sql_repository`, `effective_style`, `scene_compiler`, `legacy_bridge`, `deck_ir.strategy`, `semantic.*` stay); `contracts/authored_vnext` except what master pins need; `scripts/start-authored-vnext-dev.ps1`, `authored-vnext-controlled-load-retry-audit*`; `utils/slide_capture_batch.py` `/pdf-maker` path (`AUTHORED_RENDERER_LEGACY_ENABLED`) only if P3b removed `pdf-maker`. | P3d |
| **P3g** | Scripts, benchmarks, tests, env: authored-only scripts in `scripts/` and `servers/fastapi/scripts/` (not `build-authored-style.py`, `check_authored_style_samples.py`, `authored_style_samples_runner.py`); `scripts/benchmarks/authored-infographic/` (and its `test:next-ci` entry) and `bench_authored_render_policy.py`; authored-only tests; authored-only `AUTHORED_*` entries in `constants/settings_registry.json` then `npm run generate:settings-registry` (never §4.4 names); ruff per-file-ignores and the structural allowlist for every deleted file; `Dockerfile` `authored-exact-sources` if unused. | P3b–P3f |

Acceptance for every P3 PR: §4.5 check set, including `authored_reachability.py --check`; tests for
svg generation, single and batch page remake, `design.apply`, svg quality review, checkpoint
recovery, partial recovery, timeout repurchase, PPTX and PDF export pass. Each PR states the files
and line counts it removed (`git diff --stat`).

### P4 — Database (one PR, after P3)

Take and verify a backup first (§4.7). Then one Alembic revision dropping authored-only tables,
each only after its SQL model is unreachable (`authored_reachability.py`) and `rg` shows no raw SQL
reader:

- drop candidates: the 8 other `authored_vnext_*` tables (`candidates`, `deck_revisions`,
  `export_artifact_receipts`, `export_jobs`, `generation_operations`, `quality_decisions`,
  `render_receipts`, `slide_revisions` — 11 exist; the 3 §4.4 master-pin tables stay; check
  `sql_repository` does not read `deck_revisions`/`slide_revisions` for master pins before dropping),
  `slidespec_artifact_bundles`, `slidespec_publication_attempts`, `authored_semantic_artifacts`,
  `authored_semantic_export_bindings`, `authored_release_artifacts_v3`, `authored_release_nonces_v3`,
  `authored_gate0_structural_{completions,events,subjects,traces}_v3`,
  `authored_evidence_{bundle_completions,graph_roots}_v3`, `authored_ci_evidence_identities_v3`,
  `authored_com_qualification_jobs_v3`, `authored_capability_receipts_v2`,
  `authored_deck_emission_{active,manifests}`, `authored_emission_bundles`,
  `authored_slide_editor_events`, `authored_canonical_revisions`,
  `authored_editable_hybrid_mvp_completions_v1`, `authored_exact_provenance`;
- keep: `authored_slide_checkpoints`, `authored_deck_plan_checkpoints`,
  `authored_vnext_{publication_pointers,master_revisions,entity_heads}`, and `slides.html_content`
  with its constraints while authored rows exist. Deleting authored rows is a separate owner decision.

Acceptance: upgrade on a restored copy of the production backup; svg check set passes after it.

### P5 — Docs and guards (one PR, last)

- CLAUDE.md: "Two generation pipelines" (one pipeline), the terms list (authored, slidespec,
  authored-hybrid), the SlideSpec status paragraph, the CLI paragraph, the export bullet, the MCP
  lines; add the removal to "What was retired".
- AGENTS.md (:18, :160 and the MCP section), RUNNING-LOCAL.md (29 authored mentions and the MCP
  section). README has no authored references [V].
- Keep `check_authored_style_samples.py` and `verify-authored-release-required-checks.mjs` with the
  required step names (§4.4); update what the verify script checks if it validates removed files.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Deleting a shared module that looks authored-only | deletion only when `authored_reachability.py --check` reports it unreachable (§4.1) |
| Cutting a reference svg still needs (a route, catalog entry or branch) | §4.3 mixed-file table, §4.2 known dependencies, P3 acceptance tests |
| Runtime-only failures on rare svg paths (function-local imports, string dispatch) | P3 acceptance names recovery, repurchase, batch remake, `design.apply`; full pytest before merge |
| `main` red from CI meta-tests and the authored live E2E | handled in the phase that breaks them (§4.5) |
| Staged or queued authored work loops or fails silently | §4.6 terminal failure + message; drain before P3c/P3d |
| Option C snapshots deleted by export retention | retention exemption in P2 |
| An API client relied on fallback, sync `/generate` or `/mcp` | P0 query 5; named 422 codes; release note |
| Old sealed receipts fail validation | §4.4 literals kept |
| P4 data loss | backup before upgrade is the rollback |

---

## 7. Follow-up roadmap (separate from removal)

svg editing, cheapest and most valuable first:

1. **Whole-deck restyle:** regenerate from the stored outline with a new style / master pin / svg
   template pin through the svg generation path.
2. **Workspace chat on svg decks:** admit `svg-v1` in `compatibility_migration/runtime.py`; route
   header requests to `design.apply` and slide-body edits to svg page remake.
3. **Text edit on svg elements** with overlap/clip re-measurement and export-gate re-check.
4. **Geometry edit** (move/resize), simpler on fixed coordinates.

Later: rename shared infrastructure out of the authored namespace (§4.2), never the §4.4 literals.

---

## Appendix A — import closure

Module-level closure at `6b095226` from every svg entry point (§4.1) reaches 663 modules, 145 of
them authored/slidespec-named:

```
models.authored_chapter_plan, models.authored_header_contract, models.authored_header_metrics,
models.authored_illustration_plan, models.authored_infographic_plan, models.authored_visual_plan,
models.authored_vnext.capability_registry, models.authored_vnext.deck.scene,
models.authored_vnext.deck.scene_digest, models.authored_vnext.master.base,
models.authored_vnext.master.contract, models.authored_vnext.narrative_ir_contracts,
models.authored_vnext.semantic.contracts, models.authored_vnext.semantic.primitive_contracts,
models.authored_vnext.semantic.typography, models.sql.authored_canonical_revision,
models.sql.authored_editable_hybrid_mvp_completion, models.sql.authored_exact_provenance,
models.sql.authored_slide_checkpoint, models.sql.authored_slide_editor_event,
models.sql.authored_vnext_repository, models.sql.slidespec_artifact_bundle,
services.authored_admission, services.authored_admission_capture,
services.authored_admission_checkpoint, services.authored_admission_context,
services.authored_admission_policy, services.authored_admission_producers,
services.authored_admission_receipt, services.authored_admission_session,
services.authored_admission_shadow, services.authored_author_capacity_recovery,
services.authored_batch_policy, services.authored_canvas, services.authored_checkpoint_artifacts,
services.authored_checkpoint_errors, services.authored_checkpoint_fence,
services.authored_checkpoint_html, services.authored_checkpoint_publish_fence,
services.authored_checkpoint_render_artifacts, services.authored_checkpoint_service,
services.authored_checkpoint_stage_batch, services.authored_checkpoint_state,
services.authored_checkpoint_sweep_budget, services.authored_composition_envelope_profile,
services.authored_composition_policy, services.authored_deck_plan_checkpoint,
services.authored_font_signal, services.authored_generation_observability,
services.authored_generation_observability_metrics, services.authored_generation_performance,
services.authored_generation_planner, services.authored_generation_preflight,
services.authored_generation_timing_receipt, services.authored_generation_timing_receipt_e4,
services.authored_generation_timing_values, services.authored_header_body_extent,
services.authored_illustrations, services.authored_infographic_assets,
services.authored_local_fit_policy, services.authored_measured_gates,
services.authored_presentation_artifacts, services.authored_provider_attempt_ledger,
services.authored_provider_rate_profile, services.authored_quality_receipt_helpers,
services.authored_quality_receipt_v2, services.authored_remake_schedule,
services.authored_rollout_profile, services.authored_semantic_candidate,
services.authored_semantic_role_policy, services.authored_semantic_role_repair,
services.authored_slide_checkpoint_bundle, services.authored_source_visibility_repair,
services.authored_structural_repair, services.authored_structural_repair_compare,
services.authored_terminal_replay_plan, services.authored_terminal_slide_fallback,
services.authored_terminal_slide_flag_only, services.authored_terminal_slide_policy,
services.authored_title_recovery_contract, services.authored_vision_advisory_prompt,
services.authored_vision_contact_sheet, services.authored_vnext.generation.scene_envelope,
services.authored_vnext.semantic.canonical, services.authored_vnext.semantic.typography,
services.workspace_authored_async_admission, utils.authored_css_strip,
utils.authored_dom_categories, utils.authored_dom_fit_magnitude, utils.authored_dom_footer_zone,
utils.authored_dom_quality, utils.authored_dom_tags, utils.authored_dom_text_escape,
utils.authored_empty_media, utils.authored_fit_edits, utils.authored_font_policy,
utils.authored_font_stack, utils.authored_fragmentation_gate, utils.authored_geometry_brief,
utils.authored_header_geometry, utils.authored_header_prompt, utils.authored_header_validation,
utils.authored_html_normalize, utils.authored_html_normalize_edits,
utils.authored_html_normalize_rules_name, utils.authored_html_normalize_rules_title,
utils.authored_html_tree, utils.authored_html_tree.parser_adapter,
utils.authored_html_tree.raw_diagnostics, utils.authored_html_tree.receipts,
utils.authored_html_tree.rewrite_rules, utils.authored_html_tree.serializer,
utils.authored_image_slots, utils.authored_infographic, utils.authored_infographic.checker,
utils.authored_infographic.contracts, utils.authored_infographic.css_base,
utils.authored_infographic.css_compact, utils.authored_infographic.css_data,
utils.authored_infographic.css_fill, utils.authored_infographic.css_flow,
utils.authored_infographic.css_list, utils.authored_infographic.css_orbit,
utils.authored_infographic.css_structure, utils.authored_infographic.icons,
utils.authored_infographic.library, utils.authored_infographic.skeleton,
utils.authored_infographic.skeleton_data, utils.authored_infographic.skeleton_flow,
utils.authored_infographic.skeleton_parts, utils.authored_infographic.tokens,
utils.authored_patch_engine, utils.authored_qid_namespace, utils.authored_renderer_session,
utils.authored_renderer_worker, utils.authored_style_catalogue, utils.authored_style_exemplars,
utils.authored_style_samples, utils.authored_style_svg_recovery, utils.authored_styles,
utils.authored_void_end_tag_normalize, utils.env_settings.authored_runtime,
utils.env_settings.authored_vnext, utils.get_env_authored_publish,
utils.llm_calls.authored_semantic_alias_canonicalizer
```

Tool. P3a commits it as `scripts/authored_reachability.py`, adding: the application roots built in,
handler modules parsed from `workspace_operation_catalog.py`, and `--check`, which reads
`git diff --name-only --diff-filter=D origin/main` and fails if any deleted backend module is in
the `all`-mode closure. Usage of the script below, from the repository root:

```bash
# deletion gate (§4.1): application roots, function-local imports included
H=$(grep -o '"[a-z_][a-z0-9_.]*\.[A-Z][A-Za-z0-9]*"' servers/fastapi/services/workspace_operation_catalog.py \
  | tr -d '"' | sed 's/\.[A-Z][A-Za-z0-9]*$//' | sort -u | sed 's/^/services./' | tr '\n' ',')
W=$(cd servers/fastapi && ls workers/*.py | sed 's/\.py$//; s#/#.#g' | tr '\n' ',')
python3 graph.py servers/fastapi "server,api.main,${W}${H}" all app_closure.json
# at 6b095226: 1,443 modules; 32 of 491 authored/slidespec-named source modules unreachable

# review aid: svg-only closure, module level (the list above)
E=$(cd servers/fastapi && ls services/svg_*.py workers/public_svg_*.py | sed 's/\.py$//; s#/#.#g' | tr '\n' ',')
E="${E}services.generation_core.drivers.svg,workers.public_render_worker,utils.authored_renderer_worker"
python3 graph.py servers/fastapi "$E" top svg_closure.json
```

The catalog at `6b095226` names 14 handler classes in 12 modules for 20 operation types [V].

```python
import ast, os, sys, json
ROOT = sys.argv[1]
entries = sys.argv[2].strip(',').split(',')
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
    p = modpath(e)
    if p: seen[p] = None; queue.append(p)
while queue:
    p = queue.pop()
    for d, l in imports(p):
        if d not in seen:
            seen[d] = (p, l); queue.append(d)
json.dump({path2mod(k): (None if v is None else [path2mod(v[0]), v[1]]) for k, v in seen.items()},
          open(sys.argv[4] if len(sys.argv) > 4 else '/dev/stdout', 'w'), indent=0)
```

Limits: static only. Other string or `importlib` dispatch than the catalog is not followed, and
`top` mode skips function-local imports, so the svg closure is a floor. The deletion gate uses `all`
mode from the application roots, so it over-approximates (an authored branch keeps its imports
reachable until it is cut), which is the safe direction. Behaviour that is still referenced but no
longer wanted (an svg branch removed by mistake) is not caught by the tool; the §4.3 table and the
P3 acceptance tests cover that.

## Appendix B — sources

- `docs/generation-core-architecture-20260925.md` (svg default, authored frozen, M0 metrics)
- `docs/svg-generation-mode-design-20260916.md` §12.1 (only matched layout comparison)
- `docs/generation-quality-root-cause-and-composer-architecture-20260904.md` (authored failure rates)
- `docs/authored-editable-export-recovery-implementation-20260914.md` (hybrid native coverage)
- `docs/authored-quality-speed-programme-design-20260905.md` (CJK text rasterized without embedded fonts; the CJK default now embeds fonts for hybrid exports)
- `docs/slidespec-production-quality-speed-analysis-20260805.md` §7 (78% false positives)
- `.spec-memory/decisions/authored.generation.dual-engine-comparison.1.json`,
  `generation.svg-mode-shared-authored-styles.{1,2}.json`

## Appendix C — rev 1 claims corrected in rev 2

| rev 1 | rev 2 |
|---|---|
| Delete `endpoints/authored_operations.py` | it also serves svg quality review and remake routes (§4.3) |
| Delete the retemplate handler | it routes svg `design.apply` (§4.3) |
| Delete NX `lib/authored-hybrid/` | svg imports `schema`, `bundled-fonts*` and others (§4.3) |
| `workspace_quality_operation_handler.py:1049-1052` is the SlideSpec branch | those lines are the svg `analyze_only` dispatch |
| Removing `"authored"` from `_DRIVERS` suffices | `_DRIVER_IDS` must change too, or import fails |
| CLI `--template` default may change | `template="authored"` is a literal svg needs (§4.4) |
| Keep set by group | module list, with missing entries added (`authored_batch_remake_service`, `vision_advisory` modules, `source_id_alias`, `author_rules_v10`, `author_slide_images`, `authored_checkpoint_html`, image slots, font policy, renderer worker/warm-up, progress poller, cluster gate, preview handlers, `provider_idempotency`) |
| Closure from 3 entry points: 136 modules | all svg entry points: 145 |
| svg dispatch gate in `source_content/pipeline.py` | `presentation_generation_pipeline.py` |
| "CI runs no pytest" | svg unit subset runs in CI (#128); required E2E runs an authored live check |
| `slide_reference_images` not read by svg | svg reads it (§2.2) |
| D3 retire sync route or shim | sync route and MCP server removed |
| P4 table names | corrected and completed list |
| No rollback, in-flight or snapshot-retention handling | §4.6, §4.7, P2 option C |

Second verification pass on the rev 2 draft (corrected before commit):

| rev 2 draft | rev 2 |
|---|---|
| Hand-written keep list + svg-closure `--check` is the deletion authority | deletion is computed from application roots in `all` mode (§4.1); lists are candidates |
| P3f deletes authored_vnext generation and `deck_ir` | `errors`, `legacy_pin`, `master_pin`, `sql_repository`, `effective_style`, `scene_compiler`, `legacy_bridge`, `deck_ir.strategy`, `semantic.*` are svg dependencies (§4.2) |
| P4 drops all `authored_vnext_*` (13) | 11 exist; 3 are read by svg master pins and stay (§4.4) |
| P3e deletes `services/slidespec_*` | svg remake uses `_json_cas_predicate` through `authored_page_remake_service`; move first |
| P3d deletes local-fit modules | svg staging and the generation handler import them |
| P3d deletes `author_slide_{prompt,stream,fallback}`, `author_call_spans` | the kept `author_slide.py` imports them; P3a moves the svg helpers first |
| P3c deletes `presentation_export_route_inputs`, `..._direct_attempt_semantic` | svg export imports them through `public_export_operation`/`workspace_artifact_publication_service`; cut call sites first |
| `template.apply` is a separate branch | the handler's whole HTML path serves both; confirmation service and route named |
| `resolve_generation_strategy` raises the removal 422 | it keeps returning svg; boundaries raise (11 callers) |
| `_ANALYZE_AND_FIX_LOCKS`, `authored_quality_ocr`, `compatibility_migration/__init__`, chat refusal importers, `verifier_revisions.py`, MCP startup-security test, README, SBOM/NOTICE | each now handled in §4.2, §4.3, P1 or P1b |
