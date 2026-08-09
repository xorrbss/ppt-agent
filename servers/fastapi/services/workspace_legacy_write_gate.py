"""Admission guard for presentation writes outside the operation runner.

The workspace runner owns presentation serialization, revision fencing, retry,
and timeline publication.  Legacy HTTP mutations must therefore stop before
touching persistence.  Keep this guard deliberately small: generation bootstrap
and the runner call their own services and are not granted a blanket HTTP-route
exemption.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.workspace import WorkspaceOperation, get_workspace_owner_scope
from services.database import sql_engine
from services.workspace_runtime_profile import (
    UnsupportedWorkspaceRuntimeError,
    require_supported_workspace_runtime,
)


CHAT_FIRST_COMMAND_REQUIRED = "chat_first_command_required"


def require_legacy_presentation_write_path(
    presentation_id: uuid.UUID | None = None,
) -> None:
    """Always require a workspace command for a presentation mutation.

    ``presentation_id`` is accepted so call sites read naturally and so a
    future scoped exemption has somewhere to hang, but no deck is exempt: the
    runner is the only writer.  An unusable SQL runtime fails closed with 501
    rather than silently re-opening the direct write path.
    """

    try:
        require_supported_workspace_runtime(sql_engine.url)
    except UnsupportedWorkspaceRuntimeError as error:
        raise HTTPException(status_code=501, detail=error.code) from error
    raise HTTPException(
        status_code=409,
        detail=CHAT_FIRST_COMMAND_REQUIRED,
    )


async def require_outline_bootstrap_write_path(
    session: AsyncSession,
    presentation: PresentationModel,
) -> None:
    """Admit outline persistence only while a staged generation is prepared.

    Outline generation still precedes activation of the durable
    ``presentation.generate`` operation.  Permit that one bootstrap write for
    the owner-scoped staging deck, while leaving every published deck and every
    active/terminal operation behind the runner-only gate.
    """

    try:
        require_legacy_presentation_write_path(presentation.id)
        return
    except HTTPException as error:
        if (
            error.status_code != 409
            or error.detail != CHAT_FIRST_COMMAND_REQUIRED
        ):
            raise
        command_required_error = error

    if presentation.lifecycle_status != PRESENTATION_LIFECYCLE_STAGING:
        raise command_required_error

    operation_id = await session.scalar(
        select(WorkspaceOperation.id).where(
            WorkspaceOperation.presentation_id == presentation.id,
            WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
            WorkspaceOperation.kind == "presentation.generate",
            WorkspaceOperation.state == "awaiting_approval",
            WorkspaceOperation.expected_revision == presentation.revision,
        )
    )
    if operation_id is None:
        raise command_required_error
