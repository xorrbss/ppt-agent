"""Per-slide structural checks: canvas size, editable-text stats, native
tables, and picture-vs-source-page hash matching (full-page-copy /
page-content-crop detection).

Split out of the original checker.py (2026-09-25 eval suite) to keep each
module under the repo's file-size limit; scoring logic is unchanged.
"""
from __future__ import annotations

from imghash import ahash_bits, best_bottom_crop_match, hamming

EMU_PER_MM = 36000
A4_LANDSCAPE_MM = (297.0, 210.0)
A4_PORTRAIT_MM = (210.0, 297.0)
SIZE_TOLERANCE = 0.02  # 2%
MIN_FONT_PT = 9.5
# Tight: a genuine full-page copy of the identical source image hashes at
# distance 0 (confirmed against a known-bad negative control). A "page-content"
# crop (source content area below the title, kept as one picture -- see
# raster_redesign_fallback.PAGE_CONTENT) still shares most pixels with the full
# page and can land at distance ~15-45, so this must stay well below that to
# not misclassify it as a full-page copy.
FULL_PAGE_HASH_MAX_DIST = 10  # out of 256 bits (~96% similar)
# A "full source page" copy can still be inset inside a decorative frame/margin,
# so this only guards against classifying a small crop (logo, chart) as a full
# page; the real discriminator is the hash distance to the whole source page.
FULL_PAGE_AREA_MIN_FRACTION = 0.50
# "page-content" crop: bottom-crop of the source page (below the title band)
# kept as one picture. Distinguished from a full-page copy by comparing against
# cropped variants of the source page, not just the whole page.
CONTENT_CROP_HASH_MAX_DIST = 24
CONTENT_CROP_AREA_MIN_FRACTION = 0.30
LOW_RECALL_THRESHOLD = 0.70

PICTURE_SHAPE_TYPE = 13


def _text_frame_runs(text_frame):
    for para in text_frame.paragraphs:
        for run in para.runs:
            yield run


def slide_editable_text_stats(slide) -> dict:
    """2026-09-25 fix: native PPTX table cell text now counts toward
    `chars` (and `text_shapes`/`font_sizes_pt`/`runs_without_explicit_size`)
    the same as any other text shape -- it used to be silently excluded
    here (while `slide_text()` in checker_text.py, used for char recall/
    precision, already included it), which hid a native table's real cost
    or gain from every `editable_text_ratio_*` metric that reads this
    function's `chars`."""
    text_shapes = 0
    chars = 0
    font_sizes_pt = []
    runs_without_explicit_size = 0

    def _count_runs(runs) -> bool:
        nonlocal chars, runs_without_explicit_size
        has_text = False
        for run in runs:
            if run.text and run.text.strip():
                has_text = True
                chars += len(run.text)
                try:
                    size = run.font.size
                except Exception:
                    size = None
                if size is not None:
                    font_sizes_pt.append(size.pt)
                else:
                    runs_without_explicit_size += 1
        return has_text

    for shape in slide.shapes:
        if shape.has_text_frame:
            if _count_runs(_text_frame_runs(shape.text_frame)):
                text_shapes += 1
        if getattr(shape, "has_table", False):
            table_has_text = False
            for row in shape.table.rows:
                for cell in row.cells:
                    if _count_runs(_text_frame_runs(cell.text_frame)):
                        table_has_text = True
            if table_has_text:
                text_shapes += 1
    return {
        "text_shapes": text_shapes,
        "chars": chars,
        "font_sizes_pt": font_sizes_pt,
        "runs_without_explicit_size": runs_without_explicit_size,
    }


def slide_tables(slide) -> list[dict]:
    tables = []
    for shape in slide.shapes:
        if getattr(shape, "has_table", False):
            table = shape.table
            rows = len(table.rows)
            cols = len(table.columns)
            cell_chars = sum(len(cell.text) for row in table.rows for cell in row.cells)
            tables.append({"rows": rows, "cols": cols, "cell_chars": cell_chars})
    return tables


def slide_pictures(slide, slide_w_emu: int, slide_h_emu: int, source_hashes: list,
                    source_page_paths: dict):
    results = []
    slide_area = max(slide_w_emu * slide_h_emu, 1)
    for shape in slide.shapes:
        if shape.shape_type != PICTURE_SHAPE_TYPE:
            continue
        try:
            blob = shape.image.blob
            pixel_w, pixel_h = shape.image.size
        except Exception as exc:  # noqa: BLE001
            results.append({"error": str(exc)})
            continue
        area_fraction = (shape.width * shape.height) / slide_area
        bits = ahash_bits(blob)
        best_dist = None
        best_page = None
        for page_num, src_bits in source_hashes:
            d = hamming(bits, src_bits)
            if best_dist is None or d < best_dist:
                best_dist = d
                best_page = page_num
        is_full_page_copy = (
            best_dist is not None
            and best_dist <= FULL_PAGE_HASH_MAX_DIST
            and area_fraction >= FULL_PAGE_AREA_MIN_FRACTION
        )
        content_crop = None
        if not is_full_page_copy and best_page is not None and area_fraction >= CONTENT_CROP_AREA_MIN_FRACTION:
            crop_dist, top_fraction = best_bottom_crop_match(bits, source_page_paths[best_page])
            if crop_dist <= CONTENT_CROP_HASH_MAX_DIST:
                content_crop = {
                    "matched_source_page": best_page,
                    "hash_distance": crop_dist,
                    "estimated_source_top_fraction_excluded": round(top_fraction, 3),
                    "estimated_source_area_fraction_covered": round(1 - top_fraction, 3),
                }
        results.append(
            {
                "pixel_size": [pixel_w, pixel_h],
                "emu_size": [shape.width, shape.height],
                "area_fraction_of_slide": area_fraction,
                "best_hash_distance": best_dist,
                "best_matching_source_page": best_page,
                "is_full_page_copy": is_full_page_copy,
                "is_page_content_crop": content_crop is not None,
                "page_content_crop": content_crop,
            }
        )
    return results


def check_canvas_size(width_emu: int, height_emu: int) -> dict:
    width_mm = width_emu / EMU_PER_MM
    height_mm = height_emu / EMU_PER_MM
    target_w, target_h = A4_LANDSCAPE_MM
    ok = (
        abs(width_mm - target_w) / target_w <= SIZE_TOLERANCE
        and abs(height_mm - target_h) / target_h <= SIZE_TOLERANCE
    )
    return {
        "width_mm": round(width_mm, 2),
        "height_mm": round(height_mm, 2),
        "is_a4_landscape": ok,
    }
