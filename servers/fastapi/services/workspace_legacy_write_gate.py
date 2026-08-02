"""Fail-closed admission guard for presentation writes outside the runner.

The Chat-first workspace runner owns presentation serialization, revision
fencing, retry, and timeline publication while the rollout flag is enabled.
Legacy HTTP mutations must therefore stop before touching persistence.  Keep
this guard deliberately small: generation bootstrap and the runner call their
own services and are not granted a blanket HTTP-route exemption.
"""

from fastapi import HTTPException

from services.database import sql_engine
from services.workspace_runtime_profile import resolve_chat_first_workspace_rollout
from services.workspace_service import workspace_commands_enabled


CHAT_FIRST_COMMAND_REQUIRED = "chat_first_command_required"


def require_legacy_presentation_write_path() -> None:
    """Allow rollback mode, otherwise require a durable workspace command."""

    rollout = resolve_chat_first_workspace_rollout(
        workspace_commands_enabled(),
        sql_engine.url,
    )
    if not rollout.requested:
        return
    if not rollout.enabled:
        raise HTTPException(
            status_code=(
                501
                if rollout.reason == "chat_first_runtime_unsupported"
                else 503
            ),
            detail=rollout.reason,
        )
    raise HTTPException(
        status_code=409,
        detail=CHAT_FIRST_COMMAND_REQUIRED,
    )
