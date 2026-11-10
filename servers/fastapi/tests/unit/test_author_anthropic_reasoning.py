"""Anthropic authoring calls carry the authored effort without enabling thinking."""

from __future__ import annotations

import pytest
from llmai.shared.reasoning import ReasoningConfig

from enums.llm_provider import LLMProvider
from services.generation_core.authoring import author_call_runtime


def _provider(monkeypatch, provider: LLMProvider) -> None:
    monkeypatch.setattr(author_call_runtime, "get_llm_provider", lambda *a, **k: provider)


@pytest.mark.parametrize(("effort", "expected"), [("low", "low"), ("medium", "medium"), ("minimal", "low"), ("xhigh", "max")])
def test_anthropic_receives_the_effort_without_enabling_thinking(monkeypatch, effort, expected):
    # Enabling adaptive thinking made claude-sonnet-4-6 (no thinking by default)
    # spend 16k tokens thinking with no text; output_config.effort alone bounds
    # claude-sonnet-5 (thinks by default) and leaves 4.x models non-thinking.
    _provider(monkeypatch, LLMProvider.ANTHROPIC)
    kwargs = author_call_runtime._apply_anthropic_authoring_reasoning({"model": "m"}, effort, model="claude-sonnet-5")
    assert kwargs["extra_body"] == {"output_config": {"effort": expected}}
    assert "reasoning" not in kwargs and "thinking" not in kwargs["extra_body"]


def test_none_effort_disables_anthropic_thinking(monkeypatch):
    _provider(monkeypatch, LLMProvider.ANTHROPIC)
    kwargs = author_call_runtime._apply_anthropic_authoring_reasoning({}, "none", model="claude-sonnet-5")
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_operator_disable_word_turns_the_override_off(monkeypatch):
    _provider(monkeypatch, LLMProvider.ANTHROPIC)
    monkeypatch.setenv("AUTHORED_REASONING_EFFORT", "off")
    monkeypatch.delenv("CODEX_REASONING_EFFORT", raising=False)
    assert author_call_runtime._apply_anthropic_authoring_reasoning({"model": "m"}, "medium") == {"model": "m"}


def test_existing_extra_body_is_preserved(monkeypatch):
    _provider(monkeypatch, LLMProvider.ANTHROPIC)
    kwargs = author_call_runtime._apply_anthropic_authoring_reasoning({"extra_body": {"k": 1}}, "low")
    assert kwargs["extra_body"] == {"k": 1, "output_config": {"effort": "low"}}


@pytest.mark.parametrize("provider", [LLMProvider.CODEX, LLMProvider.OPENAI, LLMProvider.GOOGLE])
def test_other_providers_are_left_unchanged(monkeypatch, provider):
    _provider(monkeypatch, provider)
    assert author_call_runtime._apply_anthropic_authoring_reasoning({"model": "m"}, "low") == {"model": "m"}


def test_an_unresolvable_provider_leaves_the_call_unchanged(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("no provider")

    monkeypatch.setattr(author_call_runtime, "get_llm_provider", unavailable)
    assert author_call_runtime._apply_anthropic_authoring_reasoning({"model": "m"}, "low") == {"model": "m"}


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
