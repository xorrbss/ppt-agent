"""Shared access checks for presentation tombstones.

Durable workspace deletion intentionally retains a minimal presentation row so
the delete operation and timeline remain replayable.  That row is not a live
deck and must never be exposed to, or mutated by, legacy presentation routes.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PresentationModel,
)


async def require_live_presentation(
    sql_session: AsyncSession,
    presentation_id: uuid.UUID,
) -> PresentationModel:
    """Return an owner-scoped live deck, hiding durable delete tombstones."""

    presentation = await sql_session.get(PresentationModel, presentation_id)
    if (
        presentation is None
        or presentation.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED
    ):
        raise HTTPException(status_code=404, detail="Presentation not found")
    return presentation
