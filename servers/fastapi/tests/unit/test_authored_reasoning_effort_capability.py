"""An unsupported reasoning effort falls back to the lowest level the model accepts.

Regression for 2026-09-05: fast-role pages (cover, section, agenda, closing)
author at "minimal". gpt-5.6-sol lacks "minimal" but has "none", so the old
fallback worked; gpt-6-astra lacks both, and the fallback returned "minimal"
unchanged, which the provider rejects with a 400 that is not retried.
"""

from __future__ import annotations

from utils.llm_calls.author_slide import _normalized_authoring_reasoning_effort


def test_astra_maps_minimal_to_low() -> None:
    assert _normalized_authoring_reasoning_effort("gpt-6-astra", "minimal") == "low"


def test_sol_still_maps_minimal_to_none() -> None:
    assert _normalized_authoring_reasoning_effort("gpt-5.6-sol", "minimal") == "none"


def test_supported_levels_pass_through() -> None:
    for level in ("low", "medium", "high", "xhigh", "max"):
        assert _normalized_authoring_reasoning_effort("gpt-6-astra", level) == level


def test_luna_direct_probe_capabilities() -> None:
    for level in ("none", "low", "medium", "high", "xhigh", "max"):
        assert _normalized_authoring_reasoning_effort("gpt-6-luna", level) == level
    assert _normalized_authoring_reasoning_effort("gpt-6-luna", "minimal") == "none"


def test_unknown_model_is_left_alone() -> None:
    assert _normalized_authoring_reasoning_effort("gpt-5.4", "minimal") == "minimal"
