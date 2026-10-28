"""Validate server-owned review evidence bound to exact source/frame images.

Pixel comparisons are observations, not invented geometry or font metrics.
Approval is explicitly a human review of the rendered fixed regions.
"""
from __future__ import annotations

import re

from models.svg_template_frame import SvgTemplatePackage
from services.svg_templates.frame_fonts import artifact_digest, build_font_manifest, resolve_font_manifest

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


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
    if (evidence.get("schema_version") != "svg-template-review/v1"
            or evidence.get("source_digest") != package.source_digest
            or evidence.get("package_digest") != package.digest
            or evidence.get("digest") != artifact_digest({k: v for k, v in evidence.items() if k != "digest"})):
        raise ValueError("svg_template_review_evidence_mismatch")
    renderer = evidence.get("renderer", {})
    if (renderer.get("kind") != "powerpoint" or not renderer.get("version")
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
        expected_pair = artifact_digest({"source_image_digest": source_image, "frame_image_digest": frame_image})
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
