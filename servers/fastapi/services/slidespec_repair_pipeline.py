"""Pure, fail-closed SlideSpec repair orchestration.

The module deliberately has no database or HTML mutation dependency.  Callers
adapt existing render/sensor services at its callback boundaries and retain the
only publication side effect in the publisher callback.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any, Literal

from models.slidespec_v1 import RepairPlan
from services.slidespec_pipeline_performance import (
    DEFAULT_NEGATIVE_PROPOSAL_CACHE,
    NegativeProposalCache,
    ProposalCacheKey,
    deterministic_candidate_order,
    dom_evidence_digest,
    pareto_prune_plans,
)
from services.slidespec_repair import repair_cost
from services.slidespec_rollout_policy import SlideSpecRepairRolloutDecision
from utils.llm_calls.slidespec_repair_resilience import NoCandidateReason


ReasonCode = str
EvidenceState = Literal[
    "proven_clean", "proven_issue", "unknown", "not_applicable"
]

_EVIDENCE_STATE_RANK: dict[EvidenceState, int] = {
    "not_applicable": 0,
    "unknown": 1,
    "proven_clean": 2,
    "proven_issue": 3,
}


@dataclass(frozen=True, slots=True)
class EvidenceAtom:
    """One independently mergeable sensor observation.

    ``dependency_refs`` names the smallest known element/constraint/stacking
    dependency cone for the observation.  It deliberately excludes a slide-wide
    ancestor: two elements sharing only the slide root are not publication
    dependencies of one another.
    """

    sensor: str
    metric: str
    state: EvidenceState
    stage: str = "detection"
    qid: str | None = None
    semantic_id: str | None = None
    constraint_id: str | None = None
    dependency_refs: tuple[str, ...] = ()
    reason_code: ReasonCode | None = None
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    global_scope: bool = False

    @property
    def key(self) -> tuple[str, str, str, str | None, str | None, str | None]:
        return (
            self.sensor,
            self.metric,
            self.stage,
            self.qid,
            self.semantic_id,
            self.constraint_id,
        )

    @property
    def references(self) -> frozenset[str]:
        refs = set(self.dependency_refs)
        if self.qid:
            refs.update((self.qid, f"qid:{self.qid}"))
        if self.semantic_id:
            refs.update((self.semantic_id, f"semantic:{self.semantic_id}"))
        if self.constraint_id:
            refs.update(
                (self.constraint_id, f"constraint:{self.constraint_id}")
            )
        return frozenset(refs)


def merge_evidence_atoms(
    *groups: Sequence[EvidenceAtom],
) -> tuple[EvidenceAtom, ...]:
    """Join evidence monotonically; weaker/unknown data cannot erase proof."""

    merged: dict[
        tuple[str, str, str, str | None, str | None, str | None], EvidenceAtom
    ] = {}
    for group in groups:
        for atom in group:
            current = merged.get(atom.key)
            if current is None:
                merged[atom.key] = atom
                continue
            current_rank = _EVIDENCE_STATE_RANK[current.state]
            atom_rank = _EVIDENCE_STATE_RANK[atom.state]
            if atom_rank > current_rank:
                merged[atom.key] = atom
                continue
            if atom_rank < current_rank:
                continue
            # Equal-state joins retain the union of dependency proof and stable,
            # deterministic diagnostics.  The state itself never moves backward.
            merged[atom.key] = EvidenceAtom(
                sensor=current.sensor,
                metric=current.metric,
                state=current.state,
                stage=current.stage,
                qid=current.qid,
                semantic_id=current.semantic_id,
                constraint_id=current.constraint_id,
                dependency_refs=tuple(
                    sorted(set(current.dependency_refs) | set(atom.dependency_refs))
                ),
                reason_code=current.reason_code or atom.reason_code,
                diagnostics={**dict(atom.diagnostics), **dict(current.diagnostics)},
                global_scope=current.global_scope or atom.global_scope,
            )
    return tuple(merged[key] for key in sorted(merged, key=lambda item: tuple(str(v) for v in item)))


@dataclass(frozen=True, slots=True)
class SlideSpecInput:
    """Minimal canonical content view; browser text is intentionally absent."""

    revision_id: str
    spec_hash: str
    semantic_text: Mapping[str, str]
    important_tokens: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CanonicalOcrInventory:
    text_by_semantic_id: Mapping[str, str]
    important_tokens: tuple[str, ...]


def canonical_ocr_inventory(spec: SlideSpecInput) -> CanonicalOcrInventory:
    """Produce OCR truth solely from the immutable SlideSpec content plane."""

    return CanonicalOcrInventory(
        text_by_semantic_id=dict(sorted(spec.semantic_text.items())),
        important_tokens=tuple(sorted(set(spec.important_tokens))),
    )


@dataclass(frozen=True, slots=True)
class DomDefect:
    code: str
    severity: Literal["high", "medium", "low"]
    semantic_id: str
    related_semantic_ids: tuple[str, ...] = ()
    qid: str | None = None
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    physical_issue_id: str | None = None


@dataclass(frozen=True, slots=True)
class DomEvidence:
    defects: tuple[DomDefect, ...] = ()
    telemetry_complete: bool | None = None
    atoms: tuple[EvidenceAtom, ...] = ()

    def __post_init__(self) -> None:
        issue_atoms = tuple(
            EvidenceAtom(
                sensor="dom" if defect.code.startswith("dom.") else "constraint",
                metric=defect.code,
                state="proven_issue",
                qid=defect.qid,
                semantic_id=defect.semantic_id,
                constraint_id=(
                    defect.related_semantic_ids[0]
                    if defect.code == "constraint.evidence.missing"
                    and defect.related_semantic_ids
                    else None
                ),
                dependency_refs=tuple(defect.related_semantic_ids),
                diagnostics={
                    **dict(defect.diagnostics),
                    **(
                        {"physical_issue_id": defect.physical_issue_id}
                        if defect.physical_issue_id
                        else {}
                    ),
                },
            )
            for defect in self.defects
            if defect.code != "constraint.evidence.missing"
        )
        atoms = merge_evidence_atoms(self.atoms, issue_atoms)
        if self.telemetry_complete is False and not any(
            atom.state == "unknown" for atom in atoms
        ):
            # Legacy callers supplied only the global boolean.  Preserve their
            # fail-closed behavior until they provide a scoped atom.
            atoms = merge_evidence_atoms(
                atoms,
                (
                    EvidenceAtom(
                        sensor="legacy_dom",
                        metric="telemetry_complete",
                        state="unknown",
                        reason_code="dom.telemetry_incomplete",
                        global_scope=True,
                    ),
                ),
            )
        object.__setattr__(self, "atoms", atoms)
        object.__setattr__(
            self,
            "telemetry_complete",
            not any(atom.state == "unknown" for atom in atoms),
        )

    @property
    def required_geometry_defect(self) -> bool:
        return any(
            (
                defect.code
                in {
                    "dom.clipping",
                    "dom.off_canvas",
                    "dom.overflow",
                    "dom.unintended_overlap",
                }
                or defect.code.startswith("constraint.")
            )
            and defect.code != "constraint.evidence.missing"
            and defect.severity in {"high", "medium"}
            for defect in self.defects
        )

    @property
    def hard_failure(self) -> bool:
        return bool(self.blocking_unknown_atoms()) or self.required_geometry_defect

    @property
    def unknown_atoms(self) -> tuple[EvidenceAtom, ...]:
        return tuple(atom for atom in self.atoms if atom.state == "unknown")

    @staticmethod
    def _unknown_measurement_keys(
        atom: EvidenceAtom,
    ) -> tuple[tuple[str, str], ...]:
        """Identify physical missing measurements independently of consumers.

        Chrome's occlusion probe and a readability constraint can report the
        same absent ``occluded_fraction`` sample.  They remain separate audit
        atoms, but counting both as incomplete measurements inflates the review
        funnel and makes one missing sensor value look like two unknowns.
        """

        scope = atom.qid or atom.semantic_id or atom.constraint_id or "global"
        if atom.sensor == "chrome_occlusion" and atom.metric == "occlusion":
            return ((scope, "occluded_fraction"),)
        missing = atom.diagnostics.get("missing_measurements")
        target_qids = atom.diagnostics.get("target_qids")
        if isinstance(missing, Mapping):
            qids = target_qids if isinstance(target_qids, Mapping) else {}
            keys: list[tuple[str, str]] = []
            for target, names in missing.items():
                target_scope = str(qids.get(target) or scope)
                if isinstance(names, Sequence) and not isinstance(names, (str, bytes)):
                    keys.extend((target_scope, str(name)) for name in names)
            if keys:
                return tuple(dict.fromkeys(keys))
        return ((scope, f"{atom.sensor}:{atom.metric}:{atom.stage}"),)

    @property
    def incomplete_measurement_groups(
        self,
    ) -> tuple[tuple[tuple[str, str], tuple[EvidenceAtom, ...]], ...]:
        grouped: dict[tuple[str, str], list[EvidenceAtom]] = {}
        for atom in self.unknown_atoms:
            for key in self._unknown_measurement_keys(atom):
                grouped.setdefault(key, []).append(atom)
        return tuple(
            (key, tuple(grouped[key]))
            for key in sorted(grouped)
        )

    @property
    def confirmed_issue_count(self) -> int:
        physical = {
            defect.physical_issue_id
            or ":".join(
                (
                    defect.code,
                    defect.semantic_id,
                    defect.qid or "unavailable",
                )
            )
            for defect in self.defects
            if defect.code != "constraint.evidence.missing"
            and defect.severity in {"high", "medium"}
        }
        return len(physical)

    @property
    def incomplete_measurement_count(self) -> int:
        return len(self.incomplete_measurement_groups)

    @property
    def clean_slide_count(self) -> int:
        return int(
            self.confirmed_issue_count == 0
            and self.incomplete_measurement_count == 0
        )

    @staticmethod
    def _plan_references(plan: RepairPlan | None) -> frozenset[str]:
        if plan is None:
            return frozenset()
        references = set(plan.scope) | set(plan.diagnostic_refs)
        references.update(operation.target for operation in plan.operations)
        for value in tuple(references):
            if value.startswith(("qid:", "semantic:", "constraint:")):
                references.add(value.split(":", 1)[1])
        return frozenset(references)

    def blocking_unknown_atoms(
        self, plan: RepairPlan | None = None
    ) -> tuple[EvidenceAtom, ...]:
        """Return only unknowns that can affect this mutation/publication."""

        unknown = self.unknown_atoms
        if plan is None:
            return tuple(atom for atom in unknown if atom.global_scope)
        references = self._plan_references(plan)
        return tuple(
            atom
            for atom in unknown
            if atom.global_scope or bool(atom.references.intersection(references))
        )


@dataclass(frozen=True, slots=True)
class RenderedCandidate:
    html_hash: str
    render_hash: str
    realized_cost: tuple[int, int, float, float, int, str] | None = None
    constraint_cost: float = 0


@dataclass(frozen=True, slots=True)
class SensorResult:
    passed: bool
    reason_code: ReasonCode | None = None
    conclusive: bool = True
    cache_hit: bool = False
    quality_gain: float | None = None
    quality_gain_interval: tuple[float, float] | None = None
    resolved_issue_count: int | None = None
    resolved_issue_severity: float | None = None
    remaining_issue_count: int | None = None
    quality_margin: float | None = None
    diagnostics: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    cas_committed: bool
    active_switched: bool
    readback_hashes_match: bool
    receipt_id: str | None = None
    rolled_back: bool = False
    reason_code: ReasonCode | None = None


@dataclass(frozen=True, slots=True)
class CandidateOutcome:
    plan_id: str
    source: str
    proposal_status: str
    render_status: str
    validation_status: str
    apply_status: str
    publish_status: str
    post_verify_status: str
    applied: bool
    rolled_back: bool = False
    reason_codes: tuple[ReasonCode, ...] = ()
    quality_gain: float | None = None
    quality_gain_interval: tuple[float, float] | None = None
    realized_cost: tuple[int, int, float, float, int, str] | None = None
    selected: bool = False
    diagnostics: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RepairFunnel:
    candidate_proposed: int = 0
    candidate_rendered: int = 0
    candidate_validated: int = 0
    candidate_accepted: int = 0
    candidate_rejected: int = 0
    candidate_inconclusive: int = 0
    apply_committed: int = 0
    publish_active: int = 0
    postverify_passed: int = 0
    applied: int = 0
    rolled_back: int = 0
    legacy_preserved: int = 0

    def metrics(self) -> dict[str, int]:
        """Metric labels stay low-cardinality: no ids, text, or hashes."""

        return {
            "candidate_proposed": self.candidate_proposed,
            "candidate_rendered": self.candidate_rendered,
            "validation_passed": self.candidate_validated,
            "candidate_accepted": self.candidate_accepted,
            "validation_rejected": self.candidate_rejected,
            "validation_inconclusive": self.candidate_inconclusive,
            "apply_committed": self.apply_committed,
            "publish_active": self.publish_active,
            "postverify_passed": self.postverify_passed,
            "applied": self.applied,
            "rolled_back": self.rolled_back,
            "legacy_preserved": self.legacy_preserved,
        }


@dataclass(frozen=True, slots=True)
class PipelineTelemetry:
    stage_timings_ms: Mapping[str, float] = field(default_factory=dict)
    llm_calls: int = 0
    llm_candidates: int = 0
    render_calls: int = 0
    dom_calls: int = 0
    ocr_calls: int = 0
    vqa_calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    proposal_cache_hits: int = 0
    proposal_cache_misses: int = 0
    proposal_preflight_skips: int = 0
    pareto_pruned: int = 0
    composite_plan_count: int = 0
    composite_operations_merged: int = 0
    render_candidates_rejected_early: int = 0
    full_render_count: int = 0
    llm_pattern_call_count: int = 0
    llm_response_count: int = 0
    llm_schema_pass_count: int = 0
    llm_preflight_pass_count: int = 0
    llm_render_count: int = 0
    llm_provider_failures: int = 0
    llm_schema_rejections: int = 0
    llm_empty_responses: int = 0
    llm_circuit_open_count: int = 0
    llm_negative_cache_hits: int = 0
    no_candidate_reasons: tuple[NoCandidateReason, ...] = ()
    individual_fallback_count: int = 0
    repeated_plan_blocked: int = 0
    oscillation_blocked: int = 0
    iteration_limit_blocked: int = 0
    repair_costs: tuple[tuple[int, int, float, float, int, str], ...] = ()
    realized_repair_costs: tuple[tuple[int, int, float, float, int, str], ...] = ()
    quality_gains: tuple[float, ...] = ()
    quality_gain_intervals: tuple[tuple[float, float], ...] = ()
    selected_quality_gain: float | None = None


@dataclass(frozen=True, slots=True)
class RepairConflictFootprint:
    """Slide-local dependency cone used to decide whether plans may merge.

    Callers with a layout tree should supply this explicitly.  The pipeline's
    fallback derives conservative tokens from plan scope and diagnostic refs so
    legacy callers remain compatible without treating raw coordinates as proof
    of independence.
    """

    nodes: frozenset[str] = frozenset()
    ancestor_cones: frozenset[str] = frozenset()
    parent_space_pools: frozenset[str] = frozenset()
    constraints: frozenset[str] = frozenset()
    coupled_containers: frozenset[str] = frozenset()


def _operation_payloads(plan: RepairPlan) -> tuple[dict[str, Any], ...]:
    return tuple(
        operation.model_dump(mode="json", by_alias=True, exclude_none=True)
        for operation in plan.operations
    )


def _plan_signature(plan: RepairPlan) -> str:
    payload = {
        "slide": plan.slide_id,
        "scope": sorted(plan.scope),
        "operations": _operation_payloads(plan),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _operation_state_signature(plan: RepairPlan) -> str:
    """Signature for A->B->A oscillation detection, independent of plan id."""

    encoded = json.dumps(
        _operation_payloads(plan), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class RepairIterationGuard:
    """Request/deck-scoped monotonic repair history for repeated slide passes."""

    max_incremental_applies: int = 12
    _applied_signatures: dict[str, set[str]] = field(default_factory=dict)
    _operation_history: dict[str, list[str]] = field(default_factory=dict)
    _apply_counts: dict[str, int] = field(default_factory=dict)

    def rejection_reason(self, plan: RepairPlan) -> ReasonCode | None:
        slide_id = plan.slide_id
        if self._apply_counts.get(slide_id, 0) >= self.max_incremental_applies:
            return "repair.iteration_limit"
        operation_state = _operation_state_signature(plan)
        history = self._operation_history.get(slide_id, ())
        if len(history) >= 2 and operation_state == history[-2]:
            return "repair.oscillation_detected"
        signature = _plan_signature(plan)
        if signature in self._applied_signatures.get(slide_id, set()):
            return "repair.plan_signature_repeated"
        return None

    def record_applied(self, plan: RepairPlan) -> None:
        slide_id = plan.slide_id
        self._applied_signatures.setdefault(slide_id, set()).add(
            _plan_signature(plan)
        )
        self._operation_history.setdefault(slide_id, []).append(
            _operation_state_signature(plan)
        )
        self._apply_counts[slide_id] = self._apply_counts.get(slide_id, 0) + 1


@dataclass(frozen=True, slots=True)
class _AcceptedCandidate:
    plan: RepairPlan
    artifact: RenderedCandidate
    quality_gain: float
    quality_gain_interval: tuple[float, float]
    constraint_cost: float
    realized_cost: tuple[int, int, float, float, int, str]
    resolved_issue_count: int = 0
    resolved_issue_severity: float = 0
    remaining_issue_count: int | None = None
    quality_margin: float | None = None
    diagnostics: Mapping[str, object] = field(default_factory=dict)


def _rank_accepted_candidates(
    candidates: Sequence[_AcceptedCandidate],
) -> tuple[_AcceptedCandidate, ...]:
    """Rank safe candidates for the single maximum-repair behavior.

    Every item has already passed the invariant, DOM, OCR and VQA gates.  Within
    that safe set, prefer the candidate with the greatest measured improvement;
    change cost is only a tie-break.  Keeping the complete order also lets the
    publisher try the next validated candidate when a candidate-specific final
    gate rejects the first choice.
    """

    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                -item.resolved_issue_count,
                -item.resolved_issue_severity,
                -item.quality_gain,
                -item.quality_gain_interval[0],
                -item.quality_gain_interval[1],
                item.constraint_cost,
                item.realized_cost,
                item.plan.plan_id,
            ),
        )
    )


def _select_accepted_candidate(
    candidates: Sequence[_AcceptedCandidate],
) -> _AcceptedCandidate:
    return _rank_accepted_candidates(candidates)[0]


@dataclass(frozen=True, slots=True)
class RepairPipelineResult:
    route: str
    trigger: str | None
    outcomes: tuple[CandidateOutcome, ...]
    funnel: RepairFunnel
    reason_codes: tuple[ReasonCode, ...]
    telemetry: PipelineTelemetry = field(default_factory=PipelineTelemetry)
    kill_switch_enabled: bool = False
    confirmed_issue_count: int = 0
    incomplete_measurement_count: int = 0
    unresolved_confirmed_issue_count: int = 0
    clean_slide_count: int = 0

    def metrics(self) -> dict[str, object]:
        """Canonical low-cardinality operational metrics for API/dashboard use."""

        return {
            "candidates": {
                "proposed": self.funnel.candidate_proposed,
                "rendered": self.funnel.candidate_rendered,
                "accepted": self.funnel.candidate_accepted,
                "rejected": self.funnel.candidate_rejected,
            },
            "transaction": {"rollback_count": self.funnel.rolled_back},
            "rollout": {"kill_switch_enabled": self.kill_switch_enabled},
        }


ValidatePlan = Callable[[SlideSpecInput, RepairPlan], Awaitable[SensorResult]]
RenderPlan = Callable[[SlideSpecInput, RepairPlan], Awaitable[RenderedCandidate]]
InspectDom = Callable[[RenderedCandidate], Awaitable[DomEvidence]]
RunOcr = Callable[[RenderedCandidate, CanonicalOcrInventory], Awaitable[SensorResult]]
RunVqa = Callable[[RenderedCandidate], Awaitable[SensorResult]]
PublishCandidate = Callable[
    [SlideSpecInput, RepairPlan, RenderedCandidate], Awaitable[PublicationReceipt]
]
@dataclass(frozen=True, slots=True)
class ProposalBatch:
    """Per-request model funnel result without unbounded provider details."""

    plans: tuple[RepairPlan, ...] = ()
    no_candidate_reason: NoCandidateReason | None = None
    call_count: int = 0
    response_count: int = 0
    schema_pass_count: int = 0
    provider_failure_count: int = 0
    schema_rejection_count: int = 0
    empty_count: int = 0
    negative_cache_hit_count: int = 0
    circuit_open_count: int = 0


PlanFactory = Callable[
    [SlideSpecInput, DomEvidence],
    Awaitable[Sequence[RepairPlan] | ProposalBatch],
]
ProposalPreflight = Callable[[SlideSpecInput, DomEvidence], Awaitable[SensorResult]]
CandidatePartition = Callable[
    [Sequence[RepairPlan]], tuple[Sequence[RepairPlan], Sequence[RepairPlan]]
]
AppliedPlanObserver = Callable[[RepairPlan], None]
PlanFootprint = Callable[[RepairPlan], RepairConflictFootprint]


def _default_plan_footprint(plan: RepairPlan) -> RepairConflictFootprint:
    nodes = set(plan.scope)
    nodes.update(operation.target for operation in plan.operations)
    ancestors: set[str] = set()
    parent_space: set[str] = set()
    constraints: set[str] = set()
    coupled: set[str] = set()
    for reference in plan.diagnostic_refs:
        prefix, separator, value = reference.partition(":")
        if not separator or not value:
            continue
        if prefix in {"ancestor", "descendant-cone"}:
            ancestors.add(value)
        elif prefix in {"parent-space", "space-pool"}:
            parent_space.add(value)
        elif prefix in {"constraint", "footprint"}:
            constraints.add(value)
        elif prefix in {"connector", "group", "table", "auto-layout"}:
            coupled.add(f"{prefix}:{value}")
    # Tree-like ids can express ancestry without a separate layout callback.
    for node in tuple(nodes):
        pieces = node.replace("\\", "/").split("/")
        ancestors.update("/".join(pieces[:index]) for index in range(1, len(pieces)))
    return RepairConflictFootprint(
        nodes=frozenset(nodes),
        ancestor_cones=frozenset(ancestors),
        parent_space_pools=frozenset(parent_space),
        constraints=frozenset(constraints),
        coupled_containers=frozenset(coupled),
    )


def _footprints_conflict(
    left: RepairConflictFootprint, right: RepairConflictFootprint
) -> bool:
    return bool(
        left.nodes.intersection(right.nodes)
        or left.nodes.intersection(right.ancestor_cones)
        or right.nodes.intersection(left.ancestor_cones)
        or left.ancestor_cones.intersection(right.ancestor_cones)
        or left.parent_space_pools.intersection(right.parent_space_pools)
        or left.constraints.intersection(right.constraints)
        or left.coupled_containers.intersection(right.coupled_containers)
    )


def _merge_nonconflicting_plans(
    members: Sequence[RepairPlan],
) -> RepairPlan | None:
    if len(members) < 2:
        return None
    first = members[0]
    identity = (
        first.slide_id,
        first.base_spec_revision,
        first.base_spec_hash,
        first.expected_content_digest,
        first.provenance.source,
    )
    if any(
        (
            member.slide_id,
            member.base_spec_revision,
            member.base_spec_hash,
            member.expected_content_digest,
            member.provenance.source,
        )
        != identity
        for member in members[1:]
    ):
        return None
    operations = tuple(
        operation for member in members for operation in member.operations
    )
    scope = tuple(
        dict.fromkeys(
            (
                *(target for member in members for target in member.scope),
                *(operation.target for operation in operations),
            )
        )
    )
    if len(operations) > 16 or len(scope) > 8:
        return None
    max_operations = min(member.budget.maxOperations for member in members)
    max_touched = min(member.budget.maxTouchedLayoutNodes for member in members)
    if len(operations) > max_operations or len(scope) > max_touched:
        return None
    refs = tuple(
        dict.fromkeys(
            reference
            for member in members
            for reference in member.diagnostic_refs
        )
    )
    payload = first.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload["scope"] = list(scope)
    payload["diagnosticRefs"] = list(refs)
    payload["operations"] = [
        operation.model_dump(mode="json", by_alias=True, exclude_none=True)
        for operation in operations
    ]
    payload["strategy"] = "deterministic_cascade"
    payload["limits"] = {
        "maxOperations": max_operations,
        "maxAffectedBlocks": max_touched,
        "maxLayoutDepth": min(member.budget.maxLayoutDepth for member in members),
    }
    payload.pop("planId", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["planId"] = "rp_composite_" + hashlib.sha256(
        encoded.encode("utf-8")
    ).hexdigest()[:20]
    try:
        return RepairPlan.model_validate(payload)
    except ValueError:
        return None


def _composite_candidates(
    plans: Sequence[RepairPlan],
    footprint_for: PlanFootprint,
) -> tuple[tuple[RepairPlan, ...], dict[str, tuple[RepairPlan, ...]]]:
    """Greedily color the conflict graph into deterministic independent sets."""

    groups: list[list[RepairPlan]] = []
    footprints: dict[str, RepairConflictFootprint] = {
        plan.plan_id: footprint_for(plan) for plan in plans
    }
    for plan in deterministic_candidate_order(plans):
        for group in groups:
            if all(
                not _footprints_conflict(
                    footprints[plan.plan_id], footprints[member.plan_id]
                )
                for member in group
            ):
                group.append(plan)
                break
        else:
            groups.append([plan])
    composites: list[RepairPlan] = []
    members_by_id: dict[str, tuple[RepairPlan, ...]] = {}
    for group in groups[:8]:
        composite = _merge_nonconflicting_plans(group)
        if composite is None:
            continue
        composites.append(composite)
        members_by_id[composite.plan_id] = tuple(group)
    return tuple(composites), members_by_id


@dataclass(slots=True)
class SlideSpecRepairDependencies:
    deterministic_cascade: PlanFactory
    validate_plan: ValidatePlan
    render_plan: RenderPlan
    inspect_dom: InspectDom
    run_ocr: RunOcr
    run_vqa: RunVqa
    publish: PublishCandidate
    propose: PlanFactory | None = None
    proposal_preflight: ProposalPreflight | None = None
    proposal_capability_digest: str = "projection-capability:unknown"
    proposal_model_digest: str = "model:unknown"
    proposal_cache: NegativeProposalCache | None = None
    candidate_partition: CandidatePartition | None = None
    applied_plan_observer: AppliedPlanObserver | None = None
    plan_footprint: PlanFootprint | None = None
    iteration_guard: RepairIterationGuard = field(default_factory=RepairIterationGuard)
    lazy_quality_margin: float = 0.05


class SlideSpecRepairPipeline:
    """Apply deterministic candidates first, then at most one bounded LLM call."""

    def __init__(self, dependencies: SlideSpecRepairDependencies) -> None:
        self._dependencies = dependencies

    async def _try_stage(
        self,
        spec: SlideSpecInput,
        plans: Sequence[RepairPlan],
        rollout: SlideSpecRepairRolloutDecision,
        state: "_MutableFunnel",
        outcomes: list[CandidateOutcome],
        baseline_dom: DomEvidence,
    ) -> Literal["accepted", "exhausted", "inconclusive"]:
        if not plans:
            return "exhausted"
        started = time.perf_counter()
        footprint_for = self._dependencies.plan_footprint or _default_plan_footprint
        composites, members_by_id = _composite_candidates(plans, footprint_for)
        state.add_timing("conflict_graph", started)
        if composites:
            state.composite_plan_count += len(composites)
            state.composite_operations_merged += sum(
                len(composite.operations) for composite in composites
            )
            outcome = await self._try_plans(
                spec, composites, rollout, state, outcomes, baseline_dom
            )
            if outcome != "exhausted":
                return outcome
            # A failed composite is evidence about that combined mutation only.
            # Decompose it back to its original candidates and retain every gate.
            state.individual_fallback_count += 1
            decomposed_by_id = {
                member.plan_id: member
                for composite in composites
                for member in members_by_id[composite.plan_id]
            }
            decomposed = tuple(decomposed_by_id.values())
            unmerged = tuple(
                plan for plan in plans if plan.plan_id not in decomposed_by_id
            )
            plans = (*decomposed, *unmerged)
        return await self._try_plans(
            spec, plans, rollout, state, outcomes, baseline_dom
        )

    async def run(
        self,
        *,
        spec: SlideSpecInput,
        baseline_dom: DomEvidence,
        rollout: SlideSpecRepairRolloutDecision,
    ) -> RepairPipelineResult:
        if not rollout.should_run_repair:
            return self._preserved(
                rollout,
                rollout.reason_code or "legacy_preserved.disabled",
                baseline_dom,
            )
        # This is intentionally independent of VQA: a DOM clipping report starts repair.
        if not baseline_dom.required_geometry_defect:
            reason = (
                "dom.telemetry_incomplete"
                if baseline_dom.incomplete_measurement_count
                else "legacy_preserved.no_geometry_trigger"
            )
            return self._preserved(rollout, reason, baseline_dom)

        state = _MutableFunnel()
        outcomes: list[CandidateOutcome] = []
        started = time.perf_counter()
        deterministic = tuple(await self._dependencies.deterministic_cascade(spec, baseline_dom))
        state.add_timing("deterministic_cascade", started)
        had_deterministic_candidates = bool(deterministic)
        preferred: Sequence[RepairPlan] = ()
        if self._dependencies.candidate_partition is not None:
            preferred, deterministic = self._dependencies.candidate_partition(
                deterministic
            )
        if preferred:
            outcome = await self._try_stage(
                spec, preferred, rollout, state, outcomes, baseline_dom
            )
            if outcome == "accepted":
                return self._result(rollout, outcomes, state, (), baseline_dom)
            if outcome == "inconclusive":
                state.legacy_preserved += 1
                return self._result(
                    rollout,
                    outcomes,
                    state,
                    ("acceptance.inconclusive", "legacy_preserved"),
                    baseline_dom,
                )
            state.individual_fallback_count += 1
        outcome = await self._try_stage(
            spec, deterministic, rollout, state, outcomes, baseline_dom
        )
        if outcome == "accepted":
            return self._result(rollout, outcomes, state, (), baseline_dom)
        if outcome == "inconclusive":
            state.legacy_preserved += 1
            return self._result(
                rollout,
                outcomes,
                state,
                ("acceptance.inconclusive", "legacy_preserved"),
                baseline_dom,
            )
        if not had_deterministic_candidates:
            state.record_no_candidate("deterministic_no_candidate")

        if self._dependencies.propose is not None:
            if self._dependencies.proposal_preflight is not None:
                started = time.perf_counter()
                preflight = await self._dependencies.proposal_preflight(
                    spec, baseline_dom
                )
                state.add_timing("proposal_preflight", started)
                if not preflight.passed:
                    if not preflight.conclusive:
                        state.inconclusive += 1
                        state.legacy_preserved += 1
                        return self._result(
                            rollout,
                            outcomes,
                            state,
                            (
                                preflight.reason_code or "proposal.preflight_inconclusive",
                                "legacy_preserved",
                            ),
                            baseline_dom,
                        )
                    state.proposal_preflight_skips += 1
                    state.record_no_candidate("proposal_preflight_unprojectable")
                    state.legacy_preserved += 1
                    return self._result(
                        rollout,
                        outcomes,
                        state,
                        (
                            preflight.reason_code
                            or "proposal.projection_unavailable",
                            "legacy_preserved",
                        ),
                        baseline_dom,
                    )
                state.llm_preflight_pass_count += 1

            proposal_cache = (
                self._dependencies.proposal_cache
                if self._dependencies.proposal_cache is not None
                else DEFAULT_NEGATIVE_PROPOSAL_CACHE
            )
            proposal_key = ProposalCacheKey(
                spec_digest=spec.spec_hash,
                dom_digest=dom_evidence_digest(baseline_dom),
                capability_digest=self._dependencies.proposal_capability_digest,
                model_digest=self._dependencies.proposal_model_digest,
            )
            if proposal_cache.contains(proposal_key):
                state.proposal_cache_hits += 1
                state.llm_negative_cache_hits += 1
                state.record_no_candidate("negative_cache_hit")
                state.legacy_preserved += 1
                return self._result(
                    rollout,
                    outcomes,
                    state,
                    ("proposal.negative_cache_hit", "legacy_preserved"),
                    baseline_dom,
                )
            state.proposal_cache_misses += 1
            try:
                started = time.perf_counter()
                proposal_result = await self._dependencies.propose(spec, baseline_dom)
                state.add_timing("llm_proposal", started)
                if isinstance(proposal_result, ProposalBatch):
                    proposed = proposal_result.plans
                    state.llm_calls += proposal_result.call_count
                    state.llm_pattern_call_count += proposal_result.call_count
                    state.llm_response_count += proposal_result.response_count
                    state.llm_schema_pass_count += proposal_result.schema_pass_count
                    state.llm_provider_failures += proposal_result.provider_failure_count
                    state.llm_schema_rejections += proposal_result.schema_rejection_count
                    state.llm_empty_responses += proposal_result.empty_count
                    state.llm_negative_cache_hits += (
                        proposal_result.negative_cache_hit_count
                    )
                    state.llm_circuit_open_count += proposal_result.circuit_open_count
                    if proposal_result.no_candidate_reason is not None:
                        state.record_no_candidate(
                            proposal_result.no_candidate_reason
                        )
                else:
                    proposed = tuple(proposal_result)
                    state.llm_calls += 1
                    state.llm_pattern_call_count += 1
                    state.llm_response_count += 1
                    if proposed:
                        state.llm_schema_pass_count += 1
                    else:
                        state.record_no_candidate("llm_empty")
                        state.llm_empty_responses += 1
            except Exception as error:
                state.llm_calls += 1
                state.llm_pattern_call_count += 1
                state.llm_provider_failures += 1
                state.record_no_candidate("llm_provider_error")
                state.inconclusive += 1
                state.legacy_preserved += 1
                reason = (
                    "proposal.model_timeout"
                    if isinstance(error, (TimeoutError, asyncio.TimeoutError))
                    else str(error)
                    if str(error).startswith("proposal.")
                    else "proposal.provider_unavailable"
                )
                return self._result(
                    rollout,
                    outcomes,
                    state,
                    (reason, "legacy_preserved"),
                    baseline_dom,
                )
            # A proposer cannot smuggle a deterministic path into this phase.
            proposed = tuple(
                plan for plan in proposed if plan.provenance.source == "llm"
            )
            proposed = deterministic_candidate_order(proposed)[:2]
            state.llm_candidates += len(proposed)
            if not proposed and baseline_dom.incomplete_measurement_count == 0:
                proposal_cache.put(proposal_key)
            outcome = await self._try_plans(
                spec, proposed, rollout, state, outcomes, baseline_dom
            )
            if outcome == "accepted":
                return self._result(rollout, outcomes, state, (), baseline_dom)
            if outcome == "inconclusive":
                state.legacy_preserved += 1
                return self._result(
                    rollout, outcomes, state,
                    ("acceptance.inconclusive", "legacy_preserved"),
                    baseline_dom,
                )

        state.legacy_preserved += 1
        terminal_reasons: tuple[str, ...] = (
            *state.no_candidate_reasons,
            "acceptance.required_gate_failed",
            "legacy_preserved",
        )
        return self._result(
            rollout,
            outcomes,
            state,
            terminal_reasons,
            baseline_dom,
        )

    async def _try_plans(
        self,
        spec: SlideSpecInput,
        plans: Sequence[RepairPlan],
        rollout: SlideSpecRepairRolloutDecision,
        state: "_MutableFunnel",
        outcomes: list[CandidateOutcome],
        baseline_dom: DomEvidence,
    ) -> Literal["accepted", "exhausted", "inconclusive"]:
        accepted_candidates: list[_AcceptedCandidate] = []
        accepted_insert_at: int | None = None
        dependency_inconclusive = False
        validated: list[RepairPlan] = []
        for candidate in deterministic_candidate_order(plans):
            plan = candidate
            state.proposed += 1
            state.repair_costs.append(repair_cost(plan).sort_key)
            guard_reason = self._dependencies.iteration_guard.rejection_reason(plan)
            if guard_reason is not None:
                state.rejected += 1
                state.render_candidates_rejected_early += 1
                if guard_reason == "repair.plan_signature_repeated":
                    state.repeated_plan_blocked += 1
                elif guard_reason == "repair.oscillation_detected":
                    state.oscillation_blocked += 1
                else:
                    state.iteration_limit_blocked += 1
                outcomes.append(
                    self._outcome(plan, "not_started", "rejected", guard_reason)
                )
                continue
            started = time.perf_counter()
            validation = await self._dependencies.validate_plan(spec, plan)
            state.add_timing("schema_invariant", started)
            if not validation.passed:
                if not validation.conclusive:
                    state.inconclusive += 1
                    outcomes.append(self._outcome(plan, "not_started", "inconclusive", validation.reason_code or "acceptance.inconclusive"))
                    return "inconclusive"
                state.rejected += 1
                state.render_candidates_rejected_early += 1
                outcomes.append(self._outcome(plan, "not_started", "rejected", validation.reason_code or "schema.invalid_value"))
                continue
            validated.append(plan)

        renderable, pruned = pareto_prune_plans(validated)
        state.pareto_pruned += len(pruned)
        state.rejected += len(pruned)
        state.render_candidates_rejected_early += len(pruned)

        for plan in renderable:
            try:
                state.render_calls += 1
                state.full_render_count += 1
                if plan.provenance.source == "llm":
                    state.llm_render_count += 1
                started = time.perf_counter()
                artifact = await self._dependencies.render_plan(spec, plan)
                state.add_timing("render", started)
            except Exception as error:
                state.rejected += 1
                state.render_candidates_rejected_early += 1
                outcomes.append(
                    self._outcome(
                        plan,
                        "failed",
                        "rejected",
                        getattr(error, "code", "render.failed"),
                    )
                )
                continue
            state.rendered += 1
            state.dom_calls += 1
            started = time.perf_counter()
            dom = await self._dependencies.inspect_dom(artifact)
            state.add_timing("dom", started)
            # DOM is cheap and authoritative for geometry; don't spend OCR/VQA on it.
            failing_defect = next(
                (
                    defect
                    for defect in dom.defects
                    if defect.severity in {"high", "medium"}
                    and defect.code != "constraint.evidence.missing"
                ),
                None,
            )
            if failing_defect is not None:
                state.rejected += 1
                state.render_candidates_rejected_early += 1
                outcomes.append(
                    self._outcome(
                        plan,
                        "succeeded",
                        "rejected",
                        failing_defect.code,
                        diagnostics={
                            "dom_failure": {
                                "code": failing_defect.code,
                                "severity": failing_defect.severity,
                                "semantic_id": failing_defect.semantic_id,
                                "qid": failing_defect.qid,
                                **dict(failing_defect.diagnostics),
                            }
                        },
                    )
                )
                continue

            dependency_unknown = merge_evidence_atoms(
                baseline_dom.blocking_unknown_atoms(plan),
                dom.blocking_unknown_atoms(plan),
            )
            if dependency_unknown:
                dependency_inconclusive = True
                state.inconclusive += 1
                legacy_unknown = all(
                    atom.sensor == "legacy_dom" and atom.global_scope
                    for atom in dependency_unknown
                )
                if legacy_unknown:
                    missing_evidence = [
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
                        for defect in dom.defects
                        if defect.code == "constraint.evidence.missing"
                        or defect.diagnostics
                    ]
                    diagnostics: Mapping[str, object] = {
                        "dom_telemetry": {
                            "complete": False,
                            "missing_evidence": missing_evidence,
                        }
                    }
                    reason = "dom.telemetry_incomplete"
                else:
                    diagnostics = {
                        "dom_telemetry": {
                            "complete": False,
                            "dependency_blockers": [
                                {
                                    "sensor": atom.sensor,
                                    "metric": atom.metric,
                                    "stage": atom.stage,
                                    "qid": atom.qid,
                                    "semantic_id": atom.semantic_id,
                                    "constraint_id": atom.constraint_id,
                                    "reason_code": atom.reason_code,
                                    "dependency_refs": list(
                                        atom.dependency_refs
                                    ),
                                    "diagnostics": dict(atom.diagnostics),
                                }
                                for atom in dependency_unknown
                            ],
                        }
                    }
                    reason = "dom.dependency_telemetry_incomplete"
                outcomes.append(
                    self._outcome(
                        plan,
                        "succeeded",
                        "inconclusive",
                        reason,
                        diagnostics=diagnostics,
                    )
                )
                continue

            inventory = canonical_ocr_inventory(spec)
            state.ocr_calls += 1
            state.vqa_calls += 1
            started = time.perf_counter()
            ocr, vqa = await asyncio.gather(
                self._dependencies.run_ocr(artifact, inventory),
                self._dependencies.run_vqa(artifact),
            )
            state.add_timing("ocr_vqa", started)
            state.cache_hits += int(ocr.cache_hit) + int(vqa.cache_hit)
            state.cache_misses += int(not ocr.cache_hit) + int(not vqa.cache_hit)
            if not ocr.passed or not vqa.passed:
                # A conclusive sensor failure already proves this candidate is bad.
                # An unavailable peer sensor must not turn that candidate-specific
                # rejection into a pipeline-wide inconclusive result, because doing
                # so prevents the next deterministic repair from being evaluated.
                conclusive_failure = next(
                    (
                        sensor
                        for sensor in (ocr, vqa)
                        if not sensor.passed and sensor.conclusive
                    ),
                    None,
                )
                if conclusive_failure is None:
                    state.inconclusive += 1
                    outcomes.append(
                        self._outcome(
                            plan,
                            "succeeded",
                            "inconclusive",
                            ocr.reason_code
                            or vqa.reason_code
                            or "acceptance.inconclusive",
                            diagnostics={
                                "ocr": dict(ocr.diagnostics),
                                "vqa": dict(vqa.diagnostics),
                            },
                        )
                    )
                    return "inconclusive"
                state.rejected += 1
                outcomes.append(
                    self._outcome(
                        plan,
                        "succeeded",
                        "rejected",
                        conclusive_failure.reason_code
                        or "acceptance.required_gate_failed",
                        diagnostics={
                            "ocr": dict(ocr.diagnostics),
                            "vqa": dict(vqa.diagnostics),
                        },
                    )
                )
                continue
            # A boolean VQA pass proves non-regression, but not the relative rank
            # among multiple safe candidates. Publication selection therefore
            # fails closed when paired comparison produced no quality-gain score.
            # Treating a missing score as a high score can make an old/partial VQA
            # payload beat a genuinely measured candidate and worsen the slide.
            if vqa.quality_gain is None:
                state.inconclusive += 1
                outcomes.append(
                    self._outcome(
                        plan,
                        "succeeded",
                        "inconclusive",
                        "vqa.improvement_unavailable",
                        diagnostics={"vqa": dict(vqa.diagnostics)},
                    )
                )
                return "inconclusive"
            resolved_issue_count = max(0, vqa.resolved_issue_count or 0)
            if vqa.quality_gain < 0 or (
                resolved_issue_count == 0 and vqa.quality_gain <= 0
            ):
                state.rejected += 1
                outcomes.append(
                    self._outcome(
                        plan,
                        "succeeded",
                        "rejected",
                        "vqa.non_monotonic",
                        quality_gain=vqa.quality_gain,
                        quality_gain_interval=vqa.quality_gain_interval,
                        diagnostics={"vqa": dict(vqa.diagnostics)},
                    )
                )
                continue
            state.validated += 1
            state.accepted += 1
            if accepted_insert_at is None:
                accepted_insert_at = len(outcomes)
            quality_gain = vqa.quality_gain
            quality_gain_interval = vqa.quality_gain_interval or (
                quality_gain,
                quality_gain,
            )
            realized_cost = artifact.realized_cost or repair_cost(plan).sort_key
            state.quality_gains.append(quality_gain)
            state.quality_gain_intervals.append(quality_gain_interval)
            state.realized_repair_costs.append(realized_cost)
            accepted_candidates.append(
                _AcceptedCandidate(
                    plan=plan,
                    artifact=artifact,
                    quality_gain=quality_gain,
                    quality_gain_interval=quality_gain_interval,
                    constraint_cost=artifact.constraint_cost,
                    realized_cost=realized_cost,
                    resolved_issue_count=resolved_issue_count,
                    resolved_issue_severity=max(
                        0, vqa.resolved_issue_severity or 0
                    ),
                    remaining_issue_count=vqa.remaining_issue_count,
                    quality_margin=vqa.quality_margin,
                    diagnostics={
                        "ocr": dict(ocr.diagnostics),
                        "vqa": dict(vqa.diagnostics),
                    },
                )
            )
            if (
                vqa.remaining_issue_count == 0
                and vqa.quality_margin is not None
                and vqa.quality_margin >= self._dependencies.lazy_quality_margin
            ):
                # Explicit full-resolution proof with sufficient safety margin
                # makes later expensive candidates unnecessary.  Missing proof,
                # partial resolution or a narrow margin deliberately expands.
                break

        outcomes.extend(
            self._outcome(
                plan,
                "not_started",
                "rejected",
                "candidate.pareto_dominated",
            )
            for plan in pruned
        )

        if not accepted_candidates and dependency_inconclusive:
            return "inconclusive"
        if not accepted_candidates:
            return "exhausted"

        ranked = _rank_accepted_candidates(accepted_candidates)
        selected = ranked[0]
        state.selected_quality_gain = selected.quality_gain
        assert accepted_insert_at is not None

        def not_selected(candidate: _AcceptedCandidate) -> CandidateOutcome:
            return self._outcome(
                candidate.plan,
                "succeeded",
                "accepted",
                "candidate.not_selected",
                quality_gain=candidate.quality_gain,
                quality_gain_interval=candidate.quality_gain_interval,
                realized_cost=candidate.realized_cost,
                diagnostics=candidate.diagnostics,
            )

        def record_publication_outcomes(
            attempted: Sequence[CandidateOutcome],
            remaining: Sequence[_AcceptedCandidate] = (),
        ) -> None:
            outcomes[accepted_insert_at:accepted_insert_at] = [
                *attempted,
                *(not_selected(candidate) for candidate in remaining),
            ]

        if rollout.shadow_mode or not rollout.publisher_allowed:
            state.legacy_preserved += 1
            record_publication_outcomes(
                [
                    self._outcome(
                        selected.plan,
                        "succeeded",
                        "accepted",
                        "legacy_preserved.shadow",
                        quality_gain=selected.quality_gain,
                        quality_gain_interval=selected.quality_gain_interval,
                        realized_cost=selected.realized_cost,
                        selected=True,
                        diagnostics=selected.diagnostics,
                    )
                ],
                ranked[1:],
            )
            return "accepted"

        attempted_outcomes: list[CandidateOutcome] = []
        for offset, candidate in enumerate(ranked):
            plan = candidate.plan
            artifact = candidate.artifact
            try:
                started = time.perf_counter()
                receipt = await self._dependencies.publish(spec, plan, artifact)
                state.add_timing("publication", started)
            except Exception as error:
                state.legacy_preserved += 1
                attempted_outcomes.append(
                    self._outcome(
                        plan,
                        "succeeded",
                        "accepted",
                        getattr(error, "code", "artifact.publish.unexpected_error"),
                        apply_status="failed",
                        publish_status="failed",
                        quality_gain=candidate.quality_gain,
                        quality_gain_interval=candidate.quality_gain_interval,
                        realized_cost=candidate.realized_cost,
                        selected=True,
                        diagnostics=candidate.diagnostics,
                    )
                )
                record_publication_outcomes(attempted_outcomes, ranked[offset + 1 :])
                return "accepted"
            applied = (
                receipt.cas_committed
                and receipt.active_switched
                and receipt.readback_hashes_match
            )
            if applied:
                state.apply_committed += 1
                state.publish_active += 1
                state.postverify_passed += 1
                state.applied += 1
                state.selected_quality_gain = candidate.quality_gain
                self._dependencies.iteration_guard.record_applied(plan)
                attempted_outcomes.append(
                    self._outcome(
                        plan,
                        "succeeded",
                        "accepted",
                        None,
                        applied=True,
                        quality_gain=candidate.quality_gain,
                        quality_gain_interval=candidate.quality_gain_interval,
                        realized_cost=candidate.realized_cost,
                        selected=True,
                        diagnostics=candidate.diagnostics,
                    )
                )
                record_publication_outcomes(attempted_outcomes, ranked[offset + 1 :])
                observer = self._dependencies.applied_plan_observer
                if observer is not None:
                    try:
                        observer(plan)
                    except Exception:
                        # Recipe learning is an optimization.  A successfully
                        # published repair must not be converted into a failure
                        # because the request-local positive cache rejected it.
                        pass
                return "accepted"

            reason = receipt.reason_code or "artifact.publish.readback_mismatch"
            retryable_candidate_failure = (
                not receipt.cas_committed
                and not receipt.active_switched
                and not receipt.rolled_back
                and reason.startswith(
                    (
                        "acceptance.",
                        "candidate.",
                        "constraint.",
                        "dom.",
                        "invariant.",
                        "ocr.",
                        "schema.",
                        "vqa.",
                    )
                )
            )
            if receipt.rolled_back:
                state.rolled_back += 1
            attempted_outcomes.append(
                self._outcome(
                    plan,
                    "succeeded",
                    "rejected" if retryable_candidate_failure else "accepted",
                    reason,
                    apply_status="committed" if receipt.cas_committed else "failed",
                    publish_status="active" if receipt.active_switched else "failed",
                    post_verify_status=(
                        "failed" if receipt.cas_committed and receipt.active_switched
                        else "not_started"
                    ),
                    rolled_back=receipt.rolled_back,
                    quality_gain=candidate.quality_gain,
                    quality_gain_interval=candidate.quality_gain_interval,
                    realized_cost=candidate.realized_cost,
                    selected=True,
                    diagnostics=candidate.diagnostics,
                )
            )
            if retryable_candidate_failure:
                continue
            state.legacy_preserved += 1
            record_publication_outcomes(attempted_outcomes, ranked[offset + 1 :])
            return "accepted"

        # Every safe candidate was rejected by a candidate-specific final gate.
        # Preserve their evidence, then let the caller continue to the remaining
        # deterministic plans or the bounded LLM layout-plan fallback.
        record_publication_outcomes(attempted_outcomes)
        return "exhausted"

    @staticmethod
    def _outcome(
        plan: RepairPlan,
        render_status: str,
        validation_status: str,
        reason: ReasonCode | None,
        *,
        applied: bool = False,
        apply_status: str | None = None,
        publish_status: str | None = None,
        post_verify_status: str | None = None,
        rolled_back: bool = False,
        quality_gain: float | None = None,
        quality_gain_interval: tuple[float, float] | None = None,
        realized_cost: tuple[int, int, float, float, int, str] | None = None,
        selected: bool = False,
        diagnostics: Mapping[str, object] | None = None,
    ) -> CandidateOutcome:
        committed = apply_status or ("committed" if applied else "not_started")
        published = publish_status or ("active" if applied else "not_started")
        post_verified = post_verify_status or ("passed" if applied else "not_started")
        return CandidateOutcome(
            plan_id=plan.plan_id, source=plan.provenance.source, proposal_status="generated",
            render_status=render_status, validation_status=validation_status,
            apply_status=committed, publish_status=published,
            post_verify_status=post_verified, applied=applied, rolled_back=rolled_back,
            reason_codes=(reason,) if reason else (),
            quality_gain=quality_gain,
            quality_gain_interval=quality_gain_interval,
            realized_cost=realized_cost,
            selected=selected,
            diagnostics=dict(diagnostics or {}),
        )

    @staticmethod
    def _preserved(
        rollout: SlideSpecRepairRolloutDecision,
        reason: ReasonCode,
        evidence: DomEvidence,
    ) -> RepairPipelineResult:
        return RepairPipelineResult(
            route=rollout.route,
            trigger="geometry" if evidence.required_geometry_defect else None,
            outcomes=(),
            funnel=RepairFunnel(legacy_preserved=1), reason_codes=(reason,),
            kill_switch_enabled=rollout.kill_switch_active,
            confirmed_issue_count=evidence.confirmed_issue_count,
            incomplete_measurement_count=evidence.incomplete_measurement_count,
            unresolved_confirmed_issue_count=evidence.confirmed_issue_count,
            clean_slide_count=evidence.clean_slide_count,
        )

    @staticmethod
    def _result(
        rollout: SlideSpecRepairRolloutDecision,
        outcomes: list[CandidateOutcome],
        state: "_MutableFunnel",
        reasons: tuple[ReasonCode, ...],
        evidence: DomEvidence,
    ) -> RepairPipelineResult:
        funnel = state.freeze()
        return RepairPipelineResult(
            route=rollout.route, trigger="geometry", outcomes=tuple(outcomes),
            funnel=funnel, reason_codes=reasons, telemetry=state.telemetry(),
            kill_switch_enabled=rollout.kill_switch_active,
            confirmed_issue_count=evidence.confirmed_issue_count,
            incomplete_measurement_count=evidence.incomplete_measurement_count,
            unresolved_confirmed_issue_count=(
                0 if funnel.applied else evidence.confirmed_issue_count
            ),
            clean_slide_count=evidence.clean_slide_count,
        )


@dataclass(slots=True)
class _MutableFunnel:
    proposed: int = 0
    rendered: int = 0
    validated: int = 0
    accepted: int = 0
    rejected: int = 0
    inconclusive: int = 0
    apply_committed: int = 0
    publish_active: int = 0
    postverify_passed: int = 0
    applied: int = 0
    rolled_back: int = 0
    legacy_preserved: int = 0
    llm_calls: int = 0
    llm_candidates: int = 0
    render_calls: int = 0
    dom_calls: int = 0
    ocr_calls: int = 0
    vqa_calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    proposal_cache_hits: int = 0
    proposal_cache_misses: int = 0
    proposal_preflight_skips: int = 0
    pareto_pruned: int = 0
    composite_plan_count: int = 0
    composite_operations_merged: int = 0
    render_candidates_rejected_early: int = 0
    full_render_count: int = 0
    llm_pattern_call_count: int = 0
    llm_response_count: int = 0
    llm_schema_pass_count: int = 0
    llm_preflight_pass_count: int = 0
    llm_render_count: int = 0
    llm_provider_failures: int = 0
    llm_schema_rejections: int = 0
    llm_empty_responses: int = 0
    llm_circuit_open_count: int = 0
    llm_negative_cache_hits: int = 0
    no_candidate_reasons: list[NoCandidateReason] = field(default_factory=list)
    individual_fallback_count: int = 0
    repeated_plan_blocked: int = 0
    oscillation_blocked: int = 0
    iteration_limit_blocked: int = 0
    timings: dict[str, float] = field(default_factory=dict)
    repair_costs: list[tuple[int, int, float, float, int, str]] = field(
        default_factory=list
    )
    realized_repair_costs: list[tuple[int, int, float, float, int, str]] = field(
        default_factory=list
    )
    quality_gains: list[float] = field(default_factory=list)
    quality_gain_intervals: list[tuple[float, float]] = field(default_factory=list)
    selected_quality_gain: float | None = None

    def record_no_candidate(self, reason: NoCandidateReason) -> None:
        if reason not in self.no_candidate_reasons:
            self.no_candidate_reasons.append(reason)

    def add_timing(self, stage: str, started: float) -> None:
        elapsed = (time.perf_counter() - started) * 1000
        self.timings[stage] = self.timings.get(stage, 0.0) + elapsed

    def telemetry(self) -> PipelineTelemetry:
        return PipelineTelemetry(
            stage_timings_ms={
                key: round(value, 3) for key, value in sorted(self.timings.items())
            },
            llm_calls=self.llm_calls,
            llm_candidates=self.llm_candidates,
            render_calls=self.render_calls,
            dom_calls=self.dom_calls,
            ocr_calls=self.ocr_calls,
            vqa_calls=self.vqa_calls,
            cache_hits=self.cache_hits,
            cache_misses=self.cache_misses,
            proposal_cache_hits=self.proposal_cache_hits,
            proposal_cache_misses=self.proposal_cache_misses,
            proposal_preflight_skips=self.proposal_preflight_skips,
            pareto_pruned=self.pareto_pruned,
            composite_plan_count=self.composite_plan_count,
            composite_operations_merged=self.composite_operations_merged,
            render_candidates_rejected_early=self.render_candidates_rejected_early,
            full_render_count=self.full_render_count,
            llm_pattern_call_count=self.llm_pattern_call_count,
            llm_response_count=self.llm_response_count,
            llm_schema_pass_count=self.llm_schema_pass_count,
            llm_preflight_pass_count=self.llm_preflight_pass_count,
            llm_render_count=self.llm_render_count,
            llm_provider_failures=self.llm_provider_failures,
            llm_schema_rejections=self.llm_schema_rejections,
            llm_empty_responses=self.llm_empty_responses,
            llm_circuit_open_count=self.llm_circuit_open_count,
            llm_negative_cache_hits=self.llm_negative_cache_hits,
            no_candidate_reasons=tuple(self.no_candidate_reasons),
            individual_fallback_count=self.individual_fallback_count,
            repeated_plan_blocked=self.repeated_plan_blocked,
            oscillation_blocked=self.oscillation_blocked,
            iteration_limit_blocked=self.iteration_limit_blocked,
            repair_costs=tuple(self.repair_costs),
            realized_repair_costs=tuple(self.realized_repair_costs),
            quality_gains=tuple(self.quality_gains),
            quality_gain_intervals=tuple(self.quality_gain_intervals),
            selected_quality_gain=self.selected_quality_gain,
        )

    def freeze(self) -> RepairFunnel:
        return RepairFunnel(
            candidate_proposed=self.proposed, candidate_rendered=self.rendered,
            candidate_validated=self.validated, candidate_accepted=self.accepted,
            candidate_rejected=self.rejected, candidate_inconclusive=self.inconclusive,
            apply_committed=self.apply_committed, publish_active=self.publish_active,
            postverify_passed=self.postverify_passed, applied=self.applied,
            rolled_back=self.rolled_back,
            legacy_preserved=self.legacy_preserved,
        )
