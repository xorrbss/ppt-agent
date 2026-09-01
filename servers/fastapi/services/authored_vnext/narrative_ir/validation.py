"""Fail-closed validation and canonical sealing for provider Narrative IR."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from .contracts import NarrativeDraftV1, NarrativeIRV1, model_payload


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
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


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
) -> NarrativeIRV1:
    """Validate provider meaning, enforce exact order/cardinality, then add identity."""

    reject_provider_layout_or_policy(payload)
    try:
        draft = NarrativeDraftV1.model_validate(payload)
    except ValidationError as error:
        raise NarrativeIRValidationError("narrative_ir_schema_invalid") from error
    if len(draft.slides) != admitted_slide_count:
        raise NarrativeIRValidationError("narrative_ir_slide_count_mismatch")
    if expected_source_unit_ids:
        actual = [item.source_unit_id for item in draft.source_unit_order]
        if actual != list(expected_source_unit_ids):
            raise NarrativeIRValidationError("narrative_ir_source_unit_order_mismatch")
    unsigned = model_payload(draft)
    unsigned["schemaVersion"] = "narrative-ir/v1"
    unsigned["operationId"] = str(operation_id)
    sealed = {**unsigned, "narrativeDigest": canonical_digest(unsigned)}
    try:
        result = NarrativeIRV1.model_validate(sealed)
    except ValidationError as error:  # defensive server-owned construction boundary
        raise NarrativeIRValidationError("narrative_ir_seal_invalid") from error
    validate_narrative_ir(result)
    return result


def validate_narrative_ir(value: NarrativeIRV1 | Mapping[str, Any]) -> NarrativeIRV1:
    reject_provider_layout_or_policy(value)
    try:
        model = value if isinstance(value, NarrativeIRV1) else NarrativeIRV1.model_validate(value)
    except ValidationError as error:
        raise NarrativeIRValidationError("narrative_ir_schema_invalid") from error
    payload = model_payload(model)
    supplied = payload.pop("narrativeDigest")
    if supplied != canonical_digest(payload):
        raise NarrativeIRValidationError("narrative_ir_digest_mismatch")
    return model


__all__ = [
    "NarrativeIRValidationError",
    "canonical_digest",
    "canonical_json",
    "reject_provider_layout_or_policy",
    "seal_narrative_ir",
    "validate_narrative_ir",
]
