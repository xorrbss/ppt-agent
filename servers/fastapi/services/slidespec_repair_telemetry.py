"""Mutable counters projected into immutable SlideSpec pipeline telemetry."""

from __future__ import annotations

from dataclasses import dataclass, field
import time

from services.slidespec_repair_pipeline import PipelineTelemetry, RepairFunnel
from utils.llm_calls.slidespec_repair_resilience import NoCandidateReason

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
