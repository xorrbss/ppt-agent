"""The source-image seam of ordinary authored generation.

Two decisions live here, one per stage of the authored flow: which of a
request's uploads become reusable candidates for the deck, and which of those
candidates each slide is offered. Everything else -- extraction, filtering,
persistence, ranking, substitution -- belongs to ``services.source_images`` and
``utils.authored_illustrations``; this module only wires them into the
generation entry and the per-slide pipeline, and it is the single place that
reads the feature gate.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from models.generate_presentation_request import GeneratePresentationRequest
from services.assets import AssetGateway
from services.source_images.models import SourceImageCandidate
from services.source_images.registry import register_source_images
from services.source_images.selection import select_source_images
from utils.get_env import is_authored_source_images_enabled
from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    normalize_reference_mode,
)

LOGGER = logging.getLogger(__name__)


async def _uploaded_source_paths(
    request: GeneratePresentationRequest,
    session: AsyncSession,
) -> list[Path]:
    """Server-local paths of everything the caller uploaded for this deck.

    Two upload shapes reach generation and both are already resolved by the time
    authoring starts, so nothing here opens a new upload channel. Internal and
    resume flows still carry raw server paths in the compatibility ``files``
    field, while the public endpoint has canonicalized its uploads into
    immutable reference snapshots whose asset bindings are the only remaining
    handle on the bytes; resolving a binding replays the same admission the
    document extractor performs.
    """
    paths = [Path(path) for path in (request.files or [])]
    gateway = AssetGateway()
    for snapshot in request._reference_snapshots:
        for binding in snapshot.asset_bindings:
            _confirmed, private_path = await gateway.resolve_for_processing(
                session,
                asset_id=binding.asset_id,
                kind=binding.kind,
                conversation_id=snapshot.conversation_id,
            )
            paths.append(private_path)
    return paths


async def collect_source_image_candidates(
    request: GeneratePresentationRequest,
    style,
    presentation_id: uuid.UUID,
    session: AsyncSession,
) -> list[SourceImageCandidate]:
    """Register the pictures this deck may reuse, or decide it may reuse none.

    Three decks never gain one. The feature is opt-in, so an unset gate has to
    leave generation exactly as it was. A master-owned deck takes its pictures
    from company-standard furniture. And exact reconstruction reproduces a
    source slide-for-slide, where adding an image the source does not have is
    the one thing that mode forbids.

    Never raises: a deck is worth more than its source images, so an unreadable
    upload, a storage failure, or a filtered-out candidate set all degrade to
    ordinary illustration rather than losing the generation.
    """
    if not is_authored_source_images_enabled():
        return []
    if request._authored_master_resolution is not None:
        return []
    if normalize_reference_mode(
        getattr(style, "reference_mode", STYLE_REFERENCE)
    ) == EXACT_RECONSTRUCTION:
        return []
    try:
        upload_paths = await _uploaded_source_paths(request, session)
        if not upload_paths:
            return []
        candidates = await register_source_images(
            session,
            presentation_id=presentation_id,
            upload_paths=upload_paths,
        )
        # The registry only flushes; the caller owns the transaction. Commit it
        # here so the rows are durable before the long authoring run instead of
        # holding a write open across it.
        await session.commit()
        return candidates
    except Exception:
        LOGGER.warning(
            "[authored] source image registration failed for %s; "
            "continuing without source images",
            presentation_id,
            exc_info=True,
        )
        try:
            await session.rollback()
        except Exception:
            LOGGER.debug("[authored] source image rollback failed", exc_info=True)
        return []


def plan_slide_source_images(
    contents: Sequence[str],
    candidates: Sequence[SourceImageCandidate],
) -> list[dict[str, SourceImageCandidate]] | None:
    """Decide up front, in slide order, which pictures each slide is offered.

    The pipeline authors slides concurrently, so a selection made inside a
    worker would depend on completion order and the same deck would not
    reproduce. Deciding here also lets a candidate be spent: a slide places at
    most one image, so leaving an offered picture in the pool would only let a
    later slide repeat it while starving that slide of a picture of its own.

    ``None`` -- not an empty list -- means source images are not in play for
    this deck, which is the value the substitution step reads as
    "``data-asset-id`` stays inert". Ranking is pure local scoring, but it still
    must never be the reason a deck dies, so a failure returns that same
    ``None`` instead of propagating.
    """
    if not candidates:
        return None
    remaining = list(candidates)
    per_slide: list[dict[str, SourceImageCandidate]] = []
    try:
        for index, content in enumerate(contents):
            heading, _, body = (content or "").partition("\n")
            chosen = select_source_images(
                remaining,
                slide_heading=heading.strip().lstrip("#*- ").strip(),
                slide_content=body,
                slide_index=index,
                # One slot per slide, so a second offer could only be withheld
                # from a later slide that would have been able to show it.
                limit=1,
            )
            offered = {candidate.asset_id: candidate for candidate in chosen}
            per_slide.append(offered)
            if offered:
                remaining = [
                    candidate
                    for candidate in remaining
                    if candidate.asset_id not in offered
                ]
    except Exception:
        LOGGER.warning(
            "[authored] source image selection failed; "
            "continuing without source images",
            exc_info=True,
        )
        return None
    return per_slide
