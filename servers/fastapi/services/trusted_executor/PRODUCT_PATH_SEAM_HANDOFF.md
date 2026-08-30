# Product work-item seam handoff

The existing public render/export path is:

```text
public_export_operation.execute_isolated_{render,export}_operation
  -> isolated_worker_process.execute_isolated_worker_job(job)
  -> workers.public_{render,export}_worker
```

`utils.authored_renderer_worker` is not on that path and must not be used as a
surrogate hook.

## Integrated boundary

`execute_isolated_worker_job` no longer has a direct subprocess fallback. It
requires a composition-root `TrustedWorkerDispatcher`, which schedules each
immutable job through `TrustedExecutorProductAdapter` and binds the admitted
presentation identity, staged ArtifactRef, sandbox layout, argv, environment,
deadline, and JSON result.

1. build `ExecutionIdentity` from `job.operation_id`, `job.input_artifact.tenant_id`,
   `job.input_artifact.owner_id`, and the admitted presentation id;
2. stage the exact input artifact and construct a non-null
   `SandboxFilesystemSpec` plus `SandboxLaunchSpec` for the existing absolute
   worker argv;
3. submit a `TrustedExecutorWorkItem` whose `ScheduledOperation` has the same
   operation/tenant/owner/presentation ids and policy-derived node/weight;
4. let the central dispatcher call `execute_next()` with the installed platform wire transport;
5. preserve the current JSON stdin/result schema, environment allow-list,
   attempt/fence binding, result decoding, and the caller's `IsolatedWorkerResult`;
6. map verified process outcomes to `deadline_exceeded` and
   `memory_limit_exceeded`; cancellation must call the supervisor cancel path
   and require descendant-zero proof before exposing a result.

The platform transport remains deployment-supplied. It must stage the exact
ArtifactRef, provide supervised stdin/stdout or a named-pipe equivalent, map
resource outcomes, and prove descendant-zero cancellation. If no transport is
installed the product path fails closed with `trusted worker dispatcher is
unavailable`; it never falls back to an unsupervised process.
