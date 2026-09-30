"""Value types shared by the SlideSpec repair pipeline and its split modules.

``slidespec_repair_pipeline`` orchestrates; ``slidespec_dom_evidence``,
``slidespec_repair_iteration``, ``slidespec_repair_composition`` and
``slidespec_repair_telemetry`` consume these types. Keeping them in this leaf
lets every one of those modules import at module top without a cycle back
through the pipeline (docs/refactoring-audit-20260929.md section 5, phase 8).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

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
class RenderedCandidate:
    html_hash: str
    render_hash: str
    realized_cost: tuple[int, int, float, float, int, str] | None = None
    constraint_cost: float = 0


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
