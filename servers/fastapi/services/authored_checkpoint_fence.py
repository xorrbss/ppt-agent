"""The fencing primitives every authored checkpoint write shares.

Moved out of ``services/authored_checkpoint_state.py`` so the batching paths
can reuse them without that module growing past the file-size ratchet.  The
behaviour is unchanged: ``owned_job`` is still the conditional no-op UPDATE
that acquires the job (and, when linked, the workspace operation) row write
lock and lets the database recheck state, attempt and fence before any stage
data becomes visible.

``owned_job`` may now be asked more than once inside one transaction, because a
merged stage write fences first and then does several reads and writes under
the lock it took.  Repeating the full CAS in that case would cost the round
trips the merge exists to remove, so the result is memoised on the session for
exactly the identity that was fenced.  This is sound and not a shortcut: the
first CAS holds the row write lock for the rest of the transaction, so no other
writer can change the answer before the commit.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission
from models.sql.workspace import WorkspaceOperation
from services.authored_checkpoint_errors import AuthoredStageError
from services.database import async_session_maker
from utils.datetime_utils import get_current_utc_datetime


_FENCED_SESSION_KEY = "authored_checkpoint_fenced"


async def lock_public_admission_if_available(
    session: AsyncSession,
    operation_id: uuid.UUID,
) -> PublicOperationAdmission | None:
    try:
        async with session.begin_nested():
            return await session.scalar(
                select(PublicOperationAdmission)
                .where(PublicOperationAdmission.operation_id == operation_id)
                .with_for_update()
            )
    except OperationalError:
        return None


@asynccontextmanager
async def checkpoint_mutation_session(session: AsyncSession | None):
    if session is not None:
        yield session, False
        return
    async with async_session_maker() as owned_session:
        yield owned_session, True


def mark_session_fenced(
    session: AsyncSession,
    job_id: uuid.UUID,
    attempt_token: str,
    fencing_token: int | None,
) -> None:
    """Record that this transaction already holds the fenced job row lock."""

    session.info[_FENCED_SESSION_KEY] = (job_id, attempt_token, fencing_token)


def session_is_fenced(
    session: AsyncSession,
    job_id: uuid.UUID,
    attempt_token: str,
    fencing_token: int | None,
) -> bool:
    return session.info.get(_FENCED_SESSION_KEY) == (
        job_id,
        attempt_token,
        fencing_token,
    )


async def owned_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    attempt_token: str,
    *,
    fencing_token: int | None = None,
    reuse_session_fence: bool = False,
) -> PresentationGenerationJob:
    # Fence every checkpoint transaction, not just final publication.  A plain
    # SELECT leaves a race where cancellation/lease takeover can happen before
    # the checkpoint UPDATE commits.  This conditional no-op UPDATE acquires the
    # job-row write lock and is rechecked by the database before any stage data
    # is made visible.
    job = await session.get(PresentationGenerationJob, job_id)
    if job is None:
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )
    if reuse_session_fence and session_is_fenced(
        session, job_id, attempt_token, fencing_token
    ):
        return job
    now = get_current_utc_datetime()
    if job.workspace_operation_id is not None:
        if isinstance(fencing_token, bool) or not isinstance(fencing_token, int):
            await session.rollback()
            raise AuthoredStageError(
                slide_index=0,
                stage="persist",
                code="authored_workspace_operation_fencing_token_required",
                retryable=True,
            )
        admission = await lock_public_admission_if_available(
            session,
            job.workspace_operation_id,
        )
        operation_result = await session.execute(
            update(WorkspaceOperation)
            .where(
                WorkspaceOperation.id == job.workspace_operation_id,
                WorkspaceOperation.state == "running",
                WorkspaceOperation.attempt_token == attempt_token,
                WorkspaceOperation.fencing_token == fencing_token,
            )
            .values(heartbeat_at=now, updated_at=now)
        )
        if operation_result.rowcount != 1:
            await session.rollback()
            raise AuthoredStageError(
                slide_index=0,
                stage="persist",
                code="authored_workspace_operation_fence_lost",
                retryable=True,
            )
        operation_contract = await session.scalar(
            select(WorkspaceOperation.contract_version).where(
                WorkspaceOperation.id == job.workspace_operation_id
            )
        )
        if operation_contract == "public-operation/v2" and (
            admission is None
            or admission.operation_kind != "generation"
            or admission.state != "active"
            or admission.fence != fencing_token
        ):
            await session.rollback()
            raise AuthoredStageError(
                slide_index=0,
                stage="persist",
                code="authored_workspace_operation_fence_lost",
                retryable=True,
            )
        result = await session.execute(
            update(PresentationGenerationJob)
            .where(
                PresentationGenerationJob.id == job_id,
                PresentationGenerationJob.state.in_(("running", "staging")),
            )
            .values(heartbeat_at=now, updated_at=now)
        )
    else:
        result = await session.execute(
            update(PresentationGenerationJob)
            .where(
                PresentationGenerationJob.id == job_id,
                PresentationGenerationJob.attempt_token == attempt_token,
                PresentationGenerationJob.state.in_(("running", "staging")),
            )
            .values(heartbeat_at=now, updated_at=now)
        )
    if result.rowcount != 1:
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )
    job = await session.get(PresentationGenerationJob, job_id)
    if job is None:  # Defensive: the fenced row cannot normally disappear.
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )
    mark_session_fenced(session, job_id, attempt_token, fencing_token)
    return job


async def refence_job(
    session: AsyncSession,
    job: PresentationGenerationJob,
    attempt_token: str,
    *,
    fencing_token: int | None = None,
) -> None:
    """Re-assert this attempt's authority immediately before a pointer write.

    ``owned_job`` fences the start of the transaction; this repeats only the
    conditional UPDATE that proves the attempt still owns the operation, for
    the window between the reads a merged stage write does and the write it
    then commits.  One round trip, not the eleven a second ``owned_job`` costs.
    """

    now = get_current_utc_datetime()
    if job.workspace_operation_id is not None:
        result = await session.execute(
            update(WorkspaceOperation)
            .where(
                WorkspaceOperation.id == job.workspace_operation_id,
                WorkspaceOperation.state == "running",
                WorkspaceOperation.attempt_token == attempt_token,
                WorkspaceOperation.fencing_token == fencing_token,
            )
            .values(heartbeat_at=now, updated_at=now)
        )
        if result.rowcount != 1:
            await session.rollback()
            raise AuthoredStageError(
                slide_index=0,
                stage="persist",
                code="authored_workspace_operation_fence_lost",
                retryable=True,
            )
        return
    result = await session.execute(
        update(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.id == job.id,
            PresentationGenerationJob.attempt_token == attempt_token,
            PresentationGenerationJob.state.in_(("running", "staging")),
        )
        .values(heartbeat_at=now, updated_at=now)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )


async def public_checkpoint_binding(
    job_id: uuid.UUID,
    attempt_token: str,
    *,
    fencing_token: int | None,
    session: AsyncSession | None = None,
) -> tuple[uuid.UUID, str, uuid.UUID] | None:
    """Resolve a public generation's owner/tenant only from durable admission."""

    statement = (
        select(
            PublicOperationAdmission.owner_id,
            PublicOperationAdmission.tenant_id,
            PublicOperationAdmission.operation_id,
        )
        .join(
            PresentationGenerationJob,
            PresentationGenerationJob.workspace_operation_id
            == PublicOperationAdmission.operation_id,
        )
        .join(
            WorkspaceOperation,
            WorkspaceOperation.id == PresentationGenerationJob.workspace_operation_id,
        )
        .where(
            PresentationGenerationJob.id == job_id,
            PresentationGenerationJob.state.in_(("running", "staging")),
            WorkspaceOperation.state == "running",
            WorkspaceOperation.attempt_token == attempt_token,
            WorkspaceOperation.fencing_token == fencing_token,
            PublicOperationAdmission.operation_kind == "generation",
            PublicOperationAdmission.state == "active",
        )
    )
    if session is not None:
        binding = (await session.execute(statement)).one_or_none()
    else:
        async with async_session_maker() as read_session:
            binding = (await read_session.execute(statement)).one_or_none()
    if binding is None:
        return None
    return binding.owner_id, binding.tenant_id, binding.operation_id


async def load_checkpoint(
    session, job_id: uuid.UUID, slide_index: int
) -> AuthoredSlideCheckpoint:
    return (
        await session.execute(
            select(AuthoredSlideCheckpoint).where(
                AuthoredSlideCheckpoint.job_id == job_id,
                AuthoredSlideCheckpoint.slide_index == slide_index,
            )
        )
    ).scalar_one()


async def completed_count(session, job_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(AuthoredSlideCheckpoint).where(
                AuthoredSlideCheckpoint.job_id == job_id,
                AuthoredSlideCheckpoint.persist_status == "completed",
            )
        )
        or 0
    )


__all__ = [
    "checkpoint_mutation_session",
    "completed_count",
    "load_checkpoint",
    "lock_public_admission_if_available",
    "mark_session_fenced",
    "owned_job",
    "public_checkpoint_binding",
    "refence_job",
    "session_is_fenced",
]
