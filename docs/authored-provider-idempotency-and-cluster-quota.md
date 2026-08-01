# Authored provider idempotency and cluster quota

## Decision summary

Authored jobs use a database-backed, job-level admission gate in addition to
the existing process-local semaphore. `AUTHORED_CLUSTER_JOB_CONCURRENCY`
defaults to `AUTHORED_JOB_CONCURRENCY` (and ultimately `1`), so adding FastAPI
workers does not silently multiply concurrently billed authored jobs. This is
an additive runtime change and requires no schema migration.

Provider request idempotency is not injected in this change. The provider
interfaces currently used by the authored pipeline do not expose a supported
idempotency-key or per-request-header argument. Mutating private SDK clients or
sharing temporary headers across concurrent calls would be unsafe and would
make behavior provider-specific.

The installed OpenAI Python SDK does expose a generic `extra_headers` escape
hatch on its low-level image call, but the authored image-service interface
does not propagate it and the public OpenAI image API contract does not
document duplicate-suppression semantics for an Idempotency-Key header. A
generic header transport alone is therefore not treated as provider-supported
idempotency.

## Audited call paths

| Stage | Current call path | Supported request idempotency surface |
| --- | --- | --- |
| Author | `_author_illustrate_render_pipeline` -> `author_planned_slide` -> `_author_one_resilient` -> `author_slide_html` -> `llmai.BaseClient.generate` | None. The installed llmai OpenAI, Anthropic, Google and OpenRouter implementations accept generation/body options, but no request idempotency key or header map. |
| Illustrate | `apply_authored_illustration` -> `ImageGenerationService.generate_image` -> selected image provider | None propagated by `ImageGenerationService`; the OpenAI image invocation also receives no idempotency context. |
| Render | Local Playwright/Chromium rendering | Not an externally billed provider call. |
| Persist/publish | Authored checkpoint rows/artifacts and final PPTX persistence | Protected by durable attempt leases/checkpoints; the final presentation is published only after all slides succeed. |

The safe future integration point is an explicit, public provider-adapter
parameter such as `request_idempotency_key`. Once every selected adapter can
honor it, derive a deterministic value from durable identifiers, for example
`sha256(job_id + slide_index + stage + input_hash)`. The input hash is required
so a deliberate content change cannot accidentally reuse a previous provider
response. Do not pass raw user Idempotency-Key values to providers.

## Cluster admission algorithm

Only payloads whose strategy or template is `authored` enter the cluster gate.
Before an attempt token or lease is assigned, the claimant serializes the
quota check and claim:

- PostgreSQL takes the transaction-scoped advisory lock
  `0x505054415554484F` (`5787218160752281679`). The fixed application namespace
  must remain stable across workers and releases.
- SQLite ends the implicit read transaction and issues `BEGIN IMMEDIATE`, then
  re-reads claimability state. This serializes concurrent local writers without
  nesting transactions.
- Other SQL dialects retain the existing per-job compare-and-set and
  process-local semaphore. They log that the cluster quota is unavailable
  instead of relying on a race-prone active-job count.

While holding the serialization primitive, the claimant counts other authored
jobs in `running` or `staging` with a non-null attempt token and an unexpired
lease. When the limit is full, it rolls back without changing the queued job.
The launcher retries after a short asynchronous sleep. Cancellation interrupts
that sleep, so a cookie-required invocation killed before admission remains
queued; the caller can supply the same Idempotency-Key again to restore the
non-persisted cookie and resume it.

Expired leases do not occupy quota. Existing recovery and reconciliation own
the transition of abandoned attempts back to a dispatchable state.

## Operational limits and residual risk

- The gate limits authored **jobs**, not individual provider requests. Peak
  provider calls remain the cluster job limit multiplied by each job's
  author/illustration stage concurrency. Stage-specific distributed token
  buckets would require another durable coordination mechanism.
- A process can be killed after a provider accepted a request but before its
  slide checkpoint commits. Because current adapters lack provider-side
  idempotency, that one in-flight stage may be billed again after lease expiry.
  The checkpoint prevents regeneration only after its transaction commits.
- PostgreSQL behavior has an opt-in, disposable-schema integration test, but it
  must still be enabled in a release gate and run against the production major
  version before raising the cluster limit.
- Advisory locks coordinate only applications using the same fixed key. Any
  separate deployment that claims the same durable-job table must use the same
  gate.

## Verification coverage

`tests/unit/test_authored_cluster_quota.py` covers simultaneous SQLite claims,
preservation of the blocked job as an untouched queued retry, admission after
the running job finalizes, legacy/non-authored bypass, environment fallback,
and cancellation while waiting before a lease is claimed.

`tests/integration/test_postgresql_authored_cluster_quota.py` uses two spawned
Python processes with independent async engines to verify the PostgreSQL
transaction advisory lock, quota `1`, exactly one successful simultaneous
claim, durable cancellation, expired-lease capacity release, takeover fencing,
and stale-token rejection. It follows the repository's PostgreSQL safety gate:

```powershell
$env:PPT_AGENT_POSTGRES_TEST_URL = "postgresql://.../presenton_test"
$env:PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION = "1"
uv run pytest -q tests/integration/test_postgresql_authored_cluster_quota.py
```

The target database name must end in `test` or `tests`. The test creates and
removes only a unique schema; it never resets `public`. Keep the URL in an
environment variable rather than source control or command-line arguments.
