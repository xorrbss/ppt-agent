# Artifact/export durability shadow contract

Status: candidate implementation contract. This document defines a feature-gated
handler boundary only; it does not authorize a DB migration, public cutover, or
the replacement of the existing workspace artifact publication authority.

## Current boundary

`WorkspaceExportOperationHandler` retains the existing publication path by
default. Setting `PRESENTON_ARTIFACT_EXPORT_DURABILITY_SHADOW=true` enables an
in-process, fail-closed validation of the returned immutable `ArtifactRef` before
the legacy staging path is used. The shadow validates `owner`, `tenant`,
`created_by_operation`, export kind, SHA-256 shape, non-negative size, and the
claimed fence. It neither writes a publication pointer nor changes lifecycle.

The shadow has two deliberate limits:

- Existing code still calls `read_ref_bytes` and `_stage_public_artifact_cache`,
  so it buffers the complete export and stores a local staging copy.
- `ArtifactRef` has no promotion-fence or lifecycle value yet, so the handler
  cannot prove fenced pointer promotion. It must not infer that proof from a
  successfully rendered object.

## Required ports before activation

The object-storage lane should provide these streaming, immutable interfaces.
Names are proposals; their input/output invariants are the contract.

```text
stage_stream(owner, tenant, operation, kind, media_type, chunks)
  -> ProvisionalArtifactRef(digest, size, provisional_key)
commit_provisional(provisional, expected_digest, expected_size)
  -> ArtifactRef
promote_if_current(operation, revision, attempt_token, fence, artifact_ref)
  -> PublicationPointer | stale_fence
put_checkpoint(operation, revision, slide_index, input_digest, stage_version, artifact_ref, fence)
  -> CheckpointPointer | stale_fence
open_download(owner, tenant, artifact_ref, range_request)
  -> signed_get_or_stream_headers
collect_garbage(now, batch_limit) -> GCResult
```

`stage_stream` must consume chunks without assembling the file in API memory.
`commit_provisional` verifies byte digest and length before moving an object into
the committed content-addressed namespace. `promote_if_current` is the only
operation allowed to replace a public pointer, and it must compare the DB
attempt token and monotonic fence in the same transaction. A stale result may
remain a provisional object but must not update a pointer, operation, export
identity, or checkpoint.

## Lifecycle, failover, and GC

The required lifecycle is `provisional -> committed -> tombstoned -> deleted`.
Only committed objects can be referenced by checkpoints or publication pointers.
Tombstoning is DB-authoritative; physical deletion occurs only when the object
is tombstoned, retention has elapsed, `refcount = 0`, and no active export
identity or pointer references it. A crash before commit leaves an orphaned
provisional object eligible for GC after its retention period.

On worker failover, the next claim receives a strictly larger fence. It may
resume only from a committed checkpoint whose operation/revision/slide/input
digest/stage-version bindings match. Outputs from the prior fence are rejected
by `promote_if_current` and `put_checkpoint`.

Downloads re-authorize owner and tenant, preserve `Range`, `Content-Length`,
and `ETag`, and return a short-lived signed GET (or streaming headers) without
full-file buffering. The local `FileResponse` compatibility path is development
only and is not a durable authority.

## DB schema handoff to the admission/model lane

No model or migration is changed by this lane. The following additions are
required before the shadow can become authoritative:

| Relation | Required fields / constraint |
|---|---|
| `public_artifacts` | `lifecycle_state` (`provisional`, `committed`, `tombstoned`, `deleted`), `expires_at`, `tombstoned_at`, `deleted_at`, `refcount >= 0`, `last_gc_error`; only `committed` may be target of a pointer/checkpoint. |
| `public_export_identities` | unique identity binds `owner_id`, `tenant_id`, presentation/revision, `scene_digest`, `semantic_digest`, `quality_receipt_digest`, `export_mode`, `exporter_digest`; durable `fencing_token`, lease expiry, and result artifact FK. |
| `public_artifact_publications` (new) | unique `(operation_id, publication_name)`; `revision`, `artifact_id`, `attempt_token_hash`, `fencing_token`, `published_at`; pointer replacement is conditional on current attempt and fence. |
| `public_slide_checkpoints` (new) | unique `(operation_id, revision, slide_index, input_digest, stage_version)`; `artifact_id`, `attempt_token_hash`, `fencing_token`, `committed_at`; FK target requires committed artifact. |
| artifact references | increment/decrement accounting for every publication, checkpoint, and succeeded export identity must be transactionally coupled to pointer changes. |

The exact migration owner must add DB constraints/triggers or transactional
repository checks enforcing the committed-target and current-fence predicates.
The handler must not emulate those predicates with local files or process state.

## Activation evidence still required

Before any authority switch, prove: node A generation -> B render -> C export ->
D range download; failover resumes from a committed checkpoint; 100 identical
digest-bound export requests execute once; stale-fence promotion is rejected;
and GC reclaims a crash orphan without deleting a referenced object. None of
those are verified by the shadow unit test.
