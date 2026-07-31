from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel

from enums.async_task_status import AsyncTaskStatus


class AsyncTaskReadModel(BaseModel):
    """Read-only generic view of a durable presentation generation task."""

    id: str
    type: str
    status: AsyncTaskStatus
    message: str | None = None
    error: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    terminal: bool
    job_id: uuid.UUID
    presentation_id: uuid.UUID
    job_state: str
    source_status: str
