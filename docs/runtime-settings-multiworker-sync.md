# Multi-worker runtime settings synchronization

Status: accepted on 2026-08-10

## Decision

Runtime provider settings use the existing singleton `provider_settings` database
row as their durable authority. The row carries a monotonic integer `revision`.
Every FastAPI process and provider-sensitive standalone worker publishes an
immutable in-process `(revision, RuntimeSettings)` snapshot and monitors the
database revision in a background task.

The monitor polls only the revision during the normal cadence. It loads the raw
configuration only after observing a newer revision, then atomically replaces
the process snapshot. Ordinary HTTP requests and jobs never read the settings
file or database to resolve runtime provider settings.

This gives healthy workers bounded eventual convergence: one polling interval
plus a database round trip. A settings write does not synchronously wait for an
acknowledgement from every worker. Strict cluster-wide read-after-write would
require worker membership, authenticated acknowledgements, leases, and recovery;
that is intentionally outside this minimal design.

## Why the database revision is the smallest consistent mechanism

The provider settings row is already shared by API processes and external
workers, and the application already requires a database. Adding a revision and
a lightweight monitor therefore adds no mandatory infrastructure.

- Redis Pub/Sub would add a required service while remaining lossy. Durable
  recovery would still need a database revision. Redis Streams would also add
  consumer and retention operations.
- File watching is retained only as Electron compatibility ingress. Pod-local
  files, network-filesystem notification semantics, and multi-host deployment
  make a file revision unsuitable as cluster authority.
- Signals, shared memory, and local sockets do not cross host or pod boundaries.
  Making them reliable would require a worker registry and acknowledgements.
- PostgreSQL `LISTEN/NOTIFY` could later reduce wake-up latency, but periodic
  revision recovery would still be required. It is not part of this contract.

`updated_at` remains audit metadata. It is not an ordering key because clock
skew, timestamp precision, and concurrent writes can make it non-monotonic or
ambiguous.

## Write and publication contract

Provider-setting writers sanitize input and use the existing redacted-secret
placeholder merge rules. The redacted placeholder is an API representation,
never a credential, and must not be stored as a secret value.

Each effective change uses a compare-and-swap update against the revision it
read. A semantic no-op keeps the current revision. A successful change advances
the revision by exactly one and returns the exact committed config and revision
to the internal caller. Only the redacted representation crosses the HTTP
response boundary.

Process publication follows these rules:

- a higher revision atomically replaces the immutable snapshot;
- an equal revision with equal settings is an idempotent duplicate;
- an equal revision with different settings is an invariant failure and keeps
  the existing snapshot;
- a lower revision is stale or out of order and is ignored.

Requests and background tasks capture one immutable snapshot at their boundary.
In-flight work continues with that object; only later work sees a replacement.

## Runtime paths

### External API and worker deployments

After migrations and table creation, a process loads and publishes the current
database revision before starting provider-sensitive dispatchers. It then starts
the revision monitor and stops it before disposing database engines.

The same lifecycle applies to FastAPI processes and supported standalone worker
entrypoints. An admin write publishes its committed snapshot immediately in the
receiving process; other workers catch up through revision polling.

When `CAN_CHANGE_KEYS=false`, environment configuration remains immutable and
database provider settings do not override it.

### Electron

Electron retains its existing control boundary: the main process generates the
random token, writes the user configuration first, and sends an empty authenticated
POST over IPv4 loopback. The token is not exposed to the renderer or preload API.

After authorization, FastAPI imports the just-written compatibility file through
the same database compare-and-swap writer and publishes that exact committed
revision locally. Responses remain empty: `204` on success and non-disclosing
`404` or `500` on failure. No configuration values or secrets are carried in the
control request, synchronization signal, or response.

The IPC persistence write records a fresh pending nonce in the same atomic file
replacement. Normal database-to-file mirrors never overwrite a pending candidate,
even when they carry a higher revision. An ingress completion clears the marker
only when the nonce it read still matches, so an overlapping newer IPC save is
not erased. If another database writer advanced the base revision first, ingress
restores the latest database mirror for that same nonce and returns an empty
failure; the caller must retry instead of receiving a false-success response.
The pending nonce is file-local metadata and is never used as, or transmitted
with, the loopback control token.

## Database profiles

PostgreSQL is the supported shared cluster profile. Its row-level transactions
and compare-and-swap update coordinate writers across hosts and pods.

SQLite remains supported for Electron, local development, tests, and multiple
local processes sharing one database file. WAL and the configured busy timeout
allow bounded writer hand-off. First-connection WAL transitions are verified and
retried within a fixed budget so simultaneous local worker startup fails closed
instead of running with mixed journal modes. SQLite is not a supported multi-host
or shared network-filesystem cluster database.

## Failure and recovery

- Initial authoritative load failure stops process startup instead of serving an
  ambiguous partial configuration.
- A monitor failure after startup retains the last-known-good immutable snapshot
  and retries with capped backoff. Recovery reads the latest revision, so skipped
  intermediate revisions do not need replay.
- Compare-and-swap conflicts retry from a fresh row within a bounded budget. A
  caller that exhausts the budget receives a generic retryable failure.
- Invalid newer data never replaces the last-known-good snapshot.
- The database remains authoritative if a best-effort legacy-file mirror fails.
- A process restart loads the latest durable revision before provider-sensitive
  work starts.
- Rolling the database back to a lower revision while workers remain live is not
  supported. Restart all workers or advance the restored row above the previous
  maximum.

Safe observability contains only event names, source, old/new revision, outcome,
retry count or delay, and an exception class name where needed. Logs, metrics,
events, and responses must never contain settings values, setting names, secret
hashes, database URLs, file paths, control tokens, request bodies, or raw exception
messages.

## Security invariants

This design preserves the following contracts:

- secrets are not transmitted in refresh notifications or returned by settings
  APIs;
- the Electron control endpoint remains loopback-only and token-gated;
- the redacted-secret placeholder cannot replace or become a stored credential;
- `RuntimeSettings` and each published revision envelope are immutable;
- snapshot replacement is atomic and revision-monotonic within every process;
- request and task boundaries pin one snapshot;
- ordinary requests perform no settings file or database read;
- retries and observability expose metadata only.
