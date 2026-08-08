from llmai.shared import Tool, WebSearchTool  # type: ignore[import-not-found]

from enums.llm_provider import LLMProvider
from utils.llm_provider import get_llm_provider

# Gemini (Google AI + Vertex) does not allow Search and Function tools in one request.
_GEMINI_EXCLUSIVE_TOOL_PROVIDERS = frozenset(
    {
        LLMProvider.GOOGLE,
        LLMProvider.VERTEX,
    }
)


def build_chat_llm_tools(function_tools: list[Tool]) -> list[Tool | WebSearchTool]:
    """
    Chat needs slide-edit function tools on every provider. Hosted web search is
    appended only when the active provider can combine it with function tools.
    """
    tools: list[Tool | WebSearchTool] = list(function_tools)
    if get_llm_provider() not in _GEMINI_EXCLUSIVE_TOOL_PROVIDERS:
        tools.append(WebSearchTool())
    return tools
