"""Pure public-v2 operation lifecycle projection and authority checks."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


PUBLIC_OPERATION_CONTRACT = "public-operation/v2"
PUBLIC_OPERATION_EXECUTION_PROFILE = "public-generation-export/v2"
PUBLIC_GENERATION_KIND = "presentation.generate"
PUBLIC_TERMINAL_STATES = frozenset(
    {
        "succeeded",
        "failed",
        "cancelled",
        "deadline_exceeded",
        "dead_letter",
    }
)


def aware_datetime(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def public_operation_status(value: str) -> str:
    if value in {"queued", "waiting_for_assets", "awaiting_approval", "approval_required"}:
        return "queued"
    if value in {"running", "cancelling", "blocked"}:
        return "running"
    if value == "succeeded":
        return "succeeded"
    if value == "cancelled":
        return "cancelled"
    return "failed"


def public_operation_phase(*, kind: str, state: str, progress: object) -> str | None:
    stage = ""
    if isinstance(progress, Mapping):
        stage = str(progress.get("stage") or progress.get("phase") or "").lower()
    if "publish" in stage:
        return "publishing"
    if "package" in stage or "export_completed" in stage:
        return "packaging"
    if "render" in stage or "export" in stage:
        return "rendering"
    if "valid" in stage or "quality" in stage:
        return "validating"
    if "author" in stage or "outline" in stage or "generation" in stage:
        return "authoring"
    if state == "queued":
        return "admission"
    if kind.startswith("export.") and state in {"running", "cancelling"}:
        return "rendering"
    if kind == PUBLIC_GENERATION_KIND and state in {"running", "cancelling"}:
        return "authoring"
    return None


def public_progress_percent(progress: object) -> int | None:
    if not isinstance(progress, Mapping):
        return None
    raw = progress.get("percent")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return max(0, min(100, int(raw)))


def is_public_operation_authority(operation: Any, admission: Any) -> bool:
    """Require the workspace projection and admission authority to agree."""

    if operation.state == "queued":
        states_match = admission.state == "queued"
    elif operation.state in {"running", "cancelling"}:
        states_match = admission.state == "active"
    elif operation.state in {"failed", "dead_letter"}:
        states_match = admission.state in {"failed", "budget_exhausted"}
    else:
        states_match = admission.state == operation.state
    return (
        operation.contract_version == PUBLIC_OPERATION_CONTRACT
        and operation.execution_profile_version == PUBLIC_OPERATION_EXECUTION_PROFILE
        and operation.deadline_at is not None
        and operation.fencing_token == admission.fence
        and aware_datetime(operation.deadline_at) == aware_datetime(admission.deadline_at)
        and states_match
    )


__all__ = [
    "PUBLIC_GENERATION_KIND",
    "PUBLIC_OPERATION_CONTRACT",
    "PUBLIC_OPERATION_EXECUTION_PROFILE",
    "PUBLIC_TERMINAL_STATES",
    "aware_datetime",
    "is_public_operation_authority",
    "public_operation_phase",
    "public_operation_status",
    "public_progress_percent",
]
