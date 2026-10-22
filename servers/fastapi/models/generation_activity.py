"""Wire shape of ``GET /workspace/generation-activity``.

The owner's in-flight ``presentation.generate`` operations, so a client can
show that a deck is still generating (or queued) before the user starts
another one. Timestamps are ISO-8601 strings with an explicit UTC offset.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class GenerationActivityPages(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authored: int = Field(default=0, ge=0)
    rendered: int = Field(default=0, ge=0)
    persisted: int = Field(default=0, ge=0)
    # ``None`` while the job has not planned its page count yet.
    total: Optional[int] = Field(default=None, ge=1)


class GenerationActivityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    presentation_id: str
    state: Literal["running", "cancelling", "queued"]
    presentation_title: Optional[str] = None
    source_file_name: Optional[str] = None
    pages: GenerationActivityPages = Field(default_factory=GenerationActivityPages)
    stage: Optional[str] = None
    queue_position: Optional[int] = Field(default=None, ge=1)
    capacity_waiting: bool = False
    created_at: str
    started_at: Optional[str] = None
    last_progress_at: Optional[str] = None
    # The preconditions a ``cancel`` action on this operation must carry.
    operation_version: int = Field(ge=0)
    presentation_revision: int = Field(ge=0)


class GenerationActivityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_time: str
    items: list[GenerationActivityItem] = Field(default_factory=list)
