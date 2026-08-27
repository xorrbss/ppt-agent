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

import hashlib
import hmac
import logging
import uuid
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from models.generate_presentation_request import GeneratePresentationRequest
from services.assets import AssetGateway
from services.source_images.models import SourceImageCandidate
from services.source_images.registry import (
    ManifestImageOccurrence,
    register_manifest_image_occurrences,
    register_source_images,
)
from services.source_images.selection import select_source_images
from utils.authored_image_slots import source_slot_id_for_asset
from utils.get_env import is_authored_source_images_enabled
from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    normalize_reference_mode,
)

LOGGER = logging.getLogger(__name__)
_DIGEST_CHUNK_BYTES = 1024 * 1024
_SOURCE_IMAGE_INTENT_TERMS = (
    "<img", " img ", "image", "photo", "picture", "screenshot",
    "이미지", "사진", "스크린샷", ".png", ".jpg", ".jpeg", ".webp",
)


def _candidate_bytes_match_digest(candidate: SourceImageCandidate) -> bool:
    """Reopen the stored candidate and verify the bytes before offering it."""

    expected = candidate.digest.removeprefix("sha256:").lower()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        return False
    digest = hashlib.sha256()
    try:
        with candidate.path.open("rb") as stream:
            while chunk := stream.read(_DIGEST_CHUNK_BYTES):
                digest.update(chunk)
    except OSError:
        return False
    return hmac.compare_digest(digest.hexdigest(), expected)


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


async def _strict_manifest_source_image_candidates(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    session: AsyncSession,
) -> list[SourceImageCandidate]:
    """Materialize every page-locked manifest image occurrence, or fail."""

    manifest = request._source_content_manifest
    outline = request._source_document_outline
    if manifest is None or outline is None:
        raise RuntimeError("source_content_manifest_invalid")

    from services.source_content.manifest_store import (
        load_source_content_manifest_assets,
    )

    try:
        owner_id = uuid.UUID(str(manifest.owner_id))
    except (AttributeError, TypeError, ValueError) as error:
        raise RuntimeError("source_content_owner_required") from error
    sealed_assets = await load_source_content_manifest_assets(
        session,
        owner_id=owner_id,
        manifest_id=manifest.manifest_id,
    )
    units = {
        unit.source_unit_id: unit
        for unit in manifest.units
    }
    occurrences: list[ManifestImageOccurrence] = []
    for outline_slide in outline.slides:
        unit = units.get(outline_slide.source_unit_id)
        if unit is None or unit.source_index != outline_slide.source_index:
            raise RuntimeError("source_content_image_occurrence_mismatch")
        blocks = {block.source_block_id: block for block in unit.blocks}
        text_values = [
            str(block.payload.get("text") or "").strip()
            for block in unit.blocks
            if block.kind == "text" and str(block.payload.get("text") or "").strip()
        ]
        title_values = [
            str(block.payload.get("text") or "").strip()
            for block in unit.blocks
            if block.source_block_id in outline_slide.title_block_ids
            and str(block.payload.get("text") or "").strip()
        ]
        for occurrence_id in outline_slide.image_occurrence_ids:
            block = blocks.get(occurrence_id)
            if block is None or block.kind != "image":
                raise RuntimeError("source_content_image_occurrence_mismatch")
            manifest_asset_id = str(
                block.payload.get("assetId")
                or block.payload.get("asset_id")
                or ""
            )
            sealed = sealed_assets.get(manifest_asset_id)
            if not manifest_asset_id or sealed is None:
                raise RuntimeError("source_content_image_occurrence_mismatch")
            declared_digest = str(
                block.payload.get("contentDigest")
                or block.payload.get("content_digest")
                or ""
            )
            if declared_digest != str(sealed.get("digest") or ""):
                raise RuntimeError("source_content_image_digest_mismatch")
            occurrences.append(
                ManifestImageOccurrence(
                    occurrence_id=occurrence_id,
                    payload=bytes(sealed.get("content") or b""),
                    mime_type=str(sealed.get("media_type") or ""),
                    content_digest=declared_digest,
                    source_slide_index=outline_slide.source_index,
                    source_heading=(title_values[0] if title_values else None),
                    source_text="\n".join(text_values)[:600] or None,
                )
            )

    expected_count = int(manifest.counts.image_occurrences)
    if len(occurrences) != expected_count:
        raise RuntimeError("source_content_image_occurrence_mismatch")
    candidates = await register_manifest_image_occurrences(
        session,
        presentation_id=presentation_id,
        occurrences=occurrences,
    )
    if (
        len(candidates) != expected_count
        or len({candidate.asset_id for candidate in candidates}) != expected_count
        or any(not _candidate_bytes_match_digest(candidate) for candidate in candidates)
    ):
        raise RuntimeError("source_content_image_occurrence_mismatch")
    await session.commit()
    return candidates


async def collect_source_image_candidates(
    request: GeneratePresentationRequest,
    style,
    presentation_id: uuid.UUID,
    session: AsyncSession,
) -> list[SourceImageCandidate]:
    """Register the pictures this deck may reuse, or decide it may reuse none.

    Two cases never gain one. The feature is opt-in, so an unset gate has to
    leave generation exactly as it was. Exact reconstruction reproduces a
    source slide-for-slide, where adding an image the source does not have is
    the one thing that mode forbids.

    A company standard owns furniture only. It must not suppress
    authored-template body images, so master-owned authored-vNext requests use
    this same verified registry and deterministic selection path.

    Never raises: a deck is worth more than its source images, so an unreadable
    upload, a storage failure, or a filtered-out candidate set all degrade to
    ordinary illustration rather than losing the generation.
    """
    if request._source_content_authored_reflow:
        # Source-content admission already selected and sealed the image set.
        # It is a preservation contract, not the optional ordinary-authored
        # image-reuse feature, so feature gates and best-effort degradation do
        # not apply.
        return await _strict_manifest_source_image_candidates(
            request,
            presentation_id,
            session,
        )
    if not is_authored_source_images_enabled():
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
        if any(not _candidate_bytes_match_digest(candidate) for candidate in candidates):
            raise RuntimeError("authored_source_image_digest_mismatch")
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
    *,
    source_slide_locked: bool = False,
) -> list[dict[str, SourceImageCandidate]] | None:
    """Decide up front, in slide order, which pictures each slide is offered.

    The pipeline authors slides concurrently, so a selection made inside a
    worker would depend on completion order and the same deck would not
    reproduce. Deciding here gives every slide deterministic opaque source-slot
    ids. Candidates may be offered to multiple slides and may fill multiple
    slots on one slide.

    ``None`` -- not an empty list -- means source images are not in play for
    this deck, which is the value the substitution step reads as
    "``data-asset-id`` stays inert". Ranking is pure local scoring, but it still
    must never be the reason a deck dies, so a failure returns that same
    ``None`` instead of propagating.
    """
    if not candidates:
        return None
    eligible = [
        candidate for candidate in candidates if _candidate_bytes_match_digest(candidate)
    ]
    if not eligible:
        LOGGER.warning(
            "[authored] source image bytes missing or changed; continuing without source images"
        )
        return None
    per_slide: list[dict[str, SourceImageCandidate]] = [
        {} for _content in contents
    ]
    try:
        for index, content in enumerate(contents):
            content = contents[index]
            if source_slide_locked:
                chosen = [
                    candidate
                    for candidate in eligible
                    if candidate.source_slide_index == index
                ]
            else:
                heading, _, body = (content or "").partition("\n")
                chosen = select_source_images(
                    eligible,
                    slide_heading=heading.strip().lstrip("#*- ").strip(),
                    slide_content=body,
                    slide_index=index,
                    limit=None,
                )
            offered = {
                source_slot_id_for_asset(candidate.asset_id): candidate
                for candidate in chosen
            }
            per_slide[index] = offered
    except Exception:
        LOGGER.warning(
            "[authored] source image selection failed; "
            "continuing without source images",
            exc_info=True,
        )
        return None
    return per_slide
