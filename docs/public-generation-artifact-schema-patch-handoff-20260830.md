# Public artifact schema patch handoff (G3 -> G2)

This is a DB-only handoff. G3 did not edit an existing migration or model and
did not authorize activation of the shadow artifact path.

## Required relations and predicates

Add the following tables/columns in one additive migration. UUIDs should use
the repository's existing UUID type and all timestamps must be timezone-aware.

### `public_artifacts`

Add `lifecycle_state` (`provisional|committed|tombstoned|deleted`),
`expires_at`, `tombstoned_at`, `deleted_at`, `refcount` (default `0`, check
`refcount >= 0`), and nullable `last_gc_error`. Add a check that `deleted` rows
have `refcount = 0`. Publication/checkpoint foreign keys must be restricted to
rows whose state is `committed` (enforce with a transactional repository check
or a database trigger; a plain FK is insufficient).

### `public_export_identities`

The identity uniqueness key must include owner, tenant, presentation, source
revision, scene/semantic/quality/exporter digests, export mode, and the font
manifest digest. Add `fencing_token` (monotonically increasing), lease expiry,
and nullable result artifact FK. Claim and completion must compare identity,
worker, attempt-token hash, and fence in one transaction.

### `public_artifact_publications`

Create with unique `(operation_id, publication_name)` and fields `revision`,
`artifact_id`, `attempt_token_hash`, `fencing_token`, `published_at`. The
publication update predicate is the current operation attempt plus equal or
newer fence; stale attempts affect zero rows and never replace the pointer.

### `public_slide_checkpoints`

Create with unique `(operation_id, revision, slide_index, input_digest,
stage_version)` and fields `artifact_id`, `attempt_token_hash`,
`fencing_token`, `committed_at`. Require the target artifact to be committed.
Checkpoint writes and refcount increments must be one transaction; replacing a
checkpoint decrements the old artifact only after the new pointer is durable.

## GC and activation gates

GC may tombstone only expired rows with `refcount = 0`, no publication pointer,
and no active export identity. Physical deletion happens after tombstoning and
must tolerate an idempotent retry. A provisional object without a committed
row is an orphan after its retention window and may be swept independently.

Before making this authoritative, G2/integration must produce cross-node
resume, range download, 100-way singleflight, stale-fence rejection, and
referenced-object preservation evidence. The current G3 unit checks are local
adapter/shadow evidence only.
