"""Deterministic A4 SVG layout for approved raster reconstruction pages."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable
from xml.sax.saxutils import escape

from services.source_content.raster_contracts import RasterAnalysis, require_analysis_digest
from services.source_content.raster_target import raster_target_font_floor, require_raster_physical_readability


MIN_FONT_UNITS = 9.5 * 12700 / 9000 + 0.005
IMAGE_ROLES = {"screenshot", "logo", "table"}
TEXT_ROLES = {"title", "body"}


def _visual_width(value: str) -> float:
    return sum(1.0 if unicodedata.east_asian_width(char) in {"F", "W"} else 0.55 for char in value)


def _font_for_line(value: str, available_width: float, preferred: float, minimum: float = MIN_FONT_UNITS) -> float:
    preferred = max(preferred, minimum)
    needed = _visual_width(value) * preferred
    chosen = preferred if needed <= available_width else preferred * available_width / needed
    if chosen < minimum:
        raise ValueError("raster_text_requires_below_9_5pt")
    return chosen


def _text_lines(value: str, available_width: float, preferred: float, minimum: float = MIN_FONT_UNITS) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    for original in value.splitlines() or [""]:
        if _visual_width(original) * minimum <= available_width:
            result.append((original, _font_for_line(original, available_width, preferred, minimum)))
            continue
        # Preserve every reviewed character while wrapping at the 9.5 pt floor.
        chunk = ""
        for char in original:
            if chunk and _visual_width(chunk + char) * minimum > available_width:
                result.append((chunk, minimum))
                chunk = char
            else:
                chunk += char
        if chunk:
            result.append((chunk, minimum))
    return result


def _approved_regions(analysis: RasterAnalysis, page_number: int):
    if page_number < 1 or page_number > len(analysis.pages):
        raise ValueError("raster_page_number_invalid")
    require_analysis_digest(analysis)
    from services.source_content.raster_reconstruction_store import raster_review_blockers

    page = analysis.pages[page_number - 1]
    redesign_pages = {
        f"page_{item.page_number}:" for item in analysis.pages
        if getattr(item, "render_mode", "reconstruct") == "redesign"
    }
    # Redesign pages are gated by _approved_redesign_page below; page-scoped
    # blockers written for reconstruct review (OCR word review, table crops)
    # do not apply to them. Global blockers still block every page.
    blockers = [
        issue for issue in raster_review_blockers(analysis)
        if not any(issue.startswith(prefix) for prefix in redesign_pages)
    ]
    if blockers:
        raise ValueError("raster_analysis_review_unresolved")
    if getattr(page, "render_mode", "reconstruct") == "redesign":
        return _approved_redesign_page(page)
    if getattr(page, "render_mode", "reconstruct") == "full_page_preserve":
        region = getattr(page, "full_page_region", None)
        acknowledgements = set(getattr(page, "full_page_preserve_acknowledgements", ()))
        confirmed = (
            not acknowledgements if page.approval_method == "automatic_preserve"
            else acknowledgements == {"image_only", "legibility"}
        )
        if region is None or region.review_required or not confirmed:
            raise ValueError("raster_full_page_review_unresolved")
        if region.role != "full_page_preserve" or region.crop_artifact_ref is None:
            raise ValueError("raster_full_page_crop_missing")
        if (
            region.bbox.x != 0 or region.bbox.y != 0
            or abs(region.bbox.width - analysis.source.canvas_width_emu) > 1
            or abs(region.bbox.height - analysis.source.canvas_height_emu) > 1
            or region.source_pixel_bbox is None
            or region.source_pixel_bbox[:2] != (0, 0)
        ):
            raise ValueError("raster_full_page_source_boundary_invalid")
        return page
    if any(region.review_required for region in page.regions):
        raise ValueError("raster_analysis_review_unresolved")
    if any(region.role == "unknown" for region in page.regions):
        raise ValueError("raster_unknown_region_unresolved")
    if any(word.review_required for word in page.ocr_inventory):
        raise ValueError("raster_ocr_review_unresolved")
    for region in page.regions:
        if region.role in IMAGE_ROLES and region.crop_artifact_ref is None:
            raise ValueError("raster_image_crop_missing")
        if region.role in TEXT_ROLES and not (region.text or "").strip():
            raise ValueError("raster_text_missing")
        if region.role == "screenshot":
            area = region.bbox.width * region.bbox.height
            source_area = analysis.source.canvas_width_emu * analysis.source.canvas_height_emu
            if area / source_area > 0.95:
                raise ValueError("raster_whole_page_screenshot_not_reconstructed")
    return page


def _approved_redesign_page(page):
    """Admit an automatically redesigned page.

    Only regions still marked ``review_required`` block (decoration is never
    rendered, so it cannot block). Crops must exist for every region placed
    as an image, and text regions must carry text.
    """

    from services.source_content.raster_redesign_plan import region_is_crop

    if getattr(page, "approval_method", None) not in {"automatic_redesign", "manual_review"}:
        raise ValueError("raster_redesign_approval_invalid")
    for region in page.regions:
        if region.role == "decoration":
            continue
        if region.review_required:
            raise ValueError("raster_analysis_review_unresolved")
        if region_is_crop(region) and region.crop_artifact_ref is None:
            raise ValueError("raster_image_crop_missing")
        if region.role in TEXT_ROLES and not (region.text or "").strip():
            raise ValueError("raster_text_missing")
    return page


def _render_full_page_preserve_svg(analysis: RasterAnalysis, page_number: int, region) -> str:
    """Fit the one reviewed original image inside a visible A4 frame."""

    from services.raster_source_assets import raster_slot_id

    width, height = analysis.target_canvas.width_units, analysis.target_canvas.height_units
    margin, header, footer = 34, 48, 25
    available_width = width - margin * 2
    available_height = height - header - footer - margin * 2
    ratio = min(
        available_width / analysis.source.canvas_width_emu,
        available_height / analysis.source.canvas_height_emu,
    )
    image_width = math.floor(analysis.source.canvas_width_emu * ratio)
    image_height = math.floor(analysis.source.canvas_height_emu * ratio)
    if image_width <= 0 or image_height <= 0:
        raise ValueError("raster_full_page_target_invalid")
    x = (width - image_width) / 2
    y = header + margin + (available_height - image_height) / 2
    slot_id = raster_slot_id(page_number, region.region_id)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="#f6f9fd"/>'
        f'<rect width="{width}" height="{header}" fill="#123f73"/>'
        f'<rect x="{x - 3:.3f}" y="{y - 3:.3f}" width="{image_width + 6}" '
        f'height="{image_height + 6}" fill="#ffffff" stroke="#123f73" stroke-width="2"/>'
        f'<image x="{x:.3f}" y="{y:.3f}" width="{image_width}" height="{image_height}" '
        f'href="{slot_id}" preserveAspectRatio="xMidYMid meet"/>'
        '</svg>'
    )
    if len(svg.encode("utf-8")) >= 2 * 1024 * 1024:
        raise ValueError("raster_svg_size_limit")
    return svg


def candidate_layout_issue(analysis: RasterAnalysis, page_number: int) -> str | None:
    """Preview whether unreviewed regions can fit without relaxing the text floor."""

    page = analysis.pages[page_number - 1]
    width, height = analysis.target_canvas.width_units, analysis.target_canvas.height_units
    available_width = width - 68.0
    available_height = height - 48.0 - 25.0 - 68.0
    content = sorted(
        (region for region in page.regions if region.role in IMAGE_ROLES | TEXT_ROLES),
        key=lambda region: (region.bbox.y, region.bbox.x, region.z_order),
    )
    if not content:
        return "candidate_content_empty"
    rows: list[list] = []
    tolerance = analysis.source.canvas_height_emu * 0.035
    for region in content:
        if (
            analysis.target_canvas.orientation == "landscape"
            and rows and len(rows[-1]) < 4
            and abs(region.bbox.y - rows[-1][0].bbox.y) <= tolerance
            and all(
                region.bbox.x >= other.bbox.x + other.bbox.width
                or other.bbox.x >= region.bbox.x + region.bbox.width
                for other in rows[-1]
            )
        ):
            rows[-1].append(region)
        else:
            rows.append([region])
    minimum_height = max(0, len(rows) - 1) * 8.0
    seen_image = False
    try:
        for row in rows:
            column_width = (available_width - 10.0 * (len(row) - 1)) / len(row)
            if column_width <= 0:
                return "candidate_layout_overflow"
            row_heights = []
            for region in row:
                lead = region.role == "title" or (
                    analysis.target_canvas.orientation == "portrait"
                    and region.role == "body" and region.region_id == content[0].region_id
                )
                caption = analysis.target_canvas.orientation == "portrait" and region.role == "body" and seen_image
                padding = 14.0 if caption else 0.0
                if region.role in TEXT_ROLES:
                    lines = []
                    for index, source_line in enumerate((region.text or "").splitlines() or [""]):
                        preferred = 25.0 if region.role == "title" or (lead and index == 0) else 16.5
                        lines.extend(_text_lines(source_line, column_width - padding * 2, preferred,
                                              raster_target_font_floor(analysis.target_canvas)))
                    row_heights.append(sum(font * 1.35 for _, font in lines) + padding * 2)
                else:
                    row_heights.append(column_width * region.bbox.height / region.bbox.width * 0.25)
                    seen_image = True
            minimum_height += max(row_heights)
    except ValueError as error:
        return str(error)
    return "candidate_layout_overflow" if minimum_height > available_height + 0.1 else None


def render_raster_page_svg(
    analysis: RasterAnalysis,
    page_number: int,
    *,
    style_id: str = "screenshot-blue",
    approved_region_ids: Iterable[str] | None = None,
    instruction: str | None = None,
) -> str:
    """Render one approved page with editable text and lossless crop slots.

    The page count and A4 canvas remain fixed. If confirmed content cannot fit
    above the 9.5 pt floor, the function fails instead of dropping content.
    ``instruction`` (the user's generation instruction) only steers safe style
    knobs on redesign pages; it never removes or rewrites content.
    """

    if style_id != "screenshot-blue":
        raise ValueError("raster_style_not_supported")
    from services.raster_source_assets import raster_slot_id

    page = _approved_regions(analysis, page_number)
    if approved_region_ids is not None:
        all_region_ids = {
            region.region_id
            for source_page in analysis.pages
            for region in (
                (source_page.full_page_region,)
                if getattr(source_page, "render_mode", "reconstruct") == "full_page_preserve"
                else source_page.regions
            )
            if region is not None
        }
        if set(approved_region_ids) != all_region_ids:
            raise ValueError("raster_approved_region_set_mismatch")
    if getattr(page, "render_mode", "reconstruct") == "full_page_preserve":
        return _render_full_page_preserve_svg(analysis, page_number, page.full_page_region)
    if getattr(page, "render_mode", "reconstruct") == "redesign":
        from services.source_content.raster_redesign_layout import render_redesign_page_svg

        return render_redesign_page_svg(analysis, page_number, instruction=instruction)
    width, height = analysis.target_canvas.width_units, analysis.target_canvas.height_units
    margin = 34.0
    header = 48.0
    footer = 25.0
    available_width = width - 2 * margin
    available_height = height - header - footer - 2 * margin
    if available_width <= 0 or available_height <= 0:
        raise ValueError("raster_target_canvas_invalid")
    regions = sorted(page.regions, key=lambda r: (r.bbox.y, r.bbox.x, r.z_order))
    content = [region for region in regions if region.role in IMAGE_ROLES | TEXT_ROLES]
    if not content:
        raise ValueError("raster_page_has_no_approved_content")
    source_row_tolerance = analysis.source.canvas_height_emu * 0.035
    rows: list[list] = []
    for region in content:
        if (
            analysis.target_canvas.orientation == "landscape"
            and rows and len(rows[-1]) < 4
            and abs(region.bbox.y - rows[-1][0].bbox.y) <= source_row_tolerance
            and all(
                region.bbox.x >= other.bbox.x + other.bbox.width
                or other.bbox.x >= region.bbox.x + region.bbox.width
                for other in rows[-1]
            )
        ):
            rows[-1].append(region)
        else:
            rows.append([region])
    row_layouts: list[list[dict]] = []
    seen_image = False
    for row in rows:
        row.sort(key=lambda region: region.bbox.x)
        column_gap = 10.0
        column_width = (available_width - column_gap * (len(row) - 1)) / len(row)
        if column_width <= 0:
            raise ValueError("raster_page_content_overflow")
        blocks: list[dict] = []
        for index, region in enumerate(row):
            lead = region.role == "title" or (
                analysis.target_canvas.orientation == "portrait"
                and region.role == "body" and region.region_id == content[0].region_id
            )
            caption = analysis.target_canvas.orientation == "portrait" and region.role == "body" and seen_image
            padding = 14.0 if caption else 0.0
            lines = []
            if region.role in TEXT_ROLES:
                for source_line_index, source_line in enumerate((region.text or "").splitlines() or [""]):
                    lead_line = region.role == "title" or (lead and source_line_index == 0)
                    preferred = 25.0 if lead_line else 16.5
                    lines.extend(
                        (line, font, lead_line)
                        for line, font in _text_lines(source_line, column_width - padding * 2, preferred,
                                              raster_target_font_floor(analysis.target_canvas))
                    )
            blocks.append({
                "region": region,
                "x": margin + index * (column_width + column_gap),
                "width": column_width,
                "text_lines": lines,
                "text_height": sum(font * 1.35 for _, font, _ in lines) + padding * 2,
                "image_height": (
                    column_width * region.bbox.height / region.bbox.width if region.role in IMAGE_ROLES else 0.0
                ),
                "lead": lead,
                "padding": padding,
            })
            if region.role in IMAGE_ROLES:
                seen_image = True
        row_layouts.append(blocks)
    gaps = max(0, len(row_layouts) - 1) * 8.0

    def total_height(scale: float) -> float:
        return sum(
            max(max(block["text_height"], block["image_height"] * scale) for block in row) for row in row_layouts
        ) + gaps

    if total_height(0.25) > available_height + 0.1:
        raise ValueError("raster_page_content_overflow")
    low, high = 0.25, 1.0
    for _ in range(22):
        middle = (low + high) / 2
        if total_height(middle) <= available_height:
            low = middle
        else:
            high = middle
    image_scale = low
    xml = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#f6f9fd"/>',
        f'<rect width="{width}" height="{header}" fill="#123f73"/>',
        f'<path d="M {margin} {height-footer} H {width-margin}" stroke="#bfd4e8" stroke-width="1"/>',
    ]
    remaining_height = max(0.0, available_height - total_height(image_scale))
    cursor = header + margin + (
        min(135.0, remaining_height / 2)
        if analysis.target_canvas.orientation == "portrait" else 0.0
    )
    for row in row_layouts:
        row_height = max(max(block["text_height"], block["image_height"] * image_scale) for block in row)
        for block in row:
            region = block["region"]
            if region.role in TEXT_ROLES:
                padding = block["padding"]
                if padding:
                    xml.append(
                        f'<rect x="{block["x"]:.3f}" y="{cursor:.3f}" '
                        f'width="{block["width"]:.3f}" height="{row_height:.3f}" '
                        f'rx="8" fill="#eaf3fd"/>'
                    )
                baseline = cursor + padding
                for line, font, lead_line in block["text_lines"]:
                    baseline += font
                    xml.append(
                        f'<text x="{block["x"] + padding:.3f}" y="{baseline:.3f}" '
                        f'font-family="Malgun Gothic, Noto Sans CJK KR, sans-serif" '
                        f'font-size="{font:.3f}" font-weight="{"700" if lead_line else "400"}" '
                        f'fill="{"#123f73" if lead_line else "#203b55"}">{escape(line)}</text>'
                    )
                    baseline += font * 0.35
            else:
                image_width = max(1, math.floor(block["width"] * image_scale))
                image_height = max(1, math.floor(block["image_height"] * image_scale))
                image_x = block["x"] + (block["width"] - image_width) / 2
                slot_id = raster_slot_id(page_number, region.region_id)
                xml.append(
                    f'<image x="{image_x:.3f}" y="{cursor:.3f}" '
                    f'width="{image_width}" height="{image_height}" '
                    f'href="{slot_id}" preserveAspectRatio="xMidYMid meet"/>'
                )
        cursor += row_height + 8.0
    if cursor - 8.0 > height - footer - margin + 0.1 or not math.isfinite(cursor):
        raise ValueError("raster_page_content_overflow")
    xml.append("</svg>")
    result = "".join(xml)
    require_raster_physical_readability(result, analysis.target_canvas)
    if len(result.encode("utf-8")) >= 2 * 1024 * 1024:
        raise ValueError("raster_svg_size_limit")
    return result


def raster_page_render_advisories(analysis: RasterAnalysis, page_number: int, svg: str) -> tuple[dict, ...]:
    """Advisories for a rendered page's receipt, e.g. ``dense_group_pixel_fallback``.

    Empty for reconstruct and full-page-preserve pages. Each entry names the
    group, its crop region and the replaced (still approved, pixel-carried)
    region ids, so evidence, the ledger and the UI can count them.
    """
    page = analysis.pages[page_number - 1]
    if getattr(page, "render_mode", "reconstruct") != "redesign":
        return ()
    from services.source_content.raster_redesign_fallback import advisories_from_svg

    from services.source_content.raster_redesign_dedupe import carried_advisories

    return advisories_from_svg(analysis, page, svg) + carried_advisories(page)