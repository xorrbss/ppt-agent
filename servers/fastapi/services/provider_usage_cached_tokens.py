"""Cached-input token extraction for provider usage receipts.

OpenAI's Responses API (and codex) nests it under
``details.input_tokens_details.cached_tokens``; the Chat Completions API
nests it under ``details.prompt_tokens_details.cached_tokens``. llmai's
clients copy the raw usage minus totals verbatim into ``details``, so both
shapes can appear depending on which client produced the response.
"""

from __future__ import annotations

from typing import Any


def cached_tokens_from_usage(usage: Any) -> int | None:
    """Return the provider's reported cached-input token count, if any."""

    details = (
        usage.get("details") if isinstance(usage, dict) else getattr(usage, "details", None)
    )
    if not isinstance(details, dict):
        return None
    for key in ("input_tokens_details", "prompt_tokens_details"):
        nested = details.get(key)
        if not isinstance(nested, dict):
            continue
        cached = nested.get("cached_tokens")
        if isinstance(cached, bool) or not isinstance(cached, int):
            continue
        if 0 <= cached <= 2_000_000_000:
            return cached
    return None
