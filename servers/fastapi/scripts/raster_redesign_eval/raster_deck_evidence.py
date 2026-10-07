"""Product-side evidence fetch for the driver (generate_raster_deck.py):
pulls each slide's `content.svgReceipt.rasterRenderAdvisories` so callers can
tell (for example) which slides the product itself fell back to a
page-content raster for, independent of what the exported PPTX looks like.

Split out of the original generate_raster_deck.py (2026-09-25 eval suite) to
keep each module under the repo's file-size limit; behavior is unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

from raster_deck_http import request as _request
from models.document_canvas import (
    DOCUMENT_CANVAS_CONTRACT_VERSION,
    DOCUMENT_CANVAS_CONTRACTS_HEADER,
)


def fetch_slide_evidence(base: str, presentation_id: str, out_dir: Path) -> dict:
    """Pull GET /presentation/{id} and check each slide's content.svgReceipt
    for the rasterRenderAdvisories field."""
    # A4/document-canvas decks require the reader to negotiate the contract
    # version via this header, or the endpoint refuses with 409
    # document_canvas_client_upgrade_required.
    presentation = _request(
        base, "GET", f"/api/v1/ppt/presentation/{presentation_id}",
        headers={DOCUMENT_CANVAS_CONTRACTS_HEADER: DOCUMENT_CANVAS_CONTRACT_VERSION},
    )
    slides = presentation.get("slides", [])
    per_slide = []
    for slide in slides:
        content = slide.get("content") or {}
        receipt = content.get("svgReceipt") if isinstance(content, dict) else None
        has_field = isinstance(receipt, dict) and "rasterRenderAdvisories" in receipt
        advisories = receipt.get("rasterRenderAdvisories") if has_field else None
        by_code: dict = {}
        by_group: dict = {}
        if isinstance(advisories, list):
            for item in advisories:
                if not isinstance(item, dict):
                    continue
                code = item.get("code", "?")
                group = item.get("groupId", "?")
                by_code[code] = by_code.get(code, 0) + 1
                by_group[group] = by_group.get(group, 0) + 1
        per_slide.append({
            "index": slide.get("index"),
            "has_svg_receipt": receipt is not None,
            "has_raster_render_advisories_field": has_field,
            "raster_render_advisories_count": len(advisories) if isinstance(advisories, list) else None,
            "advisories_by_code": by_code,
            "advisories_by_group_id": by_group,
            "has_page_content_fallback": "page-content" in by_group,
        })
    report = {
        "presentation_id": presentation_id,
        "slide_count": len(slides),
        "slides_with_field": sum(1 for s in per_slide if s["has_raster_render_advisories_field"]),
        "slides": per_slide,
    }
    (out_dir / "slide_evidence_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[evidence] rasterRenderAdvisories field present on "
          f"{report['slides_with_field']}/{report['slide_count']} slides")
    return report
