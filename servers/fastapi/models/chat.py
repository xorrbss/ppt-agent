
from pydantic import BaseModel, ConfigDict, Field


class ChatAttachment(BaseModel):
    """A file the user attached to a chat message, already extracted to text."""

    name: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=50000)

    model_config = ConfigDict(extra="forbid")
