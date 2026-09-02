"""Wire shapes for recording one authored-slide editor event."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.sql.authored_slide_editor_event import (
    AUTHORED_SLIDE_EDITOR_EVENT_TYPES,
)

AuthoredSlideEditorEventType = Literal[
    "slide_regenerated",
    "archetype_switched",
    "bulk_text_edit",
    "slide_deleted",
    "export_completed",
]

# Keep the wire vocabulary and the table's check constraint from drifting.
assert set(AUTHORED_SLIDE_EDITOR_EVENT_TYPES) == set(
    AuthoredSlideEditorEventType.__args__
)

MAX_ARCHETYPE_CHARS = 32


class AuthoredSlideEditorEventRequest(BaseModel):
    """One editor action, as the editor reports it.

    ``event_key`` is the caller's idempotency identity. The editor should send
    a value that is stable for one user action (a click id, or the slide
    revision it acted on) so a retried request records the action once.
    """

    model_config = ConfigDict(extra="forbid")

    event_type: AuthoredSlideEditorEventType
    slide_index: Optional[int] = Field(default=None, ge=0)
    archetype: Optional[str] = Field(default=None, max_length=MAX_ARCHETYPE_CHARS)
    previous_archetype: Optional[str] = Field(
        default=None, max_length=MAX_ARCHETYPE_CHARS
    )
    changed_characters: Optional[int] = Field(default=None, ge=0)
    event_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _scope_matches_the_event(self) -> "AuthoredSlideEditorEventRequest":
        if self.event_type != "export_completed" and self.slide_index is None:
            raise ValueError("a slide-scoped editor event requires slide_index")
        if self.event_type == "archetype_switched" and not (
            self.archetype and self.previous_archetype
        ):
            raise ValueError(
                "an archetype switch requires both the previous and new archetype"
            )
        return self


class AuthoredSlideEditorEventResponse(BaseModel):
    """What was recorded, and whether this request created it."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    presentation_id: uuid.UUID
    slide_index: Optional[int]
    event_type: AuthoredSlideEditorEventType
    archetype: Optional[str]
    previous_archetype: Optional[str]
    changed_characters: Optional[int]
    created_at: datetime
    deduplicated: bool = False


__all__ = [
    "AuthoredSlideEditorEventRequest",
    "AuthoredSlideEditorEventResponse",
    "AuthoredSlideEditorEventType",
]
