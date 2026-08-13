"""Exact before/after evidence for selected SlideSpec repair targets."""

from __future__ import annotations


from models.slidespec_v1 import LayoutNode, RenderRequest, RepairPlan
from services.slidespec_acceptance import TargetIssueDelta, TargetIssueIdentity
from services.slidespec_authored_executor import _walk_layout
from services.slidespec_repair_pipeline import DomEvidence
from utils.slidespec_constraints import ConstraintReport

def target_issue_evidence(
    *,
    request: RenderRequest,
    plan: RepairPlan,
    baseline_dom: DomEvidence,
    candidate_dom: DomEvidence,
    baseline_constraints: ConstraintReport,
    candidate_constraints: ConstraintReport,
    qid_to_semantic,
) -> tuple[tuple[TargetIssueIdentity, ...], tuple[TargetIssueDelta, ...] | None]:
    """Build exact before/after evidence for only the selected plan's targets."""

    layout_by_id = {node.layoutId: node for node in _walk_layout(request.spec.layout)}
    parent_by_id: dict[str, str] = {}

    def record_parents(node: LayoutNode) -> None:
        for child in node.children:
            parent_by_id[child.layoutId] = node.layoutId
            record_parents(child)

    record_parents(request.spec.layout)
    semantic_by_layout = {
        node.layoutId: node.contentSemanticId
        for node in layout_by_id.values()
        if node.contentSemanticId is not None
    }
    layout_by_semantic = {
        semantic_id: layout_id for layout_id, semantic_id in semantic_by_layout.items()
    }
    semantic_parent = {
        node.semanticId: parent
        for node in request.spec.content
        if (parent := getattr(node, "parentSemanticId", None)) is not None
    }
    qid_by_semantic = qid_to_semantic(request)
    qid_by_semantic = {semantic: qid for qid, semantic in qid_by_semantic.items()}
    scope = set(plan.scope)
    references = set(plan.diagnostic_refs)
    required: list[TargetIssueIdentity] = []
    deltas: list[TargetIssueDelta] = []
    severity = {"medium": 1.0, "high": 2.0}

    def layout_is_in_scope(
        layout_id: str | None, semantic_id: str | None = None
    ) -> bool:
        """Treat layout/content descendants of an owner as exact target scope."""

        current = layout_id or ""
        visited: set[str] = set()
        while current and current not in visited:
            if current in scope:
                return True
            visited.add(current)
            current = parent_by_id.get(current, "")
        current_semantic = semantic_id or semantic_by_layout.get(layout_id or "") or ""
        visited_semantics: set[str] = set()
        while current_semantic and current_semantic not in visited_semantics:
            owner_layout = layout_by_semantic.get(current_semantic)
            if owner_layout in scope:
                return True
            visited_semantics.add(current_semantic)
            current_semantic = semantic_parent.get(current_semantic, "")
        return False

    candidate_defects = {
        (item.code, item.semantic_id, item.qid): item
        for item in candidate_dom.defects
    }
    for defect in baseline_dom.defects:
        layout_id = next(
            (
                item
                for item, semantic in semantic_by_layout.items()
                if semantic == defect.semantic_id
            ),
            None,
        )
        targeted = layout_is_in_scope(layout_id, defect.semantic_id) or (
            f"{defect.code}:{defect.semantic_id}" in references
        ) or (
            f"code:{defect.code}" in references
            and f"semantic:{defect.semantic_id}" in references
        )
        if not targeted:
            continue
        qid = defect.qid or qid_by_semantic.get(defect.semantic_id)
        if qid is None:
            identity = TargetIssueIdentity(
                "dom",
                defect.code,
                defect.semantic_id,
                "unavailable",
                f"dom:{defect.code}:unavailable",
            )
            required.append(identity)
            deltas.append(TargetIssueDelta(identity, before=None, after=None))
            continue
        related_qids = tuple(
            value
            for semantic_id in defect.related_semantic_ids
            if (value := qid_by_semantic.get(semantic_id)) is not None
        )
        stable_qids = tuple(sorted(dict.fromkeys((qid, *related_qids))))
        identity = TargetIssueIdentity(
            "dom",
            defect.code,
            defect.semantic_id,
            qid,
            f"dom:{defect.code}:{':'.join(stable_qids)}",
        )
        after_defect = candidate_defects.get(
            (defect.code, defect.semantic_id, qid)
        )
        identity_drift = any(
            code == defect.code
            and semantic_id == defect.semantic_id
            and candidate_qid != qid
            for code, semantic_id, candidate_qid in candidate_defects
        )
        required.append(identity)
        deltas.append(
            TargetIssueDelta(
                identity,
                before=severity.get(defect.severity),
                after=(
                    severity.get(after_defect.severity, 0.0)
                    if after_defect is not None
                    else None
                    if identity_drift
                    else 0.0
                ),
            )
        )

    candidate_violations = {
        (item.constraint_id, item.code): item
        for item in candidate_constraints.violations
        if item.strength == "hard"
    }
    for violation in baseline_constraints.violations:
        if violation.strength != "hard":
            continue
        targeted_layout = next(
            (item for item in violation.targets if layout_is_in_scope(item)),
            None,
        )
        if targeted_layout is None and f"constraint:{violation.constraint_id}" not in references:
            continue
        targeted_layout = targeted_layout or next(
            (item for item in violation.targets if item in semantic_by_layout),
            None,
        )
        semantic_id = semantic_by_layout.get(targeted_layout or "")
        qid = qid_by_semantic.get(semantic_id or "")
        identity = TargetIssueIdentity(
            "invariant",
            violation.code,
            semantic_id or "unavailable",
            qid or "unavailable",
            violation.constraint_id,
        )
        after_violation = candidate_violations.get(
            (violation.constraint_id, violation.code)
        )
        required.append(identity)
        deltas.append(
            TargetIssueDelta(
                identity,
                before=violation.magnitude,
                after=after_violation.magnitude if after_violation is not None else 0.0,
            )
        )

    if required:
        merged: dict[TargetIssueIdentity, TargetIssueDelta] = {}
        for delta in deltas:
            current = merged.get(delta.identity)
            if current is None:
                merged[delta.identity] = delta
                continue

            def conservative_max(
                left: float | None, right: float | None
            ) -> float | None:
                return None if left is None or right is None else max(left, right)

            merged[delta.identity] = TargetIssueDelta(
                delta.identity,
                before=conservative_max(current.before, delta.before),
                after=conservative_max(current.after, delta.after),
                resolution_threshold=max(
                    current.resolution_threshold, delta.resolution_threshold
                ),
                tolerance=min(current.tolerance, delta.tolerance),
                direction=delta.direction,
            )
        return tuple(merged), tuple(merged.values())

    if plan.diagnostic_refs:
        identity = TargetIssueIdentity(
            "dom",
            "acceptance.target_issue_delta_unavailable",
            next(iter(request.spec.content)).semanticId,
            "unavailable",
            "dom:acceptance.target_issue_delta_unavailable:unavailable",
        )
        return (identity,), (TargetIssueDelta(identity, before=None, after=None),)
    return (), None
