# Provider receipt 90-slide staging operations runbook (2026-08-13)

## Scope and safety state

This runbook covers the privacy-safe provider receipt benchmark and the 90-slide
capacity exercise. It does not authorize a paid call. The checked-in corpus is
fixed metadata only: 90 slide IDs, archetypes, and bounded complexity/export
units. It contains no customer prompt, source document, generated HTML, URL,
request header, exception body, credential, or API key.

`scripts/provider_operations.py` uses only Python's standard library, imports no
provider client, and opens no network connection. `validate`, `dry-run`,
`offline`, and `staging-gate` all report zero network/provider calls. Even with a
valid change ID, `staging-gate` only writes a manifest and leaves
`executor_enabled=false`. This separation is intentional: approval is necessary
but never turns a planning command into an implicit paid execution.

The price card `ops-ceiling-20260813` is a fixed operational assumption, not a
provider quote. Immediately before approval, the operator must compare it with
the contracted provider price card and replace the fixture/version through the
normal reviewed change process if the provider price is higher. Never lower the
ceiling solely to make a gate pass.

## Reproducible commands

Run from `servers/fastapi`.

```powershell
uv run python scripts/provider_operations.py `
  --fixture tests/fixtures/provider-operations-90.json `
  validate

uv run python scripts/provider_operations.py `
  --fixture tests/fixtures/provider-operations-90.json `
  dry-run `
  --output artifacts/provider-operations/dry-run.json

uv run python scripts/provider_operations.py `
  --fixture tests/fixtures/provider-operations-90.json `
  offline `
  --output artifacts/provider-operations/offline-90x3.json
```

Expected invariant results are 90 fixed slides, 3 runs, 270 completed logical
slides, one bounded retry, one cancel/checkpoint/resume, 272 modeled attempts,
retry amplification `1.007407`, and zero network, provider, and paid calls. A
second offline invocation must produce the same report byte-for-byte except that
the CLI does not add a wall-clock timestamp, so the whole report is deterministic.

The dry-run ceiling is 180 requests, 432,000 input tokens, 648,000 output tokens,
180 images, and 19,080,000 USD microunits (`$19.08`) under price card
`ops-ceiling-20260813`. This is a maximum-attempt planning ceiling, not an invoice
forecast. The job budget is 20,000,000 microunits; execution must cancel before
starting an attempt that could cross it.

## Staging approval gate and exact paid command

Before staging, record a CAB/change approval in the form `CHG-<6..12 digits>` or
`CAB-<6..12 digits>`, confirm the provider hostname in the egress allowlist, and
write the immutable gate manifest. The CLI validates only the reference format;
it cannot verify the external change system and therefore records
`reference_recorded_unverified`, never `approved`:

```powershell
uv run python scripts/provider_operations.py `
  --fixture tests/fixtures/provider-operations-90.json `
  staging-gate `
  --endpoint https://api.openai.com `
  --approval-id CHG-2026081301 `
  --output artifacts/provider-operations/staging-approved.json
```

The planner still performs no paid call. The manifest also contains these exact
commands as `paid_execution_commands` but leaves `paid_execution_authorized=false`
and `executor_enabled=false`. After a human verifies the change in the external
approval system, price version, `$19.08` ceiling, and 20,000,000-microunit kill
budget, the exact generation commands for three generic, non-sensitive 30-slide
decks are:

```powershell
$gate = Get-Content -LiteralPath artifacts/provider-operations/staging-approved.json -Raw | ConvertFrom-Json
if ($gate.approval.state -ne 'reference_recorded_unverified') { throw 'Approval reference is missing' }
$approvalVerifiedByHuman = $false # Set true only after checking the external CAB/change record.
if (-not $approvalVerifiedByHuman) { throw 'External approval has not been verified' }
if ($gate.estimated_ceiling.estimated_cost_microunits -gt 20000000) { throw 'Cost ceiling exceeds job budget' }

node ..\..\scripts\ppt-agent.mjs --content "Public operational capacity fixture A" --slides 30 `
  --mode authored --async --timeout 1800 --base http://127.0.0.1:8000
node ..\..\scripts\ppt-agent.mjs --content "Public operational capacity fixture B" --slides 30 `
  --mode authored --async --timeout 1800 --base http://127.0.0.1:8000
node ..\..\scripts\ppt-agent.mjs --content "Public operational capacity fixture C" --slides 30 `
  --mode authored --async --timeout 1800 --base http://127.0.0.1:8000
```

These commands are recorded for the approved future staging window and were not
run during implementation. The local FastAPI instance must already be configured
with tenant/user-scoped credential references; no secret may appear in the shell,
fixture, gate manifest, or report. Do not attach to or terminate an existing
PowerPoint process. Do not enable web search. The three CLI invocations are
sequential to keep the outer job concurrency at one; provider-internal scheduling
must still enforce its own RPM/concurrency policy.

## Endpoint and egress policy

The gate accepts only an exact HTTPS origin on port 443. Userinfo, query,
fragment, non-root path, IP literal, loopback/localhost, suffix-confusion host,
and non-allowlisted host are rejected. The execution layer must additionally:

1. Resolve all A/AAAA records immediately before each connection and reject
   loopback, link-local, private, carrier-grade NAT, multicast, reserved,
   documentation, and metadata ranges, including `169.254.169.254`.
2. Pin the validated address only for the connection; repeat resolution and
   validation after TTL expiry. DNS rebinding must fail closed.
3. Disable automatic redirects. If a provider contract requires a redirect,
   validate every hop with the same scheme/hostname/DNS/IP policy and a small
   audited hop limit. A redirect may never carry credentials to another origin.
4. Route provider, image, search, font, and telemetry traffic through the audited
   egress manifest. Self-host default is deny/off for every category. Enabling one
   category does not enable another. Telemetry payloads are aggregate receipt
   metrics only; prompts, documents, slide HTML, URLs, headers, exception bodies,
   stable user identifiers, and credentials are forbidden.

Recommended manifest decision fields are category, provider identifier,
allowlisted hostname, purpose, enabled boolean, tenant scope, expiry, approver,
and last verification time. Emit the decision and a non-sensitive reason code;
never log the credential reference value or resolved secret.

## Queue, backpressure, retry, circuit breaker, budget, and cancellation

The fixture's scheduler contract is a token bucket at 60 request starts/minute,
maximum concurrency 4, and bounded FIFO depth 8. The deterministic report uses
both constraints to emit RPM, concurrency, and overall capacity floors; it does
not claim to have run a real worker queue. When the queue is full, reject
before enqueue with `429` and `Retry-After`; do not create an unbounded background
task. Fairness is tenant-aware, and a single tenant cannot consume all four slots.

Retry only explicitly retryable provider failures. Use capped exponential backoff
with jitter and the provider's `Retry-After`. The fixture permits at most two
attempts per logical slide. Retries and resumed attempts count against RPM,
concurrency, request amplification, and cost budget. Idempotency keys prevent a
duplicate completion receipt from adding cost twice; `unavailable` usage remains
null, never zero.

Open the circuit after five consecutive eligible failures, stop new dequeues for
60 seconds, and probe with one half-open request. Auth/configuration/privacy errors
open or quarantine immediately and are never retried. Cancellation persists a
checkpoint before releasing the lease. Resume uses the same logical job/slide
identity, creates a distinct attempt receipt, skips completed slides, and cannot
reset the original job budget.

Kill and cancel the job, stop dequeueing, preserve the checkpoint, and open an
incident if any of these thresholds are reached or exceeded:

| Metric | Limit |
|---|---:|
| Estimated cumulative cost | 20,000,000 microunits |
| Retry amplification | 1.05 |
| Critical-path p99 | 5,000 ms in the deterministic model |
| RAM peak | 400 MiB in the deterministic model |
| Export p99 | 500 ms in the deterministic model |

Production/staging wall-clock thresholds must be separately calibrated. The
offline CPU/RAM numbers are explicitly labeled
`deterministic_capacity_model_not_host_telemetry`; they prove report plumbing and
kill evaluation, not physical host performance.

## Required report and dashboards

Archive the gate manifest, receipt aggregate, and run report under the approved
change record. The report must include p50/p95/p99/max/sample count per stage,
critical path, retry amplification, reported and estimated cost separately,
pricing version, queue depth/backpressure, circuit state, CPU, RAM, export tail,
checkpoint/resume outcome, and null usage counts. Raw provider request/response
payloads are not an observability feature and must not be retained.

Dashboards use tenant-safe aggregate dimensions only:

- SLO: completed logical slides / accepted logical slides, excluding explicit
  user cancellations; target 99.0% over 30 days.
- Latency: end-to-end job and critical-path p50/p95/p99, plus provider and export
  stages. A receipt-only critical path is a lower-bound proxy and is labeled so.
- Capacity: active workers, queue depth/age, RPM tokens, concurrency, 429
  backpressure, circuit state, cancellations, and resume completion.
- Provider: request count, retry amplification, throttles, timeouts, usage-state
  counts, price version, estimated/reported cost, and budget remaining.
- Host/export: CPU p95/p99, RAM peak, export p95/p99, disk free space, and export
  failure rate.
- Privacy/egress: denied egress by category/reason, redirect denial, DNS/IP policy
  denial, telemetry disabled/enabled state, and forbidden-field validation count.

Alert routing and first actions:

| Alert | Trigger | First action |
|---|---|---|
| Page: job budget | projected or actual cost reaches 90% | pause dequeue; confirm price/version and attempts |
| Page: circuit open | any production circuit open for 5 minutes | stop affected provider route; inspect safe reason codes |
| Page: queue age | oldest accepted job exceeds SLO for 10 minutes | apply backpressure; reduce intake; check RPM |
| Page: retry amplification | 5-minute value greater than 1.05 | pause retries; inspect throttle/timeouts |
| Page: privacy/egress | any forbidden telemetry emission or policy bypass | disable category; rotate affected secret if exposure is possible |
| Ticket: export tail | p99 above calibrated limit for 15 minutes | inspect CPU/RAM/disk and export worker saturation |
| Ticket: unavailable usage | non-zero count for two windows | preserve null semantics; reconcile provider reporting |

## Backup, restore, RPO, and RTO

Receipt rows and checkpoints are operational records; database backups contain
only secret references, never provider secret material. Desktop vault and server
KMS backups follow their platform's encrypted recovery procedure and access
control, independently of the application database.

Targets:

- Database/checkpoint RPO: 15 minutes using continuous WAL/binlog or an equivalent
  managed point-in-time mechanism. SQLite self-host deployments take an online
  SQLite backup at least every 15 minutes to an encrypted, access-controlled
  target; never copy a live file with a raw filesystem copy.
- Database RTO: 60 minutes to a clean replacement instance, including migrations,
  integrity checks, receipt reconciliation, and one offline smoke run.
- Vault/KMS recovery RTO: 4 hours. If unavailable, generation remains disabled;
  the application must not fall back to plaintext secrets.
- Quarterly restore drill and evidence retention for at least the incident/audit
  period required by the deployment owner.

Restore procedure:

1. Freeze intake and record the last accepted job/receipt cursor. Do not kill an
   unknown existing PowerPoint or provider process.
2. Provision a clean isolated database target. Restore to a timestamp before the
   incident, then run the repository's normal Alembic upgrade against the target.
3. Run database-native integrity validation. Confirm receipt idempotency-key
   uniqueness, nonnegative numeric constraints, pricing-version preservation, and
   that `usage_state=unavailable` has null token/cost fields.
4. Reconnect the application using tenant/user-scoped secret references. Recover
   vault/KMS entries only through its native audited recovery path.
5. Run fixture validation, dry-run, and offline 90x3. Confirm 270 completions,
   deterministic retry/cancel/resume, no kill condition, and zero external calls.
6. Reconcile accepted jobs after the backup cursor. Resume only checkpointed,
   incomplete jobs with their original budget and idempotency identity. Never
   replay completed provider attempts merely to fill a metric gap.
7. Reopen intake gradually (one worker, then normal concurrency) while watching
   queue, circuit, retry amplification, cost, CPU/RAM, and export tail. Record
   actual RPO/RTO in the restore-drill evidence.

## Incident evidence and stop conditions

Preserve aggregate reports, manifest hash, approval ID, price version, code SHA,
fixture hash, migration revision, safe error codes, and timestamps. Redact or
discard any accidental payload immediately under the privacy incident process;
do not attach it to the incident ticket. A failed privacy negative test,
non-allowlisted egress, secret in a DB/report/log, missing price version, cost
treated as zero when unavailable, checkpoint mismatch, duplicate-cost receipt,
or non-deterministic offline result blocks staging regardless of schedule.
