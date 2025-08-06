from typing import Any, Callable, Coroutine
from pydantic import BaseModel, Field


class LLMTool(BaseModel):
    pass


class LLMDynamicTool(LLMTool):
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Coroutine[Any, Any, str]]


class SearchWebTool(LLMTool):
    query: str = Field(description="The query to search the web for")


class GetCurrentDatetimeTool(LLMTool):
    pass
