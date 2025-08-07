from typing import Literal
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
