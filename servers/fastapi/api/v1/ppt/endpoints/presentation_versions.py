import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from models.presentation_with_slides import PresentationWithSlides
from services import presentation_version_service as version_service
from services.database import get_async_session
from services.presentation_access_service import require_live_presentation
from services.workspace_legacy_write_gate import (
    require_legacy_presentation_write_path,
)

PRESENTATION_VERSION_ROUTER = APIRouter(
    prefix="/presentation", tags=["Presentation Versions"]
)


class VersionSummary(BaseModel):
    id: uuid.UUID
    created_at: datetime
    label: Optional[str] = None
    slide_count: int


@PRESENTATION_VERSION_ROUTER.get(
    "/{id}/versions", response_model=List[VersionSummary]
)
async def list_presentation_versions(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)
):
    presentation = await require_live_presentation(sql_session, id)

    versions = await version_service.list_versions(sql_session, id)
    return [
        VersionSummary(
            id=version.id,
            created_at=version.created_at,
            label=version.label,
            slide_count=len(version.slides or []),
        )
        for version in versions
    ]


@PRESENTATION_VERSION_ROUTER.post(
    "/{id}/versions/{version_id}/restore",
    response_model=PresentationWithSlides,
)
async def restore_presentation_version(
    id: uuid.UUID,
    version_id: uuid.UUID,
    sql_session: AsyncSession = Depends(get_async_session),
):
    require_legacy_presentation_write_path(id)
    presentation = await require_live_presentation(sql_session, id)

    restored_slides = await version_service.restore_version(
        sql_session, id, version_id
    )
    if restored_slides is None:
        raise HTTPException(status_code=404, detail="Version not found")

    await sql_session.commit()

    # fonts are omitted here; the editor re-fetches the presentation after a restore
    # (which resolves fonts) — this response is just the immediately-restored slides.
    return PresentationWithSlides(
        **presentation.model_dump(),
        slides=restored_slides,
        fonts=None,
    )
