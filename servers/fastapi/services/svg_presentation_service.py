"""SVG generation with existing durable jobs and fenced per-page checkpoints.

This is a thin facade: the checkpoint/PNG primitives live in
``svg_presentation_checkpoint``, the legacy inline source-image manifest in
``svg_presentation_legacy_assets``, per-deck setup in ``svg_presentation_prepare``,
the per-page generation loop in ``svg_presentation_generate``, and publication in
``svg_presentation_publish`` (see ``services/raster_redesign_generation.py`` and
``templates/v2/models/elements/`` for the same pattern elsewhere in this codebase).

Every name below stays importable from here for existing callers and tests.
Several of them (``require_svg_creation_admission``, ``pin_svg_style``,
``request_canvas_contract``, ``async_session_maker``, ``owned_job``,
``initialize_checkpoints``, ``_checkpoint``, ``_read_png``, ``_write_png``,
``_publish``, ``run_vision_advisory``) are also the mutable hooks that ``svg_presentation_prepare`` and
``svg_presentation_generate`` read back off *this* module at call time (instead of
importing them directly), so tests that monkeypatch an attribute here still reach
those split-out modules -- exactly as they did before the split.
"""
from __future__ import annotations

from services import svg_outline_spans
from services.authored_canvas import request_canvas_contract
from services.authored_checkpoint_fence import owned_job
from services.database import async_session_maker
from services.generation_core.quality.vision_advisory import run_vision_advisory
from services.svg_generation_admission import require_svg_creation_admission, pin_svg_style
from services.svg_presentation_checkpoint import (
    _checkpoint, _collect_pages_or_fail, _digest, _NUMERIC_CLAIM, _provider_attempt_count,
    _read_png, _required_numeric_claims, _save_source_page_pngs, _write_png,
    initialize_svg_checkpoints as initialize_checkpoints,
)
from services.svg_presentation_generate import _generate
from services.svg_presentation_legacy_assets import _legacy_source_image_manifest
from services.svg_presentation_publish import _publish

__all__ = [
    "generate_svg_presentation",
    "require_svg_creation_admission", "pin_svg_style", "request_canvas_contract",
    "async_session_maker", "owned_job", "initialize_checkpoints", "run_vision_advisory",
    "_digest", "_NUMERIC_CLAIM", "_required_numeric_claims", "_provider_attempt_count",
    "_collect_pages_or_fail", "_checkpoint", "_write_png", "_read_png",
    "_save_source_page_pngs", "_legacy_source_image_manifest", "_generate", "_publish",
]


async def generate_svg_presentation(request, presentation_id, outline, language,
                                    sql_session, generation_job=None):
    from services.authored_presentation_service import _AUTHORED_JOB_SEMAPHORE
    from utils.owner_context import set_current_tenant_id, reset_current_tenant_id
    tenant_token = None
    if getattr(request, "_raster_reconstruction", False):
        from services.svg_generation_assets import publication_context
        async with async_session_maker() as authority_session:
            context, _ = await publication_context(authority_session, generation_job)
        tenant_token = set_current_tenant_id(context.tenant_id)
    try:
        with svg_outline_spans.span("job_semaphore_wait"):
            await _AUTHORED_JOB_SEMAPHORE.acquire()
        try:
            return await _generate(request, presentation_id, outline, language, sql_session, generation_job)
        finally:
            _AUTHORED_JOB_SEMAPHORE.release()
    finally:
        if tenant_token is not None:
            reset_current_tenant_id(tenant_token)
