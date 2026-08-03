"""Deterministic, reversible admission policy for workspace writes."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
import uuid

from services.workspace_runtime_profile import ChatFirstWorkspaceRollout
from utils.owner_context import get_current_owner_id


_TRUE_VALUES = frozenset({"1", "true", "on", "yes"})
_KILL_SWITCH_ENV = "CHAT_FIRST_WORKSPACE_KILL_SWITCH"
_ROLLOUT_PERCENT_ENV = "CHAT_FIRST_WORKSPACE_ROLLOUT_PERCENT"
_ROLLOUT_SALT_ENV = "CHAT_FIRST_WORKSPACE_ROLLOUT_SALT"
_SHADOW_ENV = "CHAT_FIRST_WORKSPACE_SHADOW_ENABLED"


def _boolean(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in _TRUE_VALUES


def _rollout_basis_points() -> int:
    try:
        percentage = float(os.getenv(_ROLLOUT_PERCENT_ENV, "100"))
    except (TypeError, ValueError):
        percentage = 0.0
    return max(0, min(10_000, round(percentage * 100)))


def workspace_rollout_needs_presentation_scope() -> bool:
    """Whether a deck id is needed to distinguish included/excluded writes."""

    return _rollout_basis_points() < 10_000


def workspace_cohort_bucket(
    presentation_id: uuid.UUID,
    *,
    owner_id: object | None = None,
    salt: str | None = None,
) -> int:
    """Return a stable 0..9999 cohort without exposing owner identifiers."""

    identity = get_current_owner_id() if owner_id is None else owner_id
    seed = ":".join(
        (
            str(salt if salt is not None else os.getenv(_ROLLOUT_SALT_ENV, "v1")),
            str(identity or "anonymous"),
            str(presentation_id),
        )
    )
    return int.from_bytes(sha256(seed.encode("utf-8")).digest()[:8], "big") % 10_000


@dataclass(frozen=True)
class WorkspaceWriteRolloutDecision:
    enabled: bool
    cohort_eligible: bool
    cohort_bucket: int | None
    kill_switch_active: bool
    shadow_mode: bool
    reason: str | None


def resolve_workspace_write_rollout(
    runtime: ChatFirstWorkspaceRollout,
    presentation_id: uuid.UUID | None = None,
) -> WorkspaceWriteRolloutDecision:
    """Layer cohort/kill-switch controls over the authoritative runtime gate.

    A partial rollout must be presentation scoped. Presentation-scoped
    commands use a stable cohort, so retries and restarts never change
    eligibility; an unscoped request fails closed instead of accidentally
    representing every deck as admitted.
    """

    active = resolve_active_workspace_write_rollout(presentation_id)

    if not runtime.enabled:
        enabled = False
        reason = runtime.reason
    else:
        enabled = active.enabled
        reason = active.reason

    return WorkspaceWriteRolloutDecision(
        enabled=enabled,
        cohort_eligible=active.cohort_eligible,
        cohort_bucket=active.cohort_bucket,
        kill_switch_active=active.kill_switch_active,
        shadow_mode=active.shadow_mode,
        reason=reason,
    )


def resolve_active_workspace_write_rollout(
    presentation_id: uuid.UUID | None = None,
) -> WorkspaceWriteRolloutDecision:
    """Resolve only cohort/kill controls after runtime admission succeeded.

    Bootstrap and generation compatibility paths already perform the
    authoritative runtime check in their historical admission helpers.  This
    focused resolver lets them layer the same deterministic rollout controls
    without re-running that check or changing their test seam.
    """

    kill_switch_active = _boolean(_KILL_SWITCH_ENV)
    shadow_mode = _boolean(_SHADOW_ENV)
    rollout_basis_points = _rollout_basis_points()
    scope_required = presentation_id is None and rollout_basis_points < 10_000
    bucket = (
        workspace_cohort_bucket(presentation_id)
        if presentation_id is not None
        else None
    )
    eligible = not scope_required and (
        bucket is None or bucket < rollout_basis_points
    )
    if kill_switch_active:
        enabled = False
        reason = "chat_first_kill_switch_enabled"
    elif scope_required:
        enabled = False
        reason = "chat_first_presentation_scope_required"
    elif not eligible:
        enabled = False
        reason = "chat_first_cohort_excluded"
    else:
        enabled = True
        reason = None
    return WorkspaceWriteRolloutDecision(
        enabled=enabled,
        cohort_eligible=eligible,
        cohort_bucket=bucket,
        kill_switch_active=kill_switch_active,
        shadow_mode=shadow_mode,
        reason=reason,
    )
