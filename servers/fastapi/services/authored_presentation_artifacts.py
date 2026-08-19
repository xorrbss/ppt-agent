from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
from pathlib import Path
import shutil
import uuid
from typing import List, Optional

from pathvalidate import sanitize_filename
from sqlalchemy.exc import SQLAlchemyError

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import PresentationOutlineModel
from services.authored_checkpoint_service import (
    atomic_write,
    cleanup_checkpoint_artifacts,
    clear_checkpoint_html,
    complete_stage,
)
from utils.asset_directory_utils import (
    filesystem_image_path_to_app_data_reference,
    get_exports_directory,
    get_images_directory,
)
from utils.filename_utils import safe_export_basename
from utils.llm_calls.author_deck import build_image_pptx
from utils.llm_calls.author_slide import Brand
from utils.outline_utils import get_presentation_title_from_presentation_outline


_DEFAULT_PRIMARY = "#2563EB"
LOGGER = logging.getLogger(__name__)


def _is_korean(*candidates: Optional[str]) -> bool:
    for c in candidates:
        s = (c or "").lower()
        if "korea" in s or "?쒓뎅" in s or s.startswith("ko"):
            return True
    return False


def authored_title(
    request: GeneratePresentationRequest, outline: PresentationOutlineModel
) -> str:
    """A clean deck title for authored mode. Prefer the user's topic (request.content
    first line) over the verbose generated first-slide outline (which often carries
    presenter/date/agenda scaffolding that bloats the title and filename)."""
    content = (request.content or "").strip()
    if content:
        first_line = content.splitlines()[0].lstrip("#").strip()
        if first_line:
            return first_line[:80]
    return get_presentation_title_from_presentation_outline(outline)


def resolve_brand(
    request: GeneratePresentationRequest,
    outline: PresentationOutlineModel,
    language: Optional[str],
) -> Brand:
    """Build the shared design tokens for the deck. Theme injection point: brand primary
    colour + fonts + wordmark go into every slide's design-system brief. User-supplied
    request fields win; otherwise a brand-blue primary + language-aware fonts (Noto Sans
    KR for Korean, else a clean sans) and no wordmark."""
    korean = _is_korean(language, request.language)
    return Brand(
        topic=authored_title(request, outline),
        language=language or ("Korean" if korean else "the deck's language"),
        primary=(request.primary_color or _DEFAULT_PRIMARY).strip() or _DEFAULT_PRIMARY,
        fonts=(request.fonts or ("Noto Sans KR" if korean else "Inter")).strip(),
        wordmark=(request.wordmark or "").strip(),
        primary_is_explicit=bool((request.primary_color or "").strip()),
        fonts_are_explicit=bool((request.fonts or "").strip()),
        verbosity=request.verbosity.value,
    )


def _authored_image_set_digest(
    pngs: List[bytes],
    publication_token: str | None = None,
) -> str:
    digest = hashlib.sha256()
    if publication_token:
        digest.update(publication_token.encode("utf-8"))
        digest.update(b"\0")
    for png in pngs:
        digest.update(len(png).to_bytes(8, "big"))
        digest.update(png)
    return digest.hexdigest()


def _authored_image_set_relative_directory(
    presentation_id: uuid.UUID,
    pngs: List[bytes],
    publication_token: str | None = None,
) -> str:
    image_set = _authored_image_set_digest(pngs, publication_token)
    return os.path.join("authored", str(presentation_id), "sets", image_set)


def _save_slide_pngs(
    presentation_id: uuid.UUID,
    pngs: List[bytes],
    publication_token: str | None = None,
) -> List[str]:
    """Persist immutable, content-addressed slide images for one publication.

    The publication token isolates concurrent/retried operation attempts.  A
    failed attempt can therefore remove only its own image set and can never
    overwrite bytes referenced by an already-published revision.
    """

    rel_dir = _authored_image_set_relative_directory(
        presentation_id,
        pngs,
        publication_token,
    )
    abs_dir = os.path.join(get_images_directory(), rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    refs: List[str] = []
    for i, png in enumerate(pngs):
        content_hash = hashlib.sha256(png).hexdigest()
        fname = f"slide_{i}-{content_hash}.png"
        absolute_path = os.path.join(abs_dir, fname)
        atomic_write(Path(absolute_path), png)
        refs.append(filesystem_image_path_to_app_data_reference(absolute_path))
    return refs


def _remove_partial_slide_pngs(
    presentation_id: uuid.UUID,
    pngs: List[bytes],
    publication_token: str | None = None,
) -> None:
    """Remove only one unpublished, immutable image set."""

    images_root = Path(get_images_directory()).resolve()
    authored_root = (images_root / "authored").resolve()
    presentation_root = (authored_root / str(presentation_id)).resolve()
    sets_root = (presentation_root / "sets").resolve()
    target = (
        images_root
        / _authored_image_set_relative_directory(
            presentation_id,

            pngs,
            publication_token,
        )
    ).resolve()
    if target.parent != sets_root or sets_root.parent != presentation_root:
        raise ValueError("invalid authored slide image cleanup target")
    if target.is_dir():
        shutil.rmtree(target)


def _build_image_pdf(pngs: List[bytes], out_path: str) -> str:
    from PIL import Image

    imgs = [Image.open(io.BytesIO(p)).convert("RGB") for p in pngs]
    imgs[0].save(out_path, save_all=True, append_images=imgs[1:])
    return out_path


def _build_authored_export(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    pngs: List[bytes],
    title: str,
) -> str:
    """Assemble the rendered slides into the requested export format (pure Python:
    python-pptx / PIL ??no external converter). Returns the absolute file path."""
    destination = _authored_export_path(request, presentation_id, title)
    exports = os.path.dirname(destination)
    stem, extension = os.path.splitext(os.path.basename(destination))
    os.makedirs(exports, exist_ok=True)
    if extension == ".pdf":
        from PIL import Image

        temporary = os.path.join(exports, f".{stem}.{uuid.uuid4().hex}.pdf")
        try:
            _build_image_pdf(pngs, temporary)
            with Image.open(temporary) as exported_pdf:
                if getattr(exported_pdf, "n_frames", 1) != len(pngs):
                    raise RuntimeError("authored PDF slide count validation failed")
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return destination
    temporary = os.path.join(exports, f".{stem}.{uuid.uuid4().hex}.pptx")
    try:
        build_image_pptx(pngs, temporary)
        from pptx import Presentation as PptxPresentation

        if len(PptxPresentation(temporary).slides) != len(pngs):
            raise RuntimeError("authored PPTX slide count validation failed")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def _authored_export_path(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    title: str,
) -> str:
    base = safe_export_basename(
        sanitize_filename((title or str(presentation_id)).strip() or str(presentation_id))
    )
    stem = f"{base}-{presentation_id.hex[:8]}"
    extension = "pdf" if request.export_as == "pdf" else "pptx"
    return os.path.join(get_exports_directory(), f"{stem}.{extension}")


async def _cleanup_published_checkpoints(job_id: uuid.UUID) -> None:
    """Best-effort post-publish cleanup: render artifacts and stored slide HTML.

    Failures never unwind an already-published deck; leftovers remain for the
    TTL sweeper (files) or job CASCADE delete (rows)."""
    try:
        cleanup_checkpoint_artifacts(job_id)
    except OSError:
        LOGGER.warning(
            "Unable to clean authored checkpoint artifacts for job %s",
            job_id,
            exc_info=True,
        )
    try:
        await clear_checkpoint_html(job_id)
    except SQLAlchemyError:
        LOGGER.warning(
            "Unable to clear authored checkpoint HTML for job %s",
            job_id,
            exc_info=True,
        )


async def _complete_stage_after_external_success(**kwargs):
    """Durably record a returned provider result before propagating cancellation.

    Shielding alone is insufficient because the worker would finish while the DB
    commit kept running in the background. Waiting for the shielded task here
    lets the pipeline join all workers before its attempt token can be revoked.
    """

    checkpoint_task = asyncio.create_task(complete_stage(**kwargs))
    try:
        return await asyncio.shield(checkpoint_task)
    except asyncio.CancelledError:
        await checkpoint_task
        raise
