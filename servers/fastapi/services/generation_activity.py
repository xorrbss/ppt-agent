"""The owner's in-flight deck generations, read cheaply for dashboard polling.

A user who cannot see that a long deck is still generating starts another one,
which then silently queues behind the first. This module answers "what is
generating right now, and how far along is it" from metadata columns only:

* operations come from ``workspace_operations`` (owner-scoped by the central
  ORM hook in ``services.database_owner_scope``; no owner filter here);
* page counts and the last progress instant are one ``GROUP BY`` over
  ``authored_slide_checkpoints`` status/timestamp columns -- never the
  ``svg_checkpoint``/``author_html`` payloads;
* queue metadata reuses the timeline's capacity-wait projection.

The query count is fixed regardless of how many items are returned.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
import uuid

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.generation_activity import (
    GenerationActivityItem,
    GenerationActivityPages,
    GenerationActivityResponse,
)
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation import PRESENTATION_LIFECYCLE_DELETED, PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import (
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
)
from services.workspace_service import capacity_wait_projection
from utils.datetime_utils import as_utc, get_current_utc_datetime

GENERATION_ACTIVITY_KIND = "presentation.generate"
# ``cancelling`` still holds the owner's generation slot until the worker
# stops, so a deck queued behind it must be able to see why it waits.
# Approval/asset/blocked states are deliberately absent: abandoned
# ``awaiting_approval`` generate rows linger for months without progressing,
# so listing them would pin permanent rows on the dashboard.
GENERATION_ACTIVITY_STATES = ("running", "cancelling", "queued")
GENERATION_ACTIVITY_LIMIT = 20


@dataclass(frozen=True, slots=True)
class CheckpointProgress:
    """Completed-page counts and the latest completion instant of one job."""

    authored: int = 0
    rendered: int = 0
    persisted: int = 0
    last_progress_at: datetime | None = None


def _completed_count(column):
    return func.coalesce(func.sum(case((column == "completed", 1), else_=0)), 0)


def _latest(*values: datetime | None) -> datetime | None:
    present = [as_utc(value) for value in values if value is not None]
    return max(present) if present else None


async def checkpoint_progress_by_job(
    session: AsyncSession,
    job_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, CheckpointProgress]:
    """One aggregate over checkpoint metadata for every job in ``job_ids``.

    ``last_progress_at`` is the latest author/render/persist completion: the
    single definition both this list and the draft preview report.
    """

    unique_ids = list(dict.fromkeys(job_ids))
    if not unique_ids:
        return {}
    rows = (
        await session.execute(
            select(
                AuthoredSlideCheckpoint.job_id,
                _completed_count(AuthoredSlideCheckpoint.author_status).label("authored"),
                _completed_count(AuthoredSlideCheckpoint.render_status).label("rendered"),
                _completed_count(AuthoredSlideCheckpoint.persist_status).label("persisted"),
                func.max(AuthoredSlideCheckpoint.author_completed_at).label("author_at"),
                func.max(AuthoredSlideCheckpoint.render_completed_at).label("render_at"),
                func.max(AuthoredSlideCheckpoint.persist_completed_at).label("persist_at"),
            )
            .where(AuthoredSlideCheckpoint.job_id.in_(unique_ids))
            .group_by(AuthoredSlideCheckpoint.job_id)
        )
    ).all()
    return {
        row.job_id: CheckpointProgress(
            authored=int(row.authored or 0),
            rendered=int(row.rendered or 0),
            persisted=int(row.persisted or 0),
            last_progress_at=_latest(row.author_at, row.render_at, row.persist_at),
        )
        for row in rows
    }


def _iso(value: datetime | None) -> str | None:
    return as_utc(value).isoformat() if value is not None else None


async def generation_activity(session: AsyncSession) -> GenerationActivityResponse:
    """The current owner's running, cancelling and queued generations, in that order."""

    server_time = get_current_utc_datetime()
    operations = (
        await session.execute(
            select(
                WorkspaceOperation.id,
                WorkspaceOperation.presentation_id,
                WorkspaceOperation.state,
                WorkspaceOperation.progress,
                WorkspaceOperation.version,
                WorkspaceOperation.created_at,
                WorkspaceOperation.started_at,
                WorkspaceOperation.retry_of_operation_id,
                PresentationModel.title,
                PresentationModel.revision,
            )
            .join(
                PresentationModel,
                PresentationModel.id == WorkspaceOperation.presentation_id,
            )
            .where(
                WorkspaceOperation.kind == GENERATION_ACTIVITY_KIND,
                WorkspaceOperation.state.in_(GENERATION_ACTIVITY_STATES),
                PresentationModel.lifecycle_status != PRESENTATION_LIFECYCLE_DELETED,
            )
            .order_by(
                case(
                    (WorkspaceOperation.state == "running", 0),
                    (WorkspaceOperation.state == "cancelling", 1),
                    else_=2,
                ),
                WorkspaceOperation.created_at.asc(),
                WorkspaceOperation.id.asc(),
            )
            .limit(GENERATION_ACTIVITY_LIMIT)
        )
    ).all()
    if not operations:
        return GenerationActivityResponse(server_time=server_time.isoformat(), items=[])

    operation_ids = [row.id for row in operations]
    # Generation jobs are not owner-scoped rows; they are reached only through
    # the owner-scoped operations selected above.
    jobs = {
        row.workspace_operation_id: row
        for row in (
            await session.execute(
                select(
                    PresentationGenerationJob.id,
                    PresentationGenerationJob.workspace_operation_id,
                    PresentationGenerationJob.total_slides,
                    PresentationGenerationJob.current_stage,
                ).where(PresentationGenerationJob.workspace_operation_id.in_(operation_ids))
            )
        ).all()
    }
    progress_by_job = await checkpoint_progress_by_job(
        session, [job.id for job in jobs.values()]
    )
    # The first attachment of the turn that started the operation: the same
    # snapshot the timeline shows as the attachment name.
    source_names: dict[uuid.UUID, str] = {}
    for row in (
        await session.execute(
            select(WorkspaceOperation.id, WorkspaceMessageAttachment.display_name)
            .join(
                WorkspaceMessage,
                and_(
                    WorkspaceMessage.conversation_id == WorkspaceOperation.conversation_id,
                    WorkspaceMessage.position == WorkspaceOperation.anchor_position - 1,
                ),
            )
            .join(
                WorkspaceMessageAttachment,
                WorkspaceMessageAttachment.message_id == WorkspaceMessage.id,
            )
            .where(WorkspaceOperation.id.in_(operation_ids))
            .order_by(WorkspaceOperation.id, WorkspaceMessageAttachment.ordinal.asc())
        )
    ).all():
        if row.display_name:
            source_names.setdefault(row.id, row.display_name)

    items: list[GenerationActivityItem] = []
    for row in operations:
        job = jobs.get(row.id)
        progress = progress_by_job.get(job.id) if job is not None else None
        progress = progress or CheckpointProgress()
        total = int(job.total_slides or 0) if job is not None else 0
        capacity_waiting, queue_position = capacity_wait_projection(row.state, row.progress)
        items.append(
            GenerationActivityItem(
                operation_id=str(row.id),
                presentation_id=str(row.presentation_id),
                state=row.state,
                presentation_title=row.title or None,
                source_file_name=source_names.get(row.id),
                pages=GenerationActivityPages(
                    authored=progress.authored,
                    rendered=progress.rendered,
                    persisted=progress.persisted,
                    total=total if total > 0 else None,
                ),
                stage=(job.current_stage or None) if job is not None else None,
                queue_position=queue_position,
                capacity_waiting=capacity_waiting,
                created_at=_iso(row.created_at),
                started_at=_iso(row.started_at),
                last_progress_at=_iso(progress.last_progress_at),
                operation_version=int(row.version),
                presentation_revision=int(row.revision),
                retry_of_operation_id=(
                    str(row.retry_of_operation_id)
                    if row.retry_of_operation_id is not None
                    else None
                ),
            )
        )
    return GenerationActivityResponse(server_time=server_time.isoformat(), items=items)
