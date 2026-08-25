from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os

from services.source_locked.generation_store import DurableSourceLockedGenerationStore
from services.source_locked.job_runner import run_source_locked_job
from services.source_locked.job_store import (
    DurableSourceLockedJobStore,
    RecoverableSourceLockedJob,
)
from services.source_locked.lifecycle import ArtifactLifecycleStats


logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SECONDS = 300
_DEFAULT_MAX_WORKERS = 2
_MAX_RECOVERY_BATCH = 1000


def _positive_environment_integer(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class SourceLockedMaintenancePolicy:
    interval_seconds: int = _DEFAULT_INTERVAL_SECONDS
    max_workers: int = _DEFAULT_MAX_WORKERS

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("source-locked maintenance interval must be positive")
        if self.max_workers <= 0 or self.max_workers > _MAX_RECOVERY_BATCH:
            raise ValueError("source-locked maintenance worker count is invalid")

    @classmethod
    def from_environment(cls) -> "SourceLockedMaintenancePolicy":
        return cls(
            interval_seconds=_positive_environment_integer(
                "SOURCE_LOCKED_MAINTENANCE_INTERVAL_SECONDS",
                _DEFAULT_INTERVAL_SECONDS,
            ),
            max_workers=_positive_environment_integer(
                "SOURCE_LOCKED_MAINTENANCE_MAX_WORKERS",
                _DEFAULT_MAX_WORKERS,
            ),
        )


@dataclass(frozen=True, slots=True)
class SourceLockedMaintenanceResult:
    recoverable_jobs: tuple[RecoverableSourceLockedJob, ...]
    orphan_inputs_removed: int
    artifact_lifecycle: ArtifactLifecycleStats


async def run_source_locked_maintenance_once(
    *,
    job_store: DurableSourceLockedJobStore,
    generation_store: DurableSourceLockedGenerationStore,
    recovery_limit: int,
) -> SourceLockedMaintenanceResult:
    """Run one dedicated maintenance pass without reading ownerless records."""

    artifact_lifecycle, orphan_inputs_removed, recoverable_jobs = await asyncio.gather(
        asyncio.to_thread(generation_store.run_lifecycle_maintenance),
        asyncio.to_thread(job_store.cleanup_orphan_inputs),
        asyncio.to_thread(
            job_store.recover_startup_jobs,
            limit=min(max(recovery_limit, 1), _MAX_RECOVERY_BATCH),
        ),
    )
    return SourceLockedMaintenanceResult(
        recoverable_jobs=recoverable_jobs,
        orphan_inputs_removed=orphan_inputs_removed,
        artifact_lifecycle=artifact_lifecycle,
    )


_maintenance_task: asyncio.Task[None] | None = None
_maintenance_stop: asyncio.Event | None = None
_worker_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}


async def _run_job(
    job: RecoverableSourceLockedJob,
    *,
    job_store: DurableSourceLockedJobStore,
    generation_store: DurableSourceLockedGenerationStore,
) -> None:
    try:
        await asyncio.to_thread(
            run_source_locked_job,
            owner_id=job.owner_id,
            job_id=job.job_id,
            job_store=job_store,
            generation_store=generation_store,
        )
    except Exception:
        # The runner persists ordinary failures. This protects the dispatcher
        # from an unexpected store failure without treating it as job success.
        logger.exception("Source-locked maintenance worker crashed job_id=%s", job.job_id)


def _dispatch(
    jobs: tuple[RecoverableSourceLockedJob, ...],
    *,
    job_store: DurableSourceLockedJobStore,
    generation_store: DurableSourceLockedGenerationStore,
) -> int:
    dispatched = 0
    for job in jobs:
        key = (job.owner_id, job.job_id)
        if key in _worker_tasks:
            continue
        task = asyncio.create_task(
            _run_job(
                job,
                job_store=job_store,
                generation_store=generation_store,
            ),
            name=f"source-locked-job-{job.job_id[:20]}",
        )
        _worker_tasks[key] = task

        def _forget(done: asyncio.Task[None], *, job_key=key) -> None:
            if _worker_tasks.get(job_key) is done:
                _worker_tasks.pop(job_key, None)

        task.add_done_callback(_forget)
        dispatched += 1
    return dispatched


async def _maintain_and_dispatch(
    *,
    policy: SourceLockedMaintenancePolicy,
    job_store: DurableSourceLockedJobStore,
    generation_store: DurableSourceLockedGenerationStore,
) -> SourceLockedMaintenanceResult:
    available_workers = max(policy.max_workers - len(_worker_tasks), 0)
    result = await run_source_locked_maintenance_once(
        job_store=job_store,
        generation_store=generation_store,
        recovery_limit=max(available_workers, 1),
    )
    if available_workers:
        _dispatch(
            result.recoverable_jobs[:available_workers],
            job_store=job_store,
            generation_store=generation_store,
        )
    return result


async def _maintenance_loop(
    *,
    stop: asyncio.Event,
    policy: SourceLockedMaintenancePolicy,
    job_store: DurableSourceLockedJobStore,
    generation_store: DurableSourceLockedGenerationStore,
) -> None:
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=policy.interval_seconds)
        except TimeoutError:
            try:
                await _maintain_and_dispatch(
                    policy=policy,
                    job_store=job_store,
                    generation_store=generation_store,
                )
            except Exception:
                logger.exception("Source-locked maintenance pass failed")
            continue
        return


async def start_source_locked_maintenance() -> None:
    """Recover durable jobs once, then keep cleanup and recovery active."""

    global _maintenance_stop, _maintenance_task
    if _maintenance_task is not None and not _maintenance_task.done():
        return
    policy = SourceLockedMaintenancePolicy.from_environment()
    job_store = DurableSourceLockedJobStore.from_app_data()
    generation_store = DurableSourceLockedGenerationStore.from_app_data()
    await _maintain_and_dispatch(
        policy=policy,
        job_store=job_store,
        generation_store=generation_store,
    )
    stop = asyncio.Event()
    _maintenance_stop = stop
    _maintenance_task = asyncio.create_task(
        _maintenance_loop(
            stop=stop,
            policy=policy,
            job_store=job_store,
            generation_store=generation_store,
        ),
        name="source-locked-maintenance",
    )


async def stop_source_locked_maintenance() -> None:
    """Stop polling; active fenced jobs may finish their durable transition."""

    global _maintenance_stop, _maintenance_task
    task = _maintenance_task
    stop = _maintenance_stop
    _maintenance_task = None
    _maintenance_stop = None
    if stop is not None:
        stop.set()
    if task is not None:
        await task


__all__ = [
    "SourceLockedMaintenancePolicy",
    "SourceLockedMaintenanceResult",
    "run_source_locked_maintenance_once",
    "start_source_locked_maintenance",
    "stop_source_locked_maintenance",
]
