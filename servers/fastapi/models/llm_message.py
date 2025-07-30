from typing import Literal
from pydantic import BaseModel


class LLMMessage(BaseModel):
    role: Literal["user", "system"]
    content: str
