"""Rollout policy for SlideSpec quality repair, reached only from the manual
quality review.

Scope, stated plainly: SlideSpec import/inspect/repair/publish runs only when
a user triggers the quality review
(``services/authored_quality_review_service.py``, which calls
:func:`resolve_slidespec_repair_rollout` with ``opt_in=True``). The
generation pipeline never invokes it -- no slide is repaired while a deck is
being generated, and production has applied zero SlideSpec repairs outside a
review (see ``docs/slidespec-production-quality-speed-analysis-20260805.md``).
The knobs below therefore narrow or disable what that review may do; none of
them turns SlideSpec on for generation.

``SLIDESPEC_*`` knobs (env only; not in ``constants/settings_registry.json``):

* ``SLIDESPEC_KILL_SWITCH`` -- ``true`` makes every review take the legacy
  route, even an explicit opt-in.
* ``SLIDESPEC_REPAIR_APPLY`` (default ``true``) -- ``false`` keeps a review
  in shadow mode: repairs are computed and reported, never published.
* ``SLIDESPEC_REPAIR_ALL_SLIDES`` (default ``true``) -- ``false`` limits a
  review to the page-11 diagnostic slide (``PAGE_11_SLIDE_INDEX``).
* ``SLIDESPEC_REPAIR_CANARY`` (default ``100``) -- percent of the stable
  owner/presentation cohort a review may repair; ``true`` means 100.
* ``SLIDESPEC_REPAIR_ROLLOUT_SALT`` -- salt for that cohort bucket.
* ``SLIDESPEC_REPAIR_SHADOW`` -- report-only repair runs alongside a review
  that is not admitted to repair. (``SLIDESPEC_IMPORT_SHADOW`` was removed: the
  importer only runs for ``slidespec_active``, so it only relabelled skipped
  slides; the ``import_shadow`` value stays valid on stored bundle rows.)

This is intentionally independent from the Chat-first workspace rollout. The
workspace owns write admission, while this module keeps operational rollback
controls (kill switch, cohort, slide scope, and per-request legacy
comparison) independent from that admission decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import uuid

from utils.env_settings.authored_runtime import get_slidespec_rollout_settings


PAGE_11_SLIDE_INDEX = 10


def slidespec_cohort_bucket(
    presentation_id: uuid.UUID,
    *,
    owner_id: object | None = None,
    salt: str | None = None,
) -> int:
    """Return a stable, non-identifying 0..9999 bucket."""

    seed = ":".join(
        (
            salt
            if salt is not None
            else get_slidespec_rollout_settings().rollout_salt,
            str(owner_id if owner_id is not None else "anonymous"),
            str(presentation_id),
        )
    )
    return int.from_bytes(sha256(seed.encode("utf-8")).digest()[:8], "big") % 10_000


@dataclass(frozen=True, slots=True)
class SlideSpecRepairRolloutDecision:
    """A request-scoped decision; ``publisher_allowed`` is the write authority."""

    route: str
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
    """Decide what a user-triggered quality review may do with SlideSpec.

    The only caller is the manual quality review, which passes
    ``opt_in=True``; generation never calls this, so ``opt_in=False`` (and
    therefore the legacy route) is what every non-review path would get.
    Within a review, all slides are eligible by default; deployments can
    narrow the cohort or slide scope for diagnosis, hold repairs in shadow
    mode, or set ``SLIDESPEC_KILL_SWITCH=true`` to force the legacy path.
    """

    rollout = get_slidespec_rollout_settings()
    repair_shadow = rollout.repair_shadow
    kill_switch = rollout.kill_switch
    apply_enabled = rollout.repair_apply
    slide_scope_eligible = (
        rollout.repair_all_slides
        or slide_index == PAGE_11_SLIDE_INDEX
    )
    bucket = slidespec_cohort_bucket(presentation_id, owner_id=owner_id)
    canary_eligible = bucket < rollout.canary_basis_points

    if kill_switch:
        return SlideSpecRepairRolloutDecision(
            "legacy", repair_shadow, False, apply_enabled, True,
            bucket, "system.kill_switch_enabled",
        )
    if not opt_in or not slide_scope_eligible or not canary_eligible:
        if repair_shadow:
            return SlideSpecRepairRolloutDecision(
                "repair_shadow", True, False, apply_enabled, False,
                bucket, "legacy_preserved.canary_not_admitted",
            )
        return SlideSpecRepairRolloutDecision(
            "legacy", repair_shadow, False, apply_enabled, False, bucket,
            "legacy_preserved.canary_not_admitted",
        )
    if not apply_enabled:
        return SlideSpecRepairRolloutDecision(
            "repair_shadow", repair_shadow, True, False, False,
            bucket, "legacy_preserved.apply_disabled",
        )
    return SlideSpecRepairRolloutDecision(
        "slidespec_active", repair_shadow, True, True, False,
        bucket, None,
    )
