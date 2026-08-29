"""Admit legacy authored async-task APIs to the durable workspace runner."""

from __future__ import annotations

from collections.abc import Mapping
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.workspace import WorkspaceCommandRequest
from services.workspace_operation_runner import wake_workspace_operation_runner
from services.workspace_service import WorkspaceServiceError, create_workspace_command


async def admit_workspace_authored_task(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    operation_kind: str,
    expected_revision: int,
    command: str,
    context: Mapping[str, object],
    task_id_prefix: str,
    pending_message: str,
) -> AsyncPresentationGenerationTaskModel:
    """Persist immutable work before returning the legacy polling task.

    The workspace operation/request payload is the recovery authority.  The
    compatibility task id is derived from the immutable operation id, so a
    recovered worker can recreate a missing task row without request memory or
    a Starlette background callback.
    """

    try:
        operation, _duplicate = await create_workspace_command(
            session,
            presentation_id=presentation_id,
            request=WorkspaceCommandRequest(
                command=command,
                command_type=operation_kind,
                idempotency_key=f"legacy-authored:{uuid.uuid4()}",
                expected_revision=expected_revision,
                context=dict(context),
            ),
        )
    except WorkspaceServiceError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code},
        ) from error

    task_id = f"{task_id_prefix}-{operation.id}"
    task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
    if task is None:
        task = AsyncPresentationGenerationTaskModel(
            id=task_id,
            owner_id=operation.owner_id,
            status="pending",
            message=pending_message,
            data=None,
        )
        session.add(task)
        await session.commit()
    wake_workspace_operation_runner()
    return task


__all__ = ["admit_workspace_authored_task"]
