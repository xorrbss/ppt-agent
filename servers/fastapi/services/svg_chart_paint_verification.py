"""Post-publication v5 native chart paint verification, hosted by the PPTX export.

Render passes (generation, remake) no longer run the chart paint proof: it
needs 55-105 s of capture planes per real chart, over their 60 s render budget
(docs/session-results/svg-v5-plane-budget-options-20261108.md). The export is
the only consumer of that evidence -- a v5 chart becomes a native PPTX chart
only with it -- so ``export.pptx`` runs the unchanged proof for each published
page that still lacks current evidence, with its own larger capture budget,
and stores the result on the slide (``slide.properties``, digest-bound; see
``chart_paint_verification``). ``slide.properties`` is outside the export's
admission snapshot, so recording evidence cannot invalidate an export admitted
against the same revision. A page that cannot be verified in this export
(budget, unsupported render inputs, renderer failure) keeps its charts drawn
and is verified again by a later export; nothing here fails the deck.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from services.generation_core.drivers.svg.chart_paint_verification import (
    EVIDENCE_KEY, VERIFICATION_TIMEOUT_SECONDS, chart_paint_fallbacks, current_paint_evidence,
    gated_chart_ids, seal_paint_evidence, verification_manifest,
)

LOGGER = logging.getLogger(__name__)
# What a verification must leave of the admitted export deadline for the
# export itself (bundle, worker, receipt, publication).
EXPORT_RESERVE_SECONDS = 240
# The capture client allows the worker timeout plus this cleanup window.
CLIENT_GRACE_SECONDS = 25


def deck_requirements_revision(presentation):
    from services.generation_core.drivers.svg.native_requirements import requirements_revision
    return requirements_revision((((presentation.theme or {}).get("svg") or {}).get("styleSnapshot")))


def _sidecar(content):
    from services.generation_core.native_objects import NativeObjectsError, slide_native_objects
    try:
        return slide_native_objects(content)
    except NativeObjectsError:
        # The bundle entry records the invalid sidecar's own fallback.
        return None


async def _render_inputs(session, presentation, slide) -> dict | None:
    """The published page's measurement inputs, or ``None`` when not reproducible here."""
    from services.authored_canvas import presentation_canvas_contract, presentation_canvas_metadata
    from services.document_canvas import DocumentCanvasMetadata, parse_canvas_metadata

    content = slide.content or {}
    receipt = content.get("svgReceipt") or {}
    if any(key in receipt for key in ("rasterApprovalPin", "rasterSourcePageNumber")):
        # Raster reconstruction pages measure an authorized image-only derivative.
        return None
    theme = presentation.theme or {}
    snapshot = (theme.get("svg") or {}).get("styleSnapshot") or {}
    canvas = presentation_canvas_contract(theme)
    metadata = parse_canvas_metadata(presentation_canvas_metadata(theme))
    kwargs: dict = {"width": canvas.width_px, "height": canvas.height_px}
    if isinstance(metadata, DocumentCanvasMetadata):
        kwargs["physical_canvas"] = metadata
    if receipt.get("readability_policy") is not None:
        kwargs["readability_policy"] = receipt["readability_policy"]
    if content.get("svgAssetManifest") is not None:
        from services.svg_document_mutations import read_svg_slide_assets
        assets = await read_svg_slide_assets(session, presentation, slide)
        if assets is None:
            return None
        if assets[2] != slide.svg_content:
            kwargs["materialized_svg"] = assets[2]
    template_page = receipt.get("svg_template_page")
    if template_page is not None:
        from services.svg_templates.generation import context_frame
        from services.svg_templates.selection_material import ensure_selection_material, template_package
        await ensure_selection_material(session, snapshot.get("svg_template"))
        kwargs["font_manifest"] = template_package(snapshot["svg_template"])["font_manifest"]
        frame = context_frame(snapshot, template_page)
        if frame.schema_version == "svg-template-frame/v3":
            if frame.fixed_glyph_preview is None:
                return None
            kwargs["fixed_glyph_preview"] = frame.fixed_glyph_preview.model_dump(mode="json")
    if receipt.get("system_furniture") is not None:
        kwargs["system_furniture"] = receipt["system_furniture"]
    elif template_page is not None:
        return None
    if receipt.get("source_preservation"):
        from utils.svg_template_fonts import source_font_profile
        profile = source_font_profile(snapshot)
        if profile:
            kwargs["source_font_profile"] = profile
    return kwargs


async def verify_slide_chart_paint(session, presentation, slide, *,
                                   capture_timeout: int = VERIFICATION_TIMEOUT_SECONDS, render=None) -> dict | None:
    """Run the unchanged capture proof for one published page.

    Returns the sealed evidence record of a completed validation (passed or
    failed), or ``None`` when no validation could run (no manifest, inputs not
    reproducible, renderer error or timeout).
    """
    from services.generation_core.drivers.svg.chart_content_paint import validate_chart_content_paint
    svg, content = slide.svg_content, slide.content or {}
    manifest = verification_manifest(content, svg)
    if manifest is None:
        return None
    try:
        kwargs = await _render_inputs(session, presentation, slide)
        if kwargs is None:
            return None
        if render is None:
            from services.svg_renderer import render_svg_with_metrics as render
        rendered = await render(svg, timeout=capture_timeout, chart_witness_manifest=manifest, **kwargs)
    except Exception:
        LOGGER.warning("SVG chart paint verification could not capture slide %s", getattr(slide, "id", None),
                       exc_info=True)
        return None
    paint = validate_chart_content_paint(manifest, rendered.quality, capture=rendered)
    return seal_paint_evidence(svg, manifest, content, paint)


async def store_chart_paint_evidence(session_factory, slide, record) -> bool:
    """Persist on the slide row only while it still holds the verified bytes.

    Returns whether the record was stored.
    """
    from models.sql.slide import SlideModel
    try:
        async with session_factory() as session:
            row = await session.get(SlideModel, slide.id)
            if row is None or row.svg_content != slide.svg_content:
                return False
            row.properties = {**(row.properties if isinstance(row.properties, dict) else {}), EVIDENCE_KEY: record}
            session.add(row)
            await session.commit()
            return True
    except Exception:
        LOGGER.warning("SVG chart paint evidence for slide %s was not stored", getattr(slide, "id", None),
                       exc_info=True)
        return False


def slide_chart_paint_pending(slide, *, revision) -> bool:
    """Whether a published page has v5-gated charts without current evidence.

    ``False`` for every revision other than v5.
    """
    from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V5
    if revision != REQUIREMENTS_VERSION_V5:
        return False
    content, svg = slide.content or {}, slide.svg_content
    properties = getattr(slide, "properties", None)
    properties = properties if isinstance(properties, dict) else {}
    return bool(gated_chart_ids(content, _sidecar(content), revision=revision)
                and current_paint_evidence(properties, content, svg) is None)


async def deck_chart_paint_fallbacks(session_factory, *, presentation, slides,
                                     remaining_seconds: Callable[[], int] | None = None,
                                     capture_timeout: int = VERIFICATION_TIMEOUT_SECONDS,
                                     render=None,
                                     between: Callable[[], Awaitable[None]] | None = None) -> list[list[dict]] | None:
    """Per slide, the v5 charts that must stay drawn in this export.

    ``None`` for every deck whose requirements revision is not v5 (the export
    bundle then stays exactly what it was). Pages lacking current evidence are
    verified while ``remaining_seconds()`` still covers one capture plus
    ``EXPORT_RESERVE_SECONDS``; without ``remaining_seconds`` only stored
    evidence is used.
    """
    revision = deck_requirements_revision(presentation)
    from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V5
    if revision != REQUIREMENTS_VERSION_V5:
        return None
    result = []
    for slide in slides:
        content, svg = slide.content or {}, slide.svg_content
        sidecar = _sidecar(content)
        properties = getattr(slide, "properties", None)
        properties = properties if isinstance(properties, dict) else {}
        if (gated_chart_ids(content, sidecar, revision=revision)
                and current_paint_evidence(properties, content, svg) is None
                and remaining_seconds is not None
                and remaining_seconds() >= capture_timeout + CLIENT_GRACE_SECONDS + EXPORT_RESERVE_SECONDS):
            if between is not None:
                await between()
            async with session_factory() as session:
                record = await verify_slide_chart_paint(session, presentation, slide,
                                                        capture_timeout=capture_timeout, render=render)
            if record is not None:
                properties = {**properties, EVIDENCE_KEY: record}
                await store_chart_paint_evidence(session_factory, slide, record)
        result.append(chart_paint_fallbacks(content, properties, svg, sidecar, revision=revision))
    return result


__all__ = ["EXPORT_RESERVE_SECONDS", "deck_chart_paint_fallbacks", "deck_requirements_revision",
           "slide_chart_paint_pending", "store_chart_paint_evidence", "verify_slide_chart_paint"]
