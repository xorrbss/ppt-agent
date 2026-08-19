"""Turn a deck's uploads into its persisted source-image candidate set.

Ordinary authored generation may place a picture the user already owns instead
of an invented illustration. This module is the single place that decides which
uploaded pictures qualify: it collects them (uploaded directly, or extracted
from an uploaded PPTX), applies the content filters, and persists the survivors
as uploaded ``ImageAsset`` rows so later stages can address them by a stable id.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.image_asset import ImageAsset
from services.source_images.extract import extract_pptx_images
from services.source_images.models import (
    FULL_SLIDE_COVERAGE_THRESHOLD,
    FURNITURE_REPEAT_THRESHOLD,
    MIN_SOURCE_IMAGE_EDGE_PX,
    SUPPORTED_SOURCE_IMAGE_MIME_TYPES,
    SourceImageCandidate,
)
from utils.path_helpers import get_writable_path

#: Uploads we open as decks rather than as pictures. ``.ppt`` is listed because
#: the upload surface accepts it; the extractor decides whether it can read one.
_SOURCE_DECK_SUFFIXES = frozenset({".pptx", ".pptm", ".ppt"})

#: Storage sits beside the other writable caches, one subdirectory per deck, so
#: a deleted presentation's pictures can be swept as a unit.
_STORAGE_ROOT = "source_images"

_MIME_TO_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}

_DIGEST_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class _RawImage:
    """One picture before filtering, from either kind of upload.

    The two sources hand us the bytes differently -- a direct upload is already
    a file we can copy, an extracted picture only exists in memory -- so the
    payload is carried either way and resolved once, at persist time.
    """

    mime_type: str
    width: int
    height: int
    digest: str
    source_path: Optional[Path] = None
    payload: Optional[bytes] = None
    source_slide_index: Optional[int] = None
    #: Share of its slide the picture was placed over, when the deck said.
    slide_coverage: Optional[float] = None
    source_heading: Optional[str] = None


async def register_source_images(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    upload_paths: Sequence[Path],
) -> list[SourceImageCandidate]:
    """Collect, filter and persist the pictures a deck may reuse.

    The returned candidates are in a deterministic order (upload order, then the
    order the extractor reported inside each deck) and carry the persisted
    ``ImageAsset`` id, which is the only identifier later stages resolve.
    """

    collected = await _collect_raw_images(upload_paths)
    kept = _apply_source_image_filters(collected)
    if not kept:
        return []

    storage_dir = Path(
        await asyncio.to_thread(
            get_writable_path, f"{_STORAGE_ROOT}/{presentation_id}"
        )
    )
    registered = [
        await _persist_raw_image(
            session,
            storage_dir=storage_dir,
            presentation_id=presentation_id,
            raw=raw,
        )
        for raw in kept
    ]
    # One flush for the whole set: the caller owns the surrounding transaction.
    await session.flush()
    return registered


async def _collect_raw_images(upload_paths: Sequence[Path]) -> list[_RawImage]:
    """Expand every upload into the pictures it contributes, in upload order."""

    collected: list[_RawImage] = []
    for upload_path in upload_paths:
        path = Path(upload_path)
        if path.suffix.lower() in _SOURCE_DECK_SUFFIXES:
            # Zip reading plus per-picture decoding is blocking work; keep it
            # off the event loop that is driving the rest of generation.
            extracted = await asyncio.to_thread(extract_pptx_images, path)
            collected.extend(
                _RawImage(
                    mime_type=image.mime_type,
                    width=image.width,
                    height=image.height,
                    digest=image.digest,
                    payload=image.payload,
                    source_slide_index=image.source_slide_index,
                    source_heading=image.source_heading,
                    slide_coverage=image.slide_coverage,
                )
                for image in extracted
            )
            continue
        raw = await asyncio.to_thread(_probe_uploaded_image, path)
        if raw is not None:
            collected.append(raw)
    return collected


def _probe_uploaded_image(path: Path) -> Optional[_RawImage]:
    """Describe one directly uploaded file, or ``None`` if it is not a picture."""

    try:
        with Image.open(path) as image:
            # Read the format from the bytes rather than the extension: a
            # mislabelled upload must be filtered on what it actually is.
            mime_type = Image.MIME.get((image.format or "").upper(), "")
            width, height = image.size
    except OSError:
        # A non-image upload (a PDF, a stray text file) simply contributes no
        # candidate; generation must not fail over one.
        return None

    return _RawImage(
        mime_type=mime_type,
        width=width,
        height=height,
        digest=_file_digest(path),
        source_path=path,
    )


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(_DIGEST_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _apply_source_image_filters(collected: Sequence[_RawImage]) -> list[_RawImage]:
    """Reduce the raw pictures to the ones worth offering to the slide author."""

    supported = [
        raw for raw in collected if raw.mime_type in SUPPORTED_SOURCE_IMAGE_MIME_TYPES
    ]
    large_enough = [
        raw
        for raw in supported
        if min(raw.width, raw.height) >= MIN_SOURCE_IMAGE_EDGE_PX
    ]
    # A picture covering its whole slide is that slide's backplate. Decks this
    # tool exports through the authored-hybrid path are built that way, so a
    # deck rebuilt from such an export would otherwise offer the author a
    # photograph of an entire old slide as content for a new one.
    content_sized = [
        raw
        for raw in large_enough
        if raw.slide_coverage is None
        or raw.slide_coverage < FULL_SLIDE_COVERAGE_THRESHOLD
    ]

    first_by_digest: dict[str, _RawImage] = {}
    slides_by_digest: dict[str, set[int]] = defaultdict(set)
    for raw in content_sized:
        first_by_digest.setdefault(raw.digest, raw)
        if raw.source_slide_index is not None:
            slides_by_digest[raw.digest].add(raw.source_slide_index)

    # Insertion order carries the deterministic result order for free.
    return [
        raw
        for digest, raw in first_by_digest.items()
        # Repeated across slides means chrome the master contract already owns
        # (a logo, a watermark), never content to re-place on a new slide.
        if len(slides_by_digest[digest]) < FURNITURE_REPEAT_THRESHOLD
    ]


#: Namespace for deriving a source image's asset id from the deck it belongs to
#: and the bytes it carries. Fixed forever: changing it renames every id and
#: strands the slide HTML of decks generated before the change.
_SOURCE_IMAGE_NAMESPACE = uuid.UUID("2f8a6d1e-6c3b-5f47-9d21-0a7c4b8e5d10")


def source_image_asset_id(presentation_id: uuid.UUID, digest: str) -> uuid.UUID:
    """Return the stable asset id for one picture within one deck.

    Deriving the id from (deck, bytes) rather than allocating a random one makes
    registration idempotent across the durable retries this pipeline performs.
    """
    return uuid.uuid5(_SOURCE_IMAGE_NAMESPACE, f"{presentation_id}:{digest}")


async def _persist_raw_image(
    session: AsyncSession,
    *,
    storage_dir: Path,
    presentation_id: uuid.UUID,
    raw: _RawImage,
) -> SourceImageCandidate:
    """Materialise one picture in deck storage and record it as an asset."""

    suffix = _MIME_TO_SUFFIX[raw.mime_type]
    stored_path = storage_dir / f"{raw.digest}{suffix}"
    # Digests are unique after deduping, so an existing file holds the same
    # bytes from an earlier run of the same upload; rewriting it is pure cost.
    if not await asyncio.to_thread(stored_path.exists):
        await asyncio.to_thread(_write_stored_bytes, raw, stored_path)

    asset_id = source_image_asset_id(presentation_id, raw.digest)
    # A generation attempt is durable and resumable: the pipeline replays
    # checkpointed slide HTML that already names an asset id, while this
    # function runs again from the top. A fresh random id per attempt would
    # leave that HTML pointing at an identifier the retry never offers, so the
    # picture would be dropped and a row leaked on every attempt.
    asset = await session.get(ImageAsset, asset_id)
    if asset is None:
        asset = ImageAsset(
            id=asset_id,
            path=str(stored_path),
            is_uploaded=True,
            extras={
                "presentation_id": str(presentation_id),
                "digest": raw.digest,
                "mime_type": raw.mime_type,
                "width": raw.width,
                "height": raw.height,
                "source_slide_index": raw.source_slide_index,
                "source_heading": raw.source_heading,
            },
        )
        session.add(asset)

    return SourceImageCandidate(
        asset_id=str(asset_id),
        path=stored_path,
        mime_type=raw.mime_type,
        width=raw.width,
        height=raw.height,
        digest=raw.digest,
        source_slide_index=raw.source_slide_index,
        source_heading=raw.source_heading,
    )


def _write_stored_bytes(raw: _RawImage, stored_path: Path) -> None:
    if raw.payload is not None:
        stored_path.write_bytes(raw.payload)
        return
    if raw.source_path is None:
        raise ValueError("source image carries neither payload nor source path")
    shutil.copyfile(raw.source_path, stored_path)
