"""The advisory review asks every provider for its cheapest reasoning."""

from __future__ import annotations

from types import SimpleNamespace

from services.generation_core.quality import vision_advisory


def _provider(monkeypatch, name, *, codex=False):
    import utils.llm_provider as llm_provider

    monkeypatch.setattr(llm_provider, "is_codex_selected", lambda: codex)
    monkeypatch.setattr(llm_provider, "get_llm_provider", lambda: SimpleNamespace(value=name))


def test_anthropic_thinking_is_turned_down_so_it_cannot_eat_the_answer_budget(monkeypatch):
    _provider(monkeypatch, "anthropic")
    assert vision_advisory._advisory_extra_body() == {"thinking": {"type": "disabled"}}


def test_codex_keeps_its_reasoning_effort_and_others_are_untouched(monkeypatch):
    _provider(monkeypatch, "codex", codex=True)
    assert vision_advisory._advisory_extra_body() == {
        "reasoning": {"effort": vision_advisory.ADVISORY_REASONING_EFFORT}}
    _provider(monkeypatch, "openai")
    assert vision_advisory._advisory_extra_body() is None


def test_the_anthropic_client_maps_disabled_thinking_to_each_models_lowest_setting():
    from utils.anthropic_structured_output import RepositoryAnthropicClient

    client = RepositoryAnthropicClient.__new__(RepositoryAnthropicClient)
    body = client._thinking_extra_body("claude-sonnet-5-5", {"thinking": {"type": "disabled"}}, None)
    assert body["thinking"] == {"type": "between_tools"}
