from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from models.decomposed_file_info import DecomposedFileInfo


DecomposeJobStatus = Literal[
    "queued",
    "running",
    "cancelling",
    "succeeded",
    "failed",
    "cancelled",
]


class DecomposeJobProgress(BaseModel):
    completed_files: int = Field(ge=0)
    total_files: int = Field(ge=1)
    current_file: Optional[str] = None


class DecomposeJobError(BaseModel):
    code: str
    message: str
    filename: Optional[str] = None
    retryable: bool = False


class DecomposeJobSnapshot(BaseModel):
    job_id: UUID
    status: DecomposeJobStatus
    progress: DecomposeJobProgress
    result: Optional[list[DecomposedFileInfo]] = None
    error: Optional[DecomposeJobError] = None
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None
