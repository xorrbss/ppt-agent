"""The workspace-operation fence that guards authored publish transitions.

Split out of ``services/authored_checkpoint_service.py`` to keep that facade
under the file-size ratchet.  This module owns exactly one concern: deciding
whether a generation job is linked to a running workspace operation and, when
it is, taking that operation's row write lock before the job may move into or
out of the ``staging`` state.  The unlinked (legacy) case is delegated back to
the plain attempt-token transitions in
``services/authored_checkpoint_errors.py``.
"""

from __future__ import annotations

from datetime import timedelta
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission
from models.sql.workspace import WorkspaceOperation

from services.authored_checkpoint_errors import (
    _PUBLISH_FENCE_SECONDS,
    AuthoredStageError,
    begin_publish as _begin_publish,
    complete_publish as _complete_publish,
)
from utils.datetime_utils import get_current_utc_datetime


__all__ = [
    "begin_publish",
    "complete_publish",
]

async def _lock_public_admission_if_available(
    session: AsyncSession,
    operation_id: uuid.UUID,
) -> PublicOperationAdmission | None:
    """Keep legacy/rolling schemas readable while public-v2 fails closed later."""

    try:
        async with session.begin_nested():
            return await session.scalar(
                select(PublicOperationAdmission)
                .where(PublicOperationAdmission.operation_id == operation_id)
                .with_for_update()
            )
    except OperationalError:
        return None


async def _require_workspace_operation_fence(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    attempt_token: str,
    fencing_token: int | None,
) -> bool:
    """CAS-fence a linked authored write against its workspace operation.

    Legacy generation jobs are intentionally unlinked and retain the historical
    generation-job attempt fence.  Once a job is linked, its own attempt token
    is only an identifier: the running workspace operation is authoritative.
    The conditional no-op update acquires the operation row write lock and the
    database rechecks state, attempt and fence before a caller may write.
    """

    job = await session.get(PresentationGenerationJob, job_id)
    if job is None or job.workspace_operation_id is None:
        return False
    if isinstance(fencing_token, bool) or not isinstance(fencing_token, int):
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_workspace_operation_fencing_token_required",
            retryable=True,
        )
    admission = await _lock_public_admission_if_available(
        session,
        job.workspace_operation_id,
    )
    now = get_current_utc_datetime()
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
    return True

async def begin_publish(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    attempt_token: str,
    fencing_token: int | None = None,
) -> None:
    linked = await _require_workspace_operation_fence(
        session,
        job_id=job_id,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
    )
    if not linked:
        await _begin_publish(session, job_id=job_id, attempt_token=attempt_token)
        return
    now = get_current_utc_datetime()
    result = await session.execute(
        update(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.id == job_id,
            PresentationGenerationJob.state.in_(("running", "staging")),
        )
        .values(
            state="staging",
            current_stage="publish",
            lease_expires_at=now + timedelta(seconds=_PUBLISH_FENCE_SECONDS),
            heartbeat_at=now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )


async def complete_publish(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    attempt_token: str,
    fencing_token: int | None = None,
) -> None:
    linked = await _require_workspace_operation_fence(
        session,
        job_id=job_id,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
    )
    if not linked:
        await _complete_publish(session, job_id=job_id, attempt_token=attempt_token)
        return
    now = get_current_utc_datetime()
    result = await session.execute(
        update(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.id == job_id,
            PresentationGenerationJob.state == "staging",
        )
        .values(
            state="published",
            attempt_token=None,
            lease_expires_at=None,
            heartbeat_at=now,
            current_stage="completed",
            last_error=None,
            resume_allowed=False,
            published_at=now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )
