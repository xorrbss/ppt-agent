"""Production adapter from authored quality review to the canonical SlideSpec path.

Only slides with a verified active artifact pointer are claimed.  The authored
review orchestrator first runs the lossless importer for eligible slides; anything
that fails that boundary remains a safe mixed-deck legacy owner.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import json
import logging
import math
import os
import re
import time
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.slidespec_v1 import (
    AcceptanceDecision,
    LayoutConstraints,
    LayoutNode,
    RenderRequest,
    RepairPlan,
    SlideSpec,
)
from models.sql.slide import SlideModel
from models.sql.slidespec_artifact_bundle import SlideSpecArtifactBundle
from services import presentation_version_service as version_service
from services.authored_quality_acceptance import (
    local_acceptance_decision,
    style_comparison_improvement_decision,
    style_comparison_quality_gain,
    style_comparison_quality_interval,
    style_comparison_has_regression,
)
from services.slidespec_acceptance import (
    AcceptanceEvidence,
    GateEvidence,
    TargetIssueDelta,
    TargetIssueIdentity,
    decide_acceptance,
)
from services.slidespec_artifact_store import (
    ACTIVE_POINTER_KEY,
    SlideSpecArtifactError,
    active_pointer,
    build_slidespec_artifact_bundle,
    load_active_slidespec_bundle,
    publish_slidespec_bundle_cas,
    record_publication_attempt,
    rollback_active_slide_cas,
)
from services.slidespec_repair import (
    CASCADE_POLICY_VERSION,
    build_deterministic_cascade_report,
    realized_repair_cost,
)
from services.slidespec_pipeline_performance import (
    BoundedSlideScheduler,
    IncrementalInspectionCache,
    PIPELINE_CAPABILITY_VERSION,
    PIPELINE_PERFORMANCE_WINDOW,
    adjacent_influence_digests,
    digest_parts,
    inspection_cache_digest,
)
from services.slidespec_repair_pipeline import (
    DomDefect,
    DomEvidence,
    EvidenceAtom,
    PublicationReceipt,
    RenderedCandidate,
    SensorResult,
    SlideSpecInput,
    SlideSpecRepairDependencies,
    SlideSpecRepairPipeline,
    merge_evidence_atoms,
)
from services.slidespec_rollout_policy import resolve_slidespec_repair_rollout
from utils.authored_dom_quality import (
    DomQualityReport,
    has_dom_quality_regression,
    inspect_authored_dom,
    normalize_browser_metrics,
)
from utils.authored_ocr_quality import (
    OcrQualityResult,
    compare_expected_candidate,
    compare_expected_candidate_order_insensitive,
    inspect_authored_ocr,
    normalize_ocr_text,
)
from utils.llm_calls.author_vision_qa import (
    critique_authored_style_comparison,
    style_comparison_failure_diagnostic,
)
from utils.llm_calls.propose_slidespec_repair import (
    PROMPT_VERSION as PROPOSAL_PROMPT_VERSION,
    propose_slidespec_repair,
)
from utils.llm_provider import get_model
from utils.slide_capture import (
    DOM_METRICS_VERSION,
    RENDER_CACHE_VERSION,
    chrome_runtime_digest,
    get_render_runtime_counters,
    render_html_to_png_with_metrics,
)
from utils.slidespec_constraints import (
    ConstraintReport,
    constraint_targets,
    evaluate_constraints,
    has_soft_constraint_regression,
    imported_projection_roundtrip_errors,
    observations_from_browser_metrics,
)
from utils.slidespec_core import SlideSpecError, apply_repair_plan, render_slidespec, validate_slidespec
from utils.slidespec_solver import (
    FeasibilityResult,
    build_constraint_repair_plan,
    solve_constraints,
)


LOGGER = logging.getLogger(__name__)

_DOM_CODE = {
    "clipping": "dom.clipping",
    "off_canvas": "dom.off_canvas",
    "overflow": "dom.overflow",
    "overlap": "dom.unintended_overlap",
}

_DEFECT_PRIORITY = {
    ("high", "dom.clipping"): 0,
    ("high", "dom.overflow"): 1,
    ("high", "dom.unintended_overlap"): 2,
    ("high", "dom.off_canvas"): 3,
    ("medium", "dom.unintended_overlap"): 4,
    ("medium", "dom.clipping"): 5,
    ("medium", "dom.overflow"): 6,
    ("medium", "dom.off_canvas"): 7,
    ("high", "constraint.containment.violated"): 0,
    ("high", "constraint.readability.violated"): 1,
}
_TEXT_KINDS = frozenset({"title", "text", "list", "metric", "table"})


@dataclass(frozen=True, slots=True)
class CanonicalReviewExecution:
    claimed_positions: tuple[int, ...]
    data: dict[str, Any]


@dataclass(slots=True)
class _CandidateState:
    request: RenderRequest
    image: bytes
    dom: DomQualityReport
    metrics: Mapping[str, Any]
    plan: RepairPlan
    constraints: ConstraintReport
    observations: Mapping[str, Any]
    decision: AcceptanceDecision | None = None
    ocr_non_regression: bool = False
    token_recall: float = 0.0
    vqa_non_regression: bool = False
    vqa_improved: bool = False
    required_target_issues: tuple[TargetIssueIdentity, ...] = ()
    target_issue_deltas: tuple[TargetIssueDelta, ...] | None = None


@dataclass(frozen=True, slots=True)
class _BaselineInspection:
    png: bytes
    metrics: Mapping[str, Any]
    report: DomQualityReport
    qids: Mapping[str, str]
    observations: Mapping[str, Any]
    constraints: ConstraintReport
    solver_result: FeasibilityResult
    solver_elapsed_ms: float


_BASELINE_INSPECTION_CACHE: IncrementalInspectionCache[_BaselineInspection] = (
    IncrementalInspectionCache(max_entries=256)
)


def _render_request(bundle: Any) -> RenderRequest:
    manifest = bundle.manifest if isinstance(bundle.manifest, Mapping) else {}
    return RenderRequest.model_validate(
        {
            "spec": bundle.spec,
            "renderer": manifest.get("renderer"),
            "fonts": manifest.get("fonts"),
            "assets": manifest.get("assets"),
            "source": manifest.get("source"),
        }
    )


def _semantic_text(spec: SlideSpec) -> dict[str, str]:
    return {
        node.semanticId: node.text
        for node in sorted(spec.content, key=lambda item: item.readingOrder)
        if node.text
    }


def _expected_text(spec: SlideSpec) -> str:
    return "\n".join(_semantic_text(spec).values())


def _paired_vqa_context(
    request: RenderRequest,
    plan: RepairPlan,
    *,
    verified_geometry: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Describe the bounded repair intent without copying authored text."""

    codes = sorted(
        {
            ref.removeprefix("code:")
            for ref in plan.diagnostic_refs
            if ref.startswith("code:")
        }
    )
    qids = sorted(
        {
            ref.removeprefix("qid:")
            for ref in plan.diagnostic_refs
            if ref.startswith("qid:")
        }
    )
    qid_by_layout = {
        binding.layoutId: binding.qid
        for binding in (request.source.bindings if request.source is not None else ())
    }
    payload: dict[str, Any] = {
        "schema": "slidespec-paired-vqa-context/1",
        "targetDefects": [
            {
                "category": code.removeprefix("dom."),
                "qid": qid,
            }
            for code in codes
            for qid in qids
        ],
        "operations": [
            {
                "operation": operation.op,
                "targetLayoutId": operation.target,
                "targetQid": qid_by_layout.get(operation.target),
            }
            for operation in plan.operations
        ],
        "verifiedGeometry": dict(verified_geometry or {}),
    }
    context = (
        "Evaluate whether the POST-FIX candidate visibly resolves the listed "
        "target defects without regressing protected dimensions. The identifiers "
        "are opaque and contain no authored text. Chrome geometry facts are "
        "authoritative for clipping, canvas containment, and glyph clipping; do "
        "not claim those defects when the supplied facts prove them false. Still "
        "reject real spacing, hierarchy, readability, or style regressions.\n"
        + json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    )
    return context, payload


def _important_tokens(spec: SlideSpec) -> tuple[str, ...]:
    values = [str(token.value) for token in spec.tokens if token.important]
    by_name = {token.name: str(token.value) for token in spec.tokens}
    for node in spec.content:
        values.extend(by_name[name] for name in node.importantTokens if name in by_name)
    return tuple(dict.fromkeys(value for value in values if value))


def _walk_layout(node: LayoutNode) -> Sequence[LayoutNode]:
    result = [node]
    for child in node.children:
        result.extend(_walk_layout(child))
    return result


def _qid_to_semantic(request: RenderRequest) -> dict[str, str]:
    rendered = render_slidespec(request)
    return {entry.qid: entry.semanticId for entry in rendered.semanticDom}


def _dom_evidence(
    report: DomQualityReport,
    qids: Mapping[str, str],
    *,
    diagnostic_refs: Sequence[str] | None = None,
    browser_metrics: Mapping[str, Any] | None = None,
) -> DomEvidence:
    allowed = frozenset(diagnostic_refs) if diagnostic_refs is not None else None
    normalized_metrics = normalize_browser_metrics(browser_metrics)
    metric_by_qid = {
        str(item.get("qid")): item for item in normalized_metrics.get("elements", ())
    }

    required_qids = frozenset(
        ref.removeprefix("qid:")
        for ref in (allowed or ())
        if ref.startswith("qid:")
    )

    def qid_is_in_required_subtree(qid: str) -> bool:
        """Include browser defects owned by a selected ancestor operation.

        Geometry plans can move an imported wrapper while the browser reports the
        actual clipping/overflow on one of its text descendants.  Filtering only
        by the wrapper qid would silently erase that required evidence.  Chrome's
        measured parent chain is the exact ownership proof used here.
        """

        current = qid
        visited: set[str] = set()
        while current and current not in visited:
            if current in required_qids:
                return True
            visited.add(current)
            item = metric_by_qid.get(current)
            current = str(item.get("parent_qid", "")) if item is not None else ""
        return False

    def related_by_ancestry(first: str, second: str) -> bool:
        for start, target in ((first, second), (second, first)):
            current = start
            visited: set[str] = set()
            while current and current not in visited:
                if current == target:
                    return True
                visited.add(current)
                item = metric_by_qid.get(current)
                current = str(item.get("parent_qid", "")) if item is not None else ""
        return False

    def proven_overlap_target(issue_qids: Sequence[str]) -> str | None:
        for qid in issue_qids:
            item = metric_by_qid.get(qid)
            if item is None:
                continue
            raw = item.get("occlusion_occluder_qids", ())
            occluders = (
                tuple(str(value) for value in raw)
                if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes))
                else ()
            )
            if any(
                related_by_ancestry(occluder, other)
                for occluder in occluders
                for other in issue_qids
                if other != qid
            ):
                return qid
        return None

    # One browser issue can be repeated through nested DOM nodes.  Keep one user
    # defect per physical overlap, while retaining each raw target observation as
    # a separate evidence atom for audit/replay.
    ranked = {"medium": 1, "high": 2}
    defects_by_key: dict[tuple[str, str, str], DomDefect] = {}
    overlap_clusters: list[dict[str, Any]] = []
    raw_atoms: list[EvidenceAtom] = []

    def overlap_occluders(target_qid: str, issue_qids: Sequence[str]) -> tuple[str, ...]:
        metric = metric_by_qid.get(target_qid, {})
        raw = metric.get("occlusion_occluder_qids", ())
        measured = (
            tuple(str(value) for value in raw)
            if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes))
            else ()
        )
        return tuple(
            dict.fromkeys(
                (*measured, *(qid for qid in issue_qids if qid != target_qid))
            )
        )

    def same_physical_overlap(
        target_qid: str,
        occluder_qids: Sequence[str],
        cluster: Mapping[str, Any],
    ) -> bool:
        targets = tuple(str(value) for value in cluster["target_qids"])
        occluders = tuple(str(value) for value in cluster["occluder_qids"])
        target_related = any(
            related_by_ancestry(target_qid, other) for other in targets
        )
        occluder_related = any(
            related_by_ancestry(first, second)
            for first in occluder_qids
            for second in occluders
        )
        # A single painted overlay can cover sibling text fragments as well as
        # nested descendants.  The shared/ancestrally-related occluder is the
        # physical identity proof; target ancestry is the fallback when an older
        # sensor omitted occluder qids.
        return occluder_related if occluder_qids and occluders else target_related

    def metric_area(qid: str) -> float:
        bbox = metric_by_qid.get(qid, {}).get("bbox", {})
        if not isinstance(bbox, Mapping):
            return 0.0
        try:
            return float(bbox.get("width", 0)) * float(bbox.get("height", 0))
        except (TypeError, ValueError):
            return 0.0

    for issue in report.issues:
        code = _DOM_CODE.get(issue.category)
        if code is None or issue.severity not in {"high", "medium"}:
            continue
        issue_qids = tuple(qid for qid in issue.qids if qid in qids)
        if allowed is not None:
            if required_qids:
                issue_qids = tuple(
                    qid for qid in issue_qids if qid_is_in_required_subtree(qid)
                )
        if code == "dom.unintended_overlap" and len(issue_qids) > 1:
            proven_qid = proven_overlap_target(issue_qids)
            if proven_qid is not None:
                issue_qids = (proven_qid,) + tuple(
                    qid for qid in issue_qids if qid != proven_qid
                )
        semantic_ids = tuple(dict.fromkeys(qids[qid] for qid in issue_qids))
        if allowed is not None and not required_qids:
            structured_code = f"code:{code}" in allowed
            semantic_ids = tuple(
                semantic_id
                for semantic_id in semantic_ids
                if f"{code}:{semantic_id}" in allowed
                or (structured_code and f"semantic:{semantic_id}" in allowed)
            )
        if code == "dom.unintended_overlap" and semantic_ids:
            semantic_id, *related = semantic_ids
            qid = next(
                (value for value in issue_qids if qids.get(value) == semantic_id),
                "",
            )
            occluders = overlap_occluders(qid, issue_qids)
            raw_finding = {
                "category": issue.category,
                "severity": issue.severity,
                "qids": list(issue_qids),
                "target_qid": qid,
                "occluder_qids": list(occluders),
            }
            raw_atoms.append(
                EvidenceAtom(
                    sensor="chrome_dom",
                    metric="occlusion",
                    state="proven_issue",
                    qid=qid or None,
                    semantic_id=semantic_id,
                    dependency_refs=tuple(
                        dict.fromkeys(
                            (
                                semantic_id,
                                f"semantic:{semantic_id}",
                                *(value for value in occluders),
                                *(f"qid:{value}" for value in occluders),
                            )
                        )
                    ),
                    diagnostics={"raw_finding": raw_finding},
                )
            )
            cluster = next(
                (
                    item
                    for item in overlap_clusters
                    if same_physical_overlap(qid, occluders, item)
                ),
                None,
            )
            if cluster is None:
                overlap_clusters.append(
                    {
                        "target_qids": [qid],
                        "occluder_qids": list(occluders),
                        "semantic_ids": [semantic_id, *related],
                        "severity": issue.severity,
                        "representative_qid": qid,
                        "raw_findings": [raw_finding],
                    }
                )
            else:
                cluster["target_qids"] = list(
                    dict.fromkeys((*cluster["target_qids"], qid))
                )
                cluster["occluder_qids"] = list(
                    dict.fromkeys((*cluster["occluder_qids"], *occluders))
                )
                cluster["semantic_ids"] = list(
                    dict.fromkeys((*cluster["semantic_ids"], semantic_id, *related))
                )
                cluster["raw_findings"].append(raw_finding)
                if ranked[issue.severity] > ranked[str(cluster["severity"])]:
                    cluster["severity"] = issue.severity
                current_qid = str(cluster["representative_qid"])
                if metric_area(qid) > metric_area(current_qid):
                    cluster["representative_qid"] = qid
            continue
        for semantic_id in semantic_ids:
            qid = next(
                (value for value in issue_qids if qids.get(value) == semantic_id),
                "",
            )
            key = (code, semantic_id, qid)
            current = defects_by_key.get(key)
            if current is None or ranked[issue.severity] > ranked[current.severity]:
                defects_by_key[key] = DomDefect(
                    code, issue.severity, semantic_id, qid=qid or None
                )
    overlap_defects: list[DomDefect] = []
    for cluster in overlap_clusters:
        representative_qid = str(cluster["representative_qid"])
        semantic_ids = tuple(str(value) for value in cluster["semantic_ids"])
        semantic_id = qids.get(
            representative_qid,
            semantic_ids[0] if semantic_ids else representative_qid,
        )
        related = tuple(value for value in semantic_ids if value != semantic_id)
        physical_issue_id = "dom.unintended_overlap:" + ":".join(
            (
                min(str(value) for value in cluster["target_qids"]),
                min(
                    (str(value) for value in cluster["occluder_qids"]),
                    default="unavailable",
                ),
            )
        )
        overlap_defects.append(
            DomDefect(
                "dom.unintended_overlap",
                str(cluster["severity"]),
                semantic_id,
                related,
                representative_qid,
                diagnostics={
                    "physical_overlap": {
                        "physical_issue_id": physical_issue_id,
                        "target_qids": list(cluster["target_qids"]),
                        "occluder_qids": list(cluster["occluder_qids"]),
                        "raw_findings": list(cluster["raw_findings"]),
                    }
                },
                physical_issue_id=physical_issue_id,
            )
        )

    measurement_atoms: list[EvidenceAtom] = []
    raw_issue_qids = {
        atom.qid for atom in raw_atoms if atom.qid is not None
    }
    # ``browser_metrics=None`` means that the caller only supplied the legacy
    # quality report.  The report's own telemetry status remains authoritative;
    # absence of an optional, richer metric envelope is not evidence that every
    # mapped element is missing.  When the envelope is present, however, missing
    # or unsampled elements become explicit scoped unknown atoms.
    if browser_metrics is not None:
        for qid, semantic_id in sorted(qids.items()):
            metric = metric_by_qid.get(qid)
            if metric is None:
                measurement_atoms.append(
                    EvidenceAtom(
                        sensor="chrome_dom",
                        metric="element_presence",
                        state="unknown",
                        qid=qid,
                        semantic_id=semantic_id,
                        dependency_refs=(semantic_id, f"semantic:{semantic_id}"),
                        reason_code="dom.telemetry.element_missing",
                    )
                )
                continue
            occluded = metric.get("occluded_fraction")
            continuous = metric.get("continuous_occluded_fraction")
            if occluded is None and continuous is None:
                reason = str(
                    metric.get("occlusion_unknown_reason")
                    or metric.get("occlusion_reason")
                    or "dom.occlusion.measurement_unavailable"
                )
                measurement_atoms.append(
                    EvidenceAtom(
                        sensor="chrome_occlusion",
                        metric="occlusion",
                        state="unknown",
                        qid=qid,
                        semantic_id=semantic_id,
                        dependency_refs=(semantic_id, f"semantic:{semantic_id}"),
                        reason_code=reason,
                        diagnostics={
                            "sampled": metric.get("occlusion_sample_count"),
                            "unknown_fraction": metric.get(
                                "occlusion_unknown_fraction"
                            ),
                        },
                    )
                )
            else:
                measurement_atoms.append(
                    EvidenceAtom(
                        sensor="chrome_occlusion",
                        metric="occlusion",
                        state=(
                            "proven_issue"
                            if qid in raw_issue_qids
                            else "proven_clean"
                        ),
                        qid=qid,
                        semantic_id=semantic_id,
                        dependency_refs=(semantic_id, f"semantic:{semantic_id}"),
                    )
                )
    atoms = merge_evidence_atoms(raw_atoms, measurement_atoms)
    if report.telemetry_status != "available" and not any(
        atom.state == "unknown" for atom in atoms
    ):
        atoms = merge_evidence_atoms(
            atoms,
            (
                EvidenceAtom(
                    sensor="chrome_dom",
                    metric="element_inventory",
                    state="unknown",
                    reason_code=f"dom.telemetry.{report.telemetry_status}",
                    global_scope=True,
                ),
            ),
        )
    return DomEvidence(
        defects=tuple((*defects_by_key.values(), *overlap_defects)),
        atoms=atoms,
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


def _merge_dom_evidence(*items: DomEvidence) -> DomEvidence:
    defects: dict[tuple[object, ...], DomDefect] = {}
    severity_rank = {"low": 0, "medium": 1, "high": 2}
    for item in items:
        for defect in item.defects:
            key = (
                ("physical", defect.physical_issue_id)
                if defect.physical_issue_id
                else (
                    defect.code,
                    defect.semantic_id,
                    defect.qid,
                    defect.related_semantic_ids,
                )
            )
            current = defects.get(key)
            if current is None or severity_rank[defect.severity] > severity_rank[
                current.severity
            ]:
                defects[key] = defect
    physical_by_qid: dict[str, str] = {}
    physical_by_semantic: dict[str, str] = {}
    for defect in defects.values():
        if not defect.physical_issue_id:
            continue
        physical_by_semantic[defect.semantic_id] = defect.physical_issue_id
        physical_by_semantic.update(
            {
                semantic_id: defect.physical_issue_id
                for semantic_id in defect.related_semantic_ids
            }
        )
        physical = defect.diagnostics.get("physical_overlap", {})
        if isinstance(physical, Mapping):
            for qid in physical.get("target_qids", ()):
                physical_by_qid[str(qid)] = defect.physical_issue_id
    aliased_defects = []
    for defect in defects.values():
        physical_issue_id = defect.physical_issue_id
        if defect.code == "constraint.readability.violated":
            physical_issue_id = (
                physical_issue_id
                or physical_by_qid.get(defect.qid or "")
                or physical_by_semantic.get(defect.semantic_id)
            )
        aliased_defects.append(
            replace(defect, physical_issue_id=physical_issue_id)
            if physical_issue_id != defect.physical_issue_id
            else defect
        )
    return DomEvidence(
        defects=tuple(aliased_defects),
        atoms=merge_evidence_atoms(*(item.atoms for item in items)),
    )


def _constraint_report_payload(report: ConstraintReport) -> dict[str, Any]:
    return {
        **asdict(report),
        "hard_failure": report.hard_failure,
        "soft_cost": report.soft_cost,
    }


def _off_canvas_fraction(
    metric: Mapping[str, Any], *, canvas_width: float, canvas_height: float
) -> float:
    bbox = metric.get("bbox")
    bbox = bbox if isinstance(bbox, Mapping) else {}

    def number(value: object) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return 0.0
        return result if math.isfinite(result) else 0.0

    x, y = number(bbox.get("x")), number(bbox.get("y"))
    width, height = number(bbox.get("width")), number(bbox.get("height"))
    return max(
        max(0.0, -x, x + width - canvas_width) / max(1.0, canvas_width),
        max(0.0, -y, y + height - canvas_height) / max(1.0, canvas_height),
    )


def _repair_context(
    request: RenderRequest | SlideSpec,
    evidence: DomEvidence,
    browser_metrics: Mapping[str, Any] | None = None,
    constraint_report: ConstraintReport | None = None,
) -> dict[str, Any]:
    spec = request.spec if isinstance(request, RenderRequest) else request
    source = request.source if isinstance(request, RenderRequest) else None
    layout_by_semantic = {
        node.contentSemanticId: node
        for node in _walk_layout(spec.layout)
        if node.contentSemanticId is not None
    }
    content_by_semantic = {node.semanticId: node for node in spec.content}
    binding_by_semantic = {
        binding.semanticId: binding for binding in (source.bindings if source else ())
    }
    layout_by_id = {node.layoutId: node for node in _walk_layout(spec.layout)}
    parent_by_id: dict[str, str | None] = {spec.layout.layoutId: None}
    stack = [spec.layout]
    while stack:
        parent = stack.pop()
        for child in parent.children:
            parent_by_id[child.layoutId] = parent.layoutId
            stack.append(child)
    normalized_metrics = normalize_browser_metrics(browser_metrics)
    metric_by_qid = {
        str(item.get("qid")): item for item in normalized_metrics.get("elements", ())
    }
    native_qid_by_semantic: dict[str, str] = {}
    if source is None and isinstance(request, RenderRequest):
        native_qid_by_semantic = {
            entry.semanticId: entry.qid
            for entry in render_slidespec(request).semanticDom
        }
    semantic_by_qid = {
        binding.qid: binding.semanticId for binding in (source.bindings if source else ())
    }
    semantic_by_qid.update(
        {qid: semantic_id for semantic_id, qid in native_qid_by_semantic.items()}
    )
    def qid_for_semantic(semantic_id: str) -> str | None:
        imported = binding_by_semantic.get(semantic_id)
        return (
            imported.qid
            if imported is not None
            else native_qid_by_semantic.get(semantic_id)
        )

    def metric_for_semantic(semantic_id: str) -> Mapping[str, Any] | None:
        imported = binding_by_semantic.get(semantic_id)
        qid = imported.qid if imported is not None else native_qid_by_semantic.get(semantic_id)
        metric = metric_by_qid.get(qid) if qid else None
        return metric if isinstance(metric, Mapping) else None

    def finite_number(value: object, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    def envelope_can_change(raw: object) -> bool:
        if not isinstance(raw, Mapping) or raw.get("locked") is True:
            return False
        allowed = raw.get("allowedOperations")
        if allowed is None:
            return True
        if not isinstance(allowed, Sequence) or isinstance(allowed, (str, bytes)):
            return False
        return bool(
            {
                "translateWithinParent",
                "setBounds",
                "resizeWithinBounds",
                "setFontScale",
            }.intersection(str(value) for value in allowed)
        )

    def metric_qids_related(first: str, second: str) -> bool:
        for start, target in ((first, second), (second, first)):
            current = start
            visited: set[str] = set()
            while current and current not in visited:
                if current == target:
                    return True
                visited.add(current)
                item = metric_by_qid.get(current)
                current = str(item.get("parent_qid", "")) if item is not None else ""
        return False

    def metric_qid_is_ancestor(ancestor: str, descendant: str) -> bool:
        current = descendant
        visited: set[str] = set()
        while current and current not in visited:
            if current == ancestor:
                return True
            visited.add(current)
            item = metric_by_qid.get(current)
            current = str(item.get("parent_qid", "")) if item is not None else ""
        return False

    def bound_semantic_for_metric_qid(qid: str) -> tuple[str, str] | None:
        current = qid
        visited: set[str] = set()
        while current and current not in visited:
            semantic_id = semantic_by_qid.get(current)
            if semantic_id is not None:
                return semantic_id, current
            visited.add(current)
            item = metric_by_qid.get(current)
            current = str(item.get("parent_qid", "")) if item is not None else ""
        return None

    def bound_ancestor_candidates(
        *,
        target_qid: str,
        blocked_qid: str,
        candidate_deltas_px: Sequence[tuple[float, float]],
        physical_target_qids: Sequence[str] = (),
        physical_blocker_qids: Sequence[str] = (),
    ) -> tuple[dict[str, Any], ...]:
        """Return browser-proven imported ancestors in nearest-first order."""

        if source is None:
            return ()
        width = max(1.0, float(source.viewportWidth))
        height = max(1.0, float(source.viewportHeight))
        current_metric = metric_by_qid.get(target_qid)
        if current_metric is None or current_metric.get(
            "occlusion_probe_version"
        ) not in {"owned-content-v2", "owned-paint-v3"}:
            return ()
        current_qid = (
            str(current_metric.get("parent_qid", ""))
            if current_metric is not None
            else ""
        )
        parent_chain = [target_qid]
        visited: set[str] = set()
        result: list[dict[str, Any]] = []
        depth = 1
        while current_qid and current_qid not in visited:
            visited.add(current_qid)
            parent_chain.append(current_qid)
            semantic_id = semantic_by_qid.get(current_qid)
            node = layout_by_semantic.get(semantic_id) if semantic_id else None
            if (
                semantic_id is not None
                and node is not None
                and node.bounds is not None
                and not metric_qid_is_ancestor(current_qid, blocked_qid)
            ):
                envelope = (
                    node.constraints.model_dump(mode="json")
                    if node.constraints is not None
                    else {}
                )
                deltas: list[dict[str, Any]] = []
                proved_deltas: list[tuple[float, float, Mapping[str, Any] | None]] = [
                    (dx_px, dy_px, None) for dx_px, dy_px in candidate_deltas_px
                ]
                physical_owner_failure: dict[str, Any] | None = None
                owner_metric = metric_by_qid.get(current_qid)
                owner_bbox = (
                    owner_metric.get("bbox")
                    if isinstance(owner_metric, Mapping)
                    else None
                )
                owned_targets = tuple(
                    dict.fromkeys(str(qid) for qid in physical_target_qids if qid)
                )
                blocker_metrics = tuple(
                    (
                        str(qid),
                        metric_by_qid.get(str(qid), {}).get("bbox"),
                    )
                    for qid in physical_blocker_qids
                    if isinstance(metric_by_qid.get(str(qid)), Mapping)
                )
                blocker_metrics = tuple(
                    (qid, bbox)
                    for qid, bbox in blocker_metrics
                    if isinstance(bbox, Mapping)
                )
                common_physical_owner = (
                    isinstance(owner_bbox, Mapping)
                    and owned_targets
                    and blocker_metrics
                    and all(
                        metric_qid_is_ancestor(current_qid, qid)
                        for qid in owned_targets
                    )
                )
                if common_physical_owner:
                    # Leaf-separation deltas do not prove that moving this common
                    # owner clears every raw finding in the physical cluster.
                    proved_deltas = []
                    owner_x = finite_number(owner_bbox.get("x"))
                    owner_y = finite_number(owner_bbox.get("y"))
                    owner_width = finite_number(owner_bbox.get("width"))
                    owner_height = finite_number(owner_bbox.get("height"))
                    blocker_bottom = max(
                        finite_number(bbox.get("y"))
                        + finite_number(bbox.get("height"))
                        for _, bbox in blocker_metrics
                    )
                    downward_px = blocker_bottom - owner_y
                    lower_boundaries = []
                    for qid, metric in metric_by_qid.items():
                        bbox = metric.get("bbox")
                        if (
                            not isinstance(bbox, Mapping)
                            or qid in owned_targets
                            or qid in physical_blocker_qids
                            or metric_qid_is_ancestor(current_qid, qid)
                            or metric_qid_is_ancestor(qid, current_qid)
                        ):
                            continue
                        x = finite_number(bbox.get("x"))
                        y = finite_number(bbox.get("y"))
                        item_width = finite_number(bbox.get("width"))
                        horizontal_overlap = min(
                            owner_x + owner_width, x + item_width
                        ) - max(owner_x, x)
                        if y >= owner_y + owner_height - 1e-6 and horizontal_overlap > 0:
                            lower_boundaries.append((y, qid))
                    boundary_y, boundary_qid = min(
                        lower_boundaries,
                        default=(height, "canvas-bottom"),
                    )
                    candidate_owner = {
                        "x": owner_x,
                        "y": owner_y + downward_px,
                        "width": owner_width,
                        "height": owner_height,
                    }
                    separates = all(
                        candidate_owner["y"]
                        >= finite_number(bbox.get("y"))
                        + finite_number(bbox.get("height"))
                        - 1e-6
                        for _, bbox in blocker_metrics
                    )
                    contained = (
                        downward_px > 1e-6
                        and candidate_owner["y"] + owner_height
                        <= boundary_y + 1e-6
                        and candidate_owner["y"] + owner_height <= height + 1e-6
                    )
                    if separates and contained:
                        proved_deltas.append(
                            (
                                0.0,
                                downward_px,
                                {
                                    "ownerQid": current_qid,
                                    "ownerBoundsPx": dict(owner_bbox),
                                    "candidateOwnerBoundsPx": candidate_owner,
                                    "ownedTargetQids": owned_targets,
                                    "blockerBoundsPx": {
                                        qid: dict(bbox)
                                        for qid, bbox in blocker_metrics
                                    },
                                    "lowerBoundaryQid": boundary_qid,
                                    "lowerBoundaryYPx": boundary_y,
                                    "allTargetsOwned": True,
                                    "candidateSeparatesBlockers": True,
                                    "candidateContained": True,
                                },
                            )
                        )
                    else:
                        physical_owner_failure = {
                            "reasonCode": (
                                "repair.geometry.ancestor."
                                "physical_owner_no_safe_translation"
                            ),
                            "ownerQid": current_qid,
                            "ownedTargetQids": owned_targets,
                            "blockerQids": tuple(qid for qid, _ in blocker_metrics),
                            "requiredDownPx": downward_px,
                            "availableDownPx": max(
                                0.0,
                                boundary_y - owner_y - owner_height,
                            ),
                            "lowerBoundaryQid": boundary_qid,
                            "lowerBoundaryYPx": boundary_y,
                            "candidateSeparatesBlockers": separates,
                            "candidateContained": contained,
                        }
                for dx_px, dy_px, physical_proof in sorted(
                    set(
                        (dx, dy, json.dumps(proof, sort_keys=True) if proof else "")
                        for dx, dy, proof in proved_deltas
                    ),
                    key=lambda value: (
                        abs(value[0]) + abs(value[1]),
                        value[0],
                        value[1],
                    ),
                ):
                    physical_proof = (
                        json.loads(physical_proof) if physical_proof else None
                    )
                    dx, dy = dx_px / width, dy_px / height
                    if abs(dx) <= 1e-12 and abs(dy) <= 1e-12:
                        continue
                    moved_x = node.bounds.x + dx
                    moved_y = node.bounds.y + dy
                    if (
                        moved_x < -1e-12
                        or moved_y < -1e-12
                        or moved_x + node.bounds.width > 1 + 1e-12
                        or moved_y + node.bounds.height > 1 + 1e-12
                    ):
                        continue
                    deltas.append(
                        {
                            "x": dx,
                            "y": dy,
                            "source": "chrome",
                            "telemetryComplete": True,
                            "resolvesIssue": True,
                            "deltaPx": {"x": dx_px, "y": dy_px},
                            **(
                                {"physicalOwnerProof": physical_proof}
                                if physical_proof is not None
                                else {}
                            ),
                        }
                    )
                if deltas or source is not None:
                    result.append(
                        {
                            "qid": current_qid,
                            "semanticId": semantic_id,
                            "layoutId": node.layoutId,
                            "bounds": node.bounds.model_dump(mode="json"),
                            "containerBounds": {
                                "x": 0.0,
                                "y": 0.0,
                                "width": 1.0,
                                "height": 1.0,
                            },
                            "mutationEnvelope": envelope,
                            "candidateDeltas": tuple(deltas),
                            **(
                                {"physicalOwnerFailure": physical_owner_failure}
                                if physical_owner_failure is not None
                                else {}
                            ),
                            "coordinatedOwnerOnly": not bool(deltas),
                            "depth": depth,
                            "sameCoordinateSpace": True,
                            "ownershipProven": True,
                            "browserProof": {
                                "source": "chrome",
                                "telemetryComplete": True,
                                "targetQid": target_qid,
                                "ancestorQid": current_qid,
                                "parentQidChain": tuple(parent_chain),
                            },
                        }
                    )
            item = metric_by_qid.get(current_qid)
            current_qid = str(item.get("parent_qid", "")) if item is not None else ""
            depth += 1
        return tuple(result)

    def coordinated_pair_for_owners(
        *,
        issue_target_qid: str,
        issue_occluder_qids: Sequence[str],
        target_qid: str,
        peer_qid: str,
        target_node: LayoutNode,
        peer_node: LayoutNode,
        target_bbox: Mapping[str, Any],
        peer_bbox: Mapping[str, Any],
        target_depth: int,
        peer_depth: int,
    ) -> tuple[dict[str, Any], ...]:
        """Prove bounded two-owner separation using Chrome descendant capacity."""

        if source is None:
            return ()
        target_binding = bound_semantic_for_metric_qid(target_qid)
        peer_binding = bound_semantic_for_metric_qid(peer_qid)
        if target_binding is None or peer_binding is None:
            return ()
        target_metric = metric_by_qid.get(target_qid)
        peer_metric = metric_by_qid.get(peer_qid)
        if (
            target_metric is None
            or peer_metric is None
            or target_metric.get("occlusion_probe_version")
            not in {"owned-content-v2", "owned-paint-v3"}
            or peer_metric.get("occlusion_probe_version")
            not in {"owned-content-v2", "owned-paint-v3"}
        ):
            return ()
        viewport = {
            "x": max(1.0, float(source.viewportWidth)),
            "y": max(1.0, float(source.viewportHeight)),
        }

        def envelope_for(node: LayoutNode) -> dict[str, Any]:
            return (
                node.constraints.model_dump(mode="json")
                if node.constraints is not None
                else {}
            )

        def capacity(
            owner_qid: str,
            node: LayoutNode,
            axis: str,
            direction: int,
        ) -> tuple[float, dict[str, Any]]:
            envelope = envelope_for(node)
            if not envelope_can_change(envelope):
                return 0.0, {}
            owner_metric = metric_by_qid.get(owner_qid)
            content_boxes: list[tuple[str, str, tuple[float, float, float, float]]] = []
            for qid, item in metric_by_qid.items():
                if not metric_qid_is_ancestor(owner_qid, qid):
                    continue
                tag = str(item.get("tag", "")).lower()
                content_bearing = item.get("has_owned_text") is True or tag in {
                    "img",
                    "picture",
                    "svg",
                    "video",
                    "canvas",
                }
                if not content_bearing:
                    continue
                bbox = item.get("bbox")
                bbox = bbox if isinstance(bbox, Mapping) else {}
                box = (
                    finite_number(bbox.get("x")),
                    finite_number(bbox.get("y")),
                    finite_number(bbox.get("width")),
                    finite_number(bbox.get("height")),
                )
                if box[2] > 0 and box[3] > 0:
                    content_boxes.append(
                        (
                            qid,
                            "owned-text"
                            if item.get("has_owned_text") is True
                            else "replaced-media",
                            box,
                        )
                    )
            if not content_boxes and owner_metric is not None:
                fallback = owner_metric.get("bbox")
                fallback = fallback if isinstance(fallback, Mapping) else {}
                fallback_box = (
                    finite_number(fallback.get("x")),
                    finite_number(fallback.get("y")),
                    finite_number(fallback.get("width")),
                    finite_number(fallback.get("height")),
                )
                if fallback_box[2] > 0 and fallback_box[3] > 0:
                    content_boxes.append(
                        (owner_qid, "owner-bbox-fallback", fallback_box)
                    )
            boxes = [item[2] for item in content_boxes]
            if not boxes:
                return 0.0, {}
            parent_qid = (
                str(owner_metric.get("parent_qid", ""))
                if owner_metric is not None
                else ""
            )
            parent_metric = metric_by_qid.get(parent_qid)
            parent_bbox = (
                parent_metric.get("bbox")
                if parent_metric is not None
                and isinstance(parent_metric.get("bbox"), Mapping)
                else None
            )
            parent_style = (
                parent_metric.get("computed_style", parent_metric.get("style", {}))
                if parent_metric is not None
                else {}
            )
            parent_style = parent_style if isinstance(parent_style, Mapping) else {}
            overflow_value = str(
                parent_style.get(
                    "overflowX" if axis == "x" else "overflowY",
                    parent_style.get("overflow", "visible"),
                )
            ).lower()
            clips_to_parent = overflow_value in {
                "hidden",
                "clip",
                "scroll",
                "auto",
            }
            if axis == "x":
                container_start = (
                    finite_number(parent_bbox.get("x"))
                    if parent_bbox is not None and clips_to_parent
                    else 0.0
                )
                container_end = (
                    container_start + finite_number(parent_bbox.get("width"))
                    if parent_bbox is not None and clips_to_parent
                    else viewport["x"]
                )
                raw_capacity = (
                    min(box[0] for box in boxes) - container_start
                    if direction < 0
                    else container_end - max(box[0] + box[2] for box in boxes)
                )
            else:
                container_start = (
                    finite_number(parent_bbox.get("y"))
                    if parent_bbox is not None and clips_to_parent
                    else 0.0
                )
                container_end = (
                    container_start + finite_number(parent_bbox.get("height"))
                    if parent_bbox is not None and clips_to_parent
                    else viewport["y"]
                )
                raw_capacity = (
                    min(box[1] for box in boxes) - container_start
                    if direction < 0
                    else container_end - max(box[1] + box[3] for box in boxes)
                )
            envelope_capacity = finite_number(
                envelope.get("maxTranslation"), 0.0
            ) * viewport[axis]
            resolved_capacity = max(0.0, min(raw_capacity, envelope_capacity))
            subtree_bounds = {
                "x": min(box[0] for box in boxes),
                "y": min(box[1] for box in boxes),
                "width": max(box[0] + box[2] for box in boxes)
                - min(box[0] for box in boxes),
                "height": max(box[1] + box[3] for box in boxes)
                - min(box[1] for box in boxes),
            }
            boundary_bounds = (
                dict(parent_bbox)
                if parent_bbox is not None and clips_to_parent
                else {
                    "x": 0.0,
                    "y": 0.0,
                    "width": viewport["x"],
                    "height": viewport["y"],
                }
            )
            return resolved_capacity, {
                "ownerQid": owner_qid,
                "axis": axis,
                "direction": direction,
                "boundary": {
                    "source": "chrome",
                    "kind": "clip-ancestor" if clips_to_parent else "canvas",
                    "qid": parent_qid if clips_to_parent else None,
                    "boundsPx": boundary_bounds,
                    "overflowX": str(parent_style.get("overflowX", overflow_value)),
                    "overflowY": str(parent_style.get("overflowY", overflow_value)),
                },
                "subtreeContentBoundsPx": subtree_bounds,
                "contentQids": tuple(item[0] for item in content_boxes),
                "contentKinds": {
                    item[0]: item[1] for item in content_boxes
                },
                "envelopeMaxTranslationPx": envelope_capacity,
                "capacityPx": resolved_capacity,
            }

        target_x = finite_number(target_bbox.get("x"))
        target_y = finite_number(target_bbox.get("y"))
        target_width = finite_number(target_bbox.get("width"))
        target_height = finite_number(target_bbox.get("height"))
        peer_x = finite_number(peer_bbox.get("x"))
        peer_y = finite_number(peer_bbox.get("y"))
        peer_width = finite_number(peer_bbox.get("width"))
        peer_height = finite_number(peer_bbox.get("height"))
        separations = (
            ("x", -1, target_x + target_width - peer_x),
            ("x", 1, peer_x + peer_width - target_x),
            ("y", -1, target_y + target_height - peer_y),
            ("y", 1, peer_y + peer_height - target_y),
        )
        pairs: list[dict[str, Any]] = []
        for axis, direction, separation in sorted(
            separations,
            key=lambda value: (value[2], value[0], value[1]),
        ):
            if separation <= 1e-9:
                continue
            target_capacity, target_capacity_evidence = capacity(
                target_qid, target_node, axis, direction
            )
            peer_capacity, peer_capacity_evidence = capacity(
                peer_qid, peer_node, axis, -direction
            )
            if target_capacity + peer_capacity + 1e-6 < separation:
                continue
            target_amount = min(separation / 2, target_capacity)
            peer_amount = separation - target_amount
            if peer_amount > peer_capacity:
                peer_amount = peer_capacity
                target_amount = separation - peer_amount
            if (
                target_amount <= 1e-9
                or peer_amount <= 1e-9
                or target_amount > target_capacity + 1e-6
                or peer_amount > peer_capacity + 1e-6
            ):
                continue
            target_delta_px = direction * target_amount
            peer_delta_px = -direction * peer_amount
            target_delta = target_delta_px / viewport[axis]
            peer_delta = peer_delta_px / viewport[axis]
            target_xy = {
                "x": target_delta if axis == "x" else 0.0,
                "y": target_delta if axis == "y" else 0.0,
            }
            peer_xy = {
                "x": peer_delta if axis == "x" else 0.0,
                "y": peer_delta if axis == "y" else 0.0,
            }
            pairs.append(
                {
                    "moves": (
                        {
                            "qid": target_qid,
                            "semanticId": target_binding[0],
                            "layoutId": target_node.layoutId,
                            "bounds": target_node.bounds.model_dump(mode="json"),
                            "mutationEnvelope": envelope_for(target_node),
                            "delta": target_xy,
                            "deltaPx": {
                                "x": target_delta_px if axis == "x" else 0.0,
                                "y": target_delta_px if axis == "y" else 0.0,
                            },
                        },
                        {
                            "qid": peer_qid,
                            "semanticId": peer_binding[0],
                            "layoutId": peer_node.layoutId,
                            "bounds": peer_node.bounds.model_dump(mode="json"),
                            "mutationEnvelope": envelope_for(peer_node),
                            "delta": peer_xy,
                            "deltaPx": {
                                "x": peer_delta_px if axis == "x" else 0.0,
                                "y": peer_delta_px if axis == "y" else 0.0,
                            },
                        },
                    ),
                    "proof": {
                        "axis": axis,
                        "direction": direction,
                        "separationPx": separation,
                        "capacityPx": {
                            "target": target_capacity,
                            "peer": peer_capacity,
                        },
                        "capacityEvidence": {
                            "target": target_capacity_evidence,
                            "peer": peer_capacity_evidence,
                        },
                        "source": "chrome",
                        "telemetryComplete": True,
                        "pairResolvesIssue": True,
                        "targetQid": target_qid,
                        "peerQid": peer_qid,
                        "issueTargetQid": issue_target_qid,
                        "issueOccluderQids": tuple(issue_occluder_qids),
                        "ownerDepth": {
                            "target": target_depth,
                            "peer": peer_depth,
                        },
                    },
                }
            )
        return tuple(pairs)

    def qid_chain_to_owner(qid: str, owner_qid: str) -> tuple[str, ...]:
        chain: list[str] = []
        current = qid
        visited: set[str] = set()
        while current and current not in visited:
            chain.append(current)
            if current == owner_qid:
                return tuple(chain)
            visited.add(current)
            item = metric_by_qid.get(current)
            current = str(item.get("parent_qid", "")) if item is not None else ""
        return ()

    def bound_owner_candidates(qid: str) -> tuple[dict[str, Any], ...]:
        result: list[dict[str, Any]] = []
        current = qid
        visited: set[str] = set()
        depth = 0
        while current and current not in visited:
            visited.add(current)
            semantic_id = semantic_by_qid.get(current)
            node = layout_by_semantic.get(semantic_id) if semantic_id else None
            metric = metric_by_qid.get(current)
            bbox = metric.get("bbox") if metric is not None else None
            if (
                semantic_id is not None
                and node is not None
                and node.bounds is not None
                and isinstance(bbox, Mapping)
            ):
                result.append(
                    {
                        "qid": current,
                        "semanticId": semantic_id,
                        "node": node,
                        "bbox": bbox,
                        "depth": depth,
                        "chain": qid_chain_to_owner(qid, current),
                    }
                )
            current = str(metric.get("parent_qid", "")) if metric is not None else ""
            depth += 1
        return tuple(result)

    def bbox_intersection_area(
        first: Mapping[str, Any],
        second: Mapping[str, Any],
        *,
        first_delta: tuple[float, float] = (0.0, 0.0),
        second_delta: tuple[float, float] = (0.0, 0.0),
    ) -> float:
        first_x = finite_number(first.get("x")) + first_delta[0]
        first_y = finite_number(first.get("y")) + first_delta[1]
        second_x = finite_number(second.get("x")) + second_delta[0]
        second_y = finite_number(second.get("y")) + second_delta[1]
        width = min(
            first_x + finite_number(first.get("width")),
            second_x + finite_number(second.get("width")),
        ) - max(first_x, second_x)
        height = min(
            first_y + finite_number(first.get("height")),
            second_y + finite_number(second.get("height")),
        ) - max(first_y, second_y)
        return max(0.0, width) * max(0.0, height)

    def coordinated_owner_pairs(
        *, target_qid: str, occluder_qids: Sequence[str]
    ) -> tuple[dict[str, Any], ...]:
        target_metric = metric_by_qid.get(target_qid)
        target_paint_bbox = (
            target_metric.get("bbox") if target_metric is not None else None
        )
        if not isinstance(target_paint_bbox, Mapping):
            return ()
        occluders = tuple(
            qid
            for qid in dict.fromkeys(occluder_qids)
            if qid != target_qid
            and qid in metric_by_qid
            and isinstance(metric_by_qid[qid].get("bbox"), Mapping)
        )
        if not occluders:
            return ()
        output: list[dict[str, Any]] = []
        seen_owner_pairs: set[tuple[str, str]] = set()
        for target_owner in bound_owner_candidates(target_qid):
            for paint_qid in occluders:
                for peer_owner in bound_owner_candidates(paint_qid):
                    owner_pair = (
                        str(target_owner["qid"]),
                        str(peer_owner["qid"]),
                    )
                    if owner_pair in seen_owner_pairs:
                        continue
                    seen_owner_pairs.add(owner_pair)
                    if (
                        metric_qid_is_ancestor(owner_pair[0], owner_pair[1])
                        or metric_qid_is_ancestor(owner_pair[1], owner_pair[0])
                    ):
                        continue
                    pairs = coordinated_pair_for_owners(
                        issue_target_qid=target_qid,
                        issue_occluder_qids=occluders,
                        target_qid=owner_pair[0],
                        peer_qid=owner_pair[1],
                        target_node=target_owner["node"],
                        peer_node=peer_owner["node"],
                        target_bbox=target_owner["bbox"],
                        peer_bbox=peer_owner["bbox"],
                        target_depth=int(target_owner["depth"]),
                        peer_depth=int(peer_owner["depth"]),
                    )
                    for pair in pairs:
                        first_delta = pair["moves"][0]["deltaPx"]
                        second_delta = pair["moves"][1]["deltaPx"]
                        target_delta = (
                            finite_number(first_delta.get("x")),
                            finite_number(first_delta.get("y")),
                        )
                        peer_delta = (
                            finite_number(second_delta.get("x")),
                            finite_number(second_delta.get("y")),
                        )
                        peer_owned = tuple(
                            qid
                            for qid in occluders
                            if metric_qid_is_ancestor(owner_pair[1], qid)
                        )
                        blocked = tuple(
                            qid
                            for qid in occluders
                            if bbox_intersection_area(
                                target_paint_bbox,
                                metric_by_qid[qid]["bbox"],
                                first_delta=target_delta,
                                second_delta=(
                                    peer_delta if qid in peer_owned else (0.0, 0.0)
                                ),
                            )
                            > 1e-6
                        )
                        if blocked:
                            continue
                        pair["proof"].update(
                            {
                                "ownerCoverage": {
                                    "target": {
                                        "ownerQid": owner_pair[0],
                                        "ownedQids": (target_qid,),
                                        "ancestorQidChains": {
                                            target_qid: target_owner["chain"]
                                        },
                                    },
                                    "peer": {
                                        "ownerQid": owner_pair[1],
                                        "ownedQids": peer_owned,
                                        "ancestorQidChains": {
                                            qid: qid_chain_to_owner(qid, owner_pair[1])
                                            for qid in peer_owned
                                        },
                                    },
                                    "blockedPaintOwnerQids": (),
                                },
                                "coversBlockedPaint": True,
                                "coveredIssueGroupIds": (),
                            }
                        )
                        output.append(pair)
        return tuple(
            sorted(
                output,
                key=lambda pair: (
                    -len(pair["proof"]["ownerCoverage"]["peer"]["ownedQids"]),
                    pair["proof"]["ownerDepth"]["target"]
                    + pair["proof"]["ownerDepth"]["peer"],
                    sum(
                        abs(finite_number(move["deltaPx"].get(axis)))
                        for move in pair["moves"]
                        for axis in ("x", "y")
                    ),
                    pair["moves"][0]["qid"],
                    pair["moves"][1]["qid"],
                ),
            )
        )

    def text_fragment_bounds(metric: Mapping[str, Any]) -> dict[str, float] | None:
        fragments = metric.get("text_fragments")
        if not isinstance(fragments, Sequence) or isinstance(fragments, (str, bytes)):
            return None
        rects = [item for item in fragments if isinstance(item, Mapping)]
        if not rects:
            return None
        left = min(finite_number(rect.get("x")) for rect in rects)
        top = min(finite_number(rect.get("y")) for rect in rects)
        right = max(
            finite_number(rect.get("x")) + finite_number(rect.get("width"))
            for rect in rects
        )
        bottom = max(
            finite_number(rect.get("y")) + finite_number(rect.get("height"))
            for rect in rects
        )
        if right <= left or bottom <= top:
            return None
        return {"x": left, "y": top, "width": right - left, "height": bottom - top}

    def glyph_measurement(
        metric: Mapping[str, Any], imported: Any
    ) -> dict[str, Any] | None:
        fragment_bounds = text_fragment_bounds(metric)
        if fragment_bounds is None:
            return None
        measured_width = max(
            fragment_bounds["width"],
            finite_number(metric.get("scroll_width")),
        )
        measured_height = max(
            fragment_bounds["height"],
            finite_number(metric.get("scroll_height")),
        )
        available_width = finite_number(metric.get("client_width"))
        available_height = finite_number(metric.get("client_height"))
        if min(measured_width, measured_height, available_width, available_height) <= 0:
            return None
        return {
            "source": "chrome",
            "telemetryComplete": bool(normalized_metrics.get("fonts_ready"))
            and metric.get("font_loaded") is True,
            "fontsReady": bool(normalized_metrics.get("fonts_ready"))
            and metric.get("font_loaded") is True,
            "kind": "range-bounds",
            "measuredWidthPx": measured_width,
            "measuredHeightPx": measured_height,
            "availableWidthPx": available_width,
            "availableHeightPx": available_height,
            "fontSizePx": finite_number(
                metric.get("font_size"), finite_number(getattr(imported, "fontSizePx", 0))
            ),
        }

    def precise_overflow_bounds(
        imported: Any, current: Mapping[str, float]
    ) -> dict[str, float]:
        """Expand only by the measured overflow plus a small render safety pad."""

        candidate = dict(current)
        if source is None or imported is None:
            candidate["height"] = min(1.0 - current["y"], current["height"] + 0.02)
            return candidate
        metric = metric_by_qid.get(imported.qid)
        if not isinstance(metric, Mapping):
            candidate["height"] = min(1.0 - current["y"], current["height"] + 0.02)
            return candidate
        horizontal_excess = max(
            0.0,
            float(metric.get("scroll_width", 0.0))
            - float(metric.get("client_width", 0.0)),
        )
        vertical_excess = max(
            0.0,
            float(metric.get("scroll_height", 0.0))
            - float(metric.get("client_height", 0.0)),
        )
        if horizontal_excess > 1.0:
            candidate["width"] = min(
                1.0 - current["x"],
                current["width"] + (horizontal_excess + 4.0) / source.viewportWidth,
            )
        if vertical_excess > 1.0:
            candidate["height"] = min(
                1.0 - current["y"],
                current["height"] + (vertical_excess + 4.0) / source.viewportHeight,
            )
        return candidate

    def point_segment_distance(
        point: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-12:
            return math.hypot(point[0] - start[0], point[1] - start[1])
        projection = max(
            0.0,
            min(
                1.0,
                ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
                / length_sq,
            ),
        )
        nearest = (start[0] + projection * dx, start[1] + projection * dy)
        return math.hypot(point[0] - nearest[0], point[1] - nearest[1])

    def segment_intersects_rect(
        start: tuple[float, float],
        end: tuple[float, float],
        rect: tuple[float, float, float, float],
    ) -> bool:
        """Liang-Barsky intersection, inclusive of touching the rectangle."""

        left, top, right, bottom = rect
        dx, dy = end[0] - start[0], end[1] - start[1]
        lower, upper = 0.0, 1.0
        for p, q in (
            (-dx, start[0] - left),
            (dx, right - start[0]),
            (-dy, start[1] - top),
            (dy, bottom - start[1]),
        ):
            if abs(p) <= 1e-12:
                if q < 0:
                    return False
                continue
            ratio = q / p
            if p < 0:
                lower = max(lower, ratio)
            else:
                upper = min(upper, ratio)
            if lower > upper:
                return False
        return True

    def segment_rect_clearance(
        start: tuple[float, float],
        end: tuple[float, float],
        rect: tuple[float, float, float, float],
    ) -> float:
        if segment_intersects_rect(start, end, rect):
            return 0.0
        left, top, right, bottom = rect
        corners = ((left, top), (right, top), (right, bottom), (left, bottom))

        def point_rect_distance(point: tuple[float, float]) -> float:
            x_delta = max(left - point[0], 0.0, point[0] - right)
            y_delta = max(top - point[1], 0.0, point[1] - bottom)
            return math.hypot(x_delta, y_delta)

        return min(
            point_rect_distance(start),
            point_rect_distance(end),
            *(point_segment_distance(corner, start, end) for corner in corners),
        )

    def connector_candidate(
        node: LayoutNode,
        metric: Mapping[str, Any] | None,
        obstacle_metrics: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the shortest deterministic Chrome-grounded safe dogleg.

        The route is canonical in the connector node's normalized coordinate
        space.  Obstacles and endpoint observations come from the real Chrome
        frame, so a candidate is never labeled safe from schema geometry alone.
        """

        current = node.connectorRoute
        if current is None:
            return {}, {"source": "chrome", "telemetryComplete": False}
        current_route = {
            "points": [point.model_dump(mode="json") for point in current.points]
        }
        box = metric.get("bbox") if metric is not None else None
        box = box if isinstance(box, Mapping) else {}
        box_x = finite_number(box.get("x"))
        box_y = finite_number(box.get("y"))
        box_width = finite_number(box.get("width"))
        box_height = finite_number(box.get("height"))
        geometry = metric.get("connector_geometry") if metric is not None else None
        geometry = geometry if isinstance(geometry, Mapping) else {}
        measured_points = geometry.get("pointsPx")
        if not isinstance(measured_points, Sequence) or isinstance(
            measured_points, (str, bytes)
        ):
            measured_points = ()

        obstacle_rects: list[tuple[float, float, float, float]] = []
        for obstacle_metric in obstacle_metrics:
            obstacle_box = obstacle_metric.get("bbox")
            obstacle_box = obstacle_box if isinstance(obstacle_box, Mapping) else {}
            x = finite_number(obstacle_box.get("x"))
            y = finite_number(obstacle_box.get("y"))
            width = finite_number(obstacle_box.get("width"))
            height = finite_number(obstacle_box.get("height"))
            if width > 0 and height > 0:
                obstacle_rects.append((x, y, x + width, y + height))

        expected = [
            (
                box_x + point.x * box_width,
                box_y + point.y * box_height,
            )
            for point in (current.points[0], current.points[-1])
        ]
        actual: list[tuple[float, float]] = []
        if len(measured_points) >= 2:
            for raw in (measured_points[0], measured_points[-1]):
                if isinstance(raw, Mapping):
                    actual.append((finite_number(raw.get("x")), finite_number(raw.get("y"))))
        endpoint_error = (
            max(
                abs(observed[axis] - desired[axis])
                for observed, desired in zip(actual, expected)
                for axis in (0, 1)
            )
            if len(actual) == 2 and min(box_width, box_height) > 0
            else float("inf")
        )

        def route_pixels(points: Sequence[Mapping[str, float]]) -> list[tuple[float, float]]:
            return [
                (
                    box_x + finite_number(point.get("x")) * box_width,
                    box_y + finite_number(point.get("y")) * box_height,
                )
                for point in points
            ]

        def clearance(points: Sequence[Mapping[str, float]]) -> float:
            pixels = route_pixels(points)
            if len(pixels) < 2 or not obstacle_rects:
                return float("inf") if len(pixels) >= 2 else 0.0
            return min(
                segment_rect_clearance(start, end, rect)
                for start, end in zip(pixels, pixels[1:])
                for rect in obstacle_rects
            )

        start = current_route["points"][0]
        end = current_route["points"][-1]
        candidates: list[list[dict[str, float]]] = []
        if obstacle_rects and min(box_width, box_height) > 0:
            clearance_px = 4.0
            top = (min(rect[1] for rect in obstacle_rects) - clearance_px - box_y) / box_height
            bottom = (max(rect[3] for rect in obstacle_rects) + clearance_px - box_y) / box_height
            left = (min(rect[0] for rect in obstacle_rects) - clearance_px - box_x) / box_width
            right = (max(rect[2] for rect in obstacle_rects) + clearance_px - box_x) / box_width
            if 0 <= top <= 1:
                candidates.append([start, {"x": start["x"], "y": top}, {"x": end["x"], "y": top}, end])
            if 0 <= bottom <= 1:
                candidates.append([start, {"x": start["x"], "y": bottom}, {"x": end["x"], "y": bottom}, end])
            if 0 <= left <= 1:
                candidates.append([start, {"x": left, "y": start["y"]}, {"x": left, "y": end["y"]}, end])
            if 0 <= right <= 1:
                candidates.append([start, {"x": right, "y": start["y"]}, {"x": right, "y": end["y"]}, end])

        def route_length(points: Sequence[Mapping[str, float]]) -> float:
            return sum(
                math.hypot(
                    finite_number(right_point.get("x")) - finite_number(left_point.get("x")),
                    finite_number(right_point.get("y")) - finite_number(left_point.get("y")),
                )
                for left_point, right_point in zip(points, points[1:])
            )

        safe_candidates = [points for points in candidates if clearance(points) >= 1.0]
        safe_candidates.sort(
            key=lambda points: (
                round(route_length(points), 9),
                tuple((round(point["x"], 9), round(point["y"], 9)) for point in points),
            )
        )
        selected = safe_candidates[0] if safe_candidates else current_route["points"]
        minimum_clearance = clearance(selected)
        telemetry_complete = bool(
            metric is not None
            and geometry.get("measured") is True
            and len(actual) == 2
            and obstacle_rects
            and min(box_width, box_height) > 0
        )
        return {"points": selected}, {
            "source": "chrome",
            "telemetryComplete": telemetry_complete,
            "endpointAnchors": {
                "startResolved": len(actual) == 2,
                "endResolved": len(actual) == 2,
                "maxErrorPx": endpoint_error,
            },
            "obstacleFree": bool(safe_candidates and minimum_clearance >= 1.0),
            "minimumClearancePx": minimum_clearance,
            "obstacleCount": len(obstacle_rects),
        }
    bounds: list[dict[str, Any]] = []
    fonts: list[dict[str, Any]] = []
    glyph_fits: list[dict[str, Any]] = []
    image_crops: list[dict[str, Any]] = []
    static_translations: list[dict[str, Any]] = []
    connector_routes: list[dict[str, Any]] = []
    refs: list[str] = []
    refs_by_target: dict[str, list[str]] = {}
    ordered_defects = sorted(
        evidence.defects,
        key=lambda defect: (
            _DEFECT_PRIORITY.get((defect.severity, defect.code), 99),
            defect.semantic_id,
        ),
    )
    affected_targets: set[str] = set()
    for defect in ordered_defects:
        diagnostic_semantic_ids = tuple(
            dict.fromkeys((defect.semantic_id, *defect.related_semantic_ids))
        )
        repair_semantic_id = defect.semantic_id
        connector_semantic_id = next(
            (
                semantic_id
                for semantic_id in diagnostic_semantic_ids
                if (candidate_node := layout_by_semantic.get(semantic_id)) is not None
                and candidate_node.connectorRoute is not None
                and (candidate_content := content_by_semantic.get(semantic_id)) is not None
                and candidate_content.kind == "connector"
            ),
            None,
        )
        if defect.code in {"dom.unintended_overlap", "constraint.non_overlap.violated"}:
            if connector_semantic_id is None:
                continue
            repair_semantic_id = connector_semantic_id
        elif defect.code in {
            "constraint.order.violated",
            "constraint.gap.violated",
            "constraint.alignment.violated",
            "constraint.anchor.violated",
            "constraint.group.atomic_translation",
            "constraint.evidence.missing",
            "constraint.soft_regression",
        }:
            # Resizing a common ancestor can push bottom-anchored footers and
            # overlays out of the canvas, making the slide worse.  An overlap has
            # no generally safe geometry direction, so leave it to the single
            # bounded proposal path and the rendered VQA acceptance gate.
            continue
        node = layout_by_semantic.get(repair_semantic_id)
        if node is None or node.bounds is None:
            continue
        imported = binding_by_semantic.get(repair_semantic_id)
        content = content_by_semantic.get(repair_semantic_id)
        metric = metric_for_semantic(repair_semantic_id)
        if node.layoutId not in affected_targets and len(affected_targets) >= 4:
            continue
        diagnostic_refs = tuple(
            f"{defect.code}:{semantic_id}" for semantic_id in diagnostic_semantic_ids
        )
        current = node.bounds.model_dump(mode="json")
        # Native SlideSpecs legitimately omit a mutation envelope.  The basic
        # deterministic cascade has always treated that as the schema default;
        # advanced imported-HTML projections still require the importer's
        # explicit allow-list through ``_mutation_allowed``.
        node_constraints = node.constraints or LayoutConstraints()
        mutation_envelope = node_constraints.model_dump(mode="json")
        if connector_semantic_id is not None and node.connectorRoute is not None:
            obstacle_metrics = tuple(
                obstacle_metric
                for semantic_id in diagnostic_semantic_ids
                if semantic_id != connector_semantic_id
                and (obstacle_metric := metric_for_semantic(semantic_id)) is not None
            )
            route, measurement = connector_candidate(node, metric, obstacle_metrics)
            connector_routes.append(
                {
                    "target": node.layoutId,
                    "currentRoute": node.connectorRoute.model_dump(mode="json"),
                    "route": route,
                    "mutationEnvelope": mutation_envelope,
                    "measurement": measurement,
                    "roundTrip": {
                        # Imported HTML still fails closed: without an exact SVG
                        # attribute binding, authored path mutation cannot prove a
                        # lossless source round trip. Native rendering is canonical.
                        "supported": source is None,
                        "verifiedProperties": ["connector-route"],
                    },
                }
            )
            affected_targets.add(node.layoutId)
            refs.extend(diagnostic_refs)
            refs_by_target.setdefault(node.layoutId, []).extend(diagnostic_refs)
            continue
        if defect.code in {"dom.off_canvas", "constraint.containment.violated"}:
            delta_x = delta_y = 0.0
            if imported is not None and source is not None:
                raw = imported.sourceBoundsPx
                width = source.viewportWidth
                height = source.viewportHeight
                if raw.x < 0:
                    delta_x = -raw.x / width
                elif raw.x + raw.width > width:
                    delta_x = (width - raw.x - raw.width) / width
                if raw.y < 0:
                    delta_y = -raw.y / height
                elif raw.y + raw.height > height:
                    delta_y = (height - raw.y - raw.height) / height
            candidate = {
                **current,
                "x": min(
                    max(0.0, current["x"] + delta_x),
                    1.0 - current["width"],
                ),
                "y": min(
                    max(0.0, current["y"] + delta_y),
                    1.0 - current["height"],
                ),
            }
        else:
            candidate = precise_overflow_bounds(imported, current)
        is_text_target = content is not None and content.kind in _TEXT_KINDS
        # Offer geometry first and a small font-scale alternative for text.  The
        # cascade evaluates them as separate candidates, so it never combines both
        # changes unless a later bounded proposal explicitly justifies that scope.
        use_font = (
            defect.code in {"dom.clipping", "dom.overflow"}
            and is_text_target
            and node.fontScale > 0.85
        )
        added = False
        static_position = imported is not None and imported.position == "static"
        if candidate != current and not static_position:
            existing = next(
                (item for item in bounds if item["target"] == node.layoutId), None
            )
            if existing is None:
                bounds.append(
                    {
                        "target": node.layoutId,
                        "currentBounds": current,
                        "bounds": candidate,
                    }
                )
            else:
                merged = dict(existing["bounds"])
                merged["x"] = candidate["x"] if candidate["x"] != current["x"] else merged["x"]
                merged["y"] = candidate["y"] if candidate["y"] != current["y"] else merged["y"]
                merged["width"] = max(merged["width"], candidate["width"])
                merged["height"] = max(merged["height"], candidate["height"])
                existing["bounds"] = merged
            added = True
        measured_glyph = (
            glyph_measurement(metric, imported)
            if use_font and metric is not None and imported is not None
            else None
        )
        if use_font and metric is not None and imported is not None:
            glyph_fits.append(
                {
                    "target": node.layoutId,
                    "currentScale": node.fontScale,
                    "minimumScale": node_constraints.minFontScale,
                    "mutationEnvelope": mutation_envelope,
                    "measurement": measured_glyph
                    or {
                        "source": "chrome",
                        "telemetryComplete": False,
                        "fontsReady": False,
                        "kind": "range-bounds",
                    },
                    "roundTrip": {
                        "supported": imported.fontSizePx is not None,
                        "verifiedProperties": ["font-size"],
                    },
                }
            )
            added = True
        elif use_font and metric is None:
            # Compatibility path for synthetic/unit contexts that have no
            # browser source. Production imported HTML always uses the measured
            # glyph candidate above and therefore fails closed on missing data.
            fonts.append(
                {
                    "target": node.layoutId,
                    "currentScale": node.fontScale,
                    "scale": max(0.85, round(node.fontScale * 0.95, 4)),
                    "minimumScale": 0.85,
                }
            )
            added = True
        if (
            defect.code in {"dom.clipping", "dom.overflow"}
            and content is not None
            and content.kind == "image"
            and imported is not None
            and imported.tag == "img"
            and metric is not None
        ):
            image = metric.get("image")
            image = image if isinstance(image, Mapping) else {}
            box = metric.get("bbox")
            box = box if isinstance(box, Mapping) else {}
            natural_width = finite_number(image.get("naturalWidth"))
            natural_height = finite_number(image.get("naturalHeight"))
            box_width = finite_number(box.get("width"))
            box_height = finite_number(box.get("height"))
            natural_ratio = natural_width / natural_height if natural_height else 0.0
            box_ratio = box_width / box_height if box_height else 0.0
            if imported.objectFit == "fill" and natural_ratio and box_ratio and abs(
                math.log(natural_ratio / box_ratio)
            ) > 0.05:
                image_crops.append(
                    {
                        "target": node.layoutId,
                        "currentFit": imported.objectFit,
                        "fit": "contain",
                        "currentFocalPoint": imported.cropFocalPoint.model_dump(mode="json"),
                        "focalPoint": imported.cropFocalPoint.model_dump(mode="json"),
                        "mutationEnvelope": mutation_envelope,
                        "measurement": {
                            "source": "chrome",
                            "telemetryComplete": bool(
                                image.get("complete") and natural_width and natural_height
                            ),
                            "decoded": bool(image.get("complete")),
                            "naturalWidthPx": natural_width,
                            "naturalHeightPx": natural_height,
                            "boxWidthPx": box_width,
                            "boxHeightPx": box_height,
                        },
                        "roundTrip": {
                            "supported": True,
                            "verifiedProperties": ["object-fit", "object-position"],
                        },
                    }
                )
                added = True
        if static_position and candidate != current and imported is not None:
            probe = metric.get("static_translation_probe") if metric is not None else {}
            probe = probe if isinstance(probe, Mapping) else {}
            parent = probe.get("parentBoundsPx")
            target = probe.get("targetBoundsPx")
            parent = parent if isinstance(parent, Mapping) else {}
            target = target if isinstance(target, Mapping) else {}
            parent_width = finite_number(parent.get("width"))
            parent_height = finite_number(parent.get("height"))
            dx = candidate["x"] - current["x"]
            dy = candidate["y"] - current["y"]
            candidate_in_parent = {
                "x": (
                    finite_number(target.get("x"))
                    - finite_number(parent.get("x"))
                    + dx * source.viewportWidth
                )
                / parent_width
                if source is not None and parent_width > 0
                else -1,
                "y": (
                    finite_number(target.get("y"))
                    - finite_number(parent.get("y"))
                    + dy * source.viewportHeight
                )
                / parent_height
                if source is not None and parent_height > 0
                else -1,
                "width": finite_number(target.get("width")) / parent_width
                if parent_width > 0
                else -1,
                "height": finite_number(target.get("height")) / parent_height
                if parent_height > 0
                else -1,
            }
            static_translations.append(
                {
                    "target": node.layoutId,
                    "currentBounds": current,
                    "delta": {"x": dx, "y": dy},
                    "mutationEnvelope": mutation_envelope,
                    "measurement": {
                        **probe,
                        "source": probe.get("source"),
                        "telemetryComplete": bool(
                            imported.staticTranslationSafe
                            and probe.get("telemetryComplete") is True
                        ),
                        "candidateBoundsInParent": candidate_in_parent,
                    },
                    "roundTrip": {
                        "supported": imported.staticTranslationSafe,
                        "verifiedProperties": ["transform"],
                    },
                }
            )
            added = True
        if not added:
            continue
        affected_targets.add(node.layoutId)
        refs.extend(diagnostic_refs)
        refs_by_target.setdefault(node.layoutId, []).extend(diagnostic_refs)

    geometry_issues: list[dict[str, Any]] = []
    for defect in evidence.defects:
        if defect.code == "dom.unintended_overlap":
            node = layout_by_semantic.get(defect.semantic_id)
            target_qid = defect.qid or qid_for_semantic(defect.semantic_id)
            metric = metric_by_qid.get(target_qid) if target_qid else None
            if (
                node is None
                or node.bounds is None
                or target_qid is None
                or not isinstance(metric, Mapping)
            ):
                continue
            raw_occluders = metric.get("occlusion_occluder_qids", ())
            occluder_qids = (
                tuple(str(value) for value in raw_occluders)
                if isinstance(raw_occluders, Sequence)
                and not isinstance(raw_occluders, (str, bytes))
                else ()
            )
            physical_overlap = defect.diagnostics.get("physical_overlap", {})
            physical_overlap = (
                physical_overlap
                if isinstance(physical_overlap, Mapping)
                else {}
            )
            physical_target_qids = tuple(
                str(value)
                for value in physical_overlap.get("target_qids", ())
                if value
            )
            physical_blocker_qids = tuple(
                str(value)
                for value in physical_overlap.get("occluder_qids", ())
                if value
            )
            related_qids = tuple(
                (
                    semantic_id,
                    qid_for_semantic(semantic_id),
                )
                for semantic_id in defect.related_semantic_ids
            )
            proven_pair = next(
                (
                    (value, semantic_id, related_qid)
                    for value in occluder_qids
                    for semantic_id, related_qid in related_qids
                    if value != target_qid
                    and value in metric_by_qid
                    and related_qid is not None
                    and metric_qids_related(value, related_qid)
                ),
                None,
            )
            if proven_pair is None:
                proven_pair = next(
                    (
                        (value, bound[0], bound[1])
                        for value in occluder_qids
                        if value != target_qid and value in metric_by_qid
                        for bound in (bound_semantic_for_metric_qid(value),)
                        if bound is not None
                        and bound[0] != defect.semantic_id
                        and bound[0] in layout_by_semantic
                    ),
                    None,
                )
            occluder_qid = proven_pair[0] if proven_pair is not None else None
            occluder_semantic = proven_pair[1] if proven_pair is not None else None
            related_qid = proven_pair[2] if proven_pair is not None else None
            other_node = (
                layout_by_semantic.get(occluder_semantic)
                if occluder_semantic is not None
                else None
            )
            if (
                occluder_qid is None
                or other_node is None
                or other_node.bounds is None
                or other_node.layoutId == node.layoutId
            ):
                continue
            probe_version = str(metric.get("occlusion_probe_version", ""))
            magnitude = max(
                finite_number(metric.get("occluded_fraction")),
                finite_number(metric.get("continuous_occluded_fraction")),
            )
            imported_global_space = (
                source is not None
                and defect.semantic_id in binding_by_semantic
                and occluder_semantic in binding_by_semantic
            )
            same_space = imported_global_space or (
                parent_by_id.get(node.layoutId)
                == parent_by_id.get(other_node.layoutId)
            )
            target_bbox = metric.get("bbox")
            target_bbox = target_bbox if isinstance(target_bbox, Mapping) else {}
            occluder_metric = metric_by_qid.get(occluder_qid)
            occluder_bbox = (
                occluder_metric.get("bbox")
                if isinstance(occluder_metric, Mapping)
                else {}
            )
            occluder_bbox = (
                occluder_bbox if isinstance(occluder_bbox, Mapping) else {}
            )
            target_x = finite_number(target_bbox.get("x"))
            target_y = finite_number(target_bbox.get("y"))
            target_width = finite_number(target_bbox.get("width"))
            target_height = finite_number(target_bbox.get("height"))
            occluder_x = finite_number(occluder_bbox.get("x"))
            occluder_y = finite_number(occluder_bbox.get("y"))
            occluder_width = finite_number(occluder_bbox.get("width"))
            occluder_height = finite_number(occluder_bbox.get("height"))
            target_deltas_px = (
                (occluder_x - target_x - target_width, 0.0),
                (occluder_x + occluder_width - target_x, 0.0),
                (0.0, occluder_y - target_y - target_height),
                (0.0, occluder_y + occluder_height - target_y),
            )
            other_deltas_px = tuple((-dx, -dy) for dx, dy in target_deltas_px)
            horizontal_overlap = min(
                target_x + target_width,
                occluder_x + occluder_width,
            ) - max(target_x, occluder_x)
            vertical_overlap = min(
                target_y + target_height,
                occluder_y + occluder_height,
            ) - max(target_y, occluder_y)
            minimum_displacement = min(
                value
                for value in (horizontal_overlap, vertical_overlap)
                if value >= 0
            ) if max(horizontal_overlap, vertical_overlap) >= 0 else 0.0
            stable_qids = sorted((target_qid, related_qid or occluder_qid))
            target_bound_ancestors = bound_ancestor_candidates(
                target_qid=target_qid,
                blocked_qid=occluder_qid,
                candidate_deltas_px=target_deltas_px,
                physical_target_qids=physical_target_qids,
                physical_blocker_qids=physical_blocker_qids,
            )
            physical_owner_blockers = tuple(
                failure
                for ancestor in target_bound_ancestors
                if isinstance(ancestor, Mapping)
                for failure in (ancestor.get("physicalOwnerFailure"),)
                if isinstance(failure, Mapping)
            )
            geometry_issues.append(
                {
                    "code": defect.code,
                    "semanticId": defect.semantic_id,
                    "qid": target_qid,
                    "constraintId": (
                        f"dom:{defect.code}:{':'.join(stable_qids)}"
                    ),
                    "kind": "nonOverlap",
                    "target": node.layoutId,
                    "currentBounds": node.bounds.model_dump(mode="json"),
                    "otherTarget": other_node.layoutId,
                    "otherBounds": other_node.bounds.model_dump(mode="json"),
                    "sameCoordinateSpace": same_space,
                    "containerBounds": {
                        "x": 0.0,
                        "y": 0.0,
                        "width": 1.0,
                        "height": 1.0,
                    },
                    "requiresEnvelope": source is not None,
                    "mutationEnvelope": (
                        node.constraints.model_dump(mode="json")
                        if node.constraints is not None
                        else {}
                    ),
                    "otherMutationEnvelope": (
                        other_node.constraints.model_dump(mode="json")
                        if other_node.constraints is not None
                        else {}
                    ),
                    "boundAncestors": target_bound_ancestors,
                    "otherBoundAncestors": bound_ancestor_candidates(
                        target_qid=related_qid or occluder_qid,
                        blocked_qid=target_qid,
                        candidate_deltas_px=other_deltas_px,
                    ),
                    "coordinatedAncestorMoves": (
                        coordinated_owner_pairs(
                            target_qid=target_qid,
                            occluder_qids=occluder_qids,
                        )
                        if occluder_qids
                        else ()
                    ),
                    "detectorEvidence": {
                        "confirmed": (
                            magnitude > 0
                            and probe_version
                            in {"owned-content-v2", "owned-paint-v3"}
                        ),
                        "independentElements": same_space,
                        "stage": "dom",
                        "metric": "owned_content_occluded_fraction",
                        "value": magnitude,
                        "threshold": 0.0,
                        "occluderQid": occluder_qid,
                        "occluderBindingQid": related_qid,
                        "browserIssueQidCount": 1
                        + len(defect.related_semantic_ids),
                        "targetBoundsPx": dict(target_bbox),
                        "occluderBoundsPx": dict(occluder_bbox),
                        "minimumDisplacementPx": minimum_displacement,
                        "probeVersion": probe_version,
                        "physicalOwnerBlockers": physical_owner_blockers,
                    },
                }
            )
            continue
        if defect.code not in {"dom.clipping", "dom.overflow", "dom.off_canvas"}:
            continue
        node = layout_by_semantic.get(defect.semantic_id)
        qid = defect.qid or qid_for_semantic(defect.semantic_id)
        metric = metric_by_qid.get(qid) if qid else None
        if node is None or node.bounds is None or qid is None or metric is None:
            continue
        width_basis = source.viewportWidth if source is not None else 1280.0
        height_basis = source.viewportHeight if source is not None else 720.0
        magnitude = max(
            max(
                0.0,
                finite_number(metric.get("scroll_width"))
                - finite_number(metric.get("client_width")),
            )
            / max(1.0, width_basis),
            max(
                0.0,
                finite_number(metric.get("scroll_height"))
                - finite_number(metric.get("client_height")),
            )
            / max(1.0, height_basis),
        )
        if defect.code == "dom.off_canvas":
            magnitude = _off_canvas_fraction(
                metric,
                canvas_width=width_basis,
                canvas_height=height_basis,
            )
        issue_kind = "containment" if defect.code == "dom.off_canvas" else "overflow"
        geometry_issues.append(
            {
                "code": defect.code,
                "semanticId": defect.semantic_id,
                "qid": qid,
                "constraintId": (
                    f"dom:{defect.code}:{qid}"
                    if defect.code == "dom.off_canvas"
                    else None
                ),
                "kind": issue_kind,
                "target": node.layoutId,
                "currentBounds": node.bounds.model_dump(mode="json"),
                "sameCoordinateSpace": True,
                "containerBounds": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                "requiresEnvelope": source is not None,
                "mutationEnvelope": (
                    node.constraints.model_dump(mode="json")
                    if node.constraints is not None
                    else {}
                ),
                "detectorEvidence": {
                    "confirmed": magnitude > 0,
                    "stage": "dom",
                    "metric": (
                        "canvas_overflow_fraction"
                        if defect.code == "dom.off_canvas"
                        else "overflow_fraction"
                    ),
                    "value": magnitude,
                    "threshold": 0.0,
                },
            }
        )

    constraint_by_id = {
        constraint.constraintId: constraint.model_dump(mode="json")
        for constraint in spec.constraints
    }
    semantic_by_layout = {
        node.layoutId: node.contentSemanticId
        for node in layout_by_id.values()
        if node.contentSemanticId is not None
    }
    for violation in (constraint_report.violations if constraint_report else ()):
        if violation.strength != "hard" or not violation.targets:
            continue
        target_id = violation.targets[0]
        target_node = layout_by_id.get(target_id)
        semantic_id = semantic_by_layout.get(target_id)
        qid = qid_for_semantic(semantic_id) if semantic_id else None
        if target_node is None or target_node.bounds is None or not semantic_id or not qid:
            continue
        peer_id = next(
            (item for item in violation.targets[1:] if item in layout_by_id),
            None,
        )
        peer_node = layout_by_id.get(peer_id) if peer_id else None
        peer_semantic_id = semantic_by_layout.get(peer_id or "")
        imported_global_space = (
            source is not None
            and semantic_id in binding_by_semantic
            and peer_semantic_id in binding_by_semantic
        )
        same_space = peer_node is None or imported_global_space or (
            parent_by_id.get(target_id) == parent_by_id.get(peer_id)
        )
        issue: dict[str, Any] = {
            **dict(constraint_by_id.get(violation.constraint_id, {})),
            "code": violation.code,
            "semanticId": semantic_id,
            "qid": qid,
            "constraintId": violation.constraint_id,
            "kind": violation.kind,
            "target": target_id,
            "currentBounds": target_node.bounds.model_dump(mode="json"),
            "sameCoordinateSpace": same_space,
            "requiresEnvelope": source is not None,
            "mutationEnvelope": (
                target_node.constraints.model_dump(mode="json")
                if target_node.constraints is not None
                else {}
            ),
            "detectorEvidence": {
                "confirmed": violation.magnitude > 0,
                "stage": "invariant",
                "metric": "constraint_residual",
                "value": violation.magnitude,
                "threshold": 0.0,
                "independentElements": violation.kind != "nonOverlap"
                or same_space,
            },
        }
        if peer_node is not None and peer_node.bounds is not None:
            issue.update(
                {
                    "otherTarget": peer_id,
                    "otherBounds": peer_node.bounds.model_dump(mode="json"),
                    "otherMutationEnvelope": (
                        peer_node.constraints.model_dump(mode="json")
                        if peer_node.constraints is not None
                        else {}
                    ),
                }
            )
        issue["containerBounds"] = {
            "x": 0.0,
            "y": 0.0,
            "width": 1.0,
            "height": 1.0,
        }
        geometry_issues.append(issue)

    geometry_issue_groups: dict[str, list[str]] = {}
    composition_qids: dict[str, tuple[str, ...]] = {}
    for issue in geometry_issues:
        detector = issue.get("detectorEvidence")
        detector = detector if isinstance(detector, Mapping) else {}
        group_digest = digest_parts(
            "geometry-issue-group/1",
            issue.get("code"),
            issue.get("qid"),
            issue.get("constraintId"),
            detector.get("occluderQid"),
        ).removeprefix("sha256:")[:16]
        group_id = f"geometry:{group_digest}"
        issue["issueGroupId"] = group_id
        constraint_id = issue.get("constraintId")
        geometry_issue_groups[group_id] = (
            [str(constraint_id)] if constraint_id else []
        )
        raw_ancestors = (
            *(
                issue.get("boundAncestors", ())
                if isinstance(issue.get("boundAncestors"), Sequence)
                else ()
            ),
            *(
                issue.get("otherBoundAncestors", ())
                if isinstance(issue.get("otherBoundAncestors"), Sequence)
                else ()
            ),
        )
        composition_qids[group_id] = tuple(
            dict.fromkeys(
                str(value)
                for value in (
                    issue.get("qid"),
                    detector.get("occluderQid"),
                    detector.get("occluderBindingQid"),
                    *(
                        ancestor.get("qid")
                        for ancestor in raw_ancestors
                        if isinstance(ancestor, Mapping)
                    ),
                )
                if value
            )
        )
        target = str(issue.get("target", ""))
        peer_qid = str(
            detector.get("occluderBindingQid")
            or detector.get("occluderQid")
            or ""
        )
        peer_target = str(issue.get("otherTarget", ""))
        issue_refs = tuple(
            str(value)
            for value in (
                f"issue-group:{group_id}",
                f"code:{issue.get('code')}",
                f"semantic:{issue.get('semanticId')}",
                f"qid:{issue.get('qid')}",
                f"qid:{peer_qid}" if peer_qid else None,
                f"constraint:{constraint_id}" if constraint_id else None,
                f"target:{target}" if target else None,
                f"target:{peer_target}" if peer_target else None,
                f"stage:{detector.get('stage')}",
                f"metric:{detector.get('metric')}",
            )
            if value is not None
        )
        refs.extend(issue_refs)
        if target:
            refs_by_target.setdefault(target, []).extend(issue_refs)

    # A Chrome-proved text-to-text overlap may have no clipping/overflow flag,
    # yet a bounded font probe can still be the smallest applicable mutation.
    # Emit only the envelope floor when measured range bounds prove that exact
    # scale separates the two text paints.  The rendered candidate DOM gate is
    # still authoritative for final resolution.
    glyph_targets = {str(item.get("target", "")) for item in glyph_fits}
    for issue in geometry_issues:
        detector = issue.get("detectorEvidence")
        detector = detector if isinstance(detector, Mapping) else {}
        if (
            issue.get("kind") != "nonOverlap"
            or detector.get("confirmed") is not True
        ):
            continue
        semantic_id = str(issue.get("semanticId", ""))
        target = str(issue.get("target", ""))
        qid = str(issue.get("qid", ""))
        occluder_qid = str(
            detector.get("occluderBindingQid")
            or detector.get("occluderQid")
            or ""
        )
        node = layout_by_id.get(target)
        content = content_by_semantic.get(semantic_id)
        imported = binding_by_semantic.get(semantic_id)
        target_metric = metric_by_qid.get(qid)
        occluder_metric = metric_by_qid.get(occluder_qid)
        if (
            not target
            or target in glyph_targets
            or node is None
            or content is None
            or content.kind not in _TEXT_KINDS
            or imported is None
            or getattr(imported, "fontSizePx", None) is None
            or not isinstance(target_metric, Mapping)
            or not isinstance(occluder_metric, Mapping)
        ):
            continue
        envelope = node.constraints or LayoutConstraints()
        allowed = set(envelope.allowedOperations or ())
        current_scale = node.fontScale
        minimum_scale = max(0.5, envelope.minFontScale)
        probe_scale = round(
            max(minimum_scale, current_scale * (1.0 - envelope.maxFontShrink)),
            6,
        )
        if (
            "setFontScale" not in allowed
            or not minimum_scale <= probe_scale < current_scale
        ):
            continue
        target_text = text_fragment_bounds(target_metric)
        occluder_text = text_fragment_bounds(occluder_metric)
        if target_text is None or occluder_text is None:
            continue
        target_right = target_text["x"] + target_text["width"]
        occluder_left = occluder_text["x"]
        projected_right = target_text["x"] + target_text["width"] * (
            probe_scale / current_scale
        )
        vertical_overlap = min(
            target_text["y"] + target_text["height"],
            occluder_text["y"] + occluder_text["height"],
        ) - max(target_text["y"], occluder_text["y"])
        fonts_ready = bool(normalized_metrics.get("fonts_ready")) and (
            target_metric.get("font_loaded") is True
            and occluder_metric.get("font_loaded") is True
        )
        candidate_separates = (
            vertical_overlap > 0.5
            and target_right > occluder_left + 0.5
            and projected_right <= occluder_left + 0.5
        )
        if not fonts_ready or not candidate_separates:
            continue
        glyph_fits.append(
            {
                "target": target,
                "currentScale": current_scale,
                "candidateScale": probe_scale,
                "minimumScale": minimum_scale,
                "mutationEnvelope": envelope.model_dump(mode="json"),
                "measurement": {
                    "source": "chrome",
                    "telemetryComplete": True,
                    "fontsReady": True,
                    "kind": "owned-text-separation",
                    "fontSizePx": finite_number(
                        target_metric.get("font_size"),
                        finite_number(getattr(imported, "fontSizePx", 0)),
                    ),
                    "measuredWidthPx": target_text["width"],
                    "measuredHeightPx": target_text["height"],
                    "availableWidthPx": occluder_left - target_text["x"],
                    "availableHeightPx": target_text["height"],
                    "targetTextBoundsPx": target_text,
                    "occluderTextBoundsPx": occluder_text,
                    "projectedTargetRightPx": projected_right,
                    "candidateSeparatesTextPaint": True,
                    "targetQid": qid,
                    "occluderQid": occluder_qid,
                    "overlapFraction": finite_number(detector.get("value")),
                },
                "roundTrip": {
                    "supported": True,
                    "verifiedProperties": ["font-size"],
                },
            }
        )
        glyph_targets.add(target)

    issue_endpoints = {
        str(issue["issueGroupId"]): tuple(
            dict.fromkeys(
                str(value)
                for value in (
                    issue.get("qid"),
                    (
                        issue.get("detectorEvidence", {}).get("occluderQid")
                        if isinstance(issue.get("detectorEvidence"), Mapping)
                        else None
                    ),
                    (
                        issue.get("detectorEvidence", {}).get(
                            "occluderBindingQid"
                        )
                        if isinstance(issue.get("detectorEvidence"), Mapping)
                        else None
                    ),
                )
                if value
            )
        )
        for issue in geometry_issues
    }
    coverage_signal_endpoints = tuple(
        tuple(
            dict.fromkeys(
                qid
                for qid in (
                    defect.qid or qid_for_semantic(defect.semantic_id),
                    *(
                        qid_for_semantic(semantic_id)
                        for semantic_id in defect.related_semantic_ids
                    ),
                )
                if qid
            )
        )
        for defect in evidence.defects
        if defect.code == "dom.unintended_overlap"
        and defect.related_semantic_ids
    )

    def connected_issue_groups(seed: str) -> tuple[str, ...]:
        remaining_groups = set(issue_endpoints) - {seed}
        connected = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            neighbors = {
                candidate
                for candidate in remaining_groups
                if any(
                    metric_qids_related(left, right)
                    for left in issue_endpoints.get(current, ())
                    for right in issue_endpoints.get(candidate, ())
                )
            }
            remaining_groups.difference_update(neighbors)
            connected.update(neighbors)
            frontier.extend(sorted(neighbors))
        return tuple(sorted(connected))

    for issue in geometry_issues:
        raw_pairs = issue.get("coordinatedAncestorMoves", ())
        if not isinstance(raw_pairs, Sequence) or isinstance(raw_pairs, (str, bytes)):
            continue
        for pair in raw_pairs:
            if not isinstance(pair, Mapping):
                continue
            proof = pair.get("proof")
            proof = proof if isinstance(proof, dict) else None
            if proof is None:
                continue
            coverage = proof.get("ownerCoverage")
            coverage = coverage if isinstance(coverage, dict) else None
            if coverage is None:
                continue
            target_coverage = coverage.get("target")
            peer_coverage = coverage.get("peer")
            target_coverage = (
                target_coverage if isinstance(target_coverage, dict) else None
            )
            peer_coverage = (
                peer_coverage if isinstance(peer_coverage, dict) else None
            )
            if target_coverage is None or peer_coverage is None:
                continue
            target_owner = str(target_coverage.get("ownerQid", ""))
            peer_owner = str(peer_coverage.get("ownerQid", ""))
            component = connected_issue_groups(str(issue["issueGroupId"]))
            connected_qids = tuple(
                dict.fromkeys(
                    qid
                    for group_id in component
                    for qid in issue_endpoints.get(group_id, ())
                )
            )
            closure = set(connected_qids)
            changed = True
            while changed:
                changed = False
                for signal in coverage_signal_endpoints:
                    if not any(
                        metric_qids_related(signal_qid, connected_qid)
                        for signal_qid in signal
                        for connected_qid in closure
                    ):
                        continue
                    before = len(closure)
                    closure.update(signal)
                    changed = changed or len(closure) != before
            connected_qids = tuple(sorted(closure))
            target_owned = tuple(
                qid
                for qid in connected_qids
                if metric_qid_is_ancestor(target_owner, qid)
            )
            peer_owned = tuple(
                qid
                for qid in connected_qids
                if metric_qid_is_ancestor(peer_owner, qid)
            )
            unowned = tuple(
                qid
                for qid in connected_qids
                if qid not in target_owned and qid not in peer_owned
            )
            target_coverage["ownedQids"] = target_owned
            target_coverage["ancestorQidChains"] = {
                qid: qid_chain_to_owner(qid, target_owner)
                for qid in target_owned
            }
            peer_coverage["ownedQids"] = peer_owned
            peer_coverage["ancestorQidChains"] = {
                qid: qid_chain_to_owner(qid, peer_owner)
                for qid in peer_owned
            }
            coverage["connectedIssueQids"] = connected_qids
            coverage["unownedConnectedIssueQids"] = unowned
            covered: list[str] = []
            for candidate_issue in geometry_issues:
                candidate_detector = candidate_issue.get("detectorEvidence")
                candidate_detector = (
                    candidate_detector
                    if isinstance(candidate_detector, Mapping)
                    else {}
                )
                candidate_target = str(candidate_issue.get("qid", ""))
                candidate_peer = str(
                    candidate_detector.get("occluderQid")
                    or candidate_detector.get("occluderBindingQid")
                    or ""
                )
                if not candidate_target or not candidate_peer:
                    continue
                split_across_owners = (
                    metric_qid_is_ancestor(target_owner, candidate_target)
                    and metric_qid_is_ancestor(peer_owner, candidate_peer)
                ) or (
                    metric_qid_is_ancestor(peer_owner, candidate_target)
                    and metric_qid_is_ancestor(target_owner, candidate_peer)
                )
                if split_across_owners:
                    covered.append(str(candidate_issue["issueGroupId"]))
            proof["coveredIssueGroupIds"] = tuple(dict.fromkeys(sorted(covered)))

    composable_groups = [
        str(issue["issueGroupId"])
        for issue in geometry_issues
        if issue.get("kind") == "nonOverlap"
        and isinstance(issue.get("detectorEvidence"), Mapping)
        and issue["detectorEvidence"].get("confirmed") is True
        and source is not None
    ]

    def redundant_aggregate_issue(issue: Mapping[str, Any]) -> bool:
        detector = issue.get("detectorEvidence")
        detector = detector if isinstance(detector, Mapping) else {}
        if int(finite_number(detector.get("browserIssueQidCount"), 1)) <= 1:
            return False
        endpoints = tuple(
            str(value)
            for value in (
                issue.get("qid"),
                detector.get("occluderBindingQid"),
            )
            if value
        )
        if len(endpoints) < 2:
            return False
        group_id = str(issue.get("issueGroupId", ""))
        other_targets = tuple(
            str(other.get("qid"))
            for other in geometry_issues
            if str(other.get("issueGroupId", "")) != group_id
            and other.get("kind") == "nonOverlap"
            and isinstance(other.get("detectorEvidence"), Mapping)
            and other["detectorEvidence"].get("confirmed") is True
            and other.get("qid")
        )
        return all(
            any(metric_qid_is_ancestor(endpoint, target) for target in other_targets)
            for endpoint in endpoints
        )

    required_geometry_issue_groups = tuple(
        sorted(
            {
            str(issue["issueGroupId"])
            for issue in geometry_issues
            if issue.get("kind") in {"nonOverlap", "containment"}
            and isinstance(issue.get("detectorEvidence"), Mapping)
            and issue["detectorEvidence"].get("confirmed") is True
            and issue.get("sameCoordinateSpace") is True
            and bool(issue.get("qid"))
            and bool(issue.get("target"))
            and (
                issue.get("code") == "dom.off_canvas"
                or not redundant_aggregate_issue(issue)
            )
            and (
                envelope_can_change(issue.get("mutationEnvelope"))
                or envelope_can_change(issue.get("otherMutationEnvelope"))
                or bool(issue.get("boundAncestors"))
                or bool(issue.get("otherBoundAncestors"))
            )
            }
        )
    )
    remaining = set(composable_groups)
    composition_components: list[tuple[str, ...]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        component = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            connected = {
                candidate
                for candidate in remaining
                if any(
                    metric_qids_related(left, right)
                    for left in composition_qids.get(current, ())
                    for right in composition_qids.get(candidate, ())
                )
            }
            remaining.difference_update(connected)
            component.update(connected)
            frontier.extend(sorted(connected))
        if len(component) > 1:
            composition_components.append(tuple(sorted(component)))
    geometry_composition_groups = {
        (
            "composition:"
            + digest_parts("geometry-composition/1", *component)
            .removeprefix("sha256:")[:16]
        ): component
        for component in sorted(composition_components)
    }
    if 2 <= len(required_geometry_issue_groups) <= 4:
        required_composition_id = (
            "composition:required:"
            + digest_parts(
                "geometry-required-composition/1",
                *required_geometry_issue_groups,
            ).removeprefix("sha256:")[:16]
        )
        geometry_composition_groups[required_composition_id] = (
            required_geometry_issue_groups
        )
    geometry_glyph_compositions: list[dict[str, Any]] = []
    if source is not None:
        for issue in geometry_issues:
            if issue.get("kind") != "nonOverlap":
                continue
            group_id = str(issue.get("issueGroupId", ""))
            detector = issue.get("detectorEvidence")
            detector = detector if isinstance(detector, Mapping) else {}
            if not group_id or detector.get("confirmed") is not True:
                continue
            owner_qids = tuple(
                str(ancestor.get("qid", ""))
                for key in ("boundAncestors", "otherBoundAncestors")
                for ancestor in (
                    issue.get(key) if isinstance(issue.get(key), Sequence) else ()
                )
                if isinstance(ancestor, Mapping) and ancestor.get("qid")
            )
            if not owner_qids:
                continue
            for content in spec.content:
                if content.kind != "title":
                    continue
                node = layout_by_semantic.get(content.semanticId)
                imported = binding_by_semantic.get(content.semanticId)
                if node is None or imported is None:
                    continue
                qid = imported.qid
                metric = metric_by_qid.get(qid)
                if (
                    not isinstance(metric, Mapping)
                    or metric.get("font_loaded") is not True
                    or not any(
                        metric_qid_is_ancestor(owner_qid, qid)
                        for owner_qid in owner_qids
                    )
                ):
                    continue
                envelope = node.constraints or LayoutConstraints()
                allowed = set(envelope.allowedOperations or ())
                current_scale = node.fontScale
                minimum_scale = max(0.5, envelope.minFontScale)
                candidate_scale = round(
                    max(minimum_scale, current_scale * 0.975), 6
                )
                fragments = tuple(
                    fragment
                    for fragment in metric.get("text_fragments", ())
                    if isinstance(fragment, Mapping)
                )
                text_bounds = text_fragment_bounds(metric)
                bbox = metric.get("bbox")
                bbox = bbox if isinstance(bbox, Mapping) else {}
                if (
                    "setFontScale" not in allowed
                    or getattr(imported, "fontSizePx", None) is None
                    or not minimum_scale <= candidate_scale < current_scale
                    or text_bounds is None
                    or not fragments
                ):
                    continue
                issue_qid = str(issue.get("qid", ""))
                peer_qid = str(
                    detector.get("occluderBindingQid")
                    or detector.get("occluderQid")
                    or ""
                )
                issue_node = layout_by_id.get(str(issue.get("target", "")))
                issue_semantic = str(issue.get("semanticId", ""))
                issue_binding = binding_by_semantic.get(issue_semantic)
                issue_metric = metric_by_qid.get(issue_qid)
                peer_metric = metric_by_qid.get(peer_qid)
                raw_ancestors = issue.get("boundAncestors")
                raw_ancestors = (
                    raw_ancestors
                    if isinstance(raw_ancestors, Sequence)
                    else ()
                )
                owner = next(
                    (
                        ancestor
                        for ancestor in raw_ancestors
                        if isinstance(ancestor, Mapping)
                        and metric_qid_is_ancestor(
                            str(ancestor.get("qid", "")), qid
                        )
                        and metric_qid_is_ancestor(
                            str(ancestor.get("qid", "")), issue_qid
                        )
                    ),
                    None,
                )
                owner_layout_id = str(owner.get("layoutId", "")) if owner else ""
                owner_semantic = str(owner.get("semanticId", "")) if owner else ""
                owner_qid = str(owner.get("qid", "")) if owner else ""
                owner_node = layout_by_id.get(owner_layout_id)
                owner_binding = binding_by_semantic.get(owner_semantic)
                owner_metric = metric_by_qid.get(owner_qid)
                target_bounds = detector.get("targetBoundsPx")
                target_bounds = (
                    target_bounds if isinstance(target_bounds, Mapping) else {}
                )
                peer_bounds = detector.get("occluderBoundsPx")
                peer_bounds = peer_bounds if isinstance(peer_bounds, Mapping) else {}
                font_size_px = finite_number(
                    metric.get("font_size"), getattr(imported, "fontSizePx", 0)
                )
                target_bottom = finite_number(target_bounds.get("y")) + finite_number(
                    target_bounds.get("height")
                )
                peer_top = finite_number(peer_bounds.get("y"))
                direct_delta_px = -float(max(1, math.ceil(font_size_px * 0.25)))
                total_delta_px = -float(
                    max(1, math.ceil(max(0.0, target_bottom - peer_top) + 3.0))
                )
                owner_delta_px = total_delta_px - direct_delta_px
                issue_envelope = (
                    issue_node.constraints if issue_node is not None else None
                )
                owner_envelope = (
                    owner_node.constraints if owner_node is not None else None
                )
                issue_text_bounds = (
                    text_fragment_bounds(issue_metric)
                    if isinstance(issue_metric, Mapping)
                    else None
                )
                peer_text_bounds = (
                    text_fragment_bounds(peer_metric)
                    if isinstance(peer_metric, Mapping)
                    else None
                )
                owner_bbox = (
                    owner_metric.get("bbox")
                    if isinstance(owner_metric, Mapping)
                    and isinstance(owner_metric.get("bbox"), Mapping)
                    else {}
                )
                moves_proven = (
                    owner is not None
                    and issue_node is not None
                    and issue_node.bounds is not None
                    and owner_node is not None
                    and owner_node.bounds is not None
                    and issue_binding is not None
                    and owner_binding is not None
                    and issue_binding.staticTranslationSafe is True
                    and owner_binding.staticTranslationSafe is True
                    and issue_envelope is not None
                    and owner_envelope is not None
                    and "translateWithinParent"
                    in set(issue_envelope.allowedOperations or ())
                    and "translateWithinParent"
                    in set(owner_envelope.allowedOperations or ())
                    and abs(direct_delta_px) / max(1.0, float(source.viewportHeight))
                    <= issue_envelope.maxTranslation + 1e-9
                    and abs(owner_delta_px) / max(1.0, float(source.viewportHeight))
                    <= owner_envelope.maxTranslation + 1e-9
                    and target_bottom > peer_top
                    and target_bottom + total_delta_px <= peer_top - 2.5
                    and finite_number(owner_bbox.get("y")) + owner_delta_px >= 0
                    and finite_number(owner_bbox.get("y"))
                    + owner_delta_px
                    + finite_number(owner_bbox.get("height"))
                    <= float(source.viewportHeight)
                )
                moves: tuple[dict[str, Any], ...] = ()
                move_proof: dict[str, Any] = {}
                if moves_proven:
                    moves = (
                        {
                            "role": "owner",
                            "target": owner_layout_id,
                            "layoutId": owner_layout_id,
                            "semanticId": owner_semantic,
                            "qid": owner_qid,
                            "currentBounds": owner_node.bounds.model_dump(mode="json"),
                            "mutationEnvelope": owner_envelope.model_dump(mode="json"),
                            "precondition": {
                                "expectedBounds": owner_node.bounds.model_dump(mode="json")
                            },
                            "delta": {
                                "x": 0.0,
                                "y": owner_delta_px / float(source.viewportHeight),
                            },
                            "deltaPx": {"x": 0.0, "y": owner_delta_px},
                            "staticTranslationSafe": True,
                            "roundTrip": {
                                "supported": True,
                                "verifiedProperties": ["bounds"],
                            },
                        },
                        {
                            "role": "subtitle",
                            "target": issue_node.layoutId,
                            "layoutId": issue_node.layoutId,
                            "semanticId": issue_semantic,
                            "qid": issue_qid,
                            "currentBounds": issue_node.bounds.model_dump(mode="json"),
                            "mutationEnvelope": issue_envelope.model_dump(mode="json"),
                            "precondition": {
                                "expectedBounds": issue_node.bounds.model_dump(mode="json")
                            },
                            "delta": {
                                "x": 0.0,
                                "y": direct_delta_px / float(source.viewportHeight),
                            },
                            "deltaPx": {"x": 0.0, "y": direct_delta_px},
                            "staticTranslationSafe": True,
                            "roundTrip": {
                                "supported": True,
                                "verifiedProperties": ["bounds"],
                            },
                        },
                    )
                    move_proof = {
                        "source": "chrome",
                        "telemetryComplete": True,
                        "candidateResolvesIssue": True,
                        "baselineSubtitleTextBoundsPx": issue_text_bounds,
                        "candidateSubtitleTextBoundsPx": (
                            {
                                **issue_text_bounds,
                                "y": issue_text_bounds["y"] + total_delta_px,
                            }
                            if issue_text_bounds is not None
                            else None
                        ),
                        "occluderTextBoundsPx": peer_text_bounds,
                        "occluderPaintBoundaryPx": {
                            "axis": "y",
                            "value": peer_top,
                        },
                        "ownerSubtreeBoundsPx": {
                            "baseline": dict(owner_bbox),
                            "candidate": {
                                **dict(owner_bbox),
                                "y": finite_number(owner_bbox.get("y"))
                                + owner_delta_px,
                            },
                        },
                        "canvasCapacity": {
                            "viewportHeightPx": float(source.viewportHeight),
                            "topClearancePx": finite_number(owner_bbox.get("y"))
                            + owner_delta_px,
                            "bottomClearancePx": float(source.viewportHeight)
                            - (
                                finite_number(owner_bbox.get("y"))
                                + owner_delta_px
                                + finite_number(owner_bbox.get("height"))
                            ),
                        },
                        "staticTranslationSafe": True,
                        "roundTrip": {
                            "supported": True,
                            "verifiedProperties": ["bounds", "font-size"],
                        },
                        # These are required candidate-stage observations, not
                        # baseline assertions.  The executor's rendered DOM,
                        # constraint, and source-roundtrip gates must populate
                        # and prove them before acceptance.
                        "requiredPostconditions": {
                            "domNoNewIssue": True,
                            "hardFailure": False,
                            "softCostMaximum": (
                                constraint_report.soft_cost
                                if constraint_report is not None
                                else 0.0
                            ),
                            "unknownHard": [],
                            "roundTripErrors": [],
                        },
                    }
                composition_id = (
                    "composition:glyph:"
                    + digest_parts(
                        "geometry-glyph-composition/1",
                        group_id,
                        node.layoutId,
                        str(candidate_scale),
                    ).removeprefix("sha256:")[:16]
                )
                geometry_glyph_compositions.append(
                    {
                        "compositionId": composition_id,
                        "issueGroupIds": (group_id,),
                        "required": group_id in required_geometry_issue_groups,
                        "moves": moves,
                        "proof": move_proof,
                        "glyph": {
                            "target": node.layoutId,
                            "layoutId": node.layoutId,
                            "semanticId": content.semanticId,
                            "qid": qid,
                            "currentScale": current_scale,
                            "minimumScale": minimum_scale,
                            "candidateScale": candidate_scale,
                            "mutationEnvelope": envelope.model_dump(mode="json"),
                            "precondition": {
                                "expectedBounds": (
                                    node.bounds.model_dump(mode="json")
                                    if node.bounds is not None
                                    else None
                                ),
                                "expectedScale": current_scale,
                            },
                            "measurement": {
                                "source": "chrome",
                                "telemetryComplete": True,
                                "fontsReady": True,
                                "kind": "range-bounds",
                                "fontSizePx": finite_number(
                                    metric.get("font_size"),
                                    getattr(imported, "fontSizePx", 0),
                                ),
                                "measuredWidthPx": text_bounds["width"],
                                "measuredHeightPx": text_bounds["height"],
                                "availableWidthPx": finite_number(
                                    bbox.get("width"), text_bounds["width"]
                                ),
                                "availableHeightPx": finite_number(
                                    bbox.get("height"), text_bounds["height"]
                                ),
                                "textBoundsPx": text_bounds,
                                "lineBoundsPx": tuple(dict(item) for item in fragments),
                            },
                            "roundTrip": {
                                "supported": True,
                                "verifiedProperties": ["font-size"],
                            },
                        },
                    }
                )
    geometry_exact_compositions: list[dict[str, Any]] = []
    geometry_exact_diagnostics: list[dict[str, Any]] = []
    if source is not None:
        viewport_width = float(source.viewportWidth)
        viewport_height = float(source.viewportHeight)

        def exact_vertical_move(
            qid: str, delta_px: float, role: str
        ) -> dict[str, Any] | None:
            semantic_id = semantic_by_qid.get(qid)
            node = layout_by_semantic.get(semantic_id) if semantic_id else None
            binding = binding_by_semantic.get(semantic_id) if semantic_id else None
            metric = metric_by_qid.get(qid)
            if (
                node is None
                or node.bounds is None
                or binding is None
                or not isinstance(metric, Mapping)
                or abs(delta_px) <= 1e-9
            ):
                return None
            envelope = node.constraints or LayoutConstraints()
            if (
                envelope.locked
                or "translateWithinParent"
                not in set(envelope.allowedOperations or ())
                or abs(delta_px) / viewport_height
                > envelope.maxTranslation + 1e-9
            ):
                return None
            projection: dict[str, Any]
            if binding.position == "static":
                probe = metric.get("static_translation_probe")
                probe = probe if isinstance(probe, Mapping) else {}
                interval = probe.get("safeTranslationIntervalPx")
                interval = interval if isinstance(interval, Mapping) else {}
                if (
                    binding.transform != "none"
                    or binding.staticTranslationSafe is not True
                    or probe.get("source") != "chrome"
                    or probe.get("telemetryComplete") is not True
                    or probe.get("stackingStable") is not True
                    or delta_px < finite_number(interval.get("minY")) - 1e-6
                    or delta_px > finite_number(interval.get("maxY")) + 1e-6
                ):
                    return None
                projection = {
                    "source": "chrome",
                    "telemetryComplete": True,
                    "mechanism": "static-transform",
                    "staticTranslationSafe": True,
                    "safeTranslationIntervalPx": dict(interval),
                }
            elif binding.position in {"relative", "absolute", "fixed", "sticky"}:
                proof = metric.get("containing_block_proof")
                proof = proof if isinstance(proof, Mapping) else {}
                vertical_anchor = (
                    "top"
                    if binding.topPx is not None
                    else "bottom" if binding.bottomPx is not None else None
                )
                if (
                    vertical_anchor is None
                    or proof.get("source") != "chrome"
                    or proof.get("telemetryComplete") is not True
                    or proof.get("exactBoundOffsetParent") is not True
                ):
                    return None
                projection = {
                    "source": "chrome",
                    "telemetryComplete": True,
                    "mechanism": "position-anchor",
                    "position": binding.position,
                    "verticalAnchor": vertical_anchor,
                    "offsetParentQid": proof.get("offsetParentQid"),
                }
            else:
                return None
            bbox = metric.get("bbox")
            if not isinstance(bbox, Mapping):
                return None
            return {
                "op": "translateWithinParent",
                "role": role,
                "target": node.layoutId,
                "layoutId": node.layoutId,
                "semanticId": semantic_id,
                "qid": qid,
                "currentBounds": node.bounds.model_dump(mode="json"),
                "precondition": {
                    "expectedBounds": node.bounds.model_dump(mode="json")
                },
                "value": {"x": 0.0, "y": delta_px / viewport_height},
                "valuePx": {"x": 0.0, "y": delta_px},
                "mutationEnvelope": envelope.model_dump(mode="json"),
                "projectionProof": projection,
                "roundTrip": {
                    "supported": True,
                    "verifiedProperties": ["bounds"],
                },
                "baselineBoundsPx": dict(bbox),
                "candidateBoundsPx": {
                    **dict(bbox),
                    "y": finite_number(bbox.get("y")) + delta_px,
                },
            }

        def exact_positioned_bounds(
            qid: str,
            candidate_bounds_px: Mapping[str, Any],
            role: str,
        ) -> dict[str, Any] | None:
            """Build a Chrome-anchored exact setBounds operation."""

            semantic_id = semantic_by_qid.get(qid)
            node = layout_by_semantic.get(semantic_id) if semantic_id else None
            binding = binding_by_semantic.get(semantic_id) if semantic_id else None
            metric = metric_by_qid.get(qid)
            if (
                node is None
                or node.bounds is None
                or binding is None
                or not isinstance(metric, Mapping)
                or binding.position not in {"relative", "absolute", "fixed", "sticky"}
            ):
                return None
            envelope = node.constraints or LayoutConstraints()
            allowed = set(envelope.allowedOperations or ())
            if envelope.locked or envelope.lockSize or "setBounds" not in allowed:
                return None
            proof = metric.get("containing_block_proof")
            proof = proof if isinstance(proof, Mapping) else {}
            vertical_anchor = (
                "top"
                if binding.topPx is not None
                else "bottom" if binding.bottomPx is not None else None
            )
            horizontal_anchor = (
                "left"
                if binding.leftPx is not None
                else "right" if binding.rightPx is not None else None
            )
            if (
                vertical_anchor is None
                or horizontal_anchor is None
                or proof.get("source") != "chrome"
                or proof.get("telemetryComplete") is not True
                or proof.get("exactBoundOffsetParent") is not True
            ):
                return None
            bbox = metric.get("bbox")
            if not isinstance(bbox, Mapping):
                return None
            candidate = {
                "x": finite_number(candidate_bounds_px.get("x")) / viewport_width,
                "y": finite_number(candidate_bounds_px.get("y")) / viewport_height,
                "width": finite_number(candidate_bounds_px.get("width"))
                / viewport_width,
                "height": finite_number(candidate_bounds_px.get("height"))
                / viewport_height,
            }
            current = node.bounds.model_dump(mode="json")
            position_delta = max(
                abs(candidate["x"] - current["x"]),
                abs(candidate["y"] - current["y"]),
            )
            size_delta = max(
                abs(candidate["width"] - current["width"]),
                abs(candidate["height"] - current["height"]),
            )
            if (
                position_delta > envelope.maxTranslation + 1e-9
                or size_delta > envelope.maxResizeDelta + 1e-9
                or candidate["x"] < 0
                or candidate["y"] < 0
                or candidate["x"] + candidate["width"] > 1 + 1e-9
                or candidate["y"] + candidate["height"] > 1 + 1e-9
            ):
                return None
            return {
                "op": "setBounds",
                "role": role,
                "target": node.layoutId,
                "layoutId": node.layoutId,
                "semanticId": semantic_id,
                "qid": qid,
                "currentBounds": current,
                "precondition": {"expectedBounds": current},
                "bounds": candidate,
                "boundsPx": dict(candidate_bounds_px),
                "mutationEnvelope": envelope.model_dump(mode="json"),
                "projectionProof": {
                    "source": "chrome",
                    "telemetryComplete": True,
                    "mechanism": "position-anchor",
                    "position": binding.position,
                    "verticalAnchor": vertical_anchor,
                    "horizontalAnchor": horizontal_anchor,
                    "offsetParentQid": proof.get("offsetParentQid"),
                },
                "roundTrip": {
                    "supported": True,
                    "verifiedProperties": ["bounds"],
                },
                "baselineBoundsPx": dict(bbox),
                "candidateBoundsPx": dict(candidate_bounds_px),
            }

        required_postconditions = {
            "domNoNewIssue": True,
            "hardFailure": False,
            "softCostMaximum": (
                constraint_report.soft_cost
                if constraint_report is not None
                else 0.0
            ),
            "unknownHard": [],
            "roundTripErrors": [],
        }

        # Exact bottom-stack repair: an off-canvas bound node and its immediately
        # preceding bound sibling move together, preserving their measured gap.
        for issue in geometry_issues:
            if issue.get("code") != "dom.off_canvas":
                continue
            detector = issue.get("detectorEvidence")
            detector = detector if isinstance(detector, Mapping) else {}
            target_qid = str(issue.get("qid", ""))
            target_metric = metric_by_qid.get(target_qid)
            target_bbox = (
                target_metric.get("bbox")
                if isinstance(target_metric, Mapping)
                and isinstance(target_metric.get("bbox"), Mapping)
                else None
            )
            if detector.get("confirmed") is not True or target_bbox is None:
                continue
            overflow_px = finite_number(target_bbox.get("y")) + finite_number(
                target_bbox.get("height")
            ) - viewport_height
            if overflow_px <= 0:
                continue
            parent_qid = str(target_metric.get("parent_qid", ""))
            target_top = finite_number(target_bbox.get("y"))
            target_width = finite_number(target_bbox.get("width"))
            predecessors: list[tuple[float, str, Mapping[str, Any]]] = []
            for candidate_qid, candidate_metric in metric_by_qid.items():
                if (
                    candidate_qid == target_qid
                    or str(candidate_metric.get("parent_qid", "")) != parent_qid
                    or candidate_qid not in semantic_by_qid
                ):
                    continue
                candidate_bbox = candidate_metric.get("bbox")
                if not isinstance(candidate_bbox, Mapping):
                    continue
                candidate_bottom = finite_number(candidate_bbox.get("y")) + finite_number(
                    candidate_bbox.get("height")
                )
                horizontal_overlap = min(
                    finite_number(candidate_bbox.get("x"))
                    + finite_number(candidate_bbox.get("width")),
                    finite_number(target_bbox.get("x")) + target_width,
                ) - max(
                    finite_number(candidate_bbox.get("x")),
                    finite_number(target_bbox.get("x")),
                )
                if (
                    candidate_bottom <= target_top + 0.5
                    and horizontal_overlap >= 0.8 * min(
                        finite_number(candidate_bbox.get("width")), target_width
                    )
                ):
                    predecessors.append((candidate_bottom, candidate_qid, candidate_bbox))
            if not predecessors:
                continue
            predecessor_bottom, predecessor_qid, predecessor_bbox = max(predecessors)
            if target_top - predecessor_bottom > 2.0:
                continue
            delta_px = -float(math.ceil(overflow_px - 1e-9))
            moves = tuple(
                move
                for move in (
                    exact_vertical_move(predecessor_qid, delta_px, "preceding-sibling"),
                    exact_vertical_move(target_qid, delta_px, "off-canvas-target"),
                )
                if move is not None
            )
            if len(moves) != 2:
                continue
            candidate_target = moves[1]["candidateBoundsPx"]
            candidate_predecessor = moves[0]["candidateBoundsPx"]
            if (
                finite_number(candidate_target.get("y"))
                + finite_number(candidate_target.get("height"))
                > viewport_height + 1e-6
            ):
                continue
            group_ids = tuple(
                sorted(
                    {
                    str(candidate["issueGroupId"])
                    for candidate in geometry_issues
                    if candidate.get("issueGroupId")
                    and (
                        str(candidate.get("qid", "")) == target_qid
                        or metric_qid_is_ancestor(
                            target_qid, str(candidate.get("qid", ""))
                        )
                    )
                    }
                )
            )
            diagnostic_refs = tuple(
                dict.fromkeys(
                    ref
                    for candidate in geometry_issues
                    if str(candidate.get("issueGroupId", "")) in group_ids
                    for ref in refs_by_target.get(str(candidate.get("target", "")), ())
                )
            )
            composition_id = (
                "composition:exact:"
                + digest_parts(
                    "geometry-exact-bottom-stack/1",
                    target_qid,
                    predecessor_qid,
                    str(delta_px),
                ).removeprefix("sha256:")[:16]
            )
            geometry_exact_compositions.append(
                {
                    "compositionId": composition_id,
                    "issueGroupIds": group_ids,
                    # Exact off-canvas containment is itself a required target
                    # identity even though the legacy required-group list is
                    # limited to non-overlap compositions.
                    "required": True,
                    "diagnosticRefs": diagnostic_refs,
                    "operations": moves,
                    "proof": {
                        "source": "chrome",
                        "telemetryComplete": True,
                        "candidateResolvesIssue": True,
                        "baselineBoundsPx": {
                            predecessor_qid: dict(predecessor_bbox),
                            target_qid: dict(target_bbox),
                        },
                        "candidateBoundsPx": {
                            move["qid"]: move["candidateBoundsPx"] for move in moves
                        },
                        "relationships": (
                            {
                                "kind": "adjacency",
                                "axis": "y",
                                "firstQid": predecessor_qid,
                                "secondQid": target_qid,
                                "baselineGapPx": target_top - predecessor_bottom,
                                "candidateGapPx": target_top - predecessor_bottom,
                            },
                            {
                                "kind": "canvasContainment",
                                "axis": "y",
                                "firstQid": target_qid,
                                "boundaryPx": viewport_height,
                            },
                        ),
                        "requiredPostconditions": required_postconditions,
                    },
                }
            )

        # Exact vertical packing repair: two or more independently diagnosed
        # descendants share a bound warning owner and the same bound occluder.
        overlap_clusters: dict[str, list[Mapping[str, Any]]] = {}
        for issue in geometry_issues:
            detector = issue.get("detectorEvidence")
            detector = detector if isinstance(detector, Mapping) else {}
            occluder_qid = str(
                detector.get("occluderBindingQid")
                or detector.get("occluderQid")
                or ""
            )
            raw_other_owners = issue.get("otherBoundAncestors")
            raw_other_owners = (
                raw_other_owners
                if isinstance(raw_other_owners, Sequence)
                else ()
            )
            occluder_qid = next(
                (
                    str(owner.get("qid", ""))
                    for owner in raw_other_owners
                    if isinstance(owner, Mapping)
                    and str(owner.get("qid", "")) in semantic_by_qid
                ),
                occluder_qid,
            )
            if (
                issue.get("kind") == "nonOverlap"
                and detector.get("confirmed") is True
                and occluder_qid in semantic_by_qid
            ):
                overlap_clusters.setdefault(occluder_qid, []).append(issue)
        for occluder_qid, cluster in overlap_clusters.items():
            target_qids = tuple(
                dict.fromkeys(str(item.get("qid", "")) for item in cluster if item.get("qid"))
            )
            if len(target_qids) < 2:
                geometry_exact_diagnostics.append(
                    {"stage": "cluster", "reason": "insufficient_targets", "occluderQid": occluder_qid}
                )
                continue
            common_owners = [
                candidate_qid
                for candidate_qid in semantic_by_qid
                if all(
                    metric_qid_is_ancestor(candidate_qid, target_qid)
                    for target_qid in target_qids
                )
                and not metric_qid_is_ancestor(candidate_qid, occluder_qid)
            ]
            common_owners.sort(
                key=lambda candidate_qid: max(
                    len(qid_chain_to_owner(target_qid, candidate_qid))
                    for target_qid in target_qids
                )
            )
            warning_qid = next(
                (
                    candidate_qid
                    for candidate_qid in common_owners
                    if isinstance(metric_by_qid.get(candidate_qid), Mapping)
                    and metric_by_qid[candidate_qid].get("has_owned_paint") is True
                ),
                None,
            )
            warning_metric = metric_by_qid.get(warning_qid or "")
            occluder_metric = metric_by_qid.get(occluder_qid)
            if not isinstance(warning_metric, Mapping) or not isinstance(
                occluder_metric, Mapping
            ):
                geometry_exact_diagnostics.append(
                    {"stage": "owners", "reason": "bound_owner_missing", "occluderQid": occluder_qid, "warningQid": warning_qid}
                )
                continue
            warning_bbox = warning_metric.get("bbox")
            occluder_bbox = occluder_metric.get("bbox")
            if not isinstance(warning_bbox, Mapping) or not isinstance(
                occluder_bbox, Mapping
            ):
                geometry_exact_diagnostics.append(
                    {"stage": "owners", "reason": "bbox_missing", "occluderQid": occluder_qid, "warningQid": warning_qid}
                )
                continue
            parent_qid = str(warning_metric.get("parent_qid", ""))
            siblings: list[tuple[float, str, Mapping[str, Any]]] = []
            for sibling_qid, sibling_metric in metric_by_qid.items():
                sibling_bbox = sibling_metric.get("bbox")
                if (
                    str(sibling_metric.get("parent_qid", "")) != parent_qid
                    or sibling_qid not in semantic_by_qid
                    or not isinstance(sibling_bbox, Mapping)
                ):
                    continue
                if (
                    abs(finite_number(sibling_bbox.get("x")) - finite_number(warning_bbox.get("x"))) <= 1
                    and abs(finite_number(sibling_bbox.get("width")) - finite_number(warning_bbox.get("width"))) <= 1
                    and finite_number(sibling_bbox.get("y"))
                    <= finite_number(warning_bbox.get("y"))
                ):
                    siblings.append(
                        (finite_number(sibling_bbox.get("y")), sibling_qid, sibling_bbox)
                    )
            siblings.sort()
            sibling_qids = [item[1] for item in siblings]
            if warning_qid not in sibling_qids or sibling_qids.index(warning_qid) < 2:
                geometry_exact_diagnostics.append(
                    {"stage": "packing", "reason": "preceding_stack_unproven", "occluderQid": occluder_qid, "warningQid": warning_qid, "siblingQids": tuple(sibling_qids)}
                )
                continue
            stack_items = siblings[sibling_qids.index(warning_qid) - 2 : sibling_qids.index(warning_qid) + 1]
            probe = occluder_metric.get("static_translation_probe")
            probe = probe if isinstance(probe, Mapping) else {}
            containment = probe.get("containmentBoundsPx")
            containment = containment if isinstance(containment, Mapping) else {}
            interval = probe.get("safeTranslationIntervalPx")
            interval = interval if isinstance(interval, Mapping) else {}
            containment_source_ok = (
                probe.get("source") == "chrome"
                and probe.get("telemetryComplete") is True
            )
            if not containment:
                containing_block = occluder_metric.get("containing_block_proof")
                containing_block = (
                    containing_block
                    if isinstance(containing_block, Mapping)
                    else {}
                )
                raw_bounds = containing_block.get("offsetParentBoundsPx")
                containment = raw_bounds if isinstance(raw_bounds, Mapping) else {}
                containment_source_ok = (
                    containing_block.get("source") == "chrome"
                    and containing_block.get("telemetryComplete") is True
                    and containing_block.get("exactBoundOffsetParent") is True
                )
            boundary_bottom = finite_number(containment.get("y")) + finite_number(
                containment.get("height")
            )
            occluder_bottom = finite_number(occluder_bbox.get("y")) + finite_number(
                occluder_bbox.get("height")
            )
            occluder_width = finite_number(occluder_bbox.get("width"))
            occluder_height = finite_number(occluder_bbox.get("height"))
            # Wide responsibility bands can safely trade unused vertical chrome
            # for the hidden warning while preserving their original bottom
            # inset.  The exact candidate still has to pass Chrome DOM,
            # round-trip, OCR, and VQA; narrower/taller blocks remain fail closed.
            if (
                occluder_height < 48.0
                or occluder_height > 96.0
                or occluder_width / max(1.0, occluder_height) < 8.0
            ):
                geometry_exact_diagnostics.append(
                    {
                        "stage": "capacity",
                        "reason": "compact_band_shape_unproven",
                        "occluderQid": occluder_qid,
                    }
                )
                continue
            compact_height = max(
                40.0,
                float(math.ceil(occluder_height * 2.0 / 3.0)),
            )
            compact_height = min(compact_height, occluder_height - 1.0)
            occluder_candidate_top = occluder_bottom - compact_height
            occluder_delta = occluder_candidate_top - finite_number(
                occluder_bbox.get("y")
            )
            if (
                not containment_source_ok
                or occluder_delta <= 0
                or (
                    interval
                    and occluder_delta
                    > finite_number(interval.get("maxY")) + 1e-6
                )
            ):
                geometry_exact_diagnostics.append(
                    {"stage": "capacity", "reason": "occluder_capacity_unproven", "occluderQid": occluder_qid, "deltaPx": occluder_delta, "containmentSourceOk": containment_source_ok, "interval": dict(interval)}
                )
                continue
            warning_bottom = finite_number(warning_bbox.get("y")) + finite_number(
                warning_bbox.get("height")
            )
            minimum_stack_gap_px = 1.0
            deltas: dict[str, float] = {
                str(warning_qid): (
                    occluder_candidate_top
                    - minimum_stack_gap_px
                    - warning_bottom
                )
            }
            below_candidate_top = finite_number(warning_bbox.get("y")) + deltas[str(warning_qid)]
            for _y, sibling_qid, sibling_bbox in reversed(stack_items[:-1]):
                sibling_bottom = finite_number(sibling_bbox.get("y")) + finite_number(
                    sibling_bbox.get("height")
                )
                delta = -float(
                    math.ceil(
                        max(
                            0.0,
                            sibling_bottom
                            + minimum_stack_gap_px
                            - below_candidate_top,
                        )
                    )
                )
                deltas[sibling_qid] = delta
                below_candidate_top = finite_number(sibling_bbox.get("y")) + delta
            ordered_move_data = [
                (stack_items[0][1], deltas[stack_items[0][1]], "upper-stack"),
                (stack_items[1][1], deltas[stack_items[1][1]], "middle-stack"),
                (str(warning_qid), deltas[str(warning_qid)], "warning-owner"),
            ]
            translated_moves = tuple(
                move
                for qid, delta, role in ordered_move_data
                for move in (exact_vertical_move(qid, delta, role),)
                if move is not None
            )
            compact_band = exact_positioned_bounds(
                occluder_qid,
                {
                    "x": finite_number(occluder_bbox.get("x")),
                    "y": occluder_candidate_top,
                    "width": occluder_width,
                    "height": compact_height,
                },
                "occluder-band",
            )
            moves = (
                *translated_moves,
                *((compact_band,) if compact_band is not None else ()),
            )
            if len(moves) != 4:
                geometry_exact_diagnostics.append(
                    {"stage": "projection", "reason": "move_proof_incomplete", "occluderQid": occluder_qid, "provedQids": tuple(move["qid"] for move in moves), "requested": tuple((qid, delta) for qid, delta, _role in ordered_move_data)}
                )
                continue
            candidate_bounds = {
                move["qid"]: move["candidateBoundsPx"] for move in moves
            }
            relationships: list[dict[str, Any]] = []
            for first, second in zip(moves, moves[1:3]):
                first_box = first["candidateBoundsPx"]
                second_box = second["candidateBoundsPx"]
                gap = finite_number(second_box.get("y")) - (
                    finite_number(first_box.get("y"))
                    + finite_number(first_box.get("height"))
                )
                if gap < -1e-6:
                    break
                relationships.append(
                    {
                        "kind": "nonOverlap",
                        "axis": "y",
                        "firstQid": first["qid"],
                        "secondQid": second["qid"],
                        "candidateGapPx": gap,
                    }
                )
            else:
                warning_candidate = moves[2]["candidateBoundsPx"]
                band_candidate = moves[3]["candidateBoundsPx"]
                warning_band_gap = finite_number(band_candidate.get("y")) - (
                    finite_number(warning_candidate.get("y"))
                    + finite_number(warning_candidate.get("height"))
                )
                if warning_band_gap < -1e-6:
                    continue
                relationships.extend(
                    (
                        {
                            "kind": "nonOverlap",
                            "axis": "y",
                            "firstQid": moves[2]["qid"],
                            "secondQid": moves[3]["qid"],
                            "candidateGapPx": warning_band_gap,
                        },
                        {
                            "kind": "canvasContainment",
                            "axis": "y",
                            "firstQid": moves[3]["qid"],
                            "boundaryPx": boundary_bottom,
                        },
                    )
                )
                group_ids = tuple(
                    sorted(
                        {
                        str(item["issueGroupId"])
                        for item in cluster
                        if item.get("issueGroupId")
                        }
                    )
                )
                diagnostic_refs = tuple(
                    dict.fromkeys(
                        ref
                        for item in cluster
                        for ref in refs_by_target.get(str(item.get("target", "")), ())
                    )
                )
                composition_id = (
                    "composition:exact:"
                    + digest_parts(
                        "geometry-exact-vertical-pack/1",
                        *group_ids,
                        *(f"{qid}:{delta}" for qid, delta, _role in ordered_move_data),
                        f"{occluder_qid}:{occluder_candidate_top}:{compact_height}",
                    ).removeprefix("sha256:")[:16]
                )
                geometry_exact_compositions.append(
                    {
                        "compositionId": composition_id,
                        "issueGroupIds": group_ids,
                        "required": bool(
                            set(group_ids).intersection(required_geometry_issue_groups)
                        ),
                        "diagnosticRefs": diagnostic_refs,
                        "operations": moves,
                        "proof": {
                            "source": "chrome",
                            "telemetryComplete": True,
                            "candidateResolvesIssue": True,
                            "baselineBoundsPx": {
                                move["qid"]: move["baselineBoundsPx"] for move in moves
                            },
                            "candidateBoundsPx": candidate_bounds,
                            "relationships": tuple(relationships),
                            "requiredPostconditions": required_postconditions,
                            "preservedBottomInsetPx": boundary_bottom
                            - occluder_bottom,
                            "minimumStackGapPx": minimum_stack_gap_px,
                        },
                    }
                )
    return {
        "slideId": spec.slideId,
        "baseSpecRevision": spec.specRevision,
        "baseSpecHash": spec.digests.spec,
        "expectedContentDigest": spec.digests.content,
        "diagnosticRefs": tuple(dict.fromkeys(refs)),
        "diagnosticRefsByTarget": {
            target: tuple(dict.fromkeys(target_refs))
            for target, target_refs in refs_by_target.items()
        },
        "containerBounds": bounds,
        "fontScale": fonts,
        "glyphFit": glyph_fits,
        "imageCrop": image_crops,
        "staticTranslation": static_translations,
        "connectorRoute": connector_routes,
        "geometryIssues": geometry_issues,
        "geometryIssueGroups": geometry_issue_groups,
        "geometryCompositionGroups": geometry_composition_groups,
        "geometryGlyphCompositions": tuple(geometry_glyph_compositions),
        "geometryExactCompositions": tuple(geometry_exact_compositions),
        "geometryExactDiagnostics": tuple(geometry_exact_diagnostics),
        "requiredGeometryIssueGroups": required_geometry_issue_groups,
        "maxGeometryCompositions": 4,
        "relationalConstraints": tuple(
            constraint.model_dump(mode="json")
            for constraint in spec.constraints
            if affected_targets.intersection(constraint_targets(constraint))
        ),
        "limits": {"maxOperations": 8, "maxAffectedBlocks": 4},
    }


def _token_recall(tokens: Sequence[str], text: str | None) -> float:
    if not tokens:
        return 1.0
    normalized = re.sub(r"\s+", "", normalize_ocr_text(text or "")).casefold()
    found = sum(
        re.sub(r"\s+", "", normalize_ocr_text(token)).casefold() in normalized
        for token in tokens
    )
    return found / len(tokens)


def _target_issue_evidence(
    *,
    request: RenderRequest,
    plan: RepairPlan,
    baseline_dom: DomEvidence,
    candidate_dom: DomEvidence,
    baseline_constraints: ConstraintReport,
    candidate_constraints: ConstraintReport,
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
    qid_by_semantic = _qid_to_semantic(request)
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


def _target_evidence_payload(
    deltas: Sequence[TargetIssueDelta] | None,
) -> list[dict[str, Any]]:
    """Return the canonical, text-free evidence shape consumed by replay."""

    return [
        {
            "stage": delta.identity.stage,
            "code": delta.identity.code,
            "semantic_id": delta.identity.semantic_id,
            "qid": delta.identity.qid,
            "constraint_id": delta.identity.constraint_id,
            "before": delta.before,
            "after": delta.after,
            "resolution_threshold": delta.resolution_threshold,
            "tolerance": delta.tolerance,
            "direction": delta.direction,
        }
        for delta in (deltas or ())
    ]


def _confirmed_issue_payload(evidence: DomEvidence) -> list[dict[str, Any]]:
    """Project physical user issues while retaining every raw defect."""

    grouped: dict[str, list[DomDefect]] = {}
    for defect in evidence.defects:
        if defect.code == "constraint.evidence.missing" or defect.severity not in {
            "high",
            "medium",
        }:
            continue
        identity = defect.physical_issue_id or ":".join(
            (defect.code, defect.semantic_id, defect.qid or "unavailable")
        )
        grouped.setdefault(identity, []).append(defect)
    result: list[dict[str, Any]] = []
    for identity, raw_defects in grouped.items():
        representative = min(
            raw_defects,
            key=lambda item: (
                0 if item.code.startswith("dom.") else 1,
                item.code,
                item.semantic_id,
                item.qid or "",
            ),
        )
        result.append(
            {
                "code": representative.code,
                "severity": representative.severity,
                "semantic_id": representative.semantic_id,
                "qid": representative.qid,
                "related_semantic_ids": list(
                    representative.related_semantic_ids
                ),
                "physical_issue_id": identity,
                "diagnostics": dict(representative.diagnostics),
                "raw_defects": [
                    {
                        "code": defect.code,
                        "severity": defect.severity,
                        "semantic_id": defect.semantic_id,
                        "qid": defect.qid,
                        "related_semantic_ids": list(
                            defect.related_semantic_ids
                        ),
                        "diagnostics": dict(defect.diagnostics),
                    }
                    for defect in raw_defects
                ],
            }
        )
    return result


def _evidence_atom_payload(evidence: DomEvidence) -> list[dict[str, Any]]:
    """Expose actionable atom facts without flooding the review with clean probes."""

    return [
        {
            "sensor": atom.sensor,
            "metric": atom.metric,
            "stage": atom.stage,
            "state": atom.state,
            "qid": atom.qid,
            "semantic_id": atom.semantic_id,
            "constraint_id": atom.constraint_id,
            "reason": atom.reason_code,
            "reason_code": atom.reason_code,
            "dependency_refs": list(atom.dependency_refs),
            "diagnostics": dict(atom.diagnostics),
        }
        for atom in evidence.atoms
        if atom.state in {"proven_issue", "unknown"}
    ]


def _dependency_blocker_payload(
    outcomes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Lift scoped fail-closed blockers out of candidate diagnostics for the UI."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for outcome in outcomes:
        diagnostics = outcome.get("diagnostics")
        if not isinstance(diagnostics, Mapping):
            continue
        telemetry = diagnostics.get("dom_telemetry")
        if not isinstance(telemetry, Mapping):
            continue
        blockers = telemetry.get("dependency_blockers")
        if not isinstance(blockers, Sequence) or isinstance(blockers, (str, bytes)):
            continue
        for blocker in blockers:
            if not isinstance(blocker, Mapping):
                continue
            reason = blocker.get("reason_code") or "target_dependency_unknown"
            payload = {
                "sensor": blocker.get("sensor"),
                "metric": blocker.get("metric"),
                "stage": blocker.get("stage"),
                "qid": blocker.get("qid"),
                "semantic_id": blocker.get("semantic_id"),
                "constraint_id": blocker.get("constraint_id"),
                "reason": reason,
                "reason_code": reason,
                "dependency_refs": list(blocker.get("dependency_refs") or ()),
                "diagnostics": dict(blocker.get("diagnostics") or {}),
            }
            identity = json.dumps(payload, sort_keys=True, default=str)
            if identity not in seen:
                seen.add(identity)
                result.append(payload)
    return result


def _candidate_state_key(render_input_hash: str, plan_id: str) -> str:
    """Keep plan-local evidence distinct when two plans render identical HTML."""

    return f"{render_input_hash}:{plan_id}"


def _store_candidate_state(
    candidates: dict[str, _CandidateState],
    render_input_hash: str,
    state: _CandidateState,
) -> str:
    """Store one rendered state without aliasing another plan's evidence."""

    candidate_key = _candidate_state_key(render_input_hash, state.plan.plan_id)
    candidates[candidate_key] = state
    return candidate_key


def _operation_change_payload(plan: RepairPlan) -> list[dict[str, Any]]:
    """Serialize text-free, replayable attributes changed by a repair plan."""

    changes: list[dict[str, Any]] = []
    for operation in plan.operations:
        raw = operation.model_dump(mode="json", by_alias=True, exclude_none=True)
        op_name = str(raw.get("op", ""))
        target = str(raw.get("target", ""))
        precondition = raw.get("precondition")
        before: Any = None
        expected_bounds: Mapping[str, Any] | None = None
        if isinstance(precondition, Mapping):
            candidate_bounds = precondition.get("expectedBounds")
            if isinstance(candidate_bounds, Mapping):
                expected_bounds = candidate_bounds
                before = dict(candidate_bounds)
            elif "expectedValue" in precondition:
                before = precondition.get("expectedValue")

        attribute = op_name
        after: Any = None
        if op_name == "translateWithinParent":
            attribute = "bounds"
            delta = raw.get("value")
            if expected_bounds is not None and isinstance(delta, Mapping):
                after = dict(expected_bounds)
                after["x"] = float(expected_bounds.get("x", 0.0)) + float(
                    delta.get("x", 0.0)
                )
                after["y"] = float(expected_bounds.get("y", 0.0)) + float(
                    delta.get("y", 0.0)
                )
            else:
                after = delta
        elif op_name == "setBounds":
            attribute = "bounds"
            after = raw.get("bounds")
        elif op_name == "resizeWithinBounds":
            attribute = "bounds"
            after = dict(expected_bounds or {})
            after.update(
                {
                    "width": raw.get("width"),
                    "height": raw.get("height"),
                }
            )
        elif op_name == "setFontScale":
            attribute = "fontScale"
            after = raw.get("scale")
        elif op_name == "setSpacing":
            attribute = "gap"
            after = raw.get("gap")
        elif op_name == "setPadding":
            attribute = "padding"
            after = raw.get("padding")
        else:
            after = raw.get("value")

        changes.append(
            {
                "operation": op_name,
                "target": target,
                "attribute": attribute,
                "before": before,
                "after": after,
            }
        )
    return changes


def _canonical_terminal_outcome(
    outcomes: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Return the pipeline-selected outcome, falling back only for old payloads."""

    return next(
        (
            outcome
            for outcome in reversed(outcomes)
            if outcome.get("selected") is True
        ),
        outcomes[-1] if outcomes else {},
    )


def _canonical_outcome_projection(
    outcomes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project lifecycle and proof from every accepted incremental selection."""

    terminal = _canonical_terminal_outcome(outcomes)
    selected = [
        outcome
        for outcome in outcomes
        if outcome.get("selected") is True
        and outcome.get("validation_status") == "accepted"
    ]
    if not selected and terminal:
        selected = [terminal]
    target_evidence: list[Any] = []
    element_changes: list[Any] = []
    for outcome in selected:
        diagnostics = outcome.get("diagnostics", {})
        diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
        evidence = diagnostics.get("target_evidence", [])
        changes = diagnostics.get("repair_operations", [])
        if isinstance(evidence, list):
            target_evidence.extend(evidence)
        if isinstance(changes, list):
            element_changes.extend(changes)
    return {
        "terminal": terminal,
        "accepted": terminal.get("validation_status") == "accepted",
        "apply_status": terminal.get("apply_status", "not_started"),
        "publish_status": terminal.get("publish_status", "not_started"),
        "post_verify_status": terminal.get("post_verify_status", "not_started"),
        "target_evidence": target_evidence,
        "element_changes": element_changes,
    }


def _merge_incremental_slide_results(
    passes: Sequence[Mapping[str, Any]],
    *,
    complete: bool,
) -> dict[str, Any]:
    """Merge one slide's bounded CAS repair chain without losing provenance."""

    if not passes:
        raise ValueError("incremental repair requires at least one inspection pass")
    merged = dict(passes[-1])
    merged["trigger"] = next(
        (item.get("trigger") for item in passes if item.get("trigger")), None
    )
    merged["applied"] = any(bool(item.get("applied")) for item in passes)
    merged["rolled_back"] = any(bool(item.get("rolled_back")) for item in passes)
    merged["outcomes"] = [
        outcome
        for item in passes
        for outcome in item.get("outcomes", [])
        if isinstance(outcome, Mapping)
    ]
    merged["candidate_count"] = sum(
        int(item.get("candidate_count", 0)) for item in passes
    )
    merged["projection_rejections"] = [
        rejection
        for item in passes
        for rejection in item.get("projection_rejections", [])
    ]
    merged["no_candidate"] = list(passes[-1].get("no_candidate", []))
    merged["reason_codes"] = list(
        dict.fromkeys(
            str(code)
            for item in passes
            for code in item.get("reason_codes", [])
        )
    )
    merged["confirmed_issue_count"] = max(
        int(item.get("confirmed_issue_count", 0)) for item in passes
    )
    merged["incomplete_measurement_count"] = max(
        int(item.get("incomplete_measurement_count", 0)) for item in passes
    )
    merged["unresolved_confirmed_issue_count"] = (
        0
        if complete
        else int(passes[-1].get("unresolved_confirmed_issue_count", 0))
    )
    merged["clean_slide_count"] = int(
        passes[-1].get("clean_slide_count", 0)
    )
    confirmed_issues: dict[str, Mapping[str, Any]] = {}
    for item in passes:
        for issue in item.get("confirmed_issues", []):
            if not isinstance(issue, Mapping):
                continue
            identity = str(
                issue.get("physical_issue_id")
                or ":".join(
                    (
                        str(issue.get("code", "")),
                        str(issue.get("semantic_id", "")),
                        str(issue.get("qid", "")),
                    )
                )
            )
            confirmed_issues.setdefault(identity, issue)
    merged["confirmed_issues"] = [dict(item) for item in confirmed_issues.values()]
    if confirmed_issues:
        merged["confirmed_issue_count"] = len(confirmed_issues)
    merged["incomplete_measurements"] = list(
        passes[-1].get("incomplete_measurements", [])
    )
    merged["total_ms"] = round(
        sum(float(item.get("total_ms", 0.0)) for item in passes), 3
    )

    telemetry: dict[str, Any] = {}
    for item in passes:
        current = item.get("telemetry", {})
        if not isinstance(current, Mapping):
            continue
        for key, value in current.items():
            if key == "stage_timings_ms" and isinstance(value, Mapping):
                stages = telemetry.setdefault(key, {})
                for stage, duration in value.items():
                    stages[str(stage)] = stages.get(str(stage), 0.0) + float(duration)
            elif key == "repair_costs" and isinstance(value, list):
                telemetry.setdefault(key, []).extend(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                telemetry[key] = telemetry.get(key, 0) + value
            else:
                telemetry[key] = value
    merged["telemetry"] = telemetry

    funnel: dict[str, int] = {}
    for item in passes:
        current = item.get("funnel", {})
        if not isinstance(current, Mapping):
            continue
        for key, value in current.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                funnel[str(key)] = funnel.get(str(key), 0) + int(value)
    merged["funnel"] = funnel

    metrics = {
        "solver": {
            "elapsed_ms": 0.0,
            "branches": 0,
            "unknown": 0,
            "unsat_core_size": 0,
            "search_complete": True,
        },
        "candidates": {
            "proposed": 0,
            "rendered": 0,
            "accepted": 0,
            "rejected": 0,
            "projection_rejected": 0,
        },
        "transaction": {"rollback_count": 0},
        "rollout": {"kill_switch_enabled": False},
    }
    for item in passes:
        current = item.get("metrics", {})
        if not isinstance(current, Mapping):
            continue
        solver = current.get("solver", {})
        if isinstance(solver, Mapping):
            for key in ("elapsed_ms", "branches", "unknown", "unsat_core_size"):
                metrics["solver"][key] += float(solver.get(key, 0) or 0)
            metrics["solver"]["search_complete"] = bool(
                metrics["solver"]["search_complete"]
            ) and bool(solver.get("search_complete", False))
        candidates = current.get("candidates", {})
        if isinstance(candidates, Mapping):
            for key in metrics["candidates"]:
                metrics["candidates"][key] += int(candidates.get(key, 0) or 0)
        transaction = current.get("transaction", {})
        if isinstance(transaction, Mapping):
            metrics["transaction"]["rollback_count"] += int(
                transaction.get("rollback_count", 0) or 0
            )
        rollout = current.get("rollout", {})
        if isinstance(rollout, Mapping):
            metrics["rollout"]["kill_switch_enabled"] = bool(
                metrics["rollout"]["kill_switch_enabled"]
            ) or bool(rollout.get("kill_switch_enabled"))
    merged["metrics"] = metrics
    merged["incremental_passes"] = len(passes)
    merged["incremental_complete"] = complete
    return merged


def _target_issue_failure_code(
    required: Sequence[TargetIssueIdentity],
    deltas: Sequence[TargetIssueDelta] | None,
) -> str | None:
    """Fail closed on missing, drifting, or unresolved exact target evidence."""

    if not required:
        return None
    if deltas is None:
        return "acceptance.target_issue_delta_unavailable"
    by_identity: dict[TargetIssueIdentity, TargetIssueDelta] = {}
    for delta in deltas:
        if delta.identity in by_identity:
            return "acceptance.target_issue_delta_ambiguous"
        by_identity[delta.identity] = delta
    for identity in required:
        delta = by_identity.get(identity)
        if delta is None:
            return "acceptance.target_issue_identity_mismatch"
        if (
            delta.before is None
            or delta.after is None
            or not math.isfinite(delta.before)
            or not math.isfinite(delta.after)
        ):
            return "acceptance.target_issue_delta_unavailable"
        if delta.direction == "decrease":
            if delta.before <= delta.resolution_threshold + delta.tolerance:
                return "acceptance.target_issue_baseline_unproven"
            if delta.after >= delta.before - delta.tolerance:
                return "acceptance.target_issue_not_improved"
            if delta.after > delta.resolution_threshold + delta.tolerance:
                return "acceptance.target_issue_not_resolved"
        else:
            if delta.before >= delta.resolution_threshold - delta.tolerance:
                return "acceptance.target_issue_baseline_unproven"
            if delta.after <= delta.before + delta.tolerance:
                return "acceptance.target_issue_not_improved"
            if delta.after < delta.resolution_threshold - delta.tolerance:
                return "acceptance.target_issue_not_resolved"
    return None


def _passed_evidence(
    *,
    baseline_dom: DomQualityReport,
    candidate_dom: DomQualityReport,
    ocr_non_regression: bool,
    token_recall: float,
    vqa_non_regression: bool,
    vqa_improved: bool,
    target_resolved: bool,
    constraint_report: ConstraintReport | None = None,
    constraint_non_regression: bool = True,
    target_issue_deltas: tuple[TargetIssueDelta, ...] | None = None,
) -> AcceptanceEvidence:
    severity_rank = {"low": 1, "medium": 2, "high": 3}

    def issue_buckets(report: DomQualityReport) -> dict[tuple[str, tuple[str, ...]], list[int]]:
        grouped: dict[tuple[str, tuple[str, ...]], list[int]] = {}
        for issue in report.issues:
            if issue.severity not in {"high", "medium"}:
                continue
            grouped.setdefault((issue.category, issue.qids), []).append(
                severity_rank[issue.severity]
            )
        return {key: sorted(values, reverse=True) for key, values in grouped.items()}

    baseline_buckets = issue_buckets(baseline_dom)
    new_high_medium_defects = 0
    for key, candidate_ranks in issue_buckets(candidate_dom).items():
        baseline_ranks = baseline_buckets.get(key, [])
        new_high_medium_defects += max(0, len(candidate_ranks) - len(baseline_ranks))
        new_high_medium_defects += sum(
            candidate_rank > baseline_rank
            for candidate_rank, baseline_rank in zip(candidate_ranks, baseline_ranks)
        )
    exact_target_resolved = (
        _target_issue_failure_code(
            tuple(delta.identity for delta in target_issue_deltas),
            target_issue_deltas,
        )
        is None
        if target_issue_deltas is not None
        else None
    )
    dom_improved = (
        exact_target_resolved
        if exact_target_resolved is not None
        else local_acceptance_decision(baseline_dom, candidate_dom)[0]
    )
    return AcceptanceEvidence(
        schema=GateEvidence("passed", evidence_ref="candidate.spec"),
        invariant=GateEvidence(
            "passed"
            if (
                constraint_non_regression
                and (constraint_report is None or not constraint_report.hard_failure)
            )
            else "failed",
            reason_codes=(
                ()
                if (
                    constraint_non_regression
                    and (constraint_report is None or not constraint_report.hard_failure)
                )
                else ("constraint.soft_regression",)
                if not constraint_non_regression
                else (
                    constraint_report.primary_code
                    or "constraint.invariant.violated",
                )
            ),
            evidence_ref=(
                "candidate.spec.digests"
                if constraint_report is None
                else "candidate.constraints"
            ),
        ),
        render=GateEvidence("passed", evidence_ref="candidate.render"),
        dom=GateEvidence(
            "passed",
            evidence_ref="candidate.dom",
            target_resolved=target_resolved,
            improved=dom_improved,
            non_regression=not has_dom_quality_regression(baseline_dom, candidate_dom),
            coverage=1.0 if candidate_dom.telemetry_status == "available" else 0.0,
            new_high_medium_defects=new_high_medium_defects,
        ),
        ocr=GateEvidence(
            "passed",
            evidence_ref="candidate.ocr",
            important_token_recall=token_recall,
            non_regression=ocr_non_regression,
        ),
        vqa=GateEvidence(
            "passed",
            evidence_ref="candidate.vqa",
            improved=vqa_improved,
            non_regression=vqa_non_regression,
        ),
        target_issue_deltas=target_issue_deltas,
    )


async def _run_one(
    *,
    presentation_id: uuid.UUID,
    slide: SlideModel,
    deck_snapshot: Sequence[dict[str, Any]],
    position: int,
    opt_in: bool,
    owner_id: object | None,
    session: AsyncSession,
    adjacent_digest: str = "",
    publish_gate: asyncio.Lock | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    # Keep the identifier outside SQLAlchemy's expiration lifecycle.  A rollback
    # expires ORM attributes, and fail-closed telemetry must never trigger a
    # second database access while handling the original error.
    slide_id = slide.id
    rollout = resolve_slidespec_repair_rollout(
        presentation_id, slide_index=position, opt_in=opt_in, owner_id=owner_id
    )

    def empty_metrics() -> dict[str, Any]:
        return {
            "solver": {
                "elapsed_ms": 0.0,
                "branches": 0,
                "unknown": 0,
                "unsat_core_size": 0,
                "search_complete": True,
            },
            "candidates": {
                "proposed": 0,
                "rendered": 0,
                "accepted": 0,
                "rejected": 0,
                "projection_rejected": 0,
            },
            "transaction": {"rollback_count": 0},
            "rollout": {"kill_switch_enabled": rollout.kill_switch_active},
        }
    try:
        bundle = await load_active_slidespec_bundle(session, slide)
        if bundle is None:  # defensive; callers claim only pointer-bearing slides
            return {"slide_index": position, "route": rollout.route, "applied": False,
                    "reason_codes": ["artifact.pointer.missing"], "metrics": empty_metrics()}
        request = _render_request(bundle)
    except (SlideSpecArtifactError, ValueError, TypeError) as error:
        code = error.code if isinstance(error, SlideSpecArtifactError) else "artifact.manifest.invalid"
        return {"slide_index": position, "route": rollout.route, "applied": False,
                "reason_codes": [code, "legacy_preserved"], "metrics": empty_metrics()}

    if not rollout.should_run_repair:
        return {
            "slide_index": position,
            "route": rollout.route,
            "applied": False,
            "reason_codes": [rollout.reason_code or "legacy_preserved", "legacy_preserved"],
            "spec_revision": request.spec.specRevision,
            "metrics": empty_metrics(),
        }

    source_digest = (
        request.source.htmlHash
        if request.source is not None
        else digest_parts("native-html/1", bundle.html)
    )
    baseline_cache_key = inspection_cache_digest(
        spec_digest=request.spec.digests.spec,
        content_digest=request.spec.digests.content,
        source_digest=source_digest,
        font_digest=digest_parts(
            request.fonts.model_dump(mode="json", by_alias=True)
        ),
        browser_digest=digest_parts(
            RENDER_CACHE_VERSION,
            DOM_METRICS_VERSION,
            chrome_runtime_digest(),
            request.renderer.model_dump(mode="json", by_alias=True),
        ),
        adjacent_digest=adjacent_digest,
    )
    cached_inspection = _BASELINE_INSPECTION_CACHE.get(baseline_cache_key)
    inspection_cache_hit = cached_inspection is not None
    if cached_inspection is None:
        try:
            baseline_png, baseline_metrics = await render_html_to_png_with_metrics(
                bundle.html
            )
            baseline_report = inspect_authored_dom(bundle.html, baseline_metrics)
        except Exception:  # renderer boundary is deliberately fail closed
            return {"slide_index": position, "route": rollout.route, "applied": False,
                    "reason_codes": ["render.failed", "legacy_preserved"], "metrics": empty_metrics()}

        qids = _qid_to_semantic(request)
        baseline_observations = observations_from_browser_metrics(
            request, baseline_metrics
        )
        baseline_constraints = evaluate_constraints(
            request.spec,
            observations=baseline_observations,
            baseline_observations=baseline_observations,
            source=request.source,
        )
        solver_started = time.perf_counter()
        solver_result = solve_constraints(request.spec)
        solver_elapsed_ms = round((time.perf_counter() - solver_started) * 1000, 3)
        _BASELINE_INSPECTION_CACHE.put(
            baseline_cache_key,
            _BaselineInspection(
                baseline_png,
                baseline_metrics,
                baseline_report,
                qids,
                baseline_observations,
                baseline_constraints,
                solver_result,
                solver_elapsed_ms,
            ),
        )
    else:
        baseline_png = cached_inspection.png
        baseline_metrics = cached_inspection.metrics
        baseline_report = cached_inspection.report
        qids = cached_inspection.qids
        baseline_observations = cached_inspection.observations
        baseline_constraints = cached_inspection.constraints
        solver_result = cached_inspection.solver_result
        solver_elapsed_ms = 0.0
    solver_metrics: dict[str, Any] = {
        **solver_result.metrics(),
        "elapsed_ms": solver_elapsed_ms,
        "branches": solver_result.branches_evaluated,
        "unknown": int(not solver_result.search_complete),
    }
    baseline_evidence = _merge_dom_evidence(
        _dom_evidence(baseline_report, qids, browser_metrics=baseline_metrics),
            _constraint_evidence(
                request, baseline_constraints, baseline_observations
            ),
    )
    expected = _expected_text(request.spec)
    tokens = _important_tokens(request.spec)
    baseline_ocr: OcrQualityResult | None = None
    candidates: dict[str, _CandidateState] = {}
    projection_rejections: list[dict[str, str]] = []

    async def deterministic(_spec: SlideSpecInput, dom: DomEvidence) -> Sequence[RepairPlan]:
        solver_plan = build_constraint_repair_plan(
            request.spec, feasibility=solver_result
        )
        cascade = build_deterministic_cascade_report(
            _repair_context(
                request,
                dom,
                baseline_metrics,
                constraint_report=baseline_constraints,
            )
        )
        projection_rejections[:] = [
            *(asdict(item) for item in cascade.rejections),
            *(asdict(item) for item in cascade.geometry_rejections),
        ]
        return ((solver_plan,) if solver_plan is not None else ()) + cascade.plans

    async def validate(_spec: SlideSpecInput, plan: RepairPlan) -> SensorResult:
        try:
            applied = apply_repair_plan(request.spec, plan)
            validate_slidespec(applied.spec)
            return SensorResult(True)
        except (SlideSpecError, ValueError) as error:
            return SensorResult(False, getattr(error, "code", "schema.invalid_value"))

    async def render(plan_spec: SlideSpecInput, plan: RepairPlan) -> RenderedCandidate:
        del plan_spec
        applied = apply_repair_plan(request.spec, plan)
        candidate_request = request.model_copy(update={"spec": applied.spec})
        rendered = render_slidespec(candidate_request)
        image, metrics = await render_html_to_png_with_metrics(rendered.html)
        projection_errors = imported_projection_roundtrip_errors(
            candidate_request, metrics
        )
        if projection_errors:
            raise SlideSpecError(
                "render.source_projection.roundtrip_failed",
                ",".join(projection_errors[:8]),
            )
        report = inspect_authored_dom(rendered.html, metrics)
        candidate_observations = observations_from_browser_metrics(
            candidate_request, metrics
        )
        constraint_report = evaluate_constraints(
            candidate_request.spec,
            observations=candidate_observations,
            baseline_observations=baseline_observations,
            source=candidate_request.source,
        )
        state = _CandidateState(
            candidate_request,
            image,
            report,
            metrics,
            plan,
            constraint_report,
            candidate_observations,
        )
        candidate_key = _store_candidate_state(
            candidates, rendered.renderInputHash, state
        )
        return RenderedCandidate(
            candidate_key,
            rendered.renderInputHash,
            realized_repair_cost(request.spec, applied.spec, plan).sort_key,
            constraint_report.soft_cost,
        )

    async def inspect_dom(candidate: RenderedCandidate) -> DomEvidence:
        state = candidates[candidate.html_hash]
        candidate_target_dom = _dom_evidence(
            state.dom,
            _qid_to_semantic(state.request),
            diagnostic_refs=state.plan.diagnostic_refs,
            browser_metrics=state.metrics,
        )
        required, deltas = _target_issue_evidence(
            request=request,
            plan=state.plan,
            baseline_dom=baseline_evidence,
            candidate_dom=candidate_target_dom,
            baseline_constraints=baseline_constraints,
            candidate_constraints=state.constraints,
        )
        state.required_target_issues = required
        state.target_issue_deltas = deltas
        target_failure = _target_issue_failure_code(required, deltas)
        dom_regression = has_dom_quality_regression(baseline_report, state.dom)
        exact_gate = DomEvidence()
        if target_failure is not None or dom_regression:
            semantic_id = (
                required[0].semantic_id
                if required
                else next(iter(_semantic_text(request.spec)), request.spec.slideId)
            )
            exact_gate = DomEvidence(
                (
                    DomDefect(
                        "constraint.target_issue_evidence_failed",
                        "high",
                        semantic_id,
                        diagnostics={
                            "target_issue_evidence": {
                                "reason_code": target_failure
                                or "dom.local_regression",
                                "target_evidence": _target_evidence_payload(deltas),
                            }
                        },
                    ),
                )
            )
        return _merge_dom_evidence(
            candidate_target_dom,
            _constraint_evidence(
                state.request, state.constraints, state.observations
            ),
            _soft_constraint_regression_evidence(
                state.request, baseline_constraints, state.constraints
            ),
            exact_gate,
        )

    async def run_ocr(candidate: RenderedCandidate, _inventory: Any) -> SensorResult:
        nonlocal baseline_ocr
        state = candidates[candidate.html_hash]
        if baseline_ocr is None:
            baseline_ocr = await inspect_authored_ocr(baseline_png, expected_text=expected)
        result = await inspect_authored_ocr(state.image, expected_text=expected)
        if not baseline_ocr.available or not result.available or baseline_ocr.text is None or result.text is None:
            return SensorResult(False, "ocr.unavailable", conclusive=False)
        comparison = compare_expected_candidate(expected, baseline_ocr, result)
        if not comparison.no_regression:
            comparison = compare_expected_candidate_order_insensitive(
                expected,
                baseline_ocr,
                result,
                source_text_preserved=(
                    _semantic_text(state.request.spec) == _semantic_text(request.spec)
                ),
            )
        recall = _token_recall(tokens, result.text)
        state.ocr_non_regression = comparison.no_regression
        state.token_recall = recall
        if not comparison.no_regression:
            return SensorResult(False, "ocr.recall_regressed")
        if recall < 1.0:
            return SensorResult(False, "ocr.important_token_missing")
        return SensorResult(True)

    async def run_vqa(candidate: RenderedCandidate) -> SensorResult:
        state = candidates[candidate.html_hash]
        qid_by_layout = {
            binding.layoutId: binding.qid
            for binding in (
                state.request.source.bindings
                if state.request.source is not None
                else ()
            )
        }
        metric_by_qid = {
            str(item.get("qid", "")): item
            for item in state.metrics.get("elements", ())
            if isinstance(item, Mapping) and item.get("qid")
        }
        changed_targets: list[dict[str, Any]] = []
        for operation in state.plan.operations:
            qid = qid_by_layout.get(operation.target)
            metric = metric_by_qid.get(qid or "")
            if qid is None or not isinstance(metric, Mapping):
                continue
            bbox = metric.get("bbox")
            bbox = bbox if isinstance(bbox, Mapping) else {}
            proof = metric.get("containing_block_proof")
            proof = proof if isinstance(proof, Mapping) else {}
            container = proof.get("offsetParentBoundsPx")
            container = container if isinstance(container, Mapping) else {}
            bottom_inset = None
            if bbox and container:
                bottom_inset = (
                    float(container.get("y", 0))
                    + float(container.get("height", 0))
                    - float(bbox.get("y", 0))
                    - float(bbox.get("height", 0))
                )
            changed_targets.append(
                {
                    "qid": qid,
                    "operation": operation.op,
                    "bboxPx": dict(bbox),
                    "containingBlockPx": dict(container),
                    "bottomInsetPx": (
                        round(bottom_inset, 3)
                        if bottom_inset is not None
                        else None
                    ),
                    "glyphClipped": bool(metric.get("glyph_clipped", False)),
                    "occludedFraction": float(
                        metric.get("occluded_fraction", 0) or 0
                    ),
                }
            )
        geometry_issues = [
            issue
            for issue in state.dom.issues
            if issue.category in {"overlap", "clipping", "overflow"}
            and issue.severity in {"high", "medium"}
        ]
        prompt_context, context_payload = _paired_vqa_context(
            request,
            state.plan,
            verified_geometry={
                "sourceProjectionRoundTrip": True,
                "newDomRegression": has_dom_quality_regression(
                    baseline_report, state.dom
                ),
                "remainingHighMediumGeometryIssueCount": len(geometry_issues),
                "changedTargets": changed_targets,
            },
        )
        critiques = await critique_authored_style_comparison(
            [baseline_png], [state.image], contexts=[prompt_context]
        )
        critique = critiques[0] if critiques else None
        reviewed = [critique]
        accepted, decision_reason, conclusive = style_comparison_improvement_decision(
            critique
        )

        def protected_regression(value: Any) -> bool:
            if value is None:
                return True
            return any(
                dimension.dimension
                in {"content", "readability", "hierarchy", "style"}
                and dimension.verdict in {"worse", "indeterminate"}
                for dimension in value.dimensions
            )

        # A single multimodal review can misread a frame edge even when Chrome
        # has already proved exact containment.  Adjudicate only the narrow
        # boundary case where the first reviewer agrees that the target improved
        # and finds no protected content/readability/hierarchy/style regression.
        # Two rejects still fail closed immediately; acceptance requires two
        # independent positive reviews.
        if (
            not accepted
            and critique is not None
            and critique.target_improved
            and not protected_regression(critique)
        ):
            accept_votes = 0
            reject_votes = 1
            for pass_number in (2, 3):
                if accept_votes >= 2 or reject_votes >= 2:
                    break
                adjudication_context = (
                    prompt_context
                    + f"\nIndependent adjudication pass {pass_number}/3. Reassess "
                    "the same evidence without deferring to another reviewer."
                )
                result = await critique_authored_style_comparison(
                    [baseline_png],
                    [state.image],
                    contexts=[adjudication_context],
                )
                adjudication = result[0] if result else None
                reviewed.append(adjudication)
                vote, _reason, _conclusive = style_comparison_improvement_decision(
                    adjudication
                )
                if vote:
                    accept_votes += 1
                else:
                    reject_votes += 1
            if accept_votes >= 2:
                accepted = True
                decision_reason = "vqa.adjudicated_improved"
                conclusive = True
                critique = next(
                    (
                        value
                        for value in reviewed
                        if style_comparison_improvement_decision(value)[0]
                    ),
                    critique,
                )
            elif reject_votes >= 2:
                accepted = False
                decision_reason = "vqa.adjudicated_regression"
                conclusive = True
            else:
                accepted = False
                decision_reason = "vqa.adjudication_inconclusive"
                conclusive = False
        state.vqa_non_regression = (
            accepted
            and critique is not None
            and not style_comparison_has_regression(critique)
        )
        state.vqa_improved = accepted
        return SensorResult(
            accepted,
            None if accepted else decision_reason,
            conclusive=conclusive,
            quality_gain=style_comparison_quality_gain(critique),
            quality_gain_interval=style_comparison_quality_interval(critique),
            diagnostics={
                "prompt_context": context_payload,
                "critique": (
                    critique.model_dump(mode="json")
                    if critique is not None
                    else None
                ),
                "adjudication_critiques": [
                    value.model_dump(mode="json") if value is not None else None
                    for value in reviewed
                ],
                "provider_failure": (
                    style_comparison_failure_diagnostic(
                        baseline_png,
                        state.image,
                        prompt_context,
                    )
                    if critique is None
                    else None
                ),
                "decision": {
                    "accepted": accepted,
                    "reason_code": decision_reason,
                    "conclusive": conclusive,
                    "non_regression": state.vqa_non_regression,
                },
            },
        )

    def acceptance_for_state(state: _CandidateState) -> AcceptanceDecision:
        """Evaluate and retain the same canonical decision in every rollout mode."""

        candidate_target_dom = _dom_evidence(
            state.dom,
            _qid_to_semantic(state.request),
            diagnostic_refs=state.plan.diagnostic_refs,
            browser_metrics=state.metrics,
        )
        required_target_issues, target_issue_deltas = _target_issue_evidence(
            request=request,
            plan=state.plan,
            baseline_dom=baseline_evidence,
            candidate_dom=candidate_target_dom,
            baseline_constraints=baseline_constraints,
            candidate_constraints=state.constraints,
        )
        state.required_target_issues = required_target_issues
        state.target_issue_deltas = target_issue_deltas
        evidence = _passed_evidence(
            baseline_dom=baseline_report,
            candidate_dom=state.dom,
            ocr_non_regression=state.ocr_non_regression,
            token_recall=state.token_recall,
            vqa_non_regression=state.vqa_non_regression,
            vqa_improved=state.vqa_improved,
            target_resolved=not candidate_target_dom.required_geometry_defect
            and not state.constraints.hard_failure,
            constraint_report=state.constraints,
            constraint_non_regression=not has_soft_constraint_regression(
                baseline_constraints, state.constraints
            ),
            target_issue_deltas=target_issue_deltas,
        )
        decision = decide_acceptance(
            "geometry",
            evidence,
            base_spec_hash=request.spec.digests.spec,
            candidate_spec_hash=state.request.spec.digests.spec,
            decision_id=f"decision:{state.plan.plan_id}",
            plan_id=state.plan.plan_id,
            base_spec_revision=request.spec.specRevision,
            required_target_issues=required_target_issues,
        )
        state.decision = decision
        return decision

    async def publish_unlocked(
        _spec: SlideSpecInput, plan: RepairPlan, candidate: RenderedCandidate
    ) -> PublicationReceipt:
        state = candidates[candidate.html_hash]
        decision = acceptance_for_state(state)
        if decision.outcome != "accepted":
            return PublicationReceipt(
                False,
                False,
                False,
                reason_code=(
                    decision.reason_codes[0]
                    if decision.reason_codes
                    else "acceptance.target_issue_delta_unavailable"
                ),
            )
        evidence_payload = {
            "baseline_dom": baseline_report.to_dict(),
            "candidate_dom": state.dom.to_dict(),
            "target_evidence": _target_evidence_payload(
                state.target_issue_deltas
            ),
            "baseline_constraints": _constraint_report_payload(
                baseline_constraints
            ),
            "candidate_constraints": _constraint_report_payload(
                state.constraints
            ),
            "constraint_non_regression": not has_soft_constraint_regression(
                baseline_constraints, state.constraints
            ),
            "rollout": {
                "route": rollout.route,
                "cohort_bucket": rollout.cohort_bucket,
            },
        }
        attempt_id = uuid.uuid4()
        original_html = slide.html_content
        original_content = deepcopy(slide.content)
        original_properties = deepcopy(slide.properties)

        # Persist accepted evidence before touching the live slide. A failed CAS
        # or verification can therefore always be audited after the transaction.
        try:
            candidate_artifact = (
                await session.execute(
                    select(SlideSpecArtifactBundle).where(
                        SlideSpecArtifactBundle.slide_id == slide.id,
                        SlideSpecArtifactBundle.candidate_spec_hash
                        == state.request.spec.digests.spec,
                        SlideSpecArtifactBundle.status == "candidate",
                        SlideSpecArtifactBundle.route == "slidespec_active",
                    )
                )
            ).scalar_one_or_none()
            if candidate_artifact is None:
                candidate_artifact = build_slidespec_artifact_bundle(
                    presentation_id=presentation_id,
                    slide=slide,
                    route="slidespec_active",
                    status="candidate",
                    render_request=state.request,
                    repair_plan=plan,
                    acceptance=decision,
                    evidence=evidence_payload,
                    image_bytes=state.image,
                )
                session.add(candidate_artifact)
                await session.flush()
            await record_publication_attempt(
                session,
                attempt_id=attempt_id,
                bundle=candidate_artifact,
                state="staged",
                evidence={
                    "base_spec_hash": request.spec.digests.spec,
                    "candidate_spec_hash": state.request.spec.digests.spec,
                },
            )
            await session.commit()
        except Exception as error:
            await session.rollback()
            reason = getattr(error, "code", "artifact.publish.stage_failed")
            return PublicationReceipt(
                False,
                False,
                False,
                rolled_back=False,
                reason_code=reason,
            )

        # A concurrent edit after candidate generation invalidates the candidate.
        try:
            live_slide = await session.get(SlideModel, slide.id, populate_existing=True)
            if live_slide is None:
                raise SlideSpecArtifactError("artifact.publish.target_missing")
            if (
                live_slide.html_content != original_html
                or live_slide.content != original_content
                or live_slide.properties != original_properties
            ):
                raise SlideSpecArtifactError("artifact.publish.cas_conflict")

            artifact = (
                await session.execute(
                    select(SlideSpecArtifactBundle).where(
                        SlideSpecArtifactBundle.slide_id == slide.id,
                        SlideSpecArtifactBundle.candidate_spec_hash
                        == state.request.spec.digests.spec,
                        SlideSpecArtifactBundle.status == "published",
                        SlideSpecArtifactBundle.route == "slidespec_active",
                    )
                )
            ).scalar_one_or_none()
            if artifact is None:
                artifact = build_slidespec_artifact_bundle(
                    presentation_id=presentation_id,
                    slide=live_slide,
                    route="slidespec_active",
                    status="published",
                    render_request=state.request,
                    repair_plan=plan,
                    acceptance=decision,
                    evidence=evidence_payload,
                    publication_receipt={
                        "attempt_id": str(attempt_id),
                        "candidate_bundle_id": str(candidate_artifact.id),
                        "commit_state": "activation_pending",
                        "cas": "required",
                        "post_commit_readback": "required",
                    },
                    image_bytes=state.image,
                )
            await version_service.snapshot_slides(
                session,
                presentation_id,
                list(deck_snapshot),
                label="SlideSpec automatic repair",
                force=True,
            )
            await publish_slidespec_bundle_cas(
                session, slide=live_slide, bundle=artifact
            )
            await record_publication_attempt(
                session,
                attempt_id=attempt_id,
                bundle=artifact,
                state="activation_committed",
                evidence={
                    "html_hash": artifact.html_hash,
                    "render_hash": artifact.render_hash,
                },
            )
            await session.commit()
        except Exception as error:
            await session.rollback()
            reason = getattr(error, "code", "artifact.publish.transaction_failed")
            try:
                await record_publication_attempt(
                    session,
                    attempt_id=attempt_id,
                    bundle=candidate_artifact,
                    state="failed",
                    reason_code=reason,
                )
                await session.commit()
            except Exception:
                await session.rollback()
            return PublicationReceipt(
                False,
                False,
                False,
                rolled_back=False,
                reason_code=reason,
            )

        # Verify using a fresh session so identity-map state cannot masquerade as
        # a durable publication visible to later requests.
        verification_reason: str | None = None
        bind = session.bind
        if bind is None:
            verification_reason = "artifact.publish.verification_bind_missing"
        else:
            verify_sessions = async_sessionmaker(bind, expire_on_commit=False)
            try:
                async with verify_sessions() as verify_session:
                    refreshed = await verify_session.get(SlideModel, slide.id)
                    if refreshed is None:
                        raise SlideSpecArtifactError(
                            "artifact.publish.readback_mismatch"
                        )
                    loaded = await load_active_slidespec_bundle(
                        verify_session, refreshed
                    )
                    if (
                        loaded is None
                        or loaded.id != artifact.id
                        or loaded.html_hash != artifact.html_hash
                        or loaded.render_hash != artifact.render_hash
                    ):
                        raise SlideSpecArtifactError(
                            "artifact.publish.readback_mismatch"
                        )
                    await record_publication_attempt(
                        verify_session,
                        attempt_id=attempt_id,
                        bundle=artifact,
                        state="verified",
                        evidence={
                            "html_hash": loaded.html_hash,
                            "render_hash": loaded.render_hash,
                        },
                    )
                    await verify_session.commit()
            except Exception as error:
                verification_reason = getattr(
                    error, "code", "artifact.publish.postcommit_verify_failed"
                )

        if verification_reason is None:
            return PublicationReceipt(
                True,
                True,
                True,
                receipt_id=str(artifact.id),
            )

        # Post-commit failure restores only this exact publication. If another
        # writer changed the slide, its edit wins and we report a rollback conflict.
        expected_properties = {
            **(original_properties if isinstance(original_properties, dict) else {}),
            ACTIVE_POINTER_KEY: active_pointer(artifact),
        }
        rollback_succeeded = False
        recovery_reason = verification_reason
        try:
            if bind is None:
                raise SlideSpecArtifactError(
                    "artifact.publish.verification_bind_missing"
                )
            recovery_sessions = async_sessionmaker(bind, expire_on_commit=False)
            async with recovery_sessions() as recovery_session:
                await record_publication_attempt(
                    recovery_session,
                    attempt_id=attempt_id,
                    bundle=artifact,
                    state="failed",
                    reason_code=verification_reason,
                )
                rollback_succeeded = await rollback_active_slide_cas(
                    recovery_session,
                    slide_id=slide.id,
                    expected_html=artifact.html,
                    expected_properties=expected_properties,
                    restore_html=original_html,
                    restore_properties=original_properties,
                )
                if rollback_succeeded:
                    await record_publication_attempt(
                        recovery_session,
                        attempt_id=attempt_id,
                        bundle=artifact,
                        state="rolled_back",
                        reason_code=verification_reason,
                    )
                else:
                    recovery_reason = "artifact.publish.rollback_conflict"
                    await record_publication_attempt(
                        recovery_session,
                        attempt_id=attempt_id,
                        bundle=artifact,
                        state="rollback_conflict",
                        reason_code=recovery_reason,
                        evidence={"verification_reason": verification_reason},
                    )
                await recovery_session.commit()
        except Exception as error:
            recovery_reason = getattr(
                error, "code", "artifact.publish.rollback_failed"
            )

        return PublicationReceipt(
            True,
            True,
            False,
            receipt_id=str(artifact.id),
            rolled_back=rollback_succeeded,
            reason_code=recovery_reason,
        )

    async def publish(
        spec_input: SlideSpecInput,
        plan: RepairPlan,
        candidate: RenderedCandidate,
    ) -> PublicationReceipt:
        if publish_gate is None:
            return await publish_unlocked(spec_input, plan, candidate)
        async with publish_gate:
            return await publish_unlocked(spec_input, plan, candidate)

    async def proposal_preflight(
        _spec: SlideSpecInput, dom: DomEvidence
    ) -> SensorResult:
        """Skip the model only when every diagnosed imported target is immutable.

        Native SlideSpecs remain model-eligible.  For authored imports, this is a
        conservative capability check over the already imported binding and closed
        mutation envelope; it does not attempt to reproduce projection logic.
        """

        if request.source is None:
            return SensorResult(True)
        nodes_by_semantic = {
            node.contentSemanticId: node
            for node in _walk_layout(request.spec.layout)
            if node.contentSemanticId is not None
        }
        bindings_by_semantic = {
            binding.semanticId: binding for binding in request.source.bindings
        }
        all_operations = {
            "setSpacing",
            "setPadding",
            "setFlow",
            "setTracks",
            "setColumnRatio",
            "setGridPlacement",
            "setBounds",
            "translateWithinParent",
            "resizeWithinBounds",
            "setFontScale",
            "setLineHeightToken",
            "setTextFitPolicy",
            "setImageFit",
            "setCropFocalPoint",
        }
        for defect in dom.defects:
            for semantic_id in (defect.semantic_id, *defect.related_semantic_ids):
                node = nodes_by_semantic.get(semantic_id)
                binding = bindings_by_semantic.get(semantic_id)
                if node is None or binding is None:
                    continue
                envelope = node.constraints or LayoutConstraints()
                if envelope.locked:
                    continue
                allowed = (
                    set(envelope.allowedOperations)
                    if envelope.allowedOperations is not None
                    else set(all_operations)
                )
                projectable: set[str] = set()
                if defect.code in {"dom.clipping", "dom.overflow"}:
                    if binding.position != "static":
                        projectable.update({"setBounds", "resizeWithinBounds"})
                    if binding.fontSizePx is not None:
                        projectable.update(
                            {"setFontScale", "setLineHeightToken", "setTextFitPolicy"}
                        )
                    if binding.tag == "img":
                        projectable.update({"setImageFit", "setCropFocalPoint"})
                elif defect.code in {
                    "dom.off_canvas",
                    "constraint.containment.violated",
                }:
                    if binding.position != "static":
                        projectable.update({"setBounds", "translateWithinParent"})
                    elif binding.staticTranslationSafe and binding.transform == "none":
                        projectable.add("translateWithinParent")
                elif defect.code in {
                    "constraint.gap.violated",
                    "constraint.alignment.violated",
                    "constraint.order.violated",
                }:
                    if binding.display in {
                        "flex",
                        "inline-flex",
                        "grid",
                        "inline-grid",
                    }:
                        projectable.update({"setSpacing", "setFlow", "setPadding"})
                    if binding.position != "static":
                        projectable.update({"setBounds", "translateWithinParent"})
                elif defect.code in {
                    "dom.unintended_overlap",
                    "constraint.non_overlap.violated",
                }:
                    # Imported connector topology has no round-trip mutation
                    # contract.  Non-connectors need an explicitly projectable
                    # position change; arbitrary flow relayout remains blocked.
                    if binding.tag != "svg" and binding.position != "static":
                        projectable.update({"setBounds", "translateWithinParent"})
                if allowed.intersection(projectable):
                    return SensorResult(True)
        reason = (
            "proposal.mutation_envelope_unavailable"
            if any(
                item.get("reason_code", "").startswith("repair.mutation_envelope")
                for item in projection_rejections
            )
            else "proposal.projection_unavailable"
        )
        return SensorResult(False, reason)

    async def propose(spec_input: SlideSpecInput, dom: DomEvidence) -> Sequence[RepairPlan]:
        diagnostics = [
            {"code": defect.code, "severity": defect.severity, "semanticId": defect.semantic_id}
            for defect in dom.defects
        ]
        result = await propose_slidespec_repair(
            request.spec.model_dump(mode="json", by_alias=True), diagnostics,
            slide_id=request.spec.slideId,
            base_spec_revision=request.spec.specRevision,
            base_spec_hash=request.spec.digests.spec,
            expected_content_digest=request.spec.digests.content,
        )
        if not result.ok:
            if "proposal.model_timeout" in result.reason_codes:
                raise TimeoutError("proposal.model_timeout")
            if "system.internal_error" in result.reason_codes:
                # Provider/configuration failures are transient and must never
                # become a conclusive empty-result cache entry.
                raise RuntimeError("proposal.provider_unavailable")
            return ()
        return result.plans

    capability_digest = digest_parts(
        PIPELINE_CAPABILITY_VERSION,
        CASCADE_POLICY_VERSION,
        request.source.importerVersion if request.source is not None else "native",
        [
            (
                node.layoutId,
                node.constraints.model_dump(mode="json")
                if node.constraints is not None
                else None,
            )
            for node in _walk_layout(request.spec.layout)
        ],
    )
    try:
        proposal_model = str(get_model() or "")
    except Exception:
        proposal_model = "unconfigured"
    model_digest = digest_parts(PROPOSAL_PROMPT_VERSION, proposal_model)

    pipeline = SlideSpecRepairPipeline(
        SlideSpecRepairDependencies(
            deterministic_cascade=deterministic,
            validate_plan=validate,
            render_plan=render,
            inspect_dom=inspect_dom,
            run_ocr=run_ocr,
            run_vqa=run_vqa,
            publish=publish,
            propose=propose,
            proposal_preflight=proposal_preflight,
            proposal_capability_digest=capability_digest,
            proposal_model_digest=model_digest,
        )
    )
    spec_input = SlideSpecInput(
        revision_id=f"{request.spec.slideId}:{request.spec.specRevision}",
        spec_hash=request.spec.digests.spec,
        semantic_text=_semantic_text(request.spec),
        important_tokens=tokens,
    )
    try:
        result = await pipeline.run(
            spec=spec_input, baseline_dom=baseline_evidence, rollout=rollout
        )
    except Exception as error:
        await session.rollback()
        LOGGER.exception(
            "SlideSpec repair pipeline failed closed for slide %s at position %s",
            slide_id,
            position,
        )
        return {
            "slide_index": position,
            "route": rollout.route,
            "trigger": "geometry" if baseline_evidence.required_geometry_defect else None,
            "applied": False,
            "rolled_back": False,
            "reason_codes": ["acceptance.sensor_failed", "legacy_preserved"],
            "outcomes": [],
            "funnel": {"legacy_preserved": 1},
            "metrics": {
                **empty_metrics(),
                "solver": solver_metrics,
                "sensor_failure": {
                    "stage": "repair_pipeline",
                    "exception_type": type(error).__name__,
                },
            },
            "telemetry": {},
            "total_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    shadow_artifact_id: str | None = None
    shadow_error: str | None = None
    if rollout.shadow_mode and not dry_run:
        accepted = next(
            (
                outcome
                for outcome in result.outcomes
                if outcome.validation_status == "accepted"
            ),
            None,
        )
        state = next(
            (
                candidate_state
                for candidate_state in candidates.values()
                if accepted is not None
                and candidate_state.plan.plan_id == accepted.plan_id
            ),
            None,
        )
        if accepted is not None and state is not None:
            candidate_target_dom = _dom_evidence(
                state.dom,
                _qid_to_semantic(state.request),
                diagnostic_refs=state.plan.diagnostic_refs,
                browser_metrics=state.metrics,
            )
            required_target_issues, target_issue_deltas = _target_issue_evidence(
                request=request,
                plan=state.plan,
                baseline_dom=baseline_evidence,
                candidate_dom=candidate_target_dom,
                baseline_constraints=baseline_constraints,
                candidate_constraints=state.constraints,
            )
            evidence = _passed_evidence(
                baseline_dom=baseline_report,
                candidate_dom=state.dom,
                ocr_non_regression=state.ocr_non_regression,
                token_recall=state.token_recall,
                vqa_non_regression=state.vqa_non_regression,
                vqa_improved=state.vqa_improved,
                target_resolved=not candidate_target_dom.required_geometry_defect
                and not state.constraints.hard_failure,
                constraint_report=state.constraints,
                constraint_non_regression=not has_soft_constraint_regression(
                    baseline_constraints, state.constraints
                ),
                target_issue_deltas=target_issue_deltas,
            )
            decision = decide_acceptance(
                "geometry",
                evidence,
                base_spec_hash=request.spec.digests.spec,
                candidate_spec_hash=state.request.spec.digests.spec,
                decision_id=f"decision:{state.plan.plan_id}",
                plan_id=state.plan.plan_id,
                base_spec_revision=request.spec.specRevision,
                required_target_issues=required_target_issues,
            )
            state.decision = decision
            artifact = build_slidespec_artifact_bundle(
                presentation_id=presentation_id,
                slide=slide,
                route="repair_shadow",
                status="shadow",
                render_request=state.request,
                repair_plan=state.plan,
                acceptance=decision,
                evidence={
                    "baseline_dom": baseline_report.to_dict(),
                    "candidate_dom": state.dom.to_dict(),
                    "baseline_constraints": _constraint_report_payload(
                        baseline_constraints
                    ),
                    "candidate_constraints": _constraint_report_payload(
                        state.constraints
                    ),
                    "constraint_non_regression": not has_soft_constraint_regression(
                        baseline_constraints, state.constraints
                    ),
                    "rollout": {
                        "route": rollout.route,
                        "cohort_bucket": rollout.cohort_bucket,
                    },
                },
                image_bytes=state.image,
            )
            try:
                existing = await session.execute(
                    select(SlideSpecArtifactBundle).where(
                        SlideSpecArtifactBundle.slide_id == slide.id,
                        SlideSpecArtifactBundle.candidate_spec_hash
                        == artifact.candidate_spec_hash,
                        SlideSpecArtifactBundle.status == artifact.status,
                        SlideSpecArtifactBundle.route == artifact.route,
                    )
                )
                persisted = existing.scalar_one_or_none()
                if persisted is None:
                    session.add(artifact)
                    await session.commit()
                    persisted = artifact
                shadow_artifact_id = str(persisted.id)
            except Exception:
                await session.rollback()
                shadow_error = "artifact.shadow.persist_failed"

    reason_codes = list(result.reason_codes)
    if shadow_error is not None:
        reason_codes.insert(0, shadow_error)
    operational_metrics = result.metrics()
    operational_metrics["solver"] = solver_metrics
    operational_metrics["candidates"]["projection_rejected"] = len(
        projection_rejections
    )
    states_by_plan_id = {state.plan.plan_id: state for state in candidates.values()}
    serialized_outcomes: list[dict[str, Any]] = []
    canonical_overrides = 0
    for outcome in result.outcomes:
        payload = asdict(outcome)
        state = states_by_plan_id.get(outcome.plan_id)
        if state is not None:
            diagnostics = dict(payload.get("diagnostics", {}))
            diagnostics["target_evidence"] = _target_evidence_payload(
                state.target_issue_deltas
            )
            diagnostics["constraint_evidence"] = {
                "baseline_soft_cost": baseline_constraints.soft_cost,
                "candidate_soft_cost": state.constraints.soft_cost,
                "soft_regression": has_soft_constraint_regression(
                    baseline_constraints, state.constraints
                ),
                "hard_failure": state.constraints.hard_failure,
                "unknown_hard": list(state.constraints.unknown_hard),
            }
            diagnostics["source_projection_roundtrip"] = {
                "passed": True,
                "errors": [],
            }
            diagnostics["repair_operations"] = _operation_change_payload(state.plan)
            if outcome.selected and outcome.validation_status == "accepted":
                decision = state.decision or acceptance_for_state(state)
                diagnostics["acceptance_decision"] = decision.model_dump(
                    mode="json", by_alias=True
                )
                if decision.outcome != "accepted":
                    canonical_overrides += 1
                    payload["validation_status"] = decision.outcome
                    payload["reason_codes"] = list(decision.reason_codes)
            payload["diagnostics"] = diagnostics
        serialized_outcomes.append(payload)
    funnel_payload = result.funnel.metrics()
    if canonical_overrides:
        funnel_payload["candidate_accepted"] = max(
            0, funnel_payload["candidate_accepted"] - canonical_overrides
        )
        funnel_payload["validation_rejected"] += canonical_overrides
        operational_metrics["candidates"]["accepted"] = max(
            0,
            int(operational_metrics["candidates"].get("accepted", 0))
            - canonical_overrides,
        )
        operational_metrics["candidates"]["rejected"] = int(
            operational_metrics["candidates"].get("rejected", 0)
        ) + canonical_overrides
    confirmed_issues = _confirmed_issue_payload(baseline_evidence)
    return {
        "slide_index": position,
        "route": result.route,
        "trigger": result.trigger,
        "applied": bool(result.funnel.applied),
        "rolled_back": bool(result.funnel.rolled_back),
        "confirmed_issue_count": result.confirmed_issue_count,
        "incomplete_measurement_count": result.incomplete_measurement_count,
        "unresolved_confirmed_issue_count": (
            result.unresolved_confirmed_issue_count
        ),
        "clean_slide_count": result.clean_slide_count,
        "confirmed_issues": confirmed_issues,
        "detected_issues": [
            str(issue.get("code"))
            for issue in confirmed_issues
            if issue.get("code")
        ],
        "evidence_atoms": _evidence_atom_payload(baseline_evidence),
        "incomplete_measurements": [
            {
                "sensor": atom.sensor,
                "metric": atom.metric,
                "stage": atom.stage,
                "qid": atom.qid,
                "semantic_id": atom.semantic_id,
                "constraint_id": atom.constraint_id,
                "reason": atom.reason_code,
                "reason_code": atom.reason_code,
                "dependency_refs": list(atom.dependency_refs),
                "diagnostics": dict(atom.diagnostics),
            }
            for atom in baseline_evidence.unknown_atoms
        ],
        "publish_blockers": _dependency_blocker_payload(serialized_outcomes),
        "reason_codes": reason_codes,
        "shadow_artifact_id": shadow_artifact_id,
        "outcomes": serialized_outcomes,
        "candidate_count": int(result.funnel.candidate_proposed),
        "projection_rejections": projection_rejections,
        "no_candidate": projection_rejections if not result.outcomes else [],
        "funnel": funnel_payload,
        "metrics": operational_metrics,
        "telemetry": {
            "stage_timings_ms": dict(result.telemetry.stage_timings_ms),
            "llm_calls": result.telemetry.llm_calls,
            "llm_candidates": result.telemetry.llm_candidates,
            "render_calls": result.telemetry.render_calls,
            "dom_calls": result.telemetry.dom_calls,
            "ocr_calls": result.telemetry.ocr_calls,
            "vqa_calls": result.telemetry.vqa_calls,
            "cache_hits": result.telemetry.cache_hits,
            "cache_misses": result.telemetry.cache_misses,
            "inspection_cache_hits": int(inspection_cache_hit),
            "inspection_cache_misses": int(not inspection_cache_hit),
            "proposal_cache_hits": result.telemetry.proposal_cache_hits,
            "proposal_cache_misses": result.telemetry.proposal_cache_misses,
            "proposal_preflight_skips": result.telemetry.proposal_preflight_skips,
            "pareto_pruned": result.telemetry.pareto_pruned,
            "repair_costs": [list(cost) for cost in result.telemetry.repair_costs],
        },
        "total_ms": round((time.perf_counter() - started) * 1000, 3),
    }


async def execute_slidespec_authored_review(
    *,
    presentation_id: uuid.UUID,
    slides: Sequence[SlideModel],
    positions: Sequence[int],
    opt_in: bool,
    owner_id: object | None,
    session: AsyncSession,
    dry_run: bool = False,
) -> CanonicalReviewExecution:
    """Claim and execute pointer-backed slides, leaving all others to legacy."""

    deck_started = time.perf_counter()
    render_counters_before = get_render_runtime_counters()

    claimed = tuple(
        position
        for position in positions
        if isinstance(slides[position].properties, Mapping)
        and ACTIVE_POINTER_KEY in slides[position].properties
    )
    slide_ids = {position: slides[position].id for position in claimed}
    deck_snapshot = tuple(slide.model_dump(mode="json") for slide in slides)
    adjacency = adjacent_influence_digests(deck_snapshot)
    try:
        slide_concurrency = max(
            1,
            min(8, int(os.getenv("SLIDESPEC_SLIDE_CONCURRENCY", "3"))),
        )
    except ValueError:
        slide_concurrency = 3
    scheduler = BoundedSlideScheduler(slide_concurrency)
    publish_gate = asyncio.Lock()

    async def run_position(
        position: int, worker_session: AsyncSession
    ) -> dict[str, Any]:
        slide = await worker_session.get(
            SlideModel, slide_ids[position], populate_existing=True
        )
        if slide is None:
            return {
                "slide_index": position,
                "route": "slidespec_active",
                "applied": False,
                "reason_codes": ["slide.missing", "legacy_preserved"],
            }
        passes: list[dict[str, Any]] = []
        accepted_repairs = 0
        complete = False
        while accepted_repairs <= 4:
            current = await _run_one(
                presentation_id=presentation_id,
                slide=slide,
                deck_snapshot=deck_snapshot,
                position=position,
                opt_in=opt_in,
                owner_id=owner_id,
                session=worker_session,
                adjacent_digest=adjacency[position],
                publish_gate=publish_gate,
                dry_run=dry_run,
            )
            passes.append(current)
            if not current.get("applied") or dry_run:
                complete = not bool(current.get("trigger"))
                break
            accepted_repairs += 1
            if accepted_repairs >= 4:
                current.setdefault("reason_codes", []).append(
                    "incremental_repair_limit_reached"
                )
                break
            slide = await worker_session.get(
                SlideModel,
                slide_ids[position],
                populate_existing=True,
            )
            if slide is None:
                current.setdefault("reason_codes", []).append("slide.missing_after_publish")
                break
        return _merge_incremental_slide_results(passes, complete=complete)

    bind = session.bind
    if len(claimed) > 1 and bind is not None:
        worker_sessions = async_sessionmaker(bind, expire_on_commit=False)

        async def isolated(position: int) -> dict[str, Any]:
            async with worker_sessions() as worker_session:
                return await run_position(position, worker_session)

        raw_results = await scheduler.map(claimed, isolated)
    else:
        raw_results = tuple(
            [await run_position(position, session) for position in claimed]
        )

    results = []
    for result in raw_results:
        outcomes_for_slide = [
            outcome
            for outcome in result.get("outcomes", [])
            if isinstance(outcome, Mapping)
        ]
        canonical = _canonical_outcome_projection(outcomes_for_slide)
        terminal = canonical["terminal"]
        triggered = bool(result.get("trigger"))
        confirmed_for_slide = int(result.get("confirmed_issue_count", 0))
        incomplete_for_slide = int(
            result.get("incomplete_measurement_count", 0)
        )
        unresolved_for_slide = int(
            result.get("unresolved_confirmed_issue_count", 0)
        )
        applied_for_slide = bool(result.get("applied"))
        incremental_complete = bool(result.get("incremental_complete", True))
        accepted_for_slide = bool(canonical["accepted"])
        reasons = [str(code) for code in result.get("reason_codes", [])]
        terminal_reasons = [
            str(code) for code in terminal.get("reason_codes", []) if str(code)
        ]
        candidate_rejections = [
            {
                "plan_id": str(outcome.get("plan_id", "")),
                "source": str(outcome.get("source", "")),
                "render_status": str(outcome.get("render_status", "")),
                "validation_status": str(outcome.get("validation_status", "")),
                "reason_codes": [
                    str(code) for code in outcome.get("reason_codes", []) if str(code)
                ],
                "diagnostics": dict(outcome.get("diagnostics", {}))
                if isinstance(outcome.get("diagnostics"), Mapping)
                else {},
            }
            for outcome in outcomes_for_slide
            if outcome.get("validation_status") in {"rejected", "inconclusive"}
        ]
        result.update(
            {
                "attempted": triggered or bool(outcomes_for_slide),
                "accepted": accepted_for_slide,
                "apply_status": canonical["apply_status"],
                "publish_status": canonical["publish_status"],
                "post_verify_status": canonical["post_verify_status"],
                "rejection_reason": None
                if applied_for_slide
                else (
                    terminal_reasons[0]
                    if terminal_reasons
                    else (reasons[0] if reasons else None)
                ),
                "candidate_rejections": candidate_rejections,
                "target_evidence": canonical["target_evidence"],
                "element_changes": canonical["element_changes"],
                "resolved_issues": ["geometry"] if applied_for_slide else [],
                "remaining_issues": ["geometry"]
                if unresolved_for_slide > 0
                else [],
                "confirmed_issue_count": confirmed_for_slide,
                "incomplete_measurement_count": incomplete_for_slide,
                "unresolved_confirmed_issue_count": unresolved_for_slide,
                "clean_slide_count": int(
                    confirmed_for_slide == 0 and incomplete_for_slide == 0
                ),
                # Keep the established authored-review API contract so the
                # frontend can distinguish a canonical SlideSpec patch from a
                # full-slide fallback instead of normalizing it to ``none``.
                "path": "local_patch" if outcomes_for_slide else "none",
            }
        )
        results.append(result)
    funnel: dict[str, Any] = {}
    for result in results:
        for key, value in result.get("funnel", {}).items():
            funnel[key] = funnel.get(key, 0) + int(value)
    telemetry_keys = (
        "llm_calls",
        "llm_candidates",
        "render_calls",
        "dom_calls",
        "ocr_calls",
        "vqa_calls",
        "cache_hits",
        "cache_misses",
        "inspection_cache_hits",
        "inspection_cache_misses",
        "proposal_cache_hits",
        "proposal_cache_misses",
        "proposal_preflight_skips",
        "pareto_pruned",
    )
    for key in telemetry_keys:
        funnel[key] = sum(
            int(result.get("telemetry", {}).get(key, 0)) for result in results
        )
    stage_timings_ms: dict[str, float] = {}
    for result in results:
        for stage, duration in result.get("telemetry", {}).get(
            "stage_timings_ms", {}
        ).items():
            stage_timings_ms[str(stage)] = stage_timings_ms.get(str(stage), 0.0) + float(
                duration
            )
    funnel["stage_timings_ms"] = {
        stage: round(duration, 3) for stage, duration in sorted(stage_timings_ms.items())
    }
    funnel["total_ms"] = round(
        sum(float(result.get("total_ms", 0.0)) for result in results), 3
    )
    funnel["slide_concurrency"] = slide_concurrency
    funnel["deterministic_resolved"] = sum(
        bool(result.get("applied"))
        and any(
            outcome.get("source") == "deterministic"
            and outcome.get("validation_status") == "accepted"
            for outcome in result.get("outcomes", [])
        )
        for result in results
    )
    funnel["llm_resolved"] = sum(
        bool(result.get("applied"))
        and any(
            outcome.get("source") == "llm"
            and outcome.get("validation_status") == "accepted"
            for outcome in result.get("outcomes", [])
        )
        for result in results
    )
    def metric_number(value: object) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) else 0.0

    operational_metrics: dict[str, Any] = {
        "solver": {
            "elapsed_ms": 0.0,
            "branches": 0,
            "unknown": 0,
            "unsat_core_size": 0,
            "search_complete": True,
        },
        "candidates": {
            "proposed": 0,
            "rendered": 0,
            "accepted": 0,
            "rejected": 0,
            "projection_rejected": 0,
        },
        "transaction": {"rollback_count": 0},
        "rollout": {"kill_switch_enabled": False},
    }
    for result in results:
        item_metrics = result.get("metrics")
        if not isinstance(item_metrics, Mapping):
            continue
        solver = item_metrics.get("solver")
        if isinstance(solver, Mapping):
            for key in ("elapsed_ms", "branches", "unknown", "unsat_core_size"):
                operational_metrics["solver"][key] += metric_number(solver.get(key))
            operational_metrics["solver"]["search_complete"] = bool(
                operational_metrics["solver"]["search_complete"]
            ) and bool(solver.get("search_complete", False))
        candidate_metrics = item_metrics.get("candidates")
        if isinstance(candidate_metrics, Mapping):
            for key in (
                "proposed",
                "rendered",
                "accepted",
                "rejected",
                "projection_rejected",
            ):
                operational_metrics["candidates"][key] += int(
                    metric_number(candidate_metrics.get(key))
                )
        transaction = item_metrics.get("transaction")
        if isinstance(transaction, Mapping):
            operational_metrics["transaction"]["rollback_count"] += int(
                metric_number(transaction.get("rollback_count"))
            )
        rollout_metrics = item_metrics.get("rollout")
        if isinstance(rollout_metrics, Mapping):
            operational_metrics["rollout"]["kill_switch_enabled"] = bool(
                operational_metrics["rollout"]["kill_switch_enabled"]
            ) or bool(rollout_metrics.get("kill_switch_enabled"))
    operational_metrics["solver"]["elapsed_ms"] = round(
        operational_metrics["solver"]["elapsed_ms"], 3
    )
    applied = [result["slide_index"] for result in results if result.get("applied")]
    outcomes = [
        outcome
        for result in results
        for outcome in result.get("outcomes", [])
        if isinstance(outcome, Mapping)
    ]
    candidate_count = len(outcomes)
    accepted_candidate_count = sum(
        outcome.get("validation_status") == "accepted" for outcome in outcomes
    )
    rejected_candidate_count = sum(
        outcome.get("validation_status") in {"rejected", "inconclusive"}
        for outcome in outcomes
    )
    rejection_reasons: dict[str, int] = {}
    for result in results:
        for code in result.get("reason_codes", []):
            rejection_reasons[str(code)] = rejection_reasons.get(str(code), 0) + 1
    for outcome in outcomes:
        for code in outcome.get("reason_codes", []):
            rejection_reasons[str(code)] = rejection_reasons.get(str(code), 0) + 1
    funnel["deck_wall_ms"] = round(
        (time.perf_counter() - deck_started) * 1000,
        3,
    )
    performance_mode = (
        "warm"
        if results
        and funnel.get("inspection_cache_hits", 0) == len(results)
        else "cold"
    )
    PIPELINE_PERFORMANCE_WINDOW.record(
        performance_mode,
        {"deck_total": funnel["deck_wall_ms"], **funnel["stage_timings_ms"]},
    )
    render_counters_after = get_render_runtime_counters()
    chrome_hits = (
        int(render_counters_after.get("render_cache_hits", 0))
        - int(render_counters_before.get("render_cache_hits", 0))
        + int(render_counters_after.get("metrics_cache_hits", 0))
        - int(render_counters_before.get("metrics_cache_hits", 0))
        + int(render_counters_after.get("session_reuse_hits", 0))
        - int(render_counters_before.get("session_reuse_hits", 0))
    )
    chrome_misses = int(render_counters_after.get("chrome_process_launches", 0)) - int(
        render_counters_before.get("chrome_process_launches", 0)
    )
    funnel["latency_percentiles"] = PIPELINE_PERFORMANCE_WINDOW.snapshot()
    funnel["stage_usage"] = {
        "llm": {
            "cache_hits": int(funnel.get("proposal_cache_hits", 0)),
            "cache_misses": int(funnel.get("proposal_cache_misses", 0)),
        },
        "chrome": {
            "cache_hits": max(0, chrome_hits),
            "cache_misses": max(0, chrome_misses),
        },
        "acceptance": {
            "cache_hits": int(funnel.get("cache_hits", 0)),
            "cache_misses": int(funnel.get("cache_misses", 0)),
        },
        "postverify": {
            "cache_hits": 0,
            "cache_misses": int(funnel.get("postverify_passed", 0)),
        },
    }
    return CanonicalReviewExecution(
        claimed_positions=claimed,
        data={
            "reviewed_count": len(results),
            "issue_slide_count": sum(
                int(item.get("confirmed_issue_count", 0)) > 0
                for item in results
            ),
            "confirmed_issue_count": sum(
                int(item.get("confirmed_issue_count", 0)) for item in results
            ),
            "incomplete_measurement_count": sum(
                int(item.get("incomplete_measurement_count", 0))
                for item in results
            ),
            "unresolved_confirmed_issue_count": sum(
                int(item.get("unresolved_confirmed_issue_count", 0))
                for item in results
            ),
            "clean_slide_count": sum(
                int(item.get("clean_slide_count", 0)) for item in results
            ),
            "issues": [
                issue
                for item in results
                for issue in item.get("confirmed_issues", [])
                if isinstance(issue, Mapping)
            ],
            "fixed_count": len(applied),
            "candidate_count": candidate_count,
            "accepted_candidate_count": accepted_candidate_count,
            "rejected_candidate_count": rejected_candidate_count,
            "fixed_slide_indices": applied,
            "remaining_issue_count": sum(
                int(item.get("unresolved_confirmed_issue_count", 0))
                for item in results
            ),
            "remaining_issue_indices": [
                item["slide_index"]
                for item in results
                if item.get("remaining_issues")
            ],
            "remaining_issues": [
                issue
                for item in results
                if int(item.get("unresolved_confirmed_issue_count", 0)) > 0
                for issue in item.get("confirmed_issues", [])
                if isinstance(issue, Mapping)
            ],
            "candidate_decisions": outcomes,
            "rejection_reasons": rejection_reasons,
            "version_saved": bool(applied),
            "slide_results": results,
            "routing": {"slidespec": True, "claimed_positions": list(claimed)},
            "metrics": {**funnel, **operational_metrics},
        },
    )


__all__ = ["CanonicalReviewExecution", "execute_slidespec_authored_review"]
