"""Validate server-owned review evidence bound to exact source/frame images.

Pixel comparisons are observations, not invented geometry or font metrics.
Approval is explicitly a human review of the rendered fixed regions.
"""
from __future__ import annotations

import re

from models.svg_template_frame import SvgTemplatePackage
from services.svg_templates.frame_fonts import artifact_digest, build_font_manifest, resolve_font_manifest

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def review_image_pair_digest(evidence, item):
    value = {"source_image_digest": item.get("source_image_digest"),
             "frame_image_digest": item.get("frame_image_digest")}
    if evidence.get("schema_version") == "svg-template-review/v3":
        value.update(source_review_domain=evidence.get("source_review_domain"),
            renderer=evidence.get("renderer"), frame_id=item.get("frame_id"),
            frame_digest=item.get("frame_digest"), source_image_origin=item.get("source_image_origin"),
            source_page=item.get("source_page"), source_pdf_digest=item.get("source_pdf_digest"),
            capture_receipt_digest=item.get("capture_receipt_digest"),
            fixed_glyph_preview_digest=item.get("fixed_glyph_preview_digest"),
            source_canvas_digest=item.get("source_canvas_digest"), rasterizer_digest=item.get("rasterizer_digest"),
            raster_scale=item.get("raster_scale"))
    return artifact_digest(value)


def validate_pdf_review_provenance(evidence, item, frame=None):
    renderer = evidence.get("renderer", {})
    rasterizer = renderer.get("rasterizer", {})
    if (evidence.get("schema_version") != "svg-template-review/v3"
            or evidence.get("source_review_domain") != "powerpoint-pdf/pdfium/v1"
            or renderer.get("kind") != "powerpoint-pdf" or not renderer.get("version")
            or not renderer.get("producer_build") or not renderer.get("capture_operation_id")
            or rasterizer.get("kind") != "pdfium" or not rasterizer.get("version")
            or not rasterizer.get("helper_version") or not rasterizer.get("pillow_version")
            or item.get("source_image_origin") != "captured-source-pdf"):
        raise ValueError("svg_template_source_pdf_review_provenance_invalid")
    flags = {"rotation": 0, "crop": [0, 0, 0, 0], "may_draw_forms": False,
             "fill_color": [255, 255, 255, 255], "grayscale": False, "draw_annots": False,
             "optimize_mode": None, "no_smoothtext": False, "no_smoothimage": False,
             "no_smoothpath": False, "force_halftone": False, "limit_image_cache": False,
             "rev_byteorder": False, "prefer_bgrx": False, "maybe_alpha": False,
             "color_scheme": None, "fill_to_stroke": False,
             "extra_flags": 0, "force_bitmap_format": 4}
    encoder = {"format": "PNG", "optimize": False, "compress_level": 6, "icc_profile": None, "pnginfo": None}
    if rasterizer.get("flags") != flags or rasterizer.get("png_encoder") != encoder:
        raise ValueError("svg_template_source_pdf_raster_flags_invalid")
    digests = [renderer.get(k) for k in ("source_pdf_digest", "capture_receipt_digest", "source_canvas_digest")]
    digests += [rasterizer.get(k) for k in ("library_digest", "helper_digest", "code_digest", "bitmap_helper_digest",
                                           "raw_binding_digest", "png_encoder_digest", "image_file_digest", "image_core_digest")]
    digests += [item.get(k) for k in ("source_pdf_digest", "capture_receipt_digest", "source_canvas_digest",
                                     "fixed_glyph_preview_digest", "rasterizer_digest")]
    if any(not isinstance(v, str) or not _DIGEST.fullmatch(v) for v in digests):
        raise ValueError("svg_template_source_pdf_review_provenance_invalid")
    if (renderer.get("environment_digest") != artifact_digest({k: v for k, v in renderer.items() if k != "environment_digest"})
            or item.get("source_environment_digest") != renderer.get("environment_digest")
            or item.get("rasterizer_digest") != artifact_digest(rasterizer)
            or type(item.get("source_page")) is not int or not 0 <= item["source_page"] <= 199
            or type(item.get("raster_scale")) not in (int, float) or not 0 < item["raster_scale"] <= 100
            or any(item.get(k) != renderer.get(k) for k in ("source_pdf_digest", "capture_receipt_digest", "source_canvas_digest"))):
        raise ValueError("svg_template_source_pdf_review_provenance_invalid")
    if frame is not None:
        authority = frame.fixed_glyph_preview
        if (authority is None or item["source_pdf_digest"] != authority.source_pdf_digest
                or item["capture_receipt_digest"] != authority.capture_receipt_digest
                or item["fixed_glyph_preview_digest"] != authority.authority_digest
                or item["source_canvas_digest"] != authority.source_canvas_digest
                or item["source_page"] != frame.source_page
                or renderer["version"] != authority.producer_version
                or renderer["producer_build"] != authority.producer_build
                or evidence.get("source_digest") != authority.source_pptx_digest
                or item["raster_scale"] != frame.source_canvas.width_px / authority.physical_canvas_pt[0]):
            raise ValueError("svg_template_source_pdf_review_authority_mismatch")
    return True


def frame_region_review_digest(frame, *, source_image_digest: str, frame_image_digest: str) -> str:
    return artifact_digest({"frame_digest": frame.digest,
        "regions": [element.bounds.model_dump(mode="json") for element in frame.elements if element.layer == "foreground"],
        "source_image_digest": source_image_digest, "frame_image_digest": frame_image_digest})


def validate_frame_review(package: SvgTemplatePackage | dict, evidence: dict, *,
                          selected_frame_ids: tuple[str, ...]) -> dict:
    package = SvgTemplatePackage.model_validate(package)
    if artifact_digest(package) != package.digest:
        raise ValueError("svg_template_package_digest_mismatch")
    if not selected_frame_ids or len(set(selected_frame_ids)) != len(selected_frame_ids):
        raise ValueError("svg_template_review_selection_invalid")
    frames = {frame.frame_id: frame for frame in package.frames}
    if not set(selected_frame_ids) <= frames.keys():
        raise ValueError("svg_template_review_frame_unknown")
    for frame_id in selected_frame_ids:
        resolve_font_manifest(frames[frame_id].font_manifest)
        if package.schema_version.endswith("/v3"):
            from services.svg_templates.frame_glyphs import validate_frame_glyph_authority
            validate_frame_glyph_authority(frames[frame_id])
    v3 = package.schema_version.endswith("/v3")
    if (evidence.get("schema_version") != ("svg-template-review/v3" if v3 else "svg-template-review/v1")
            or evidence.get("source_digest") != package.source_digest
            or evidence.get("package_digest") != package.digest
            or evidence.get("digest") != artifact_digest({k: v for k, v in evidence.items() if k != "digest"})):
        raise ValueError("svg_template_review_evidence_mismatch")
    renderer = evidence.get("renderer", {})
    if (renderer.get("kind") != ("powerpoint-pdf" if v3 else "powerpoint") or not renderer.get("version")
            or not _DIGEST.fullmatch(renderer.get("environment_digest", ""))
            or renderer.get("font_manifest_digest") != evidence.get("analysis_font_manifest_digest", package.font_manifest.digest)):
        raise ValueError("svg_template_source_render_review_required")
    entries = evidence.get("frames", [])
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("svg_template_review_frames_invalid")
    reviews = {entry.get("frame_id"): entry for entry in entries}
    if len(reviews) != len(entries):
        raise ValueError("svg_template_review_duplicate_frame")
    for frame_id in selected_frame_ids:
        frame, item = frames[frame_id], reviews.get(frame_id, {})
        if v3:
            validate_pdf_review_provenance(evidence, item, frame)
        if any(issue.severity == "blocked" for issue in (*package.issues, *frame.issues)):
            raise ValueError("svg_template_review_unsupported_fixed_element")
        if item.get("frame_digest") != frame.digest:
            raise ValueError("svg_template_review_frame_digest_mismatch")
        if item.get("font_manifest_digest") != frame.font_manifest.digest:
            raise ValueError("svg_template_review_font_manifest_mismatch")
        source_image, frame_image = item.get("source_image_digest", ""), item.get("frame_image_digest", "")
        if not _DIGEST.fullmatch(source_image) or not _DIGEST.fullmatch(frame_image):
            raise ValueError("svg_template_review_image_evidence_missing")
        if item.get("region_review_digest") != frame_region_review_digest(frame,
                source_image_digest=source_image, frame_image_digest=frame_image):
            raise ValueError("svg_template_review_regions_mismatch")
        review = item.get("review", {})
        expected_pair = review_image_pair_digest(evidence, item)
        if (review.get("method") != "human-source-frame-comparison"
                or review.get("classification_approved") is not True
                or review.get("preservation_approved") is not True
                or review.get("reviewed_image_pair_digest") != expected_pair
                or not isinstance(review.get("decision_id"), str) or not review["decision_id"]):
            raise ValueError("svg_template_review_decision_required")
    return {"review_method": "human-source-frame-comparison", "package_digest": package.digest,
            "evidence_digest": evidence["digest"], "selected_frame_ids": list(selected_frame_ids)}


def select_reviewed_package(package: SvgTemplatePackage | dict, selected_frame_ids: tuple[str, ...]) -> SvgTemplatePackage:
    """Materialize an explicit user selection after validating original evidence."""
    package = SvgTemplatePackage.model_validate(package)
    selected = set(selected_frame_ids)
    if not selected or len(selected) != len(selected_frame_ids) or not selected <= {frame.frame_id for frame in package.frames}:
        raise ValueError("svg_template_review_selection_invalid")
    frames = tuple(frame for frame in package.frames if frame.frame_id in selected)
    fonts = {asset.asset_id: asset for frame in frames for asset in frame.font_manifest.assets}
    required = tuple({family for frame in frames for family in frame.font_manifest.required_families})
    manifest = build_font_manifest(tuple(fonts.values()), required_families=required)
    pages = {frame.source_page for frame in frames}
    result = package.model_copy(update={"frames": frames, "font_manifest": manifest,
        "body_evidence": tuple(item for item in package.body_evidence if item.source_page in pages), "digest": ""})
    return result.model_copy(update={"digest": artifact_digest(result)})
