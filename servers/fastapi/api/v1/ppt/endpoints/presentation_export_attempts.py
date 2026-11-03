"""Read-only export-attempt history used by the Next.js export history panel.

The begin/complete/fail routes for direct Hybrid/Fidelity attempts were removed:
exports now run only as ``export.pptx``/``export.pdf`` workspace operations, and
no client called them any more.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from services.database import get_async_session
from services.presentation_export_direct_attempt_history import (
    list_direct_hybrid_export_attempt_history,
)


PRESENTATION_EXPORT_ATTEMPT_ROUTER = APIRouter()


@PRESENTATION_EXPORT_ATTEMPT_ROUTER.get(
    "/{presentation_id}/export-attempts/hybrid/history"
)
async def hybrid_attempt_history(
    presentation_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
):
    return await list_direct_hybrid_export_attempt_history(
        session,
        presentation_id=presentation_id,
    )
