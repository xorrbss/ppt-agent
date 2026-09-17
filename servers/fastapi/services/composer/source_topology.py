"""Evidence for source-preserved image-under-text paint order in v2 geometry.

Only image targets may be reconciled. Every measured occluder must be source
text or a trusted, exactly materialized source image already later in the
original paint order. Source boxes remain unchanged. Unknown owners, changed
geometry or image bytes, and text victims stay blocking. Each original issue
is retained as an explicit witness.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import replace
from hashlib import sha256
import math
from typing import Any

from lxml import html as lxml_html

from services.composer.canvas import declared_canvas_dimensions
from services.composer.contracts import Measurement
from services.composer.plan import ComposerComponent, ComposerSlidePlan

_EPSILON_PX = 0.75


def _box(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        box = tuple(float(value[key]) for key in ("x", "y", "width", "height"))
    except (TypeError, ValueError, KeyError):
        return None
    return box if all(math.isfinite(v) for v in box) and box[2] > 0 and box[3] > 0 else None


def _original_box(component: ComposerComponent, canvas_width: int = 1280, canvas_height: int = 720) -> tuple[float, float, float, float] | None:
    rect, geometry = component.source_rect, component.source_transform
    if rect is None or geometry.get("rotationDegrees"):
        return None
    try:
        page_width = float(geometry["cx"]) / rect.width
        page_height = float(geometry["cy"]) / rect.height
        if page_width <= 0 or page_height <= 0:
            return None
        scale = min(canvas_width / page_width, canvas_height / page_height)
        width, height = page_width * scale, page_height * scale
        expected = ((canvas_width - width) / 2 + rect.x * width, (canvas_height - height) / 2 + rect.y * height,
                    rect.width * width, rect.height * height)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    return expected if all(math.isfinite(v) for v in expected) else None


def _same(left, right) -> bool:
    return bool(left and right and all(abs(a - b) <= _EPSILON_PX for a, b in zip(left, right)))


def _inside(inner, outer) -> bool:
    return bool(inner and outer and inner[0] >= outer[0] - _EPSILON_PX
                and inner[1] >= outer[1] - _EPSILON_PX
                and inner[0] + inner[2] <= outer[0] + outer[2] + _EPSILON_PX
                and inner[1] + inner[3] <= outer[1] + outer[3] + _EPSILON_PX)


def _intersects(left, right) -> bool:
    return bool(left and right and min(left[0] + left[2], right[0] + right[2]) > max(left[0], right[0])
                and min(left[1] + left[3], right[1] + right[3]) > max(left[1], right[1]))


def reconcile_source_image_overlaps(html: str, plan: ComposerSlidePlan, measurement: Measurement) -> Measurement:
    canvas_width, canvas_height = declared_canvas_dimensions(html)
    if (measurement.metrics.get("width"), measurement.metrics.get("height")) != (canvas_width, canvas_height):
        return measurement
    if not measurement.telemetry_available or not measurement.blocking_issues or not plan.source_placements:
        return measurement
    digest = "sha256:" + sha256(html.encode("utf-8")).hexdigest()
    if measurement.metrics.get("source_digest") != digest:
        return measurement
    try:
        document = lxml_html.document_fromstring(html)
    except (ValueError, TypeError):
        return measurement
    bodies = document.xpath('//body[@data-source-fit-policy="composer-source-fit/v2"]')
    if len(bodies) != 1:
        return measurement
    nodes = {node.get("data-qid"): node for node in bodies[0].xpath('.//*[@data-qid]')}
    elements = {element.get("qid"): element for element in measurement.metrics.get("elements", []) if isinstance(element, dict)}
    components = {component.component_id: component for component in plan.source_placements}
    bindings = {paragraph.source.binding_id: component for component in plan.source_placements for paragraph in component.paragraphs}

    def trusted_image(qid: str, component: ComposerComponent) -> bool:
        proof = plan.source_image_materializations.get(component.component_id)
        node = nodes.get(qid)
        if not isinstance(proof, dict) or node is None or node.tag != "img":
            return False
        if proof.get("assetId") != component.asset_id or node.get("data-source-block-id") != component.component_id:
            return False
        if proof.get("colorTransforms") != component.image_color_transforms:
            return False
        try:
            crop = [float(value) for value in proof.get("crop", "").split(",")]
            expected = [float(component.image_crop.get(key, 0)) for key in ("left", "top", "right", "bottom")]
            flip = proof.get("flip", "").split(",")
            expected_flip = [str(int(bool(component.source_transform.get(key)))) for key in ("flipH", "flipV")]
            if crop != expected or flip != expected_flip:
                return False
            source = node.get("src", "")
            if not source.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")):
                return False
            pixels = base64.b64decode(source.split(",", 1)[1], validate=True)
        except (TypeError, ValueError, AttributeError, binascii.Error):
            return False
        return bool(pixels) and proof.get("renderedDigest") == "sha256:" + sha256(pixels).hexdigest()

    def unchanged_owner(qid: str, component: ComposerComponent) -> bool:
        node = nodes.get(qid)
        if node is None:
            return False
        for ancestor in node.iterancestors():
            if "cx-geo" not in (ancestor.get("class") or "").split():
                continue
            observation = elements.get(ancestor.get("data-qid"), {})
            style = observation.get("computed_style") or {}
            if style.get("transform") not in {None, "none"}:
                return False
            return _same(_box(observation.get("bbox")), _original_box(component, canvas_width, canvas_height))
        return False

    retained: list[str] = []
    witnesses: list[dict] = []
    for issue in measurement.blocking_issues:
        prefix = "overlap: browser pixel-area probe reports a continuous occluding layer ("
        if not issue.startswith(prefix) or not issue.endswith(")"):
            retained.append(issue)
            continue
        qid = issue[len(prefix):-1]
        target = elements.get(qid, {})
        target_component = components.get(target.get("source_block_id"))
        evidence = target.get("occlusion_measurement_evidence") or {}
        occluders = target.get("occlusion_occluder_qids")
        valid = (target_component is not None and target_component.kind == "image"
                 and target.get("tag") == "img" and target.get("semantic_role") == "image"
                 and evidence.get("status") == "measured" and evidence.get("image_alpha_target_supported") is True
                 # The pixel sensor hashes Chrome's prepared DOM; the capture
                 # envelope separately binds original input HTML above.
                 and evidence.get("source_digest") == measurement.metrics.get("render_digest")
                 and evidence.get("render_digest") == measurement.metrics.get("render_digest")
                 and str(target.get("occlusion_sensor_version", "")).startswith("owned-paint-v14")
                 and target.get("occlusion_unknown_fraction") == 0 and target.get("occlusion_unbound_count") == 0
                 and target.get("occlusion_sample_count", 0) > 0
                 and target.get("occlusion_known_sample_count") == target.get("occlusion_sample_count")
                 and isinstance(occluders, list) and bool(occluders))
        pairs: list[dict] = []
        if valid:
            original_target = _original_box(target_component, canvas_width, canvas_height)
            valid = (trusted_image(qid, target_component) and unchanged_owner(qid, target_component)
                     and _same(_box(target.get("bbox")), original_target))
            for owner_qid in occluders:
                owner = elements.get(owner_qid, {})
                component = bindings.get(owner.get("source_block_id")) or components.get(owner.get("source_block_id"))
                # The full sensor Set of occluders is checked, never a top-N list.
                image_owner = component is not None and component.kind == "image"
                owner_paint_proven = (trusted_image(owner_qid, component) if image_owner
                                      else bool(owner.get("has_owned_text")))
                if (component is None or not owner_paint_proven
                    or component.source_order <= target_component.source_order
                    or not isinstance(owner.get("effective_z_index"), (int, float))
                    or not isinstance(target.get("effective_z_index"), (int, float))
                    or owner["effective_z_index"] <= target["effective_z_index"]
                    or not unchanged_owner(owner_qid, component)
                    or not _intersects(original_target, _original_box(component, canvas_width, canvas_height))
                    or not _inside(_box(owner.get("bbox")), _original_box(component, canvas_width, canvas_height))):
                    valid = False
                    break
                pairs.append({"occluderQid": owner_qid, "sourceBlockId": component.component_id,
                              "sourceOrder": component.source_order, "sourceBoxPx": list(_original_box(component, canvas_width, canvas_height)),
                              "kind": "image" if image_owner else "text",
                              **({"renderedDigest": plan.source_image_materializations[component.component_id]["renderedDigest"]} if image_owner else {})})
        if not valid:
            retained.append(issue)
            continue
        witnesses.append({"schemaVersion": "composer-source-image-topology/v1", "originalIssue": issue,
                          "targetQid": qid, "sourceBlockId": target_component.component_id,
                          "sourceOrder": target_component.source_order, "sourceBoxPx": list(original_target),
                          "sourceDigest": digest, "occluders": pairs,
                          "renderedDigest": plan.source_image_materializations[target_component.component_id]["renderedDigest"],
                          "verdict": "preserved-source-image-paint-order"})
    if not witnesses:
        return measurement
    return replace(measurement, blocking_issues=tuple(retained),
                   metrics={**measurement.metrics, "source_topology_witnesses": [*measurement.metrics.get("source_topology_witnesses", []), *witnesses]},
                   notes=(*measurement.notes, *(f"source image overlap preserved: {item['targetQid']}" for item in witnesses)))
