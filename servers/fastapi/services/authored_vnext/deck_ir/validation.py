"""Cross-object validation for the Deck IR v1.3 contract graph."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .canonical import canonical_digest, derive_qid_registry
from .contracts import (
    ArtDirectionIntentV1,
    CompositionIntentGraphV1,
    DeckCriticPatchV1,
    DeckIRV1,
    DesignGrammarManifestV1,
    RequirementCoverageReceiptV1,
    RequirementGraphV1,
    SEALED_CONTRACT_DIGEST_FIELDS,
    SourceBindingV1,
)


ModelT = TypeVar("ModelT", bound=BaseModel)
_FORBIDDEN_KEYS = {
    "x", "y", "width", "height", "px", "css", "html", "svg", "selector", "transform",
    "font", "fontname", "fontsize", "color", "opacity", "zindex", "shadow", "filter",
}
_RAW_MARKUP = re.compile(r"<\s*(?:!doctype|/?[a-z][a-z0-9:-]*)(?:\s|>|/)", re.I)
_RAW_CSS = re.compile(
    r"(?:\b(?:position|display|font-size|z-index|background|grid-template|transform)\s*:|\burl\s*\(|\d+(?:\.\d+)?px\b)",
    re.I,
)
_REMOTE_URL = re.compile(r"\b(?:https?|data|javascript):", re.I)
_RAW_INTENT_MODELS = (
    DeckIRV1,
    CompositionIntentGraphV1,
    ArtDirectionIntentV1,
    DeckCriticPatchV1,
)


class DeckIRContractError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise DeckIRContractError(code)


def _scan_safe_intent(value: Any, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).replace("-", "").replace("_", "").casefold()
            if normalized in _FORBIDDEN_KEYS:
                _fail("deck_ir_raw_geometry_or_style_forbidden")
            _scan_safe_intent(item, path=(*path, str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_safe_intent(item, path=(*path, str(index)))
    elif isinstance(value, str) and (_RAW_MARKUP.search(value) or _RAW_CSS.search(value) or _REMOTE_URL.search(value)):
        _fail("deck_ir_raw_geometry_or_style_forbidden")


def _validate_self_digest(model: BaseModel, digest_field: str) -> None:
    alias = model.__class__.model_fields[digest_field].alias or digest_field
    payload = model.model_dump(mode="json", by_alias=True)
    actual = payload.pop(alias)
    if actual != canonical_digest(payload):
        _fail("deck_ir_canonical_digest_mismatch")


def parse_contract(
    model_type: type[ModelT],
    payload: Mapping[str, Any] | ModelT,
    digest_field: str | None = None,
) -> ModelT:
    """Parse a strict contract and verify raw-input and self-digest boundaries."""

    if issubclass(model_type, _RAW_INTENT_MODELS):
        _scan_safe_intent(payload)
    try:
        model = payload if isinstance(payload, model_type) else model_type.model_validate(payload)
    except ValidationError as error:
        raise DeckIRContractError("deck_ir_schema_invalid") from error
    field = digest_field or SEALED_CONTRACT_DIGEST_FIELDS.get(model_type)
    if field is not None:
        _validate_self_digest(model, field)
    return model


def _assert_acyclic(nodes: set[str], edges: list[tuple[str, str]]) -> None:
    adjacency: dict[str, list[str]] = {node: [] for node in nodes}
    indegree = {node: 0 for node in nodes}
    for source, target in edges:
        if source == target:
            _fail("composition_intent_graph_invalid")
        adjacency[source].append(target)
        indegree[target] += 1
    pending = sorted(node for node, count in indegree.items() if count == 0)
    visited = 0
    while pending:
        node = pending.pop(0)
        visited += 1
        for target in adjacency[node]:
            indegree[target] -= 1
            if indegree[target] == 0:
                pending.append(target)
                pending.sort()
    if visited != len(nodes):
        _fail("composition_intent_graph_invalid")


def _validate_composition(deck: DeckIRV1, graph: CompositionIntentGraphV1) -> None:
    slide = next((item for item in deck.slides if item.slide_key == graph.slide_key), None)
    if slide is None or slide.composition_intent_graph_digest != graph.graph_digest:
        _fail("deck_ir_composition_digest_mismatch")
    zone_keys = [zone.zone_key for zone in graph.zones]
    if len(zone_keys) != len(set(zone_keys)) or set(graph.reading_order) != set(zone_keys):
        _fail("composition_intent_graph_invalid")
    if len(graph.reading_order) != len(set(graph.reading_order)):
        _fail("composition_intent_graph_invalid")
    nodes = set(zone_keys)
    edges: list[tuple[str, str]] = []
    for relation in graph.relations:
        if relation.from_ not in nodes or relation.to not in nodes:
            _fail("composition_intent_graph_invalid")
        if relation.strength == "hard" and relation.relation in {"leftOf", "above"}:
            edges.append((relation.from_, relation.to))
        elif relation.strength == "hard" and relation.relation in {"rightOf", "below"}:
            edges.append((relation.to, relation.from_))
    order_edges = list(zip(graph.reading_order, graph.reading_order[1:]))
    _assert_acyclic(nodes, edges + order_edges)
    owners = Counter(ref for zone in graph.zones for ref in zone.content_refs)
    content_keys = {item.element_key for item in slide.components if item.kind != "decoration"}
    if any(owners[key] != 1 for key in content_keys):
        _fail("composition_intent_content_owner_invalid")
    allowed = content_keys | {"title", "summary", "claim"} | {
        item.element_key for item in slide.components if item.kind == "decoration"
    }
    if not set(owners).issubset(allowed):
        _fail("composition_intent_content_owner_invalid")


def _validate_art_direction(graph: CompositionIntentGraphV1, intent: ArtDirectionIntentV1, *, provider_proposal: bool = False) -> None:
    if intent.slide_key != graph.slide_key or intent.composition_intent_graph_digest != graph.graph_digest:
        _fail("deck_ir_art_direction_digest_mismatch")
    roles = {zone.zone_key: zone.semantic_role for zone in graph.zones}
    if intent.focal_anchor.zone_key not in roles:
        _fail("art_direction_intent_invalid")
    for first, second in intent.overlap_policy.allowed_zone_pairs:
        if first not in roles or second not in roles:
            _fail("art_direction_intent_invalid")
        if first == second:
            _fail("art_direction_intent_invalid")
        if not provider_proposal and not ({roles[first], roles[second]} & {"decoration", "background"}):
            _fail("art_direction_overlap_unsafe")


def validate_deck_ir(
    deck: DeckIRV1,
    compositions: Sequence[CompositionIntentGraphV1],
    art_directions: Sequence[ArtDirectionIntentV1],
    requirement_graph: RequirementGraphV1 | None = None,
    source_bindings: Iterable[SourceBindingV1] = (),
    *,
    admitted_slide_count: int | None = None,
    grammar_manifest: DesignGrammarManifestV1 | None = None,
    provider_art_proposals: bool = False,
) -> str:
    """Validate a complete Deck IR graph and return its canonical digest."""

    _scan_safe_intent(deck)
    if admitted_slide_count is not None and len(deck.slides) != admitted_slide_count:
        _fail("deck_ir_slide_count_mismatch")
    if grammar_manifest is not None and deck.design_language.grammar_id != grammar_manifest.grammar_id:
        _fail("deck_ir_grammar_id_mismatch")
    comp_by_slide = {item.slide_key: item for item in compositions}
    art_by_slide = {item.slide_key: item for item in art_directions}
    if len(comp_by_slide) != len(compositions) or len(art_by_slide) != len(art_directions):
        _fail("deck_ir_slide_intent_duplicate")
    if set(comp_by_slide) != {item.slide_key for item in deck.slides} or set(art_by_slide) != set(comp_by_slide):
        _fail("deck_ir_slide_intent_incomplete")
    for graph in compositions:
        _scan_safe_intent(graph)
        _validate_self_digest(graph, "graph_digest")
        _validate_composition(deck, graph)
        intent = art_by_slide[graph.slide_key]
        _scan_safe_intent(intent)
        _validate_self_digest(intent, "intent_digest")
        slide = next(item for item in deck.slides if item.slide_key == graph.slide_key)
        if slide.art_direction_intent_digest != intent.intent_digest:
            _fail("deck_ir_art_direction_digest_mismatch")
        _validate_art_direction(graph, intent, provider_proposal=provider_art_proposals)
    derive_qid_registry(deck)

    bindings = list(source_bindings)
    binding_by_id = {item.source_binding_id: item for item in bindings}
    if len(binding_by_id) != len(bindings):
        _fail("deck_ir_source_binding_duplicate")
    requirement_by_id = (
        {item.requirement_id: item for item in requirement_graph.requirements}
        if requirement_graph is not None
        else {}
    )
    for slide in deck.slides:
        if requirement_graph is not None and not set(slide.requirement_ids).issubset(requirement_by_id):
            _fail("deck_ir_requirement_reference_invalid")
        if not set(slide.source_binding_ids).issubset(binding_by_id):
            _fail("deck_ir_source_binding_reference_invalid")
        for component in slide.components:
            if component.source_binding_id is not None:
                binding = binding_by_id.get(component.source_binding_id)
                if binding is None or component.payload.source_content_digest != binding.content_digest:
                    _fail("deck_ir_source_binding_invalid")
    source_counts = Counter(
        component.source_binding_id
        for slide in deck.slides
        for component in slide.components
        if component.source_binding_id is not None
    )
    if any(source_counts[binding.source_binding_id] < binding.required_occurrences for binding in bindings):
        _fail("deck_ir_source_binding_occurrence_missing")
    if requirement_graph is not None:
        all_binding_ids = set(binding_by_id)
        for requirement in requirement_graph.requirements:
            if not set(requirement.source_binding_ids).issubset(all_binding_ids):
                _fail("deck_ir_requirement_source_binding_invalid")
    return canonical_digest(deck)


def requirement_coverage_totals(graph, occurrences: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compute exact occurrence evidence, including failures; never invent PASS."""
    counts = Counter(item["requirementId"] for item in occurrences)
    required = [item for item in graph.requirements if item.priority == "required"]
    optional = [item for item in graph.requirements if item.priority == "optional"]
    duplicates = [{"requirementId": item.requirement_id,
                   "slideKeys": sorted({entry["slideKey"] for entry in occurrences if entry["requirementId"] == item.requirement_id}),
                   "reason": "maximum-occurrences-exceeded"}
                  for item in graph.requirements if counts[item.requirement_id] > item.maximum_occurrences]
    covered_required = sum(counts[item.requirement_id] >= item.minimum_occurrences for item in required)
    return {"requiredTotal": len(required), "requiredCovered": covered_required,
            "optionalTotal": len(optional), "optionalCovered": sum(counts[item.requirement_id] >= item.minimum_occurrences for item in optional),
            "duplicateViolations": duplicates,
            "uncoveredRequirementIds": [item.requirement_id for item in graph.requirements if counts[item.requirement_id] < item.minimum_occurrences],
            "occurrences": list(occurrences),
            "status": "passed" if covered_required == len(required) and not duplicates else "failed"}


def validate_requirement_coverage(
    receipt: RequirementCoverageReceiptV1,
    graph: RequirementGraphV1,
    deck: DeckIRV1,
) -> None:
    _validate_self_digest(graph, "graph_digest")
    _validate_self_digest(receipt, "receipt_digest")
    if receipt.requirement_graph_digest != graph.graph_digest or receipt.deck_ir_digest != canonical_digest(deck):
        _fail("deck_ir_coverage_input_digest_mismatch")
    requirements = {item.requirement_id: item for item in graph.requirements}
    components = {
        (slide.slide_key, component.element_key): (slide, component)
        for slide in deck.slides
        for component in slide.components
    }
    counts: Counter[str] = Counter()
    occurrence_keys: set[tuple[str, str, str]] = set()
    for occurrence in receipt.occurrences:
        occurrence_key = (occurrence.requirement_id, occurrence.slide_key, occurrence.element_key)
        if occurrence_key in occurrence_keys:
            _fail("deck_ir_coverage_occurrence_duplicate")
        occurrence_keys.add(occurrence_key)
        requirement = requirements.get(occurrence.requirement_id)
        target = components.get((occurrence.slide_key, occurrence.element_key))
        if requirement is None or target is None:
            _fail("deck_ir_coverage_occurrence_invalid")
        slide, component = target
        if occurrence.requirement_id not in component.requirement_ids:
            _fail("deck_ir_coverage_occurrence_invalid")
        if requirement.allowed_slide_roles and slide.role not in requirement.allowed_slide_roles:
            _fail("deck_ir_coverage_slide_role_invalid")
        if occurrence.source_binding_id is not None and occurrence.source_binding_id != component.source_binding_id:
            _fail("deck_ir_coverage_source_binding_invalid")
        counts[occurrence.requirement_id] += 1
    required = [item for item in graph.requirements if item.priority == "required"]
    optional = [item for item in graph.requirements if item.priority == "optional"]
    covered_required = sum(counts[item.requirement_id] >= item.minimum_occurrences for item in required)
    covered_optional = sum(counts[item.requirement_id] >= item.minimum_occurrences for item in optional)
    uncovered = {item.requirement_id for item in graph.requirements if counts[item.requirement_id] < item.minimum_occurrences}
    duplicate_ids = {item.requirement_id for item in graph.requirements if counts[item.requirement_id] > item.maximum_occurrences}
    receipt_duplicate_ids = {item.requirement_id for item in receipt.duplicate_violations}
    if (
        receipt.required_total != len(required)
        or receipt.required_covered != covered_required
        or receipt.optional_total != len(optional)
        or receipt.optional_covered != covered_optional
        or set(receipt.uncovered_requirement_ids) != uncovered
        or duplicate_ids != receipt_duplicate_ids
    ):
        _fail("deck_ir_coverage_receipt_inexact")


def validate_revision_immutability(base: DeckIRV1, candidate: DeckIRV1) -> None:
    """Enforce protected title, source identity, and QID registry invariants."""

    if base.deck_key != candidate.deck_key or base.title != candidate.title:
        _fail("deck_ir_title_immutable")
    if derive_qid_registry(base) != derive_qid_registry(candidate):
        _fail("deck_ir_qid_immutable")
    base_slides = {slide.slide_key: slide for slide in base.slides}
    candidate_slides = {slide.slide_key: slide for slide in candidate.slides}
    if set(base_slides) != set(candidate_slides):
        _fail("deck_ir_qid_immutable")
    for slide_key, base_slide in base_slides.items():
        next_slide = candidate_slides[slide_key]
        base_elements = {item.element_key: item for item in base_slide.components}
        next_elements = {item.element_key: item for item in next_slide.components}
        for element_key, base_component in base_elements.items():
            next_component = next_elements[element_key]
            is_title = base_component.semantic_role == "title" or element_key == "title"
            if is_title and base_component.payload != next_component.payload:
                _fail("deck_ir_title_immutable")
            if base_component.source_binding_id is not None and (
                base_component.source_binding_id != next_component.source_binding_id
                or base_component.payload != next_component.payload
            ):
                _fail("deck_ir_source_immutable")


def validate_critic_patch(patch: DeckCriticPatchV1, deck: DeckIRV1) -> None:
    _scan_safe_intent(patch)
    if patch.deck_ir_digest != canonical_digest(deck):
        _fail("deck_ir_critic_input_digest_mismatch")
    slides = {slide.slide_key for slide in deck.slides}
    targets = [target.slide_key for target in patch.targets]
    if len(targets) != len(set(targets)) or not set(targets).issubset(slides):
        _fail("deck_ir_critic_target_invalid")


__all__ = [
    "DeckIRContractError",
    "parse_contract",
    "validate_critic_patch",
    "validate_deck_ir",
    "validate_requirement_coverage",
    "validate_revision_immutability",
]
