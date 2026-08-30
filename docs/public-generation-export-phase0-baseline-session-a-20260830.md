# Public generation/export Phase 0 baseline — Session A

Date: 2026-08-30
Scope: read-only product-path census plus public API correlation and metric naming
Reference contract: `docs/public-generation-export-target-design-handoff-20260830.md`

## Checkout and runtime baseline

- Git root: `C:/project/PPT-agent/ppt-agent`
- Branch: `fix/authored-release-score-20260829`
- HEAD: `3a9690152aaad1ad7d14a0fcdb046ba2c196a74b`
- Upstream: `origin/fix/authored-release-score-20260829`
- The checkout was already dirty. Existing `.spec-memory`, artifact, document, and
  log changes were preserved.
- No listener owned ports `3000`, `3010`, `5000`, `5110`, `8000`, or `8010` at
  census time. This baseline is therefore a static product-path trace, not a live
  end-to-end run or latency benchmark.

## Current product path

### Generation

1. `usePresentationGeneration` calls
   `PresentationGenerationApi.generateAuthoredAsync`.
2. The client posts to
   `POST /api/v1/ppt/presentation/generate/async`, then polls status and may post
   cancellation.
3. FastAPI persists a generation job and either wakes the workspace runner or
   registers `launch_generation_job` as a Starlette background task.
4. The default launcher creates a tracked asyncio task in the FastAPI process.
   The job handler then directly awaits the authored provider, authoring,
   Chromium rendering, optional VQA, and package/publication pipeline.

At census time, the legacy synchronous
`POST /api/v1/ppt/presentation/generate` directly awaits the full generation
handler. It therefore executes heavy provider/render/export work during the
public HTTP request lifetime and is not an acceptable canonical v2 path.

The Phase 1 adapter gate added by this session now returns
`410 sync_generation_deprecated` on PostgreSQL cluster/public runtimes before
request validation or heavy work starts. The inline behavior remains available
only on the explicit `sqlite-local` compatibility profile.

### Workspace export

1. The presentation header submits `export.pptx` or `export.pdf` to
   `POST /api/v1/ppt/workspace/presentations/{presentationId}/commands`.
2. The workspace runner claims the durable operation. Embedded mode executes the
   handler in a FastAPI-process asyncio task; configured external-worker mode can
   execute it in the managed worker process.
3. Authored export runs the render worker, export worker, and artifact publication
   path.
4. `GET /api/v1/ppt/workspace/artifacts/{artifactId}/download` reauthorizes the
   artifact. The public-artifact branch currently reads all artifact bytes before
   returning a one-chunk streaming response. The UI then calls `response.blob()`.
   This is not end-to-end bounded-memory streaming.

The existing Next `POST /api/export-presentation` route only admits the workspace
command and returns `202`; it does not perform render/export inline. The legacy
`/api/export-presentation/file` route still resolves and serves a local export
path and must remain a compatibility surface until a later migration gate.

## Canonical v2 correlation contract

- `operationId` is the only public correlation key from submission receipt through
  status, events, cancellation, artifact metadata, and authorized download.
- `receiptId` identifies the immutable admission receipt; it does not replace the
  operation correlation key.
- Internal structured logs may also carry `request_id`, `attempt`, and `fence`, but
  public responses and events must not expose owner IDs, tenant IDs, session IDs,
  local paths, raw object-store keys, provider secrets, or credentials.
- Metric labels must never contain `operationId`, `receiptId`, request IDs,
  presentation IDs, owner IDs, or tenant IDs. Those values belong in bounded logs
  or durable events, not high-cardinality metrics.

## Existing observation surface

- HTTP correlation: `x-request-id` / request-local request ID.
- Workspace observation fields include operation/presentation/conversation IDs,
  command type, duration, event lag, attempt/retry/recovery counts, revisions,
  fencing token, revision conflict, and fence outcome.
- Workspace events include `accepted`, `claimed`, `running`, `retry_scheduled`,
  `cancel_requested`, `cancelled`, `recovered`, `succeeded`, `failed`,
  `dead_letter`, `blocked`, `lease_lost`, `released`, and `runner_fault`.
- Existing aggregate metrics include `workspace_operation_admission_total`,
  `workspace_operation_total`, `workspace_dead_letter_total`,
  `workspace_lease_lost_total`, `workspace_revision_conflict_total`, cleanup and
  outbox counters, and event lag. These workspace metrics are process-local and
  non-authoritative.
- Public admission already emits
  `presenton_public_provider_requests_total`,
  `presenton_public_provider_actual_cost_unavailable_total`, and
  `presenton_public_metrics_available`.
- The durable authored timing receipt currently uses stages `outline`, `author`,
  `repair`, `checkpoint`, `export`, and `publication`.

## Phase 0 canonical metric names

The following names are the bounded public-service vocabulary. Implementing the
worker-side observations belongs to the owning control-plane/worker sessions;
this session does not add a second metrics authority.

| Metric | Allowed low-cardinality labels | Meaning |
|---|---|---|
| `presenton_public_api_requests_total` | `route`, `method`, `status_class` | Canonical v2 edge request count |
| `presenton_public_api_request_duration_seconds` | `route`, `method` | Admission/read/cancel/download edge latency |
| `presenton_public_operation_queue_wait_seconds` | `kind`, `outcome` | Accepted-to-running queue wait |
| `presenton_public_operation_phase_duration_seconds` | `kind`, `phase`, `outcome` | Durable phase duration |
| `presenton_public_operation_retry_total` | `kind`, `phase`, `reason` | Durable retry decisions |
| `presenton_public_worker_rss_bytes` | `worker_kind` | Worker/sandbox resident memory |
| `presenton_public_provider_requests_total` | `kind`, `class` | Existing initial/retry provider calls |
| `presenton_public_provider_actual_cost_unavailable_total` | `kind` | Existing missing actual-cost evidence |
| `presenton_public_artifact_download_bytes_total` | `format`, `outcome` | Authorized bytes sent without artifact identity labels |

Canonical `phase` values are `admission`, `authoring`, `validating`, `rendering`,
`packaging`, and `publishing`. Canonical public states remain only `queued`,
`running`, `succeeded`, `failed`, and `cancelled`.

## Baseline gaps and ownership handoff

- Canonical v2 endpoints and receipts were absent at census start.
- The browser generation client and `scripts/ppt-agent.mjs` still submit the
  richer v1 request shape. They were not cut over to v2 in this lane because the
  Session B durable adapter is not bound and the v2 request does not yet prove
  parity for every existing authored/source-content option. The CLI's sync call
  now receives the PostgreSQL deprecation response; `--async` remains a v1
  compatibility path.
- The v1 synchronous generate implementation still exists for `sqlite-local`
  compatibility. PostgreSQL cluster/public runtimes now fail with an explicit
  deprecation response before entering that implementation; the endpoint was not
  deleted.
- Embedded async execution is detached from the HTTP response but is not a true
  process boundary. Production selection of the external worker is a separate
  activation decision.
- Current observation does not join every queue, provider, authoring, render,
  package, publication, and download stage under one durable operation.
- Public artifact download is not proven bounded-memory streaming.
- No live API-to-worker-to-artifact run, latency baseline, 5/10/20-slide fixture,
  concurrency test, failure injection, or deployment/cutover evidence was produced
  in this session.
