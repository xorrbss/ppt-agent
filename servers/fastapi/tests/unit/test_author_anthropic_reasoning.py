"""Anthropic authoring calls carry an explicit, bounded thinking effort."""

from __future__ import annotations

import pytest
from llmai.shared.reasoning import ReasoningConfig

from enums.llm_provider import LLMProvider
from services.generation_core.authoring import author_slide


def _provider(monkeypatch, provider: LLMProvider) -> None:
    monkeypatch.setattr(author_slide, "get_llm_provider", lambda *a, **k: provider)


def test_anthropic_receives_the_authored_effort_as_reasoning(monkeypatch):
    # claude-sonnet-5 thinks by default when thinking is omitted; a live svg run
    # spent whole 16k author budgets on it and returned empty pages.
    _provider(monkeypatch, LLMProvider.ANTHROPIC)
    kwargs = author_slide._apply_anthropic_authoring_reasoning({"model": "m"}, "low", model="claude-sonnet-5")
    assert isinstance(kwargs["reasoning"], ReasoningConfig)
    assert kwargs["reasoning"].effort.value == "low"
    assert "extra_body" not in kwargs


def test_none_effort_disables_anthropic_thinking(monkeypatch):
    _provider(monkeypatch, LLMProvider.ANTHROPIC)
    kwargs = author_slide._apply_anthropic_authoring_reasoning({}, "none", model="claude-sonnet-5")
    assert kwargs["reasoning"].enabled is False


@pytest.mark.parametrize("provider", [LLMProvider.CODEX, LLMProvider.OPENAI, LLMProvider.GOOGLE])
def test_other_providers_are_left_unchanged(monkeypatch, provider):
    _provider(monkeypatch, provider)
    assert author_slide._apply_anthropic_authoring_reasoning({"model": "m"}, "low") == {"model": "m"}


def test_an_unresolvable_provider_leaves_the_call_unchanged(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("no provider")

    monkeypatch.setattr(author_slide, "get_llm_provider", unavailable)
    assert author_slide._apply_anthropic_authoring_reasoning({"model": "m"}, "low") == {"model": "m"}


def test_svg_input_receipt_serialises_the_reasoning_config(monkeypatch):
    from services.generation_core.authoring import author_slide_svg

    monkeypatch.setattr(author_slide_svg, "read_authoring_profile", lambda _snapshot: {"profile": "test"})
    receipt = author_slide_svg.svg_author_input_receipt(
        {}, [], provider="anthropic", model="m", budget=1,
        kwargs={"model": "m", "reasoning": ReasoningConfig(effort="low")},
    )
    other = author_slide_svg.svg_author_input_receipt(
        {}, [], provider="anthropic", model="m", budget=1,
        kwargs={"model": "m", "reasoning": ReasoningConfig(effort="medium")},
    )
    assert receipt["author_prompt_digest"] != other["author_prompt_digest"]
