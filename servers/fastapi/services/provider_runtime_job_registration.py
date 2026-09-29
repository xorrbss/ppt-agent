"""Process-local provider runtime job registration seeded from durable receipts.

``ProviderRuntimeControl`` keeps retry-amplification counters per durable job,
but they live only as long as the worker process.  A resumed attempt (worker
restart, lease takeover by another process) starts with counters at zero, and
its first provider call is often a retry by design: the slide checkpoint
records the provider attempt ordinal of the interrupted call, so the resumed
call carries ordinal 2 and ``is_retry=True``.  The control then rejected it
with ``retry_requires_an_initial_request`` and cancelled the whole job.

Registration therefore seeds the process-local counters from the provider
usage receipts persisted by earlier attempts before the first call is admitted.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol
import uuid

from sqlalchemy.exc import SQLAlchemyError

from services.provider_runtime_control import (
    JobKillConditions,
    ProviderRuntimeControl,
)
from utils.env_settings.provider_credentials import provider_optional_limit

LOGGER = logging.getLogger(__name__)


class RuntimeJobRegistry(Protocol):
    """The per-event-loop registry owned by provider usage instrumentation."""

    control: ProviderRuntimeControl
    registration_lock: asyncio.Lock
    registered_jobs: set[str]
    latest_job_keys: dict[str, str]


class RuntimeJobScope(Protocol):
    """The subset of a provider usage scope that registration depends on."""

    job_id: uuid.UUID
    attempt_number: int


def optional_env_int(name: str) -> int | None:
    return provider_optional_limit(name)


async def persisted_request_counts(job_id: uuid.UUID) -> tuple[int, int]:
    """Read ``(initial, retry)`` provider request counts from durable receipts.

    Seeding is an accounting optimisation, never an admission authority: a
    resumed attempt must still start when the receipt store cannot be read
    (a database without the receipt table, a transient connection failure).
    An unreadable store therefore yields ``(0, 0)`` and a warning rather than
    failing the attempt that was being recovered.
    """

    # Imported lazily so instrumentation never depends on the database module
    # at import time; the session factory is resolved at call time.
    from services.database import async_session_maker
    from services.provider_usage_receipt_service import (
        persisted_provider_request_counts,
    )

    try:
        async with async_session_maker() as session:
            return await persisted_provider_request_counts(session, job_id)
    except SQLAlchemyError:
        LOGGER.warning(
            "Provider request counts unavailable for job %s; "
            "seeding the runtime job from zero",
            job_id,
            exc_info=True,
        )
        return 0, 0


async def ensure_runtime_job(
    runtime: RuntimeJobRegistry, scope: RuntimeJobScope
) -> str:
    """Register the durable job once per process and return its runtime key."""

    durable_job_id = str(scope.job_id)
    # A durable generation job has one provider budget across every operation
    # attempt.  Re-registering an attempt-suffixed runtime job resets the hard
    # cap after recovery and permits retry amplification.
    job_id = durable_job_id
    if job_id in runtime.registered_jobs:
        runtime.latest_job_keys[durable_job_id] = job_id
        return job_id
    async with runtime.registration_lock:
        if job_id in runtime.registered_jobs:
            return job_id
        initial_requests = 0
        retry_requests = 0
        if scope.attempt_number > 1:
            # This process has never seen the job, but earlier attempts have
            # already spent requests. Continue their accounting instead of
            # starting from zero.
            initial_requests, retry_requests = await persisted_request_counts(
                scope.job_id
            )
        deadline = optional_env_int("PROVIDER_JOB_DEADLINE_SECONDS")
        await runtime.control.register_job(
            job_id,
            estimated_budget_microunits=optional_env_int(
                "PROVIDER_JOB_ESTIMATED_BUDGET_MICROUNITS"
            ),
            actual_budget_microunits=optional_env_int(
                "PROVIDER_JOB_ACTUAL_BUDGET_MICROUNITS"
            ),
            kill_conditions=JobKillConditions(
                deadline_seconds=float(deadline) if deadline is not None else None,
                max_total_requests=optional_env_int(
                    "PROVIDER_JOB_MAX_TOTAL_REQUESTS"
                ),
                max_retry_requests=optional_env_int(
                    "PROVIDER_JOB_MAX_RETRY_REQUESTS"
                ),
                max_consecutive_failures=optional_env_int(
                    "PROVIDER_JOB_MAX_CONSECUTIVE_FAILURES"
                ),
            ),
            initial_requests=initial_requests,
            retry_requests=retry_requests,
        )
        runtime.registered_jobs.add(job_id)
        runtime.latest_job_keys[durable_job_id] = job_id
    return job_id
