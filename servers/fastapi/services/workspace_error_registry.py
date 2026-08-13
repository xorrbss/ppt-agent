"""Central, safe public error contract for the chat-first workspace API.

Internal exceptions may carry provider text, paths, prompts, or implementation
details.  Only the stable definitions in this module (or the deliberately
generic fallback) are allowed to cross the workspace HTTP boundary.
"""

from __future__ import annotations

import re

from fastapi import HTTPException
from services.workspace_error_definitions import WORKSPACE_ERROR_REGISTRY
from services.workspace_error_types import WorkspaceErrorDefinition


_STABLE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


def resolve_workspace_error(
    code: object,
    *,
    status_code: int | None = None,
    retryable: bool | None = None,
) -> WorkspaceErrorDefinition:
    """Return an allow-listed public definition.

    Unknown internal codes never pass through the HTTP boundary: even a
    syntactically stable provider or handler code may reveal implementation
    detail or create an undocumented client dependency.
    """

    normalized = str(code or "").strip().lower()
    if not _STABLE_CODE.fullmatch(normalized):
        normalized = "workspace_request_failed"
    registered = WORKSPACE_ERROR_REGISTRY.get(normalized)
    if registered is None:
        registered = WORKSPACE_ERROR_REGISTRY["workspace_request_failed"]
    return WorkspaceErrorDefinition(
        code=registered.code,
        safe_message=registered.safe_message,
        retryable=registered.retryable if retryable is None else bool(retryable),
        status_code=registered.status_code if status_code is None else status_code,
    )


class WorkspaceAPIException(HTTPException):
    """HTTP exception whose public fields have already been safety-normalized."""

    def __init__(
        self,
        code: object,
        *,
        status_code: int | None = None,
        retryable: bool | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        definition = resolve_workspace_error(
            code,
            status_code=status_code,
            retryable=retryable,
        )
        self.code = definition.code
        self.safe_message = definition.safe_message
        self.retryable = definition.retryable
        super().__init__(
            status_code=definition.status_code,
            detail=definition.code,
            headers=headers,
        )
