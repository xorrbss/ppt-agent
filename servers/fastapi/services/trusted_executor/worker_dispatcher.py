"""Product wire port from admitted worker jobs into the trusted executor.

The dispatcher owns fair scheduling and the complete immutable job/result wire
binding.  A platform transport must stage the supplied ArtifactRef, supervise
the process tree, and return stdout bytes; no direct subprocess fallback is
selected by this module.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import time
from typing import Mapping, Protocol, Sequence
import uuid

from services.isolated_worker_contracts import IsolatedWorkerJob
from services.trusted_executor.contracts import (
    ExecutionIdentity,
    ResourceLimits,
    SandboxFilesystemSpec,
    SandboxLaunchSpec,
    SandboxPolicy,
)
from services.trusted_executor.product_adapter import (
    ProductExecutionResult,
    TrustedExecutorProductAdapter,
    TrustedExecutorWorkItem,
)
from services.trusted_executor.weighted_scheduler import ScheduledOperation


class TrustedWorkerWireError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        resource_metrics: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.resource_metrics = dict(resource_metrics or {})
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TrustedWorkerWireResult:
    stdout: bytes
    exit_code: int = 0
    resource_metrics: Mapping[str, object] = field(default_factory=dict)


class TrustedWorkerWireTransport(Protocol):
    async def execute(
        self,
        *,
        work_item: TrustedExecutorWorkItem,
        job: IsolatedWorkerJob,
        payload: bytes,
        environment: Mapping[str, str],
    ) -> TrustedWorkerWireResult: ...


class TrustedWorkerDispatcher:
    """Serialize fair dispatch while preserving per-operation wire results."""

    def __init__(
        self,
        *,
        adapter: TrustedExecutorProductAdapter,
        transport: TrustedWorkerWireTransport,
        staging_root: Path,
        node_id: str,
    ) -> None:
        root = Path(staging_root)
        if not root.is_absolute() or not node_id.strip():
            raise ValueError("trusted worker dispatcher configuration is invalid")
        self._adapter = adapter
        self._transport = transport
        self._staging_root = root
        self._node_id = node_id.strip()
        self._dispatch_lock = asyncio.Lock()

    async def execute(
        self,
        *,
        job: IsolatedWorkerJob,
        presentation_id: uuid.UUID,
        payload: bytes,
        command: Sequence[str],
        environment: Mapping[str, str],
    ) -> TrustedWorkerWireResult:
        if not command or not Path(command[0]).is_absolute():
            raise TrustedWorkerWireError("trusted worker executable must be absolute")
        attempt_scope = hashlib.sha256(job.attempt_token.encode("utf-8")).hexdigest()[:16]
        operation_root = (
            self._staging_root
            / str(job.operation_id)
            / job.role
            / f"fence-{job.fencing_token}-{attempt_scope}"
        )
        input_root = operation_root / "input"
        temporary_root = operation_root / "tmp"
        staged_input = input_root.joinpath(*job.input_artifact.storage_key.split("/"))
        deadline_seconds = max(1, job.deadline_epoch_seconds - int(time.time()))
        executable = Path(command[0])
        arguments = tuple(command[1:])
        launch_spec = SandboxLaunchSpec(
            identity=ExecutionIdentity(
                operation_id=job.operation_id,
                tenant_id=job.input_artifact.tenant_id,
                owner_id=job.input_artifact.owner_id,
                presentation_id=presentation_id,
            ),
            role=job.role,
            executable=executable,
            arguments=arguments,
            limits=ResourceLimits(
                memory_bytes=2 * 1024 * 1024 * 1024,
                process_limit=32,
                deadline_seconds=min(deadline_seconds, 600),
                temporary_bytes=512 * 1024 * 1024,
            ),
            policy=SandboxPolicy(),
            filesystem=SandboxFilesystemSpec(
                sandbox_root=operation_root,
                input_root=input_root,
                temporary_root=temporary_root,
                staged_inputs=(staged_input,),
            ),
        )
        work_item = TrustedExecutorWorkItem(
            scheduling=ScheduledOperation(
                operation_id=str(job.operation_id),
                node_id=self._node_id,
                tenant_id=job.input_artifact.tenant_id,
                owner_id=str(job.input_artifact.owner_id),
                presentation_id=str(presentation_id),
            ),
            launch_spec=launch_spec,
        )
        wire_result: TrustedWorkerWireResult | None = None

        async def run(selected: TrustedExecutorWorkItem) -> ProductExecutionResult:
            nonlocal wire_result
            if selected is not work_item:
                raise TrustedWorkerWireError("trusted worker dispatch identity mismatch")
            wire_result = await self._transport.execute(
                work_item=selected,
                job=job,
                payload=payload,
                environment=environment,
            )
            return ProductExecutionResult(
                status="completed",
                exit_code=wire_result.exit_code,
            )

        async with self._dispatch_lock:
            self._adapter.submit(work_item)
            executed = await self._adapter.execute_next(run)
        if executed is None or executed.work_item is not work_item or wire_result is None:
            raise TrustedWorkerWireError("trusted worker was not dispatched")
        if executed.result.status != "completed":
            raise TrustedWorkerWireError("trusted worker execution failed")
        return wire_result


__all__ = [
    "TrustedWorkerDispatcher",
    "TrustedWorkerWireError",
    "TrustedWorkerWireResult",
    "TrustedWorkerWireTransport",
]
