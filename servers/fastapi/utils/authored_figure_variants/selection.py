"""Pick one rendered candidate from deterministic geometry alone.

Two rules govern the choice, in this order:

1. **No deterministic regression.** A candidate is eligible only when the
   existing DOM gate (``has_dom_quality_regression``) says it does not add or
   worsen any issue the baseline already had. Best-of-N may improve a slide; it
   may never trade a safety check away for a prettier score.
2. **Lowest weighted penalty.** Overflow and viewport escape dominate, then
   text-box overlap, then whitespace balance and the achieved ink ratio. Ties
   are resolved by render order, so the baseline wins whenever a variant is not
   strictly better.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from utils.authored_dom_quality import DomQualityReport, has_dom_quality_regression
from utils.authored_figure_variants.metrics import CandidateMetrics, ink_ratio_penalty
from utils.authored_figure_variants.variants import FigureVariant

#: Penalty weights. Overflow/escape are hard failures the gate also reports, so
#: they outweigh the two aesthetic terms by an order of magnitude.
OVERFLOW_WEIGHT = 6.0
OFF_CANVAS_WEIGHT = 6.0
OVERLAP_WEIGHT = 4.0
BALANCE_WEIGHT = 1.0
INK_WEIGHT = 1.5
#: A variant must beat the baseline by more than this to displace it.
SELECTION_MARGIN = 1e-6


@dataclass(frozen=True)
class RenderedCandidate:
    """A candidate that has been through Chromium once."""

    variant: FigureVariant
    metrics: CandidateMetrics
    report: DomQualityReport | None = None


@dataclass(frozen=True)
class FigureSelection:
    """Which candidate won, what every candidate scored, and why."""

    chosen_index: int
    scores: tuple[float, ...]
    eligible: tuple[bool, ...]
    candidates: tuple[RenderedCandidate, ...]

    @property
    def chosen(self) -> RenderedCandidate:
        return self.candidates[self.chosen_index]

    @property
    def improved(self) -> bool:
        return self.chosen_index != 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_chosen": self.chosen.variant.variant_id,
            "density": self.chosen.variant.density_class,
            "emphasis_index": self.chosen.variant.emphasis_index,
            "candidates": [
                {
                    **candidate.variant.to_dict(),
                    "score": round(score, 6),
                    "eligible": eligible,
                    "metrics": candidate.metrics.to_dict(),
                }
                for candidate, score, eligible in zip(
                    self.candidates, self.scores, self.eligible, strict=True
                )
            ],
        }


def candidate_score(metrics: CandidateMetrics) -> float:
    """Lower is better. Every term is normalised against the rendered canvas."""
    height = metrics.viewport_height or 720.0
    area = (metrics.viewport_width or 1280.0) * height
    return (
        OVERFLOW_WEIGHT * (metrics.overflow_px / height)
        + OFF_CANVAS_WEIGHT * (metrics.off_canvas_px / height)
        + OVERLAP_WEIGHT * (metrics.text_overlap_area / area if area else 0.0)
        + BALANCE_WEIGHT * (1.0 - metrics.whitespace_balance)
        + INK_WEIGHT * ink_ratio_penalty(metrics.ink_ratio)
    )


def select_candidate(candidates: Sequence[RenderedCandidate]) -> FigureSelection:
    """Choose one candidate; the first entry is always treated as the baseline."""
    if not candidates:
        raise ValueError("candidate selection needs at least the baseline render")
    baseline = candidates[0]
    scores = tuple(candidate_score(candidate.metrics) for candidate in candidates)
    eligible: list[bool] = [True]
    for candidate in candidates[1:]:
        if candidate.report is None or baseline.report is None:
            # Without both reports the no-regression rule cannot be proven, and
            # an unproven variant must not displace what the author wrote.
            eligible.append(False)
            continue
        eligible.append(not has_dom_quality_regression(baseline.report, candidate.report))
    chosen = 0
    best = scores[0]
    for index in range(1, len(candidates)):
        if eligible[index] and scores[index] < best - SELECTION_MARGIN:
            chosen, best = index, scores[index]
    return FigureSelection(
        chosen_index=chosen,
        scores=scores,
        eligible=tuple(eligible),
        candidates=tuple(candidates),
    )


__all__ = [
    "BALANCE_WEIGHT",
    "INK_WEIGHT",
    "OFF_CANVAS_WEIGHT",
    "OVERFLOW_WEIGHT",
    "OVERLAP_WEIGHT",
    "SELECTION_MARGIN",
    "FigureSelection",
    "RenderedCandidate",
    "candidate_score",
    "select_candidate",
]
