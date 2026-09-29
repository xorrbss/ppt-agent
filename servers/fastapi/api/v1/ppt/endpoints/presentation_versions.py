import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
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
    label_key: Optional[str] = None
    label_params: Optional[dict] = None
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
            label_key=version.label_key,
            label_params=version.label_params,
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
    canvas_contracts: Optional[str] = Header(default=None, alias="X-Document-Canvas-Contracts"),
):
    require_legacy_presentation_write_path(id)
    presentation = await require_live_presentation(sql_session, id)

    from services.document_canvas import require_canvas_capability

    require_canvas_capability(presentation.theme, canvas_contracts)
    try:
        restored_slides = await version_service.restore_version(
            sql_session, id, version_id
        )
    except ValueError as error:
        if str(error).startswith("document_canvas"):
            raise HTTPException(status_code=409, detail=str(error)) from error
        raise
    if restored_slides is None:
        raise HTTPException(status_code=404, detail="Version not found")

    require_canvas_capability(presentation.theme, canvas_contracts)
    await sql_session.commit()

    # fonts are omitted here; the editor re-fetches the presentation after a restore
    # (which resolves fonts) — this response is just the immediately-restored slides.
    return PresentationWithSlides(
        **presentation.model_dump(exclude={"fonts"}),
        slides=restored_slides,
        fonts=None,
    )
