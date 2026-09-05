"""Coercion of provider response content into plain text or a JSON object.

Split out of ``utils/llm_utils.py``, which otherwise owns call orchestration
(retries, budgets, streaming, usage receipts). These four functions own a
different, purely local problem: the ``llmai`` providers hand back a string,
a list of content parts, a Pydantic model, or a dict depending on provider
and response format, and every caller needs one of two shapes -- text, or a
parsed object.

Reading a response is deliberately lenient (a malformed JSON body yields
``None`` rather than raising) because callers decide whether an unusable
response is worth a retry; this layer only reports that it could not be
read.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Optional

import dirtyjson
from llmai.shared import normalize_content_parts


def extract_text(content: Any) -> Optional[str]:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (bytes, bytearray)):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
        joined = "".join(parts)
        return joined or None
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    return None


def extract_structured_content(content: Any) -> Optional[dict]:
    if content is None:
        return None
    if isinstance(content, dict):
        return content
    if hasattr(content, "model_dump"):
        dumped = content.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped

    raw_text = extract_text(content)
    if not raw_text:
        return None

    try:
        parsed = dirtyjson.loads(raw_text)
    except Exception:
        return None

    if isinstance(parsed, dict):
        return dict(parsed)
    return None


def serialize_structured_content(content: Any) -> Optional[str]:
    parsed = extract_structured_content(content)
    if parsed is not None:
        return json.dumps(parsed, ensure_ascii=False)

    raw_text = extract_text(content)
    if raw_text:
        return raw_text
    return None


def message_content_to_text(content: Sequence[Any] | str | None) -> Optional[str]:
    joined = "".join(
        part.text
        for part in normalize_content_parts(content)
        if isinstance(getattr(part, "text", None), str)
    )
    return joined or None


__all__ = [
    "extract_structured_content",
    "extract_text",
    "message_content_to_text",
    "serialize_structured_content",
]
