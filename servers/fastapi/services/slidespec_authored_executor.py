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
import hashlib
from io import BytesIO
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
from services.slidespec_repair_context import build_repair_context
from services.slidespec_repair_patterns import (
    PATTERN_POLICY_VERSION,
    PatternObservation,
    RepairPatternRegistry,
    pattern_observation,
    repair_pattern_key,
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
    ProposalBatch,
    RepairConflictFootprint,
    RepairIterationGuard,
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
    RESPONSE_SCHEMA_VERSION as PROPOSAL_RESPONSE_SCHEMA_VERSION,
    propose_slidespec_repair,
)
from utils.llm_provider import get_llm_provider, get_model
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
from utils.slidespec_projection import (
    PROJECTION_CAPABILITY_VERSION,
    ProjectionDryRun,
    dry_project_plan,
    projection_schema_context,
)
from utils.slidespec_solver import (
    FeasibilityResult,
    build_constraint_repair_plan,
    solve_constraints,
)


LOGGER = logging.getLogger(__name__)


def _proposal_llm_enabled() -> bool:
    """Allow deterministic-only repair runs without disabling paired VQA.

    The proposal model and the paired visual acceptance reviewer have different
    reliability characteristics.  Keep the production default unchanged while
    allowing replay/operations to stop proposal calls explicitly when that
    provider path is unhealthy; all deterministic candidates still traverse the
    unchanged hard/content/OCR/source/VQA gates.
    """

    return os.getenv("SLIDESPEC_REPAIR_PROPOSAL_LLM_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }

_DOM_CODE = {
    "clipping": "dom.clipping",
    "off_canvas": "dom.off_canvas",
    "overflow": "dom.overflow",
    "overlap": "dom.unintended_overlap",
}

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


@dataclass(frozen=True, slots=True)
class _PreparedBaselineInput:
    request: RenderRequest
    html: str
    active_pointer: Mapping[str, Any]


_BASELINE_INSPECTION_CACHE: IncrementalInspectionCache[_BaselineInspection] = (
    IncrementalInspectionCache(max_entries=256)
)
_BASELINE_INSPECTION_EPOCH: dict[str, int] = {}


def _baseline_inspection_cache_key(
    *,
    slide_id: object,
    request: RenderRequest,
    html: str,
    adjacent_digest: str,
) -> str:
    """Key every baseline by revision, sealed digests and render environment."""

    source_digest = (
        request.source.htmlHash
        if request.source is not None
        else digest_parts("native-html/1", html)
    )
    return inspection_cache_digest(
        spec_digest=digest_parts(
            "revision-spec/1",
            request.spec.specRevision,
            request.spec.digests.spec,
            _BASELINE_INSPECTION_EPOCH.get(str(slide_id), 0),
        ),
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


async def _get_baseline_inspection(
    *,
    slide_id: object,
    request: RenderRequest,
    html: str,
    adjacent_digest: str,
) -> tuple[_BaselineInspection, bool]:
    cache_key = _baseline_inspection_cache_key(
        slide_id=slide_id,
        request=request,
        html=html,
        adjacent_digest=adjacent_digest,
    )
    cached = _BASELINE_INSPECTION_CACHE.get(cache_key)
    if cached is not None:
        return cached, True

    baseline_png, baseline_metrics = await render_html_to_png_with_metrics(html)
    baseline_report = inspect_authored_dom(html, baseline_metrics)
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
    inspection = _BaselineInspection(
        baseline_png,
        baseline_metrics,
        baseline_report,
        qids,
        baseline_observations,
        baseline_constraints,
        solver_result,
        round((time.perf_counter() - solver_started) * 1000, 3),
    )
    _BASELINE_INSPECTION_CACHE.put(cache_key, inspection)
    return inspection, False


def _invalidate_baseline_inspection(slide_id: object) -> None:
    """Make every pre-publication cache entry unreachable after a mutation."""

    key = str(slide_id)
    _BASELINE_INSPECTION_EPOCH[key] = _BASELINE_INSPECTION_EPOCH.get(key, 0) + 1


def _slide_execution_controls(
    positions: Sequence[int], *, max_incremental_repairs: int
) -> tuple[dict[int, asyncio.Lock], dict[int, RepairIterationGuard]]:
    """Allocate publication and history state at slide, not pass, granularity."""

    return (
        {position: asyncio.Lock() for position in positions},
        {
            position: RepairIterationGuard(
                max_incremental_applies=max_incremental_repairs
            )
            for position in positions
        },
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


def _repair_conflict_footprint(
    request: RenderRequest, plan: RepairPlan
) -> RepairConflictFootprint:
    """Resolve publication dependencies from the canonical layout/constraints."""

    by_id: dict[str, LayoutNode] = {}
    by_semantic: dict[str, LayoutNode] = {}
    parent_by_id: dict[str, LayoutNode | None] = {}

    def visit(node: LayoutNode, parent: LayoutNode | None) -> None:
        by_id[node.layoutId] = node
        parent_by_id[node.layoutId] = parent
        if node.contentSemanticId is not None:
            by_semantic[node.contentSemanticId] = node
        for child in node.children:
            visit(child, node)

    visit(request.spec.layout, None)
    target_refs = set(plan.scope)
    target_refs.update(operation.target for operation in plan.operations)
    target_nodes: set[str] = set()
    for target in target_refs:
        node = by_id.get(target) or by_semantic.get(target)
        target_nodes.add(node.layoutId if node is not None else target)

    ancestor_cones: set[str] = set()
    parent_space_pools: set[str] = set()
    coupled_containers: set[str] = set()
    coupled_kinds = {"connector", "group", "table", "grid", "stack", "frame"}
    content_kinds = {
        content.semanticId: content.kind for content in request.spec.content
    }
    for target in tuple(target_nodes):
        node = by_id.get(target)
        if node is None:
            continue
        parent = parent_by_id.get(node.layoutId)
        if parent is not None:
            parent_space_pools.add(parent.layoutId)
        current: LayoutNode | None = node
        while current is not None:
            if (
                current.kind in coupled_kinds
                or content_kinds.get(current.contentSemanticId or "")
                in coupled_kinds
            ):
                coupled_containers.add(current.layoutId)
            current = parent_by_id.get(current.layoutId)
            if current is not None:
                ancestor_cones.add(current.layoutId)

    def string_refs(value: object) -> set[str]:
        if isinstance(value, str):
            return {value}
        if isinstance(value, Mapping):
            return {
                item
                for nested in value.values()
                for item in string_refs(nested)
            }
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            return {item for nested in value for item in string_refs(nested)}
        return set()

    constraints: set[str] = set()
    dependency_refs = target_refs | target_nodes
    for constraint in request.spec.constraints:
        raw = constraint.model_dump(mode="json", by_alias=True, exclude_none=True)
        if dependency_refs.intersection(string_refs(raw)):
            constraints.add(
                str(raw.get("constraintId") or raw.get("id") or constraint.kind)
            )
    return RepairConflictFootprint(
        nodes=frozenset(target_nodes),
        ancestor_cones=frozenset(ancestor_cones),
        parent_space_pools=frozenset(parent_space_pools),
        constraints=frozenset(constraints),
        coupled_containers=frozenset(coupled_containers),
    )


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
    return build_repair_context(
        request,
        evidence,
        browser_metrics,
        constraint_report,
        render_request_type=RenderRequest,
        render_native=render_slidespec,
    )


def _token_recall(tokens: Sequence[str], text: str | None) -> float:
    if not tokens:
        return 1.0
    normalized = re.sub(r"\s+", "", normalize_ocr_text(text or "")).casefold()
    found = sum(
        re.sub(r"\s+", "", normalize_ocr_text(token)).casefold() in normalized
        for token in tokens
    )
    return found / len(tokens)


async def _inspect_ocr_with_bounded_multiscale(
    image: bytes,
    *,
    expected_text: str,
) -> OcrQualityResult:
    """Retry only borderline OCR at two bounded raster scales.

    Geometry remains the primary repair mechanism.  This retry changes no
    candidate content and cannot turn a clear OCR regression into a pass: it is
    limited to results already near the expected-text threshold and the normal
    baseline/token gates still evaluate the selected OCR result.
    """

    initial = await inspect_authored_ocr(image, expected_text=expected_text)
    metrics = initial.metrics if isinstance(initial.metrics, Mapping) else {}
    cer = float(metrics.get("cer", math.inf))
    missing = float(metrics.get("missing_rate", math.inf))
    if not initial.available or initial.text is None:
        return initial

    def coverage(result: OcrQualityResult) -> tuple[float, float, float, float]:
        comparison = compare_expected_candidate_order_insensitive(
            expected_text,
            result,
            result,
            source_text_preserved=True,
        )
        candidate = comparison.metrics.get("candidate", {})
        character = candidate.get("character", {})
        token = candidate.get("token", {})
        return (
            float(character.get("recall", 0.0)),
            float(token.get("recall", 0.0)),
            float(character.get("precision", 0.0)),
            float(token.get("precision", 0.0)),
        )

    initial_coverage = coverage(initial)
    borderline_coverage = (
        initial_coverage[0] >= 0.55 and initial_coverage[1] >= 0.45
    )
    if not (
        0.03 < cer <= 0.15
        or 0.03 < missing <= 0.08
        or borderline_coverage
    ):
        return initial

    try:
        from PIL import Image

        source = Image.open(BytesIO(image)).convert("RGB")
    except Exception:
        return initial

    def selection_score(
        values: tuple[float, float, float, float],
        *,
        result_cer: float,
        result_missing: float,
        scale: float,
    ) -> tuple[float, float, float, float, float, float]:
        return (
            min(values[0] / 0.75, values[1] / 0.65),
            values[0] + values[1],
            values[2] + values[3],
            -result_cer,
            -result_missing,
            -scale,
        )

    ranked: list[
        tuple[tuple[float, float, float, float, float, float], float, OcrQualityResult]
    ] = [
        (
            selection_score(
                initial_coverage,
                result_cer=cer,
                result_missing=missing,
                scale=1.0,
            ),
            1.0,
            initial,
        )
    ]
    for scale in (1.5, 2.0):
        resized = source.resize(
            (
                max(1, round(source.width * scale)),
                max(1, round(source.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
        encoded = BytesIO()
        resized.save(encoded, format="PNG", optimize=True)
        retry = await inspect_authored_ocr(
            encoded.getvalue(), expected_text=expected_text
        )
        retry_metrics = retry.metrics if isinstance(retry.metrics, Mapping) else {}
        retry_cer = float(retry_metrics.get("cer", math.inf))
        retry_missing = float(retry_metrics.get("missing_rate", math.inf))
        retry_coverage = (0.0, 0.0, 0.0, 0.0)
        if retry.available and retry.text is not None:
            retry_coverage = coverage(retry)
            ranked.append(
                (
                    selection_score(
                        retry_coverage,
                        result_cer=retry_cer,
                        result_missing=retry_missing,
                        scale=scale,
                    ),
                    scale,
                    retry,
                )
            )
        if (
            retry_cer <= 0.03
            and retry_missing <= 0.03
            and retry_coverage[0] >= 0.75
            and retry_coverage[1] >= 0.65
        ):
            break
    _score, selected_scale, selected = max(ranked, key=lambda item: item[0])
    return replace(
        selected,
        metrics={
            **selected.metrics,
            "multiscale_selected": selected_scale,
            "multiscale_attempted": [item[1] for item in ranked],
        },
    )


def _ocr_gate_diagnostics(
    *,
    spec: SlideSpec,
    request: RenderRequest,
    baseline: OcrQualityResult,
    candidate: OcrQualityResult,
    comparison: Any,
) -> dict[str, Any]:
    """Expose text-free token mapping and OCR comparison evidence for replay."""

    bindings_by_semantic: dict[str, list[str]] = {}
    for binding in request.source.bindings if request.source is not None else ():
        bindings_by_semantic.setdefault(binding.semanticId, []).append(binding.qid)
    token_by_name = {token.name: str(token.value) for token in spec.tokens}
    mapped_names = {
        name
        for node in spec.content
        for name in node.importantTokens
        if name in token_by_name
    }
    entries: list[dict[str, Any]] = []
    baseline_text = baseline.text or ""
    candidate_text = candidate.text or ""
    for token in spec.tokens:
        if not token.important and token.name not in mapped_names:
            continue
        value = str(token.value)
        normalized = re.sub(r"\s+", "", normalize_ocr_text(value)).casefold()
        semantic_ids = [
            node.semanticId
            for node in spec.content
            if token.name in node.importantTokens
        ]
        entries.append(
            {
                "token_name": token.name,
                "token_digest": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                "semantic_ids": semantic_ids,
                "qids": sorted(
                    {
                        qid
                        for semantic_id in semantic_ids
                        for qid in bindings_by_semantic.get(semantic_id, ())
                    }
                ),
                "mapping_complete": bool(semantic_ids)
                and all(bindings_by_semantic.get(value) for value in semantic_ids),
                "baseline_present": normalized
                in re.sub(r"\s+", "", normalize_ocr_text(baseline_text)).casefold(),
                "candidate_present": normalized
                in re.sub(r"\s+", "", normalize_ocr_text(candidate_text)).casefold(),
            }
        )
    return {
        "comparison": comparison.to_dict(),
        "baseline": baseline.to_dict(),
        "candidate": candidate.to_dict(),
        "important_tokens": entries,
        "important_token_count": len(entries),
        "token_mapping_complete": bool(entries)
        and all(item["mapping_complete"] for item in entries),
        "token_mapping_status": "complete" if entries else "not_applicable",
        "crop_evidence_available": False,
    }


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


def _incomplete_measurement_payload(evidence: DomEvidence) -> list[dict[str, Any]]:
    """Project one row per missing physical measurement, retaining consumers."""

    result: list[dict[str, Any]] = []
    for (scope, metric), atoms in evidence.incomplete_measurement_groups:
        result.append(
            {
                "scope": scope,
                "metric": metric,
                "state": "unknown",
                "sensors": sorted({atom.sensor for atom in atoms}),
                "stages": sorted({atom.stage for atom in atoms}),
                "qids": sorted({atom.qid for atom in atoms if atom.qid}),
                "semantic_ids": sorted(
                    {atom.semantic_id for atom in atoms if atom.semantic_id}
                ),
                "constraint_ids": sorted(
                    {atom.constraint_id for atom in atoms if atom.constraint_id}
                ),
                "reason_codes": sorted(
                    {atom.reason_code for atom in atoms if atom.reason_code}
                ),
                "dependency_refs": sorted(
                    {
                        dependency
                        for atom in atoms
                        for dependency in atom.dependency_refs
                    }
                ),
                "evidence_atom_count": len(atoms),
            }
        )
    return result


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


def _maximum_repair_path(
    outcomes: Sequence[Mapping[str, Any]],
) -> str:
    """Expose the internal route without turning it into a user choice."""

    terminal = _canonical_terminal_outcome(outcomes)
    plan_id = str(terminal.get("plan_id") or "")
    source = str(terminal.get("source") or "")
    if plan_id.startswith("rp_pattern_"):
        return "pattern"
    if source == "llm":
        return "llm_repair_plan"
    return "deterministic" if terminal else "none"


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
            elif key == "no_candidate_reasons" and isinstance(value, list):
                telemetry[key] = sorted(
                    set(telemetry.get(key, ())) | {str(item) for item in value}
                )
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


def _execution_rollout(
    presentation_id: uuid.UUID,
    *,
    position: int,
    opt_in: bool,
    owner_id: object | None,
    dry_run: bool,
):
    rollout = resolve_slidespec_repair_rollout(
        presentation_id, slide_index=position, opt_in=opt_in, owner_id=owner_id
    )
    if dry_run and rollout.should_run_repair:
        # Dry-run is an authority boundary, not merely a telemetry mode.  Force
        # the canonical pipeline down its shadow path even when the process-wide
        # apply flag is enabled, so publisher callbacks and their DB/CAS writes
        # are unreachable by construction.
        rollout = replace(
            rollout,
            route="repair_shadow",
            repair_shadow=True,
            apply_enabled=False,
            reason_code="legacy_preserved.dry_run",
        )
    return rollout


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
    pattern_registry: RepairPatternRegistry | None = None,
    iteration_guard: RepairIterationGuard | None = None,
    prepared_baseline: _PreparedBaselineInput | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    # Keep the identifier outside SQLAlchemy's expiration lifecycle.  A rollback
    # expires ORM attributes, and fail-closed telemetry must never trigger a
    # second database access while handling the original error.
    slide_id = slide.id
    rollout = _execution_rollout(
        presentation_id,
        position=position,
        opt_in=opt_in,
        owner_id=owner_id,
        dry_run=dry_run,
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
    current_pointer = (
        slide.properties.get(ACTIVE_POINTER_KEY)
        if isinstance(slide.properties, Mapping)
        else None
    )
    if (
        prepared_baseline is not None
        and isinstance(current_pointer, Mapping)
        and dict(current_pointer) == dict(prepared_baseline.active_pointer)
        and slide.html_content == prepared_baseline.html
    ):
        request = prepared_baseline.request
        baseline_html = prepared_baseline.html
    else:
        try:
            bundle = await load_active_slidespec_bundle(session, slide)
            if bundle is None:  # defensive; callers claim only pointer-bearing slides
                return {"slide_index": position, "route": rollout.route, "applied": False,
                        "reason_codes": ["artifact.pointer.missing"], "metrics": empty_metrics()}
            request = _render_request(bundle)
            baseline_html = bundle.html
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

    try:
        baseline, inspection_cache_hit = await _get_baseline_inspection(
            slide_id=slide_id,
            request=request,
            html=baseline_html,
            adjacent_digest=adjacent_digest,
        )
    except Exception:  # renderer boundary is deliberately fail closed
        return {"slide_index": position, "route": rollout.route, "applied": False,
                "reason_codes": ["render.failed", "legacy_preserved"], "metrics": empty_metrics()}
    baseline_png = baseline.png
    baseline_metrics = baseline.metrics
    baseline_report = baseline.report
    qids = baseline.qids
    baseline_observations = baseline.observations
    baseline_constraints = baseline.constraints
    solver_result = baseline.solver_result
    solver_elapsed_ms = 0.0 if inspection_cache_hit else baseline.solver_elapsed_ms
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
    pattern_key = repair_pattern_key(request, baseline_evidence)
    expected = _expected_text(request.spec)
    tokens = _important_tokens(request.spec)
    baseline_ocr: OcrQualityResult | None = None
    candidates: dict[str, _CandidateState] = {}
    projection_rejections: list[dict[str, str]] = []

    def record_projection_rejections(
        plan: RepairPlan | None,
        projection: ProjectionDryRun,
        *,
        stage: str,
    ) -> None:
        for rejection in projection.rejections:
            payload = {
                "target": rejection.target,
                "feature": rejection.operation,
                "reason_code": rejection.reason_code
                or "render.source_projection.unsupported",
                "stage": stage,
            }
            if rejection.qid is not None:
                payload["qid"] = rejection.qid
            if plan is not None:
                payload["plan_id"] = plan.plan_id
            if payload not in projection_rejections:
                projection_rejections.append(payload)

    async def deterministic(_spec: SlideSpecInput, dom: DomEvidence) -> Sequence[RepairPlan]:
        reused = (
            pattern_registry.instantiate(pattern_key, request)
            if pattern_registry is not None
            else ()
        )
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
            *(asdict(item) for item in cascade.coupled_rejections),
        ]
        proposed = (
            reused
            + ((solver_plan,) if solver_plan is not None else ())
            + cascade.plans
        )
        projectable: list[RepairPlan] = []
        for plan in proposed:
            projection = dry_project_plan(request, plan)
            if projection.supported:
                projectable.append(plan)
            else:
                record_projection_rejections(
                    plan, projection, stage="deterministic_preflight"
                )
        return tuple(projectable)

    async def validate(_spec: SlideSpecInput, plan: RepairPlan) -> SensorResult:
        projection = dry_project_plan(request, plan)
        if not projection.supported:
            record_projection_rejections(plan, projection, stage="proposal_preflight")
            return SensorResult(
                False,
                projection.reason_code or "proposal_preflight_unprojectable",
                diagnostics={
                    "projection_rejections": [
                        rejection.telemetry() for rejection in projection.rejections
                    ]
                },
            )
        try:
            applied = apply_repair_plan(request.spec, plan)
            validate_slidespec(applied.spec)
            feasibility = solve_constraints(
                applied.spec,
                compute_unsat_core=False,
            )
            if not feasibility.feasible and feasibility.search_complete:
                return SensorResult(
                    False,
                    "constraint.static_infeasible",
                    diagnostics={"solver": feasibility.metrics()},
                )
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
            baseline_ocr = await _inspect_ocr_with_bounded_multiscale(
                baseline_png, expected_text=expected
            )
        result = await _inspect_ocr_with_bounded_multiscale(
            state.image, expected_text=expected
        )
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
        diagnostics = _ocr_gate_diagnostics(
            spec=request.spec,
            request=request,
            baseline=baseline_ocr,
            candidate=result,
            comparison=comparison,
        )
        if not comparison.no_regression:
            return SensorResult(
                False, "ocr.recall_regressed", diagnostics=diagnostics
            )
        if recall < 1.0:
            return SensorResult(
                False, "ocr.important_token_missing", diagnostics=diagnostics
            )
        return SensorResult(True, diagnostics=diagnostics)

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
        resolved_issue_count = 0
        resolved_issue_severity = 0.0
        if target_issue_deltas is not None:
            severity_by_identity = {
                (defect.code, defect.semantic_id, defect.qid): (
                    2.0 if defect.severity == "high" else 1.0
                )
                for defect in baseline_evidence.defects
            }
            for delta in target_issue_deltas:
                if _target_issue_failure_code(
                    (delta.identity,), (delta,)
                ) is not None:
                    continue
                resolved_issue_count += 1
                resolved_issue_severity += severity_by_identity.get(
                    (
                        delta.identity.code,
                        delta.identity.semantic_id,
                        delta.identity.qid,
                    ),
                    1.0,
                )
        remaining_issue_count = max(
            0, len(required_target_issues) - resolved_issue_count
        )
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
        quality_gain = style_comparison_quality_gain(critique)
        quality_interval = style_comparison_quality_interval(critique)
        return SensorResult(
            accepted,
            None if accepted else decision_reason,
            conclusive=conclusive,
            quality_gain=quality_gain,
            quality_gain_interval=quality_interval,
            resolved_issue_count=resolved_issue_count,
            resolved_issue_severity=resolved_issue_severity,
            remaining_issue_count=remaining_issue_count,
            quality_margin=(
                quality_interval[0]
                if accepted and quality_interval is not None
                else None
            ),
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
            candidate_blueprint = build_slidespec_artifact_bundle(
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
                candidate_artifact = candidate_blueprint
                session.add(candidate_artifact)
                await session.flush()
            elif (
                candidate_artifact.render_input_hash
                != candidate_blueprint.render_input_hash
                or candidate_artifact.html_hash != candidate_blueprint.html_hash
                or candidate_artifact.render_hash != candidate_blueprint.render_hash
                or candidate_artifact.manifest != candidate_blueprint.manifest
            ):
                raise SlideSpecArtifactError(
                    "artifact.publish.render_fingerprint_conflict"
                )
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

            published_blueprint = build_slidespec_artifact_bundle(
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
                artifact = published_blueprint
            elif (
                artifact.render_input_hash != published_blueprint.render_input_hash
                or artifact.html_hash != published_blueprint.html_hash
                or artifact.render_hash != published_blueprint.render_hash
                or artifact.manifest != published_blueprint.manifest
            ):
                raise SlideSpecArtifactError(
                    "artifact.publish.render_fingerprint_conflict"
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
            _invalidate_baseline_inspection(slide.id)
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
            # The UPDATE and its audit rows share this transaction.  A commit
            # failure rolls both back, so never report the in-memory CAS result
            # as a durable rollback.
            rollback_succeeded = False
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
        """Skip the model when diagnosed targets have no provable projection."""

        def allow_pattern_call() -> SensorResult:
            if pattern_registry is None or pattern_registry.claim_llm(pattern_key):
                return SensorResult(True)
            return SensorResult(False, "proposal.pattern_call_already_used")

        target_semantic_ids = sorted(
            {
                semantic_id
                for defect in dom.defects
                for semantic_id in (defect.semantic_id, *defect.related_semantic_ids)
                if semantic_id
            }
        )
        capability = projection_schema_context(
            request, target_semantic_ids=target_semantic_ids
        )
        if capability["supportedOperations"]:
            return allow_pattern_call()
        projection_rejections.append(
            {
                "target": ",".join(target_semantic_ids) or "diagnostic-scope",
                "feature": "closed-operation-vocabulary",
                "reason_code": "proposal_preflight_unprojectable",
                "stage": "llm_call_preflight",
            }
        )
        return SensorResult(False, "proposal_preflight_unprojectable")

    async def propose(spec_input: SlideSpecInput, dom: DomEvidence) -> ProposalBatch:
        target_semantic_ids = sorted(
            {
                semantic_id
                for defect in dom.defects
                for semantic_id in (defect.semantic_id, *defect.related_semantic_ids)
                if semantic_id
            }
        )
        capability = projection_schema_context(
            request, target_semantic_ids=target_semantic_ids
        )
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
            projection_context=capability,
        )
        projectable: list[RepairPlan] = []
        if result.ok:
            for plan in result.plans:
                projection = dry_project_plan(request, plan)
                if projection.supported:
                    projectable.append(plan)
                else:
                    record_projection_rejections(
                        plan, projection, stage="llm_proposal_preflight"
                    )
        return ProposalBatch(
            plans=tuple(projectable),
            no_candidate_reason=(
                result.no_candidate_reason
                if result.no_candidate_reason is not None or projectable
                else "proposal_preflight_unprojectable"
            ),
            call_count=result.call_count,
            response_count=result.response_count,
            schema_pass_count=result.schema_pass_count,
            provider_failure_count=result.provider_failure_count,
            schema_rejection_count=result.schema_rejection_count,
            empty_count=result.empty_count,
            negative_cache_hit_count=result.negative_cache_hit_count,
            circuit_open_count=result.circuit_open_count,
        )

    capability_context = projection_schema_context(request)
    capability_digest = digest_parts(
        PIPELINE_CAPABILITY_VERSION,
        CASCADE_POLICY_VERSION,
        PROJECTION_CAPABILITY_VERSION,
        capability_context,
    )
    try:
        proposal_model = str(get_model() or "")
        selected_provider = get_llm_provider()
        proposal_provider = str(
            getattr(selected_provider, "value", selected_provider) or ""
        )
    except Exception:
        proposal_model = "unconfigured"
        proposal_provider = "unconfigured"
    model_digest = digest_parts(
        proposal_provider,
        proposal_model,
        PROPOSAL_RESPONSE_SCHEMA_VERSION,
        PROPOSAL_PROMPT_VERSION,
    )

    def partition_pattern_candidates(
        plans: Sequence[RepairPlan],
    ) -> tuple[Sequence[RepairPlan], Sequence[RepairPlan]]:
        preferred = tuple(
            plan
            for plan in plans
            if plan.provenance.policy_version == PATTERN_POLICY_VERSION
        )
        remaining = tuple(
            plan
            for plan in plans
            if plan.provenance.policy_version != PATTERN_POLICY_VERSION
        )
        return preferred, remaining

    def remember_applied_pattern(plan: RepairPlan) -> None:
        if pattern_registry is not None:
            pattern_registry.record_applied(pattern_key, request, plan)

    proposal_llm_enabled = _proposal_llm_enabled()
    pipeline = SlideSpecRepairPipeline(
        SlideSpecRepairDependencies(
            deterministic_cascade=deterministic,
            validate_plan=validate,
            render_plan=render,
            inspect_dom=inspect_dom,
            run_ocr=run_ocr,
            run_vqa=run_vqa,
            publish=publish,
            propose=propose if proposal_llm_enabled else None,
            proposal_preflight=proposal_preflight if proposal_llm_enabled else None,
            proposal_capability_digest=capability_digest,
            proposal_model_digest=model_digest,
            candidate_partition=partition_pattern_candidates,
            applied_plan_observer=remember_applied_pattern,
            plan_footprint=lambda plan: _repair_conflict_footprint(request, plan),
            iteration_guard=iteration_guard or RepairIterationGuard(),
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
        "incomplete_measurements": _incomplete_measurement_payload(
            baseline_evidence
        ),
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
            "composite_plan_count": result.telemetry.composite_plan_count,
            "composite_operations_merged": result.telemetry.composite_operations_merged,
            "render_candidates_rejected_early": result.telemetry.render_candidates_rejected_early,
            "full_render_count": result.telemetry.full_render_count,
            "llm_pattern_call_count": result.telemetry.llm_pattern_call_count,
            "llm_response_count": result.telemetry.llm_response_count,
            "llm_schema_pass_count": result.telemetry.llm_schema_pass_count,
            "llm_preflight_pass_count": result.telemetry.llm_preflight_pass_count,
            "llm_render_count": result.telemetry.llm_render_count,
            "llm_provider_failures": result.telemetry.llm_provider_failures,
            "llm_schema_rejections": result.telemetry.llm_schema_rejections,
            "llm_empty_responses": result.telemetry.llm_empty_responses,
            "llm_circuit_open_count": result.telemetry.llm_circuit_open_count,
            "llm_negative_cache_hits": result.telemetry.llm_negative_cache_hits,
            "no_candidate_reasons": list(result.telemetry.no_candidate_reasons),
            # Canonical public funnel contract.  Keep the internal aliases above
            # for existing consumers while the UI migrates.
            "llm_valid_plan_count": result.telemetry.llm_candidates,
            "llm_provider_failure_count": result.telemetry.llm_provider_failures,
            "llm_schema_reject_count": result.telemetry.llm_schema_rejections,
            "llm_rendered_count": result.telemetry.llm_render_count,
            "proposal_preflight_unprojectable_count": (
                result.telemetry.proposal_preflight_skips
            ),
            "negative_cache_hit_count": result.telemetry.llm_negative_cache_hits,
            "individual_fallback_count": result.telemetry.individual_fallback_count,
            "repeated_plan_blocked": result.telemetry.repeated_plan_blocked,
            "oscillation_blocked": result.telemetry.oscillation_blocked,
            "iteration_limit_blocked": result.telemetry.iteration_limit_blocked,
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
    pattern_registry = RepairPatternRegistry()
    max_incremental_repairs = 12
    publication_locks, iteration_guards = _slide_execution_controls(
        claimed, max_incremental_repairs=max_incremental_repairs
    )
    bind = session.bind
    worker_sessions = (
        async_sessionmaker(bind, expire_on_commit=False)
        if len(claimed) > 1 and bind is not None
        else None
    )

    async def preinspect_position(
        position: int, worker_session: AsyncSession
    ) -> tuple[PatternObservation | None, bool, _PreparedBaselineInput | None]:
        """Populate the immutable baseline cache and emit one strict fingerprint."""

        slide = await worker_session.get(
            SlideModel, slide_ids[position], populate_existing=True
        )
        if slide is None:
            return None, False, None
        rollout = resolve_slidespec_repair_rollout(
            presentation_id,
            slide_index=position,
            opt_in=opt_in,
            owner_id=owner_id,
        )
        if not rollout.should_run_repair:
            return None, False, None
        try:
            bundle = await load_active_slidespec_bundle(worker_session, slide)
            if bundle is None:
                return None, False, None
            request = _render_request(bundle)
            baseline, cache_hit = await _get_baseline_inspection(
                slide_id=slide.id,
                request=request,
                html=bundle.html,
                adjacent_digest=adjacency[position],
            )
            evidence = _merge_dom_evidence(
                _dom_evidence(
                    baseline.report,
                    baseline.qids,
                    browser_metrics=baseline.metrics,
                ),
                _constraint_evidence(
                    request, baseline.constraints, baseline.observations
                ),
            )
            pointer = (
                slide.properties.get(ACTIVE_POINTER_KEY)
                if isinstance(slide.properties, Mapping)
                else None
            )
            prepared = (
                _PreparedBaselineInput(request, bundle.html, dict(pointer))
                if isinstance(pointer, Mapping)
                else None
            )
            return pattern_observation(position, request, evidence), cache_hit, prepared
        except Exception:
            # The normal slide path retains the canonical fail-closed diagnostic.
            return None, False, None

    preinspection_started = time.perf_counter()
    if worker_sessions is not None:
        async def isolated_preinspect(
            position: int,
        ) -> tuple[PatternObservation | None, bool, _PreparedBaselineInput | None]:
            async with worker_sessions() as worker_session:
                return await preinspect_position(position, worker_session)

        preinspection_results = await scheduler.map(claimed, isolated_preinspect)
    else:
        preinspection_results = tuple(
            [await preinspect_position(position, session) for position in claimed]
        )
    preinspection_elapsed_ms = (
        time.perf_counter() - preinspection_started
    ) * 1000
    pattern_registry.prepare(
        tuple(
            observation
            for observation, _cache_hit, _prepared in preinspection_results
            if observation is not None
        ),
        elapsed_ms=preinspection_elapsed_ms,
        cache_hits=sum(
            prepared is not None and cache_hit
            for _observation, cache_hit, prepared in preinspection_results
        ),
        cache_misses=sum(
            prepared is not None and not cache_hit
            for _observation, cache_hit, prepared in preinspection_results
        ),
        inspected_count=sum(
            prepared is not None
            for _observation, _cache_hit, prepared in preinspection_results
        ),
    )
    prepared_baselines = {
        position: prepared
        for position, (_observation, _cache_hit, prepared) in zip(
            claimed, preinspection_results, strict=True
        )
        if prepared is not None
    }

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
        prepared_baseline = prepared_baselines.get(position)
        while accepted_repairs < max_incremental_repairs:
            current = await _run_one(
                presentation_id=presentation_id,
                slide=slide,
                deck_snapshot=deck_snapshot,
                position=position,
                opt_in=opt_in,
                owner_id=owner_id,
                session=worker_session,
                adjacent_digest=adjacency[position],
                publish_gate=publication_locks[position],
                pattern_registry=pattern_registry,
                iteration_guard=iteration_guards[position],
                prepared_baseline=prepared_baseline,
                dry_run=dry_run,
            )
            prepared_baseline = None
            passes.append(current)
            if not current.get("applied") or dry_run:
                complete = not bool(current.get("trigger"))
                break
            accepted_repairs += 1
            if accepted_repairs >= max_incremental_repairs:
                current.setdefault("reason_codes", []).append(
                    "incremental_repair_limit_reached"
                )
                current["unresolved_confirmed_issue_count"] = max(
                    int(current.get("unresolved_confirmed_issue_count", 0)),
                    int(current.get("confirmed_issue_count", 0)),
                )
                current["clean_slide_count"] = 0
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

    async def execute_batch(positions_to_run: Sequence[int]) -> tuple[dict[str, Any], ...]:
        if not positions_to_run:
            return ()
        if worker_sessions is not None:
            async def isolated(position: int) -> dict[str, Any]:
                async with worker_sessions() as worker_session:
                    return await run_position(position, worker_session)

            return await scheduler.map(positions_to_run, isolated)
        return tuple(
            [await run_position(position, session) for position in positions_to_run]
        )

    results_by_position: dict[int, dict[str, Any]] = {}
    representative_positions: set[int] = set()
    for wave in pattern_registry.representative_waves():
        representative_positions.update(wave)
        wave_results = await execute_batch(wave)
        results_by_position.update(zip(wave, wave_results, strict=True))
    remaining_positions = tuple(
        position for position in claimed if position not in representative_positions
    )
    remaining_results = await execute_batch(remaining_positions)
    results_by_position.update(
        zip(remaining_positions, remaining_results, strict=True)
    )
    raw_results = tuple(results_by_position[position] for position in claimed)

    # The staged waves validate low/mid/high-pressure representatives before
    # general reuse. Singleton fingerprints are only in ``remaining`` and
    # therefore always enter the individual deterministic path.

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
                "path": _maximum_repair_path(outcomes_for_slide),
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
        "composite_plan_count",
        "composite_operations_merged",
        "render_candidates_rejected_early",
        "full_render_count",
        "llm_pattern_call_count",
        "llm_response_count",
        "llm_schema_pass_count",
        "llm_preflight_pass_count",
        "llm_render_count",
        "llm_provider_failures",
        "llm_schema_rejections",
        "llm_empty_responses",
        "llm_circuit_open_count",
        "llm_negative_cache_hits",
        "llm_valid_plan_count",
        "llm_provider_failure_count",
        "llm_schema_reject_count",
        "llm_rendered_count",
        "proposal_preflight_unprojectable_count",
        "negative_cache_hit_count",
        "individual_fallback_count",
        "repeated_plan_blocked",
        "oscillation_blocked",
        "iteration_limit_blocked",
    )
    for key in telemetry_keys:
        funnel[key] = sum(
            int(result.get("telemetry", {}).get(key, 0)) for result in results
        )
    funnel["no_candidate_reasons"] = sorted(
        {
            str(reason)
            for result in results
            for reason in result.get("telemetry", {}).get(
                "no_candidate_reasons", ()
            )
            if str(reason)
        }
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
    pattern_metrics = pattern_registry.metrics()
    funnel.update(pattern_metrics)
    funnel.setdefault("stage_timings_ms", {})["pattern_preinspection"] = float(
        pattern_metrics.get("pattern_preinspection_ms", 0.0)
    )
    funnel["preinspection_render_count"] = int(
        pattern_metrics.get("pattern_preinspection_cache_misses", 0)
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
    detailed_issue_count = sum(
        int(item.get("confirmed_issue_count", 0)) for item in results
    )
    remaining_issue_count = sum(
        int(item.get("unresolved_confirmed_issue_count", 0))
        for item in results
    )
    resolved_issue_count = max(0, detailed_issue_count - remaining_issue_count)
    remaining_slide_indices = [
        item["slide_index"]
        for item in results
        if int(item.get("unresolved_confirmed_issue_count", 0)) > 0
        or int(item.get("incomplete_measurement_count", 0)) > 0
    ]
    funnel.update(
        {
            "detailed_issue_count": detailed_issue_count,
            "resolved_issue_count": resolved_issue_count,
            "remaining_slide_count": len(remaining_slide_indices),
            "remaining_issue_count": remaining_issue_count,
        }
    )
    return CanonicalReviewExecution(
        claimed_positions=claimed,
        data={
            "reviewed_count": len(results),
            "issue_slide_count": sum(
                int(item.get("confirmed_issue_count", 0)) > 0
                for item in results
            ),
            "detailed_issue_count": detailed_issue_count,
            "confirmed_issue_count": detailed_issue_count,
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
            "resolved_issue_count": resolved_issue_count,
            "candidate_count": candidate_count,
            "accepted_candidate_count": accepted_candidate_count,
            "rejected_candidate_count": rejected_candidate_count,
            "fixed_slide_indices": applied,
            "remaining_slide_count": len(remaining_slide_indices),
            "remaining_issue_count": remaining_issue_count,
            "remaining_issue_indices": remaining_slide_indices,
            "remaining_slide_indices": remaining_slide_indices,
            "remaining_issues": [
                issue
                for item in results
                if int(item.get("unresolved_confirmed_issue_count", 0)) > 0
                for issue in item.get("confirmed_issues", [])
                if isinstance(issue, Mapping)
            ],
            "candidate_decisions": outcomes,
            "rejection_reasons": rejection_reasons,
            **pattern_metrics,
            "version_saved": bool(applied),
            "slide_results": results,
            "routing": {"slidespec": True, "claimed_positions": list(claimed)},
            "metrics": {**funnel, **operational_metrics},
        },
    )


__all__ = ["CanonicalReviewExecution", "execute_slidespec_authored_review"]
