"""Routes one raster reconstruction page to the correct SVG renderer.

Extracted out of ``svg_presentation_service.py`` (kept at/under the file-size
ratchet) so that page routing between the deterministic reconstruct/
full_page_preserve layout and the "redesign" layout stays in one small,
testable place.

``redesign`` pages (produced by ``analyze_redesign_pptx`` under
``analysis_mode="auto_redesign"``) are the default, editable A4 rebuild of an
image-only source: they get the caller's generation instruction so the
deterministic layout can honor requested tone/structure. ``reconstruct`` and
``full_page_preserve`` pages behave exactly as before and never see the
instruction, since neither renders from a fresh layout pass.
"""
from __future__ import annotations

from services.source_content.raster_contracts import RasterAnalysis, RasterApprovalPin
from services.source_content.raster_svg_layout import (
    raster_page_render_advisories, render_raster_page_svg,
)


def render_raster_reconstruction_page_svg(
    raster_analysis: RasterAnalysis,
    raster_pin: RasterApprovalPin,
    page_number: int,
    *,
    style_id: str,
    instructions: str | None,
) -> str:
    """Render one approved raster page, honoring its analysed render mode."""
    page = raster_analysis.pages[page_number - 1]
    kwargs: dict[str, object] = {}
    if getattr(page, "render_mode", None) == "redesign":
        kwargs["instruction"] = instructions
    return render_raster_page_svg(
        raster_analysis,
        page_number,
        style_id=style_id,
        approved_region_ids=raster_pin.approved_region_ids,
        **kwargs,
    )


def raster_render_advisories_for_page(
    raster_analysis: RasterAnalysis, page_number: int, svg: str,
) -> list[dict]:
    """Non-blocking, per-page receipt advisories for one rendered raster page.

    Empty for reconstruct/full_page_preserve pages. Callers store the result
    alongside the page's other raster evidence (rasterCropLineage,
    rasterAnalysisDigest, ...) in the checkpoint payload, which becomes
    slide.content.svgReceipt at publish.
    """
    return list(raster_page_render_advisories(raster_analysis, page_number, svg))


def raster_required_text_for_page(
    raster_analysis: RasterAnalysis, raster_pin: RasterApprovalPin, page_number: int, final_svg: str,
) -> list[str]:
    """Required text tokens the measurement gate must find visible.

    Approved title/body region text, minus any region the dense-group pixel
    fallback already carries as pixels (raster_page_render_advisories'
    replacedRegionIds): those tokens are accounted for by the content ledger
    via redesign_content_ledger(..., pixel_region_ids=...), not by rendered
    SVG text. Empty for full_page_preserve, whose one embedded crop carries
    everything as pixels.
    """
    page = raster_analysis.pages[page_number - 1]
    if page.render_mode == "full_page_preserve":
        return []
    approved_ids = set(raster_pin.approved_region_ids)
    replaced_ids = {
        region_id
        for advisory in raster_page_render_advisories(raster_analysis, page_number, final_svg)
        for region_id in advisory.get("replacedRegionIds", ())
    }
    return [
        region.text for region in page.regions
        if region.region_id in approved_ids and region.region_id not in replaced_ids
        and region.role in {"title", "body"} and region.text and region.text.strip()
    ]


def raster_measure_via_materialized_admission(
    raster_analysis: RasterAnalysis, raster_pin: RasterApprovalPin, page_number: int, final_svg: str,
) -> bool:
    """True when this page's expanded (base64-embedded-crop) SVG must be
    measured through the authorized-materialized admission the export worker
    already uses for expanded base64, not the 2MB canonical-SVG-contract
    limit -- the *canonical*, slot-referenced SVG stays subject to that limit
    either way. full_page_preserve (one embedded crop) and redesign pages
    that actually embed at least one crop (N crops, routinely 1.6-1.9MB of
    lossless capture bytes) both need it; a text-only redesign page (no
    ``<image>`` at all) has no expanded bytes to admit, so it stays on the
    normal path, and reconstruct pages do not use this route either.
    """
    page = raster_analysis.pages[page_number - 1]
    if page.render_mode == "full_page_preserve":
        return page.page_number in raster_pin.full_page_preserve_pages
    if page.render_mode != "redesign":
        return False
    import xml.etree.ElementTree as ET
    return any(node.tag.rsplit("}", 1)[-1] == "image" for node in ET.fromstring(final_svg).iter())


def raster_source_font_profile(raster_analysis: RasterAnalysis, page_number: int) -> str | None:
    """The packaged-font marker to pin for the measurement gate on a redesign
    page (None otherwise): mirrors the source-reflow path's own font pinning
    so the width-probe resolves the SVG's declared Noto Sans KR family
    instead of guessing at a system font that may not distinguish all-Hangul
    strings correctly.
    """
    page = raster_analysis.pages[page_number - 1]
    if page.render_mode != "redesign":
        return None
    from utils.svg_template_fonts import trusted_font_marker
    return trusted_font_marker()


def raster_measurement_inputs(
    raster_analysis: RasterAnalysis, raster_pin: RasterApprovalPin, page_number: int, final_svg: str,
) -> tuple[list[str], bool, str | None]:
    """Bundle the three per-page inputs the measurement gate needs for a
    raster page: required_text, whether to route through the authorized-
    materialized admission, and the font profile to pin.
    """
    return (
        raster_required_text_for_page(raster_analysis, raster_pin, page_number, final_svg),
        raster_measure_via_materialized_admission(raster_analysis, raster_pin, page_number, final_svg),
        raster_source_font_profile(raster_analysis, page_number),
    )
