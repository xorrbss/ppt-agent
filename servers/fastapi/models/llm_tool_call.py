from typing import Literal, Optional
from pydantic import BaseModel


class LLMToolCall(BaseModel):
    pass


class OpenAIToolCallFunction(BaseModel):
    name: str
    arguments: str


class OpenAIToolCall(LLMToolCall):
    id: str
    type: Literal["function"] = "function"
    function: OpenAIToolCallFunction


class GoogleToolCall(LLMToolCall):
    name: str
    arguments: Optional[dict] = None
