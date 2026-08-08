"""Pure routing policy for the hybrid AI-authored quality review.

The review service owns rendering and model calls.  This module deliberately has
no database, browser, or provider dependency so that the safety-critical choice
of what receives image VQA can be inspected and tested independently.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
from math import ceil
from typing import Literal

ReviewScope = Literal["all", "current", "subset"]
EscalationLevel = Literal["none", "batch", "full"]


@dataclass(frozen=True)
class MontageBatch:
    """A small group of slides that receives one global-consistency image review."""

    batch_index: int
    slide_indices: tuple[int, ...]


@dataclass(frozen=True)
class ReviewRoutingPlan:
    """The work selected after every requested slide has passed local checks."""

    scope: ReviewScope
    slide_indices: tuple[int, ...]
    local_check_indices: tuple[int, ...]
    montage_batches: tuple[MontageBatch, ...]
    suspected_slide_indices: tuple[int, ...]
    clean_audit_indices: tuple[int, ...]
    detail_vqa_indices: tuple[int, ...]
    sampling_seed: str
    detail_vqa_is_full_resolution: bool = True

    def as_dict(self) -> dict:
        """Return JSON-safe telemetry without exposing implementation objects."""

        return asdict(self)


@dataclass(frozen=True)
class ReviewRoutingEscalation:
    """Detail-VQA work added when a deterministic clean-slide audit detects risk."""

    level: EscalationLevel
    failed_audit_indices: tuple[int, ...]
    affected_batch_indices: tuple[int, ...]
    additional_detail_vqa_indices: tuple[int, ...]
    detail_vqa_indices: tuple[int, ...]
    reason: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _unique_sorted(indices: Sequence[int], *, slide_count: int) -> tuple[int, ...]:
    if any(not isinstance(index, int) or isinstance(index, bool) for index in indices):
        raise ValueError("Slide indices must be integers")
    normalized = sorted(set(indices))
    if any(index < 0 or index >= slide_count for index in normalized):
        raise ValueError("Slide index is outside the presentation")
    return tuple(normalized)


def _deterministic_sample(
    indices: Sequence[int], *, count: int, seed: str
) -> tuple[int, ...]:
    """Choose a stable pseudo-random sample independent of input ordering."""

    if count <= 0 or not indices:
        return ()
    ranked = sorted(
        indices,
        key=lambda index: sha256(f"{seed}:{index}".encode()).digest(),
    )
    return tuple(sorted(ranked[: min(count, len(ranked))]))


def _build_montage_batches(
    selected: tuple[int, ...], *, preferred_batch_size: int
) -> tuple[MontageBatch, ...]:
    """Balance 6--9-up contact sheets while covering every selected slide.

    A 10- or 11-slide selection cannot be *partitioned* into 6--9 slide
    groups.  In those two boundary cases, use two adjacent 6-up sheets and
    overlap their middle slides.  This retains a readable contact-sheet size
    and, unlike the former full-detail fallback, keeps global consistency
    review enabled for every selected slide.
    """

    count = len(selected)
    if count < 6:
        return ()
    if count in (10, 11):
        return (
            MontageBatch(batch_index=0, slide_indices=selected[:6]),
            MontageBatch(batch_index=1, slide_indices=selected[-6:]),
        )
    minimum_batches = ceil(count / 9)
    maximum_batches = count // 6
    preferred_batches = ceil(count / preferred_batch_size)
    if minimum_batches > maximum_batches:
        raise ValueError("selected slides cannot form 6--9 slide montage batches")
    batch_count = min(max(preferred_batches, minimum_batches), maximum_batches)
    base, remainder = divmod(count, batch_count)
    sizes = tuple(
        base + (1 if index >= batch_count - remainder else 0)
        for index in range(batch_count)
    )
    batches: list[MontageBatch] = []
    cursor = 0
    for batch_index, size in enumerate(sizes):
        batches.append(
            MontageBatch(
                batch_index=batch_index,
                slide_indices=selected[cursor : cursor + size],
            )
        )
        cursor += size
    return tuple(batches)


def plan_authored_review_routing(
    *,
    scope: ReviewScope,
    slide_count: int,
    local_issue_indices: Sequence[int] = (),
    current_slide_index: int | None = None,
    target_indices: Sequence[int] | None = None,
    sampling_seed: str = "authored-quality-review",
    batch_size: int = 8,
    clean_audit_rate: float = 0.15,
) -> ReviewRoutingPlan:
    """Plan local, montage, and detail image inspection for an authored review.

    A current-slide review never trades quality for speed: it receives a
    full-resolution detail VQA call regardless of local findings.  A deck review
    first locally checks every slide, then creates 6--9 slide montage batches,
    sends local suspects to detail VQA, and audits a deterministic subset of
    otherwise-clean slides.  The caller must call :func:`apply_audit_results`
    before accepting a deck-level result.
    """

    if scope not in ("all", "current", "subset"):
        raise ValueError("scope must be 'all', 'current', or 'subset'")
    if slide_count < 1:
        raise ValueError("slide_count must be positive")
    if not 6 <= batch_size <= 9:
        raise ValueError("batch_size must be between 6 and 9")
    if not 0 <= clean_audit_rate <= 1:
        raise ValueError("clean_audit_rate must be between 0 and 1")

    issue_indices = _unique_sorted(local_issue_indices, slide_count=slide_count)
    explicit_targets = (
        _unique_sorted(target_indices, slide_count=slide_count)
        if target_indices is not None
        else None
    )
    if scope == "current":
        if explicit_targets is not None:
            if len(explicit_targets) != 1:
                raise ValueError("current scope accepts exactly one target index")
            selected = explicit_targets
        elif current_slide_index is None:
            raise ValueError("current_slide_index is required for current scope")
        else:
            selected = _unique_sorted((current_slide_index,), slide_count=slide_count)
        suspects = tuple(index for index in issue_indices if index in selected)
        return ReviewRoutingPlan(
            scope=scope,
            slide_indices=selected,
            local_check_indices=selected,
            montage_batches=(),
            suspected_slide_indices=suspects,
            clean_audit_indices=(),
            detail_vqa_indices=selected,
            sampling_seed=sampling_seed,
        )

    if scope == "subset" and explicit_targets is None:
        raise ValueError("subset scope requires target_indices")
    if scope == "subset" and not explicit_targets:
        raise ValueError("subset scope requires at least one target index")

    selected = (
        explicit_targets if explicit_targets is not None else tuple(range(slide_count))
    )
    suspect_set = set(issue_indices).intersection(selected)
    # Small selections cannot form a legible 6--9 slide montage, so retain the
    # full-detail quality gate.  Every selection of six or more slides is
    # covered by montage batches, including 10/11 via an intentional overlap.
    if len(selected) < 6:
        return ReviewRoutingPlan(
            scope=scope,
            slide_indices=selected,
            local_check_indices=selected,
            montage_batches=(),
            suspected_slide_indices=tuple(sorted(suspect_set)),
            clean_audit_indices=(),
            detail_vqa_indices=selected,
            sampling_seed=sampling_seed,
        )

    clean_indices = tuple(index for index in selected if index not in suspect_set)
    audit_count = min(len(clean_indices), ceil(len(clean_indices) * clean_audit_rate))
    clean_audits = _deterministic_sample(
        clean_indices, count=audit_count, seed=sampling_seed
    )
    batches = _build_montage_batches(selected, preferred_batch_size=batch_size)
    detail_indices = tuple(sorted(suspect_set | set(clean_audits)))
    return ReviewRoutingPlan(
        scope=scope,
        slide_indices=selected,
        local_check_indices=selected,
        montage_batches=batches,
        suspected_slide_indices=tuple(sorted(suspect_set)),
        clean_audit_indices=clean_audits,
        detail_vqa_indices=detail_indices,
        sampling_seed=sampling_seed,
    )


def apply_audit_results(
    plan: ReviewRoutingPlan,
    *,
    failed_audit_indices: Sequence[int] = (),
    full_escalation_batch_threshold: int = 2,
) -> ReviewRoutingEscalation:
    """Escalate safely after clean-slide audit failures.

    One failed clean audit expands its montage batch to detail VQA.  Failures in
    two independent batches expand the whole requested deck, because the local
    checker has then demonstrated a cross-batch false-negative pattern.  A
    current-slide plan has no clean audit and therefore cannot be escalated here.
    """

    if full_escalation_batch_threshold < 1:
        raise ValueError("full_escalation_batch_threshold must be positive")
    failed = _unique_sorted(
        failed_audit_indices, slide_count=max(plan.slide_indices) + 1
    )
    audit_set = set(plan.clean_audit_indices)
    if not set(failed).issubset(audit_set):
        raise ValueError("Only selected clean-slide audits can trigger escalation")
    if not failed:
        return ReviewRoutingEscalation(
            level="none",
            failed_audit_indices=(),
            affected_batch_indices=(),
            additional_detail_vqa_indices=(),
            detail_vqa_indices=plan.detail_vqa_indices,
        )

    # Boundary plans for 10/11 slides intentionally overlap two montage sheets.
    # Attribute each failed audit to one deterministic owner batch so one failed
    # slide in the overlap cannot masquerade as independent failures in two
    # batches and trigger a premature full-deck escalation.
    owner_batch_by_slide = {
        slide_index: next(
            batch.batch_index
            for batch in plan.montage_batches
            if slide_index in batch.slide_indices
        )
        for slide_index in failed
    }
    affected_batches = tuple(sorted(set(owner_batch_by_slide.values())))
    if len(affected_batches) >= full_escalation_batch_threshold:
        expanded = plan.slide_indices
        level: EscalationLevel = "full"
        reason = "clean_audit_failed_in_multiple_batches"
    else:
        expanded = tuple(
            index
            for batch in plan.montage_batches
            if batch.batch_index in affected_batches
            for index in batch.slide_indices
        )
        level = "batch"
        reason = "clean_audit_failed"
    all_detail = tuple(sorted(set(plan.detail_vqa_indices) | set(expanded)))
    return ReviewRoutingEscalation(
        level=level,
        failed_audit_indices=failed,
        affected_batch_indices=affected_batches,
        additional_detail_vqa_indices=tuple(
            index for index in all_detail if index not in set(plan.detail_vqa_indices)
        ),
        detail_vqa_indices=all_detail,
        reason=reason,
    )
