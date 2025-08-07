from typing import List, Literal, Optional
from pydantic import BaseModel


class LLMMessage(BaseModel):
    pass


class LLMUserMessage(LLMMessage):
    role: Literal["user"] = "user"
    content: str


class LLMSystemMessage(LLMMessage):
    role: Literal["system"] = "system"
    content: str


class LLMToolCallMessage(LLMMessage):
    role: Literal["tool"] = "tool"
    id: str
    content: str
    type: str
    tool_call_id: str


class LLMAssistantMessage(LLMMessage):
    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: Optional[List[dict]] = None
