# Public web launch readiness assessment

- Date: 2026-09-11 (evidence set); written 2026-09-12
- Revision: v3 — v2 corrected v1 after four independent reviews; v3 corrects
  v2's section 3.1 and 4.3 after three pre-implementation reviews and direct
  reads showed the admission control plane is live on the product path. Every
  refuted claim is replaced, not softened.
- Scope: performance, scalability, and operational readiness of opening this
  fork as a multi-user web service
- Method: code and migration inspection cross-checked against measured runs;
  no service was started, restarted, or altered
- Scoring frame: the five approval axes of
  `docs/public-generation-export-service-architecture-20260830.md` section 14
- Verdict: **56 / 100**. Not launch-ready. The admission control plane is
  live on the product path (generation and UI export, owner/tenant scope), but
  global quota and export single-flight are not yet authority, no OS composes
  a production-mode isolated export, the generation cluster is serialized to
  one deck by default, and the publish rate on identical input is 38%.

## 1. Scorecard

| Axis | Target | v1 | **v2** | Deciding evidence |
|---|---:|---:|---:|---|
| Architecture | 88 | 82 | **74** | Admission live for generation and UI export (owner/tenant); global scope and single-flight not yet authority; no production-mode isolation composition on any OS |
| Security | 90 | 80 | **72** | Login throttle exists; no general rate limit, no malware scan on stock image, session secret on shared disk |
| Observability | 85 | 70 | **62** | Slide-level failures persist, but no CI executes the 7,522-test Python suite (3.1a), so nothing is observed automatically |
| Performance / scalability | 85 | 58 | **50** | Cluster generation defaults to 1 deck; every 5-slide product run on record failed at admission |
| Business reliability | 87 | 45 | **40** | Publish rate 5/13 = 38% on identical input, under a flag whose default is stricter |

## 2. Measured performance

Fixed 35-slide PPTX input, `content_policy.text=verbatim`,
`AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW=true`, supervised local stack,
runs executed one at a time. This is the **default path for a PPTX upload from
the web UI** (`useUploadPageState.ts:250-252`, `source-content/contract.ts:63-64`
send `source_content_restyle` + `verbatim`), not an edge case — but it is a
single input, single model, so it is a floor, not a distribution.

| Stage | Measured | Source |
|---|---|---|
| Generation | 736.2 s / 35 slides = **21.0 s/slide** | `artifacts/authored-full-product-validation-20260910/repeatability-20260911.md`, run-11 |
| Hybrid export, isolated worker | 893.8 s / 35 slides = **25.5 s/slide**, 35/35 native | `.../run-11-hybrid-export/w22-report.md` |
| Export process footprint | peak **81 processes per deck** at capture admission 12 (~6.75 per capture) | `.../run-11-hybrid-export/w17-throughput.md` — an earlier run (24/35 completed, old budget scheduler), **not** the same execution as the 893.8 s figure |

Small decks: there is **no successful 5- or 10-slide product-path run in the
repository**. Every attempt on record failed before generation started
(`workspace_request_failed` at admission):
`artifacts/authored-vnext-controlled-load-20260829/{public-service-5slide-live-20260830,
current-5slides-c1-capacity-fix-20260830, current-5slides-c2-20260830,
score-lift-5slides-c1-20260830, score-lift-owner-reducer-5slides-c1-20260830}`,
all `succeededOperations: 0`. The only successful small runs are one-slide decks
(`actual-load-v2`, C2, 222 s; `actual-soak-120s`, 875 s wall).

The 32-cell load matrix and the 24-hour soak have never been executed. The
approval gates (5 slides ≤ 180 s p95, 10 slides ≤ 360 s p95) therefore have no
measurement on either side; a linear projection from the 35-slide rates gives
~230 s and ~465 s, both misses, but the honest statement is that the SLA is
**unmeasured, and the runs that tried to measure it did not get past
admission**.

### 2.1 Throughput ceilings that worker count does not change

- `services/presentation_generation_job_service.py:44-48` —
  `AUTHORED_CLUSTER_JOB_CONCURRENCY` defaults to `AUTHORED_JOB_CONCURRENCY`
  which defaults to **1**, enforced as a database-backed cluster-wide gate. The
  comment states the intent: adding workers must not multiply provider spend.
  Consequence: with defaults, **the whole cluster generates one deck at a
  time** no matter how many generation workers run.
- `services/authored_presentation_service.py:53` — in-process semaphore, also 1.
- `services/workspace_operation_runner.py:128` — `WORKSPACE_OPERATION_MAX_CONCURRENCY` default 2.
- None of these three knobs is in `constants/settings_registry.json` or the
  README; an operator has no way to discover them.
- `global_active_generation` policy default is **20**
  (`alembic/versions/fb5e9c3a7d10_add_public_control_plane.py:728`) — twenty
  admitted, one running. Same shape as the export mismatch in 3.3.

v1 claimed "generation workers already scale horizontally". The claim mechanism
(`SKIP LOCKED` row claims, lease/fencing) is real; the throughput does not scale
until the cluster gate is raised deliberately.

## 3. P0 findings

### 3.1 The control plane is active on the product path — with two gaps

v2 of this document said the admission control plane was gated off and export
bypassed it. Both were wrong, and are corrected here from direct reads.

- **Generation is admitted.** The `if chat_first_enabled:` at
  `presentation_generate.py:1807` is not a flag: `_chat_first_generation_enabled()`
  (`presentation_generate_admission.py:19-24`) returns `True` for every SQLite
  or PostgreSQL runtime and raises 501 otherwise. The `else` branches at
  `:1943-1949` and `:2134-2141` are unreachable dead code.
  `CHAT_FIRST_WORKSPACE_ENABLED` is referenced only by tests and an e2e script.
- **UI export is admitted.** `servers/nextjs/app/api/export-presentation/route.ts:15-77`
  launches nothing; it forwards to
  `POST /workspace/presentations/{id}/commands` and returns 202 + receipt
  (commit `3a9690152`). The header submits `export.pptx` / `export.pdf`
  workspace commands. `workspace_service.py:1802-1810` calls
  `admit_public_operation` unconditionally for export kinds, and the handler
  (`workspace_quality_export_operation_handlers.py:594-608`) fails closed
  without an active admission. Execution is the isolated worker
  (`isolated_hybrid_v1` / `isolated_raster_v1`) — the UI path and the
  workspace path are the same path and the same editable-hybrid artifact.

The two real gaps:

1. **Global scope is not read on this path.** Phase-1 `admit_public_operation`
   (`public_operation_admission.py:569, :649-660, :1495-1505`) checks owner and
   tenant counters only. `global_active_generation/export` are read by the v2
   admission (`:847-856`, `:1232-1241`), which the standard route reaches for
   generation (`_admit_generation_operation_v2_compatible`) but not for export.
2. **The generation-time `export_as` path is broken or legacy.**
   `utils/export_utils.py:185-220` posts to `/api/export-presentation` and
   expects a `path` in the response; the route now returns a 202 receipt.
   This path serves `POST /generate` with `export_as` (used by
   `scripts/ppt-agent.mjs --export`) behind `legacy_path_only_rolling_facade`.
   It needs a decision (drop `export_as`, or chain an export command on
   generation completion), not a fix.

`WeightedExportAdmission` (`lib/authored-hybrid/export-admission.ts`) is
consumed by `chrome-runner.ts` inside the isolated worker
(`scripts/public-hybrid-export-worker.mjs`), not by the Next route; it is
worker-internal capture admission and is correct where it is.

### 3.1a No CI runs the Python test suite on the real remote

Found 2026-09-12 after a test was discovered red at the very commit that
introduced it. Three independently sufficient causes, each verified directly:

| Fact | Evidence |
|---|---|
| `origin` is Bitbucket; GitHub push is disabled | `git remote -v` → `origin https://bitbucket.org/ibizsoftware/devax-ppt`, `upstream … DISABLED (push)`; the repo says so itself in `.github/workflows/README.md:106` |
| The only pytest invocation in the repository is in a workflow that does not run | `.github/workflows/test-all.yml:63-67` is the sole `pytest` call anywhere |
| The pipeline that does run has no Python test gate | `bitbucket-pipelines.yml` contains **zero** occurrences of `pytest`; its Python steps are `ruff check .` and a contract `--check` (`:49-50`) |
| Commits reach `main` without a pull request | no PR-merge titles in the last 200 commits; `3a9690152` and `76d519f21` landed by direct push |

So **641 test files / 7,522 collected tests never execute in CI.** The
invocation itself is sound — no `addopts`, no `-k`/`-m`/`--deselect`, no
`continue-on-error`, and a local run with the CI arguments collects the file in
question as 31 tests — meaning that had it ever run, it would have been red. It
simply never ran.

Consequences beyond this one file:
- Ten files / 21 tests skip without `PPT_AGENT_POSTGRES_TEST_URL`, including
  `test_postgresql_owner_row_level_security.py` — the multi-tenant isolation
  gate (4.6 understated this: it is not "not in CI", there is no CI).
- Nine files / 41 tests are module-level skipped, three of them on `os.name != "nt"`.
- 19 of 29 root `scripts/*.test.mjs` are in no gate's list.
- Of 77 Cypress specs, 3 actually run on origin; 51 component specs are not
  named anywhere.
- `scripts/required-ci-contract.test.mjs` cannot catch any of this: it matches
  workflow *text*, asserts two substrings of the pytest command
  (`:86-88` — a trailing `-k` would still pass), and requires of Bitbucket only
  ruff, E2E and the Node suite. It even runs 15 regexes against the source of
  `workspaceChatFirstRoute.cy.ts` (`:1168-1211`), a spec nothing executes.
- CLAUDE.md's "~1,880 tests" is stale by 4x (7,522 collected).

**Measured 2026-09-12** (`artifacts/verbatim-design-impl-20260911/ci-linux-baseline.md`,
commit `54fe19fe7`): on a clean Linux runner the suite is
**7,174 passed / 228 failed / 118 skipped of 7,520, in 16m44s**. Of the 228
failures only **15 (6.6%) are real product defects** — the rest are
harness-gap 65, stale-fixture 46, platform 44, environment 26. So the suite is
close to usable as a gate; it was never the code quality that kept it out of
CI, because it was never in CI to begin with.

Three things that measurement surfaced, each worth more than the pass count:

- **A hang that did not reproduce — the first report was wrong.** The baseline
  described an 11-minute no-progress stall in `tests/unit` with "24 threads in
  `epoll_wait` and a listening socket with no peer". Investigated 2026-09-12
  and refuted: that socket belongs to a root-owned WSL system listener present
  before pytest starts (its port changes per boot), no test under `tests/unit`
  opens an in-process server, and two full single-process runs completed
  (768 s and 931 s) with results identical to the chunked baseline. The 24
  threads are leaked aiosqlite engine threads from
  `tests/unit/test_authored_quality_review.py`, which never disposes the
  engines it creates. What the run was waiting for remains unknown; it was a
  single unreproduced observation. `pytest-timeout` was still added (300 s,
  measured against a 124.9 s longest healthy test) — not to hide a stall but so
  a recurrence names the test and dumps every thread stack instead of stalling.
- **A genuine unbounded wait, found while investigating the above.**
  `utils/slide_capture.py:1904` joins a shared render task with
  `await asyncio.shield(task)` and **no timeout**, while the sibling metrics
  join at `:1183` passes `timeout=max(1, timeout) + 15`. If a shared render
  task never finishes, every joiner waits forever. This one can happen in
  production.
- **51 tests that verify nothing.** One harness seam
  (`tests/support/authored_worker_lane_harness.py:684` patches only
  `terminal_mod`, and `authored_presentation_service.py:61-80` propagates
  `render_html_to_png` but not the `_with_metrics` variant) makes the entire
  authored worker lane — wave scheduling, overload concurrency halving and
  cooldown, safety-gate remake→flag, terminal flag publication, third-pass
  rescue, deck-plan illustration, slide receipts — fail at the render step and
  prove nothing beyond that. One fix restores all 51.
- **The Chrome-gated 59 never ran here and will run on the real CI image**, so
  the true failure count on `cypress/included` is likely higher than 228.

Two confirmed product defects (verified directly, not taken from the report):
`ExportableDeck = DeckSnapshot | DeckGenerationSnapshot`
(`services/authored_vnext/integration/export_payload.py:20`) is unsatisfiable
for one arm — `worker_scene` requires `.scene` (`:38-43`) and `DeckSnapshot`
(`models/authored_vnext/master/slide.py`) has no such field, yet
`master/service.py:213` returns exactly that type; and
`services/account_deletion_controller.py:254` compares a possibly-naive
`claim_expires_at` against an aware now, so an expired deletion claim raises
`TypeError` instead of being reclaimed — while `utils/datetime_utils.as_utc`
exists for this and is not used here.

Full census and ranked proposals: `artifacts/verbatim-design-impl-20260911/ci-coverage-gap.md`.
The proposals are not applied — adding a Bitbucket pytest step must be measured
on a clean Linux runner first, since the current failure count there is unknown.

### 3.2 Publish rate is 38%, and lower under the default flag

**Update 2026-09-12 (later series).** The gate work continued after this
document's evidence set and the rate is moving: shadow mode 4/5, enforce mode
3/5 (runs 31-40), against 2/5 for the pre-design series. The two enforce
failures were the text-binder class that the token-mode design removes
structurally, and both were correctly classified non-retryable after the third
rescue attempt rather than published broken. Capture cost was 42-45 per deck
(1.2-1.3 per slide) with 3-5 reuses. This does not yet meet the 95% gate and is
a small sample, but the direction is the opposite of what the figure below
records, and the figure should be re-measured against the default flags before
it is used for a launch decision.

Thirteen runs of the same 35-slide input across three series published five
(`repeatability-20260911.md` and its series 2/3 sub-tables: 2/5, 2/5, 1/3; one
run-22 failure was a provider 429). Excluding the 429: 5/12 = 42%. The approval
gate requires 95%.

All thirteen ran with `AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW=true`;
the default is **false** (`utils/get_env_authored_publish.py:24`,
`services/authored_terminal_slide_policy.py:52`). Under the default, a flagged
slide fails the deck outright, so the shipped publish rate is below 38%.

Failure classes are not one thing:

| Class | Runs | Nature |
|---|---|---|
| `source_content_browser_readback_failed` | 12, 14, 17, 19, 23 | LLM output (author / repair / VQA / rescue) broke a bound source block; each series surfaced a **new** path whose validator was missing (`w23-report.md`, "Why the earlier checks missed it") |
| `authored_slide_author_failed` | 13, 18 | Renderer policy rejection (CSS `content:"1"`, unbalanced `img`) — unrelated to readback |
| provider 429 | 22 | Personal-token quota, see `codex-429-not-a-blocker` |

The readback class has a structural cause: verbatim text is bound after the
fact and every generating path needs its own validator. The fixes shipped so far
(W21–W26) each added one — the pattern the project's own rule forbids
(symptom fixes per path). `docs/authored-verbatim-placeholder-authoring-design-20260911.md`
section 2.1 removes the class constructively. **Deciding whether to adopt that
design is the real P0**; it is not mentioned in v1.

v1 claimed block-level failure detail is unrecoverable. That is true for exactly
one seam: the deck-final readback in
`services/authored_admission_publication.py:96` raises `AuthoredStageError`
without passing `fail_stage`, so `presentation_generation_job_service.py:674-685`
stores only the code. The per-slide path
(`authored_presentation_pipeline.py:1463-1473`, `authored_checkpoint_state.py:366-394`)
already persists `block_ids` via `as_safe_dict` (run-23 `06-final-status.json`
shows it). Closing the one seam is a half-day change; it is not on the critical
path to 95%.

### 3.3 Export has three deadline authorities that do not know each other

| Authority | Value | Where |
|---|---:|---|
| Policy row `export_deadline_seconds` | 600 s, flat since v1 | `alembic/versions/f9c3a7d1e5b8_add_public_operation_admission.py:105`; `_policy_deadline_seconds` returns the ceiling unscaled for non-generation kinds (`public_operation_admission.py:175-196`) |
| Lease heartbeat | enforces the 600 s | `workspace_operation_lease.py:113` → `lease_lost`, runner `:4527` |
| Hybrid job grant | **1740 s** constant | `services/public_export_operation.py:521` `HYBRID_EXPORT_DEADLINE_SECONDS`, injected at `:1957` |
| Trusted executor cap | 1800 s | `worker_dispatcher.py:91,103` — an upper `min()`, never the grant |

After 600 s the heartbeat is refused and the result can no longer commit, but
the isolated worker keeps Chrome running to 1740 s. At the measured
25.5 s/slide, any deck over ~23 slides does **up to 1140 s of computation whose
result cannot be committed**. Meanwhile `max_slides` was raised to 100
(`c8d5f3a20b71`) and the generation deadline was scaled to 14400 s
(`a2c4e6f8b1d3`); export was never scaled.

v1 said "two authorities" and attributed 1740 s to the executor cap; both were
wrong. `public_operation_deadline_scope.py` is not consumed on the export path
at all (consumers: `outlines.py`, `presentation_generate.py`,
`presentation_generation_operation_handler.py`).

### 3.4 No operating system has a production-mode isolated export

- Linux: `services/trusted_executor/isolation_policy.py:209-215` marks every
  control UNSUPPORTED; `process_supervisor.py:90-112` reports
  `enforcement_ready=False, "launcher integration is not implemented"`.
- Windows: `isolation_policy.py:186-207` also marks the five required controls
  (`contracts.py:114-125`, incl. NETWORK_NONE) UNSUPPORTED. The Job Object
  supervisor runs only when `PUBLIC_TRUSTED_WORKER_LOCAL_POLICY=development`
  **and** `ENVIRONMENT ∈ {local, development, dev, test}`
  (`composition.py:99-108, :128`).
- With `ENVIRONMENT=production` the isolated export runtime **refuses to
  start** (`api/lifespan.py:215-224`), and it is only composed at all under
  `PUBLIC_V2_INTEGRATION_PROFILE=postgresql-strict` (`:217-218`).

v1 offered "finish Linux, or declare Windows the supported runtime". The second
option does not exist: Windows is a development-gap exception, not a runtime.
The only production path is implementing a Linux namespace launcher
(bwrap/nsjail plus cgroup v2) behind the existing `isolation_policy` contract.

### 3.4a Two of the six declared worker resource limits are not enforced

Found 2026-09-12 while restoring the only test that covers worker resource
metrics. The target architecture (section 9.3) requires container memory, CPU,
PID, open-file, output-byte and wall-clock limits to all be enforced. Actually:

| Limit | Enforced by |
|---|---|
| memory, process count, CPU rate, deadline | Job Object — OS-enforced |
| **open file handles** (`PPT_WORKER_OPEN_FILE_LIMIT`) | **nothing.** The name is only in env allowlists (`services/trusted_executor/contracts.py:175`, `services/public_worker_profiles.py:135`, `services/isolated_worker_process.py:76`); it is forwarded to the child and never read. `ResourceLimits` (`contracts.py:70-78`) has no field for it |
| output bytes (`PPT_WORKER_OUTPUT_LIMIT_BYTES`) | **the sandboxed child itself** (`workers/public_worker_runtime.py:252`, `workers/public_worker_store.py:149`) — cooperative self-limiting by the process the limit exists to contain |
| handle-count telemetry (`peakHandleCount`) | no producer; every read is `.get(..., 0)`, so `worker_peak_handle_count` is always 0 |

The dead names `worker_limit_triggered` and `worker_final_process_count`
survive only in the payload allowlist at
`services/workspace_operation_runner.py:266`; nothing produces them.

This is a containment gap, not only a metrics gap: a worker running untrusted
LLM-authored HTML can exhaust file descriptors, and the byte ceiling on its
output is one it enforces on itself. The container backend (plan package F)
can close both — Docker `HostConfig.Ulimits` (`nofile`) for the first, and a
supervisor-side size check on the output bind for the second.

The Job Object limit of 96 processes is **per dispatch (per deck)** —
`wire_transport.py:251` creates a new `OperationProcessSupervisor` per launch,
`process_supervisor.py:405` creates the Job then. So 81 peak processes vs 96 is
a per-deck margin, not a host ceiling; v1's "one export per host" derivation
was wrong. The real per-host bounds are CPU (22 logical on the bench box) and
the 8 GiB per-deck memory limit (`worker_dispatcher.py:89`).

On the isolated (v2) path the scheduler is the effective cap —
`composition.py:35-44`: `global_cap=4, node_cap=4, tenant_cap=2, owner_cap=2,
presentation_cap=2`. The policy row's 20 is unreachable there. A measured-cap
mechanism already exists: with `PUBLIC_RUNTIME_PROFILE` set,
`_effective_runtime_quota_limits` (`public_operation_admission.py:394-434`)
clamps the policy row to `min(policy, tier.render_export_slots{4/8/8/12},
PUBLIC_EXECUTABLE_EXPORT_SLOTS, PUBLIC_PROVIDER_EXPORT_CONCURRENCY_CAP)`
(`public_workstation_runtime_profile.py:35-38, :143-190`). It is off by default.

## 4. P1 findings

### 4.1 HTTP-layer defence

- Login **is** throttled: `api/v1/auth/router.py:38-58`, 5 failures / 300 s
  sliding window → 429. (v1 said "unthrottled"; wrong.) Note
  `api/v1/auth/rate_limit.py` `LOGIN_RATE_LIMITER` is imported but never called —
  dead duplicate of the router-local implementation.
- Everything else is not: no `limit_req`/`limit_conn` in `nginx.conf`, nothing
  in `api/middlewares.py`, `proxy.ts`, or `scripts/presenton-local-proxy.mjs`.
  Uploads, SSE connections, status polling, and the public share endpoint
  (`presentation_share.py`; token is 32 random bytes, so brute force is
  impractical but unbounded) are all unlimited.

### 4.2 No malware scanning on the stock image

`docker-compose.chat-first-canary.yml:1-5`: the stock image has no `clamscan`
and no definition feed; ClamAV exists only in the operator-built canary image.
The standard `production` profile scans **no** uploaded document or image.
`PRESENTON_MULTI_DEPLOYMENT_TIER=production` refuses to start without a scanner
(`chat_first_asset_malware_scan_required`), so today a production tier cannot
be run from the shipped image at all.

### 4.3 Export single-flight is still a shadow

`services/public_export_identity.py:75-88` and the five gate sites in
`public_export_operation.py:952, :992, :1024, :1109, :1406` sit behind
`PUBLIC_EXPORT_IDENTITY_V2_ENV` (default off). The unique key that exists in
`models/sql/public_artifact.py:270-282` (owner, tenant, presentation, revision,
scene/semantic/quality/exporter digests, export mode, output mode, font
manifest, render adapter) is not yet authority, so the same export submitted
twice runs twice. Admission (4.3 in v2 was wrong to blame Next) is not the
issue; identity is.

### 4.4 Shared-disk dependency, concretely

With `PUBLIC_ARTIFACT_STORE_BACKEND=s3` these still resolve `app_data` paths
shared between `api` and workers: `services/export_task_operations.py`,
`export_task_runtime.py`, `authored_checkpoint_artifacts.py`,
`template_v2_pptx_storage.py`, `template_v2_pptx_worker.py`,
`workspace_artifact_publication_service.py`, `export_retention_service.py`,
`icon_finder_service.py`, `source_locked/job_store.py`, `utils/export_utils.py`,
and — a secret, not a blob — `provider_credential_vault.py`. `AUTH_SECRET_KEY`
(`api/v1/auth/config.py:71-81`) also lives in the app_data config, so multiple
API replicas must share the disk to share session signing.

### 4.5 Connection pool and CORS

- `utils/db_utils.py:111`: `pool_size=5`, `max_overflow=10`, **per process**;
  ~6–7 processes exhaust PostgreSQL's default 100. Tunable via `DB_POOL_SIZE` /
  `DB_MAX_OVERFLOW` but no profile sets them.
- `api/main.py:80-99`: with `CORS_ALLOWED_ORIGINS` unset, localhost origins are
  allowed with `allow_credentials=True`, `allow_methods=["*"]`. Nothing forces
  the variable under `ENVIRONMENT=production`.

### 4.6 Row-level security is sound in code but not in CI

`b5a0d3e7c9f1` was repaired in `dbb00294c`. `tests/integration/test_postgresql_owner_row_level_security.py:56`
skips without `PPT_AGENT_POSTGRES_TEST_URL`; CI runs SQLite only, so RLS is
never exercised by a required gate.

## 5. What is sound — and under which switches

| Asset | Real | Active by default on the product path? |
|---|---|---|
| Durable operations: lease, heartbeat, attempt/fencing, `SKIP LOCKED` claims; API restart does not kill a deck (compose runbook §5) | yes | yes |
| OIDC principal binding, digest-only (`public_principal_binding.py`); wired into `SessionAuthMiddleware` via `api/v1/auth/principal.py`, not only `/api/v2` | yes | yes |
| PostgreSQL role split + startup authority probe (rejects superuser / BYPASSRLS logins) | yes | `multi` profile only |
| Webhook SSRF validation at register and dispatch (`webhook/router.py:38`, `webhook_service.py:87`) | yes | yes |
| Three-tier quota, immutable policy versions (`public_admission.py`) | yes | yes for owner/tenant on generation and UI export; **global scope unread on the export path** (3.1) |
| Isolated export worker, Job Object budgets, weighted scheduler | yes | yes on the Windows development stack; **no production-mode composition on any OS** (3.4) |
| S3 content-addressed artifact store, provisional→commit, signed range downloads | yes | opt-in; local-path consumers remain (4.4) |
| Release matrix + Ed25519 evidence gate (`scripts/public-service-release-matrix.mjs`) | yes | never executed — and neither is the Python suite at all (3.1a) |

## 6. Recommended order

| Priority | Work | Addresses |
|---|---|---|
| P0 | Decide on verbatim placeholder authoring (`...-design-20260911.md` §2.1) instead of the next per-path validator; target 95% under the **default** flag | 3.2 |
| P0 | Make `admit_public_operation` read the global scope; decide the generation-time `export_as` contract; promote `PublicExportIdentity` from shadow to authority | 3.1, 4.3 |
| P0 | One export deadline: derive the hybrid job grant from `admission.deadline_at - now`; scale `export_deadline_seconds` by slide count (25.5 s × n + overhead, ceiling 1800) in `_policy_deadline_seconds`; delete the 1740 constant | 3.3 |
| P0 | Implement the Linux namespace launcher behind `isolation_policy`; stop describing Windows as a runtime | 3.4 |
| P0 | Raise `AUTHORED_CLUSTER_JOB_CONCURRENCY` deliberately (after provider-quota validation) and register it, `AUTHORED_JOB_CONCURRENCY`, `WORKSPACE_OPERATION_MAX_CONCURRENCY` in `settings_registry.json`; lower `global_active_generation` to match | 2.1 |
| P1 | Enable `PUBLIC_RUNTIME_PROFILE` once the development stack sets its five required inputs (`public_workstation_runtime_profile.py:193-222` — physical memory ≥ 120 GiB, executable slots, provider caps); with them unset every admission is rejected | 3.5 |
| P1 | Ship a scanner-equipped production image, or make the scan tier the only production tier | 4.2 |
| P1 | nginx `limit_req` / `limit_conn`; SSE connection cap; delete the dead `LOGIN_RATE_LIMITER` | 4.1 |
| P1 | Move `provider_credential_vault` and `AUTH_SECRET_KEY` off app_data; then the remaining path consumers to `ArtifactRef` | 4.4 |
| P1 | Pool size per replica count; require `CORS_ALLOWED_ORIGINS` in production; run the RLS test in CI against PostgreSQL | 4.5, 4.6 |
| P1 | Close the deck-final readback seam (`authored_admission_publication.py:96` → `fail_stage`) | 3.2 |
| P2 | Execute the 32-cell matrix and 24 h soak — first get one 5-slide product run past admission | section 2 |

Items 3.1, 3.3, 3.4, 2.1 are single-point invariants. None of them may be
patched per call path.

## 7. Conclusion

v1 over-credited the machinery; v2 over-corrected by trusting one review that
misread the export route. v3 is from direct reads of the load-bearing claims.
The admission control plane **is** live for generation and UI export
(owner/tenant scope), and export runs in the isolated worker on the Windows
development stack. What is not live: global-scope quota on export, export
single-flight, a production-mode isolation composition on any OS, cluster
generation concurrency above one deck, and a publish rate above 38%.

The path to launch is (1) the placeholder authoring decision, (2) one export
deadline with slide-count scaling, (3) a Linux launcher backend for the
trusted executor (new supervisor, composition and transport path mapping —
not a configuration change), (4) global scope and identity authority on
export — in that order. None of it is more infrastructure first.
