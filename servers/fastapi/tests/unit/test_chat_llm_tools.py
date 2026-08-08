import pytest
from llmai.shared import Tool, WebSearchTool  # type: ignore[import-not-found]

from enums.llm_provider import LLMProvider
from services.chat.llm_tools import build_chat_llm_tools


def _sample_function_tools() -> list[Tool]:
    return [
        Tool(
            name="getSlideAtIndex",
            description="Read a slide",
            input_schema={"type": "object", "properties": {}},
        )
    ]


@pytest.mark.parametrize(
    "provider",
    [LLMProvider.GOOGLE, LLMProvider.VERTEX],
)
def test_build_chat_llm_tools_omits_web_search_for_gemini_providers(monkeypatch, provider):
    monkeypatch.setenv("LLM", provider.value)
    tools = build_chat_llm_tools(_sample_function_tools())

    assert len(tools) == 1
    assert isinstance(tools[0], Tool)
    assert not any(isinstance(tool, WebSearchTool) for tool in tools)


@pytest.mark.parametrize(
    "provider",
    [LLMProvider.OPENAI, LLMProvider.ANTHROPIC, LLMProvider.CUSTOM],
)
def test_build_chat_llm_tools_includes_web_search_for_other_providers(monkeypatch, provider):
    monkeypatch.setenv("LLM", provider.value)
    tools = build_chat_llm_tools(_sample_function_tools())

    assert len(tools) == 2
    assert isinstance(tools[-1], WebSearchTool)
