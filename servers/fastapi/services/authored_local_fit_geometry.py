"""The local-fit export geometry of a persisted presentation's canvas."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class LocalFitGeometry(NamedTuple):
    """Field names match the keys of a local-fit ``exportScaleManifest``."""

    width_pt: float
    height_pt: float
    viewport_width: int
    viewport_height: int


async def presentation_local_fit_geometry(
    session: AsyncSession, presentation_id: uuid.UUID
) -> LocalFitGeometry:
    """Raise when the presentation or its canvas cannot be resolved."""

    from models.sql.presentation import PresentationModel
    from services.authored_canvas import presentation_canvas_contract
    from services.document_canvas import DocumentCanvasMetadata
    from services.document_canvas_generation import export_size_points

    presentation = await session.scalar(
        select(PresentationModel).where(PresentationModel.id == presentation_id)
    )
    if presentation is None:
        raise ValueError("presentation_not_found")
    theme = presentation.theme
    canvas = presentation_canvas_contract(theme)
    physical_canvas = (
        DocumentCanvasMetadata.model_validate(theme["documentCanvas"])
        if isinstance(theme, Mapping) and "documentCanvas" in theme
        else None
    )
    width_pt, height_pt = export_size_points(canvas, physical_canvas)
    return LocalFitGeometry(width_pt, height_pt, int(canvas.width_px), int(canvas.height_px))
