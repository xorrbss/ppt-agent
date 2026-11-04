"""Owner-admitted template selection and page-local, immutable body contracts."""
from __future__ import annotations

from copy import deepcopy
import math
import re
import xml.etree.ElementTree as ET

from fastapi import HTTPException

from models.svg_template_frame import SvgTemplateFrame, SvgTemplateFrameAdaptation
from models.svg_template_codec import unpack_adaptation_fonts
from services.svg_templates.selection_material import (
    SELECTION_SCHEMA_V1, SELECTION_SCHEMAS, compact_selection, template_adaptations, template_package,
)
from utils.hashing import canonical_json_digest

SELECTION_SCHEMA = SELECTION_SCHEMA_V1


def template_selection(snapshot: dict | None) -> dict | None:
    selection = (snapshot or {}).get("svg_template")
    if selection is None:
        return None
    if (not isinstance(selection, dict) or selection.get("schema_version") not in SELECTION_SCHEMAS
            or selection.get("digest") != canonical_json_digest(
                {key: value for key, value in selection.items() if key != "digest"})):
        raise ValueError("svg_template_execution_snapshot_invalid")
    return selection


async def prepare_template_request(request, session) -> None:
    pin = getattr(request, "svg_template_pin", None)
    if pin is None:
        return
    from services.authored_canvas import request_canvas_metadata
    from services.svg_templates.catalog import normalized_canvas, resolve_template_selection
    from models.document_canvas import DocumentCanvasMetadata

    pin = pin.model_dump(mode="json", by_alias=True) if hasattr(pin, "model_dump") else pin
    canvas = DocumentCanvasMetadata.model_validate(normalized_canvas(request_canvas_metadata(request)))
    frozen = template_selection(getattr(request, "_svg_style_snapshot", None))
    if frozen is None:
        from services.svg_templates.availability import require_new_template_admission
        require_new_template_admission()
    mode = request.svg_template_mode
    if mode == "template-redesign" and getattr(request, "common_header_mode", None) is None:
        request.common_header_mode = "free"
    selection = await resolve_template_selection(
        session, pin=pin, mode=mode, canvas=canvas,
        source_mode=getattr(request, "source_mode", None),
        has_company_master=bool(getattr(request, "master_pin", None)),
        has_common_title_design=(getattr(request, "common_header_mode", None) == "uniform"
                                 or getattr(request, "common_header_design", None) is not None),
        frame_map=getattr(request, "svg_template_frame_map", None),
        allow_archived=frozen is not None,
    )
    frame_map = dict(getattr(request, "svg_template_frame_map", None) or {})
    count = len(request.slides_markdown) if getattr(request, "slides_markdown", None) else getattr(request, "n_slides", None)
    if any(not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]*", key)
           or (count is not None and int(key) > count) for key in frame_map):
        raise HTTPException(422, "svg_template_frame_map_invalid")
    if mode == "template-redesign" and frozen is None:
        # Admission only: a deck admitted before the lock keeps replaying.
        from services.svg_templates.catalog import require_source_canvas
        require_source_canvas(selection["package"], canvas.model_dump(mode="json", by_alias=True))
    selection.update(schema_version=SELECTION_SCHEMA, frame_map=frame_map,
                     canvas=canvas.model_dump(mode="json", by_alias=True))
    if mode == "template-redesign":
        frames = selection["package"].get("frames", [])
        if not frames or any(frame["frame_id"] not in selection["adaptations"] for frame in frames):
            raise HTTPException(409, "svg_template_canvas_preparation_required")
        for frame in frames:
            adapted = SvgTemplateFrameAdaptation.model_validate(unpack_adaptation_fonts(
                selection["adaptations"][frame["frame_id"]], selection["package"]))
            if (adapted.source_frame_digest != frame["digest"]
                    or adapted.target_canvas != canvas):
                raise HTTPException(409, "svg_template_adaptation_conflict")
    selection["digest"] = canonical_json_digest(selection)
    # New admissions store the material by reference (v2); a job frozen with
    # an inline v1 selection keeps replaying against the same v1 form.
    if frozen is None or frozen.get("schema_version") != SELECTION_SCHEMA_V1:
        selection = compact_selection(selection)
    if frozen is not None and frozen != selection:
        raise HTTPException(409, "svg_template_execution_snapshot_conflict")
    request._svg_template_selection = deepcopy(frozen or selection)


def apply_template_snapshot(snapshot: dict, request) -> None:
    if getattr(request, "svg_template_pin", None) is None:
        return
    selection = getattr(request, "_svg_template_selection", None)
    if not isinstance(selection, dict):
        raise ValueError("svg_template_admission_required")
    snapshot["svg_template"] = deepcopy(selection)
    snapshot["style_id"] = "svg-template:" + selection["template_pin"]["templateId"]
    snapshot["provenance"]["template_pin"] = deepcopy(selection["template_pin"])
    # Catalogue-specific recovery images are not evidence for a different user's template.
    snapshot.pop("validated_recovery_candidates", None)
    snapshot["mapped_guidance"] = ""
    snapshot["common_guidance"] = {}
    snapshot["overrides"] = {}
    if selection["mode"] == "template-redesign":
        manifest = template_package(selection)["font_manifest"]
        families = list(dict.fromkeys(asset["family"] for asset in manifest["assets"]))
        if not families:
            raise ValueError("svg_template_fonts_missing")
        profile = selection["style_projection"]
        for role in ("heading", "body"):
            observed = (profile.get("fonts", {}).get(role) or {}).get("family")
            snapshot["tokens"][role + "_font"] = observed if observed in families else families[0]
        snapshot["tokens"]["mono_font"] = snapshot["tokens"]["body_font"]
        snapshot["composition_guidance"] = [
            "The server preserves original fonts, background, header, footer and logos.",
            "Author only the permitted body region. Reuse successful source hierarchy, comparison and image treatment when they fit this content.",
            "Adjust only what needs improvement; freely redesign the body when content or canvas makes the source composition unsuitable.",
            "Prefer clear simple compositions. Decorative cards, icons or visual difference are not evidence of improvement.",
            "Examples contain no factual authority: use only this request's facts, labels, figures and relationships.",
        ]
    snapshot["digest"] = canonical_json_digest({key: value for key, value in snapshot.items() if key != "digest"}, allow_nan=True)


_HEADER_KEYS = ("title", "chapter_number", "chapter_title")


def automatic_frame_id(frames, index: int) -> str:
    """The frame of a page the request's frame map does not name.

    Page 1 takes the cover frame. Any other page takes the body frame that shows
    most of the header fields (title, chapter number, chapter name), then most
    dynamic fields, then has the largest body region; the first such frame in
    source order wins a tie. The choice depends on the template only, so every
    caller recomputes the same frame.
    """
    if index == 0:
        cover = next((frame for frame in frames if frame["role"] == "cover"), None)
        if cover is not None:
            return cover["frame_id"]
    bodies = [frame for frame in frames if frame["role"] == "body"] or list(frames)

    def fit(frame) -> tuple:
        fields = set(frame.get("dynamic_fields") or ())
        region = frame.get("body_region") or {}
        return (sum(key in fields for key in _HEADER_KEYS), len(fields),
                float(region.get("width", 0)) * float(region.get("height", 0)))

    return max(bodies, key=fit)["frame_id"]


def page_frame(snapshot: dict, index: int, count: int, role: str | None = None) -> SvgTemplateFrame:
    selection = template_selection(snapshot)
    if selection is None or selection["mode"] != "template-redesign" or not 0 <= index < count:
        raise ValueError("svg_template_page_context_invalid")
    package, adaptations = template_package(selection), template_adaptations(selection)
    frames = package["frames"]
    selected_id = selection["frame_map"].get(str(index + 1))
    if selected_id is None:
        selected_id = automatic_frame_id(frames, index)
    if selected_id not in adaptations:
        raise ValueError("svg_template_canvas_preparation_required")
    return SvgTemplateFrameAdaptation.model_validate(unpack_adaptation_fonts(
        adaptations[selected_id], package)).frame


#: Margin between the region the author is given and the region a page is validated against.
AUTHORING_MARGIN_PX = 4.0


def _inset(box, margin: float) -> dict:
    return {"x": box.x + margin, "y": box.y + margin,
            "width": max(0.0, box.width - 2 * margin), "height": max(0.0, box.height - 2 * margin)}


def template_page_context(snapshot: dict, index: int, count: int, content: str,
                          role: str | None = None, chapter: dict | None = None) -> dict | None:
    """The page's template contract. ``chapter`` (``{"number", "title"}``, from the
    outline page or a saved page's context) fills a frame's chapter tab."""
    selection = template_selection(snapshot)
    if selection is None or selection["mode"] != "template-redesign":
        return None
    from utils.outline_utils import explicit_outline_title
    from services.svg_templates.frame_composition import body_background, render_frame_svg

    frame = page_frame(snapshot, index, count, role)
    title = explicit_outline_title(content)
    if "title" in frame.dynamic_fields and not title:
        raise ValueError("svg_template_title_required")
    section = re.match(r"^\s*(\d+(?:\.\d+)*)[.)\s]", title or "")
    values = {"title": title or "", "page_number": str(index + 1),
              "section_number": section.group(1) if section else ""}
    from services.svg_templates.chapters import chapter_values, header_values
    values.update(chapter_values(frame, chapter, cover=index == 0))
    values.update(header_values(frame, title or "", chapter))
    # The render here still checks that the bound values can be drawn (fonts and glyphs, and
    # chapter tabs, which fail when too long). An over-wide title or breadcrumb is shrunk and
    # cut by _fit_header_text instead of failing, so it does not reach this check.
    render_frame_svg(frame, dynamic_values=values)
    adaptation = template_adaptations(selection)[frame.frame_id]
    result = {
        "schema_version": "svg-template-page/v1", "page_index": index, "page_count": count,
        "frame_id": frame.frame_id, "frame_digest": frame.digest,
        "adaptation_id": adaptation["adaptation_id"], "adaptation_digest": adaptation["digest"],
        "dynamic_values": values, "dynamic_fields": list(frame.dynamic_fields),
        "content_digest": canonical_json_digest({"content": content}),
        # The author gets the frame's regions with a safety margin; pages are
        # still validated against the frame's own. Drafts drew stroked cards
        # exactly on the region edge and painted half the stroke outside it.
        "body_region": _inset(frame.body_region, AUTHORING_MARGIN_PX),
        "forbidden_regions": [_inset(box, -AUTHORING_MARGIN_PX) for box in frame.forbidden_regions],
        "font_families": list(dict.fromkeys(asset.family for asset in frame.font_manifest.assets)),
        "font_faces": [{"family": asset.family, "weight": asset.weight, "face": asset.face}
                       for asset in frame.font_manifest.assets],
        "source_body_evidence": [item for item in template_package(selection).get("body_evidence", [])
                                 if item["source_page"] == frame.source_page],
        "body_background": body_background(frame),
    }
    result["digest"] = canonical_json_digest(result)
    return result


def body_author_rules(rules: str, snapshot: dict, *, body_floor: float | None = None) -> str:
    selection = template_selection(snapshot)
    if not selection or selection["mode"] != "template-redesign":
        return rules
    from utils.get_env_concurrency import get_svg_template_component_style_env
    component_rules = ""
    if get_svg_template_component_style_env():
        from services.svg_templates.component_style import component_style_rules
        component_rules = component_style_rules(template_package(selection).get("component_style"),
                                                body_floor=body_floor)
    rules = rules.replace(
        "Never create a page number or use id svg-system-page-number. Reserve the bottom-right 64x36 area for the system footer.",
        "Do not author any system page number, header, footer or logo. The template owns their exact regions.",
    )
    rules = rules.replace("tokens.background for the canvas background", "tokens.background for body accents when appropriate")
    rules = rules.replace(
        "If tokens.wordmark is a non-empty string and is not supplied as system furniture, render it exactly once as small (<=14px), unobtrusive text "
        "near the bottom-left corner; never inside the reserved bottom-right footer area.",
        "The server owns all template wordmarks; do not add another wordmark.",
    )
    rules = rules.replace("do not copy their arrangement or substitute their content.",
                          "reuse successful source arrangements when appropriate, but never substitute their content.")
    rules = rules.replace("Choose the title's position and visual weight from this page's message and evidence.",
                          "When template_page.dynamic_fields contains title, do not repeat that page title in the body. Otherwise compose a title inside the body.")
    # The native table/chart rules name their group native-*-SLUG, which composition rejects
    # here (svg_template_body_namespace_required: every body id starts with body-).
    for kind in ("table", "chart"):
        rules = rules.replace(f'id="native-{kind}-SLUG"', f'id="body-native-{kind}-SLUG"')
    return rules + "\n" + "\n".join(snapshot["composition_guidance"]) + (
        "\nThe template_page input is authoritative. Return one full-canvas SVG coordinate system,"
        " with all body paint strictly inside body_region and outside forbidden_regions."
        " Paint includes strokes: a stroke extends half its stroke-width beyond the shape's geometry,"
        " so keep every shape, line and card at least half its stroke-width plus 2px inside body_region;"
        " never align a stroked edge exactly on a body_region edge."
        " template_page.body_background is the template paint under the body (a dark photo on a cover or"
        " divider): text drawn directly on it uses body_background.text_color or another colour with at"
        " least 4.5:1 contrast to body_background.mean_color; a card behind text may set its own contrast."
        " Use transparent space outside the body; do not add a full-canvas background."
        " All authored element IDs must start with body-. Never use svg-system- IDs."
        " Preserve every required fact and number. template_page.font_faces is authoritative for this page;"
        " choose only its exact families and weights, even when global font tokens suggest a different face."
        " This body boundary also applies to correction, quality recovery and free composition instructions."
    ) + component_rules


def compose_template_page(snapshot: dict, svg: str, context: dict, *, allow_existing=False,
                          source_manifest: dict | None = None) -> str:
    from services.svg_templates.frame_composition import compose_frame_svg

    frame = page_frame(snapshot, context["page_index"], context["page_count"])
    body = svg
    if allow_existing:
        root = ET.fromstring(svg)
        groups = [node for node in root if node.get("id") == "svg-system-template-body"]
        if groups:
            if len(groups) != 1:
                raise ValueError("svg_template_composed_body_invalid")
            clean = ET.Element(root.tag, dict(root.attrib))
            clean.extend(deepcopy(list(groups[0])))
            body = ET.tostring(clean, encoding="unicode")
    result = compose_frame_svg(frame, body, dynamic_values=context["dynamic_values"], source_manifest=source_manifest)
    if allow_existing and body != svg:
        def structure(node):
            return node.tag, sorted(node.attrib.items()), node.text or "", tuple(structure(child) for child in node)
        if structure(ET.fromstring(svg)) != structure(ET.fromstring(result)):
            raise ValueError("svg_template_frame_changed")
    return result


def apply_template_body_quality(quality: dict, svg: str, snapshot: dict, context: dict) -> None:
    from models.svg_template_frame import FrameRect
    from services.svg_templates.frame_composition import validate_body_paint
    from services.svg_text_overlap import _ink_rect, comparison_fragments

    root = ET.fromstring(svg)
    body = next((node for node in root if node.get("id") == "svg-system-template-body"), None)
    if body is None:
        raise ValueError("svg_template_composed_body_missing")
    qids = {node: f"svg-{i}" for i, node in enumerate(root.iter())}
    parents = {child: parent for parent in root.iter() for child in parent}
    def inherited(node, name, default):
        while node is not None:
            if name in node.attrib:
                return node.get(name)
            node = parents.get(node)
        return default

    def geometry_box(value):
        if not isinstance(value, dict):
            return None
        try:
            result = {key: float(value[key]) for key in ("x", "y", "width", "height")}
        except (KeyError, TypeError, ValueError):
            return None
        return result if all(math.isfinite(number) for number in result.values()) and result["width"] >= 0 and result["height"] >= 0 else None
    measurements = {item.get("qid"): item for item in (quality.get("browser_metrics") or {}).get("elements", [])}
    bounds, failures, owners = [], [], []
    for node in body.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in {"svg", "g"}:
            continue
        item = measurements.get(qids[node])
        if item is None:
            failures.append({"kind": "svg_template_body_measurement_missing", "qid": qids[node]})
            continue
        if not item.get("css_visible", False) or item.get("effective_opacity", 0) <= 0:
            continue
        boxes = ([_ink_rect(box) for box, _ in comparison_fragments(item)]
                 if item.get("has_owned_text") else [geometry_box(item.get("bbox"))])
        if tag == "tspan" and not item.get("has_owned_text"):
            continue
        if not boxes:
            failures.append({"kind": "svg_template_body_measurement_missing", "qid": qids[node]})
        for box in boxes:
            if box is None:
                failures.append({"kind": "svg_template_body_measurement_missing", "qid": qids[node]})
                continue
            stroke = inherited(node, "stroke", "none")
            try:
                margin = float(inherited(node, "stroke-width", "1")) / 2 if stroke != "none" else 0
                if margin and tag in {"path", "polyline", "polygon"} and inherited(node, "stroke-linejoin", "miter") == "miter":
                    margin *= float(inherited(node, "stroke-miterlimit", "4"))
                if not math.isfinite(margin) or margin < 0:
                    raise ValueError("invalid stroke")
            except (ValueError, TypeError):
                failures.append({"kind": "svg_template_body_stroke_unmeasured", "qid": qids[node]})
                continue
            bounds.append(FrameRect(x=box["x"] - margin, y=box["y"] - margin,
                                    width=box["width"] + 2 * margin, height=box["height"] + 2 * margin))
            owners.append(node)
    frame = page_frame(snapshot, context["page_index"], context["page_count"])
    try:
        validate_body_paint(frame, tuple(bounds))
    except ValueError as error:
        failure = {"kind": str(error)}
        if str(error) == "svg_template_body_outside_allowed_region":
            # Name what left the region, so a repair can move exactly that.
            region = frame.body_region
            outside = [(owner, box) for owner, box in zip(owners, bounds, strict=True)
                       if box.x < region.x - .5 or box.y < region.y - .5
                       or box.x + box.width > region.x + region.width + .5
                       or box.y + box.height > region.y + region.height + .5]
            failure.update(body_region=region.model_dump(mode="json"), elements=[
                {"qid": qids[owner], "id": owner.get("id"),
                 "box": {key: round(getattr(box, key), 1) for key in ("x", "y", "width", "height")}}
                for owner, box in outside[:8]])
        failures.append(failure)
    quality.setdefault("failures", []).extend(failures)
    quality["template_preservation"] = {
        "schema_version": "svg-template-preservation/v1", "page_context_digest": context["digest"],
        "frame_digest": context["frame_digest"], "adaptation_digest": context["adaptation_digest"],
        "svg_digest": canonical_json_digest({"svg": svg}), "body_paint_measured": not failures,
    }
    if failures:
        quality["passed"] = False
        quality["geometry_passed"] = False


def preflight_template_outline(snapshot: dict, outline) -> None:
    selection = template_selection(snapshot)
    if not selection or selection["mode"] != "template-redesign":
        return
    count = len(outline.slides)
    if any(int(key) > count for key in selection["frame_map"]):
        raise ValueError("svg_template_frame_map_invalid")
    for index, slide in enumerate(outline.slides):
        template_page_context(snapshot, index, count, slide.content, chapter=getattr(slide, "chapter", None))


def validate_template_publication(snapshot: dict, pages, outline) -> None:
    selection = template_selection(snapshot)
    if not selection or selection["mode"] != "template-redesign":
        return
    page_frame(snapshot, 0, len(pages))
    for index, (payload, _png) in enumerate(pages):
        validate_template_publication_page(snapshot, payload, index=index, count=len(pages),
                                           content=outline.slides[index].content,
                                           chapter=getattr(outline.slides[index], "chapter", None))
        if page_frame(snapshot, index, len(pages)).schema_version == "svg-template-frame/v3":
            from services.svg_presentation_checkpoint import _read_png
            from services.svg_templates.frame_fonts import bytes_digest
            png = _png if _png is not None else _read_png(payload)
            if not isinstance(png, bytes) or bytes_digest(png) != payload["fixedGlyphPreview"]["pngDigest"]:
                raise ValueError("svg_template_glyph_publication_png_mismatch")


def validate_template_publication_page(snapshot: dict, payload: dict, *, index: int, count: int,
                                       content: str, chapter: dict | None = None) -> None:
    """Share the intact page proof between terminal keep and deck publication."""
    from services.svg_master_furniture import master_furniture_receipt
    canvas = page_frame(snapshot, 0, count).source_canvas
    expected = template_page_context(snapshot, index, count, content, chapter=chapter)
    if expected != payload.get("svg_template_page"):
        raise ValueError("svg_template_publication_context_mismatch")
    svg = payload["svg_content"]
    source_manifest = {node.get("href"): {"required": True} for node in ET.fromstring(svg).iter()
                       if node.tag.rsplit("}", 1)[-1] == "image" and node.get("href", "").startswith("source-slot-")}
    receipt = master_furniture_receipt(svg, snapshot, index=index, n=count,
        width=canvas.width_px, height=canvas.height_px, template_page_context=expected,
        source_manifest=source_manifest or None)
    proof = (payload.get("quality") or {}).get("template_preservation") or {}
    if (payload.get("system_furniture") != receipt
            or proof.get("page_context_digest") != expected["digest"]
            or proof.get("frame_digest") != expected["frame_digest"]
            or proof.get("adaptation_digest") != expected["adaptation_digest"]
            or proof.get("svg_digest") != canonical_json_digest({"svg": svg})
            or proof.get("body_paint_measured") is not True
            or (payload.get("quality") or {}).get("measurement_complete") is not True):
        raise ValueError("svg_template_preservation_unverified")
    frame = page_frame(snapshot, index, count)
    marker = payload.get("fixedGlyphPreview")
    quality = payload.get("quality") or {}
    glyph_quality = quality.get("fixed_glyph_preview")
    if frame.schema_version == "svg-template-frame/v3":
        from services.svg_contract import source_digest
        from services.svg_templates.frame_glyphs import validate_frame_glyph_authority
        authority = validate_frame_glyph_authority(frame)
        selection = template_selection(snapshot)
        pin = selection.get("template_pin")
        envelope = payload.get("svgAssetManifest") or {}
        placement = envelope.get("screenPlacement") or {}
        required = {"version", "authorityDigest", "materializerCodeDigest", "canonicalSvgDigest",
                    "screenDisplaySvgDigest", "screenRenderSvgDigest", "qidProjectionDigest", "trustedFontCssDigest",
                    "pngDigest", "fontManifestDigest", "screenPlacementDigest",
                    "slideRevision", "templatePin", "frameId", "frameDigest", "adaptationDigest"}
        shared = {"version", "authorityDigest", "materializerCodeDigest", "canonicalSvgDigest",
                  "screenDisplaySvgDigest", "screenRenderSvgDigest", "qidProjectionDigest", "trustedFontCssDigest", "pngDigest"}
        if (not isinstance(marker, dict) or set(marker) != required or not isinstance(glyph_quality, dict)
                or {k: marker[k] for k in shared} != glyph_quality
                or marker["version"] != "svg-fixed-glyph-preview/v1"
                or marker["authorityDigest"] != authority.authority_digest
                or marker["materializerCodeDigest"] != authority.materializer_code_digest
                or marker["canonicalSvgDigest"] != source_digest(svg)
                or quality.get("source_digest") != marker["canonicalSvgDigest"]
                or not isinstance(pin, dict) or set(pin) != {"templateId", "revision", "digest"}
                or not isinstance(pin.get("templateId"), str) or not pin["templateId"]
                or type(pin.get("revision")) is not int or pin["revision"] < 1
                or not isinstance(pin.get("digest"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", pin["digest"])
                or marker["templatePin"] != pin
                or marker["frameId"] != frame.frame_id or marker["frameDigest"] != frame.digest
                or marker["adaptationDigest"] != expected["adaptation_digest"]
                or marker["fontManifestDigest"] != frame.font_manifest.digest
                or (quality.get("trusted_font_materialization") or {}).get("manifest_digest") != frame.font_manifest.digest
                or not isinstance(marker["screenDisplaySvgDigest"], str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", marker["screenDisplaySvgDigest"])
                or any(not isinstance(marker[k], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", marker[k])
                       for k in ("screenRenderSvgDigest", "qidProjectionDigest", "trustedFontCssDigest"))
                or not isinstance(placement.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", placement["sha256"])
                or marker["screenPlacementDigest"] != placement["sha256"]
                or envelope.get("slideRevision") is None or marker["slideRevision"] != envelope["slideRevision"]
                or not isinstance(payload.get("png_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", payload["png_sha256"])
                or marker["pngDigest"] != "sha256:" + payload["png_sha256"]
                or quality.get("png_digest") != marker["pngDigest"]):
            raise ValueError("svg_template_glyph_publication_identity_mismatch")
    elif marker is not None or glyph_quality is not None:
        raise ValueError("svg_template_glyph_version_mismatch")
