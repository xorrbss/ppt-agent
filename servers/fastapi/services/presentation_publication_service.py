from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation import PresentationModel
from models.sql.workspace import WorkspaceOperation
from services.workspace_operation_runner import append_operation_event
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


class PresentationPublicationConflict(RuntimeError):
    code = "stale_presentation_revision"


async def publish_presentation_revision(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    expected_revision: int,
    operation_id: uuid.UUID | None = None,
    attempt_token: str | None = None,
    result: dict | None = None,
    operation_kind: str | None = None,
    advance_revision: bool = True,
) -> int:
    """Increment the publication token exactly once using a fenced CAS.

    Checkpoint writers must not call this function. The caller commits only
    after immutable external artifacts and their hashes are durable.

    ``advance_revision=False`` records the attempt's result and terminalizes the
    operation without moving the token.  A turn that only answered a question
    changed nothing, and bumping the revision for it invalidates the
    ``expected_revision`` every other client is holding.  The revision is still
    verified under the same fence, so a concurrently superseded read cannot
    report its answer as current.
    """

    if expected_revision < 0:
        raise ValueError("expected_revision must be nonnegative")
    result_revision = expected_revision + 1 if advance_revision else expected_revision
    now = get_current_utc_datetime()
    published_operation_version: int | None = None

    owner_id = get_current_owner_id()
    async with session.begin_nested():
        # Validate the attempt fence first. The savepoint guarantees that a
        # caller cannot accidentally commit half of this publication after
        # catching a conflict exception.
        if operation_id is not None:
            if not attempt_token:
                raise ValueError("attempt_token is required with operation_id")
            operation_statement = (
                update(WorkspaceOperation)
                .where(
                    WorkspaceOperation.id == operation_id,
                    WorkspaceOperation.presentation_id == presentation_id,
                    WorkspaceOperation.state == "running",
                    WorkspaceOperation.attempt_token == attempt_token,
                    WorkspaceOperation.expected_revision == expected_revision,
                )
                .values(
                    state="succeeded",
                    result_revision=result_revision,
                    result=result,
                    completed_at=now,
                    lease_expires_at=None,
                    heartbeat_at=now,
                    attempt_token=None,
                    version=WorkspaceOperation.version + 1,
                    updated_at=now,
                )
                .returning(
                    WorkspaceOperation.id,
                    WorkspaceOperation.version,
                )
            )
            if owner_id is not None:
                operation_statement = operation_statement.where(
                    WorkspaceOperation.owner_id == owner_id
                )
            completed_operation = (
                await session.execute(operation_statement)
            ).mappings().one_or_none()
            if completed_operation is None:
                raise PresentationPublicationConflict("stale_operation_fence")
            published_operation_version = int(completed_operation["version"])

        presentation_statement = (
            update(PresentationModel)
            .where(
                PresentationModel.id == presentation_id,
                PresentationModel.revision == expected_revision,
            )
            .values(revision=result_revision, updated_at=now)
            .returning(PresentationModel.revision)
            if advance_revision
            # Same fence, no write: the turn produced nothing to publish, so
            # verify the token rather than move it.
            else select(PresentationModel.revision).where(
                PresentationModel.id == presentation_id,
                PresentationModel.revision == expected_revision,
            )
        )
        if owner_id is not None:
            presentation_statement = presentation_statement.where(
                PresentationModel.owner_id == owner_id
            )
        if (
            await session.execute(presentation_statement)
        ).scalar_one_or_none() is None:
            raise PresentationPublicationConflict(
                PresentationPublicationConflict.code
            )

        if operation_id is not None:
            terminal_payload = dict(result or {})
            terminal_payload.update(
                state="succeeded",
                result_revision=result_revision,
            )
            projection_values = None
            if operation_kind == "quality.review":
                completion_progress = {
                    "stage": "quality_review_completed",
                    "percent": 100,
                }
                terminal_payload.update(
                    completion_progress,
                    result=dict(result or {}),
                )
                projection_values = {"progress": completion_progress}
            terminal_event = await append_operation_event(
                session,
                operation_id=operation_id,
                event_type="operation.result",
                payload=terminal_payload,
                operation_kind=operation_kind,
                allowed_states=("succeeded",),
                expected_version=published_operation_version,
                projection_values=projection_values,
                increment_version=True,
            )
            if terminal_event is None:
                raise PresentationPublicationConflict("stale_operation_event_fence")

            terminal_version = terminal_event.operation_version
            if advance_revision:
                published_event = await append_operation_event(
                    session,
                    operation_id=operation_id,
                    event_type="presentation.published",
                    payload={
                        "state": "succeeded",
                        "revision": result_revision,
                        "result_revision": result_revision,
                    },
                    allowed_states=("succeeded",),
                    expected_version=terminal_version,
                    increment_version=True,
                )
                if published_event is None:
                    raise PresentationPublicationConflict(
                        "stale_presentation_event_fence"
                    )
                terminal_version = published_event.operation_version

            completed_event = await append_operation_event(
                session,
                operation_id=operation_id,
                event_type="run.completed",
                payload={
                    "state": "succeeded",
                    "result_revision": result_revision,
                },
                allowed_states=("succeeded",),
                expected_version=terminal_version,
            )
            if completed_event is None:
                raise PresentationPublicationConflict(
                    "stale_run_completion_event_fence"
                )

    return int(result_revision)
