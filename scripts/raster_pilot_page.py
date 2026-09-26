"""Build one reviewed A4 SVG page and its evidence receipt."""

from __future__ import annotations

import base64
import io
from pathlib import Path
import re

from PIL import Image

if __package__:
    from .raster_pilot_media import box, digest, json_file, pixel_digest
    from .raster_pilot_render import (
        IMAGE_ROLES,
        ROLES,
        TEXT_ROLES,
        classify_token,
        ocr_tokens,
        svg_cell_background,
        svg_number,
        svg_text,
        text_box_fit_estimate,
    )
else:
    from raster_pilot_media import box, digest, json_file, pixel_digest
    from raster_pilot_render import (
        IMAGE_ROLES,
        ROLES,
        TEXT_ROLES,
        classify_token,
        ocr_tokens,
        svg_cell_background,
        svg_number,
        svg_text,
        text_box_fit_estimate,
    )

SCHEMA = "raster-pilot/v1"
CANVASES = {"landscape": (1188, 840), "portrait": (840, 1188)}


def build_page(
    page: dict,
    source_image: Image.Image,
    source_entry: str,
    source_blob: bytes,
    source_sha: str,
    source_shape: dict,
    output: Path,
    *,
    orientation: str,
    command: Path,
    tessdata: Path,
    whole_timeout: float,
    crop_timeout: float,
) -> tuple[dict, list[dict]]:
    page_width, page_height = CANVASES[orientation]
    number = page.get("page")
    if not isinstance(number, int) or number <= 0:
        raise ValueError("page must be a positive slide number")
    regions = page.get("regions")
    if not isinstance(regions, list) or not regions:
        raise ValueError(f"page {number}: regions must be a nonempty list")
    seen: set[str] = set()
    validated: list[dict] = []
    for raw in regions:
        if not isinstance(raw, dict) or raw.get("role") not in ROLES:
            raise ValueError(f"page {number}: unsupported region role")
        name = raw.get("id")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in seen:
            raise ValueError(f"page {number}: region IDs must be unique and simple")
        seen.add(name)
        region = dict(raw)
        if not isinstance(region.get("semantic_role"), str) or not region["semantic_role"].strip():
            raise ValueError(f"page {number} {name}: semantic_role label is required")
        region["bbox_px"] = box(
            raw.get("bbox_px"),
            f"page {number} {name} bbox_px",
            source_image.size,
            integer=True,
        )
        if region["role"] != "ignored_decoration":
            region["target_box"] = box(
                raw.get("target_box"),
                f"page {number} {name} target_box",
                (page_width, page_height),
                integer=False,
            )
        if region["role"] in IMAGE_ROLES:
            area = region["bbox_px"][2] * region["bbox_px"][3]
            if area >= source_image.width * source_image.height * 0.90:
                raise ValueError(f"page {number} {name}: near-full-page raster is not a reconstructed region")
        validated.append(region)

    whole = ocr_tokens(source_image, command=command, tessdata=tessdata, timeout=whole_timeout, psm=11)
    inventory = []
    counts = {
        key: 0
        for key in (
            "uncovered",
            "partial_overlap",
            "ambiguous",
            "outside_text_region",
            "covered_image",
            "ignored_decoration",
        )
    }
    for token in whole:
        category, ids = classify_token(token, validated)
        counts[category] += 1
        inventory.append({**token, "category": category, "region_ids": ids})
    if not whole:
        raise RuntimeError(f"page {number}: whole-page OCR returned no tokens")

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{page_width}" height="{page_height}" viewBox="0 0 {page_width} {page_height}">',  # noqa: E501
        f'<rect x="0" y="0" width="{page_width}" height="{page_height}" fill="#ffffff"/>',
    ]
    receipts = []
    review = []
    background_parts = []
    image_parts = []
    text_parts = []
    for region in validated:
        rx, ry, rw, rh = region["bbox_px"]
        crop = source_image.crop((rx, ry, rx + rw, ry + rh))
        png = io.BytesIO()
        crop.save(png, format="PNG")
        crop_bytes = png.getvalue()
        receipt = {
            "id": region["id"],
            "role": region["role"],
            "semantic_role": region.get("semantic_role"),
            "source_bbox_px": region["bbox_px"],
            "target_box": region.get("target_box"),
            "source_image_entry": source_entry,
            "source_image_sha256": digest(source_blob),
            "source_page_pixel_sha256": source_sha,
            "crop_png_sha256": digest(crop_bytes),
            "crop_pixel_sha256": pixel_digest(crop),
        }
        if region["role"] in IMAGE_ROLES:
            tx, ty, tw, th = region["target_box"]
            slot_w, slot_h = max(1, int(round(tw))), max(1, int(round(th)))
            slot_x, slot_y = tx + (tw - slot_w) / 2, ty + (th - slot_h) / 2
            fit_scale = min(slot_w / rw, slot_h / rh)
            fit_w, fit_h = rw * fit_scale, rh * fit_scale
            fit_x, fit_y = slot_x + (slot_w - fit_w) / 2, slot_y + (slot_h - fit_h) / 2
            href = "data:image/png;base64," + base64.b64encode(crop_bytes).decode("ascii")
            image_parts.append(
                f'<image x="{svg_number(slot_x)}" y="{svg_number(slot_y)}" width="{slot_w}" height="{slot_h}" preserveAspectRatio="xMidYMid meet" href="{href}"/>'  # noqa: E501
            )
            receipt["render_kind"] = "embedded_lossless_crop"
            receipt["svg_image_slot_box"] = [slot_x, slot_y, slot_w, slot_h]
            receipt["effective_render_box"] = [fit_x, fit_y, fit_w, fit_h]
            receipt["fit_policy"] = "aspect_preserving_contain_no_upstream_crop"
        elif region["role"] in TEXT_ROLES:
            if region["role"] == "table_cell":
                background_parts.append(svg_cell_background(region))
            crop_tokens = ocr_tokens(crop, command=command, tessdata=tessdata, timeout=crop_timeout, psm=6)
            candidate = " ".join(token["text"] for token in crop_tokens)
            authority = region.get("review_authority")
            reviewed = region.get("reviewed_text")
            reference = region.get("reference_text")
            if reviewed is not None and (not isinstance(reviewed, str) or authority != "user"):
                raise ValueError("reviewed_text requires review_authority=user")
            if reference is not None and (not isinstance(reference, str) or authority != "agent_visual"):
                raise ValueError("reference_text requires review_authority=agent_visual")
            rendered = reviewed if reviewed is not None else reference if reference is not None else candidate
            origin = (
                "user_reviewed"
                if reviewed is not None
                else "agent_visual_reference"
                if reference is not None
                else "crop_ocr_candidate"
            )
            needs_review = origin != "user_reviewed"
            text_parts.extend(svg_text(region, rendered))
            fit = text_box_fit_estimate(region, rendered)
            receipt.update(
                {
                    "render_kind": "editable_svg_text",
                    "text_origin": origin,
                    "needs_review": needs_review,
                    "crop_ocr_token_count": len(crop_tokens),
                    "rendered_text_sha256": digest(rendered.encode("utf-8")),
                    **fit,
                }
            )
            review.append(
                {
                    "page": number,
                    "region_id": region["id"],
                    "role": region["role"],
                    "candidate_text": candidate,
                    "reference_text": reference,
                    "reviewed_text": reviewed,
                    "rendered_text": rendered,
                    "text_origin": origin,
                    "review_authority": authority,
                    "needs_review": needs_review,
                    "crop_ocr_tokens": crop_tokens,
                    "source_bbox_px": region["bbox_px"],
                    **fit,
                }
            )
        else:
            receipt["render_kind"] = "ignored_decoration_annotation"
            receipt["needs_review"] = True
        receipts.append(receipt)
    parts += background_parts + image_parts + text_parts + ["</svg>"]
    svg_path = output / f"page-{number:03d}.svg"
    svg_path.write_text("\n".join(parts), encoding="utf-8")
    page_receipt = {
        "version": SCHEMA,
        "page": number,
        "svg_file": svg_path.name,
        "svg_sha256": digest(svg_path.read_bytes()),
        "source_image_entry": source_entry,
        "source_image_sha256": digest(source_blob),
        "source_page_pixel_sha256": source_sha,
        "source_shape": source_shape,
        "source_size_px": list(source_image.size),
        "target_canvas": {
            "width": page_width,
            "height": page_height,
            "orientation": orientation,
            "unit": "9000_EMU",
        },
        "whole_page_ocr": {
            "engine": "tesseract_kor+eng_psm11",
            "independent_of_crop_ocr": True,
            "same_engine_as_crop_ocr": True,
            "not_completeness_proof": True,
            "tokens": inventory,
            "category_counts": counts,
        },
        "regions": receipts,
        "review_required": not review
        or any(
            item["needs_review"] or item["vertical_overflow_estimate"] or item["horizontal_overflow_estimate"]
            for item in review
        )
        or any(item.get("needs_review", False) for item in receipts)
        or counts["uncovered"] > 0
        or counts["partial_overlap"] > 0
        or counts["ambiguous"] > 0
        or counts["ignored_decoration"] > 0,
        "acceptance": "pilot_evidence_only",
    }
    json_file(output / f"page-{number:03d}.receipt.json", page_receipt)
    return page_receipt, review
