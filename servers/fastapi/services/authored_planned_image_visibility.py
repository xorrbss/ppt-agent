"""Read-only ordinary-image receipt from the final, immutable capture.

Geometry is necessary evidence, never a substitute for semantic/visual review.
Strict source-content visibility deliberately does not use this module.
"""

import hashlib
import math
from collections.abc import Mapping, Sequence

from utils.authored_illustrations import _data_image_digest


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def inspect_planned_source_images(
    *, html: str, metrics: Mapping | None, assignments: Sequence[Mapping],
    candidates: Mapping, visual_review: Mapping | None = None,
) -> dict:
    """Verify assigned assets and bounded overlap; unmeasured evidence fails closed.

    ``visual_review`` must identify this HTML digest and explicitly affirm
    relevance, critical-region visibility and readability for every asset.
    """
    digest = "sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest()
    issues = []
    receipt = {"html_digest": digest, "geometry_passed": False,
               "visual_passed": False, "passed": False, "issues": issues,
               "status": "review_required"}
    if not assignments:
        receipt.update(status="not_applicable")
        return receipt
    if (not isinstance(metrics, Mapping) or metrics.get("source_digest") != digest
            or metrics.get("fonts_ready") is not True
            or not isinstance(metrics.get("elements"), list)):
        issues.append({"code": "measurement_missing_or_stale"})
        return receipt
    elements = [item for item in metrics["elements"] if isinstance(item, Mapping)]
    candidate_by_id = {str(item.asset_id): item for item in candidates.values()}
    assignment_by_id = {str(item.get("asset_id")): item for item in assignments}
    qid_to_asset = {}
    matches = {}
    for asset_id in assignment_by_id:
        candidate = candidate_by_id.get(asset_id)
        expected = candidate.digest.removeprefix("sha256:").lower() if candidate else None
        found = [item for item in elements if expected and item.get("tag") == "img"
                 and isinstance(item.get("image"), Mapping)
                 and _data_image_digest(item["image"].get("src")) == expected]
        matches[asset_id] = found
        for item in found:
            qid_to_asset[str(item.get("qid"))] = asset_id
        if not found:
            issues.append({"asset_id": asset_id, "code": "assigned_image_missing"})
    for asset_id, found in matches.items():
        assignment = assignment_by_id[asset_id]
        # A detail crop is meaningful only alongside a fully measured anchor of
        # the same original. Never let several fragments replace the original.
        anchors = [item for item in found if (
            (_number(item["image"].get("viewportIntersectionRatio")) or 0) >= .99
            and (item.get("occlusion_measurement_evidence") or {}).get("status") == "measured"
            and _number(item.get("occlusion_unknown_fraction")) == 0
            and (_number(item.get("occluded_fraction")) is not None)
            and _number(item.get("occluded_fraction")) < .95
            and item["image"].get("complete") is True
            and item["image"].get("cssVisible") is True
            and (_number(item["image"].get("effectiveOpacity")) or 0) >= .95
        )]
        if found and not anchors:
            issues.append({"asset_id": asset_id, "code": "full_image_anchor_missing"})
        for item in found:
            image = item["image"]
            box = item.get("bbox") or {}
            width, height = _number(box.get("width")), _number(box.get("height"))
            opacity = _number(image.get("effectiveOpacity"))
            intersection = _number(image.get("viewportIntersectionRatio"))
            occluded = _number(item.get("occluded_fraction"))
            evidence = item.get("occlusion_measurement_evidence") or {}
            unknown = _number(item.get("occlusion_unknown_fraction"))
            detail = len(found) > 1 and bool(anchors) and item not in anchors
            # Partial clones still need real Chrome paint evidence and at least
            # a 16px visible viewport intersection. VQA checks the crop's actual
            # informational usefulness; unmeasured/hidden clones fail closed.
            viewport_width, viewport_height = _number(metrics.get("width")), _number(metrics.get("height"))
            x, y = _number(box.get("x")), _number(box.get("y"))
            visible_width = (min(x + width, viewport_width) - max(x, 0)
                             if None not in (x, width, viewport_width) else 0)
            visible_height = (min(y + height, viewport_height) - max(y, 0)
                              if None not in (y, height, viewport_height) else 0)
            detail_measured = (detail and visible_width >= 16 and visible_height >= 16
                               and intersection is not None and intersection > 0
                               and unknown is not None and 0 <= unknown < .95)
            if (image.get("complete") is not True or image.get("cssVisible") is not True
                    or not (_number(image.get("naturalWidth")) or 0) > 0
                    or not (_number(image.get("naturalHeight")) or 0) > 0
                    or width is None or height is None or min(width, height) < 16
                    or opacity is None or opacity < .95 or intersection is None
                    or (intersection < .99 and not detail_measured) or evidence.get("status") != "measured"
                    or occluded is None or occluded >= .95
                    or (unknown != 0 and not detail_measured)):
                issues.append({"asset_id": asset_id, "code": "visibility_unproven"})
                continue
            if occluded > 0:
                occluders = item.get("occlusion_occluder_qids") or []
                allowed = bool(assignment.get("group_id") and assignment.get("allow_overlap") and occluders)
                for qid in occluders:
                    other = assignment_by_id.get(qid_to_asset.get(str(qid)), {})
                    allowed = allowed and bool(other.get("allow_overlap") and
                                               other.get("group_id") == assignment.get("group_id"))
                if not allowed:
                    issues.append({"asset_id": asset_id, "code": "unplanned_occlusion"})
    for item in elements:
        if item.get("has_owned_text") is True and any(
            str(qid) in qid_to_asset for qid in item.get("occlusion_occluder_qids", ())
        ):
            issues.append({"code": "image_covers_text", "qid": item.get("qid")})
    receipt["geometry_passed"] = not issues
    reviews = (visual_review or {}).get("assets", {})
    receipt["visual_passed"] = bool(
        (visual_review or {}).get("html_digest") == digest
        and all(isinstance(reviews.get(asset), Mapping) and all(
            reviews[asset].get(key) is True
            for key in ("relevant", "critical_regions_visible", "readable")
        ) for asset in assignment_by_id)
    )
    if not receipt["visual_passed"]:
        issues.append({"code": "visual_review_missing_or_failed"})
    receipt["passed"] = receipt["geometry_passed"] and receipt["visual_passed"]
    receipt["status"] = "used_verified" if receipt["passed"] else "review_required"
    return receipt
