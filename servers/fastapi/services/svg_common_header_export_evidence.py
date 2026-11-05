"""Current, full-scene evidence for generated common headers, version two."""
from __future__ import annotations

import base64
import hashlib
import math
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from utils.hashing import canonical_json_sha256

MARKER_ID = "svg-system-common-header-v2-contract"
DRAWABLE = frozenset({"text", "rect", "circle", "ellipse", "line", "path", "polygon", "polyline", "image"})


def digest(value):
    return "sha256:" + canonical_json_sha256(value)


COMMON_HEADER_VERIFIER_FILES = (
    "services/svg_common_header_export_evidence.py", "services/svg_common_header_v2.py",
    "services/svg_common_header_v2_geometry.py", "services/svg_common_header.py", "services/svg_renderer.py",
    "services/svg_text_overlap.py",
    "workers/svg_common_header_export_v2.py", "workers/svg_common_header_export.py",
    "services/svg_export_page_evidence.py", "services/svg_export_quality.py",
    "services/generation_core/drivers/svg/render.py", "services/svg_master_furniture.py")


def common_header_verifier_digest():
    """The header verifier identity that slide evidence, readbacks and export identities carry.

    It is the reviewed revision's identity (``services/verifier_revisions``) while
    the running source is a reviewed state of that revision, and the raw
    ``common_header_verifier_source_digest()`` otherwise.
    """
    from services.verifier_revisions import COMMON_HEADER_VERIFIER_LEDGER

    return COMMON_HEADER_VERIFIER_LEDGER.resolve(common_header_verifier_source_state())


def common_header_verifier_source_digest():
    """The pre-migration identity: the verifier files' bytes as they are on disk."""
    return common_header_verifier_source_state().raw


def common_header_verifier_source_state():
    from services.verifier_revisions import COMMON_HEADER_VERIFIER_LEDGER

    root = Path(__file__).resolve().parents[1]
    return COMMON_HEADER_VERIFIER_LEDGER.source_state(
        {name: root / name for name in COMMON_HEADER_VERIFIER_FILES}, digest)


def header_scene(svg):
    if MARKER_ID not in svg:
        return None
    from services.svg_common_header import GROUP_ID
    from services.svg_common_header_v2 import common_header_group_digest

    root = ET.fromstring(svg)
    markers = [node for node in root.iter() if node.get("id") == MARKER_ID]
    if not markers:
        return None
    groups = [node for node in root.iter() if node.get("id") == GROUP_ID]
    if len(markers) != 1 or len(groups) != 1 or groups[0] not in list(root) or markers[0] not in list(groups[0]):
        raise ValueError("svg_common_header_export_binding_invalid")
    group = groups[0]
    nodes = list(group.iter())
    ids = [node.get("id") for node in nodes]
    if (any(not identity or not identity.startswith(GROUP_ID) for identity in ids)
            or len(ids) != len(set(ids)) or any(sum(node.get("id") == identity for node in root.iter()) != 1 for identity in ids)):
        raise ValueError("svg_common_header_export_binding_invalid")
    elements = {}
    for node in nodes:
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "g":
            continue
        if tag not in DRAWABLE or list(node):
            raise ValueError("svg_common_header_export_not_editable")
        record = {"tag": tag, "attributes": dict(node.attrib), "text": node.text or ""}
        if tag == "image":
            href = node.get("href", "")
            if not href.startswith("data:image/") or ";base64," not in href:
                raise ValueError("svg_common_header_export_asset_invalid")
            record["assetDigest"] = "sha256:" + hashlib.sha256(base64.b64decode(href.split(",", 1)[1], validate=True)).hexdigest()
        elements[node.get("id")] = record
    return {"groupDigest": common_header_group_digest(group), "elements": elements}


def common_header_v2_current(slide, quality, generated):
    from services.svg_common_header import validate_common_header_binding

    try:
        contract, page = generated["contract"], generated["page"]
        validate_common_header_binding(contract, page, index=generated["index"], count=generated["count"])
        scene = header_scene(slide.svg_content or "")
        evidence = quality.get("common_header") or {}
        return (scene is not None and page.get("applied") is True and evidence.get("passed") is True
                and evidence.get("version") == "svg-common-header-render/v2"
                and evidence.get("origin") == "generated-common-header"
                and evidence.get("header_digest") == contract["digest"]
                and evidence.get("page_digest") == page["digest"]
                and evidence.get("title_digest") == page["title_digest"]
                and evidence.get("rendered_group_digest") == scene["groupDigest"] == page["rendered_group_digest"]
                and evidence.get("element_ids") == list(scene["elements"])
                and evidence.get("body_box") == contract["body_box"]
                and evidence.get("verifier_digest") == common_header_verifier_digest())
    except (ValueError, KeyError, TypeError, AttributeError, ET.ParseError):
        return False


def common_header_v2_ooxml_current(slides, value):
    """A v1 title-only receipt cannot attest a marked full-scene v2 header."""
    try:
        if (value.get("schemaVersion") != "svg-common-header-ooxml/v2" or value.get("passed") is not True
                or value.get("origin") != "generated-common-header" or value.get("officeRenderMeasured") is not False
                or value.get("verifierDigest") != common_header_verifier_digest()
                or value.get("digest") != digest({key: item for key, item in value.items() if key != "digest"})):
            return False
        scale = value.get("pointPerPx")
        if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
            return False
        scenes = [(index, header_scene(slide.svg_content or "")) for index, slide in enumerate(slides)]
        scenes = [(index, scene) for index, scene in scenes if scene is not None]
        pages = value.get("pages")
        if not isinstance(pages, list) or len(pages) != len(scenes):
            return False
        from services.svg_contract import source_digest
        projection = value.get("canonicalProjectionBinding")
        if projection is not None and (not isinstance(projection, dict)
                or value.get("finalPackageReverified") is not True
                or projection.get("sameRenderedHeaderGroups") is not True
                or projection.get("canonicalSourceDigests") != [source_digest(slide.svg_content or "") for slide in slides]
                or projection.get("conversionReadbackDigest") != value.get("conversionReadbackDigest")
                or not isinstance(value.get("conversionReadbackDigest"), str)
                or not value["conversionReadbackDigest"].startswith("sha256:")):
            return False
        for (index, scene), page in zip(scenes, pages, strict=True):
            slide = slides[index]
            receipt = (slide.content or {}).get("svgReceipt") or {}
            generated = (receipt.get("system_furniture") or {}).get("generated_common_header") or {}
            if (not common_header_v2_current(slide, receipt.get("quality") or {}, generated)
                    or page.get("pageIndex") != index or page.get("svgDigest") != source_digest(slide.svg_content or "")
                    or page.get("renderedGroupDigest") != scene["groupDigest"]):
                return False
            elements = page.get("elements")
            if not isinstance(elements, list) or len(elements) != len(scene["elements"]):
                return False
            ids = set()
            for record, (identity, source) in zip(elements, scene["elements"].items(), strict=True):
                sid, bounds = record.get("shapeId"), record.get("boundsEmu")
                if (record.get("elementId") != identity or record.get("tag") != source["tag"]
                        or record.get("sourceElementDigest") != digest(source) or record.get("editable") is not True
                        or record.get("nativeKind") != ("pic" if source["tag"] == "image" else "sp")
                        or not isinstance(record.get("ooxmlDigest"), str) or not record["ooxmlDigest"].startswith("sha256:")
                        or type(sid) is not int or sid < 1 or sid in ids
                        or not isinstance(bounds, list) or len(bounds) != 4 or any(type(number) is not int for number in bounds)):
                    return False
                if source["tag"] == "text" and record.get("text") != source["text"]:
                    return False
                if source["tag"] == "image" and record.get("assetDigest") != source["assetDigest"]:
                    return False
                ids.add(sid)
        return True
    except (ValueError, KeyError, TypeError, AttributeError, ET.ParseError):
        return False


def apply_common_header_v2_quality(quality, svg, contract, binding, furniture_receipt):
    """Measure every drawable against its frozen input and the reserved body."""
    from services.svg_common_header import GROUP_ID, validate_common_header_binding
    from services.svg_contract import _numbers
    from services.svg_common_header_v2_geometry import intersects
    from services.svg_text_overlap import _ink_rect, comparison_fragments, text_overlaps

    failures = []
    scene = header_scene(svg)
    try:
        validate_common_header_binding(contract, binding)
        if scene is None or scene["groupDigest"] != binding["rendered_group_digest"]:
            raise ValueError("svg_common_header_digest_mismatch")
    except (ValueError, KeyError, TypeError):
        failures.append({"kind": "svg_common_header_digest_mismatch"})
    root = ET.fromstring(svg)
    parents = {child: parent for parent in root.iter() for child in parent}
    metrics = {item.get("qid"): item for item in (quality.get("browser_metrics") or {}).get("elements", [])}
    reserved = set((furniture_receipt or {}).get("element_ids") or [])
    fields = {GROUP_ID + "-" + field["node_id"] + f"-line-{index + 1}": field
              for field in contract["fields"] for index in range(field["max_lines"])}
    body = contract["body_box"]
    canvas_backgrounds = 0
    measured_header_text = []

    def field_bounds(node, field):
        # A field allocates layout space; Pillow glyph ink is only predicted
        # paint evidence, not the permitted extent of browser glyph ink.
        bounds = dict(field["box"])
        while node is not None:
            transform = node.get("transform")
            if transform:
                match = re.fullmatch(r"translate\(([^()]+)\)", transform)
                if not match:
                    raise ValueError("svg_common_header_transform_unsupported")
                values = _numbers(match[1])
                if len(values) not in (1, 2):
                    raise ValueError("svg_common_header_transform_unsupported")
                bounds["x"] += values[0]
                bounds["y"] += values[1] if len(values) == 2 else 0
            node = parents.get(node)
        return bounds

    def inherited(node, key, default):
        while node is not None:
            if key in node.attrib:
                return node.get(key)
            node = parents.get(node)
        return default

    def inside(box, bounds, tolerance=.5):
        return (box is not None and all(math.isfinite(float(box[key])) for key in ("x", "y", "width", "height"))
                and box["x"] >= bounds["x"] - tolerance and box["y"] >= bounds["y"] - tolerance
                and box["x"] + box["width"] <= bounds["x"] + bounds["width"] + tolerance
                and box["y"] + box["height"] <= bounds["y"] + bounds["height"] + tolerance)

    def untransformed(node):
        while node is not None:
            if node.get("transform"):
                return False
            node = parents.get(node)
        return True

    for index, node in enumerate(root.iter()):
        tag, identity = node.tag.rsplit("}", 1)[-1], node.get("id", "")
        if tag in {"svg", "g", "defs", "metadata", "title", "desc"}:
            continue
        qid = f"svg-{index}"
        item = metrics.get(qid)
        if item is None:
            failures.append({"kind": "svg_common_header_measurement_unavailable", "qid": qid})
            continue
        is_header = identity in (scene or {}).get("elements", {})
        stroke_margin_safe = False
        if not item.get("css_visible", False) or item.get("effective_opacity", 0) <= 0:
            if is_header and tag == "text":
                failures.append({"kind": "svg_common_header_text_hidden", "qid": qid})
            continue
        if (not is_header and tag == "text" and not item.get("has_owned_text")
                and not (node.text or "").strip()
                and not any((child.tail or "").strip() for child in node)):
            # A text wrapper owns no glyphs when all wording lives in tspans.
            # Those descendants are independently measured below; an empty
            # owned-fragment list on their parent is not body paint.
            continue
        if item.get("has_owned_text") or tag == "text":
            # Keep the shared probe's conservative ink-cell/line-box fallback;
            # dropping it loses measured geometry and reports false overflow.
            boxes = [_ink_rect(box) for box, _basis in comparison_fragments(item)]
        elif tag == "tspan":
            continue
        else:
            boxes = [_ink_rect(item.get("bbox"))]
            stroke = inherited(node, "stroke", "none")
            fill = inherited(node, "fill", "black")
            if float(inherited(node, "stroke-opacity", "1")) <= 0:
                stroke = "none"
            if float(inherited(node, "fill-opacity", "1")) <= 0:
                fill = "none"
            if stroke == "none" and (fill == "none" or tag in {"line", "polyline"}):
                continue
            if stroke != "none":
                raw = boxes[0] or item.get("bbox") or {}
                try:
                    box = {key: float(raw[key]) for key in ("x", "y", "width", "height")}
                    half = float(inherited(node, "stroke-width", "1")) / 2
                    boxes = [{"x": box["x"] - half, "y": box["y"] - half,
                              "width": box["width"] + half * 2, "height": box["height"] + half * 2}]
                    painted = boxes[0]
                    stroke_margin_safe = (tag in {"rect", "circle", "ellipse"} and math.isfinite(half) and half > 0
                        and untransformed(node) and inside(box, body)
                        and inside(painted, {"x": 0, "y": 0, **contract["canvas"]})
                        and painted["y"] >= body["y"] - .5
                        and painted["y"] + painted["height"] <= body["y"] + body["height"] + .5
                        and all(not intersects(painted, reserved_box) for reserved_box in contract.get("brand_reservations", [])))
                except (KeyError, TypeError, ValueError):
                    boxes = [None]
        if is_header:
            header_bounds = {"x": 0, "y": 0, "width": contract["canvas"]["width"], "height": contract["content_top_px"]}
            bounds = header_bounds
            if tag == "text":
                field = fields.get(identity)
                if field is None:
                    failures.append({"kind": "svg_common_header_binding_invalid", "qid": qid})
                    continue
                painted = binding.get("paint_boxes", {}).get(identity) or []
                if not painted:
                    failures.append({"kind": "svg_common_header_measurement_unavailable", "qid": qid})
                    continue
                try:
                    bounds = field_bounds(node, field)
                except (KeyError, TypeError, ValueError):
                    failures.append({"kind": "svg_common_header_binding_invalid", "qid": qid})
                    continue
                if boxes and all(box is not None for box in boxes):
                    measured_header_text.append(item)
                if (item.get("glyph_clipped") or item.get("font_loaded") is not True
                        or str(item.get("resolved_font_family", "")).strip("'\"") != field["font"]["family"]
                        or abs(float(item.get("font_size", 0)) - field["font"]["size_px"]) > .5
                        or item.get("effective_opacity", 0) < .9):
                    failures.append({"kind": "svg_common_header_font_mismatch", "qid": qid})
            if not boxes or any(not inside(box, bounds, 1 if tag == "text" else .5)
                    or not inside(box, header_bounds)
                    or intersects(box, body)
                    or any(intersects(box, reservation) for reservation in contract.get("brand_reservations", []))
                    for box in boxes):
                failures.append({"kind": "svg_common_header_overflow", "qid": qid})
        elif identity not in reserved and identity != "svg-system-page-number":
            # Preserve the existing allowance for the initial full-canvas background.
            box = boxes[0] if len(boxes) == 1 else None
            background = (tag == "rect" and box and abs(box["x"]) <= .5 and abs(box["y"]) <= .5
                          and abs(box["width"] - contract["canvas"]["width"]) <= .5
                          and abs(box["height"] - contract["canvas"]["height"]) <= .5
                          and not any(previous.get("css_visible", False) and previous.get("tag") not in {"svg", "g"}
                                      for previous in list(metrics.values())[:list(metrics).index(qid)]))
            if background:
                canvas_backgrounds += 1
                background = canvas_backgrounds == 1
            if not background and not stroke_margin_safe and (not boxes or any(not inside(box, body) for box in boxes)):
                failures.append({"kind": "svg_common_header_body_intrusion", "qid": qid})
    overlaps, overlap_basis = text_overlaps(measured_header_text)
    failures.extend({"kind": "svg_common_header_text_overlap", "qids": pair} for pair in overlaps)
    quality["common_header"] = {"version": "svg-common-header-render/v2", "origin": "generated-common-header",
        "header_digest": contract["digest"], "page_digest": binding["digest"], "title_digest": binding["title_digest"],
        "rendered_group_digest": (scene or {}).get("groupDigest"), "element_ids": list((scene or {}).get("elements", {})),
        "body_box": body, "basis": "existing_browser_glyph_ink_and_paint", "verifier_digest": common_header_verifier_digest(),
        "text_overlap_basis": overlap_basis,
        "passed": not failures}
    if failures:
        quality["passed"] = quality["geometry_passed"] = False
        quality.setdefault("failures", []).extend(failures)
