# Public scale-out: development-stage plan

- Date: 2026-09-12
- Status: Proposed, **v2** — rewritten after three pre-implementation reviews
  traced every package against HEAD. v1 scheduled work that was already done
  (B), already resolved (B′ row 1), unreachable (A), or impossible as written
  (F attestation, D default-on). This version lists only work that exists.
- Basis: `docs/public-launch-readiness-assessment-20260911.md` (v3) and the
  accepted target architecture in
  `docs/public-generation-export-service-architecture-20260830.md`
- Decisions taken by the owner (2026-09-12):
  - Authentication is owned by another service. This repository keeps its
    OIDC principal binding and builds no login, signup, or admin UI.
  - The API runs on Linux in operations. Development stays on Windows.
  - Launch-time work — release evidence, approval gates, ingress defence,
    malware-scanned images — is listed in section 6 and not scheduled here.
  - Nothing here may break the Windows development stack
    (`scripts/start-local-stack.ps1`).

## 1. What is already true at HEAD (do not re-implement)

| Claimed gap in earlier drafts | Actual state | Evidence |
|---|---|---|
| Generation admission is flag-gated | Always on. `_chat_first_generation_enabled()` returns `True` for SQLite/PostgreSQL, 501 otherwise; the `else` branches are dead | `presentation_generate_admission.py:19-24`; `presentation_generate.py:1943-1949, :2134-2141` |
| UI export runs Chrome in Next without admission | Next route only admits (202 + receipt) and forwards to `POST /workspace/presentations/{id}/commands`; handler fails closed without an active admission; execution is the isolated worker | `app/api/export-presentation/route.ts:15-77` (commit `3a9690152`); `workspace_service.py:1802-1810`; `workspace_quality_export_operation_handlers.py:594-608, :685-687` |
| Isolated child needs network | It does not: Fetch interception + `Network.setBlockedURLs`, `Page.setDocumentContent`, fonts from a staged allowlist directory, raster from a staged PNG bundle | `chrome-runner.ts:215-238`; `public_export_hybrid.py:96`; `public_export_worker.py:49` |
| python-parent export is 2.2× slower (stdio) | Resolved: cause was Chrome teardown (`taskkill` + process-table scan); fixed in `6cc0b78ce`; single slide now 22–28 s, same as node parent. stdio→file controls were measured and had no effect | `run-11-hybrid-export/w20-report.md` |
| Provider credentials are plaintext in app_data | They are in a vault with dev (DPAPI) and ops (AWS Secrets Manager / KMS) backends | `services/provider_credential_vault.py:456-477` |

## 2. Out of scope until launch

| Item | Why it waits | What exists |
|---|---|---|
| Login, signup, IdP | Owned by another service | `api/v1/auth/principal.py` + `SessionAuthMiddleware`; `PPT_PUBLIC_OIDC_*` |
| Release matrix, soak, Ed25519 evidence | Approval process | `scripts/public-service-release-matrix.mjs`, `public-service-release-gate.mjs` |
| Ingress rate limiting, CORS | Deployment configuration | `CORS_ALLOWED_ORIGINS`; nginx/ingress `limit_req` |
| Malware-scanned production image | Image build | `docker-compose.chat-first-canary.yml` |
| Redis / broker | No claim-latency evidence | PostgreSQL `SKIP LOCKED` queue |
| Concurrency *values* | Need the organisation provider key | `ProviderRuntimeBudget` |

## 3. Work packages

### A. Dead generation branch and its two orphans

| Change | Location |
|---|---|
| Delete the unreachable sync `else` in `queue_presentation_generation` and the `else: background_tasks.add_task(launch_generation_job, …)` after `wake_workspace_operation_runner()`. **Keep `launch_generation_job` itself** — it is live on the two idempotency-retry paths (`existing.state == "awaiting_resume"`, ~:1788 and ~:2136) | `presentation_generate.py` ~:1949, ~:2144-2149 (line numbers drift; another session is restructuring this block on 2026-09-12 — coordinate before editing) |
| Test monkeypatches: none force the dead branch (all patch `_chat_first_generation_enabled` to `True` or assert it); re-check after the `else` deletion | `tests/unit/test_chat_first_runtime_admission.py`, `test_chat_first_generation_integration.py` |
| `CHAT_FIRST_WORKSPACE_ENABLED` / `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED` are read by no app code. Occurrences to clean: `servers/nextjs/scripts/run-workspace-e2e.mjs:3157` (not root `scripts/`), `tests/support/workspace_browser_live_harness.py:78`, `tests/integration/test_workspace_production_lifecycle_gate.py:382`, `tests/integration/test_workspace_gate_e_vertical_flow.py:99`. Leave `tests/unit/test_server_repository_environment.py:57-75` — it uses the name only as a sample for the `.env` loader | listed |
| **Decision needed — pre-workspace decks.** A presentation without a `WorkspaceOperation` bootstrap row already gets 409 `chat_first_bootstrap_operation_not_found` (`presentation_generate.py:1820-1835`). No backfill exists. Either write one Alembic data migration or document that such decks are read-only | new migration or `RUNNING-LOCAL.md` |
| **Decision needed — sync CLI on PostgreSQL.** `scripts/ppt-agent.mjs` default (non-`--async`) hits `_require_local_sync_generation` → 410 on any non-SQLite runtime (`presentation_generate_admission.py:27-34`), i.e. it is already broken on the standard Windows stack. Make `--async` the default or route the CLI to `/api/v2/presentations/generations` | `scripts/ppt-agent.mjs`, its test |

Days. No product-path behaviour change.

### B. Finish export: the three pieces that are not done

| Change | Location |
|---|---|
| **Decision needed — generation-time `export_as`.** `utils/export_utils.py:185-220` posts to `/api/export-presentation` expecting `path`; the route returns 202. Chain: `presentation_export_adapters.py:40-44` → `presentation_export_router.py` → `presentation_generation_export_orchestration.py:347-351` → `presentation_generate.py:241`; CLI contract `scripts/ppt-agent.mjs:157, :420, :453` reads `/app_data/exports/…`. Options: (i) drop `export_as` and have the CLI submit an export command after completion, or (ii) auto-chain an `export.pptx/pdf` command on generation completion. (ii) keeps the CLI contract; choose it unless the sync path is being retired anyway | `utils/export_utils.py` (delete after), `presentation_export_adapters.run_general_adapter`, `ppt-agent.mjs` |
| `admit_public_operation` reads the global scope on export | `services/public_operation_admission.py:569, :649-660, :1495-1505` |
| `PublicExportIdentity` becomes authority: remove the `PUBLIC_EXPORT_IDENTITY_V2_ENV` gate at its five sites; key is the existing unique constraint (owner, tenant, presentation, revision, scene/semantic/quality/exporter digests, export mode, output mode, font manifest, render adapter) | `services/public_export_operation.py:952, :992, :1024, :1109, :1406`; `services/public_export_identity.py:75-88`; `models/sql/public_artifact.py:270-282` |
| Split PPTX **import** conversions out of `export_task_service` before any export deletion: `convert_pptx_to_json`, `convert_pptx_to_html`, `extract_schema` (`export_task_operations.py:65, :97, :228`) are used by `template_v2_pptx_ingestion_service.py:26` and `template_v2_pptx_analysis_service.py:242` → `structured_template_imports.py` | new `services/pptx_import_conversions.py` |
| Then delete the direct attempt path and its gates: `export_task_service.py`, `export_task_operations.py`, `export_task_runtime.py`, `utils/export_utils.py`; `proxy.ts:86, :100-109, :161` (`/pdf-maker?exportSession=` cookie hand-off); `.github/workflows/template-v2-export-fidelity.yml:11` path filter; `lib/export-fidelity/*.e2e.test.mjs`, `lib/run-bundled-presentation-export.e2e.test.mjs`; FastAPI tests `test_presentation_export_router.py`, `test_presentation_export_attempt_service.py`, `test_export_task_pptx_to_json.py`, `test_authored_fidelity_export_utils.py` and siblings | listed |
| `hooks/useHybridExportWarmup.ts:83` — the speculative warmup already submits a command; decide keep or delete | `servers/nextjs/hooks/useHybridExportWarmup.ts` |
| **Keep** `lib/authored-hybrid/export-admission.ts`: it is worker-internal capture admission (`chrome-runner.ts:27, :63, :355` → `index.ts:10` → `scripts/public-hybrid-export-worker.mjs`), not the Next route. Move it under a worker-only path if the layout matters | — |
| `pptx_mode` defaults to `fidelity` (`public_export_operation.py:116`); hybrid needs the request field. Decide whether hybrid is the product default | `public_export_operation.py:116`, header command payload |

Not touched: `pptx_slides.py`, `pdf_slides.py`, `presentation_share.py` (no
export-module imports), `electron/` (version contract only).

**Windows prerequisite for running the export kind locally.** The export
executor is composed only under `PUBLIC_V2_INTEGRATION_PROFILE=postgresql-strict`
(`api/lifespan.py:215-224`) with `PUBLIC_TRUSTED_WORKER_STAGING_ROOT`,
`PUBLIC_WORKER_NODE_ID` (`composition.py:157-163`),
`PUBLIC_DATABASE_PROCESS_ROLE=controller` (`database.py:259-269`),
`WORKSPACE_OPERATION_WORKER_MODE=external`,
`WORKSPACE_OPERATION_EXTERNAL_CONTROLLER=export` (`workspace_operation_worker.py:151-160`).
`scripts/start-public-v2-rc.ps1:313-324, :418` injects all of them;
`start-local-stack.ps1` must too, or in `embedded` mode
`install_trusted_worker_dispatcher(None)` (`isolated_worker_process.py:113-116`)
fails closed and every export kind fails. Removing the lifespan gate requires
either excluding export kinds from embedded mode or composing the executor
unconditionally.

**Linux.** B on Linux depends on F (section 4). There is no non-Windows
sandbox backend today.

### B′. Per-slide export cost: Chrome launches per slide

Baseline is post-fix W22: 25.5 s/slide, 35/35 native (`w22-report.md`). The
stdio hypothesis is closed (section 1). The remaining lever is the number of
Chrome launches per slide — `extractor.ts:65` spawns a fresh Chrome per call
(`chrome-runner.ts:337`):

| Launch | When | Location |
|---|---|---|
| observation *or* layout extraction (`??`, one not both) | always | `slide-layer.ts:337-345` |
| backplate | always | `slide-layer.ts` |
| re-render, up to 2 | selection-set mismatch or opaque conflict | `slide-layer.ts:574, :623` |
| raster band × N | `audited-multi-z-band` only | `slide-layer.ts:649-667` |
| render-role raster | separate role | `public_render_worker.py:114-116` |

| Change | Location |
|---|---|
| Remove the re-render loop: settle the final selection set before the first backplate | `slide-layer.ts:520-580, :604-640` |
| Take the N raster bands as successive screenshots in one Chrome session; `contract.ts:708-745` constrains PNG properties, not session count | `slide-layer.ts:649-667` |
| Observation + backplate from one session with two screenshots, preserving the `capturedPngDigest` binding | `slide-layer.ts:357` |
| Re-measure with `w17-measure.py` before any container count is chosen | `artifacts/.../run-11-hybrid-export/w14-direct-repro/` |

Independent; Windows harness. The container count in F is sized after B′.

**Evidence added 2026-09-12 (read before starting B′).** A live Hybrid export
regression under host load (another session editing ~40 files and running
tests concurrently) showed the run-21 deck falling back on **28/35 slides with
`chrome-teardown-timeout`**, and **833 s of the worker's 834 s was
`exportSchedulerQueueWait`** — `artifacts/authored-full-product-validation-20260910/run-11-hybrid-export/49-regression-20260912.md`
and `run-21-hybrid-export/50-*` … `54-*`. A quiet-host re-measurement is
pending. Implication: before reducing launches per slide, B′ must first
decompose the queue wait (scheduler `global_cap`, capture admission 12,
teardown-timeout fallback path) — launch count is not the first-order term if
the slide is waiting, not rendering.

### C. One export deadline — in this order

Today: policy `export_deadline_seconds = 600` in every version
(`f9c3a7d1e5b8:105`; v2/v3/v4 copy the column), enforced by the lease
(`workspace_operation_lease.py:113`) and checked at completion
(`workspace_operation_runner.py:2614` → `_public_deadline_unavailable` `:1084`);
hybrid worker grant `HYBRID_EXPORT_DEADLINE_SECONDS = 1740`
(`public_export_operation.py:521, :1957`). This is an **existing defect**: a
35-slide hybrid export (894 s) already outlives its admission.

Order matters. Deriving the grant from the admission *before* the migration
cuts hybrid from 1740 s to 600 s (~23 slides).

| Step | Change | Location |
|---|---|---|
| 1 | Inject `planned_slide_count` when the export command is submitted (today `request_plan=None` → 0) | `workspace_service.py:1809`; `public_operation_admission.py:783-787` |
| 2 | Migration: `export_deadline_seconds` becomes the ceiling (1800) | new Alembic revision after `a2c4e6f8b1d3` |
| 3 | `_policy_deadline_seconds` scales export by slide count (per-slide seconds + fixed overhead, capped by the ceiling) | `public_operation_admission.py:175-196`; constants beside `public_generation_deadline_policy.py` |
| 4 | Worker grant = `admission.deadline_at - now`; `render_and_export_authored_htmls` has `claim.operation_id` + `session_factory` (`:1240`) to load the admission row; delete the 1740 constant; decide whether non-hybrid 600 / render 120 (`:571-578`) derive from the same source | `public_export_operation.py:521, :571-578, :1957`; tests `test_isolated_public_workers.py:788, :999, :1021` |

Executor 1800 cap (`worker_dispatcher.py:91, :103`) stays as the outer `min()`.

### D. Concurrency knobs: register, route through the access layer, derive

| Change | Location |
|---|---|
| Add `get_env.py` accessors and rewrite the three raw reads (`_positive_int_env` / `os.getenv`): `AUTHORED_CLUSTER_JOB_CONCURRENCY`, `AUTHORED_JOB_CONCURRENCY` (`presentation_generation_job_service.py:35-49`, `authored_presentation_service.py:53`), `WORKSPACE_OPERATION_MAX_CONCURRENCY` (`workspace_operation_runner.py:4230-4233`) | `utils/get_env.py`, three call sites |
| Register the three in `settings_registry.json` (the generator does not emit Python; accessors are hand-written) | `constants/settings_registry.json`, `npm run generate:settings-registry` |
| Fourth knob for `SchedulerCaps.global_cap` (hard-coded 4, no env) so `global_active_export` has something to derive from | `services/trusted_executor/composition.py:36-46` |
| `global_active_generation` ← cluster job concurrency; `global_active_export` ← scheduler global cap, via `_effective_runtime_quota_limits` | `public_operation_admission.py:394-434` |
| **Do not default `PUBLIC_RUNTIME_PROFILE` on** until `start-local-stack.ps1` sets its five required inputs (`PPT_RUNTIME_PHYSICAL_MEMORY_BYTES` ≥ 120 GiB, `PUBLIC_EXECUTABLE_{GENERATION,EXPORT}_SLOTS`, `PUBLIC_PROVIDER_{GENERATION,EXPORT}_CONCURRENCY_CAP`); missing inputs raise `PublicWorkstationProfileError` → every admission rejected (`public_workstation_runtime_profile.py:193-222`, `public_operation_admission.py:407-410`). Either add them to the stack script or make an unset profile mean "no clamp" | `scripts/start-local-stack.ps1` or `public_workstation_runtime_profile.py` |

All three values are read once at import/runner creation; a runtime-settings
change needs a restart. Document that or move the reads behind the coordinator.

### E. Session secret and remaining local-path consumers

Provider credentials are **not** in scope — the vault already separates dev
(DPAPI) and ops (Secrets Manager/KMS) backends and feeds the `CAN_CHANGE_KEYS`
admin flow (`api/v1/admin/router.py:90, :106-113`). Forcing env-only would
break that flow.

| Change | Location |
|---|---|
| Unify the two auth-secret implementations first: `get_or_create_auth_secret` (`api/v1/auth/config.py:71-81`, used at `users.py:178`) and the separate `_get_or_create_auth_secret` in `utils/simple_auth.py:96, :134, :190` | `utils/simple_auth.py` → `api/v1/auth/config.py` |
| Then source `AUTH_SECRET_KEY` from env with a redesigned rotation story: `bootstrap.py:89-93` (`RESET_AUTH` / `AUTH_OVERRIDE_FROM_ENV` → `persist_admin_credentials(rotate_secret=True)`) rewrites the secret at runtime, which env cannot do. Options: rotation becomes an operator action (new env + restart), or the secret moves to the vault backend | `api/v1/auth/bootstrap.py`, `config.py` |
| `ArtifactRef` cutover for the seven live consumers — all confirmed on the product path; `workspace_artifact_publication_service.py` has 9+ callers and is the largest | `authored_checkpoint_artifacts.py`, `template_v2_pptx_storage.py`, `template_v2_pptx_worker.py`, `workspace_artifact_publication_service.py`, `export_retention_service.py`, `icon_finder_service.py`, `source_locked/job_store.py` |

Development keeps `PUBLIC_ARTIFACT_STORE_BACKEND=local-development`;
operations sets `s3`. Two to three weeks, not one to two.

### F. Linux launcher backend for the trusted executor

This is new code across three layers. v1's "attest via env manifest" is
forbidden by the code's own invariants — `isolation_policy.py:6-7, :131-141`
("configuration names and environment variables cannot make it true") and
`wire_transport.py:299-301` ("child environment markers are never accepted as
isolation evidence") — and there is no non-Windows process launch at all:
`OperationProcessSupervisor.__init__` raises for any backend other than
`windows-job-object` (`process_supervisor.py:385-387`), `launch()` is
`CreateProcessW` only (`:566-700`), `composition.py:93-96` raises
`IsolationUnavailableError` for anything but `windows-local`.

| Layer | Change | Location |
|---|---|---|
| Composition | `PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND=container`; a production composition path (none exists today) | `services/trusted_executor/composition.py:93-128` |
| Supervisor | A container supervisor: create / attach / wait / kill through the Docker or Kubernetes API; stdio over the attach stream; `PlatformIsolationReport` built from the runtime's own inspect output (network none, memory, pids, read-only root) — evidence the parent verifies, not a declaration the child makes | new `process_supervisor_container.py`; `isolation_policy.py:209-215` reports SUPPORTED from that evidence |
| Transport | Path contract generalised from "same filesystem" to "parent path ↔ container mount path": inputs staged to `PUBLIC_WORKER_INPUT_STORE_ROOT`, outputs read from `sandbox_root/output` with digest verification (`wire_transport.py:239-241, :262, :293-295, :337-342`). `output`/`tmp` must be a **shared writable volume** (so `read_only: true` applies to root only); `WorkerArtifactTransportCache` is a local-disk cache (`artifact_store.py:849`) mounted at the same absolute path | `wire_transport.py`, compose/K8s spec |
| Capability key | `PUBLIC_WORKER_CAPABILITY_KEY_FILE` (`public_worker_runtime.py:86`) is read inside the child; reconcile with `secrets_allowed=False` (`contracts.py:107`) — likely a per-job short-lived key mounted read-only | `contracts.py`, `public_worker_runtime.py` |
| Telemetry | `supervisor.telemetry`, `wait_for_zero`, memory/deadline exceptions are Job-Object based; redefine on cgroup stats and exit codes | `process_supervisor.py`, `worker_dispatcher.py` |
| Image | python + node + Chrome; `pids_limit` ≥ 96 (W17 peak 81) | `Dockerfile` export target |
| Close the two unenforced limits (see assessment 3.4a): add `HostConfig.Ulimits` `nofile` from a new `ResourceLimits.open_file_limit`, and verify the output-bind size in the supervisor instead of trusting the child's self-limit | `contracts.py` (`ResourceLimits`), `container_supervisor.py`, `wire_transport.py` output collection |

Windows untouched. Test in WSL2/Docker or a Linux CI job. **Three to four
weeks**, not one. B on Linux depends on this.

### G. Quality — first when staffing is shared

Adopt placeholder authoring per
`docs/authored-verbatim-placeholder-authoring-design-20260911.md` §2.1; stop
adding per-path readback validators. Target 95% publish rate under the
**default** flags. At 38% every other package is measured on top of failures.

## 4. Order and dependencies

```
G  placeholder authoring           (independent; first if staffing is shared)
B′ Chrome launches per slide       (independent; before any container count)

A  dead branch + 2 decisions ──┐
C  one export deadline (1→4)  ─┼─→ B  finish export (export_as, global scope, identity, deletions)
D  knobs via access layer     ─┘            │
E  auth secret + ArtifactRef  ←─────────────┘  (E's export consumers vanish with B)
F  container launcher backend (3 layers) ─────→ B on Linux ─→ Linux deploy
```

| Package | Size | Depends on |
|---|---|---|
| G | separate track | — |
| B′ | ~1 week | harness exists |
| A | days | two owner decisions |
| C | ~1 week | strict internal order |
| D | days | stack script or profile fallback |
| B | 2 weeks | A, C, D; one owner decision (`export_as`) |
| E | 2–3 weeks | B |
| F | 3–4 weeks | Linux/Docker access; sized after B′ |

## 4.1 Owner decisions — taken 2026-09-12

1. Pre-workspace decks: **read-only** (view/export allowed, regenerate keeps 409; no backfill) (A).
2. `ppt-agent.mjs` sync mode on PostgreSQL: **`--async` becomes the default** (A).
3. Generation-time `export_as`: **auto-chain** an `export.pptx/pdf` workspace command on generation completion; CLI contract kept (B).
4. `pptx_mode`: **hybrid is the product default** (B).
5. Auth secret: **credential vault backend** (dev DPAPI / ops Secrets Manager), runtime rotation kept (E).

## 4.2 Unknowns only measurement answers

- Timeline SSE across N API replicas: events are in the database
  (`workspace_operation_events`); the polling load at which a fan-out layer is
  needed is unmeasured — that, not queue depth, is the first plausible reason
  for Redis.
- Database growth: SQLite reached 9.5 GB; whether `workspace_cleanup_*` and
  `export_retention_service.py` keep pace on PostgreSQL under multi-user load
  is unmeasured.

## 5. Windows development invariants

- `scripts/start-local-stack.ps1` owns the stack: PostgreSQL 21000 → API 8000
  → generation and export controllers → Next 3000 → proxy 5000. It must gain
  the strict-profile inputs (section 3.B) and, if D enables the runtime
  profile, its five inputs.
- uvicorn `--reload false`; `APP_DATA_DIRECTORY` and `USER_CONFIG_PATH`
  required; `server.py`'s own `.env` loader.
- `DISABLE_AUTH=true` / loopback development principal keeps working.
- Job Object dev-gap isolation stays the Windows export backend.
- `PUBLIC_ARTIFACT_STORE_BACKEND=local-development` stays the development
  artifact backend.
- New knobs default to current behaviour.

## 6. Attached at Linux deployment (not scheduled here)

`PPT_PUBLIC_OIDC_*`; ingress `limit_req` / `limit_conn` and SSE caps;
`CORS_ALLOWED_ORIGINS`; ClamAV image with `WORKSPACE_ASSET_MALWARE_SCAN_MODE=required`;
`PUBLIC_ARTIFACT_STORE_BACKEND=s3`; pgbouncer or `DB_POOL_SIZE` per replica
count; organisation provider key → final concurrency values; release matrix
and soak.

## 6.1 Debt noted while implementing

- `services/authored_vnext/integration/export_service.py` is frozen at 1064
  lines. Wiring the async repository facade into it (commit `b3e65902c`) fit
  under the ratchet only by folding two names onto one line in an import list
  and an `except` tuple. The policy answer is to extract when touching a frozen
  exception; it was deferred because another session was editing the same
  subsystem. Extract it the next time it is substantively changed.
- `AsyncAuthoredVNextSqlRepository` existed in `sql_repository.py` with no
  callers before that commit. Check for an existing facade before writing one.
- The UI duplicate-export guard (commit `036f6b48e`) threaded an
  `activeExportOperationType` value from `WorkspaceChatPanel` → `PresentationPage`
  → `PresentationHeader` → `ExportMenu`, adding 5-6 lines to each of three files
  that are already frozen ratchet exceptions, and loosening their baselines
  (505→511, 1069→1073, 917→922). The logic itself was extracted into a hook in
  `workspaceChatPanelUtils.ts`, so only the prop threading remains. The better
  shape is for `ExportMenu` to consume the timeline state directly (context or
  the hook) instead of being passed it through two ancestors — that removes the
  threading and lets all three baselines go back down. Do it the next time any
  of those three components is substantively changed; relaxing a size baseline
  to add prop drilling is exactly the pattern the ratchet exists to stop.

## 6.2 What landed on 2026-09-12

Verified against the product path where the note says so; otherwise verified by
focused tests only.

| Package | Landed | Evidence |
|---|---|---|
| A — dead generation branches | yes | `presentation_generate.py` 2254→2240; the unsupported-runtime 501 guard was found untested and restored (`8cb45a583`) |
| B — export as an operation | mostly | PPTX-import conversions split out (`62ab21d50`); export identity made cluster authority, flag removed (`b53975fc0`); global quota read on the export path (`80b148b28`); join-the-in-flight (`1ea7e882e`) plus a UI duplicate guard (`036f6b48e`); `export_as` now chains a workspace command (`db8e6b5a2`) and the legacy in-request export path is deleted (`1ae1228c4`). Deferred: `export_task_service`/`export_task_operations` (test-only now), `authored_export_quality_flag` (orphaned), two Next e2e specs |
| B′ — per-slide export cost | no | Re-scoped after the run-21 regression showed 833 s of 834 s was scheduler queue wait; decompose the wait before cutting launches |
| C — one export deadline | yes | Slide-count scaling (`74e6ea503`), worker grant derived from the admission row and the 1740 s constant deleted (`aaa953571`); policy v5 ceiling (`4e9eebfdd`) |
| D — concurrency knobs | yes | Accessors + registry (`da117155b`, `9453f3d85`); global quota clamped to the knobs (`cf5b3a5f1`, restored as `c5b09413f`) |
| E — secrets and storage | partly | Session signing secret unified and vault-backed **with a fallback** when no backend is configured (`5f52bb4cf`), plus a `local_file` dev backend. The `ArtifactRef` cutover for the seven local-path consumers is not done |
| F — container isolation | yes (code) | Supervisor, runtime, isolation evidence, stdio, composition (`d08a10423`…`3f4c93166`); isolation is proven from `container.inspect()`, never from configuration. No Docker run yet; the `docker` SDK is still not a dependency |
| G — quality | other session | Placeholder authoring shipped there |

Three defects were found and fixed while doing the above, each verified by
reverting the fix to confirm the test catches it: an expired account-deletion
claim raised `TypeError` instead of being reclaimed; the export repository was
called synchronously on the event loop; and a constraint trigger derived the
global quota expectation from leases while the phase-1 path creates none —
that last one was introduced by `80b148b28` in this session and rejected a
finished 35-slide deck at COMMIT after 761 s. Fixed in `ca874fbb5` and
**verified on the product path**: the same fixture now publishes
(`publication=4.3s`, zero drift errors).

The database-revision contract (`50f6c5c34`, `db7f0518b`, `5a78c9afb`) came out
of that incident: a schema mismatch now refuses at startup, from one
implementation called at each process boundary, with a remedy the connected
role can actually perform.

## 7. Not doing

Rewrite of FastAPI/Next; an in-process namespace launcher (bwrap/nsjail —
same three layers of new code as F with a second enforcement implementation);
env-manifest attestation (forbidden by the isolation invariants); moving
provider credentials out of the vault; microservice split of control or
quality modules; broker before claim-latency evidence; deleting
`export-admission.ts` (worker-internal); exposing non-isolated export to
multiple users.
