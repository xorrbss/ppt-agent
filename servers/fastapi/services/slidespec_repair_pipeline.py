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


from services.slidespec_ocr_inventory import (
    CanonicalOcrInventory,
    SlideSpecInput,
    canonical_ocr_inventory,
)

from services.slidespec_dom_evidence import DomDefect, DomEvidence

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


from services.slidespec_repair_iteration import (
    RepairIterationGuard,
    _AcceptedCandidate,
    _operation_payloads,
    _operation_state_signature,
    _plan_signature,
    _rank_accepted_candidates,
    _select_accepted_candidate,
)

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


from services.slidespec_repair_composition import (
    _composite_candidates,
    _default_plan_footprint,
    _footprints_conflict,
    _merge_nonconflicting_plans,
)

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


from services.slidespec_repair_telemetry import _MutableFunnel
# ruff: noqa: E402, F401
