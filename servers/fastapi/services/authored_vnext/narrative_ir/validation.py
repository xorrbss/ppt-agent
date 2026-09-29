"""Fail-closed validation and canonical sealing for provider Narrative IR."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from models.authored_vnext.narrative_ir_contracts import ContentGraphV1, NarrativeDraftV1, NarrativeIRV1, model_payload
from utils.hashing import canonical_json_bytes, sha256_prefixed


_FORBIDDEN_KEYS = frozenset(
    {
        "absolute",
        "allowedtags",
        "background",
        "color",
        "coordinates",
        "csp",
        "css",
        "egresspolicy",
        "filter",
        "font",
        "fontname",
        "fontsize",
        "height",
        "href",
        "html",
        "javascript",
        "js",
        "markup",
        "networkpolicy",
        "opacity",
        "position",
        "px",
        "safetypolicy",
        "sanitizer",
        "script",
        "securityheaders",
        "selector",
        "style",
        "stylesheet",
        "svg",
        "transform",
        "url",
        "width",
        "x",
        "y",
        "zindex",
    }
)
_RAW_MARKUP = re.compile(r"<\s*(?:!doctype|/?[a-z][a-z0-9:-]*)(?:\s|>|/)", re.I)
_RAW_STYLE = re.compile(
    r"(?:\b(?:position|display|font-size|z-index|background|grid-template|transform)\s*:|"
    r"\burl\s*\(|\d+(?:\.\d+)?px\b)",
    re.I,
)
_REMOTE_OR_EXECUTABLE_URL = re.compile(r"\b(?:https?|data|javascript|file):", re.I)


class NarrativeIRValidationError(ValueError):
    code = "narrative_ir_invalid"

    def __init__(self, code: str | None = None) -> None:
        resolved = code or self.code
        super().__init__(resolved)
        self.code = resolved


def canonical_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    return canonical_json_bytes(value).decode("utf-8")


def canonical_digest(value: Any) -> str:
    return sha256_prefixed(canonical_json(value).encode("utf-8"))


def reject_provider_layout_or_policy(value: Any) -> None:
    """Reject provider-authored rendering or safety authority recursively."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).replace("-", "").replace("_", "").casefold()
            if normalized in _FORBIDDEN_KEYS or normalized.endswith("px"):
                raise NarrativeIRValidationError("narrative_ir_layout_or_policy_forbidden")
            reject_provider_layout_or_policy(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            reject_provider_layout_or_policy(item)
    elif isinstance(value, str) and (
        _RAW_MARKUP.search(value)
        or _RAW_STYLE.search(value)
        or _REMOTE_OR_EXECUTABLE_URL.search(value)
    ):
        raise NarrativeIRValidationError("narrative_ir_layout_or_policy_forbidden")


def seal_narrative_ir(
    payload: Mapping[str, Any],
    *,
    operation_id: UUID,
    admitted_slide_count: int,
    expected_source_unit_ids: Sequence[str] = (),
    content_graph: ContentGraphV1 | Mapping[str, Any] | None = None,
) -> NarrativeIRV1:
    """Validate provider meaning, enforce exact order/cardinality, then add identity."""

    if any(
        key in payload
        for key in ("generationLane", "generation_lane", "contentGraphDigest", "content_graph_digest")
    ):
        raise NarrativeIRValidationError("narrative_ir_server_authority_forbidden")
    provider_slides = payload.get("slides")
    if isinstance(provider_slides, Sequence):
        for item in provider_slides:
            if isinstance(item, Mapping) and (
                "sealedContentUnits" in item or "sealed_content_units" in item
            ):
                raise NarrativeIRValidationError("narrative_ir_server_authority_forbidden")
    reject_provider_layout_or_policy(payload)
    try:
        draft = NarrativeDraftV1.model_validate(payload)
    except ValidationError as error:
        raise NarrativeIRValidationError("narrative_ir_schema_invalid") from error
    if len(draft.slides) != admitted_slide_count:
        raise NarrativeIRValidationError("narrative_ir_slide_count_mismatch")
    graph: ContentGraphV1 | None = None
    if content_graph is not None:
        from .content_graph import validate_content_graph

        graph = validate_content_graph(content_graph)
        if graph.generation_lane != "new-authoring":
            raise NarrativeIRValidationError("narrative_ir_generation_lane_invalid")
        expected_source_unit_ids = tuple(
            item.source_unit_id for item in graph.source_unit_order
        )
    if expected_source_unit_ids:
        actual = [item.source_unit_id for item in draft.source_unit_order]
        if actual and actual != list(expected_source_unit_ids):
            raise NarrativeIRValidationError("narrative_ir_source_unit_order_mismatch")
    if graph is not None:
        units_by_id = {
            unit.content_unit_id: unit for page in graph.pages for unit in page.content_units
        }
        slide_unit_ids = [tuple(slide.content_unit_ids) for slide in draft.slides]
        if not any(slide_unit_ids) and len(graph.pages) == len(draft.slides):
            slide_unit_ids = [
                tuple(unit.content_unit_id for unit in page.content_units)
                for page in graph.pages
            ]
        flattened = [unit_id for ids in slide_unit_ids for unit_id in ids]
        if len(flattened) != len(set(flattened)):
            raise NarrativeIRValidationError("narrative_ir_content_unit_duplicate")
        if set(flattened) != set(units_by_id):
            raise NarrativeIRValidationError("narrative_ir_content_unit_coverage_mismatch")
        sealed_slides: list[dict[str, Any]] = []
        for slide, unit_ids in zip(draft.slides, slide_unit_ids, strict=True):
            slide_payload = model_payload(slide)
            slide_payload["contentUnitIds"] = list(unit_ids)
            slide_payload["sealedContentUnits"] = [
                model_payload(units_by_id[unit_id]) for unit_id in unit_ids
            ]
            ownership = {
                item.source_binding_id: item
                for unit_id in unit_ids
                for item in units_by_id[unit_id].source_ownership
            }
            slide_payload["sourceOwnership"] = [
                model_payload(ownership[key]) for key in sorted(ownership)
            ]
            sealed_slides.append(slide_payload)
        unsigned = model_payload(draft)
        unsigned["slides"] = sealed_slides
        unsigned["sourceUnitOrder"] = [
            model_payload(item) for item in graph.source_unit_order
        ]
        unsigned["contentGraphDigest"] = graph.content_graph_digest
    else:
        unsigned = model_payload(draft)
        unsigned["contentGraphDigest"] = None
    unsigned["schemaVersion"] = "narrative-ir/v1"
    unsigned["operationId"] = str(operation_id)
    unsigned["generationLane"] = "new-authoring"
    sealed = {**unsigned, "narrativeDigest": canonical_digest(unsigned)}
    try:
        result = NarrativeIRV1.model_validate(sealed)
    except ValidationError as error:  # defensive server-owned construction boundary
        raise NarrativeIRValidationError("narrative_ir_seal_invalid") from error
    validate_narrative_ir(result)
    return result


def validate_narrative_ir(
    value: NarrativeIRV1 | Mapping[str, Any],
    *,
    content_graph: ContentGraphV1 | Mapping[str, Any] | None = None,
) -> NarrativeIRV1:
    try:
        model = value if isinstance(value, NarrativeIRV1) else NarrativeIRV1.model_validate(value)
    except ValidationError as error:
        raise NarrativeIRValidationError("narrative_ir_schema_invalid") from error
    payload = model_payload(model)
    supplied = payload.pop("narrativeDigest")
    if supplied != canonical_digest(payload):
        raise NarrativeIRValidationError("narrative_ir_digest_mismatch")
    unit_ids = [
        unit_id for slide in model.slides for unit_id in slide.content_unit_ids
    ]
    sealed_units = [
        unit for slide in model.slides for unit in slide.sealed_content_units
    ]
    if len(unit_ids) != len(set(unit_ids)):
        raise NarrativeIRValidationError("narrative_ir_content_unit_duplicate")
    if unit_ids != [unit.content_unit_id for unit in sealed_units]:
        raise NarrativeIRValidationError("narrative_ir_content_unit_binding_mismatch")
    for unit in sealed_units:
        unit_payload = model_payload(unit)
        unit_digest = unit_payload.pop("contentDigest")
        if unit_digest != canonical_digest(unit_payload):
            raise NarrativeIRValidationError("narrative_ir_content_unit_digest_mismatch")
    provider_view = model_payload(model)
    provider_view.pop("narrativeDigest", None)
    provider_view.pop("operationId", None)
    provider_view.pop("generationLane", None)
    provider_view.pop("contentGraphDigest", None)
    for slide in provider_view.get("slides", []):
        if isinstance(slide, dict):
            slide.pop("sealedContentUnits", None)
    reject_provider_layout_or_policy(provider_view)
    if content_graph is not None:
        from .content_graph import validate_content_graph

        graph = validate_content_graph(content_graph)
        if model.content_graph_digest != graph.content_graph_digest:
            raise NarrativeIRValidationError("narrative_ir_content_graph_digest_mismatch")
        expected = [
            unit.content_unit_id for page in graph.pages for unit in page.content_units
        ]
        if unit_ids != expected:
            # Slide split/merge may change grouping, but never source order.
            raise NarrativeIRValidationError("narrative_ir_content_unit_order_mismatch")
    return model


__all__ = [
    "NarrativeIRValidationError",
    "canonical_digest",
    "canonical_json",
    "reject_provider_layout_or_policy",
    "seal_narrative_ir",
    "validate_narrative_ir",
]
