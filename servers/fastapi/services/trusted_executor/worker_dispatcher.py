"""Product wire port from admitted worker jobs into the trusted executor.

The dispatcher owns fair scheduling and the complete immutable job/result wire
binding.  A platform transport must stage the supplied ArtifactRef, supervise
the process tree, and return stdout bytes; no direct subprocess fallback is
selected by this module.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
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


@dataclass(slots=True)
class _PendingDispatch:
    work_item: TrustedExecutorWorkItem
    job: IsolatedWorkerJob
    payload: bytes
    environment: Mapping[str, str]
    result: asyncio.Future[TrustedWorkerWireResult]
    runner: asyncio.Task[object] | None = None
    submitted: bool = False


class TrustedWorkerDispatcher:
    """Serialize scheduler bookkeeping while allowing cap-safe wire concurrency."""

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
        self._pending: dict[str, deque[_PendingDispatch]] = {}

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
        operation_id = work_item.scheduling.operation_id
        dispatch = _PendingDispatch(
            work_item=work_item,
            job=job,
            payload=payload,
            environment=dict(environment),
            result=asyncio.get_running_loop().create_future(),
        )
        async with self._dispatch_lock:
            queue = self._pending.setdefault(operation_id, deque())
            queue.append(dispatch)
            if len(queue) == 1:
                self._submit_locked(dispatch)
        try:
            await asyncio.shield(self._pump())
            return await asyncio.shield(dispatch.result)
        except asyncio.CancelledError:
            await self._cancel(operation_id, dispatch)
            raise

    async def _pump(self) -> None:
        """Reserve every eligible item under the lock, then start transports outside it."""

        gates: list[asyncio.Event] = []
        async with self._dispatch_lock:
            while gate := await self._reserve_one_locked():
                gates.append(gate)
        for gate in gates:
            gate.set()

    async def _reserve_one_locked(self) -> asyncio.Event | None:
        """Make one adapter reservation while ``_dispatch_lock`` is held."""

        selected = asyncio.get_running_loop().create_future()
        transport_gate = asyncio.Event()
        release_lock_owned = False
        selected_dispatch: _PendingDispatch | None = None
        selected_wire_result: TrustedWorkerWireResult | None = None

        async def run(work_item: TrustedExecutorWorkItem) -> ProductExecutionResult:
            nonlocal release_lock_owned, selected_dispatch, selected_wire_result
            operation_id = work_item.scheduling.operation_id
            queue = self._pending.get(operation_id)
            selected_dispatch = queue[0] if queue else None
            if selected_dispatch is None or selected_dispatch.work_item is not work_item:
                raise TrustedWorkerWireError("trusted worker dispatch identity mismatch")
            selected.set_result(selected_dispatch)
            await transport_gate.wait()
            try:
                selected_wire_result = await self._transport.execute(
                    work_item=work_item,
                    job=selected_dispatch.job,
                    payload=selected_dispatch.payload,
                    environment=selected_dispatch.environment,
                )
                return ProductExecutionResult(
                    status="completed",
                    exit_code=selected_wire_result.exit_code,
                )
            finally:
                # The adapter releases its generation-fenced reservation immediately
                # after this callback returns.  Reacquire the bookkeeping lock so the
                # release and the next fair selection cannot interleave.
                await self._dispatch_lock.acquire()
                release_lock_owned = True

        async def execute_reserved() -> object:
            try:
                executed = await self._adapter.execute_next(run)
                if executed is not None and executed.result.status != "completed":
                    raise TrustedWorkerWireError("trusted worker execution failed")
                if executed is not None:
                    if selected_dispatch is None or selected_wire_result is None:
                        raise TrustedWorkerWireError("trusted worker was not dispatched")
                    if not selected_dispatch.result.done():
                        selected_dispatch.result.set_result(selected_wire_result)
                return executed
            except BaseException as error:
                if selected_dispatch is not None and not selected_dispatch.result.done():
                    if isinstance(error, asyncio.CancelledError):
                        selected_dispatch.result.cancel()
                    else:
                        selected_dispatch.result.set_exception(error)
                raise
            finally:
                if release_lock_owned:
                    if selected_dispatch is not None:
                        operation_id = selected_dispatch.work_item.scheduling.operation_id
                        queue = self._pending.get(operation_id)
                        if queue and queue[0] is selected_dispatch:
                            queue.popleft()
                            if queue:
                                self._submit_locked(queue[0])
                            else:
                                self._pending.pop(operation_id, None)
                    self._dispatch_lock.release()
                    await self._pump()

        runner = asyncio.create_task(execute_reserved())
        runner.add_done_callback(
            lambda task: None if task.cancelled() else task.exception()
        )
        selection_waiter = asyncio.ensure_future(asyncio.shield(selected))
        done, _ = await asyncio.wait(
            {runner, selection_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if runner in done:
            selection_waiter.cancel()
            with suppress(asyncio.CancelledError):
                await selection_waiter
            executed = await runner
            if executed is not None:
                raise TrustedWorkerWireError("trusted worker selection was not observed")
            return None

        dispatch = await selection_waiter
        dispatch.runner = runner
        return transport_gate

    async def _cancel(
        self,
        operation_id: str,
        dispatch: _PendingDispatch,
    ) -> None:
        runner: asyncio.Task[object] | None = None
        async with self._dispatch_lock:
            queue = self._pending.get(operation_id)
            if queue is None or dispatch not in queue:
                return
            runner = dispatch.runner
            if runner is None:
                if dispatch.submitted:
                    if not self._adapter.cancel_pending(operation_id):
                        raise TrustedWorkerWireError(
                            "trusted worker pending cancellation failed"
                        )
                    if queue[0] is not dispatch:
                        raise TrustedWorkerWireError(
                            "trusted worker pending order mismatch"
                        )
                    queue.popleft()
                    if queue:
                        self._submit_locked(queue[0])
                else:
                    queue.remove(dispatch)
                if not queue:
                    self._pending.pop(operation_id, None)
                dispatch.result.cancel()
            else:
                runner.cancel()
        if runner is not None:
            with suppress(asyncio.CancelledError):
                await asyncio.shield(runner)
        await self._pump()

    def _submit_locked(self, dispatch: _PendingDispatch) -> None:
        self._adapter.submit(dispatch.work_item)
        dispatch.submitted = True


__all__ = [
    "TrustedWorkerDispatcher",
    "TrustedWorkerWireError",
    "TrustedWorkerWireResult",
    "TrustedWorkerWireTransport",
]
