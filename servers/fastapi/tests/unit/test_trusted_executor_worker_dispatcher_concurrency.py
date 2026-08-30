from __future__ import annotations

import asyncio
from collections import deque
import hashlib
import sys
import time
import uuid
from pathlib import Path

import pytest

from services.isolated_worker_contracts import ArtifactRef, IsolatedWorkerJob
from services.trusted_executor.product_adapter import TrustedExecutorProductAdapter
from services.trusted_executor.weighted_scheduler import (
    SchedulerCaps,
    SchedulerPolicy,
    WeightedScheduler,
)
from services.trusted_executor.worker_dispatcher import (
    TrustedWorkerDispatcher,
    TrustedWorkerWireResult,
)


def _job(
    *,
    owner_id: uuid.UUID,
    operation_id: uuid.UUID,
    fencing_token: int,
) -> IsolatedWorkerJob:
    payload = str(operation_id).encode()
    digest = hashlib.sha256(payload).hexdigest()
    artifact = ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="tenant-dispatcher-test",
        kind="slide-html",
        sha256=digest,
        size_bytes=len(payload),
        media_type="text/html",
        storage_key=f"objects/{digest}",
        created_by_operation_id=operation_id,
        retention_class="ephemeral",
    )
    return IsolatedWorkerJob(
        role="render",
        operation_id=operation_id,
        attempt_token=f"attempt-{operation_id}",
        fencing_token=fencing_token,
        input_digest=digest,
        input_artifact=artifact,
        capability="dispatcher-concurrency-test",
        deadline_epoch_seconds=int(time.time()) + 30,
    )


class _BlockingTransport:
    def __init__(self) -> None:
        self.started: asyncio.Queue[uuid.UUID] = asyncio.Queue()
        self.releases: dict[uuid.UUID, deque[asyncio.Event]] = {}
        self.active = 0
        self.maximum_active = 0
        self.bindings: list[tuple[uuid.UUID, int, str]] = []

    async def execute(self, *, work_item, job, payload, environment):
        operation_id = job.operation_id
        release = asyncio.Event()
        self.releases.setdefault(operation_id, deque()).append(release)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.bindings.append(
            (
                operation_id,
                job.fencing_token,
                work_item.launch_spec.filesystem.sandbox_root.name,
            )
        )
        await self.started.put(operation_id)
        try:
            await release.wait()
            return TrustedWorkerWireResult(stdout=payload)
        finally:
            self.active -= 1

    def release(self, operation_id: uuid.UUID) -> None:
        self.releases[operation_id].popleft().set()


def _dispatcher(tmp_path: Path, transport: _BlockingTransport, *, global_cap: int = 2):
    scheduler = WeightedScheduler(
        SchedulerPolicy(
            caps=SchedulerCaps(
                global_cap=global_cap,
                node_cap=global_cap,
                tenant_cap=global_cap,
                owner_cap=global_cap,
                presentation_cap=1,
            )
        )
    )
    return TrustedWorkerDispatcher(
        adapter=TrustedExecutorProductAdapter(scheduler),
        transport=transport,
        staging_root=(tmp_path / "staging").resolve(),
        node_id="node-dispatcher-test",
    )


def test_dispatcher_runs_transport_outside_lock_and_preserves_presentation_cap(
    tmp_path: Path,
) -> None:
    asyncio.run(_concurrent_dispatch(tmp_path))


async def _concurrent_dispatch(tmp_path: Path) -> None:
    transport = _BlockingTransport()
    dispatcher = _dispatcher(tmp_path, transport)
    owner_id = uuid.uuid4()
    presentation_a = uuid.uuid4()
    presentation_b = uuid.uuid4()
    jobs = [
        _job(owner_id=owner_id, operation_id=uuid.uuid4(), fencing_token=11),
        _job(owner_id=owner_id, operation_id=uuid.uuid4(), fencing_token=12),
        _job(owner_id=owner_id, operation_id=uuid.uuid4(), fencing_token=13),
    ]

    async def execute(job: IsolatedWorkerJob, presentation_id: uuid.UUID):
        return await dispatcher.execute(
            job=job,
            presentation_id=presentation_id,
            payload=str(job.operation_id).encode(),
            command=(str(Path(sys.executable).resolve()),),
            environment={"PUBLIC_WORKER_ROLE": job.role},
        )

    first = asyncio.create_task(execute(jobs[0], presentation_a))
    assert await transport.started.get() == jobs[0].operation_id
    second = asyncio.create_task(execute(jobs[1], presentation_b))
    assert await transport.started.get() == jobs[1].operation_id
    third = asyncio.create_task(execute(jobs[2], presentation_a))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(transport.started.get(), timeout=0.05)
    assert transport.maximum_active == 2

    transport.release(jobs[0].operation_id)
    assert await asyncio.wait_for(transport.started.get(), timeout=1) == jobs[2].operation_id
    transport.release(jobs[1].operation_id)
    transport.release(jobs[2].operation_id)

    results = await asyncio.gather(first, second, third)
    assert [result.stdout for result in results] == [
        str(job.operation_id).encode() for job in jobs
    ]
    assert [(operation_id, fence) for operation_id, fence, _ in transport.bindings] == [
        (jobs[0].operation_id, 11),
        (jobs[1].operation_id, 12),
        (jobs[2].operation_id, 13),
    ]
    assert [root.startswith(f"fence-{job.fencing_token}-") for job, (_, _, root) in zip(jobs, transport.bindings)] == [
        True,
        True,
        True,
    ]


def test_dispatcher_queues_concurrent_work_sharing_one_operation_generation(
    tmp_path: Path,
) -> None:
    asyncio.run(_same_operation_dispatch(tmp_path))


async def _same_operation_dispatch(tmp_path: Path) -> None:
    transport = _BlockingTransport()
    dispatcher = _dispatcher(tmp_path, transport)
    owner_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    first_job = _job(
        owner_id=owner_id,
        operation_id=operation_id,
        fencing_token=31,
    )
    second_job = _job(
        owner_id=owner_id,
        operation_id=operation_id,
        fencing_token=32,
    )

    async def execute(job: IsolatedWorkerJob):
        return await dispatcher.execute(
            job=job,
            presentation_id=presentation_id,
            payload=f"fence-{job.fencing_token}".encode(),
            command=(str(Path(sys.executable).resolve()),),
            environment={},
        )

    first = asyncio.create_task(execute(first_job))
    assert await transport.started.get() == operation_id
    second = asyncio.create_task(execute(second_job))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(transport.started.get(), timeout=0.05)

    transport.release(operation_id)
    assert await asyncio.wait_for(transport.started.get(), timeout=1) == operation_id
    transport.release(operation_id)
    results = await asyncio.gather(first, second)

    assert [result.stdout for result in results] == [b"fence-31", b"fence-32"]
    assert [(operation, fence) for operation, fence, _ in transport.bindings] == [
        (operation_id, 31),
        (operation_id, 32),
    ]
    assert transport.bindings[0][2].startswith("fence-31-")
    assert transport.bindings[1][2].startswith("fence-32-")


def test_cancelling_a_cap_blocked_dispatch_removes_it_before_transport(
    tmp_path: Path,
) -> None:
    asyncio.run(_cancel_pending_dispatch(tmp_path))


async def _cancel_pending_dispatch(tmp_path: Path) -> None:
    transport = _BlockingTransport()
    dispatcher = _dispatcher(tmp_path, transport, global_cap=1)
    owner_id = uuid.uuid4()
    first_job = _job(owner_id=owner_id, operation_id=uuid.uuid4(), fencing_token=21)
    cancelled_job = _job(owner_id=owner_id, operation_id=uuid.uuid4(), fencing_token=22)

    async def execute(job: IsolatedWorkerJob):
        return await dispatcher.execute(
            job=job,
            presentation_id=uuid.uuid4(),
            payload=str(job.operation_id).encode(),
            command=(str(Path(sys.executable).resolve()),),
            environment={},
        )

    first = asyncio.create_task(execute(first_job))
    assert await transport.started.get() == first_job.operation_id
    cancelled = asyncio.create_task(execute(cancelled_job))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    transport.release(first_job.operation_id)
    await first
    await asyncio.sleep(0)
    assert [operation_id for operation_id, _, _ in transport.bindings] == [
        first_job.operation_id
    ]
