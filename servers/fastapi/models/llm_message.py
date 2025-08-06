from typing import List, Literal, Optional
from pydantic import BaseModel


class LLMMessage(BaseModel):
    pass


class LLMUserMessage(LLMMessage):
    role: Literal["user"]
    content: str


class LLMSystemMessage(LLMMessage):
    role: Literal["system"]
    content: str


class LLMToolCallMessage(LLMMessage):
    role: Literal["tool"]
    content: str
    tool_call_id: str


class LLMAssistantMessage(LLMMessage):
    role: Literal["assistant"]
    content: str | None = None
    tool_calls: Optional[List[dict]] = None
