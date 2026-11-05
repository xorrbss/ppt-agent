"""Cached-input token extraction for provider usage receipts.

OpenAI's Responses API (and codex) nests it under
``details.input_tokens_details.cached_tokens``; the Chat Completions API
nests it under ``details.prompt_tokens_details.cached_tokens``. llmai's
clients copy the raw usage minus totals verbatim into ``details``, so both
shapes can appear depending on which client produced the response.

Anthropic reports prompt-cache traffic as two flat counters instead:
``cache_read_input_tokens`` (served from cache) and
``cache_creation_input_tokens`` (written to cache). llmai's Anthropic client
folds both into ``input_tokens`` and leaves them in ``details``.
"""

from __future__ import annotations

from typing import Any

_MAX_TOKENS = 2_000_000_000


def _details(usage: Any) -> dict | None:
    details = (
        usage.get("details") if isinstance(usage, dict) else getattr(usage, "details", None)
    )
    return details if isinstance(details, dict) else None


def _bounded_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= _MAX_TOKENS else None


def cached_tokens_from_usage(usage: Any) -> int | None:
    """Return the provider's reported cached-input token count, if any."""

    details = _details(usage)
    if details is None:
        return None
    for key in ("input_tokens_details", "prompt_tokens_details"):
        nested = details.get(key)
        if not isinstance(nested, dict):
            continue
        cached = _bounded_count(nested.get("cached_tokens"))
        if cached is not None:
            return cached
    return _bounded_count(details.get("cache_read_input_tokens"))


def cache_write_tokens_from_usage(usage: Any) -> int | None:
    """Return the Anthropic prompt-cache write token count, if reported."""

    details = _details(usage)
    if details is None:
        return None
    return _bounded_count(details.get("cache_creation_input_tokens"))
