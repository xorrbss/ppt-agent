"""Default-on policy with explicit rollback for SlideSpec quality repair.

This is intentionally independent from the Chat-first workspace rollout.  The
The workspace owns write admission, while this module keeps operational
rollback controls (kill switch, cohort, slide scope, and per-request legacy
comparison) independent from that admission decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
import uuid


PAGE_11_SLIDE_INDEX = 10
_TRUE = frozenset({"1", "true", "on", "yes"})
_IMPORT_SHADOW = "SLIDESPEC_IMPORT_SHADOW"
_REPAIR_SHADOW = "SLIDESPEC_REPAIR_SHADOW"
_REPAIR_CANARY = "SLIDESPEC_REPAIR_CANARY"
_REPAIR_APPLY = "SLIDESPEC_REPAIR_APPLY"
_REPAIR_ALL_SLIDES = "SLIDESPEC_REPAIR_ALL_SLIDES"
_KILL_SWITCH = "SLIDESPEC_KILL_SWITCH"
_ROLLOUT_SALT = "SLIDESPEC_REPAIR_ROLLOUT_SALT"


def _enabled(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE


def _canary_basis_points() -> int:
    """Parse an explicit percent, with true as an intentional 100% opt-in."""

    raw = os.getenv(_REPAIR_CANARY, "100").strip().lower()
    if raw in _TRUE:
        return 10_000
    try:
        return max(0, min(10_000, round(float(raw) * 100)))
    except (TypeError, ValueError):
        return 0


def slidespec_cohort_bucket(
    presentation_id: uuid.UUID,
    *,
    owner_id: object | None = None,
    salt: str | None = None,
) -> int:
    """Return a stable, non-identifying 0..9999 bucket."""

    seed = ":".join(
        (
            salt if salt is not None else os.getenv(_ROLLOUT_SALT, "v1"),
            str(owner_id if owner_id is not None else "anonymous"),
            str(presentation_id),
        )
    )
    return int.from_bytes(sha256(seed.encode("utf-8")).digest()[:8], "big") % 10_000


@dataclass(frozen=True, slots=True)
class SlideSpecRepairRolloutDecision:
    """A request-scoped decision; ``publisher_allowed`` is the write authority."""

    route: str
    import_shadow: bool
    repair_shadow: bool
    canary_eligible: bool
    apply_enabled: bool
    kill_switch_active: bool
    cohort_bucket: int | None
    reason_code: str | None

    @property
    def should_run_repair(self) -> bool:
        return self.route in {"repair_shadow", "slidespec_active"}

    @property
    def shadow_mode(self) -> bool:
        return self.route == "repair_shadow"

    @property
    def publisher_allowed(self) -> bool:
        return self.route == "slidespec_active" and self.apply_enabled


def resolve_slidespec_repair_rollout(
    presentation_id: uuid.UUID,
    *,
    slide_index: int,
    opt_in: bool,
    owner_id: object | None = None,
) -> SlideSpecRepairRolloutDecision:
    """Resolve the default-on SlideSpec path with explicit rollback controls.

    SlideSpec repair and publication are active for all slides by default.
    Deployments can narrow the cohort or slide scope for diagnosis, while
    ``SLIDESPEC_KILL_SWITCH=true`` immediately restores the legacy path.
    """

    import_shadow = _enabled(_IMPORT_SHADOW)
    repair_shadow = _enabled(_REPAIR_SHADOW)
    kill_switch = _enabled(_KILL_SWITCH)
    apply_enabled = _enabled(_REPAIR_APPLY, default=True)
    slide_scope_eligible = (
        _enabled(_REPAIR_ALL_SLIDES, default=True)
        or slide_index == PAGE_11_SLIDE_INDEX
    )
    bucket = slidespec_cohort_bucket(presentation_id, owner_id=owner_id)
    canary_eligible = bucket < _canary_basis_points()

    if kill_switch:
        return SlideSpecRepairRolloutDecision(
            "legacy", import_shadow, repair_shadow, False, apply_enabled, True,
            bucket, "system.kill_switch_enabled",
        )
    if not opt_in or not slide_scope_eligible or not canary_eligible:
        if repair_shadow:
            return SlideSpecRepairRolloutDecision(
                "repair_shadow", import_shadow, True, False, apply_enabled, False,
                bucket, "legacy_preserved.canary_not_admitted",
            )
        return SlideSpecRepairRolloutDecision(
            "import_shadow" if import_shadow else "legacy", import_shadow,
            repair_shadow, False, apply_enabled, False, bucket,
            "legacy_preserved.canary_not_admitted",
        )
    if not apply_enabled:
        return SlideSpecRepairRolloutDecision(
            "repair_shadow", import_shadow, repair_shadow, True, False, False,
            bucket, "legacy_preserved.apply_disabled",
        )
    return SlideSpecRepairRolloutDecision(
        "slidespec_active", import_shadow, repair_shadow, True, True, False,
        bucket, None,
    )
