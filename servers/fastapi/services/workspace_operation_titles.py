"""Display titles for durable workspace operations.

The backend emits the English title from the kind registry plus a message key
(``workspace.operation.<kind>``) the frontend renders in the UI locale.  Kept
out of the workspace service so the projection module stays about projection.
"""

from __future__ import annotations

from constants.user_messages import workspace_operation_title_key
from models.workspace_operation_kinds import get_workspace_operation_kind

UNKNOWN_WORKSPACE_OPERATION_TITLE = "Workspace task"
UNKNOWN_WORKSPACE_OPERATION_KIND = "unknown"


def workspace_operation_title(kind: str) -> str:
    spec = get_workspace_operation_kind(kind)
    return spec.title if spec is not None else UNKNOWN_WORKSPACE_OPERATION_TITLE


def workspace_operation_title_fields(kind: str) -> dict[str, str]:
    """``title`` plus ``title_key`` for a timeline operation item."""

    spec = get_workspace_operation_kind(kind)
    return {
        "title": workspace_operation_title(kind),
        "title_key": workspace_operation_title_key(
            spec.kind if spec is not None else UNKNOWN_WORKSPACE_OPERATION_KIND
        ),
    }
