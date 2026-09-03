"""Bounded deterministic local candidate enumeration and deck selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from .intent_resolution import resolve_art_intent
from .zone_layout import solve_zone_grid

from .grammar import BALANCED_QUALITY_K, MAX_PARETO_BEAM_WIDTH, DesignGrammarManifest, DeckIrGrammarError, contract_mapping, digest, validate_art_direction_intent, validate_composition_intent


SCORING_BASIS = "solver-structural-fitness/v1"
_FITNESS_DIMENSIONS = ("hierarchy", "whitespace", "roleFitness", "artDirection")
_VISUAL_WEIGHT = {"ambient": 0, "supporting": 1, "primary": 2, "dominant": 3}


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


def _candidate_layouts(families, primitives, art):
    """Keep six distinct families, with expressive variants inside fixed K."""
    focal = art["focalAnchor"]["strength"] != "subtle"
    generous = art["negativeSpace"]["amount"] == "generous"
    preferred = {
        "grid": "base", "rail": "emphasis" if focal else "spacious",
        "split": "emphasis" if art["asymmetry"] != "none" else "base",
        "stage": "emphasis" if focal else "base", "stack": "spacious" if generous else "base",
        "flow": "base", "cluster": "spacious", "inset": "spacious" if generous else "emphasis",
        "band": "emphasis",
    }
    primary = [(family, preferred[family] if preferred[family] in primitives[family].variants else primitives[family].variants[0]) for family in families]
    yield from primary
    # The policy permits K=7/8, but never expands K after a browser failure.
    for variant in ("base", "emphasis", "spacious"):
        for family in families:
            if variant in primitives[family].variants and (family, variant) not in primary:
                yield family, variant


def _relation_satisfied(edge, placements):
    first, second = placements[edge["from"]], placements[edge["to"]]
    relation = edge["relation"]
    return {
        "leftOf": first["column"] + first["span"] <= second["column"],
        "rightOf": second["column"] + second["span"] <= first["column"],
        "above": first["row"] + first["rowSpan"] <= second["row"],
        "below": second["row"] + second["rowSpan"] <= first["row"],
        "alignTop": first["row"] == second["row"],
        "alignCenter": 2 * first["row"] + first["rowSpan"] == 2 * second["row"] + second["rowSpan"],
        "equalWidth": first["span"] == second["span"],
        "flow": first["row"] + first["rowSpan"] <= second["row"] or (first["row"] == second["row"] and first["column"] + first["span"] <= second["column"]),
        "emphasis": first["span"] * first["rowSpan"] >= second["span"] * second["rowSpan"],
    }[relation]


def _structural_fitness(composition, art, placements):
    """Score solved intent agreement, never aesthetic quality or readability.

    Legacy qualityVector keys are retained for receipt compatibility. Every
    value comes from grid spans/rows and declared intent; a vacuous constraint
    is satisfied, not an invented visual observation. Chromium and visual
    acceptance remain separate, mandatory authorities.
    """
    zones = {item["zoneKey"]: item for item in composition["zones"]}
    order = composition["readingOrder"]
    hierarchy = []
    for index, key in enumerate(order):
        for other in order[index + 1:]:
            first, second = placements[key], placements[other]
            if first["row"] != second["row"]:
                continue
            priority = _VISUAL_WEIGHT.get(zones[key].get("visualWeight"), 1) - _VISUAL_WEIGHT.get(zones[other].get("visualWeight"), 1)
            if priority:
                hierarchy.append((first["span"] - second["span"]) * priority >= 0)
    row_count = max(item["row"] + item["rowSpan"] - 1 for item in placements.values())
    occupied = {(row, column) for item in placements.values() for row in range(item["row"], item["row"] + item["rowSpan"]) for column in range(item["column"], item["column"] + item["span"])}
    empty_fraction = 1 - len(occupied) / (12 * row_count)
    requested_empty = {"compact": 0, "balanced": 1 / 12, "generous": 2 / 12}[art["negativeSpace"]["amount"]]
    role_fit = sum(min(1, placements[key]["span"] / min(12, zones[key]["preferredColumnSpan"])) * len(zones[key]["contentRefs"]) for key in order) / sum(len(zone["contentRefs"]) for zone in zones.values())
    agreement = [float(_relation_satisfied(edge, placements)) for edge in composition["relations"] if edge["strength"] == "soft"]
    focal = art["focalAnchor"]["zoneKey"]
    agreement.append(min(1, placements[focal]["span"] / min(12, zones[focal]["preferredColumnSpan"])))
    bias = art["negativeSpace"]["bias"]
    for row in range(1, row_count + 1):
        row_items = [item for item in placements.values() if item["row"] <= row < item["row"] + item["rowSpan"]]
        left = min(item["column"] for item in row_items) - 1
        right = 13 - max(item["column"] + item["span"] for item in row_items)
        if bias in {"left", "right"}:
            agreement.append(float(left >= right if bias == "left" else right >= left))
    return dict(zip(_FITNESS_DIMENSIONS, (round(value, 6) for value in (
        sum(hierarchy) / len(hierarchy) if hierarchy else 1.0,
        1 - abs(empty_fraction - requested_empty), role_fit, sum(agreement) / len(agreement),
    )), strict=True))


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
    for family, variant in _candidate_layouts(families, variants_by_family, art_direction):
        primitive = variants_by_family[family]
        ordinal = len(raw) + 1
        layout_id = f"{primitive.primitive_id}/{variant}"
        resolution = resolve_art_intent(slide=slide, composition=composition, proposal=art_direction,
                                        grammar=grammar, layout_id=layout_id)
        placements, status = {}, "passed"
        try:
            placements = solve_zone_grid(composition, slide["components"], family, variant=variant, art_direction=resolution.intent)
            # Grid-testable hard relations are feasibility, not ranking. Pixel
            # alignment, emphasis and all text safety still need Chromium.
            if any(not _relation_satisfied(edge, placements) for edge in composition["relations"] if edge["strength"] == "hard" and edge["relation"] not in {"alignCenter", "emphasis"}):
                raise DeckIrGrammarError("deck_ir_capacity_unsatisfiable")
            vector = _structural_fitness(composition, art_direction, placements)
        except DeckIrGrammarError as error:
            if error.code != "deck_ir_capacity_unsatisfiable":
                raise
            status = "failed"
            vector = {key: 0.0 for key in _FITNESS_DIMENSIONS}
        payload = {"seed": seed, "slideKey": slide_key, "layoutId": layout_id, "qualityVector": vector,
                   "resolutionReceiptDigest": resolution.receipt["receiptDigest"]}
        candidate_digest = digest(payload)
        signature = digest({"family": family, "variant": variant, "grid": [(item["row"], item["column"], item["span"], item["rowSpan"]) for item in placements.values()]})
        raw.append(Candidate(f"c{ordinal:02d}", slide_key, layout_id, variant, status, vector, signature, candidate_digest, ordinal,
                             composition, art_direction, resolution.intent, resolution.receipt))
        if len(raw) == requested_k:
            break
    if len(raw) != requested_k:
        raise DeckIrGrammarError("candidate_set_exhausted")
    return tuple(raw)


def hard_filter(candidates: Sequence[Candidate], *, floors: Mapping[str, float] | None = None) -> tuple[Candidate, ...]:
    # Structural preference is not an aesthetic hard gate. Callers may request
    # an explicit ranking floor; no fabricated visual-quality floor is applied.
    floors = dict(floors or {})
    return tuple(item for item in candidates if item.hard_constraint_status == "passed" and all(item.quality_vector.get(key, 0.0) >= value for key, value in floors.items()))


def _pareto_rank(combinations):
    """True non-dominated layers, with deterministic diversity tie-breaking."""
    remaining = list(combinations)
    vectors = {tuple(item.candidate_digest for item in combo): tuple(sum(item.quality_vector.get(key, 0.0) for item in combo) for key in _FITNESS_DIMENSIONS) for combo in remaining}

    def vector(combo):
        return vectors[tuple(item.candidate_digest for item in combo)]

    def dominates(first, second):
        left, right = vector(first), vector(second)
        pairs = list(zip(left, right, strict=True))
        return all(a >= b for a, b in pairs) and any(a > b for a, b in pairs)

    def tie_break(combo):
        repeats = len(combo) - len({item.diversity_signature for item in combo})
        return repeats, -sum(vector(combo)), tuple(item.candidate_digest for item in combo)

    while remaining:
        frontier = [combo for combo in remaining if not any(dominates(other, combo) for other in remaining)]
        yield from sorted(frontier, key=tie_break)
        remaining = [combo for combo in remaining if combo not in frontier]


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
        beam = []
        for combo in _pareto_rank(expanded):
            beam.append(combo)
            if len(beam) == beam_width:
                break
    chosen = beam[0]
    selected = {item.slide_key: item for item in chosen}
    ranked = {key: (selected[key], *(combo[0] for combo in _pareto_rank([(item,) for item in items if item != selected[key]]))) for key, items in feasible.items()}
    receipt = {
        "schemaVersion": "candidate-set-selection-receipt/v2", "grammarDigest": grammar.manifest_digest,
        "compilerVersion": "constraint-compiler/v1", "candidatePolicyDigest": grammar.candidate_policy_digest,
        "scoringBasis": SCORING_BASIS,
        "slides": [{"slideKey": key, "requestedK": len(candidates_by_slide[key]), "enumeratedCount": len(candidates_by_slide[key]), "feasibleCount": len(feasible[key]), "candidates": [item.to_mapping() for item in candidates_by_slide[key]], "selectedCandidateId": selected[key].candidate_id} for key in ordered_keys],
        "deckSelection": {"algorithm": "pareto-beam-v1", "beamWidth": beam_width, "selectedCombinationDigest": digest([item.candidate_digest for item in chosen])}, "providerCallsAdded": 0,
    }
    receipt = {**receipt, "receiptDigest": digest(receipt)}
    from .contracts import CandidateSetSelectionReceiptV2
    CandidateSetSelectionReceiptV2.model_validate(receipt)
    return CandidateSelection(selected, ranked, receipt)


__all__ = ["Candidate", "CandidateSelection", "enumerate_candidates", "hard_filter", "select_deck_candidates"]
