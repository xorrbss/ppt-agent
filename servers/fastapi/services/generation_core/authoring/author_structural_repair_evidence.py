"""Budget and measured evidence helpers for authored structural repair."""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from services.authored_structural_repair import RepairAssessment, SlideRepairResult
from services.authored_source_visibility_repair import _is_root_page_number
from utils.authored_dom_quality import build_compact_dom_map, normalize_browser_metrics

@dataclass
class StructuralRepairBudget:
    """One slide's shared deterministic/model candidate budget.

    A provider proposal is only started while a capture slot is available.  A
    capture is charged before Chromium/local-fit starts, so failed or cancelled
    work cannot silently restore the budget.
    """

    max_candidate_captures: int = 3
    max_provider_transmissions: int = 2
    candidate_captures: int = 0
    provider_transmissions: int = 0

    def can_capture(self, *, reserve_for_model: int = 0) -> bool:
        return self.candidate_captures < max(
            0, self.max_candidate_captures - max(0, reserve_for_model)
        )

    def claim_capture(self, *, reserve_for_model: int = 0) -> bool:
        if not self.can_capture(reserve_for_model=reserve_for_model):
            return False
        self.candidate_captures += 1
        return True

    def can_propose_model_candidate(self) -> bool:
        return (
            self.provider_transmissions < self.max_provider_transmissions
            and self.can_capture()
        )

    def claim_provider_transmission(self) -> bool:
        if not self.can_propose_model_candidate():
            return False
        self.provider_transmissions += 1
        return True

    def receipt(self) -> dict[str, int]:
        return {
            "candidate_capture_limit": self.max_candidate_captures,
            "candidate_captures": self.candidate_captures,
            "candidate_captures_remaining": max(
                0, self.max_candidate_captures - self.candidate_captures
            ),
            "provider_transmission_limit": self.max_provider_transmissions,
            "provider_transmissions": self.provider_transmissions,
            "provider_transmissions_remaining": max(
                0, self.max_provider_transmissions - self.provider_transmissions
            ),
        }

def _repair_evaluator_for(
    candidate_evaluator: Callable[[int, str, Mapping[str, Any]], Awaitable[RepairAssessment]] | None,
    candidate_evaluators: Mapping[int, Callable[[int, str, Mapping[str, Any]], Awaitable[RepairAssessment]]] | None,
    slide_index: int,
):
    if candidate_evaluators is not None:
        try:
            return candidate_evaluators[slide_index]
        except KeyError as error:
            raise ValueError("candidate evaluator missing for structural slide") from error
    if candidate_evaluator is None:
        raise ValueError("candidate evaluator is required for structural repair")
    return candidate_evaluator

def _advance_repair_evaluator(
    evaluator: object,
    assessment: RepairAssessment,
    html: str,
    metrics: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> None:
    """Advance only an explicitly selected candidate; legacy callables remain valid."""

    advance = getattr(evaluator, "advance", None)
    if not callable(advance):
        return
    expected = getattr(evaluator, "selected_digest", None)
    if not isinstance(expected, str) or not expected:
        raise ValueError("repair_selected_state_unavailable")
    advance(
        expected,
        assessment,
        html,
        dict(metrics),
        context=dict(context) if context is not None else None,
    )

class AuthoredVqaResidualDomIssuesError(ValueError):
    """Fail-closed source-reflow result that still exposes durable repairs.

    Local patches are provider work that may have succeeded even when another
    slide still has a blocking DOM issue.  Carry the successful HTML/PNG pairs
    to the checkpoint owner so a retry resumes from those repairs instead of
    presenting the same original deck to VQA again.
    """

    def __init__(
        self,
        summary: str,
        *,
        htmls: Sequence[str],
        pngs: Sequence[bytes],
        fixed: Sequence[int],
        repair_details: Sequence[Mapping[str, Any]],
        repair_results: Sequence[SlideRepairResult] = (),
    ) -> None:
        super().__init__(f"authored_vqa_residual_dom_issues:{summary}")
        self.htmls = list(htmls)
        self.pngs = list(pngs)
        self.fixed = list(fixed)
        self.repair_details = [dict(item) for item in repair_details]
        self.repair_results = list(repair_results)

def _structural_compact_dom(html: str, metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Retain measured parent/layout fields needed to plan a complete sibling group."""
    measured = {
        item["qid"]: item
        for item in normalize_browser_metrics(metrics)["elements"]
        if item.get("qid")
    }
    result = []
    for node in build_compact_dom_map(html, metrics):
        item = node.to_dict()
        evidence = measured.get(node.qid, {})
        item["parentQid"] = evidence.get("parent_qid", "")
        item["sourceBlockId"] = evidence.get(
            "sourceBlockId", evidence.get("source_block_id", "")
        )
        item["semanticRole"] = evidence.get("semanticRole", evidence.get("semantic_role", ""))
        computed = evidence.get("computed_style", evidence.get("style", {}))
        if isinstance(computed, Mapping):
            for key in (
                "gap", "rowGap", "columnGap", "gridTemplateColumns",
                "gridTemplateRows", "width", "height", "minWidth", "minHeight",
                "maxWidth", "maxHeight", "boxSizing", "flexWrap",
            ):
                if key in computed:
                    item["style"][key] = str(computed[key])[:100]
        result.append(item)
    return result

def _measured_causal_issue_qids(
    metrics: Mapping[str, Any], *, issue_qids: Collection[str],
) -> tuple[str, ...]:
    """Preserve actual defects plus their digest-bound, one-hop covering nodes."""
    def number(value: object) -> float | None:
        try:
            parsed = float(str(value).removesuffix("px"))
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    elements = normalize_browser_metrics(metrics)["elements"]
    by_qid = {str(item["qid"]): item for item in elements if item.get("qid")}
    causal_targets = {str(target) for target in issue_qids}
    for target in tuple(causal_targets):
        measured = by_qid.get(target, {})
        evidence = measured.get("occlusion_measurement_evidence")
        occluders = measured.get("occlusion_occluder_qids", ())
        # A non-source DOM overlap can report only the covered text. Include
        # the measured covering branch as a cause, but never follow guessed,
        # stale, unknown or recursively unrelated occlusion relationships.
        if (
            isinstance(evidence, Mapping)
            and evidence.get("status") == "measured"
            and evidence.get("qid") == target
            and evidence.get("metric") == "occlusion"
            # Capture caches rebind the outer source identity to the
            # pre-admission HTML; in-page evidence retains the admitted
            # render projection as its source. Both must name this capture.
            and evidence.get("source_digest") in {
                metrics.get("source_digest"), metrics.get("render_digest"),
            }
            and evidence.get("render_digest") == metrics.get("render_digest")
            and bool(metrics.get("source_digest"))
            and bool(metrics.get("render_digest"))
            and (number(evidence.get("known")) or 0) > 0
            and (number(evidence.get("covered")) or 0) > 0
            and number(measured.get("occlusion_unknown_fraction")) == 0
            and number(measured.get("occlusion_unbound_count")) == 0
            and isinstance(occluders, (list, tuple))
            and 0 < len(occluders) <= 8
        ):
            causal_targets.update(
                str(qid) for qid in occluders
                if str(qid) in by_qid
                and not _is_root_page_number(by_qid[str(qid)], metrics)
            )
    return tuple(sorted(causal_targets))

def _measured_overflowing_grid_qids(
    metrics: Mapping[str, Any], *, issue_qids: Collection[str] | None = None,
) -> tuple[str, ...]:
    """Name overflowing grids owning an actual defect or its proven occluder.

    Unfiltered inventory remains available to diagnostic callers. Repair callers
    must supply their current issues: harmless intrinsic grid overflow elsewhere
    must not create an unrelated repair group or merge two independent regions.
    """
    def number(value: object) -> float | None:
        try:
            parsed = float(str(value).removesuffix("px"))
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    elements = normalize_browser_metrics(metrics)["elements"]
    by_qid = {str(item["qid"]): item for item in elements if item.get("qid")}
    causal_ancestors: set[str] = set()
    if issue_qids is not None:
        for target in _measured_causal_issue_qids(metrics, issue_qids=issue_qids):
            current = str(target)
            chain: set[str] = set()
            while current and current in by_qid and current not in chain:
                chain.add(current)
                current = str(by_qid[current].get("parent_qid") or "")
            # Missing parents and cycles cannot establish measured ownership.
            if not current:
                causal_ancestors.update(chain)

    owners: list[str] = []
    for item in elements:
        qid = str(item.get("qid") or "")
        if issue_qids is not None and qid not in causal_ancestors:
            continue
        style = item.get("computed_style", item.get("style", {}))
        if not qid or not isinstance(style, Mapping) or style.get("display") != "grid":
            continue
        client_width = number(item.get("clientWidth", item.get("client_width")))
        scroll_width = number(item.get("scrollWidth", item.get("scroll_width")))
        client_height = number(item.get("clientHeight", item.get("client_height")))
        scroll_height = number(item.get("scrollHeight", item.get("scroll_height")))
        if (
            client_width is not None and scroll_width is not None
            and scroll_width > client_width + 1.0
        ) or (
            client_height is not None and scroll_height is not None
            and scroll_height > client_height + 1.0
        ):
            owners.append(qid)
    return tuple(sorted(set(owners)))

def _measured_structural_issue_qids(
    metrics: Mapping[str, Any], *, issue_qids: Collection[str],
) -> tuple[str, ...]:
    """Keep both the covering branch and its grid owner in the scope targets."""
    return tuple(sorted({
        *_measured_causal_issue_qids(metrics, issue_qids=issue_qids),
        *_measured_overflowing_grid_qids(metrics, issue_qids=issue_qids),
    }))
