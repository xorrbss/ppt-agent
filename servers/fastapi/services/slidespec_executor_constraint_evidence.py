"""Hard and soft constraint evidence for canonical SlideSpec review."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from models.slidespec_v1 import RenderRequest
from services.slidespec_authored_executor import _semantic_text, _walk_layout
from services.slidespec_repair_pipeline import DomDefect, DomEvidence, EvidenceAtom
from utils.slidespec_constraints import (
    ConstraintReport,
    constraint_targets,
    has_soft_constraint_regression,
)

def _constraint_evidence(
    request: RenderRequest,
    report: ConstraintReport,
    observations: Mapping[str, Any] | None = None,
) -> DomEvidence:
    """Convert hard relational failures into the pipeline's cheap DOM gate."""

    semantic_by_layout = {
        node.layoutId: node.contentSemanticId
        for node in _walk_layout(request.spec.layout)
        if node.contentSemanticId is not None
    }
    qid_by_layout = {
        binding.layoutId: binding.qid
        for binding in (request.source.bindings if request.source is not None else ())
    }
    fallback_semantic = next(iter(_semantic_text(request.spec)), request.spec.slideId)
    defects: list[DomDefect] = []
    atoms: list[EvidenceAtom] = []
    for violation in report.violations:
        if violation.strength != "hard":
            continue
        semantic_ids = tuple(
            dict.fromkeys(
                semantic_by_layout[target]
                for target in violation.targets
                if target in semantic_by_layout
            )
        )
        semantic_id = semantic_ids[0] if semantic_ids else fallback_semantic
        target_qids = tuple(
            qid_by_layout[target]
            for target in violation.targets
            if qid_by_layout.get(target)
        )
        defects.append(
            DomDefect(
                violation.code,
                "high",
                semantic_id,
                semantic_ids[1:],
                qid=target_qids[0] if target_qids else None,
                diagnostics={
                    "constraint_violation": {
                        "constraint_id": violation.constraint_id,
                        "kind": violation.kind,
                        "targets": list(violation.targets),
                        "target_qids": list(target_qids),
                        "magnitude": violation.magnitude,
                    }
                },
            )
        )
    constraint_by_id = {
        constraint.constraintId: constraint for constraint in request.spec.constraints
    }
    for constraint_id in report.unknown_hard:
        constraint = constraint_by_id.get(constraint_id)
        targets = constraint_targets(constraint) if constraint is not None else ()
        unknown_semantic_ids = tuple(
            dict.fromkeys(
                semantic_by_layout[target]
                for target in targets
                if target in semantic_by_layout
            )
        )
        unknown_semantic_id = (
            unknown_semantic_ids[0]
            if unknown_semantic_ids
            else fallback_semantic
        )
        missing_targets = [
            target
            for target in targets
            if observations is None or target not in observations
        ]
        missing_measurements: dict[str, list[str]] = {}
        if constraint is not None and constraint.kind == "readability":
            for target in targets:
                observation = observations.get(target) if observations else None
                if observation is None:
                    continue
                missing = []
                if constraint.requireVisible and observation.visible is None:
                    missing.append("visible")
                if constraint.requireVisible and observation.occluded_fraction is None:
                    missing.append("occluded_fraction")
                if constraint.noTruncation:
                    for name in ("truncated", "glyph_clipped", "line_box_clipped"):
                        if getattr(observation, name, None) is None:
                            missing.append(name)
                if missing:
                    missing_measurements[target] = missing
        defects.append(
            DomDefect(
                "constraint.evidence.missing",
                "high",
                unknown_semantic_id,
                (constraint_id,),
                diagnostics={
                    "constraint_observation": {
                        "constraint_id": constraint_id,
                        "kind": constraint.kind if constraint is not None else "unknown",
                        "targets": list(targets),
                        "target_qids": {
                            target: qid_by_layout.get(target)
                            for target in targets
                        },
                        "missing_targets": missing_targets,
                        "missing_measurements": missing_measurements,
                    }
                },
            )
        )
        target_qids = tuple(
            qid_by_layout[target]
            for target in targets
            if qid_by_layout.get(target)
        )
        dependency_refs = tuple(
            dict.fromkeys(
                (
                    constraint_id,
                    f"constraint:{constraint_id}",
                    *targets,
                    *target_qids,
                    *(f"qid:{qid}" for qid in target_qids),
                )
            )
        )
        atoms.append(
            EvidenceAtom(
                sensor="slidespec_constraint",
                metric=(constraint.kind if constraint is not None else "unknown"),
                state="unknown",
                stage="constraint_measurement",
                qid=target_qids[0] if target_qids else None,
                semantic_id=unknown_semantic_id,
                constraint_id=constraint_id,
                dependency_refs=dependency_refs,
                reason_code="constraint.evidence.missing",
                diagnostics={
                    "missing_targets": missing_targets,
                    "missing_measurements": missing_measurements,
                    "target_qids": {
                        target: qid_by_layout.get(target)
                        for target in targets
                        if qid_by_layout.get(target)
                    },
                },
            )
        )
    return DomEvidence(tuple(defects), atoms=tuple(atoms))


def _soft_constraint_regression_evidence(
    request: RenderRequest,
    baseline: ConstraintReport,
    candidate: ConstraintReport,
) -> DomEvidence:
    """Represent weighted soft-constraint regression as a cheap hard gate."""

    if not has_soft_constraint_regression(baseline, candidate):
        return DomEvidence()
    semantic_by_layout = {
        node.layoutId: node.contentSemanticId
        for node in _walk_layout(request.spec.layout)
        if node.contentSemanticId is not None
    }
    fallback_semantic = next(iter(_semantic_text(request.spec)), request.spec.slideId)
    regressed = sorted(
        (
            violation
            for violation in candidate.violations
            if violation.strength == "soft"
        ),
        key=lambda item: (-item.weighted_cost, item.constraint_id),
    )
    baseline_by_id: dict[str, list[Any]] = {}
    candidate_by_id: dict[str, list[Any]] = {}
    for violation in baseline.violations:
        if violation.strength == "soft":
            baseline_by_id.setdefault(violation.constraint_id, []).append(violation)
    for violation in candidate.violations:
        if violation.strength == "soft":
            candidate_by_id.setdefault(violation.constraint_id, []).append(violation)
    deltas: list[dict[str, Any]] = []
    regressed_ids: list[str] = []
    for constraint_id in sorted(set(baseline_by_id) | set(candidate_by_id)):
        before_items = baseline_by_id.get(constraint_id, [])
        after_items = candidate_by_id.get(constraint_id, [])
        before_magnitude = sum(item.magnitude for item in before_items)
        after_magnitude = sum(item.magnitude for item in after_items)
        before_cost = sum(item.weighted_cost for item in before_items)
        after_cost = sum(item.weighted_cost for item in after_items)
        if abs(after_cost - before_cost) <= 1e-6:
            continue
        representative = next(iter(after_items or before_items))
        if after_cost > before_cost + 1e-6:
            regressed_ids.append(constraint_id)
        deltas.append(
            {
                "constraint_id": constraint_id,
                "kind": representative.kind,
                "targets": list(representative.targets),
                "before_magnitude": before_magnitude,
                "after_magnitude": after_magnitude,
                "before_weighted_cost": before_cost,
                "after_weighted_cost": after_cost,
                "weighted_cost_delta": after_cost - before_cost,
                "before_details": [item.detail for item in before_items],
                "after_details": [item.detail for item in after_items],
            }
        )
    semantic_ids = tuple(
        dict.fromkeys(
            semantic_by_layout[target]
            for violation in regressed
            for target in violation.targets
            if target in semantic_by_layout
        )
    )
    return DomEvidence(
        (
            DomDefect(
                "constraint.soft_regression",
                "high",
                semantic_ids[0] if semantic_ids else fallback_semantic,
                tuple(item.constraint_id for item in regressed[:8]),
                diagnostics={
                    "soft_constraint_regression": {
                        "baseline_soft_cost": baseline.soft_cost,
                        "candidate_soft_cost": candidate.soft_cost,
                        "soft_cost_delta": candidate.soft_cost - baseline.soft_cost,
                        "regressed_constraint_ids": regressed_ids,
                        "constraint_deltas": deltas,
                    }
                },
            ),
        )
    )

