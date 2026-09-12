# Dead and duplicate source audit — 2026-09-12

Branch `serve/main-20260902`, HEAD `4848dcbb7`, working-tree content (not HEAD) as of 2026-09-12.
Read-only audit; no source file was changed. Nine parallel auditors, each with a disjoint
scope and a shared contract (evidence rules, label set, known holds, deletion coupling lists).
Every DEAD claim below was hand-verified by at least: dotted import-path grep, basename grep
with extension, lazy-import check (function-body imports, `importlib`, dynamic `import()`),
and string/spawn reference check (`runnerPath`, `Path / "x.mjs"`, `python -m`).

Previous audit: 2026-09-05 (109 + 24 + 43 files removed). This audit starts from that baseline
and adds duplicate detection, "implemented vs executed" reachability, and read-without-writer
field analysis.

## 1. Headline

| Category | Count | Approx. lines |
|---|---|---|
| Already deleted on `main`, still present here (merge, don't re-delete) | 208 files | ~40k |
| DEAD files (zero importers, verified) | 27 | ~1,900 |
| RETIRED-REMNANT files on this branch (retired feature, unreachable) | 9 files + 8 symbols | ~2,200 |
| TEST-ONLY production modules | 17 | ~5,300 |
| DEAD-SYMBOL clusters (unreferenced functions/reducers ≥15 lines) | 31 | ~1,600 |
| DUPLICATE families (confirmed, both locations read) | 93 | ~5,000 duplicated |
| Behavioural forks inside duplicates (same concern, different result) | 12 | — |
| Fields read in production but never written in production | 12 groups | — |
| Receipt/telemetry keys consumed with no producer | 6 clusters (~70 keys) | — |
| Unused npm dependencies | 6 | — |
| Cypress specs never run by any CI pipeline | ~54 of 77 | — |
| Tracked JUNK | 2 (BOM, evidence JSON) | — |

The single largest item is not dead code in the usual sense: this branch is 175 commits
ahead of and 72 behind `main`, and `main` already removed the Composer, the Electron shell,
and the PyInstaller entrypoint. See §2 before touching anything in those clusters.

## 2. Already deleted on `main` — merge, do not re-delete

`git diff --name-status --diff-filter=D $(git merge-base HEAD main) main` lists 208 files, all
still present in this worktree.

| Cluster | Files | Commit on main | State on this branch |
|---|---|---|---|
| `servers/fastapi/services/composer/` | 37 (8,746 lines) | `5289aae8d` | unchanged since merge-base; only prod importer is a **lazy** import at `api/v1/ppt/endpoints/presentation_generate.py:817` under `COMPOSER_RESTYLE_ENABLED` (registry default `false`) |
| `servers/fastapi/scripts/composer_*` | 14 + `composer_offline_eval_targets.json` | `5289aae8d` | top-level imports only |
| `servers/fastapi/tests/unit/test_composer_*` | 13 | `5289aae8d` | collect cleanly today; `test_composer_manifest_enrichment.py` is NOT one of them (it tests `source_content`; main renamed it) |
| Composer seams | `presentation_generate.py:816-819`, `utils/get_env.py:315-323`, `models/generate_presentation_request.py:102`, `services/source_content/pipeline.py:145-166`, `constants/settings_registry.json:916-923` | `5289aae8d` | three of these files are modified-uncommitted by other sessions; these hunks are the only expected merge conflicts |
| `electron/` | 121 files | `784fe0719` | load-bearing in four workflows here (see below) |
| `servers/fastapi/packaged_runtime_entrypoint.py`, `server.spec`, `tests/unit/test_packaged_runtime_entrypoint.py`, `tests/integration/test_electron_runtime_settings_refresh.py` | 4 | `784fe0719` | `test-all.yml` still runs `PyInstaller server.spec` |
| `.github/workflows/sync-releaes-to-r2.yml`, `docs/electron-release-dependency-audit.md` | 2 | `784fe0719` | — |

What must change together when `main` is merged (otherwise CI breaks):

- `.github/workflows/authored-renderer-security.yml` — `npm ci --prefix electron`, `electron/scripts/authored-renderer-runtime-probe.cjs`
- `.github/workflows/cross-platform-packaging-security.yml` — path filter `electron/**`, `build:electron`
- `.github/workflows/g4-pptx-roundtrip.yml` — ~20 `working-directory: electron` steps
- `.github/workflows/upstream-compatibility.yml` — path filters on `electron/app/main.ts` and others
- `.github/workflows/test-all.yml` — `PyInstaller server.spec`
- `compatibility/protected-local-patches.json`, `upstream-byte-parity.json`, `selective-integration-ledger-20260726/20260820.json`, `upstream-intake-policy.json`, `windows-packaging-*-dist-receipt-20260821.json`
- Deleting `services/composer/` without the three seam hunks leaves a lazy `ImportError` at request time whenever the knob is set (no boot-time failure).

## 3. DEAD files (zero importers, zero spawn/string references)

`Lazy` = whether any importer loads the module inside a function body (deleting such a module
kills a running worker on first call rather than at boot). `Coupling` = lists that name the path
and must be regenerated or edited: BP = `compatibility/upstream-byte-parity.json`
(regenerate with `node scripts/verify-upstream-byte-parity.mjs --write`), RUFF =
`servers/fastapi/pyproject.toml` per-file-ignores, RATCHET = `scripts/file-size-ratchet-baseline.json`.

### 3.1 FastAPI

| Path | Lines | Evidence | Lazy | Coupling |
|---|---|---|---|---|
| `utils/internal_http.py` | 16 | 0 callers; superseded by `api/v1/auth/internal.py:authenticated_internal_request_headers` (4 live callers). `utils/simple_auth.get_internal_auth_headers` / `get_configured_auth_username` then lose their only external caller | no | BP |
| `utils/ppt_utils.py` | 82 | 0 importers; only a docstring mention in `services/composer/pipeline_inputs.py:99` and a ruff ignore | no | RUFF:392 |
| `services/authored_vnext/semantic/editable_hybrid_libreoffice_readback.py` | 275 | only `tests/unit/test_editable_hybrid_libreoffice_readback.py` | no | BP, RUFF:173,232 |
| `tests/support/source_locked_live_http_e2e.py` | 456 | argparse CLI; 0 refs in tests/scripts/docs/workflows/package.json; no `.ps1` wrapper; added `249f4148d` | — | none |
| `tests/support/source_locked_startup_recovery_live.py` | 251 | same pattern, same commit | — | none |
| `scripts/authored_admission_fixture_replay.py` | — | 0 refs (sibling `authored_admission_shadow_report.py` is loaded lazily by `test_authored_admission_integration.py:358`) | — | none |

### 3.2 Next.js

| Path | Lines | Evidence | Lazy | Coupling |
|---|---|---|---|---|
| `app/api/has-required-key/route.ts` | 22 | 0 references to `/api/has-required-key` in nextjs, fastapi, electron, scripts, docs | no | none |
| `app/(presentation-generator)/(dashboard)/Components/DashboardNav.tsx` | 56 | 0 importers; dashboard chrome is `dashboard/components/Header.tsx` | no | BP |
| `app/(presentation-generator)/hooks/useFontLoader.tsx` | 15 | 0 importers | no | BP |
| `app/(presentation-generator)/presentation/components/ThemeSelector.tsx` | 91 | 0 importers; theme editing moved to `(dashboard)/theme/` | no | BP |
| `app/(presentation-generator)/upload/components/LanguageSelector.tsx` | 68 | 0 importers; `UploadPage.tsx` does not import it | no | BP |
| `app/(presentation-generator)/upload/components/TemplateCatalog.tsx` | 41 | 0 importers; superseded by `TemplateSelection.tsx` (same `AuthoredStylePicker`) | no | BP |
| `app/(presentation-generator)/upload/components/ThemeGallery.tsx` | 138 | 0 importers; `UploadPage` uses `UploadDesignGallery` | no | BP |
| `components/ui/radio-group.tsx` | 46 | 0 importers; sole user of `@radix-ui/react-radio-group` | no | none |
| `components/ui/scroll-area.tsx` | 48 | 0 importers; sole user of `@radix-ui/react-scroll-area` | no | none |
| `components/slide-editor/model/template-v2-group.ts` | 238 | 0 importers; all four exports unreferenced; added with `KeyboardShortcutsDialog` in `0fee92b50` but never wired | no | none |
| `lib/template-v2-ast/json.ts` | 44 | 0 importers; barrel re-exports only `compiler.ts`; 5 exports 0 refs | no | BP |
| `lib/template-v2-ast/error.ts` | 15 | 0 importers; `compiler.ts` throws its own errors | no | BP |
| `lib/template-v2-general-renderer.d.ts` | 10 | every importer uses the `.mjs` specifier, which resolves `.d.mts` (sibling `template-v2-render-plan.d.mts` exists for exactly that reason) | no | BP |
| `public/figma/outline-check.svg` | asset | 0 refs | — | none |
| `public/logo-white.png` | asset | 0 refs | — | none |
| `public/vendor/tailwindcss-browser-4.3.3.js` + `-LICENSE` | asset | `components/runtime/TailwindCdnRuntime.tsx` loads `cdn.tailwindcss.com`; sibling `chart-4.5.1` and `datalabels` files ARE loaded by `lib/chart-browser.ts` | — | none |

Not dead despite looking so (verified): `lib/authored-hybrid/mode.ts` is imported with an explicit
`.ts` extension by `lib/export-presentation-route.ts:3` (value) and `lib/presentation-export-strategy.ts:2`
(type only). The module is loaded, but its only value caller `resolveRequestedPptxMode` runs inside
`executeExportPresentationRouteRequest` (`export-presentation-route.ts:320`), whose only caller is
`app/api/export-presentation/route.test.ts` — the live `app/api/export-presentation/route.ts` forwards
to the FastAPI workspace command instead. So `mode.ts` is imported-but-never-executed in production;
it belongs with the dead in-process export route function in §6, not with the DEAD files.
`lib/ooxml-vnext/generated/ooxml-package.ts` (generated by `contracts/authored_vnext/ooxml/generate.py`,
checked by `tests/unit/test_authored_vnext_ooxml_contract.py`); `utils/authored_renderer_worker.py`
(spawned as `-m utils.authored_renderer_worker` from `utils/renderer_worker_policy.py:103`);
`scripts/authored_infographic_fit_boxes.py` (lazy import inside `if args.mode == "boxes"` in
`authored_infographic_fit_sweep.py`); `scripts/warm_fastembed_cache.py` (`Dockerfile:29`, `Dockerfile.dev:74`);
`services/public_quota_reconciliation.py` (loaded dynamically by alembic revision `c6e8a0f2b4d7`).

Methodological trap recorded twice this audit: `app/api/internal/ooxml-vnext/*` and
`lib/export-presentation-route.ts` import with explicit `.ts` extensions. An importer regex that
requires the basename to be followed by a quote produces false DEAD claims.

## 4. RETIRED-REMNANT on this branch (not covered by the main merge)

| Path | Lines | Evidence | Lazy | Coupling | Recommendation |
|---|---|---|---|---|---|
| `services/template_v2_generation_service.py` | 482 | its six functions have 0 non-test callers; only `TemplateV2GenerationTarget` (defined in the contracts module) is imported, by the dead `build_slide_inputs`. This is the retired template_v2 "private execution path" | no | BP | delete with contracts + `tests/unit/test_template_v2_generation_entrypoint.py`, `test_template_v2_native_generation_gate.py`. Keep `template_v2_native_storage.py` (live). Open question: existing v2 decks have no native-export route today |
| `services/template_v2_generation_contracts.py` | 363 | sole importer above | no | BP | delete with above |
| `utils/llm_calls/generate_slide_content.py` | 267 | only prod importer `template_v2_generation_service.py:69` + 2 tests; named in CLAUDE.md's pipeline trace as the non-authored content step | no | none | delete with above |
| `utils/llm_calls/generate_presentation_structure.py` | 184 | 0 importers; the retired "pick a layout per slide" step | no | RUFF:386, BP | delete with `utils/get_dynamic_models.get_presentation_structure_model_with_n_slides` (9 lines; other caller `test_small_surfaces_coverage.py`) |
| `utils/layout_capacity.py` | 335 | 0 importers; refs only in `docs/adaptive-*.md`, `docs/content-first-design.md`; the `authored_quality_receipt_v2.py` hit is the string key `"layout_capacity"` | no | BP | delete |
| `services/presentation_export_route_inputs.py`: `resolve_template_identity` L39-69, `build_slide_inputs` L108-137, `hash_export_artifact` L29-37 | 70 | 0 refs | — | — | delete; severs the type import that keeps the v2 generation service "reachable" |
| `services/community_presentations.py`: `load_community_references` L149-182, `build_community_design_context` L195-239, `merge_reference_fonts` L185-192 | 87 | 0 refs; endpoint `community.py` uses only `list_`/`get_community_presentation`; upstream outline-prompt feature | — | BP | delete the three |
| `services/authored_quality_review_service.py`: `_run_quality_review` L279-315, `_public_review_error_detail` L270-277 | 45 | 0 refs; `queue_authored_quality_review` delegates to `admit_workspace_authored_task`; unused `background_tasks` L321 | — | RUFF | delete; keep `run_authored_quality_review` (used by `workspace_quality_operation_handler.py:37,118`) |
| `services/chat/service.py::PresentationChatService.stream_reply` | 146 (L199-344) | 0 callers; the legacy `endpoints/chat.py` is gone; only consumer of the class is `workspace_chat_operation_handler.py:109` (run API) | — | none | delete with `ChatStreamEventType` plumbing |
| `services/chat/sql_chat_history.py::list_conversations` L187-216; `conversation_store.append_turn` L73-121; `sql_chat_history.append_turn` L146-184, `replace_messages` L100-143 | 162 | 0 prod refs; only `tests/test_chat_conversation_store.py` | — | none | delete with the test |
| `app/(presentation-generator)/(dashboard)/community/components/{CommunityPage,CommunityDesignPreviewDialog,CommunityPreviewPrimitives}.tsx` | 777 | `community/page.tsx` is `redirect("/dashboard")` and imports none of them; commit `212405580` "hide the community gallery this fork cannot serve" | no | BP | product decision: delete the subtree including the redirect stub, or keep dormant |
| Stale references to the deleted `utils/export_utils.py` (removed in `1ae1228c4`, an ancestor of HEAD) | — | `compatibility/upstream-byte-parity.json:2276`; `services/export_task_contracts.py:42` (an unreachable `exportSession=` redaction regex); `services/presentation_generation_export_chain.py:5` (comment); 4 docs | — | BP | clean the ledger entry and the regex |
| `services/presentation_export_adapters.py` (365), `services/presentation_export_router.py` (567) | — | `git status` shows `AD`: staged add, deleted on disk, absent from HEAD. Another session's export refactor is mid-flight | — | — | IN-EDIT: do not commit the staged add; confirm with that session |

## 5. TEST-ONLY production modules

| Path | Lines | Importers | Recommendation |
|---|---|---|---|
| `services/export_task_service.py` + `export_task_operations.py` | 59 + 80 | 4 unit tests; prod importer was the deleted `utils/export_utils.py`; `pptx_import_conversions.py:37-40` says it was split "so that the direct export attempt path (`export_from_url`) can be deleted later". CLAUDE.md line 138 still names `export_task_service.py` as the export renderer | IN-EDIT (export refactor session). Delete; repoint the 4 tests at `PptxImportConversionsService`; rehome `_move_export_to_owner`; fix CLAUDE.md |
| `services/authored_export_quality_flag.py` | 131 | own test only; consumes `get_env.get_authored_export_fallback_limit`; docstring says "reporting only" | wire into the hybrid export receipt or delete module + test + getter |
| `services/trusted_executor/chrome_runtime.py` | 697 | re-exported by the package `__init__` (loaded at boot) but the three exported names have no user outside the package except their own test; `PRODUCT_PATH_SEAM_HANDOFF.md` calls it a future seam | wire into the isolated render worker or delete module + re-export + test + RATCHET entry |
| `services/trusted_executor/launcher.py` | 116 | tests only; never wired into `composition.py` | same decision as above |
| `services/compatibility_migration/rollout.py` | 369 | `scripts/authored_only_canary.py:18` + test | HELD (canary runbook) |
| `services/authored_vnext/master/repository.py::InMemoryAuthoredVNextMasterRepository` | 135 | 4 tests | move to `tests/support/` |
| `templates/v2/local_assets.py` | 578 | `tests/unit/test_template_v2_local_assets.py` only | template-v2 hold area; confirm with v2 owner |
| `templates/v2/pptx/vision_adapter.py` | 337 | `tests/unit/test_template_v2_pptx_vision_adapter.py` only | same |
| `utils/{artifact_style_analysis,artifact_style_builder,artifact_style_pdf,artifact_style_pptx,authored_style_conversion_io,authored_style_converter}.py` | 2,480 | `scripts/build-authored-style.py`, `scripts/convert-authored-style.py`, 2 tests | SCRIPT-ONLY ops tooling; keep |
| TEST-ONLY symbols: `provider_usage_receipt_service.aggregate_provider_usage_for_job` L224-298 (75), `authored_title_recovery_contract.safe_title_recovery_receipt` L425-462 (38), `conversation_event_contract.recover_assistant_checkpoints` L241-271 (31) | 144 | tests only | delete or wire |
| `lib/authored-hybrid/native-image-decoration.ts` | 176 | own test; header says "NOT YET WIRED IN"; env `AUTHORED_HYBRID_NATIVE_IMAGE_DECORATION` read nowhere else and not in the registry | wire (schema/contract need radius/border/shadow on image payloads) or delete |
| `lib/template-v2-ai-text-rewrite.ts` | 710 | own test; a diverged twin of the live `lib/template-v2-ai-rewrite.ts` (816); 20 of 21 exports re-declared there | delete after porting the length-code test cases |
| `lib/template-v2-design-variables-contract.ts` | 622 | 4 tests replaying upstream fixtures; 9 exports 0 prod refs | keep as compatibility oracle under a test dir, or delete with 4 tests + 3 fixtures |
| `lib/template-v2-structural-edit.ts` | 543 | roundtrip test only | same decision |
| `app/(presentation-generator)/(dashboard)/Components/DashboardSidebar.tsx` | 128 | only `components/OnBoarding/ResponsiveNavigation.cy.tsx`; the other referrer was the dead `DashboardNav.tsx` | unfinished dashboard-nav redesign or dead work; decide with the owner |
| `tests/support/workspace_browser_live_harness.py` | 534 | DOC-ONLY: 0 code importers, named in 4 chat-first docs as a manual harness | keep |
| `lib/ooxml-vnext/cli.mjs` | 87 | spawned only by two FastAPI integration tests; prod uses `product-runner.mjs` | keep; document in `lib/ooxml-vnext/README.md` |

Other DOC-ONLY / JUNK / UNCERTAIN:

- DOC-ONLY: `services/trusted_executor/PRODUCT_PATH_SEAM_HANDOFF.md` (move to `docs/`); four `tsconfig.check.json` files (`lib/authored-vnext/{flow,master,workbench}/`, `app/(presentation-generator)/authored-vnext/`) referenced only by a README (add an npm script or delete); `lib/ooxml-vnext/fixtures/evidence/existing-90.office-matrix.json`, `existing-90.build-receipt.json` (release receipts, referenced only by docs).
- JUNK: `services/template_v2_pptx_retention_service.py` starts with a UTF-8 BOM (only flat module with one; `ast.parse` of raw text fails); `lib/ooxml-vnext/fixtures/evidence/existing-90-powerpoint-repair-scope.json` referenced by nothing but the byte-parity ledger. No tracked logs, `.db`, stray html, or tsbuildinfo (the ones on disk are untracked). `pnpm-lock.yaml` is not tracked.
- UNCERTAIN: `lib/run-bundled-presentation-export.e2e.test.mjs` is excluded by `run-ci-node-tests.mjs` ("dedicated flow") but no script, workflow, or `test-local.mjs` profile runs it; `scripts/verify-local-stack-knobs.ps1` has no caller from the stack supervisor; `utils/renderer_isolation.encode_request` (newline-delimited) vs `utils/renderer_frame_codec.encode_framed_request` (framed) look like two renderer IPC generations, trace under `CHROME_REUSE_ENABLED`; which of `master/file_repository.py` (bound by the master router) and `integration/sql_repository_composition.py` is the product master repository.

## 6. DEAD-SYMBOL (unreferenced functions ≥15 lines, incl. tests)

Pydantic validators and `HTMLParser.handle_*` callbacks were checked and excluded as framework-invoked.

| Path | Lines | Note |
|---|---|---|
| `services/authored_vnext/generation/retained_reference.py::_layout_library` L429-751, `_source_layout_library` L1102-1279 | 501 | in a 2,184-line RATCHET file; deleting shrinks the exception ~23% |
| `services/presentation_generation_job_service.py::get_workspace_linked_generation_queue_position` L274-295 + `_workspace_linked_generation_queue_position` L200-271 | 95 | `public_operation_admission.py:2622` reports `queuePosition` as `queue_position_not_persisted` — consciously unwired |
| `services/presentation_generation_job_service.py::resolve_generation_job_presentation_id` L1091-1116 | 26 | |
| `services/authored_vnext/integration/sql_repository_composition.py::record_quality_decision` L434-495 | 62 | not in `sql_repository_types.py` |
| `services/authored_vnext/integration/sql_repository_publication.py::rollback_master_publication` L260-309 | 50 | sibling `rollback_publication` is used |
| `services/authored_vnext/source_native_generation.py::_replace_frame_text` L3361-3405 | 45 | RATCHET (4,170 lines) |
| `services/authored_presentation_generation.py::_apply_bounded_layout_alignment_repairs` L784-816, `_apply_bounded_closing_title_reducer` L761-781 | 54 | RATCHET (3,449 lines) |
| `services/authored_vnext/generation/effective_style.py::_role_policy` L315-345 | 31 | |
| `services/authored_author_capacity_recovery.py::preauthor_title_recovery_contract_prompt` L241-270 | 30 | also a 4th copy of the tag-strip regex |
| `services/source_content/manifest_store.py::mark_coverage_receipts_stale` L435-460 | 26 | delete or wire into coverage invalidation |
| `services/assets/gateway.py::AssetGateway.bind_many` L190-214 | 25 | |
| `services/authored_vnext/composition_capacity.py::_spatial_components` L356-378 | 23 | |
| `services/slidespec_authored_executor.py::_off_canvas_fraction` L830-848 | 19 | byte-identical twin in `slidespec_repair_context/numeric.py:9-25` is the one used |
| `services/authored_vnext/integration/sql_repository_jobs.py::heartbeat_export_job` L278-295 | 18 | |
| `store/slices/presentationGeneration.ts`: `setSlidesRendered`, `updateSlideHtmlContent`, `updateSlideChart`, `updateSlideContent`, `updateSlideImage`, `updateImageProperties`, `updateSlideIcon` (L86-385) | ~185 | exported via `slice.actions`, never dispatched; five mutate the retired `slide.content` shape; `setLayoutLoading` prod 0 / test 1; `undoRedoSlice.finishUndoRedo` prod 0 |
| `utils/providerUtils.ts`: `updateLLMConfig` L27-106, `changeProvider` L111-127 | 97 | |
| `utils/presentationLimits.ts`: `MAX_OUTLINE_CONTENT_WORDS`, `countOutlineWords`, `trimTextToWordLimit`, `limitOutlines`, `parseLimitedSlideCount` | 36 | retired outline→template flow; keep `MAX_NUMBER_OF_SLIDES`, `clampSlideCountValue` |
| `utils/mixpanel.ts`: `getDistinctId`, `identifyAnonymous`, `resetTelemetryCache`, default export `mixpanelUtils` | 40 | `track`/`initMixpanel` etc. are live (24 importers) |
| `utils/storeHelpers.ts`: `isCodexMissingSelectedModel`, `shouldBlockCodexOutboundNav`, `syncStoreAfterCodexSignOut` L276-309 | 27 | confirm with settings owner |
| `utils/api.ts`: `getBackendAssetSource` L254-266, `resolveBackendAssetSource` L268-272, `BackendAssetLike` | 20 | also un-export `getFastAPIUrl` |
| `utils/analytics.ts`: `bucketFileSize`, `bucketMessageLength` | 22 | |
| `lib/authored-hybrid/renderer-isolation.ts::authoredRendererAttestationProjection` L122-158 | 37 | |
| `lib/export-presentation-route.ts::executeExportPresentationRouteRequest` (+ `lib/authored-hybrid/mode.ts::resolveRequestedPptxMode`, `resolvePptxMode`) | ~60 + 24 | only caller is `app/api/export-presentation/route.test.ts`; the live route forwards to the FastAPI workspace command. The rest of `export-presentation-route.ts` (`fetchPersistedPresentationProjectionForExport`) is live via `pdf-maker/page.tsx:55` and `export-presentation-data/[id]/route.ts:53`, which also keeps `authored-hybrid/security.ts` / `security-data.ts::readBoundedResponseText` LIVE regardless of the hybrid knob |
| `lib/authored-vnext/master/fixtures.ts::makeSlideSpec` L301-319 | 19 | 0 callers; near-twin of `view-model.ts::createSlideSpec` |
| `types/global.d.ts`: `ShapeProps`, `TextFrameProps` | 14 | |

Over-exported but used in-file (drop `export` only): `lib/authored-vnext/master/client.ts` `normalize*` ×7, `lib/chart-browser.ts` `createPresentonDataLabelPlugin`/`registerPresentonDataLabelPlugin`, `presentationGenUploadSlice`, `lib/ooxml-vnext/security.ts::resolveOoxmlSecurityPolicy`, `lib/template-v2-studio.ts::updateTemplateV2Element`, `lib/authored-vnext/workbench/reducer.ts::operationPatch`/`applyPatch`.

## 7. Behavioural forks (duplicates that already disagree)

These are the highest-value duplicate findings: two copies of one concern that now return
different results for the same input.

| # | Concern | Copies | Difference |
|---|---|---|---|
| F1 | `_resolve_workspace_export_options` | `services/workspace_quality_export_operation_handlers.py:101-127` vs `workspace_quality_operation_handler.py:997-1018` | `font_embedding` defaults to `None` (CJK default applied later) in one, `False` in the other — a wire field with two meanings |
| F2 | Master-background `styleDigest` | `services/authored_vnext/integration/export_service.py:111-139` vs `ooxml/native_scene.py:216-229` | one uses `prefixed_sha256`, the other `canonical_digest` — the same furniture gets two digests |
| F3 | Canonical JSON digest | ~45 files (`canonical_json` ×8, `_canonical_json` ×12, `canonical_digest` ×11, `_canonical_digest` ×12, `_canonical_value` ×4) | only `services/authored_vnext/semantic/canonical.py` implements the versioned NFC form; `allow_nan`, `ensure_ascii`, `default=str`, `"sha256:"` prefix vary per copy; content-addressed stores can disagree across modules |
| F4 | WCAG relative luminance | `utils/color_contrast.py:75-93` (survivor) and 6 copies | sRGB threshold `0.03928` vs `0.04045` coexist |
| F5 | Published-master lookup | `authored_vnext/generation/sql_repository.py:190-211 get_master_head` vs `integration/sql_repository_composition.py:86-108 get_published_master` | only the integration copy filters `status == "published"` |
| F6 | PPTX `rId` allocation | `lib/authored-hybrid/pptx-archive.ts:174-186`, `pptx-package-parts.ts:80-100`, `pptx-font-embedding.ts:136-150`, `lib/ooxml-vnext/package-ops.ts` | duplicate-Id throw exists in one copy; malformed ids throw or skip depending on which ran last |
| F7 | `xmlUnescape` | `lib/ooxml-vnext/master-inventory-xml.ts:21`, `master-readback.ts:34`, `package-graph.ts:54`, `package-ops.ts:46` | 27-line vs 8-line copies differ on numeric-entity decoding inside one OOXML pipeline |
| F8 | CSS `font-family` list parser (browser-injected) | `lib/authored-hybrid/browser-source.ts:98` (naive `split(",")`) vs `local-fit-browser.ts:92` (quote/escape-aware) | the naive one feeds font embedding and coverage |
| F9 | `_number` finite-float coercion | `services/slidespec_repair.py:85-92` (default `0.0`) vs `slidespec_geometry_candidates.py:124-131` ≡ `slidespec_coupled_repair.py:83-90` (default `nan`) | same input yields different candidates across repair stages |
| F10 | Windows hidden-subprocess kwargs | `services/document_conversion_service.py:66-76`, `templates/pptx_convert.py:28-36` (with `STARTUPINFO`) vs `liteparse_service.py:508-511`, `export_task_contracts.py:45-48` (only `CREATE_NO_WINDOW`) | console-window behaviour differs on this fork's primary platform |
| F11 | Provider summary strings | `settings/setting-page/providerSummaries.ts:1-49` vs `upload/components/CurrentConfig.tsx:6-57` | "이미지 생성 비활성화됨" vs "이미지 생성 사용 안 함" for the same state; also `useSettingsPageState.ts:78-106` vs `useUploadPageState.ts:318-347` with different Korean error copy |
| F12 | Glyph-width heuristic | `authored_vnext/body_grammar.py:161-181` ≡ `master/preview.py:617-638` vs `generation/title_fit.py:214-228` ≡ `master/title_fit.py:39-53` | two different glyph tables (0.32/0.58/0.9 vs 0.42/0.56/0.68), each copied twice |
| F13 | Chrome executable discovery | `lib/authored-hybrid/chrome-runtime-discovery.ts:118-168` vs `lib/run-bundled-presentation-export.ts:76-125` (protected) + `scripts/lib/template-v2-pdf-golden-runtime.mjs` | candidate tables differ by env-var order |
| F14 | Source-locked readback projection | `services/source_locked/conversion.py:48-69` vs `restoration.py:139-156` | restoration hard-codes `byte_exact_source_package=False, fallback_allowed=False` |

## 8. DUPLICATE families

Survivor = the recommended module to keep. Line counts are all copies combined.

### 8.1 Python

| Concern | Copies | Lines | Survivor |
|---|---|---|---|
| Positive/bounded int from env | 19 (`author_slide.py:125`, `author_vision_qa.py:137`, `authored_illustrations.py:150`, `slide_capture.py:1939` one hash; `liteparse_service.py:48`; `db_utils.py:86`; `workspace_operation_outbox_dispatcher.py:93` ≡ `workspace_operation_runner.py:451`; `workspace_event_retention.py:72`; `decompose_job_service.py:52`; `export_retention_service.py:24`; `workspace_asset_maintenance.py:52` ≡ `workspace_artifact_runtime_config.py:6`; `authored_checkpoint_sweep_budget.py:27`; `artifact_lifecycle_runtime.py:22`; `public_workstation_runtime_profile.py:268`; `provider_durable_admission.py:54` ≡ `provider_runtime_job_registration.py:49`; `get_env.py:352` ≡ `public_generation_deadline_env.py:21`) | ~170 | `utils/get_env.py` — one `env_int(name, default, minimum, maximum, on_invalid)`; four failure policies (default / clamp / warn / raise) currently coexist |
| Canonical JSON / digest (F3) | ~45 files | ~400 | `services/authored_vnext/semantic/canonical.py`; re-export from `utils/slidespec_core.py`, `services/authored_admission_context.py`; check persisted digests before switching |
| sha256 of file (1 MiB chunks) | 12 (`assets/gateway.py:91`, `authored_review_asset_store.py:24`, `presentation_export_attempt_service.py:163`, `presentation_export_direct_attempt_service.py:91`, `presentation_export_powerpoint_qa.py:96`, `workspace_cleanup_storage.py:107` public, `editable_hybrid_libreoffice_readback.py:36`, scripts ×5) + 13 bytes twins | ~72 | `workspace_cleanup_storage.sha256_file` or `utils/hashing.py` |
| `as_utc` | 14 (`utils/datetime_utils.py:8` survivor; `workspace_service.py:254`, `provider_durable_runtime.py:38`, six `semantic/*_v3.py`, `public_operation_deadline.py:29`, `public_operation_deadline_scope.py:60`, `workspace_api_contract.py:23`, `presentation_share.py:73`, `generation/sql_repository.py:68`) | ~55 | `utils.datetime_utils.as_utc` (two copies stamp tzinfo without `astimezone`) |
| `_number` finite-float / `_mapping` coercion | 25 + 18 files | ~250 | `utils/coerce.py` (`finite_float`, `as_mapping`); also `utils/slidespec_core.py` for the slidespec family (F9) |
| Verbatim-capacity slot-layout search | `authored_vnext/source_native_generation.py` ↔ `source_verbatim_capacity.py` (`_balanced_contiguous_ends` 1358-1387 ↔ 741-772 identical; `visual_layout`, `visual_row_layout`, `evaluate`, `_render_unit`, `overlay_layout`, `table_height`) | ~300 | B was extracted from A in `53fd1ae33` but A kept its copies; parameterise the height estimator in `source_verbatim_capacity.py` |
| PPTX package readers (`.rels` → slide parts in `sldId` order) | 6 (`source_content/pptx.py:156-189`, `source_locked/pptx.py:47-156`, `source_images/extract.py:138-205`, `authored_vnext/master/ai_import.py:102-239`, `design_pack/compiler.py:130-150`, `authored_local_fit_export.py:35-50`; `templates/v2/pptx/native_clone.py:214-231` lacks the expansion-bomb guard) | ~250 | one `utils/pptx_package.py` based on `source_content/pptx.py` (secure_xml); `extract.py` and `ai_import.py` even duplicate the limit constants (50_000_000 / 300_000_000 / 20_000) |
| Master repository protocol implementations | 4 (`generation/repository.py` in-memory, `generation/sql_repository.py` 756, `integration/sql_repository_composition.py` 569, `master/file_repository.py` 330, `master/repository.py` in-memory) | ~2,000 | decide the product path (UNCERTAIN §5); move in-memory doubles to `tests/support/` |
| SQLite lock retry decorator | `generation/sql_repository.py:78-106` ↔ `integration/sql_repository.py:43-71` | 60 | `integration/sql_repository_types.py` |
| Mem0 wrapper | `services/mem0_presentation_memory_service.py` (275) ↔ `services/chat/chat_memory_store.py` (412): `_get_client`, `_disable_runtime`, `_add`, `retrieve_context` | ~150 | one `Mem0MemoryStore(scope_fn)` base; both live |
| Background maintenance loop lifecycle | 4 (`workspace_asset_maintenance.py:253-320`, `workspace_cleanup_journal_service.py:1520-1585`, `export_retention_service.py:170-215`, `template_v2_pptx_retention_service.py:365-408`) | ~170 | `services/periodic_maintenance.PeriodicMaintenance`; keep the four public `start_/stop_` names |
| Events endpoints (cursor / `Last-Event-ID` / SSE headers) | `endpoints/conversations.py:279-331` ↔ `workspace.py:685-744` (0.951); `:127-142` ↔ `:593-612` | ~150 | shared `_serve_event_page_or_stream` |
| Master background + furniture colour (F2) | `integration/export_service.py:106-195` ↔ `ooxml/native_scene.py:208-406` | ~180 | `ooxml/master_furniture.py` |
| WCAG luminance/contrast (F4) | 7 | ~95 | `utils/color_contrast.py` |
| Glyph width / wrapped-line / marker parser (F12) | `body_grammar.py:161,432`, `generation/title_fit.py:85,214`, `master/preview.py:617`, `master/title_fit.py:39` | ~110 | `authored_vnext/text_metrics.py` (touches IN-EDIT `master/service.py`) |
| Placeholder owner assignment | `source_content/placeholder/substitute.py:93-131 _owners` ↔ `substitute_legacy.py:143-185 _owners_raw` ("Port of substitute._owners", both live) | 82 | parameterise `_owners` over an adapter |
| `normalize_font_family_name` | `endpoints/pptx_slides.py:115-151` ↔ `templates/pptx_font_utils.py:141-169` (outputs differ on camel-case split) | 66 | `templates/pptx_font_utils.py` |
| CSS-strip scanning walkers | `utils/authored_css_strip.py` (regex L101-109; walkers L310-345+) ↔ `utils/authored_html_normalize.py` (L94-103; L207-257) — byte-identical regex constants, copy-pasted walkers although normalize already imports css_strip's public API (L83) | ~120 | extract a parameterised walker into `authored_css_strip.py` |
| HTML scan parsers | `utils/authored_header_repair.py:95-190 _PartScanParser` ↔ `utils/authored_html_normalize.py:289-380 _HiddenScanParser` (same `_OpenFrame`/`_offset`/`handle_*` scaffolding) | ~190 | shared base parser |
| SELECT FOR UPDATE on `PublicOperationAdmission` in `begin_nested` | `authored_checkpoint_fence.py:80-92` (public), `authored_checkpoint_publish_fence.py:39-53`, `workspace_operation_runner.py:998-1012` | 40 | `authored_checkpoint_fence` (IN-EDIT neighbourhood) |
| Deadline-policy scaffolding | `public_export_deadline_policy.py:58-92` ↔ `public_generation_deadline_policy.py:127-169,337-343` (7 structurally identical accessors) + `utils/public_generation_deadline_env.py:21` | ~80 | parametrise |
| Process-tree kill | `utils/slide_capture_worker_client.py:56-69` ↔ `slide_capture_worker_session.py:65-80` | 30 | client imports session |
| Exception cause-chain walkers | `authored_author_stage_deferral.py:194`, `provider_idempotency.py:88,106`, `author_slide_fallback.py:41` | ~55 | `utils/exception_chain.find_in_cause_chain` |
| Persisted-plan loaders (`theme.get(key)` → `model_validate` → warn) | `authored_deck_chapter_plan.py:225`, `authored_deck_illustration_plan.py:20`, `authored_deck_infographic_plan.py:109`, `authored_header_contract_service.py:104` | 48 | `load_theme_model(theme, key, model, label)` |
| Windows hidden-subprocess kwargs (F10) | 4 | ~30 | `export_task_contracts.windows_hidden_subprocess_kwargs` + `STARTUPINFO` |
| Hand-rolled owner predicates | `presentation_share.py:162`, `assets/compatibility.py:37`, `compatibility_readiness.py:100`, `references/service.py:101` | ~20 | violates "do not hand-roll owner filters" (`services/database.py:109` hook); delete or one `owner_predicate` next to the hook with a coverage test |
| `_positive_environment_integer` | `source_locked/lifecycle.py:20-38` ↔ `maintenance.py:24-34` | 30 | one |
| Source-locked SQLite store skeleton | `source_locked/generation_store.py:44-132` ↔ `job_store.py:62-150` | ~90 | `SourceLockedSqliteStore` base |
| `_mapping` coercers | `source_content/coverage.py:107`, `export_readback.py:38`, `readback.py:63` | 24 | helper in `source_content/contracts.py` |
| `_load_artifact_bytes` | `semantic/lifecycle.py:443-458` ↔ `semantic/remake.py:125-140` | 32 | `semantic/canonical.py` |
| `_atomic_write_bytes` / `_fsync_directory` | `authored_icon_pack/store.py:201` ↔ `authored_illustration_reviews.py:43`; `authored_review_asset_store.py:32-46` ↔ `workspace_artifact_publication_service.py:196-208` | ~60 | `utils/atomic_write.py` |
| Exact small pairs | worker `main()/serve()` (`template_v2_pptx_worker.py:86-109` ↔ `workspace_operation_worker.py:281-304`); `_safe_failure` (`presentation_generation_attempt_timing.py:44` ↔ `public_generation_worker_lifecycle.py:131`); `_linked_generation_job_id` (`workspace_operation_runner.py:856` ↔ `workspace_service.py:563`); PBKDF2 verify + `_base64url_decode` (`api/v1/auth/config.py:48-66` ↔ `utils/simple_auth.py:88-102`); `_confined_below` (`templates/packages/evidence_validation.py:362` ↔ `validator.py:279`); `_resolve_prompt_language` ×4 (`llm_calls/{edit_slide,generate_content_brief,generate_presentation_outlines,generate_slide_content}.py`); `_read`/`_plain` (`generation/montage.py:14` ↔ `quality.py:82`, `_plain` also `deck_ir/quality.py:114`, `slide_quality_acceptance.py:67`); bbox `_edges` (`authored_dom_footer_zone.py:63` ↔ `authored_dom_text_escape.py:76`, superset in `authored_dom_fit_magnitude.py`); worker signing-key loader (`public_export_operation.py:200-214` ↔ `workers/public_worker_runtime.py:85-97`); `json_value` (`authored_admission_context.py:28-51` public ↔ `authored_evidence_ledger.py:61-66`); `_wait_for_wake` / `dispatch_once` / `_run_loop` (`workspace_operation_outbox_dispatcher.py` ↔ `workspace_operation_runner.py`); `wait_for_termination` (`trusted_executor/container_supervisor.py:202` ↔ `process_supervisor.py:765`); `validate_status_payload` (`templates/v2/pptx/analyzer_contract.py:81` ↔ `models.py:64`); `require_*_presentation` (`compatibility_migration/runtime.py:79` ↔ `presentation_access_service.py:19`); `_sniff_mime` (`source_images/extract.py:304` ↔ composer, goes with the merge); `Rect`/`absolute_layout_bounds` (`utils/slidespec_constraints.py:30,244` ↔ `utils/slidespec_solver.py:64,148`, `authored_deck_instruments.py:82`) | ~500 | keep the named survivor in each pair |
| UTC-now wrappers | `presentation_generation_job_service.py:298`, `template_v2_pptx_ingestion_service.py:119`, `template_v2_pptx_retention_service.py:59`, `workspace_operation_runner.py:465` | trivial | `utils.datetime_utils` |

### 8.2 TypeScript / JavaScript

| Concern | Copies | Lines | Survivor |
|---|---|---|---|
| Settings vs onboarding vs upload provider UI (F11) | `settings/ImageProvider.tsx:58-136` ↔ `components/OnBoarding/PresentonMode.tsx:227-314` (three identical `<Select>` blocks); `useSettingsPageState.ts:109-124` ↔ `PresentonMode.tsx:324-339` (exact); `useSettingsPageState.ts:78-106` ↔ `useUploadPageState.ts:318-347`; `providerSummaries.ts` ↔ `CurrentConfig.tsx` | ~300 | `providerSummaries.ts`; shared `ImageQualitySelect`, `ensureStockImageProviderReady`, `useCodexAuthStatus`; strings via `i18n/messages`; byte-parity ledger update |
| Chart data-label plugin, TS vs inlined string | `lib/chart-browser.ts:73-486` (~20 functions) ↔ `lib/template-v2-html/chart-script.ts:34-58` (13 KB minified string of the same functions; `2ff3986e0` consolidated the TS but left the string for exported HTML) | ~400 + 13 KB | generate the inline script from `chart-browser.ts` (`String(fn)` splicing as `browser-source.ts` does) |
| XML escaping | 8 (`native-chart.ts:341`, `native-plan.ts:1562`, `ooxml-vnext/master-compiler.ts:56`, `package-ops.ts:37` byte-identical; `lib/math.ts:80`; `native-table.ts:129`, `pptx-assembler.ts:290` drop `'`; `pptx-archive.ts:160`, `pptx-font-embedding.ts:126` encode CR/LF) + `xmlUnescape` ×4 (F7) | ~120 | `lib/ooxml-vnext/package-ops.ts` or `lib/xml-escape.ts` with `escapeXmlText` + `escapeXmlAttribute` |
| HTML escaping | `template-v2-html/css.ts:69,78` (exported), `template-v2-general-renderer.mjs:26`, `SafeHtmlSlidePreview.tsx:27`, `authored-hybrid/html-instrumentation.ts:85` | ~30 | `template-v2-html/css.ts` |
| `isRecord` ×43 files, `clamp` ×13-15, `numberValue` ×10, `finiteNumber` ×5, `round` ×4, `unique` ×5 | 71+ | ~330 | `lib/guards.ts` or the already-exported `importing/template-v2/values.ts` / `template-v2-preview/core.ts`; browser-injected scripts keep their copy |
| px → EMU | 7 (`native-plan.ts:40,1571,1575`, `shadow-effect.ts:34,122`, `native-image-decoration.ts:114`, `native-table.ts:130`, `pptx-assembler.ts:303`, `ooxml-vnext/materializer.ts:1007,1046`, `master-compiler.ts:44,95`) — three unlabelled rounding policies | ~40 | add `pxToEmu`, `ptToEmu`, `EMU_PER_CSS_PX`, `EMU_PER_POINT` to `lib/authored-hybrid/units.ts` |
| Colour parsing | `chart-browser.ts:113`, chart-script inline, `css-paint-browser.ts:28`, `template-v2-html/table.ts:278`, `template-v2-html/css.ts:47`; name collision `components/slide-editor/charts/chart-data.ts:215 normalizeChartColor` vs `template-v2-html/chart-colors.ts:23` (different contract) | ~90 | `lib/color.ts`; rename one `normalizeChartColor` |
| Deep clone | JSON round-trip (`blank-slide.ts:34`, `template-v2-group.ts:236`, `undoRedoSlice.ts:20`), `structuredClone` (`TemplateV2Studio.tsx:81`, `template-v2-ast/json.ts:9`), recursive (`template-v2-studio-commands.ts:226,239`, `template-v2-chart-table-contract.ts:63,111`, `template-v2-infographic-contract.ts:35`); validating clone `template-v2-design-variables-contract.ts:75-128` ↔ `template-v2-infographic-contract.ts:35-88` (0.980) + `hasExactKeys` ×4 + `isPlainRecord` ×3 | ~250 | `lib/template-v2-ast/json.ts` (`cloneJsonValue`); keep the two cycle-guarded validators |
| JSON-tree path helpers | `readPath` (`template-v2-quality-inspector.ts:510` ↔ `template-v2-slide-variants.ts:133`), `replaceAtPath` (`template-v2-studio-commands.ts:161` ↔ `template-v2-studio.ts:435`), `elementAtPath` (`TemplateV2Canvas.tsx:80` ↔ `TemplateV2ElementTree.tsx:52`), `move` (`template-v2-structural-edit.ts:150` ↔ `template-v2-table-operations.ts:277`), `deepEqual` ×3, `stableStringify`/`canonical` ×2 | ~315 | `lib/template-v2-ast/` |
| Konva viewport helpers + `StudioVector` | `components/canvas-core/CanvasCoreViewport.tsx:48-92` ↔ `lib/template-v2-konva.ts:164-207`; `TemplateV2CanvasElement.tsx:65-116` ↔ `TemplateV2CanvasPlanned.tsx:25-78` (Planned copy 0 refs) | ~190 | canvas-core; one `StudioVector` |
| Bounding-box union | `importing/template-v2/raw-frames.ts:62-78`, `model/template-v2-group.ts:190-201` (dead file), `template-v2-studio-media.ts:125-143` (exported superset) | ~48 | `unionStudioCanvasRects` |
| Canvas text measurement | `components/slide-editor/text/template-v2-text.ts:1474 measureRunText` and `:1486 measureRenderText` (fallback factors 0.58 vs `TEXT_AVERAGE_CHAR_EM`) + `TemplateV2CanvasHelpers.ts:146 measureTextRun` (new canvas per call) | ~40 | `measureRenderText` (cached canvas) |
| Next→FastAPI base URL | `lib/fastapi-internal.ts:3-13` (11 importers) ↔ `app/api/export-presentation-data/[id]/route.ts:11-23` (exact) ↔ `utils/serverAuth.ts:21-34` (dead `NODE_ENV` branch) | 40 | `lib/fastapi-internal.ts` |
| decompose-documents proxy routes | `app/api/decompose-documents/jobs/[jobId]/route.ts:17-67` ↔ `jobs/route.ts:15-70` ↔ `decompose-documents/route.ts:81-135` (same header forwarding, 502 envelope, `X-Request-Id`) | ~160 | `lib/fastapi-proxy.ts proxyToFastApi`; byte-parity update |
| Chrome executable discovery (F13) | 3 | ~120 | export the candidate table from `chrome-runtime-discovery.ts` (`run-bundled-presentation-export.ts` is protected) |
| Theme CSS-variable application | `(export)/pdf-maker/PdfMakerPage.tsx:268-312` ↔ `presentation/utils/applyPresentationThemeDom.ts:39-82` (7 importers) | 89 | `applyPresentationThemeToElement` |
| Bounded response reader | `google-fonts-fetch.ts:111-141` ↔ `security-data.ts:37-75` (exported) | 70 | export a bytes variant from `security-data.ts` |
| Browser-injected helpers | `scriptSlices` (`browser-source.ts:170-204`) ↔ `font-policy.ts:53-86 splitTextByScript`; CSS comma-list splitter ×3 (`css-paint-browser.ts:53`, `shadow-effect-browser.ts:41`, `browser-source.ts:2828`); `fontFamilies` (F8) | ~135 | `browser-source.ts` is a protected local patch: generate the browser copy from the TS source at build time, or add a golden test asserting identical output |
| SSE retry scheduler | `outline/hooks/useOutlineStreaming.ts:190-215` ↔ `presentation/hooks/usePresentationStreaming.ts:212-235` | 50 | `useEventSourceWithRetry` |
| Digest / CLI helpers in scripts | `sha256` ×21 files, `stableDigest` ×6, `canonicalJson` ×9, `atomicJson` ×4, `parseArgs` ×15 (306 lines in `scripts/`) | ~500 | `lib/ooxml-vnext/digest.ts`; `scripts/lib/cli-common.mjs` |
| Exact small pairs | `abortableFetch` (`scripts/generation-benchmark.mjs:102` ↔ `scripts/ppt-agent.mjs:216`); `formatChartValue` (`chart-browser.ts:94` ↔ `TemplateV2HtmlSlidePreview.tsx:107`); editable-target check (`slideKeyboardNavigation.ts:37` ↔ `template-v2-studio-keyboard.ts:41`); `fetchStatus` (`settings/PrivacySettings.tsx:12` ↔ `OnBoarding/FinalStep.tsx:36`); `template-v2-text-editing.ts:46` ↔ `template-v2-text.ts:679`; `general-renderer.mjs:841` ↔ `template-v2-vector.ts:519`; `makeSlideSpec` ↔ `createSlideSpec` | ~200 | import the survivors |

### 8.3 Test helpers (FastAPI)

| Helper | Occurrences | Survivor |
|---|---|---|
| `def _run(coro): return asyncio.run(coro)` | 46 files under `tests/unit`, byte-identical | one helper in `tests/conftest.py` or `tests/support/asyncio_helpers.py` |
| Alembic `Config` builder (`_alembic_config` / `_config(database_url)`) | 17 migration test files, byte-identical | `tests/support/alembic_helpers.py::build_alembic_config` |
| `_brand()` minimal `Brand(...)` fixture | 15 files, same shape | conftest `brand_factory` |
| `_pptx() -> bytes` | 4 files | `tests/support/pptx_fixtures.py` |
| `_presentation() -> PresentationModel` | 4 files (bodies not diffed) | verify first |

Detector raw output (Python AST clone hashing, TS token clone hashing, same-name families,
whole-file pairs) was produced in the session scratchpad; it is not checked in. The detector
approach is reproducible: normalise identifiers positionally, hash bodies ≥8 statements, bucket
near-clones by name and 12-token shingles, SequenceMatcher ≥0.85.

## 9. Read in production, never written in production

Precedent: `image_text_evidence` had two readers and zero writers, leaving 414 of 1,252 fixture
slides silently empty. The same shape appears in these places.

| # | Field / key | Readers | Writers | Effect |
|---|---|---|---|---|
| W1 | `GateEvidence.impacted_recall` (`services/slidespec_acceptance.py:46`) | `:343,:347` | none in production (3 test writes) | the SlideSpec acceptance OCR gate can only report `ocr.unavailable` |
| W2 | `quality_receipt_t1_triggers` per-slide flags `denseData`, `hasTable`, `hasChart`, `layoutCapacity`, `cjkFontWarning`, `nearOverlap`, `nearOverflow`, `largeRasterIsland`, `hybridBoundary` (`services/authored_quality_receipt_v2.py:1739-1767`) | live (`:2110`, `:2463`) | the only live producer `authored_quality_receipt_v3_pipeline.py:298` emits `{role, repairApplied}`; `deck_ir/product_adapter.py:462` adds `layoutCapacity` only on the opt-in deck-IR path | triggers `dense_data`, `cjk_font`, `near_geometry_threshold`, `hybrid_or_raster_boundary` can never fire |
| W3 | `ArtifactLifecycleRecord.delete_after` (`services/artifact_lifecycle.py:62`) | `:453` GC skip | only tests; `SqlArtifactLifecycleAuthority._record` (`:143-151`) omits it | GC deferral branch inert |
| W4 | `GroupConstraint.*` (`models/slidespec_v1.py:214`) | `utils/slidespec_solver.py:349`, `slidespec_constraints.py:379` | no `GroupConstraint(` in production; importer emits only Readability/Order/NonOverlap (`slidespec_authored_importer.py:359,380,595,607`) | group-constraint arm of solver/checker is test-only |
| W5 | `NonOverlapConstraint.clearance`, `OrderConstraint.minimumSeparation` (`slidespec_v1.py:291,306`) | solver `:343`, constraints `:669`, `slidespec_geometry_candidates.py:400,453,1279,1319`, `coupled_overlap_clearance.py:68` | importer never passes them; no repair op sets them (`utils/slidespec_core.py:396-408`) | every imported constraint runs with zero clearance |
| W6 | `ReadabilityConstraint.minLineHeightRatio`, `minContrastRatio` (`slidespec_v1.py:320`) | `slidespec_constraints.py`, `slidespec_v1.py:332` | importer omits (`:359-372`) | line-height and contrast checks never armed |
| W7 | `LayoutIntent.intentionalOverlapGroup`, `alignmentGroup` (`slidespec_v1.py:133`) | `slidespec_core.py:582`, `slidespec_repair_pattern_index.py:108` | importer sets only `allowIntentionalOverlap` (`:878`) | dead projection branches |
| W8 | SlideSpec repair-context keys `requiredBounds`, `maxGeometryCandidates`, `minimumSeparation`, `ocrSafety`, `retryScales`, `textNodeMappingComplete`, `browserMeasurement`, `protectedRegion` | `slidespec_geometry_candidates.py:493,2427,453,1319`, `slidespec_coupled_repair.py:254,260`, `slidespec_repair.py:421,680` | `slidespec_repair_context/builder.py` emits none of them | readers always take the default arm |
| W9 | `quality["nativeTypographyEvidence"]` (`semantic/readiness_validators.py:710-724`) | `:840` (bounded-hybrid v3 release path) | no producer in Python, TS, or contracts | the validator can only fail with `authored_trusted_cjk_native_evidence_invalid`; reachability under default config UNCERTAIN |
| W10 | Composition-envelope caps `maxNumericItems`/`maxNumericCards`, `maxTableRows`, `maxTableColumns`, `imageAspectPolicy` | `composition_capacity.py:97,1770,1798`, `narrative_ir/frame_search.py:197` | none | table caps effectively 10,000, numeric caps 0, aspect policy never `"exact"` |
| W11 | Next.js legacy-alias fallbacks (`timelineProjection.ts:512`, `WorkspaceOperationCard.tsx:343`, `WorkspaceOperationResult.ts:159,199`, `services/api/authored-quality-normalization*.ts:161`) — ~50 snake_case keys such as `actor_id`, `fixed_pages`, `full_renders`, `schema_rejects` | `??` alias arms | no backend producer | harmless but widens the normalizers; prune once persisted workspace rows are confirmed clean |
| W12 | deck-IR report contracts `VisualNonRegressionReportV1`, `CanaryWindowV1`, `QualityDriftReceiptV1`, `ObservedDriftV1`, `GenerationIntentLineageV1` (`deck_ir/contracts.py:640-807`); `GenerateDeckRequest.slide_scene_inputs` | validation / gated pipeline | no constructor anywhere except two tests | contract-only |

UNCERTAIN: `qualityRejectionLabels.ts` keys (`improvement_unavailable`, `no_geometry_trigger`,
`roundtrip_failed`, `typed_pipeline_unavailable`) vs backend prefixed codes (`vqa.*`,
`legacy_preserved.*`, `render.source_projection.*`, `repair.*`) — check whether the map strips
the prefix.

## 10. Env / settings knob drift

- `WEB_SEARCH_PROVIDER` is a registry entry (targets `fastapi.runtime`, `start.user_config`, `compose.shared`, `docs.public`, `user_config.ui`, default `"auto"`) with no reader anywhere. `npm run test:settings-registry` cannot catch a reader-less entry.
- `get_env.py` literals absent from the registry and set nowhere: `AUTHORED_RESUME_MAX_ATTEMPTS`, `ENABLE_MOCK_ENDPOINTS` (gates router registration), `SENTRY_DSN`.
- `os.getenv` outside `get_env*.py`, unregistered and set by no compose/start.js/Dockerfile/script: 13 release-authority signing knobs (`AUTHORED_GATE0_STRUCTURAL_*`, `AUTHORED_RELEASE_*`), 9 `SLIDESPEC_*` (documented only in `services/slidespec_rollout_policy.py:14-26`), `AUTHORED_{EDITABLE_MVP,SEMANTIC_HYBRID}_KILL_SWITCH`, `AUTHORED_PROVIDER_CONCURRENCY`, `AUTHORED_SLIDE_PIPELINE_CONCURRENCY`, `AUTHORED_REASONING_EFFORT`, `AUTHORED_VNEXT_{DEFAULT_COMPANY_STANDARD_ID,INTERNAL_COMPATIBILITY_ENABLED,NEW_GENERATION_ENABLED,TENANT_ID}`, `PUBLIC_WORKER_*`, `PPT_PUBLIC_OIDC_*`, `WORKSPACE_{ASSET_ORPHAN_TTL_SECONDS,CLEANUP_MAINTENANCE_INTERVAL_SECONDS,DESTRUCTIVE_APPROVAL_TTL_SECONDS}`, `MEM0_*`, `CORS_ALLOWED_ORIGINS`, `LOG_LEVEL`, `TESSDATA_PREFIX`, `TESSERACT_CMD`, and others (~35 total).
- TS `process.env` set nowhere: `AUTHORED_HYBRID_{BACKPLATE_SCALE,CAPTURE_TRACE,CHROME_PATH,NATIVE_CHARTS,NATIVE_IMAGE_DECORATION}`, `OOXML_VNEXT_{INTERNAL_SECRET,MAX_BODY_BYTES,WORKER_TIMEOUT_MS}`, `NEXT_PUBLIC_TEMPLATE_V2_STUDIO_ENABLED`, `NEXT_PUBLIC_ENABLE_LEGACY_DOCUMENT_PREVIEW`, `PRESENTON_FONT_EMBEDDING_DIR`, `WORKSPACE_E2E_CYPRESS_TIMEOUT_MS`.
- Knobs set only by bench/dev scripts, never by a shipped deployment file: `AUTHORED_HTML_NORMALIZATION`, `AUTHORED_RENDERER_MODE`, `AUTHORED_CHROME_REUSE`, `AUTHORED_VNEXT_*_ENABLED`, `AUTHORED_SEMANTIC_*`, `DECK_IR_GENERATION_ENABLED`, `PUBLIC_V2_INTEGRATION_PROFILE`, `PUBLIC_ARTIFACT_S3_*`. These are the gates in §11.

CLAUDE.md already records the "four places" invariant as violated; this section makes the
backlog explicit rather than adding to it.

## 11. Implemented vs executed

LIVE = reached from a request or worker path under default configuration. GATED-OFF = reached
only when a default-off knob is on and no shipped config turns it on. MANUAL = reached only from
an explicit user action, never during generation.

| Cluster | Lines | Class | Gate (default) | Nearest live seam |
|---|---|---|---|---|
| SlideSpec (`services/slidespec_*`, `slidespec_repair_context/*`, `utils/slidespec_*`, `models/slidespec_v1.py`) | ~36,600 | MANUAL | `SLIDESPEC_KILL_SWITCH` off, `REPAIR_APPLY` true, `REPAIR_ALL_SLIDES` true, `REPAIR_CANARY` 100 | `authored_quality_review_canonical.py:271,281,314-320` (lazy) ← `endpoints/authored_operations.py:44 queue_authored_quality_review`. Generation never imports it (`slidespec_rollout_policy.py:1-12`). Inside the review, the OCR gate (W1), group arm (W4), `requiredBounds`/`ocrSafety` arms (W8) cannot fire for lack of inputs. This, not a flag, explains "0 repairs applied" in the 2026-08-05 90-slide run |
| `services/authored_structural_repair.py` / `_compare.py` | 670 | LIVE (`validate_repair_result`, `structural_checkpoint_metrics`, `validate_structural_checkpoint`; verbatim evaluators on the `source_reflow` path) except `bind_repair_candidate` | `AUTHORED_VERBATIM_SINGLE_REPAIR_LOOP` False (`get_env_programme.py:324`) | `authored_presentation_generation.py:1077,1096,2013-2082,2203` |
| `utils/authored_infographic/*` | 2,592 | `contracts.py` (247) and `LIBRARY_MARKER` LIVE; checker/skeleton/css_*/tokens/icons (~2,250) GATED-OFF | `AUTHORED_INFOGRAPHIC_PLAN_ENABLED` off ("Off by default (2026-09-05)", `get_env.py:271-280`) | `author_slide.py:1208`, `author_batch.py:248` call unconditionally; no-op when the plan is `None` |
| `utils/authored_html_tree/*` | 552 | `raw_diagnostics.py` LIVE (lazy, `authored_admission.py:49`); normalizer GATED-OFF | `AUTHORED_HTML_NORMALIZATION` `"legacy"` (`get_env_programme.py:94`); `AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING` False | `authored_html_normalize.py:426-441`; `authored_admission.py:134-146` |
| `services/source_content/placeholder/*` | ~890 | GATED-OFF | `AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING` False | `authored_admission.py:136,192` (lazy) |
| `services/source_content/{materializer (986), publication (867), export_readback (365)}.py`, `allocator.ContentAllocator`, `PageLockedTemplateCompositionPlanner` | ~2,400 | UNREACHABLE — only from `pipeline.build_source_content_staging` / `generate_source_content_restyle_presentation`, which have zero callers in prod, scripts, or tests | — | `publication.py` is a lazy import at `pipeline.py:794` |
| `services/trusted_executor/*` except `dispatch_evidence.py` | ~4,900 | GATED-OFF and fails closed | `PUBLIC_V2_INTEGRATION_PROFILE` unset (needs `postgresql-strict`, `api/lifespan.py:215-226`); `PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND` unset → `IsolationUnavailableError` (`composition.py:96-116`); role `ppt-core` never configured (`docker-compose.yml:420-451`) | `isolated_worker_process.py:338-340`; `probe_platform_isolation` reports every control UNSUPPORTED by construction (`composition.py:139-141`) |
| `services/composer/*` | 8,746 | GATED-OFF + already deleted on main | `COMPOSER_RESTYLE_ENABLED` false | `presentation_generate.py:817` (lazy) |
| `authored_vnext`: `master/*`, `design_pack/*`, `generation_route.py`, `generation/{legacy_bridge,legacy_pin,effective_style,body_grammar,retained_reference_marker,capability_registry}`, `narrative_ir/content_graph.py`, `deck_ir/strategy.py` (preflight), `semantic/{canonical,query,publication,readiness*,editable_scene,remake,export_binding,*_v3}` | — | LIVE | `AUTHORED_VNEXT_MASTER_ENABLED` "true"; `*_v3` behind admin authz | `router.py:107-111`, `endpoints/design_packs.py`, `public_v2_orchestrator.py:222,1052` (lazy), `presentation_export_direct_attempt_semantic.py:26-38`, `lifespan.py:211-212` |
| `authored_vnext`: `source_native_generation.py` (4,170), `composition*.py`, `source_unitization`, `source_verbatim_capacity`, `preserve_source_orchestration`, `source_render_geometry`, `slide_quality_acceptance`, `narrative_ir/{director,orchestration,frame_search,checkpoint}` | ~12,000 | LIVE-BY-DATA (only when `selectedRoute == "source-native"`, i.e. a pinned or resolvable design pack) | none | `presentation_generation_operation_handler.py:224-232` (lazy) |
| `authored_vnext`: deck-IR execution (`deck_ir/{integration,director,compiler,grammar,product_adapter,candidates,canonical,validation,zone_layout,intent_resolution,style_profiles,browser_closure,component_renderer,quality}`) | ~6,400 | OPT-IN-GATED-OFF | request `generation_engine` default `"authored"` (`api/v2/contracts.py:120`) + `DECK_IR_GENERATION_ENABLED` unset | handler `:389-411` |
| `authored_vnext`: `generation/{pipeline,scene_compiler,mutations,sql_repository,gate0e_probes,core_bridge,scene_checkpoint,scene_envelope}`, `semantic/{native_capabilities,editable_mvp_completion}` | ~14,000 | GATED-OFF | `AUTHORED_VNEXT_GENERATION_ENABLED` "false" (+ `INTERNAL_COMPATIBILITY_ENABLED`, `NEW_GENERATION_ENABLED`) | `generation/router.py:71-111` |
| `authored_vnext`: `integration/{export_service,sql_repository*,artifact_store,worker}`, `ooxml/service.py`, `semantic/lifecycle` + `export_binding` writes, `semantic/typography.py` | ~8,500 | GATED-OFF | `AUTHORED_VNEXT_EXPORT_ENABLED` "false", `AUTHORED_VNEXT_OOXML_PACKAGE_ENABLED` "false", `AUTHORED_SEMANTIC_BUNDLE_WRITES_ENABLED` "false" | routers |
| `authored_vnext/ooxml/native_scene.py` | 3,151 | UNCERTAIN — its cited seams (`source_content/export_readback.py:17`, `publication.py:614`) are themselves unreachable; the confirmed path `integration/worker.py:36-52` is behind `EXPORT_ENABLED` | — | proof: a caller of `materialize_scene_pptx` under `presentation_export_direct_attempt_*` |
| Next.js `lib/authored-hybrid/*` (~60 modules incl. `browser-source.ts` 3,900, `native-plan.ts` 2,886, `slide-layer.ts` 965, `pptx-assembler.ts` 746) | ~15,000 | GATED-OFF | `PUBLIC_EXPORT_HYBRID_COMMAND_JSON` unset → `workers/public_export_hybrid.py:68-70` returns `None` | only importer `scripts/public-hybrid-export-worker.mjs:166-174` (dynamic import, spawned by `services/public_export_worker_bundle.py`) |
| Next.js `lib/authored-hybrid/export.ts` cluster (11 files, not 10: `export`, `export-evidence`, `export-performance`, `export-result-cache`, `powerpoint-qid-expectation`, `raster-pdf`, `scene-html-assets`, `export-process-supervisor`, `export-render-url`, `run-bundled-presentation-export`, `runtime-limits`) | — | HELD (0 callers by design; `protected-local-patches.json`; `test_isolated_public_workers.py:791-833`) | — | `export-progress.ts` is outside the closure (live reader via `app/api/internal/export-progress/route.ts`, returns `progress_unavailable` because the writer is held) |
| Next.js `lib/ooxml-vnext/*` (adapter 533, materializer 1,667, master-compiler 764, compliance 498, …) | ~8,000 | GATED-OFF | `AUTHORED_VNEXT_OOXML_WORKER_TOKEN` / `OOXML_VNEXT_INTERNAL_SECRET` unset → 503 (`app/api/internal/ooxml-vnext/route.ts:56-61`) | reached only from the `EXPORT_ENABLED` router and `legacy_bridge.py:486-497`; `product-runner.mjs` spawned by `ooxml/adapter.py:52-58` under `OOXML_PACKAGE_ENABLED` |
| `services/source_locked/*` (15), `source_content` core, `trusted_executor/dispatch_evidence.py`, `compatibility_migration/*` except `rollout.py`, `services/chat/*` via the workspace handler and `conversations` FeatureModule | — | LIVE | none (`chat` is dark when `CHAT_FIRST_WORKSPACE_ENABLED=false`; wired, not dead) | `lifespan.py:201-202`, `presentation_generate.py:107-111,501,815` |

Lazy-only reachability in flat services (16 modules; removal fails at first call in a running
worker, not at boot): `authored_admission_legacy`, `authored_illustration_reviews`,
`authored_local_fit_chart_readback`, `authored_local_fit_export`, `authored_local_fit_export_evidence`,
`authored_slide_quality_receipt`, `authored_vision_advisory`, `lightweight_document_service`,
`local_stage_receipt_queue`, `provider_credential_backends`, `provider_pricing_registry`,
`provider_runtime_snapshot`, `provider_settings_sync`, `provider_usage_receipt_service`,
`public_quality_receipt_v3_projection`, `slidespec_authored_importer`. Whole FastAPI production
tree: 1,208 modules, 1,002 top-level reachable, 179 lazy-only, 27 not reached (all classified above).

## 12. Dependencies and CI

Unused `servers/nextjs/package.json` dependencies (0 import sites):

| Package | Note |
|---|---|
| `mermaid` ^11.16.1 | CLAUDE.md line 153 says "diagrams via Mermaid"; recently bumped for 5 security advisories; nothing imports it. Wire it or remove it and fix CLAUDE.md |
| `@radix-ui/react-radio-group` | only `components/ui/radio-group.tsx` (dead) |
| `@radix-ui/react-scroll-area` | only `components/ui/scroll-area.tsx` (dead) |
| `@radix-ui/react-toast` | toasts go through `sonner` (45 importers) |
| `@paciolan/remote-component` | — |
| `react-simple-code-editor` | code editing is `@tiptap/*` + `prismjs` |

Verified live: `@radix-ui/react-icons`, `tailwindcss-animate`, `canvas-confetti`, `react-colorful`, `jsonrepair`, `mixpanel-browser`, `konva`/`react-konva`, `katex`, `prismjs`, `cmdk`.

CI gaps (coverage, not dead code):

- 77 tracked Cypress specs; `test-all.yml` runs 22 named specs and bitbucket one more (already in the 22). About 54 specs never run in any pipeline: the `template-v2-studio` toolbar suite, most of the workspace/chat-first suite, `authored-vnext` master-setup specs, `AuthoredWorkbench.cy.tsx`. All 77 resolve every import (verified), so these are runnable.
- `lib/run-bundled-presentation-export.e2e.test.mjs` is excluded from the shared Node runner and named by no dedicated flow.
- Four `tsconfig.check.json` files are referenced only by a README.
- `g4-pptx-roundtrip.yml` no longer calls the missing `scripts/check_*_pptx_roundtrip.py` (the names survive only in a comment); the memory item about a broken workflow is resolved.
- `run-ci-node-tests.mjs` walks `app/` with `readdirSync`, so the `[id]`-directory glob trap does not affect it; Cypress `fast-glob` also descends into `app/template-v2-studio/[templateId]/` (13 specs matched).

## 13. Recommended execution order

Each phase is independently mergeable. Every deletion requires regenerating
`compatibility/upstream-byte-parity.json` (`node scripts/verify-upstream-byte-parity.mjs --write`)
and editing any RUFF / RATCHET entry named above.

1. **Merge `main`** (removes 208 files: composer, electron, packaged entrypoint). Resolve the three composer seam hunks and the four workflow files in §2 in the same change. Do not hand-delete any of those clusters first.
2. **Zero-risk deletions** (§3, no lazy importers, no IN-EDIT overlap): `utils/internal_http.py`, `utils/ppt_utils.py`, `semantic/editable_hybrid_libreoffice_readback.py`, the two `tests/support/source_locked_*_live*.py` CLIs (after asking the source-locked owner), `scripts/authored_admission_fixture_replay.py`, the seven dead `app/` files, the two dead shadcn primitives plus their radix packages, `template-v2-group.ts`, the three dead `lib/` files, the three public assets, the four unused npm packages. Then `npm run test:file-size-ratchet`, `npm run lint`, `uv run ruff check .`.
3. **Retired template_v2 generation path** (§4): `template_v2_generation_service.py`, `template_v2_generation_contracts.py`, `generate_slide_content.py`, `generate_presentation_structure.py` + `get_presentation_structure_model_with_n_slides`, `layout_capacity.py`, the three dead functions in `presentation_export_route_inputs.py`, their tests, RUFF lines 386/392. Decide first whether existing v2 decks need a native-export route (nothing provides one today).
4. **Export-path leftovers** (§4, §5): coordinate with the session that removed `export_utils.py` — `export_task_service.py` + `export_task_operations.py`, the `AD` phantoms, the stale `exportSession=` regex, the ledger entry, CLAUDE.md line 138.
5. **Retired chat/community/quality-review symbols** (§4) and **DEAD-SYMBOL** rows (§6). The 501-line `retained_reference.py` pair and the 185-line reducer cluster are the largest.
6. **Behavioural forks** (§7) — fix F1 (`font_embedding`), F2/F3 (digests), F4 (WCAG threshold), F5 (published filter), F6/F7 (OOXML), F8 (font-family parser), F9 (`_number` default), F11 (Korean strings) before consolidating the wider duplicate families, since consolidation forces a choice of behaviour in each.
7. **Duplicate consolidation** (§8) by lines × copies: env-int parser, canonical digest, PPTX package reader, XML/HTML escapers, chart data-label plugin, provider UI, `isRecord`/`clamp` guards, test `_run`/alembic helpers.
8. **Writeless fields** (§9): either produce `impacted_recall`, the T1 slide flags, and the repair-context keys, or delete the reader arms. This is the same class of defect as `image_text_evidence` and decides whether the SlideSpec review can ever apply a repair.
9. **Knob hygiene** (§10) and **CI coverage** (§12) as separate housekeeping changes.

## 14. IN-EDIT overlap (confirm with the owning session before acting)

Files being edited by other sessions at audit time: `services/authored_rollout_profile.py`,
`authored_admission_policy.py`, `authored_admission_context.py`, `authored_admission.py`,
`authored_admission_checkpoint.py`, `authored_checkpoint_html.py`, `authored_checkpoint_state.py`,
`authored_checkpoint_stage_batch.py`, `authored_presentation_service.py`, `account_deletion_controller.py`,
`retained_reference.py`, `services/authored_vnext/master/service.py`, `services/source_content/placeholder/{prompt,block_table}.py`,
`services/source_content/source_runs.py`, `utils/authored_html_tree/raw_diagnostics.py`, `utils/slide_capture.py`,
`utils/provider_overload.py`, `utils/get_env_programme.py`, `utils/llm_calls/author_slide.py`,
`tests/unit/test_authored_admission.py`, `tests/support/authored_worker_lane_harness.py`,
plus the export-refactor session's `AD` files.

No DEAD-file finding touches those files. Findings that do: the env-int family
(`author_slide.py:125`, `slide_capture.py:1939`), `json_value` (`authored_admission_context.py:28`),
the FOR UPDATE admission lock trio (`authored_checkpoint_fence` neighbourhood), the glyph-width
consolidation (`master/service.py` imports `master/title_fit`), and
`source_content/placeholder/block_table.py::table_from_manifest` (test-only, 9 tests, 0 prod — not tabled).

## 15. Cross-auditor conflicts, resolved by direct check

| Item | Claims | Resolution |
|---|---|---|
| `lib/authored-hybrid/mode.ts` | one auditor: DEAD; another: live | imported (explicit `.ts` extension) by `export-presentation-route.ts`, so the module loads; but its only value caller is the test-only `executeExportPresentationRouteRequest`. Classified as a dead symbol chain (§6), not a dead file |
| `lib/authored-hybrid/security.ts`, `security-data.ts` | gated-off with the hybrid cluster | `readBoundedResponseText` is called by `export-presentation-route.ts:216` on two ungated live paths; LIVE |
| `lib/ooxml-vnext/generated/ooxml-package.ts` | DEAD vs TEST-ONLY | generated by `contracts/authored_vnext/ooxml/generate.py`; keep |
| `utils/ppt_utils.py`, `utils/layout_capacity.py` | reported by one auditor only | confirmed 0 importers |
| `templates/v2/local_assets.py`, `templates/v2/pptx/vision_adapter.py` | reported by one auditor only | confirmed test-only |
| `export.ts` hold closure size | contract said 10 | computed 11 (`runtime-limits.ts`, `export-render-url.ts`, `export-process-supervisor.ts`, `run-bundled-presentation-export.ts` inside; `export-progress.ts` outside) |
| Composer "deleted 2026-09-08" (memory) | present here | true on `main`, not on this branch |

## 16. Held items (not findings)

`lib/authored-hybrid/export.ts` cluster (11 files, protected); `scripts/composer_*` (goes with the
merge); slidespec benchmark helpers and `authored_only_canary/census` (runbooks); `scripts/authored_checkpoint_repair_probe.py` + `.ps1`
and `scripts/authored_live_series_provider_usage.py` + `.ps1` (runbook pairs, add to the hold list);
`deck_ir/hybrid_admission.py`; `lib/export-fidelity`, `test-harness`, fixture builders; Template V2
studio and data paths; `services/public_quota_reconciliation.py` (alembic); worker entrypoints
`template_v2_pptx_worker.py` / `workspace_operation_worker.py`; `workspace_legacy_write_gate.py`,
`provider_settings_legacy.py` ("legacy" in name only).
