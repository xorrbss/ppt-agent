"""Durable server-side version history for a presentation's slides.

The editor autosave (`PATCH /presentation/update`) snapshots the deck's slides
into `presentation_versions`, throttled so rapid 1s autosaves collapse into
time-spaced restore points and capped to the most recent N. Restoring replaces
the live slides with a snapshot, after checkpointing the current state so a
restore is itself undoable. This makes the previously ephemeral client-side undo
buffer durable across reloads.

Kept out of the presentation endpoint (already a god-file) so the storage policy
lives in one place.
"""
from copy import deepcopy
import uuid
from datetime import timezone
from typing import List, Optional

from sqlalchemy import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from models.sql.presentation_version import PresentationVersionModel
from models.sql.slide import SlideModel
from templates.v2.persistence import canonicalize_slide_dump
from utils.datetime_utils import get_current_utc_datetime

# Don't snapshot more often than this — rapid autosaves collapse to one point.
MIN_SNAPSHOT_INTERVAL_SECONDS = 60
# Keep only the most recent N versions per presentation.
MAX_VERSIONS_PER_PRESENTATION = 20


async def _latest_version(
    session: AsyncSession, presentation_id: uuid.UUID
) -> Optional[PresentationVersionModel]:
    result = await session.scalars(
        select(PresentationVersionModel)
        .where(PresentationVersionModel.presentation_id == presentation_id)
        .order_by(PresentationVersionModel.created_at.desc())
        .limit(1)
    )
    return result.first()


def _seconds_since(created) -> float:
    """Age of a stored timestamp in seconds, tolerant of SQLite returning a naive
    datetime for a timezone-aware column."""
    now = get_current_utc_datetime()
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created).total_seconds()


async def _prune(session: AsyncSession, presentation_id: uuid.UUID) -> None:
    stale_ids = list(
        await session.scalars(
            select(PresentationVersionModel.id)
            .where(PresentationVersionModel.presentation_id == presentation_id)
            .order_by(PresentationVersionModel.created_at.desc())
            .offset(MAX_VERSIONS_PER_PRESENTATION)
        )
    )
    if stale_ids:
        await session.execute(
            delete(PresentationVersionModel).where(
                PresentationVersionModel.id.in_(stale_ids)
            )
        )


async def snapshot_slides(
    session: AsyncSession,
    presentation_id: uuid.UUID,
    slides_dumps: List[dict],
    *,
    label: Optional[str] = None,
    force: bool = False,
) -> Optional[PresentationVersionModel]:
    """Append a version snapshot if one is due (or forced). Returns the created
    version, or None when skipped (empty slides, or throttled)."""
    if not slides_dumps:
        return None

    canonical_slides = [canonicalize_slide_dump(dump) for dump in slides_dumps]

    if not force:
        latest = await _latest_version(session, presentation_id)
        if latest is not None and _seconds_since(latest.created_at) < (
            MIN_SNAPSHOT_INTERVAL_SECONDS
        ):
            return None

    version = PresentationVersionModel(
        presentation_id=presentation_id,
        slides=canonical_slides,
        label=label,
    )
    session.add(version)
    # Autoflush before the prune SELECT includes the row just added.
    await _prune(session, presentation_id)
    return version


async def list_versions(
    session: AsyncSession, presentation_id: uuid.UUID
) -> List[PresentationVersionModel]:
    result = await session.scalars(
        select(PresentationVersionModel)
        .where(PresentationVersionModel.presentation_id == presentation_id)
        .order_by(PresentationVersionModel.created_at.desc())
    )
    return list(result)


def _slide_from_dump(presentation_id: uuid.UUID, dump: dict) -> SlideModel:
    canonical = canonicalize_slide_dump(dump)
    return SlideModel(
        id=uuid.uuid4(),
        presentation=presentation_id,
        layout_group=canonical.get("layout_group"),
        layout=canonical.get("layout"),
        index=canonical.get("index"),
        content=deepcopy(canonical.get("content") or {}),
        html_content=canonical.get("html_content"),
        ui=deepcopy(canonical.get("ui")),
        speaker_note=canonical.get("speaker_note"),
        properties=deepcopy(canonical.get("properties")),
    )


async def restore_version(
    session: AsyncSession, presentation_id: uuid.UUID, version_id: uuid.UUID
) -> Optional[List[SlideModel]]:
    """Replace the deck's live slides with a snapshot. Returns the restored slides,
    or None if the version does not belong to this presentation. The caller commits."""
    version = await session.get(PresentationVersionModel, version_id)
    if version is None or version.presentation_id != presentation_id:
        return None

    # Validate the complete target before checkpointing or deleting live state.
    restored = [
        _slide_from_dump(presentation_id, dump) for dump in (version.slides or [])
    ]

    # Checkpoint the current slides first, so the restore is itself undoable.
    current = list(
        await session.scalars(
            select(SlideModel)
            .where(SlideModel.presentation == presentation_id)
            .order_by(SlideModel.index)
        )
    )
    if current:
        await snapshot_slides(
            session,
            presentation_id,
            [slide.model_dump(mode="json") for slide in current],
            label="복원 전 자동 저장",
            force=True,
        )

    await session.execute(
        delete(SlideModel).where(SlideModel.presentation == presentation_id)
    )
    session.add_all(restored)
    return restored
