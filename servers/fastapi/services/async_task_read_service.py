from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import Select, case, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.auth.context import get_current_owner_id
from enums.async_task_status import AsyncTaskStatus
from models.async_task_read import AsyncTaskReadModel
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.presentation_generation_job_service import (
    NONTERMINAL_STATES,
    TERMINAL_STATES,
)


TASK_TYPE_PRESENTATION_GENERATION = "presentation.generate"

_STATUS_BY_JOB_STATE = {
    **{state: AsyncTaskStatus.PENDING for state in NONTERMINAL_STATES},
    "published": AsyncTaskStatus.COMPLETED,
    "failed": AsyncTaskStatus.ERROR,
}

_JOB_STATES_BY_STATUS = {
    AsyncTaskStatus.COMPLETED: frozenset({"published"}),
    AsyncTaskStatus.ERROR: frozenset({"failed"}),
}


def _owner_filter():
    owner_id = get_current_owner_id()
    if owner_id is None:
        return AsyncPresentationGenerationTaskModel.owner_id.is_(None)
    return AsyncPresentationGenerationTaskModel.owner_id == owner_id


def _base_query() -> Select:
    """Build the owner-scoped durable source query used by every read."""

    return (
        select(
            AsyncPresentationGenerationTaskModel,
            PresentationGenerationJob,
        )
        .join(
            PresentationGenerationJob,
            PresentationGenerationJob.task_id
            == AsyncPresentationGenerationTaskModel.id,
        )
        .where(_owner_filter())
    )


def _to_read_model(
    task: AsyncPresentationGenerationTaskModel,
    job: PresentationGenerationJob,
) -> AsyncTaskReadModel:
    # Unknown future states remain visibly nonterminal instead of being reported
    # as a false success/failure.
    status = _STATUS_BY_JOB_STATE.get(job.state, AsyncTaskStatus.PENDING)
    return AsyncTaskReadModel(
        id=task.id,
        type=TASK_TYPE_PRESENTATION_GENERATION,
        status=status,
        message=task.message,
        error=task.error,
        data=task.data,
        created_at=task.created_at,
        updated_at=_latest_datetime(task.updated_at, job.updated_at),
        terminal=job.state in TERMINAL_STATES,
        job_id=job.id,
        presentation_id=job.presentation_id,
        job_state=job.state,
        source_status=task.status,
    )


def _latest_datetime(left: datetime, right: datetime) -> datetime:
    """Compare SQLite-naive and PostgreSQL-aware timestamps portably."""

    def comparable(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    return left if comparable(left) >= comparable(right) else right


async def get_async_task(
    task_id: str,
    sql_session: AsyncSession,
) -> AsyncTaskReadModel | None:
    row = (
        await sql_session.execute(
            _base_query().where(
                AsyncPresentationGenerationTaskModel.id == task_id
            )
        )
    ).one_or_none()
    if row is None:
        return None
    task, job = row
    return _to_read_model(task, job)


async def list_async_task_reads(
    *,
    sql_session: AsyncSession,
    task_type: str | None = None,
    status: AsyncTaskStatus | str | None = None,
    created_at: datetime | None = None,
    created_at_from: datetime | None = None,
    created_at_to: datetime | None = None,
    order_by: Literal["created_at", "updated_at"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: int = 50,
    offset: int = 0,
) -> list[AsyncTaskReadModel]:
    if task_type is not None and task_type != TASK_TYPE_PRESENTATION_GENERATION:
        return []

    statement = _base_query()
    if status is not None:
        normalized_status = AsyncTaskStatus(status)
        if normalized_status is AsyncTaskStatus.PENDING:
            # The response maps unknown future durable states to pending, so
            # the filter must preserve the same contract instead of hiding
            # recoverable work introduced by a newer worker.
            statement = statement.where(
                PresentationGenerationJob.state.not_in(TERMINAL_STATES)
            )
        else:
            statement = statement.where(
                PresentationGenerationJob.state.in_(
                    _JOB_STATES_BY_STATUS[normalized_status]
                )
            )
    created_at_start = created_at_from or created_at
    if created_at_start is not None:
        statement = statement.where(
            AsyncPresentationGenerationTaskModel.created_at >= created_at_start
        )
    if created_at_to is not None:
        statement = statement.where(
            AsyncPresentationGenerationTaskModel.created_at <= created_at_to
        )

    if order_by == "updated_at":
        order_column = case(
            (
                PresentationGenerationJob.updated_at
                >= AsyncPresentationGenerationTaskModel.updated_at,
                PresentationGenerationJob.updated_at,
            ),
            else_=AsyncPresentationGenerationTaskModel.updated_at,
        )
    else:
        order_column = AsyncPresentationGenerationTaskModel.created_at
    statement = statement.order_by(
        order_column.asc() if order == "asc" else order_column.desc(),
        AsyncPresentationGenerationTaskModel.id.asc(),
    )
    statement = statement.offset(offset).limit(limit)

    rows = (await sql_session.execute(statement)).all()
    return [_to_read_model(task, job) for task, job in rows]
