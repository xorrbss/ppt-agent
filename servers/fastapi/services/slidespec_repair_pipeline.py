"""Pure, fail-closed SlideSpec repair orchestration.

The module deliberately has no database or HTML mutation dependency.  Callers
adapt existing render/sensor services at its callback boundaries and retain the
only publication side effect in the publisher callback.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
import time
from typing import Literal

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


ReasonCode = str


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


@dataclass(frozen=True, slots=True)
class DomEvidence:
    defects: tuple[DomDefect, ...] = ()
    telemetry_complete: bool = True

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
            and defect.severity in {"high", "medium"}
            for defect in self.defects
        )

    @property
    def hard_failure(self) -> bool:
        return not self.telemetry_complete or self.required_geometry_defect


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
    repair_costs: tuple[tuple[int, int, float, float, int, str], ...] = ()
    realized_repair_costs: tuple[tuple[int, int, float, float, int, str], ...] = ()
    quality_gains: tuple[float, ...] = ()
    quality_gain_intervals: tuple[tuple[float, float], ...] = ()
    selected_quality_gain: float | None = None


@dataclass(frozen=True, slots=True)
class _AcceptedCandidate:
    plan: RepairPlan
    artifact: RenderedCandidate
    quality_gain: float
    quality_gain_interval: tuple[float, float]
    constraint_cost: float
    realized_cost: tuple[int, int, float, float, int, str]
    diagnostics: Mapping[str, object] = field(default_factory=dict)


def _select_accepted_candidate(
    candidates: Sequence[_AcceptedCandidate],
) -> _AcceptedCandidate:
    """Select from the uncertainty-aware Pareto frontier with stable tie breaks."""

    def dominates(left: _AcceptedCandidate, right: _AcceptedCandidate) -> bool:
        quality_certainly_better = (
            left.quality_gain_interval[0] > right.quality_gain_interval[1] + 1e-9
        )
        quality_equivalent = left.quality_gain_interval == right.quality_gain_interval
        costs_no_worse = (
            left.constraint_cost <= right.constraint_cost
            and left.realized_cost <= right.realized_cost
        )
        costs_strictly_better = (
            left.constraint_cost < right.constraint_cost
            or left.realized_cost < right.realized_cost
        )
        return costs_no_worse and (
            quality_certainly_better or (quality_equivalent and costs_strictly_better)
        )

    frontier = [
        candidate
        for candidate in candidates
        if not any(
            other is not candidate and dominates(other, candidate)
            for other in candidates
        )
    ]
    best_lower = max(item.quality_gain_interval[0] for item in frontier)
    equivalent = [
        item
        for item in frontier
        if item.quality_gain_interval[1] + 1e-9 >= best_lower
    ]
    return min(
        equivalent,
        key=lambda item: (
            item.constraint_cost,
            item.realized_cost,
            -item.quality_gain,
            item.plan.plan_id,
        ),
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
PlanFactory = Callable[[SlideSpecInput, DomEvidence], Awaitable[Sequence[RepairPlan]]]
ProposalPreflight = Callable[[SlideSpecInput, DomEvidence], Awaitable[SensorResult]]


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


class SlideSpecRepairPipeline:
    """Apply deterministic candidates first, then at most one bounded LLM call."""

    def __init__(self, dependencies: SlideSpecRepairDependencies) -> None:
        self._dependencies = dependencies

    async def run(
        self,
        *,
        spec: SlideSpecInput,
        baseline_dom: DomEvidence,
        rollout: SlideSpecRepairRolloutDecision,
    ) -> RepairPipelineResult:
        if not rollout.should_run_repair:
            return self._preserved(rollout, rollout.reason_code or "legacy_preserved.disabled")
        if not baseline_dom.telemetry_complete:
            return self._preserved(rollout, "dom.telemetry_incomplete")
        # This is intentionally independent of VQA: a DOM clipping report starts repair.
        if not baseline_dom.required_geometry_defect:
            return self._preserved(rollout, "legacy_preserved.no_geometry_trigger")

        state = _MutableFunnel()
        outcomes: list[CandidateOutcome] = []
        started = time.perf_counter()
        deterministic = tuple(await self._dependencies.deterministic_cascade(spec, baseline_dom))
        state.add_timing("deterministic_cascade", started)
        outcome = await self._try_plans(
            spec, deterministic, rollout, state, outcomes
        )
        if outcome == "accepted":
            return self._result(rollout, outcomes, state, ())
        if outcome == "inconclusive":
            state.legacy_preserved += 1
            return self._result(
                rollout, outcomes, state, ("acceptance.inconclusive", "legacy_preserved")
            )

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
                        )
                    state.proposal_preflight_skips += 1
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
                    )

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
                state.legacy_preserved += 1
                return self._result(
                    rollout,
                    outcomes,
                    state,
                    ("proposal.negative_cache_hit", "legacy_preserved"),
                )
            state.proposal_cache_misses += 1
            try:
                state.llm_calls += 1
                started = time.perf_counter()
                proposed = tuple(await self._dependencies.propose(spec, baseline_dom))
                state.add_timing("llm_proposal", started)
            except Exception as error:
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
                    rollout, outcomes, state, (reason, "legacy_preserved")
                )
            # A proposer cannot smuggle a deterministic path into this phase.
            proposed = tuple(
                plan for plan in proposed if plan.provenance.source == "llm"
            )
            proposed = deterministic_candidate_order(proposed)[:2]
            state.llm_candidates += len(proposed)
            if not proposed:
                proposal_cache.put(proposal_key)
            outcome = await self._try_plans(spec, proposed, rollout, state, outcomes)
            if outcome == "accepted":
                return self._result(rollout, outcomes, state, ())
            if outcome == "inconclusive":
                state.legacy_preserved += 1
                return self._result(
                    rollout, outcomes, state,
                    ("acceptance.inconclusive", "legacy_preserved"),
                )

        state.legacy_preserved += 1
        return self._result(rollout, outcomes, state, ("acceptance.required_gate_failed", "legacy_preserved"))

    async def _try_plans(
        self,
        spec: SlideSpecInput,
        plans: Sequence[RepairPlan],
        rollout: SlideSpecRepairRolloutDecision,
        state: "_MutableFunnel",
        outcomes: list[CandidateOutcome],
    ) -> Literal["accepted", "exhausted", "inconclusive"]:
        accepted_candidates: list[_AcceptedCandidate] = []
        accepted_insert_at: int | None = None
        validated: list[RepairPlan] = []
        for candidate in deterministic_candidate_order(plans):
            plan = candidate
            state.proposed += 1
            state.repair_costs.append(repair_cost(plan).sort_key)
            started = time.perf_counter()
            validation = await self._dependencies.validate_plan(spec, plan)
            state.add_timing("schema_invariant", started)
            if not validation.passed:
                if not validation.conclusive:
                    state.inconclusive += 1
                    outcomes.append(self._outcome(plan, "not_started", "inconclusive", validation.reason_code or "acceptance.inconclusive"))
                    return "inconclusive"
                state.rejected += 1
                outcomes.append(self._outcome(plan, "not_started", "rejected", validation.reason_code or "schema.invalid_value"))
                continue
            validated.append(plan)

        renderable, pruned = pareto_prune_plans(validated)
        state.pareto_pruned += len(pruned)
        state.rejected += len(pruned)

        for plan in renderable:
            try:
                state.render_calls += 1
                started = time.perf_counter()
                artifact = await self._dependencies.render_plan(spec, plan)
                state.add_timing("render", started)
            except Exception as error:
                state.rejected += 1
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
            if dom.hard_failure:
                # A directly measured defect conclusively rejects this candidate
                # even when an unrelated constraint observation is unavailable.
                # Preserve fail-closed behavior for a candidate whose *only* hard
                # signal is missing evidence, but do not let that uncertainty hide
                # a concrete failure or prevent evaluation of the next candidate.
                failing_defect = next(
                    (
                        defect
                        for defect in dom.defects
                        if defect.severity in {"high", "medium"}
                        and defect.code != "constraint.evidence.missing"
                    ),
                    None,
                )
                if failing_defect is None and not dom.telemetry_complete:
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
                    state.inconclusive += 1
                    outcomes.append(
                        self._outcome(
                            plan,
                            "succeeded",
                            "inconclusive",
                            "dom.telemetry_incomplete",
                            diagnostics={
                                "dom_telemetry": {
                                    "complete": False,
                                    "missing_evidence": missing_evidence,
                                }
                            },
                        )
                    )
                    return "inconclusive"
                if failing_defect is None:
                    # Defensive fallback: DomEvidence.hard_failure should imply a
                    # concrete defect or incomplete telemetry.
                    state.inconclusive += 1
                    outcomes.append(
                        self._outcome(
                            plan,
                            "succeeded",
                            "inconclusive",
                            "dom.telemetry_incomplete",
                        )
                    )
                    return "inconclusive"
                state.rejected += 1
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
                    plan,
                    artifact,
                    quality_gain,
                    quality_gain_interval,
                    artifact.constraint_cost,
                    realized_cost,
                    {
                        "ocr": dict(ocr.diagnostics),
                        "vqa": dict(vqa.diagnostics),
                    },
                )
            )

        outcomes.extend(
            self._outcome(
                plan,
                "not_started",
                "rejected",
                "candidate.pareto_dominated",
            )
            for plan in pruned
        )

        if not accepted_candidates:
            return "exhausted"

        selected = _select_accepted_candidate(accepted_candidates)
        state.selected_quality_gain = selected.quality_gain
        nonselected_outcomes: list[CandidateOutcome] = []
        for candidate in accepted_candidates:
            if candidate is selected:
                continue
            nonselected_outcomes.append(
                self._outcome(
                    candidate.plan,
                    "succeeded",
                    "accepted",
                    "candidate.not_selected",
                    quality_gain=candidate.quality_gain,
                    quality_gain_interval=candidate.quality_gain_interval,
                    realized_cost=candidate.realized_cost,
                    diagnostics=candidate.diagnostics,
                )
            )
        assert accepted_insert_at is not None

        def record_selected(outcome: CandidateOutcome) -> None:
            outcomes[accepted_insert_at:accepted_insert_at] = [
                outcome,
                *nonselected_outcomes,
            ]

        plan = selected.plan
        artifact = selected.artifact
        if rollout.shadow_mode or not rollout.publisher_allowed:
            state.legacy_preserved += 1
            record_selected(
                self._outcome(
                    plan,
                    "succeeded",
                    "accepted",
                    "legacy_preserved.shadow",
                    quality_gain=selected.quality_gain,
                    quality_gain_interval=selected.quality_gain_interval,
                    realized_cost=selected.realized_cost,
                    selected=True,
                    diagnostics=selected.diagnostics,
                )
            )
            return "accepted"
        try:
            started = time.perf_counter()
            receipt = await self._dependencies.publish(spec, plan, artifact)
            state.add_timing("publication", started)
        except Exception as error:
            state.legacy_preserved += 1
            record_selected(
                self._outcome(
                    plan,
                    "succeeded",
                    "accepted",
                    getattr(error, "code", "artifact.publish.unexpected_error"),
                    apply_status="failed",
                    publish_status="failed",
                    quality_gain=selected.quality_gain,
                    quality_gain_interval=selected.quality_gain_interval,
                    realized_cost=selected.realized_cost,
                    selected=True,
                    diagnostics=selected.diagnostics,
                )
            )
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
            record_selected(
                self._outcome(
                    plan,
                    "succeeded",
                    "accepted",
                    None,
                    applied=True,
                    quality_gain=selected.quality_gain,
                    quality_gain_interval=selected.quality_gain_interval,
                    realized_cost=selected.realized_cost,
                    selected=True,
                    diagnostics=selected.diagnostics,
                )
            )
            return "accepted"
        if receipt.rolled_back:
            state.rolled_back += 1
        state.legacy_preserved += 1
        record_selected(
            self._outcome(
                plan,
                "succeeded",
                "accepted",
                receipt.reason_code or "artifact.publish.readback_mismatch",
                apply_status="committed" if receipt.cas_committed else "failed",
                publish_status="active" if receipt.active_switched else "failed",
                post_verify_status=(
                    "failed" if receipt.cas_committed and receipt.active_switched
                    else "not_started"
                ),
                rolled_back=receipt.rolled_back,
                quality_gain=selected.quality_gain,
                quality_gain_interval=selected.quality_gain_interval,
                realized_cost=selected.realized_cost,
                selected=True,
                diagnostics=selected.diagnostics,
            )
        )
        return "accepted"

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
    def _preserved(rollout: SlideSpecRepairRolloutDecision, reason: ReasonCode) -> RepairPipelineResult:
        return RepairPipelineResult(
            route=rollout.route, trigger=None, outcomes=(),
            funnel=RepairFunnel(legacy_preserved=1), reason_codes=(reason,),
            kill_switch_enabled=rollout.kill_switch_active,
        )

    @staticmethod
    def _result(
        rollout: SlideSpecRepairRolloutDecision,
        outcomes: list[CandidateOutcome],
        state: "_MutableFunnel",
        reasons: tuple[ReasonCode, ...],
    ) -> RepairPipelineResult:
        return RepairPipelineResult(
            route=rollout.route, trigger="geometry", outcomes=tuple(outcomes),
            funnel=state.freeze(), reason_codes=reasons, telemetry=state.telemetry(),
            kill_switch_enabled=rollout.kill_switch_active,
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
