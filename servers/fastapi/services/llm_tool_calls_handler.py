import asyncio
from datetime import datetime
from typing import Any, Callable, Coroutine, List
from fastapi import HTTPException
from openai.types.chat.chat_completion_message import ChatCompletionMessageToolCall
from enums.llm_call_type import LLMCallType
from enums.llm_provider import LLMProvider
from models.llm_message import LLMMessage, LLMToolCallMessage
from models.llm_tool_call import LLMToolCall
from models.llm_tools import (
    GetCurrentDatetimeTool,
    LLMDynamicTool,
    LLMTool,
    SearchWebTool,
)


class LLMToolCallsHandler:
    def __init__(self, client):
        from services.llm_client import LLMClient

        self.client: LLMClient = client

        self.tools_map: dict[str, Callable[..., Coroutine[Any, Any, str]]] = {
            "SearchWebTool": self.search_web_tool_call_handler,
            "GetCurrentDatetimeTool": self.get_current_datetime_tool_call_handler,
        }
        self.dynamic_tools: List[LLMDynamicTool] = []

    def get_tool_handler(
        self, tool_name: str
    ) -> Callable[..., Coroutine[Any, Any, str]]:
        handler = self.tools_map.get(tool_name)
        if not handler:
            dynamic_tools = list(
                filter(lambda tool: tool.name == tool_name, self.dynamic_tools)
            )
            if dynamic_tools:
                return dynamic_tools[0].handler
        raise HTTPException(status_code=500, detail=f"Tool {tool_name} not found")

    def parse_tool(self, tool: type[LLMTool] | LLMDynamicTool, strict: bool = False):
        if isinstance(tool, LLMDynamicTool):
            self.dynamic_tools.append(tool)

        match self.client.llm_provider:
            case LLMProvider.OPENAI:
                return self.parse_tool_openai(tool, strict)
            case LLMProvider.ANTHROPIC:
                return self.parse_tool_anthropic(tool)
            case LLMProvider.GOOGLE:
                return self.parse_tool_google(tool)
            case _:
                raise ValueError(
                    f"LLM provider must be either openai, anthropic, or google"
                )

    def parse_tool_openai(
        self, tool: type[LLMTool] | LLMDynamicTool, strict: bool = False
    ):
        if isinstance(tool, LLMDynamicTool):
            name = tool.name
            description = tool.description
            parameters = tool.parameters
        else:
            name = tool.__class__.__name__
            description = tool.__class__.__doc__ or ""
            parameters = tool.model_dump(mode="json")

        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "strict": strict,
                "parameters": parameters,
            },
        }

    def parse_tool_anthropic(self, tool: type[LLMTool] | LLMDynamicTool):
        pass

    def parse_tool_google(self, tool: type[LLMTool] | LLMDynamicTool):
        pass

    async def handle_tool_calls_openai(
        self,
        tool_calls: List[LLMToolCall],
    ) -> List[LLMToolCallMessage]:
        async_tool_calls_tasks = []
        for tool_call in tool_calls:
            tool_name = tool_call.name
            tool_handler = self.get_tool_handler(tool_name)
            async_tool_calls_tasks.append(tool_handler(tool_call.arguments))

        tool_call_results: List[str] = await asyncio.gather(*async_tool_calls_tasks)
        return [
            LLMToolCallMessage(
                role="tool",
                content=result,
                tool_call_id=tool_call.id,
            )
            for tool_call, result in zip(tool_calls, tool_call_results)
        ]

    # ? Tool call handlers
    async def search_web_tool_call_handler(self, tool_call: dict) -> str:
        pass

    async def get_current_datetime_tool_call_handler(self, tool_call: dict) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
