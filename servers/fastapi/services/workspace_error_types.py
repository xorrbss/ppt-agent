from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceErrorDefinition:
    code: str
    safe_message: str
    retryable: bool
    status_code: int


def definition(
    code: str,
    safe_message: str,
    *,
    retryable: bool = False,
    status_code: int = 400,
) -> WorkspaceErrorDefinition:
    return WorkspaceErrorDefinition(
        code=code,
        safe_message=safe_message,
        retryable=retryable,
        status_code=status_code,
    )
