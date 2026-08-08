"""Rollout-aware admission guard for presentation writes outside the runner.

The Chat-first workspace runner owns presentation serialization, revision
fencing, retry, and timeline publication while the rollout flag is enabled.
Legacy HTTP mutations must therefore stop before touching persistence.  Keep
this guard deliberately small: generation bootstrap and the runner call their
own services and are not granted a blanket HTTP-route exemption.
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
from services.workspace_rollout_policy import (
    resolve_workspace_write_rollout,
    workspace_rollout_needs_presentation_scope,
)
from services.workspace_runtime_profile import resolve_chat_first_workspace_rollout
from services.workspace_service import workspace_commands_enabled


CHAT_FIRST_COMMAND_REQUIRED = "chat_first_command_required"


def legacy_write_requires_presentation_scope() -> bool:
    """Preflight a mutation whose deck id requires a read-only lookup.

    Returns ``True`` only for a partial cohort. Fully enabled admission fails
    before any database access; flag-off and kill-switch rollback can continue
    directly; invalid runtimes remain fail-closed.
    """

    rollout = resolve_chat_first_workspace_rollout(
        workspace_commands_enabled(),
        sql_engine.url,
    )
    if not rollout.requested:
        return False
    decision = resolve_workspace_write_rollout(rollout)
    if not rollout.enabled:
        raise HTTPException(
            status_code=(
                501
                if rollout.reason == "chat_first_runtime_unsupported"
                else 503
            ),
            detail=rollout.reason,
        )
    if decision.kill_switch_active:
        return False
    if workspace_rollout_needs_presentation_scope():
        return True
    raise HTTPException(status_code=409, detail=CHAT_FIRST_COMMAND_REQUIRED)


def require_legacy_presentation_write_path(
    presentation_id: uuid.UUID | None = None,
) -> None:
    """Allow rollback/excluded cohorts, otherwise require a workspace command.

    A presentation id must be supplied whenever the legacy mutation is scoped
    to an existing deck.  This keeps the stable owner+deck cohort decision
    identical on the workspace route and its rollback-compatible legacy route.
    Calls without an id intentionally remain fail-closed for partial cohorts;
    they are reserved for genuinely unscoped compatibility operations.
    """

    rollout = resolve_chat_first_workspace_rollout(
        workspace_commands_enabled(),
        sql_engine.url,
    )
    if not rollout.requested:
        return
    write_rollout = resolve_workspace_write_rollout(
        rollout,
        presentation_id,
    )
    if not rollout.enabled:
        raise HTTPException(
            status_code=(
                501
                if rollout.reason == "chat_first_runtime_unsupported"
                else 503
            ),
            detail=rollout.reason,
        )
    if not write_rollout.enabled:
        if write_rollout.reason in {
            "chat_first_kill_switch_enabled",
            "chat_first_cohort_excluded",
        }:
            return
        raise HTTPException(
            status_code=503,
            detail=write_rollout.reason or "chat_first_runtime_unavailable",
        )
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
    ``presentation.generate`` operation.  During the migration, permit that
    one bootstrap write for the owner-scoped staging deck, while leaving every
    published deck and every active/terminal operation behind the runner-only
    gate.
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
