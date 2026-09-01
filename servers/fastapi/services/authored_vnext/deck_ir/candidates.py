"""Bounded deterministic local candidate enumeration and deck selection."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Mapping, Sequence
from .intent_resolution import resolve_art_intent

from .grammar import BALANCED_QUALITY_K, MAX_PARETO_BEAM_WIDTH, DesignGrammarManifest, DeckIrGrammarError, contract_mapping, digest, validate_art_direction_intent, validate_composition_intent


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    slide_key: str
    layout_id: str
    variant_id: str
    hard_constraint_status: str
    quality_vector: Mapping[str, float]
    diversity_signature: str
    candidate_digest: str
    rank: int
    composition: Mapping[str, Any]
    provider_art_intent: Mapping[str, Any]
    resolved_art_intent: Mapping[str, Any]
    resolution_receipt: Mapping[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {"candidateId": self.candidate_id, "slideKey": self.slide_key, "layoutId": self.layout_id, "variantId": self.variant_id, "hardConstraintStatus": self.hard_constraint_status, "qualityVector": dict(self.quality_vector), "diversitySignature": self.diversity_signature, "candidateDigest": self.candidate_digest, "rank": self.rank, "resolutionReceipt": dict(self.resolution_receipt)}


@dataclass(frozen=True)
class CandidateSelection:
    selected: Mapping[str, Candidate]
    ranked: Mapping[str, tuple[Candidate, ...]]
    receipt: Mapping[str, Any]


def _slide_key(slide: Mapping[str, Any]) -> str:
    key = slide.get("slideKey")
    if not isinstance(key, str) or not key:
        raise DeckIrGrammarError("deck_ir_request_mismatch")
    return key


def _primitive_families(composition: Mapping[str, Any]) -> tuple[str, ...]:
    relation_names = {item.get("relation") for item in composition.get("relations", []) if isinstance(item, Mapping)}
    if {"leftOf", "rightOf"}.intersection(relation_names):
        return ("split", "rail", "grid", "stage", "cluster", "inset")
    if {"above", "below", "flow"}.intersection(relation_names):
        return ("stack", "flow", "band", "grid", "stage", "cluster")
    return ("grid", "stage", "cluster", "inset", "rail", "split")


def enumerate_candidates(*, slide: Mapping[str, Any], composition: Mapping[str, Any], art_direction: Mapping[str, Any], grammar: DesignGrammarManifest, requested_k: int = BALANCED_QUALITY_K) -> tuple[Candidate, ...]:
    """Enumerate exactly K static candidates with no provider or wall-clock input."""

    slide = contract_mapping(slide)
    composition = contract_mapping(composition)
    art_direction = contract_mapping(art_direction)
    if requested_k < 4 or requested_k > 8:
        raise DeckIrGrammarError("candidate_policy_invalid")
    validate_composition_intent(composition, grammar=grammar)
    validate_art_direction_intent(art_direction, grammar=grammar)
    slide_key = _slide_key(slide)
    component_kinds = [item.get("kind") for item in slide.get("components", []) if isinstance(item, Mapping)]
    if not all(grammar.supports_component(kind) for kind in component_kinds):
        raise DeckIrGrammarError("generation_strategy_capability_missing")
    raw: list[Candidate] = []
    variants_by_family = {item.family: item for item in grammar.primitives}
    families = [family for family in _primitive_families(composition) if family in variants_by_family]
    seed = digest({"slide": slide, "composition": composition, "artDirection": art_direction, "grammar": grammar.manifest_digest})
    for variant, family in product(("base", "emphasis", "spacious"), families):
        primitive = variants_by_family[family]
        if variant not in primitive.variants:
            continue
        ordinal = len(raw) + 1
        layout_id = f"{primitive.primitive_id}/{variant}"
        # Static ranking hints, NOT measured quality. Browser/quality gates
        # remain authoritative; no ordinal-derived pseudo-measurements.
        wide = any(kind in {"table", "chart", "timeline", "process"} for kind in component_kinds)
        vector = {"hierarchy": 0.8, "whitespace": 0.8,
                  "roleFitness": 0.9 if wide and family in {"stage", "stack", "band"} else 0.8,
                  "artDirection": 0.9 if art_direction.get("asymmetry") != "none" and family in {"rail", "split", "inset"} else 0.8}
        resolution = resolve_art_intent(slide=slide, composition=composition, proposal=art_direction,
                                        grammar=grammar, layout_id=layout_id)
        payload = {"seed": seed, "slideKey": slide_key, "layoutId": layout_id, "qualityVector": vector,
                   "resolutionReceiptDigest": resolution.receipt["receiptDigest"]}
        candidate_digest = digest(payload)
        raw.append(Candidate(f"c{ordinal:02d}", slide_key, layout_id, variant, "passed", vector, digest({"family": family, "variant": variant, "focal": art_direction.get("focalAnchor")}), candidate_digest, ordinal,
                             composition, art_direction, resolution.intent, resolution.receipt))
        if len(raw) == requested_k:
            break
    if len(raw) != requested_k:
        raise DeckIrGrammarError("candidate_set_exhausted")
    return tuple(raw)


def hard_filter(candidates: Sequence[Candidate], *, floors: Mapping[str, float] | None = None) -> tuple[Candidate, ...]:
    floors = dict(floors or {"hierarchy": 0.7, "whitespace": 0.7, "roleFitness": 0.7, "artDirection": 0.7})
    return tuple(item for item in candidates if item.hard_constraint_status == "passed" and all(item.quality_vector.get(key, 0.0) >= value for key, value in floors.items()))


def select_deck_candidates(*, candidates_by_slide: Mapping[str, Sequence[Candidate]], grammar: DesignGrammarManifest, beam_width: int = MAX_PARETO_BEAM_WIDTH) -> CandidateSelection:
    """Select one deterministic Pareto-ranked combination, bounded to B<=8."""

    if not 1 <= beam_width <= MAX_PARETO_BEAM_WIDTH:
        raise DeckIrGrammarError("candidate_policy_invalid")
    ordered_keys = tuple(sorted(candidates_by_slide))
    feasible = {key: hard_filter(candidates_by_slide[key]) for key in ordered_keys}
    if any(not items for items in feasible.values()):
        raise DeckIrGrammarError("candidate_set_exhausted")
    beam: list[tuple[Candidate, ...]] = [()]
    for key in ordered_keys:
        expanded = [prefix + (candidate,) for prefix in beam for candidate in feasible[key]]
        def score(combo: tuple[Candidate, ...]) -> tuple[float, int, tuple[str, ...]]:
            total = sum(sum(item.quality_vector.values()) for item in combo)
            repeats = len(combo) - len({item.diversity_signature for item in combo})
            return (-total, repeats, tuple(item.candidate_digest for item in combo))
        beam = sorted(expanded, key=score)[:beam_width]
    chosen = beam[0]
    selected = {item.slide_key: item for item in chosen}
    ranked = {key: (selected[key], *sorted((item for item in items if item != selected[key]), key=lambda item: (-sum(item.quality_vector.values()), item.layout_id, item.variant_id, item.candidate_digest))) for key, items in feasible.items()}
    receipt = {
        "schemaVersion": "candidate-set-selection-receipt/v2", "grammarDigest": grammar.manifest_digest,
        "compilerVersion": "constraint-compiler/v1", "candidatePolicyDigest": grammar.candidate_policy_digest,
        "slides": [{"slideKey": key, "requestedK": len(candidates_by_slide[key]), "enumeratedCount": len(candidates_by_slide[key]), "feasibleCount": len(feasible[key]), "candidates": [item.to_mapping() for item in candidates_by_slide[key]], "selectedCandidateId": selected[key].candidate_id} for key in ordered_keys],
        "deckSelection": {"algorithm": "pareto-beam-v1", "beamWidth": beam_width, "selectedCombinationDigest": digest([item.candidate_digest for item in chosen])}, "providerCallsAdded": 0,
    }
    receipt = {**receipt, "receiptDigest": digest(receipt)}
    from .contracts import CandidateSetSelectionReceiptV2
    CandidateSetSelectionReceiptV2.model_validate(receipt)
    return CandidateSelection(selected, ranked, receipt)


__all__ = ["Candidate", "CandidateSelection", "enumerate_candidates", "hard_filter", "select_deck_candidates"]
