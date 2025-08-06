from typing import Optional
from pydantic import BaseModel


class LLMToolCall(BaseModel):
    id: Optional[str] = None
    name: str
    arguments: Optional[str] = None
