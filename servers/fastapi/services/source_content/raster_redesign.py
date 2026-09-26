"""Automatic redesign analysis of image-only PPTX sources.

Every page becomes editable title/body/table regions plus crops for what can
only be pixels (application captures, icons). The complete page image is kept
as ``reference_region`` for audit and fallback only. Human review is required
only for regions whose measured confidence is low.
"""

from __future__ import annotations

import io
import os
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from services.source_content.raster_analysis import (
    EmbeddedRasterPage,
    RasterDraft,
    _emu_box,
    _overlap,
    _pixel_sha256,
    _sha256,
    extract_embedded_raster_pages,
)
from services.source_content.raster_contracts import seal_analysis
from services.source_content.raster_llm_structure import VisionJson
from services.source_content.raster_page_plan import PagePlan
from services.source_content.raster_page_fallback import robust_plan
from services.source_content.raster_redesign_ocr import redesign_ocr
from services.source_content.raster_stage_timing import page_task, stage
from services.source_content.raster_table_overlay import expand_overlays

PIXEL_ROLES = frozenset({"screenshot", "logo", "decoration"})
CROP_ROLES = frozenset({"screenshot", "logo", "table", "decoration"})
REDESIGN_REASON = "image_only_source_redesign"
# Non-blocking review suggestions in analysis.unresolved; approval ignores them.
ADVISORY_PREFIX = "advisory:"
# Deck-level: no vision structurer configured, so no page text can be independently confirmed.
STRUCTURER_UNAVAILABLE = f"{ADVISORY_PREFIX}vision_structurer_unavailable"


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _page_result(
    page: EmbeddedRasterPage,
    slide_size: tuple[int, int],
    *,
    tesseract_path: Path,
    tessdata_path: Path,
    structurer: VisionJson | None = None,
) -> tuple[dict, dict[str, bytes], tuple[str, ...]]:
    image = page.image
    media_digest = _sha256(page.media_bytes)
    issues: list[str] = []
    try:
        if not tesseract_path.is_file() or not tessdata_path.is_dir():
            raise RuntimeError("local_ocr_unavailable")
        words, lines = redesign_ocr(image, executable=tesseract_path, tessdata=tessdata_path)
        with stage(None, "plan"):
            plan, failures = robust_plan(image, words, lines, structurer, tesseract_path, tessdata_path)
        issues.extend(f"{ADVISORY_PREFIX}page_{page.page_number}:{failure}" for failure in failures)
        words = plan.words or words
        issues.extend(f"{ADVISORY_PREFIX}page_{page.page_number}:corrected:{entry}" for entry in plan.corrections)
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        # Without OCR nothing can be redesigned; the page blocks approval.
        words, plan = (), PagePlan((), ())
        issues.append(f"page_{page.page_number}:redesign_ocr_unavailable")
    lineage = {
        "page_number": page.page_number,
        "source_occurrence_id": page.occurrence_id,
        "source_media_part": page.media_part,
        "crop_source_sha256": media_digest,
    }
    crops: dict[str, bytes] = {}
    # A native table's pixel cells become crop regions right after it (raster_table_overlay).
    entries = expand_overlays(plan.regions, page.page_number, words)
    ids = [entry[0] for entry in entries]
    planned_regions = [entry[1] for entry in entries]
    # Pixel roles below text; a table's pixel cell above its table.
    stacking = sorted(range(len(entries)), key=lambda i: (
        2 if entries[i][3] else int(planned_regions[i].role not in PIXEL_ROLES), i))
    z_order = {index: position for position, index in enumerate(stacking)}
    regions: list[dict] = []
    for index, (region_id, planned, pixel_cells, _) in enumerate(entries):
        x, y, width, height = planned.bbox
        crop = image.crop((x, y, x + width, y + height))
        if planned.role in CROP_ROLES:
            crops[region_id] = _png(crop)
        if planned.review_reason is not None:
            # Advisory only: the region already renders losslessly as pixels.
            issues.append(f"{ADVISORY_PREFIX}page_{page.page_number}:{region_id}:{planned.review_reason}")
        regions.append(
            {
                **lineage,
                "region_id": region_id,
                "role": planned.role,
                "bbox": _emu_box(planned.bbox, image, slide_size),
                "z_order": z_order[index],
                "text": planned.text if planned.role in ("title", "body") or planned.editable_fallback else None,
                "source_pixel_bbox": planned.bbox,
                "pixel_sha256": _pixel_sha256(crop),
                "overlaps": tuple(
                    ids[j] for j, other in enumerate(planned_regions)
                    if j != index and _overlap(planned.bbox, other.bbox)
                ),
                "review_required": False,
                "editable_fallback": planned.editable_fallback,
                "confidence": max(0.0, min(1.0, planned.confidence)),
                "reading_order": index,
                "table_cells": planned.table_cells,
                "review_reason": planned.review_reason,
                "ocr_token_indices": planned.word_indices,
                "group_id": planned.group_id,
                "heading_line_count": planned.heading_line_count,
                "table_pixel_cells": pixel_cells,
            }
        )
    reference_id = f"p{page.page_number}-source-reference"
    crops[reference_id] = _png(image)
    reference = {
        **lineage,
        "region_id": reference_id,
        "role": "source_reference",
        "bbox": {"x": 0, "y": 0, "width": slide_size[0], "height": slide_size[1]},
        "z_order": 0,
        "source_pixel_bbox": (0, 0, image.width, image.height),
        "pixel_sha256": _pixel_sha256(image),
        "review_required": False,
    }
    inventory = [
        {
            "text": word.text,
            "bbox": _emu_box(word.bbox, image, slide_size),
            "confidence": word.confidence,
            "engine": word.engine,
            "source_sha256": media_digest,
            "review_required": False,
        }
        for index, word in enumerate(words)
    ]
    result = {
        "page_number": page.page_number,
        "regions": regions,
        "ocr_inventory": inventory,
        "render_mode": "redesign",
        "approval_method": "automatic_redesign",
        "reference_region": reference,
        "unpromoted_ocr_indices": plan.unpromoted,
        "recommended_render_mode": "redesign",
        "recommendation_reason": REDESIGN_REASON,
    }
    return result, crops, tuple(issues)


def analyze_redesign(
    source_path: Path,
    *,
    orientation: str,
    tesseract_path: Path,
    tessdata_path: Path,
    max_workers: int | None = None,
    structurer: VisionJson | None = None,
) -> RasterDraft:
    if orientation not in {"portrait", "landscape"}:
        raise ValueError("raster_target_orientation_invalid")
    source_sha, width_emu, height_emu, embedded = extract_embedded_raster_pages(source_path)
    workers = max(1, min(len(embedded), max_workers or min(12, os.cpu_count() or 2)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="raster-redesign") as pool:
        results = list(
            pool.map(
                # A copy of this context per page task: pool threads do not inherit it.
                page_task(
                    lambda page: _page_result(
                        page,
                        (width_emu, height_emu),
                        tesseract_path=tesseract_path,
                        tessdata_path=tessdata_path,
                        structurer=structurer,
                    )
                ),
                embedded,
            )
        )
    pages, crops, issues = [], {}, []
    if structurer is None:
        # Every page is planned without the vision reader: said once for the deck.
        issues.append(STRUCTURER_UNAVAILABLE)
    for page_result, page_crops, page_issues in results:
        pages.append(page_result)
        crops.update(page_crops)
        issues.extend(page_issues)
    target_size = (840, 1188) if orientation == "portrait" else (1188, 840)
    analysis = seal_analysis(
        {
            "analysis_id": uuid.uuid5(uuid.NAMESPACE_URL, f"raster-redesign:{source_sha}:{orientation}:v1"),
            "source": {
                "source_sha256": source_sha,
                "page_count": len(embedded),
                "canvas_width_emu": width_emu,
                "canvas_height_emu": height_emu,
            },
            "target_canvas": {
                "orientation": orientation,
                "width_units": target_size[0],
                "height_units": target_size[1],
            },
            "pages": pages,
            "unresolved": tuple(issues),
        }
    )
    return RasterDraft(analysis=analysis, crop_png_by_region=crops)
