# Template V2 canary readiness

Template V2 remains default-OFF. A canary is ready only when the backend flag is
exactly `true` (ignoring case and surrounding whitespace) and the template ID is
present in an explicit, valid allowlist. This procedure prepares a canary; it
does not deploy or enable anything by itself.

Last rehearsed end-to-end on local Windows on **2026-07-25** against
`origin/main` @ `83ec1d21`; see [Rehearsal record](#rehearsal-record).

## Policy inputs

Set both values in the FastAPI process:

```text
ENABLE_TEMPLATE_V2=true
TEMPLATE_V2_TEMPLATE_ALLOWLIST=canary-template-a,canary-template-b
```

For staging and production, also declare the managed deployment tier:

```text
TEMPLATE_V2_DEPLOYMENT_TIER=staging
# or: TEMPLATE_V2_DEPLOYMENT_TIER=production
DATABASE_URL=postgresql://...
```

`local` is the default tier so developer and unit-test SQLite remain unchanged.
`staging` and `production` fail readiness and FastAPI/worker startup when the
configured database is not PostgreSQL. An unknown tier also fails closed. This
is an intentional guard against SQLite's measured concurrent-write failures;
do not bypass it for a managed canary.

The checked-in Compose profiles set this explicitly: `production` and
`production-gpu` use `production`, while `development` and `development-gpu`
use `development`. A raw `Dockerfile` image, direct `start.js` launch, systemd,
or Kubernetes deployment inherits the process environment and cannot infer its
deployment tier safely, so managed deployments through those paths **must**
set `TEMPLATE_V2_DEPLOYMENT_TIER=staging|production`. Packaged Electron and
ordinary desktop/local launches remain `local`; do not use that default as a
managed deployment classification.

The allowlist is comma-separated. Empty entries, duplicate IDs, `*`, control
characters, and IDs longer than 128 characters invalidate the entire
configuration. Values such as `1`, `yes`, and `on` do not merely fail to enable
the feature: they are invalid configuration (`template_v2_flag_invalid`) and
also void an otherwise valid allowlist. Only unset, empty, and `false` are a
clean disabled state. Malformed configuration fails closed: discovery, writes,
imports, and the PPTX dispatcher remain disabled.

Separators other than a comma are not detected. `a;b`, `a b`, and `a|b` are each
accepted as one literal template ID, so the readiness check still exits `0` and
reports `allowlisted_template_count: 1` while every write for `a` and for `b`
returns 403. Always confirm `allowlisted_template_count` equals the number of
IDs you intended.

The Studio flag `NEXT_PUBLIC_TEMPLATE_V2_STUDIO_ENABLED` is separate, and despite
the `NEXT_PUBLIC_` prefix it is **not** inlined at build time. Its only production
reader is the server component `app/template-v2-studio/[templateId]/page.tsx`,
which evaluates it per request, so the Studio is enabled and disabled by
restarting Next.js — no rebuild and no new bundle. Its check is a strict
`=== "true"`, with no trim or case fold, so `TRUE` and `" true "` enable the
backend flag but not this one. Enabling it does not override the backend policy,
and the backend flag does not expose the Studio UI.

## Worker mode

The default `TEMPLATE_V2_PPTX_WORKER_MODE=embedded` keeps the durable dispatcher
inside the FastAPI process for single-node deployments. For multi-node
deployments, set `TEMPLATE_V2_PPTX_WORKER_MODE=external` in both the API and a
dedicated worker process, then start the worker from `servers/fastapi`:

```powershell
uv run python -m services.template_v2_pptx_worker
```

The external worker consumes the same database-backed queue with compare-and-set
claims, leases, heartbeats, stale-attempt recovery, and graceful requeue. Invalid
worker-mode values fail closed by starting no embedded dispatcher.

## Offline readiness check

From `servers/fastapi`, with the intended environment loaded:

```powershell
uv run python scripts/check_template_v2_canary.py
```

The command prints content-free JSON and exits `0` only for
`template_v2_canary_ready`. It never prints template IDs. Exit `2` is a NO-GO;
use the `code` field to distinguish a disabled feature, missing allowlist,
invalid configuration, or `template_v2_managed_canary_requires_postgresql`.
The output also reports only the database backend and deployment tier, never a
database URL or credentials.

Before a canary handoff, run:

```powershell
uv run pytest tests/unit/test_template_v2_policy.py
uv run pytest tests/unit/test_template_v2_strategies.py tests/unit/test_structured_templates_api.py
```

## Canary verification

Use only the existing authenticated `/api/v1/ppt/structured-templates` routes.
There is no `/api/v2` rollout surface.

Template V2 ships no PPTX fixture, and the only `.pptx` in the tree
(`servers/fastapi/.venv/Lib/site-packages/pptx/templates/default.pptx`) has zero
slides: it uploads successfully and then fails in the worker with
`pptx_contains_no_slides`. Create a one-slide source first, from
`servers/fastapi`:

```powershell
uv run python -c "from pptx import Presentation; from pptx.util import Inches; p=Presentation(); s=p.slides.add_slide(p.slide_layouts[6]); s.shapes.add_textbox(Inches(1),Inches(1),Inches(6),Inches(1)).text_frame.text='canary'; p.save('canary.pptx')"
```

The first template must be created by importing that file: `POST
/api/v1/ppt/structured-templates/imports` (multipart `template_id` + `pptx_file`,
plus a required `Idempotency-Key` header of 8-200 characters and content type
`application/vnd.openxmlformats-officedocument.presentationml.presentation`),
then `POST /{import_id}/confirm`. `POST /api/v1/ppt/structured-templates` cannot
bootstrap a canary: it requires a presentation already persisted as
`version=v2-standard, mode=template`, and only import confirmation produces one.

1. Confirm the readiness command exits `0` and reports the expected allowlist
   count.
2. Confirm list discovery returns only allowlisted template IDs.
3. Confirm a write for a non-allowlisted ID returns HTTP 403. Use `DELETE
   /api/v1/ppt/structured-templates/<non-allowlisted-id>`; the policy gate runs
   before any body or existence check. `POST` is not a usable probe -- an invalid
   `layouts` body returns 422 before the policy gate is reached. A PPTX import
   for a non-allowlisted ID also returns 403, but only once the
   `Idempotency-Key` header is present; without it the request fails validation
   with 422 first.
4. Exercise one allowlisted Template V2 template workflow, then one existing
   authored and one existing adaptive workflow. The allowlist gates Template V2
   template IDs only; authored and adaptive decks stay `v1-standard` and never
   reach the rollout policy, so their execution paths must be identical with the
   flag on and off.
5. Monitor `template_v2_rollout` events by operation, outcome, and code.
   Template identifiers are hashed; presentation content is never logged.
6. Monitor `template_v2_pptx_queue` events. `dispatch` counts work offered to
   workers, while `recover` counts expired leases returned to the queue. Both
   payloads contain only an operation, outcome, bounded aggregate count, and,
   for recovery, bounded `duration_ms`.
7. Monitor `template_v2_generation` events for generation and export success,
   failure code, and `duration_ms`. The event retains only a SHA-256 prefix of
   the template ID, a bounded revision, and closed enum values. It never accepts
   prompts, presentation/job IDs, paths, filenames, or exception text.

These event families are emitted at INFO on the
`services.template_v2_rollout`,
`services.template_v2_pptx_queue_observability`, and
`services.template_v2_generation_observability` loggers. At
`LOG_LEVEL=WARNING`, normal volume is suppressed, but recovered stale leases,
failed dispatcher iterations/import attempts, cleanup failures, and the
content-free startup `template_v2_pptx_health` signal remain visible.

Operators can query the same aggregate health without enabling Template V2:

```powershell
uv run python scripts/check_template_v2_operations.py --mode health
```

The command exits `2` for stale, failed, review-required, or overdue-cleanup
state and never prints job IDs, tenant identifiers, filenames, or presentation
content. Health priority is stale, failed, review-required, then overdue
cleanup.

## Allowlist expansion gates

Expand the allowlist by exactly one template ID per change. Never use a wildcard
or expand UI exposure independently of the backend allowlist. The on-call owner
must record the deployment SHA, hashed template ID, template revision, start/end
time, query results, and approval; keep the raw ID-to-hash mapping in a
restricted operations record, not in logs or dashboards.

Use the following default gate for each new template/revision. A team may set a
stricter service-specific latency threshold before the canary, but must not
relax a gate during an active observation window:

| Gate | Required result |
|---|---|
| Observation | At least 24 continuous hours and 100 completed generation attempts, whichever is later |
| Generation | At least 99% success and p95 at or below the predeclared SLO |
| Export | At least 99% success for every enabled export type and p95 at or below its predeclared SLO |
| Correctness | Zero partial publications, duplicate presentations from one idempotency key, revision mismatches, or unintended fallback to authored/adaptive |
| Recovery | Zero repeated lease recovery for the same synthetic exercise; aggregate recovered work below 1% of dispatched work |
| Operations | `--mode health` exits 0, `rollback_safe` is true, and private storage is ready |
| Compatibility | Authored/adaptive smoke tests and the Template V2 CI suites remain green |

Pause expansion immediately for any correctness, privacy, credential, or
durability defect. Roll back for a five-minute failure ratio above 5%, any
partial/duplicate publication, or an unhealthy rollback gate. For a 1-5%
failure ratio, latency SLO breach, or recovered lease, freeze the allowlist and
investigate; restart the 24-hour window after the fix. Low traffic is not proof
of health: do not waive the 100-attempt minimum.

## Dashboard queries and alerts

The repository emits structured JSON log metrics rather than Prometheus
metrics. The examples below are Loki/LogQL templates and assume an application
label of `{app="presenton-fastapi"}`. Adapt only the stream selector to the
deployment. The `regexp`/`line_format` stages strip the normal log prefix before
the JSON parser:

```logql
# Generation attempts by outcome (15 minutes)
sum by (outcome) (
  count_over_time(
    {app="presenton-fastapi"} |= "template_v2_generation "
    | regexp "template_v2_generation (?P<event>\\{.*\\})$"
    | line_format "{{.event}}" | json
    | operation="generate" [15m]
  )
)

# Generation success ratio (15 minutes)
sum(count_over_time(
  {app="presenton-fastapi"} |= "template_v2_generation "
  | regexp "template_v2_generation (?P<event>\\{.*\\})$"
  | line_format "{{.event}}" | json
  | operation="generate" | outcome="success" [15m]
))
/
sum(count_over_time(
  {app="presenton-fastapi"} |= "template_v2_generation "
  | regexp "template_v2_generation (?P<event>\\{.*\\})$"
  | line_format "{{.event}}" | json
  | operation="generate" [15m]
))

# Generation p95 latency in milliseconds (15 minutes)
quantile_over_time(
  0.95,
  {app="presenton-fastapi"} |= "template_v2_generation "
  | regexp "template_v2_generation (?P<event>\\{.*\\})$"
  | line_format "{{.event}}" | json
  | operation="generate" | unwrap duration_ms [15m]
)

# Export failures by type and stable code (15 minutes)
sum by (export_type, code) (
  count_over_time(
    {app="presenton-fastapi"} |= "template_v2_generation "
    | regexp "template_v2_generation (?P<event>\\{.*\\})$"
    | line_format "{{.event}}" | json
    | operation="export" | outcome="failure" [15m]
  )
)

# Recovered leases and recovery p95 duration (15 minutes)
sum(count_over_time(
  {app="presenton-fastapi"} |= "template_v2_pptx_queue "
  | regexp "template_v2_pptx_queue (?P<event>\\{.*\\})$"
  | line_format "{{.event}}" | json
  | operation="recover" | count > 0 [15m]
))

quantile_over_time(
  0.95,
  {app="presenton-fastapi"} |= "template_v2_pptx_queue "
  | regexp "template_v2_pptx_queue (?P<event>\\{.*\\})$"
  | line_format "{{.event}}" | json
  | operation="recover" | unwrap duration_ms [15m]
)

# Unsafe rollback/operational health events
sum(count_over_time(
  {app="presenton-fastapi"} |= "template_v2_pptx_health "
  | regexp "template_v2_pptx_health (?P<event>\\{.*\\})$"
  | line_format "{{.event}}" | json
  | rollback_safe="false" [5m]
))
```

Build panels for generation/export volume, success ratio, p50/p95/p99 latency,
failure code, recovered lease count/duration, and rollback health. Split only by
closed fields such as operation, outcome, export type, code, revision, and the
allowlisted template hash.

Configure these alerts:

| Severity | Condition |
|---|---|
| Critical | Any partial/duplicate publication signal from the durable-generation invariant check |
| Critical | Generation or export failure ratio above 5% for 5 minutes with at least 20 attempts |
| Critical | Latest health event has `rollback_safe=false` or the health command exits 2 |
| Warning | Generation or export failure ratio above 1% for 15 minutes with at least 20 attempts |
| Warning | p95 generation/export latency exceeds its declared SLO for 15 minutes |
| Warning | Any non-exercise recovery has `count > 0`, or recovery duration exceeds one dispatcher interval |
| Warning | Expected canary traffic exists but no generation telemetry arrives for 15 minutes |

Keep `LOG_LEVEL=INFO` for the canary. A no-telemetry alert must be conditioned
on expected request traffic or a synthetic probe; an idle canary is not an
incident. Dashboards and notifications must never include raw log lines, raw
template identifiers, URLs, prompts, filenames, or exception text.

## Rollback

Set `ENABLE_TEMPLATE_V2=false` (or remove it) and restart the FastAPI process.
This immediately blocks new Template V2 discovery, writes, imports, and
dispatcher startup. Existing persisted Template V2 presentations remain
readable/exportable; rollback is not a data migration. Do not remove stored
Template V2 rows or source artifacts as part of the flag rollback.

Drain imports **before** flipping the flag. The drain gate counts `queued`,
`processing`, `finalizing`, `confirming`, `review_required`, and `failed` rows:

```powershell
uv run python scripts/check_template_v2_operations.py --mode rollback
```

Exit `0` and `template_v2_rollback_drain_complete` are required before rollback.
The output is aggregate-only. While the flag is off, an
import in `queued`, `processing`, `finalizing`, `review_required`, or `failed` is
read-only: its status stays readable, but `confirm`, `cancel`, and `retry` all
return 403, so the owner can neither finish nor abandon it.

Private-source TTL cleanup is independent from the dispatcher and continues
while the feature flag is off. It starts with the FastAPI lifespan and uses the
same durable per-row claims in multi-process deployments. For a maintenance
host or stopped API, run it explicitly:

```powershell
uv run python scripts/check_template_v2_operations.py --mode cleanup
```

The cleanup command is feature-flag independent, content-free, and exits `2`
when any claimed deletion failed.

To drain without a full rollback, keep `ENABLE_TEMPLATE_V2=true` and shrink
`TEMPLATE_V2_TEMPLATE_ALLOWLIST` to only the affected template IDs. Confirm,
cancel, and retry keep working for those IDs and TTL cleanup keeps running, while
every other template is blocked exactly as it would be by a full rollback.

## Recovery and rollback drill

Run this drill in a dedicated staging tenant with a synthetic template and
synthetic content. Do not force lease expiry or edit queue rows in production.
Target recovery objectives are RPO 0 for committed presentations and confirmed
templates, rollback RTO 15 minutes, and stalled-import recovery within one lease
period plus one dispatcher interval.

### Stalled-worker recovery

1. Record the commit SHA, deployment tier, database backend, worker mode,
   configured lease/dispatcher intervals, flag state, and allowlist count.
2. Confirm `--mode health` exits 0 and capture the aggregate dashboard baseline.
3. Submit one synthetic PPTX import with a unique idempotency key. Stop only the
   external staging worker after it claims the job; do not stop the API or
   private-source cleanup.
4. Let the lease expire. If the drill window cannot wait, an authorized database
   operator may expire only that recorded synthetic row, after verifying the
   exact primary key and staging database. Record the change and restore normal
   lease settings immediately.
5. Restart the worker and verify a `template_v2_pptx_queue` recovery event with
   `count=1`, then verify the import reaches its expected terminal state.
6. Retry the original request with the same idempotency key. Verify it resolves
   to the same operation and does not create a second template/presentation.
7. Confirm `--mode health` exits 0, no active/stale/failed rows remain, and no
   private source was lost. Record actual recovery time and the aggregate
   before/after counts.

### Flag rollback and restoration

1. Stop new synthetic submissions, run `--mode rollback`, and proceed only on
   exit 0 with `template_v2_rollback_drain_complete`.
2. Record aggregate row counts and content-free digests required to compare the
   staging state; do not export identifiers or content into the drill record.
3. Set `ENABLE_TEMPLATE_V2=false`, restart API and worker processes, and verify
   discovery plus all new Template V2 mutations are blocked.
4. Verify previously committed Template V2 presentations remain readable and
   exportable, and private-source cleanup still runs.
5. Restore the exact prior flag and one-template allowlist, restart processes,
   run readiness and health checks, then generate and export one synthetic deck.
6. Compare the aggregate state/digests from step 2. The drill passes only with
   no lost committed rows, no duplicate publication, no leaked private source,
   health exit 0, and completion inside the RTO.

Attach command exit codes, query snapshots, UTC timestamps, actual RTO/RPO,
incident/issue links, and operator/reviewer approval to the drill record.
Redact database URLs and keep the raw template mapping outside the record.

## Rehearsal record

Executed 2026-07-25 on local Windows against `origin/main` @ `83ec1d21`, with an
isolated `APP_DATA_DIRECTORY` and a real 3-slide PPTX source. Upstream
compatibility held at 426 checks throughout.

| Step | Result |
|---|---|
| Readiness matrix (19 configurations) | Exit `0` for `true`/`TRUE`/`" true "` and a 128-character ID; exit `2` for `1`/`yes`/`on`, unset/blank allowlist, empty entry, trailing comma, `*`, `a,*`, duplicate, 129 characters, and an embedded newline |
| Discovery / write / import gating | Unauthenticated 401; list returns only allowlisted IDs; write and import on a non-allowlisted ID 403; stale `expected_revision` 409 |
| Import lifecycle | `queued` -> `review_required` in 2 s -> `confirmed`, all three slides parsed into layouts |
| Authored + adaptive regression | Full pytest identical with the flag off and on (831 passed, 3 skipped each); live adaptive produced an editable 3-slide PPTX and live authored a 3-slide image deck, both persisted `v1-standard` |
| Dispatcher vs. concurrent load | 12 concurrent create/read/delete workers for 45s, three paired rounds, 60 seeded queue rows so the dispatcher was actively claiming work: 92.4 ops/s and 18.0 failures with the flag on versus 88.1 ops/s and 24.3 failures with it off. The flag-on arm scored slightly better on every metric and the per-round ranges overlap, so the dispatcher adds no measurable contention. The ~0.5% failure rate is SQLite's own write serialization and is present in both arms — see `compatibility/README.md`, "Database compatibility" |
| Rollback | Writes/imports 403, discovery hidden while the row still existed, reads and exports 200, and every row plus every private source preserved |
| Worker mode | `external` starts no embedded dispatcher (job stayed `queued` for 15 s); the external worker then claimed it and emitted content-free `template_v2_pptx_queue` and `template_v2_pptx_analysis` events |

A follow-up on 2026-07-25 measured the Studio flag directly, because the first
rehearsal had assumed it was build-time and skipped it. On one bundle built
*without* the flag, restarting Next.js with `NEXT_PUBLIC_TEMPLATE_V2_STUDIO_ENABLED=true`
served the Studio and `=false` served the not-found fallback — under both
`next start` and the standalone server Docker uses. The env var name also survives
verbatim in the server chunk, i.e. it is read rather than substituted. Cypress ran
95/95 with the flag on, covering the Studio specs and the legacy upload, dashboard,
theme, and generation specs. So including the Studio in a canary costs neither a
rebuild nor a bundle rollout, and it rolls back as fast as the backend flag.

The rehearsal found one defect: neither event family reached any log sink in the
FastAPI process, because the root logger had no handler. Fixed in the same
change set; `template_v2_rollout` went 0 -> 2 and `template_v2_pptx_queue` 0 -> 10
for an identical request sequence, making steps 5 and 6 above actionable as
written.

## Private source retention

An import keeps a private copy of the uploaded `.pptx` outside the served
`/app_data` mount. `SOURCE_CLEANUP_STATES` in
`services/template_v2_pptx_retention_service.py` decides which of them are
reclaimed once `TEMPLATE_V2_PPTX_SOURCE_TTL_DAYS` (default 7) has passed:

| Final state | Uploaded source |
|---|---|
| `review_required`, `failed`, `cancelled` | deleted at TTL |
| `confirmed` | **retained**, so a materialized template can be audited against the original deck |

Cleanup runs in an independent FastAPI lifespan service regardless of
`ENABLE_TEMPLATE_V2`; the dispatcher may also request a cleanup iteration, but
the shared interval and durable row claims prevent duplicate deletion across
processes. A standalone maintenance invocation is available through
`scripts/check_template_v2_operations.py --mode cleanup`.
