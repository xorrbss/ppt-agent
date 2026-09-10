"""Runtime admission facade for presentation generation."""

from datetime import datetime
from typing import Any, Optional
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.database import sql_engine
from services.workspace_runtime_profile import (
    UnsupportedWorkspaceRuntimeError,
    WorkspaceRuntimeKind,
    require_supported_workspace_runtime,
    resolve_workspace_runtime_profile,
)


def _chat_first_generation_enabled() -> bool:
    try:
        require_supported_workspace_runtime(sql_engine.url)
    except UnsupportedWorkspaceRuntimeError as error:
        raise HTTPException(status_code=501, detail=error.code) from error
    return True


def _require_local_sync_generation(database_url_or_dialect: Any) -> None:
    profile = resolve_workspace_runtime_profile(database_url_or_dialect)
    if profile.kind == WorkspaceRuntimeKind.SQLITE_LOCAL:
        return
    raise HTTPException(
        status_code=410,
        detail={"reasonCode": "sync_generation_deprecated", "replacement": "/api/v2/presentations/generations"},
        headers={"Deprecation": "true", "Link": '</api/v2/presentations/generations>; rel="successor-version"'},
    )


def _require_local_legacy_async_generation(database_url_or_dialect: Any) -> None:
    try:
        require_supported_workspace_runtime(database_url_or_dialect)
    except UnsupportedWorkspaceRuntimeError as error:
        raise HTTPException(status_code=501, detail=error.code) from error


PRESENTATION_GENERATE_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation"])


class AsyncPresentationGenerationTaskResponse(BaseModel):
    id: str
    status: str
    message: Optional[str] = None
    error: Optional[dict] = None
    created_at: datetime
    updated_at: datetime
    data: Optional[dict] = None
    progress: Optional[dict] = None
    failure: Optional[dict] = None


class AsyncPresentationGenerationAdmissionResponse(AsyncPresentationGenerationTaskResponse):
    """Accepted durable generation plus its canonical workspace operation."""

    operation_id: Optional[uuid.UUID] = None
