"""Comparison-review configuration tokens sealed into the release-review policy.

Moved unchanged out of ``services.authored_quality_telemetry`` (and the prompt
version out of ``generation_core.authoring.author_vision_qa``) when the
authored quality review was deleted. ``release_review_checker_policy`` digests
these values, so they must stay byte-identical to what earlier receipts sealed.
"""

from __future__ import annotations

import re
from typing import Any

from utils.llm_calls.critique_slide import STYLE_COMPARISON_PROMPT_VERSION
from utils.llm_provider import get_model

_SAFE_OBSERVATION_TOKEN = re.compile(r"^[A-Za-z0-9._:/@+-]{1,128}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")

STYLE_COMPARISON_VQA_PROMPT_VERSION = (
    f"authored-style-comparison-v2:{STYLE_COMPARISON_PROMPT_VERSION}"
)


def safe_review_model() -> str:
    """Return a bounded configuration token, never a prompt or provider response."""
    try:
        model = str(get_model() or "unknown")
    except Exception:  # noqa: BLE001 - telemetry is best-effort only
        return "unknown"
    return _safe_configuration_token(model)


def _safe_configuration_token(value: Any) -> str:
    """Return one log-safe identifier, rejecting paths and free-form content."""
    token = str(value or "unknown")
    token_lower = token.casefold()
    if (
        not _SAFE_OBSERVATION_TOKEN.fullmatch(token)
        or _WINDOWS_ABSOLUTE_PATH.match(token)
        or token.startswith(("/", "\\", "file:"))
        or "\\" in token
        or token_lower.startswith(("sk-", "api_key", "apikey", "bearer", "basic"))
    ):
        return "unknown"
    return token


def style_comparison_observation_metadata() -> dict[str, str]:
    """Read comparison-review configuration without exposing cache-key material."""
    return {
        "model": safe_review_model(),
        "prompt_version": _safe_configuration_token(STYLE_COMPARISON_VQA_PROMPT_VERSION),
        "cache_version": _safe_configuration_token(STYLE_COMPARISON_VQA_PROMPT_VERSION),
    }
