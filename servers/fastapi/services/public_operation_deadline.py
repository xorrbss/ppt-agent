"""Absolute deadline derivation shared by every public admission entry point.

Split out of ``services/public_operation_admission.py`` (a frozen file-size
exception) rather than grown in place.  The policy row column is the immutable
per-version *ceiling*; the slide-count formula of the operation kind runs
underneath it and admission pins ``min(ceiling, formula)``, bounded further by
the auth expiry and the server hard deadline.  Nothing here reads the
database or the clock: the caller supplies ``now`` so the pinned instant and
``queued_at`` agree.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models.sql.public_admission import PublicPolicyProfile
from services.public_export_deadline_policy import public_export_deadline_seconds
from services.public_generation_deadline_policy import (
    public_generation_deadline_seconds,
)
from services.public_operation_admission_models import (
    _PHASE2_COMMIT_RESERVE_SECONDS,
    ProviderRequestPlan,
    PublicAdmissionRejected,
    _nonnegative,
)


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field}_required")
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _planned_slide_count(
    operation_kind: str,
    request_plan: ProviderRequestPlan | None,
    planned_slide_count: int | None,
) -> int:
    """Generation reads the provider plan; export reads the deck's own count."""

    if operation_kind == "generation":
        return request_plan.slide_count if request_plan is not None else 0
    return _nonnegative(planned_slide_count or 0, "planned_slide_count")


def _policy_deadline_seconds(
    *,
    policy: PublicPolicyProfile,
    operation_kind: str,
    planned_slide_count: int,
) -> int:
    """Return the policy budget for the kind, scaled by slide count.

    The policy column is the immutable per-version ceiling and each kind's
    formula decides underneath it: a version that keeps a legacy fixed budget
    (generation 900 s in ``public-default/v1``/``v2``, export 600 s up to
    ``v4``) is unchanged, while the version that raised the ceiling
    (generation ``v3``/``v4``, export ``v5``) lets the formula decide.
    """

    ceiling = _nonnegative(
        int(getattr(policy, f"{operation_kind}_deadline_seconds")),
        "policy_deadline_seconds",
    )
    formula = (
        public_generation_deadline_seconds
        if operation_kind == "generation"
        else public_export_deadline_seconds
    )
    return min(ceiling, formula(planned_slide_count))


def _absolute_deadline(
    *,
    now: datetime,
    policy: PublicPolicyProfile,
    operation_kind: str,
    planned_slide_count: int,
    auth_expires_at: datetime | None,
    server_hard_deadline_at: datetime | None,
) -> datetime:
    seconds = _policy_deadline_seconds(
        policy=policy,
        operation_kind=operation_kind,
        planned_slide_count=planned_slide_count,
    )
    if seconds < _PHASE2_COMMIT_RESERVE_SECONDS:
        raise PublicAdmissionRejected("public_policy_deadline_invalid")
    candidates = [_utc(now, "admission_now") + timedelta(seconds=seconds)]
    if auth_expires_at is not None:
        candidates.append(_utc(auth_expires_at, "auth_expires_at"))
    if server_hard_deadline_at is not None:
        candidates.append(_utc(server_hard_deadline_at, "server_hard_deadline_at"))
    deadline = min(candidates)
    if deadline <= _utc(now, "admission_now") + timedelta(
        seconds=_PHASE2_COMMIT_RESERVE_SECONDS
    ):
        raise PublicAdmissionRejected("public_deadline_insufficient")
    return deadline


__all__ = [
    "_absolute_deadline",
    "_planned_slide_count",
    "_policy_deadline_seconds",
    "_utc",
]
