"""Read-only history of detached one-page remake previews.

Kept separate from ``authored_page_remake_service`` so the mutating preview and
publication path stays focused: this module never writes.  It reports what the
chat timeline should show after a reload, and which of those previews may still
be applied.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.authored_page_remake_service import (
    PREVIEW_KIND,
    authored_page_source_digest,
)

# Task rows carry the presentation only inside their JSON payload, so this scans
# a bounded window of the owner's most recent tasks instead of the whole table.
_SCAN_LIMIT = 300
DEFAULT_HISTORY_LIMIT = 20


async def list_authored_page_remake_previews(
    session: AsyncSession,
    presentation_id: uuid.UUID,
    *,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> list[dict[str, Any]]:
    """Return recent remake previews for one presentation, newest first.

    ``candidate_html`` is deliberately omitted: the timeline renders the stored
    image, and the publication path reads the HTML from the task itself.
    """

    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is None:
        return []

    slide_rows = await session.execute(
        select(SlideModel)
        .where(SlideModel.presentation == presentation_id)
        .order_by(SlideModel.index)
    )
    digests = [
        authored_page_source_digest(slide) for slide in slide_rows.scalars().all()
    ]

    task_rows = await session.execute(
        select(AsyncPresentationGenerationTaskModel)
        .where(
            AsyncPresentationGenerationTaskModel.owner_id == presentation.owner_id
        )
        .order_by(AsyncPresentationGenerationTaskModel.updated_at.desc())
        .limit(_SCAN_LIMIT)
    )

    history: list[dict[str, Any]] = []
    for task in task_rows.scalars().all():
        data = task.data if isinstance(task.data, dict) else None
        if data is None or data.get("kind") != PREVIEW_KIND:
            continue
        if data.get("presentation_id") != str(presentation_id):
            continue
        slide_index = data.get("slide_index")
        if isinstance(slide_index, bool) or not isinstance(slide_index, int):
            continue

        phase = data.get("phase")
        validation = data.get("validation")
        # A preview may only be applied while the page it was built from is
        # untouched.  Publication re-checks the same digest, so anything reported
        # here as restorable is exactly what publication would still accept.
        restorable = (
            task.status == "completed"
            and phase == "preview_ready"
            and 0 <= slide_index < len(digests)
            and data.get("source_digest") == digests[slide_index]
        )
        history.append(
            {
                "preview_task_id": task.id,
                "slide_index": slide_index,
                "status": task.status,
                "phase": phase,
                "candidate_image": data.get("candidate_image"),
                "candidate_issues": data.get("candidate_issues") or [],
                "validation": validation if isinstance(validation, dict) else None,
                "restorable": restorable,
                "error_code": (task.error or {}).get("detail")
                if isinstance(task.error, dict)
                else None,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
            }
        )
        if len(history) >= limit:
            break
    return history
