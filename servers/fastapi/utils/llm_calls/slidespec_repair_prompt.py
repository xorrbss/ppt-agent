"""Bounded schema and prompt projection for SlideSpec repair proposals."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from llmai.shared import SystemMessage, UserMessage

from utils.llm_calls.propose_slidespec_repair import (
    ALLOWED_OPERATIONS,
    ALLOWED_STRATEGIES,
    CANONICAL_ISSUE_CODES,
    MAX_AFFECTED_BLOCKS,
    MAX_CANDIDATES,
    MAX_OPERATIONS,
    _CSS,
    _FORBIDDEN_KEYS,
    _HTML,
    _SAFE_ID,
    _SYSTEM,
    canonicalize_issue_code,
)

def _schema(allowed_operations: Sequence[str] | None = None) -> dict[str, Any]:
    operation_enum = sorted(
        set(allowed_operations or ALLOWED_OPERATIONS).intersection(ALLOWED_OPERATIONS)
    )
    if not operation_enum:
        operation_enum = sorted(ALLOWED_OPERATIONS)
    operation = {
        "type": "object",
        "required": ["op", "target"],
        "properties": {
            "op": {"type": "string", "enum": operation_enum},
            "target": {"type": "string", "minLength": 1, "maxLength": 256},
        },
        "additionalProperties": True,
    }
    plan = {
        "type": "object",
        "required": [
            "planId",
            "slideId",
            "baseSpecRevision",
            "baseSpecHash",
            "expectedContentDigest",
            "strategy",
            "operations",
        ],
        "properties": {
            "planId": {"type": "string", "minLength": 1, "maxLength": 128},
            "slideId": {"type": "string", "minLength": 1, "maxLength": 256},
            "baseSpecRevision": {"type": "integer", "minimum": 0},
            "baseSpecHash": {"type": "string", "minLength": 1, "maxLength": 256},
            "expectedContentDigest": {"type": "string", "minLength": 1, "maxLength": 256},
            "diagnosticRefs": {"type": "array", "items": {"type": "string"}},
            "strategy": {"type": "string", "enum": sorted(ALLOWED_STRATEGIES)},
            "operations": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_OPERATIONS,
                "items": operation,
            },
        },
        "additionalProperties": True,
    }
    return {
        "type": "object",
        "required": ["plans"],
        "properties": {
            "plans": {
                "type": "array",
                "maxItems": MAX_CANDIDATES,
                "items": plan,
            }
        },
        "additionalProperties": False,
    }


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "[truncated]"
    if isinstance(value, Mapping):
        return {
            str(key): _safe_value(item, depth=depth + 1)
            for key, item in value.items()
            if str(key).lower().replace("_", "") not in _FORBIDDEN_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_safe_value(item, depth=depth + 1) for item in value[:256]]
    if isinstance(value, str):
        if _HTML.search(value) or _CSS.search(value):
            return "[redacted]"
        return value[:1000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:200]


def _semantic_ids(spec_view: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key) in {"semanticId", "semantic_id", "target"}:
                    token = str(item)
                    if _SAFE_ID.fullmatch(token):
                        ids.add(token)
                visit(item)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                visit(item)

    visit(spec_view)
    return ids


def build_messages(
    spec_view: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]], *,
    slide_id: str, base_spec_revision: int, base_spec_hash: str,
    expected_content_digest: str,
    projection_context: Mapping[str, Any] | None = None,
) -> tuple[list[Any], set[str]]:
    safe_spec = _safe_value(spec_view)
    safe_diagnostics = []
    for diagnostic in diagnostics:
        canonical_code = canonicalize_issue_code(
            diagnostic.get("code", diagnostic.get("category"))
        )
        if canonical_code is None:
            continue
        safe_diagnostics.append(
            _safe_value({**dict(diagnostic), "code": canonical_code})
        )
    semantic_ids = _semantic_ids(spec_view)
    if not semantic_ids:
        raise ValueError("redacted spec view has no semanticId scope")
    if not slide_id or not base_spec_hash or not expected_content_digest:
        raise ValueError("base repair identity is incomplete")
    payload = {
        "slideId": slide_id,
        "baseSpecRevision": base_spec_revision,
        "baseSpecHash": base_spec_hash,
        "expectedContentDigest": expected_content_digest,
        "limits": {
            "maxCandidates": MAX_CANDIDATES,
            "maxOperations": MAX_OPERATIONS,
            "maxAffectedBlocks": MAX_AFFECTED_BLOCKS,
        },
        "issueEnum": sorted(CANONICAL_ISSUE_CODES),
        "semanticIdScope": sorted(semantic_ids),
        "projectionCapability": _safe_value(projection_context or {}),
        "specView": safe_spec,
        "diagnostics": safe_diagnostics,
    }
    prompt = "Propose bounded RepairPlans for this data:\n" + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    return [SystemMessage(content=_SYSTEM), UserMessage(content=prompt)], semantic_ids
