from __future__ import annotations

import asyncio
import io
import logging
import os
import uuid
from typing import List, Mapping, Sequence

from pathvalidate import sanitize_filename
from sqlalchemy.exc import SQLAlchemyError

from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_checkpoint_service import (
    clear_checkpoint_html,
    complete_stage,
)
from services.authored_admission_checkpoint import prepare_checkpoint_write
from services.generation_core.style.build import authored_title, resolve_brand
from utils.asset_directory_utils import (
    get_exports_directory,
)
from utils.filename_utils import safe_export_basename
from utils.get_env import is_authored_retain_checkpoint_html_enabled
from services.generation_core.authoring.author_deck import build_image_pptx
from services.authored_canvas import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX, canvas_or_default
from services.slide_image_sets import (  # noqa: F401 - historical import surface for authored callers
    _authored_image_set_digest,
    _authored_image_set_relative_directory,
    _remove_partial_slide_pngs,
    _save_slide_pngs,
)

__all__ = ["authored_title", "resolve_brand"]

LOGGER = logging.getLogger(__name__)


def _build_image_pdf(
    pngs: List[bytes], out_path: str, *,
    canvas_width_px: int = DEFAULT_CANVAS_WIDTH_PX, canvas_height_px: int = DEFAULT_CANVAS_HEIGHT_PX,
    physical_size_emu: tuple[int, int] | None = None,
) -> str:
    from PIL import Image

    imgs = [Image.open(io.BytesIO(p)).convert("RGB") for p in pngs]
    expected_size = (int(canvas_width_px), int(canvas_height_px))
    if any(image.size != expected_size for image in imgs):
        raise ValueError("authored export image canvas mismatch")
    resolution = 96.0
    if physical_size_emu is not None:
        width_emu, height_emu = physical_size_emu
        if width_emu <= 0 or height_emu <= 0 or width_emu * canvas_height_px != height_emu * canvas_width_px:
            raise ValueError("document_canvas_geometry_mismatch")
        resolution = canvas_width_px * 914400 / width_emu
    imgs[0].save(
        out_path,
        save_all=True,
        append_images=imgs[1:],
        resolution=resolution,
    )
    return out_path


def _build_authored_export(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    pngs: List[bytes],
    title: str,
    *,
    local_fit_receipts: Sequence[Mapping[str, object]] | None = None,
    canvas: object | None = None,
) -> str:
    """Assemble the rendered slides into the requested export format (pure Python:
    python-pptx / PIL ??no external converter). Returns the absolute file path."""
    destination = _authored_export_path(request, presentation_id, title)
    exports = os.path.dirname(destination)
    stem, extension = os.path.splitext(os.path.basename(destination))
    os.makedirs(exports, exist_ok=True)
    canvas_width_px, canvas_height_px = canvas_or_default(canvas)
    physical_kwargs = {}
    document_canvas = getattr(request, "_document_canvas", None)
    if document_canvas is not None:
        from services.document_canvas import canvas_physical_size
        from services.document_canvas_generation import export_size_points

        export_size_points(canvas, document_canvas)
        physical_kwargs["physical_size_emu"] = canvas_physical_size(document_canvas)
    if extension == ".pdf":
        from PIL import Image

        temporary = os.path.join(exports, f".{stem}.{uuid.uuid4().hex}.pdf")
        try:
            _build_image_pdf(
                pngs,
                temporary,
                canvas_width_px=canvas_width_px,
                canvas_height_px=canvas_height_px,
                **physical_kwargs,
            )
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
        build_image_pptx(
            pngs,
            temporary,
            canvas_width_px=canvas_width_px,
            canvas_height_px=canvas_height_px,
            **physical_kwargs,
        )
        from pptx import Presentation as PptxPresentation

        if len(PptxPresentation(temporary).slides) != len(pngs):
            raise RuntimeError("authored PPTX slide count validation failed")
        if local_fit_receipts is not None:
            from services.authored_local_fit_export import verify_local_fit_pptx

            verify_local_fit_pptx(
                temporary,
                local_fit_receipts,
                mode="fidelity",
                pngs=pngs,
            )
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
    """Seal-aware post-publish cleanup of stored slide HTML.

    Render artifacts are deliberately left in place after the receipt seal so
    the bounded TTL/byte-budget sweeper can reclaim them.  Immediate recursive
    deletion made post-crash evidence unavailable and bypassed the retention
    policy.  The caller writes the durable ``.sealed`` marker before entering
    this function; failed or in-flight attempts therefore remain protected.
    """
    if is_authored_retain_checkpoint_html_enabled():
        # Bench runs keep the pre-repair HTML for the offline fit replay
        # (design 20260904, section 6.3); statuses are sealed either way.
        LOGGER.info("Retaining authored checkpoint HTML for job %s", job_id)
        return
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

    checkpoint_task = asyncio.create_task(complete_stage(**prepare_checkpoint_write(kwargs)))
    try:
        return await asyncio.shield(checkpoint_task)
    except asyncio.CancelledError:
        await checkpoint_task
        raise
