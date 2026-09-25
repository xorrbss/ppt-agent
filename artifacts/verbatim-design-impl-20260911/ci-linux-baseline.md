# FastAPI suite on a clean Linux runner — measured baseline

Date: 2026-09-12
Commit measured: `d25d91c35b05ba030277cec13cb3a09cc2110b53` (detached, committed state only)
Runner: WSL2 Ubuntu, 22 cores / 21 GB, Python 3.11.15, `uv 0.11.17`, `uv sync --frozen`
Companion document: `artifacts/verbatim-design-impl-20260911/ci-coverage-gap.md` (commit `d25d91c35`)

This is a **measurement**. Nothing was fixed, no CI configuration was changed, and every
proposal below is marked PROPOSAL.

## 0. Environment, and how it differs from the declared workflow

The task's suggested command shape declared three variables. `.github/workflows/test-all.yml`
(job `test-fastapi`) declares seven. The runs below used the **workflow's** set, so the
baseline is comparable to what a real gate would see:

| Variable | Suggested shape | `test-all.yml` | Used here |
|---|---|---|---|
| `APP_DATA_DIRECTORY` | `/tmp/app_data` | `/tmp/presenton-app-data` | workflow value |
| `TEMP_DIRECTORY` | — | `/tmp/presenton-temp` | workflow value |
| `USER_CONFIG_PATH` | — | `/tmp/presenton-app-data/userConfig.json` | workflow value |
| `PUPPETEER_EXECUTABLE_PATH` | — | `/usr/bin/chromium` | workflow value (binary absent here) |
| `DATABASE_URL` | `sqlite+aiosqlite:///./test.db` | same | same |
| `DISABLE_ANONYMOUS_TRACKING` | — | `true` | workflow value |
| `DISABLE_IMAGE_GENERATION` | `true` | `true` | same |

Other deltas against the workflow, all of them *reductions* in what this run could exercise:

- The workflow `apt-get install`s `libreoffice fontconfig chromium chromium-driver imagemagick`
  before pytest. This runner has **none** of them, and also has **no `node` on `PATH`**.
- The workflow runs `contracts/template_v2/generate.py --check` and `ruff check .` before pytest,
  then splits pytest into two invocations (Gate E alone, then `--ignore` that file). This run
  executed pytest only, in one logical pass.
- `pytest-timeout` and `pytest-xdist` are **not** in the locked environment. There is no
  per-test timeout and no parallelism anywhere in this suite.

## 1. Per-directory results

`-q -p no:cacheprovider`, serial, full output captured to `/tmp/baseline-*.log`.

| Directory | Collected | Passed | Failed | Errors | Skipped | Wall (s) |
|---|---:|---:|---:|---:|---:|---:|
| `tests/unit` | 7256 | 7028 | 174 | 0 | 54 | 673 |
| `tests/integration` | 159 | 51 | 44 | 0 | 64 | 295 |
| `tests/regression` | 26 | 23 | 3 | 0 | 0 | 16 |
| `tests/edge_cases` | 6 | 4 | 2 | 0 | 0 | 9 |
| `tests/` (9 top-level modules) | 73 | 68 | 5 | 0 | 0 | 11 |
| **Total** | **7520** | **7174** | **228** | **0** | **118** | **1004 (16m44s)** |

`Errors` is zero in the pytest sense: no collection errors and no fixture/teardown errors.
Collected (7520) matches the `--collect-only` count recorded in `ci-coverage-gap.md`.

### 1a. `tests/unit` had to be split, and why that matters

`tests/unit` was first run as one invocation. It **deadlocked** at roughly test 533 of 7256
(~6% of the progress bar) and made no further progress for 11 minutes. Process inspection:
24 threads, `State: S`, `wchan = do_epoll_wait`, one listening socket on `127.0.0.1:39843`
with no peer. A test had started an in-process server and was waiting on something that never
arrived. It was killed, not recovered.

Re-running the identical files split into 12 chunks (files 1–49, 50–97, …, each under
`timeout -s KILL 1500`) completed every chunk, including the file range that contained the
stall. The hang is therefore **order- or timing-dependent, not a fixed test**. Observed once;
not reproduced in the chunked runs.

The per-chunk numbers that make up the `tests/unit` row:

| Chunk | Files | Failed | Passed | Skipped | pytest (s) |
|---|---|---:|---:|---:|---:|
| 1 | 1–49 | 15 | 710 | 7 | 79.4 |
| 2 | 50–97 | 3 | 638 | 1 | 17.6 |
| 3 | 98–145 | 14 | 987 | 2 | 33.2 |
| 4 | 146–193 | 41 | 477 | 2 | 32.9 |
| 5 | 194–241 | 47 | 338 | 1 | 81.5 |
| 6 | 242–289 | 4 | 707 | 0 | 68.8 |
| 7 | 290–337 | 8 | 469 | 2 | 56.4 |
| 8 | 338–385 | 10 | 403 | 1 | 45.7 |
| 9 | 386–433 | 6 | 416 | 25 | 26.0 |
| 10 | 434–481 | 7 | 864 | 1 | 62.2 |
| 11 | 482–529 | 5 | 557 | 1 | 33.9 |
| 12 | 530–577 | 14 | 441 | 11 | 78.2 |
| 13 | `tests/unit/services/**` | 0 | 21 | 0 | 4.3 |

**A blocking gate cannot be built on a suite with no per-test timeout and an observed
non-deterministic deadlock.** Without `pytest-timeout`, that hang consumes the whole job
timeout and reports nothing.

## 2. Failures grouped by root cause

228 failures resolve to 21 groups plus a 34-failure singleton tail. Buckets are exactly the
five requested: `platform`, `environment`, `stale-fixture`, `harness-gap`, `product-defect`.

| # | Root cause | Tests | Bucket |
|---|---|---:|---|
| G1 | Pipeline re-capture seam is not stubbed | 51 | harness-gap |
| G2 | Shared fixture hard-codes the Windows font `Malgun Gothic` | 36 | platform (fails, does not skip) |
| G3 | `node` binary not on `PATH` | 15 | environment |
| G4 | `DeckSnapshot` can never satisfy the export payload builder | 14 | product-defect |
| G6 | Authored-only admission is now permanent; tests assert the old codes | 14 | stale-fixture |
| G5 | Hand-built `create_all(tables=[...])` subsets drifted | 12 | stale-fixture |
| G9 | Importer collapses every exception to `import.failed` | 9 | harness-gap |
| G7 | Retained OSB reference PPTX is unreachable | 8 | environment (+ latent product-defect) |
| G8 | Seeded presentation rows no longer derive to any driver | 8 | stale-fixture |
| G10 | Exact float equality against a clock-derived timeout | 4 | platform (fails, does not skip) |
| G13 | `GenerationAttemptTimingReceiptV2.failure_stage` removed | 4 | stale-fixture |
| G11 | template-v2 compat router route set changed (`GET` gone) | 3 | stale-fixture |
| G12 | Route-introspection helper cannot handle a two-body route | 3 | harness-gap |
| G14 | `C:/…` paths hard-coded in trusted-executor tests | 3 | platform (fails, does not skip) |
| G15 | `SimpleNamespace` / ad-hoc stand-ins drifted | 3 | stale-fixture |
| G16 | `monkeypatch.setattr` targets a removed module attribute | 2 | harness-gap |
| G17 | `seal_content_integrity_receipt()` signature changed under the test | 2 | stale-fixture |
| G18 | Artifact CAS guard refuses a non-development SQLite profile | 2 | environment |
| G19 | Naive/aware datetime comparison raises `TypeError` | 1 | product-defect |
| G20 | Embedded-PostgreSQL harness looks for `initdb.exe`/`pg_ctl.exe` | 1 | platform (fails, does not skip) |
| G21 | Production lifecycle gate needs real worker processes | 1 | environment |
| Z | Singleton contract drifts (see §2b) | 34 | mixed |

Bucket totals over the 194 grouped failures: `harness-gap` 65, `platform` 44, `stale-fixture` 46,
`environment` 26, `product-defect` 15. The 34-failure tail is dominated by `stale-fixture`.

### G1 — pipeline re-capture seam is not stubbed (51, harness-gap)

Failure shapes: `AuthoredStageError: Authored slide render failed`,
`authored_local_fit_original_output_unavailable`, `reusable authored Chrome render failed`,
`'error' == 'completed'`, `'render' == 'author'`, `composition safety gate (telemetry:unavailable)`.

The chain: `services/authored_presentation_pipeline.py:1196` and `:1240` call
`admit_local_fit_capture(..., render=render_html_to_png_with_metrics, ...)` /
`preserve_original_capture(..., render=render_html_to_png_with_metrics, ...)`, resolving
`render_html_to_png_with_metrics` as a **module global of the pipeline module**.
`services/authored_local_fit_admission.py:171` then drives a real capture and
`:189` swallows whatever failed into `AuthoredLocalFitRejected("original_output_unavailable")`.

Both harnesses stub the wrong module:

- `tests/support/authored_worker_lane_harness.py:684` — `monkeypatch.setattr(terminal_mod,
  "render_html_to_png_with_metrics", fake_capture)`. Only `terminal_mod`. `pipeline_mod` is
  patched for `inspect_authored_dom`, `blocking_safety_issues`, `capacity_violations`,
  `ensure_browser_metrics`, `get_cached_browser_metrics` — but not for this function.
- `services/authored_presentation_service.py:61-80` (`_sync_runtime_dependencies`) propagates
  `render_html_to_png` into `_pipeline`, and `find_chrome` into `_generation`. It does **not**
  propagate `render_html_to_png_with_metrics`. The harness's comment at line 664 ("the service
  facade is copied into the pipeline module, so patch the facade") is true for
  `render_html_to_png` and false for the `_with_metrics` variant.
- `tests/unit/test_authored_author_stage_rescue.py` (docstring lines 3-6) inherits the same
  pattern via `tests/unit/test_authored_pipeline.py`.

**Currently unverified because of this gap** — these 51 tests establish nothing beyond
"the render stage fails":

- authored worker-lane e2e: token-profile wave scheduling and publication;
- overload handling: concurrency halving and recovery after three successes, scheduler-owned
  cooldown wait, the wave barrier that prevents slide loss under overload;
- safety gate: a real defect reaching remake and then flag through the scheduler;
- terminal behaviour: persistent renderer policy violation failing the deck when flag
  publishing is off, and persistent author failure publishing a flagged placeholder when on;
- author-stage D7: third simplified attempt, flagged placeholder, "only the failed slide
  spends a rescue call", knob-off behaviour, provider crash in the third attempt;
- deck-plan illustration: per-slide image exclusion, plan round-trip through the persisted theme;
- slide-pipeline receipts: first-pass provider attempt counts, recorded normalizations,
  cover-title promotion.

Caveat: this runner has no Chromium. On the CI image (which apt-installs `chromium` and sets
`PUPPETEER_EXECUTABLE_PATH`) the unstubbed call might reach a real browser and pass — slowly,
and against the harness's own stated intent ("No browser in CI",
`tests/support/authored_worker_lane_harness.py:653`). Either outcome is a defect in the harness,
not in the product.

### G2 — `Malgun Gothic` hard-coded in a shared fixture (36, platform)

`tests/support/authored_vnext_master_fixture.py:66-88` pins `Malgun Gothic` as the master's
latin / eastAsian / complexScript / hang / jpan family and as `windowsFallback`.
`services/authored_vnext/generation/scene_compiler.py:424` (`_measurement_font_path`) then
raises `authored_vnext_font_measurement_unavailable` because no such font file exists on Linux.
Two more tests in the same family fail downstream with
`authored_vnext_operation_conflict` (409) — the first generate attempt dies on the font and
leaves the operation non-terminal.

This is a **failure, not a skip, so it is a portability bug**. The repository already has the
correct pattern and does not use it here: `tests/unit/test_authored_native_typography_registry.py:70,119`
and `tests/unit/test_authored_cjk_release_authority_v3.py:177,182,206` *skip* with
"Windows CJK font-byte contract requires Malgun Gothic and Batang" when the font is absent.

No Linux CI image will ever have Malgun Gothic, so these 36 will fail on any Linux gate until
the fixture either skips or pins a font that is installable there.

### G3 — `node` not on `PATH` (15, environment)

`FileNotFoundError: [Errno 2] No such file or directory: 'node'` in the vNext OOXML package
import/lifecycle and corporate-normalize paths, plus one gate-1 canonical-graph unit test.
Missing input: a `node` executable. Both real CI images have one (`cypress/included:15.19.0`
ships Node; `ubuntu-latest` ships Node), so these 15 are an artifact of this runner and are
**not** evidence about CI.

### G4 — `DeckSnapshot` can never satisfy the export payload builder (14, product-defect)

Failing invariant: `build_worker_request()` declares it accepts either arm of
`ExportableDeck`, but rejects one of them unconditionally.

- `services/authored_vnext/integration/export_payload.py:20` —
  `ExportableDeck = DeckSnapshot | DeckGenerationSnapshot`
- `services/authored_vnext/integration/export_payload.py:38-43` — `worker_scene()` does
  `getattr(deck, "scene", None)` and raises `ValueError("authored_vnext_scene_required")` when
  it is `None`.
- `models/authored_vnext/master/slide.py:73-83` — `DeckSnapshot` **has no `scene` field at all**.
  Only `DeckGenerationSnapshot` (`models/authored_vnext/deck/operations.py:198`) has one.
- `services/authored_vnext/master/service.py:213` — `create_deck()` returns a `DeckSnapshot`.

So the master-service deck → export path raises 100% of the time, while the type annotation
says it is supported. Introduced by `1e4a81845 feat(authored-vnext): complete scene-native
integration`, which made `scene` mandatory without either giving `DeckSnapshot` a scene or
removing it from the union.

The alternative reading — "the master-service deck path was intentionally retired and 14 tests
are stale" — is possible, but then the union at `export_payload.py:20` and the return type at
`master/service.py:213` are both wrong. Either way the defect is in product code.

Affected: all of `tests/integration/test_authored_vnext_export_integration.py` (12) plus the
owner-isolation export test and the rollback test.

### G5 — hand-built `create_all(tables=[...])` subsets drifted (12, stale-fixture)

`sqlite3.OperationalError: no such table: main.public_artifacts` (unit, 7 tests, e.g.
`tests/unit/test_chat_turn_transaction_boundary.py:112` — `SQLModel.metadata.create_all(sync_connection, tables=TABLES)`),
and `no such table: public_operation_admissions` / `authored_deck_plan_checkpoints`
(integration, 3 checkpoint tests, same explicit-subset pattern). The product writes tables the
test never created.

### G6 — authored-only admission is permanent; tests assert the pre-admission codes (14, stale-fixture)

`services/compatibility_migration/admission.py:34-51` states the contract plainly: authored-only
creation "is a product invariant, not a rollout switch", `blocked_drivers=("legacy","template-v2")`,
stable code `legacy_generation_admission_closed`. Tests still expect the older responses:

- `tests/test_presentation_generation_api.py` (5): expects 400 / success, gets 409
  `legacy_generation_admission_closed`.
- `tests/edge_cases/test_generation_edge_cases.py` (2): expects 400 and 408, gets 409 and 422
  `generation_strategy_not_supported`.
- `tests/integration/test_blank_presentation_api_contract.py` (3): expects 201 / 500, gets 409
  `legacy_blank_presentation_admission_closed`.
- template-v2 import (3): 409 `template_v2_import_admission_closed`.
- workspace fence-loss (1): expects 201, gets 409.

### G7 — retained OSB reference PPTX is unreachable (8, environment + latent product-defect)

`services/authored_vnext/generation/retained_reference.py:2123` raises
`retained OSB native source PPTX is required` because `_reference_path()` returns `None`.

Two independent causes, both real:

1. **Missing input.** The default search is `<repo_root>/osb-template-24-layouts.pptx` and
   `<repo_root>/assets/retained-references/<skill>/reference.pptx`. Neither is tracked by git
   (`git ls-files` finds nothing), neither is in `.gitignore`, and neither exists in the
   Windows working tree either. The override is `AUTHORED_RETAINED_REFERENCE_ROOT`. So these 8
   fail on Windows today as well — they are standing failures, not a Linux regression.
2. **Latent product-defect.** `retained_reference.py:151` computes
   `repo_root = Path(__file__).resolve().parents[6]`. For
   `servers/fastapi/services/authored_vnext/generation/retained_reference.py`, `parents[5]` is
   the repository root and `parents[6]` is its **parent directory**. Verified on this checkout:
   `parents[5] == /home/ibiz/ci-baseline`, `parents[6] == /home/ibiz`. Committing the asset at
   the repo root would therefore still not be found.

### G8 — seeded presentation rows derive to no driver (8, stale-fixture)

`services/presentation_profiles.py:296` raises `unsupported_presentation_driver` when a row is
neither authored (`mode == "authored"` or `theme.mode == "authored"`) nor
`version == "v2-standard"`. The failing tests seed `PresentationModel(version='v1-standard', …)`
directly and are caught by the central `before_flush` stamping hook
(`services/database_owner_scope.py:268` → `services/presentation_profile_persistence.py:44`).
Affected: the authored workspace SQLite publication tests and the workspace Gate-E vertical flow.

### G9 — importer collapses every exception to `import.failed` (9, harness-gap)

Nine tests assert `rejection_reasons == {"import.dom.empty": N}` and get
`{"import.failed": N}`. `services/slidespec_authored_importer.py:1103` is
`failures[position] = getattr(error, "code", "import.failed")` inside a bare
`except Exception`, so the concrete exception from `build_authored_import_request()` is
discarded and cannot be recovered from the receipt or the log.

**Unverified as a result**: the importer's fail-closed rejection taxonomy — that an unimportable
deck is rejected *specifically* as `import.dom.empty` and therefore preserved without page
regeneration, without a legacy revise/CAS write, without an image critique and without an HTML
rewrite. Today any failure at all produces the same terminal outcome, so the tests cannot tell
the intended path from an accident. The swallowing itself is worth fixing before this group is
re-triaged; the underlying exception is most likely the same render seam as G1, but that is
**not proven** here.

### G10 — exact float equality against a clock-derived timeout (4, platform)

`{'timeout': 359.9999997} != {'timeout': 360.0}`, `{'timeout': 12.4999966…} != {'timeout': 12.5}`.
`utils/provider_idempotency.py:73-76` passes either the constant or a caller-supplied remaining
deadline; the tests assert exact equality with the constant. Windows' coarser clock happens to
yield the exact value; Linux's finer clock does not. Fails rather than skips, so it is a
portability bug — in the test's precision, not in the product.

### G11–G21 (short form)

- **G11** (3, stale-fixture): `GET` is no longer on the template-v2 compat item route;
  `test_compat_router_adds_advanced_routes_without_legacy_collisions` and two OpenAPI shape
  tests still expect `{'DELETE','GET','PATCH'}`.
- **G12** (3, harness-gap): the tests' own route-introspection helper raises
  `RuntimeError("route … has an ambiguous request body")` for
  `/authored-vnext/master/ai-import/analyses`, which legitimately has two body parameters.
  **Unverified**: the "every route has exactly one request-body model" contract, for every
  route, because the helper aborts on the first multi-body route.
- **G13** (4, stale-fixture): `'GenerationAttemptTimingReceiptV2' object has no attribute
  'failure_stage'`.
- **G14** (3, platform): `tests/unit/test_trusted_executor_contracts.py:35-46` hard-codes
  `C:/sandbox/op-a`, `C:/Windows/System32/cmd.exe`. `Path("C:/…").is_absolute()` is `False` on
  POSIX, so `services/trusted_executor/isolation_policy.py:64` rejects them. Fails, does not skip.
- **G15** (3, stale-fixture): `'types.SimpleNamespace' object has no attribute
  'public_artifact_id'` (2) and `'BrokenSession' object has no attribute 'get'` (1) — ad-hoc
  stand-ins that never grew the attributes the product now reads. This is the pattern already
  fixed twice elsewhere today.
- **G16** (2, harness-gap): `monkeypatch.setattr` on attributes that no longer exist in
  `services.presentation_generation_operation_handler` and
  `services.authored_vnext.generation.legacy_bridge`. **Unverified**: whatever those two tests
  claimed to assert, since the patch target is gone.
- **G17** (2, stale-fixture): `seal_content_integrity_receipt() missing 3 required keyword-only
  arguments: 'attempt_binding_digest', 'preserve_source_input_digest', 'provider_call_budget'`.
- **G18** (2, environment): `ArtifactStoreError: artifact.cas_guard_sqlite_not_development` —
  the CAS guard refuses SQLite outside a development profile. Missing input: a development
  profile flag or a PostgreSQL URL.
- **G19** (1, product-defect): `services/account_deletion_controller.py:254` —
  `request.claim_expires_at <= get_current_utc_datetime()` compares a naive datetime read back
  from the database against `datetime.now(timezone.utc)` and raises
  `TypeError: can't compare offset-naive and offset-aware datetimes`. The repository already
  ships `utils/datetime_utils.as_utc()` documented as "for portable comparisons"; this call
  site does not use it. Failing invariant: an expired account-deletion claim must be
  reclaimable; instead the reclaim path raises.
- **G20** (1, platform): `Failed: PostgreSQL 15 initdb.exe and pg_ctl.exe are required` — the
  embedded-PostgreSQL harness looks for `.exe` names. It fails rather than skipping, unlike the
  `PPT_AGENT_POSTGRES_TEST_URL` gates that skip cleanly.
- **G21** (1, environment): `test_production_api_external_worker_restart_and_sse_backfill`
  (the workflow's dedicated Gate E step) times out waiting for real external worker processes.

### 2b. Singleton tail (34)

One test each unless noted; all are single-assertion contract drifts and read as
`stale-fixture` unless marked otherwise:

`test_every_literal_workspace_error_code_is_registered`,
`test_the_bundled_faces_match_the_exporter_package` (environment: the versioned
`presentation-export` runtime is not synced in this clone),
`test_generation_mode_contract_and_server_owned_route_receipt` (`'authored' == 'deck-ir'`),
`test_ppt_router_is_composed_from_an_explicit_static_feature_tuple` (`'design-packs' == 'community'`),
`test_confirmation_is_target_owned_source_immutable_and_idempotent` (`'design.apply' == 'template.apply'`),
`test_authored_capability_mismatch_fails_before_invocation` and
`test_client_factory_is_not_constructed_before_runtime_policy_passes`
(`'provider_driver_mismatch' == 'provider_capability_mismatch'`, 2),
`test_generated_contracts_are_current_and_compact`,
`test_same_image_placed_twice_on_one_slide_counts_once`,
`test_render_cache_hits_and_render_policy_version_invalidates`,
`test_importer_v4_infers_only_baseline_supported_relational_intent`,
`test_legacy_async_generation_is_local_only` (DID NOT RAISE),
`test_template_v2_requires_exact_template_mode_identity` (DID NOT RAISE),
`test_preview_page_number_is_one_based` (the product moved from
`background_tasks.add_task` to `admit_workspace_authored_task`,
`services/authored_page_remake_service.py:639`, so `background.tasks[0]` is empty),
`test_chat_first_workers_start_and_stop_in_dependency_order` (lambda arity),
`test_slide_failure_logger_collapses_unknown_code_without_leaking_it`,
`test_a_source_binding_rejection_is_recorded_as_a_retryable_slide_failure`,
`test_draft_validate_publish_preview_deck_and_regenerate_conflict`
(`preview["html"].count("data-master-furniture") == 5` but got 0 — **no production code emits
`data-master-furniture`**; the only occurrence in the repository is the allowlist at
`utils/renderer_static_policy.py:28`, so this is either a stale assertion or a silent loss of
master furniture in the preview and needs an owner decision),
`test_source_native_table_cells_share_data_driven_estimator_columns`,
`test_post_vqa_admission_measures_the_candidate_instead_of_only_reading_cache`,
plus 12 structural-diff failures (`test_persisted_asset_is_uploaded_and_carries_source_metadata`,
`test_authored_publication_failure_reports_snapshotted_job_after_rollback`,
`test_fixture_examples_round_trip_through_public_pydantic_models`,
`test_runtime_capabilities_error_registry_and_privacy_are_exact`,
`test_generated_openapi_matches_fixture_shapes_and_endpoint_refs`,
`test_authored_render_runtime_metrics_are_metadata_only`,
`test_footer_zone_category_is_advisory_not_blocking`,
`test_exact_reconstruction_drops_candidates_inside_the_pipeline`,
`test_metrics_recovery_uses_bounded_one_shot_outside_external_worker`,
`test_generation_handler_mints_cookie_in_owner_context_without_persisting_it` (2 params),
`test_openapi_requires_idempotency_header_and_excludes_it_from_body` — the last one is a
serialization-alias drift: the schema now says `authoredStyle`, the test says `authored_style`).

### 2c. How many failures are genuinely product defects

**15 of 228 (6.6%)**, from **two** distinct defects:

| Defect | Tests | Location |
|---|---:|---|
| `DeckSnapshot` arm of `ExportableDeck` is unsatisfiable | 14 | `services/authored_vnext/integration/export_payload.py:20,38-43`; `models/authored_vnext/master/slide.py:73` |
| Naive/aware datetime comparison in the deletion-claim reclaim path | 1 | `services/account_deletion_controller.py:254` |

Plus one **latent** defect the environment bucket currently masks: the `parents[6]` repo-root
off-by-one at `services/authored_vnext/generation/retained_reference.py:151` (8 tests, G7).

Two further items need an owner's call before they can be bucketed with confidence: the missing
`data-master-furniture` markers (§2b) and the `import.failed` collapse (G9) that hides its own
cause at `services/slidespec_authored_importer.py:1103`.

## 3. Skips

118 skips total: 54 in `tests/unit`, 64 in `tests/integration`, 0 elsewhere.

| Skip reason | Unit | Integration | Total | Always true on a **Linux CI image**? |
|---|---:|---:|---:|---|
| headless Chrome / Chrome-Chromium not installed | 24 | 35 | **59** | **No** — the workflow apt-installs `chromium`; these would *run* there, outcome unknown |
| `PPT_AGENT_POSTGRES_TEST_URL` not set | 4 | 15 | 19 | Yes, unless a PostgreSQL service is added |
| Win32 Job Objects / Windows process supervision | 9 | 2 | 11 | Yes |
| Reviewed Windows CJK font bytes (Malgun Gothic, Batang) | 5 | 1 | 6 | Yes |
| Docker transport retired | 3 | 3 | 6 | Yes |
| `PUBLIC_V2_WINDOWS_PRODUCT_E2E` / export-only live RC | 0 | 6 | 6 | Yes |
| Local-only corpora (`identical source regression`, 31-slide AI-master) | 4 | 0 | 4 | Yes |
| Windows process metrics / drive-letter staging / PowerShell 7 | 3 | 0 | 3 | Yes |
| `repository .env is not present` | 1 | 0 | 1 | Yes |
| LibreOffice not installed | 1 | 0 | 1 | **No** — the workflow apt-installs `libreoffice` |
| Electron TypeScript not built | 0 | 1 | 1 | Yes |
| tesseract executable not found | 0 | 1 | 1 | Yes |

**Coverage a Linux gate still would not buy you: 58 of 118 skips** (everything except the 59
Chrome-gated and the 1 LibreOffice-gated rows). Of those 58, **21 are Windows-only by
construction** (Job Objects, Windows font bytes, process metrics, drive-letter staging,
PowerShell 7, the Windows product E2E) and can never run on Linux at all; 19 need a PostgreSQL
service; the rest need Docker, Electron, tesseract, a `.env`, or local corpora.

The 59 Chrome-gated skips are the largest single unknown in this report: on a real CI image
they would execute for the first time ever, and nothing here predicts their outcome.

## 4. PROPOSAL — what it would take to make this a blocking gate

**PROPOSAL. No CI configuration was changed by this work.**

### PROPOSAL 4a — order of work

The buckets are not equally expensive, and one of them is not a test problem at all.

| Order | Work | Tests cleared | Rough size |
|---|---|---:|---|
| 0 | Add `pytest-timeout` to the locked environment and set a per-test timeout | — | ~1 hour, but **mandatory** (see §1a) |
| 1 | `platform` — make the four Windows-only groups skip instead of fail (G2 fixture font, G10 float equality, G14 `C:/…` paths, G20 `.exe` PostgreSQL harness) | 44 | 1–2 days; the skip idiom already exists in the repo |
| 2 | `harness-gap` — G1 (one seam), G9 (stop swallowing), G12, G16 | 65 | 3–5 days; **G1 is one fix** (propagate or patch `render_html_to_png_with_metrics`) that clears 51 |
| 3 | `product-defect` — G4 and G19, plus a decision on G7's `parents[6]` | 15 (+8) | 2–4 days, mostly deciding what the `DeckSnapshot` arm is *for* |
| 4 | `stale-fixture` — G5, G6, G8, G11, G13, G15, G17 and the §2b tail | ~80 | 5–8 days, individually small but numerous |
| 5 | `environment` — decide per group: provide `node` (free in both CI images), sync the export runtime, decide whether the OSB reference is committed, whether Gate E gets real workers, whether PostgreSQL is a service | 26 | 1–3 days once the decisions are made |

Total to green, on this runner's evidence: roughly **2–4 engineer-weeks**, not counting whatever
the 59 Chrome-gated tests do the first time they actually execute.

### PROPOSAL 4b — is a non-blocking gate safe now?

**Yes, and it is the right next step**, with three conditions:

1. It must be `continue-on-error` (or a separate non-required workflow) and it must **not** be
   added to the Bitbucket required set, since `origin` is Bitbucket and that is where merges
   are gated.
2. It must carry a per-test timeout (PROPOSAL 4a step 0). Without one, the deadlock in §1a
   turns a non-blocking job into a job that burns the full runner timeout on every run.
3. It should publish the failure count as an artifact so the number can be ratcheted. A ratchet
   on "228 or fewer" is enforceable today and would immediately stop new regressions, which is
   most of the value of a blocking gate at a fraction of the cost.

A **blocking** gate should not be proposed until steps 1–3 above are done and the Chrome-gated
59 have been run once on the real image. Turning it on earlier would block every merge on
pre-existing debt that no one on this fork has ever been asked to carry.

### PROPOSAL 4c — two things worth doing regardless of any gate

- G1 is a single-line-class fix that restores 51 tests' worth of real coverage over the
  authored worker lane. That coverage is currently *claimed* and not *delivered*, which is worse
  than having no test, and it is independent of whether CI ever runs pytest.
- G9's `except Exception` at `services/slidespec_authored_importer.py:1103` makes its own
  failures undiagnosable. Preserving the cause costs almost nothing and would let the 9 tests
  be triaged properly.

## 5. Limitations

This runner is **not** the CI image, and the differences cut in both directions.

- **Image.** Bitbucket's shared steps use `cypress/included:15.19.0` plus apt `python3` and
  `uv 0.11.17`; the GitHub `test-fastapi` job uses `ubuntu-latest` with `astral-sh/setup-uv`
  and apt `libreoffice fontconfig chromium chromium-driver imagemagick`. This runner is a bare
  WSL Ubuntu with **no node, no chromium, no libreoffice, no imagemagick, no tesseract**.
- **What that could hide (failures that may not be real on CI):** all 15 of G3 (`node`), the
  1 LibreOffice-gated skip, and plausibly some or all of G1's 51 — a present Chromium might let
  the unstubbed seam succeed.
- **What that could hide (failures CI would have and this run does not):** the 59 Chrome-gated
  tests skipped here would *execute* on the CI image. They have never run in CI or, as far as
  this measurement can tell, anywhere automated. The real CI failure count could be higher than
  228, and there is no basis here for guessing by how much.
- **Not affected by the image:** G2 (no Linux image has Malgun Gothic), G4, G5, G6, G8, G14,
  G19, G20, and the §2b tail. These are the buckets this measurement establishes with confidence.
- **Fork state.** The clone is committed state at `d25d91c35`, which is correct for a gate but
  means untracked local assets (the OSB reference PPTX, the synced `presentation-export`
  runtime, a repository `.env`) are absent. Three groups (G7, one §2b singleton, one skip) are
  attributable to that and would behave identically in CI.
- **Sequencing.** `tests/unit` ran as 13 invocations rather than one, so per-test ordering
  differs from a single-invocation CI run. The 174 unit failures are stable across that split
  in aggregate, but the §1a deadlock demonstrates that this suite is *not* order-independent,
  and a single-invocation CI run may produce a different result.
- **Concurrency.** One `tests/integration` re-run (for skip reasons only) overlapped the tail
  of the unit chunks. Both share `DATABASE_URL=sqlite+aiosqlite:///./test.db` in the same
  working directory. The failure counts reported in §1 come from the isolated first runs; the
  overlapping run was used only to read `-rs` skip reasons.
- **Runs.** Every number here is from a single run. No test was retried, and flakiness was not
  measured.
