"""Deterministic fixed-frame extraction; original body layouts remain evidence."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from models.svg_template_frame import (
    FrameIssue, FrameRect, SvgTemplateBodyEvidence, SvgTemplateFrame,
    SvgTemplateFrameElement, SvgTemplatePackage,
)
from services.document_canvas import document_canvas_from_source_slide_size
from services.source_content.pptx_extraction_constants import NS, R_NS
from services.source_content.pptx_package_parts import _ordered_slide_parts, _relationship_map
from services.source_content.pptx_typography import PptxTypographyResolver, placeholder_of
from services.source_content.security import admit_pptx_package
from services.svg_templates.frame_fonts import (
    artifact_digest, build_font_manifest, bundled_font_assets, bytes_digest,
    embedded_font_assets, font_asset_for, verified_server_font_assets,
    font_validation_scope,
)
from services.svg_templates.frame_ooxml import (
    OoxmlFrameContext, group_leaves, shape_bounds, shape_id, svg_node, xml_digest,
)

MAX_TEMPLATE_PAGES = 100
#: Pages analysed when the request names none: page 1 and the first page of
#: every other slide layout. A template is its furniture, not every page.
DEFAULT_REPRESENTATIVE_PAGES = 12
MAX_TEMPLATE_ASSET_BYTES = 80 * 1024 * 1024
MAX_TEMPLATE_PACKAGE_BYTES = 64 * 1024 * 1024
_DYNAMIC = {"title": "title", "ctrTitle": "title", "sldNum": "page_number"}
_FIXED_PLACEHOLDERS = {*_DYNAMIC, "ftr", "dt", "hdr"}
_LATENT_PLACEHOLDERS = {"ftr", "dt", "hdr", "sldNum"}


def _children(root):
    tree = root.find("p:cSld/p:spTree", NS)
    return tuple(child for child in tree if etree.QName(child).localname not in {"nvGrpSpPr", "grpSpPr", "extLst"}) if tree is not None else ()


def _latent_placeholder_visibility(context, ancestry):
    """A master placeholder is a definition, not an instantiated slide field.

    Actual slide instances qualify unless the nearest explicit header/footer
    flag disables them. A slide can also explicitly enable a field inherited
    from its layout/master. Master/layout defaults alone never instantiate it.
    """
    slide_part = ancestry[0]
    instances = {ph.type for shape in _children(context.root(slide_part))
                 if (ph := placeholder_of(shape)) is not None and ph.type in _LATENT_PLACEHOLDERS}
    flags, explicitly_enabled_on_slide, nearest_part = {}, set(), {}
    for part in ancestry:
        root = context.root(part)
        header_footer = root.find("p:hf", NS)
        if header_footer is not None:
            for field in _LATENT_PLACEHOLDERS:
                raw = header_footer.get(field)
                if raw is None:
                    continue
                if raw not in {"true", "false", "1", "0"}:
                    raise ValueError("svg_template_header_footer_visibility_invalid")
                flags.setdefault(field, raw in {"true", "1"})
                if part == slide_part and raw in {"true", "1"}:
                    explicitly_enabled_on_slide.add(field)
        for shape in _children(root):
            ph = placeholder_of(shape)
            if ph is not None and ph.type in _LATENT_PLACEHOLDERS:
                nearest_part.setdefault(ph.type, part)
    return {field: nearest_part.get(field) for field in _LATENT_PLACEHOLDERS
            if flags.get(field, True) and (field in instances or field in explicitly_enabled_on_slide)}


def _repeat_key(shape, package, part):
    """Non-text furniture repeats without OOXML object id/name differences."""
    from copy import deepcopy
    copied = deepcopy(shape)
    for marker in copied.findall(".//p:cNvPr", NS):
        marker.attrib.clear()
    relationships = _relationship_map(package, part)
    for blip in copied.findall(".//a:blip", NS):
        key = f"{{{R_NS}}}embed"
        relation = relationships.get(blip.get(key, ""))
        if relation is not None and relation[1].endswith("/image"):
            blip.set(key, bytes_digest(package.read(relation[0])))
    # Text is deliberately retained: matching coordinates alone cannot turn
    # changing body content into fixed furniture.
    return xml_digest(copied)


def _background(context, ancestry, frame_id):
    for part in ancestry:
        node = context.root(part).find("p:cSld/p:bg", NS)
        if node is None:
            continue
        properties = node.find("p:bgPr", NS)
        if properties is None:
            reference = node.find("p:bgRef", NS)
            theme = context.theme(part)
            if reference is None or theme is None:
                raise ValueError("svg_template_background_style_unsupported")
            index = int(reference.get("idx", "0"))
            # DrawingML's style matrix uses 1-based fill indices; background
            # fills have the distinct 1001-based index range.
            category = "bgFillStyleLst" if index >= 1001 else "fillStyleLst"
            offset = index - 1001 if index >= 1001 else index - 1
            choices = theme.find("a:themeElements/a:fmtScheme/a:" + category, NS)
            if choices is None or offset < 0 or offset >= len(choices):
                raise ValueError("svg_template_background_style_unsupported")
            fill = choices[offset]
            if etree.QName(fill).localname != "solidFill":
                raise ValueError("svg_template_background_unsupported")
            placeholder = context.color(next(iter(reference), None), part)
            color = context.color(fill, part, placeholder=placeholder)
        else:
            fill = properties.find("a:solidFill", NS)
            if fill is None or any(etree.QName(child).localname not in {"solidFill", "effectLst"} or
                                   (etree.QName(child).localname == "effectLst" and len(child)) for child in properties):
                raise ValueError("svg_template_background_unsupported")
            color = context.color(fill, part)
        bounds = FrameRect(x=0, y=0, width=context.canvas.width_px, height=context.canvas.height_px)
        svg = svg_node("rect", id=frame_id + "-background", x=0, y=0,
                       width=bounds.width, height=bounds.height, fill=color)
        return SvgTemplateFrameElement(element_id=frame_id + "-background", source_part=part,
            source_shape_id="background", source_xml_digest=xml_digest(node), kind="rect",
            layer="background", bounds=bounds, svg=etree.tostring(svg, encoding="unicode"),
            size_policy="span-between-anchors")
    # OOXML's unstyled slide background is white, not transparent.
    bounds = FrameRect(x=0, y=0, width=context.canvas.width_px, height=context.canvas.height_px)
    svg = svg_node("rect", id=frame_id + "-background", x=0, y=0,
                   width=bounds.width, height=bounds.height, fill="#FFFFFF")
    return SvgTemplateFrameElement(element_id=frame_id + "-background", source_part=ancestry[0],
        source_shape_id="implicit-background", source_xml_digest=artifact_digest({"background": "FFFFFF"}),
        kind="rect", layer="background", bounds=bounds, svg=etree.tostring(svg, encoding="unicode"),
        size_policy="span-between-anchors")


def representative_pages(context, slides) -> tuple[int, ...]:
    """0-based default selection: page 1, then the first page of each other layout."""
    chosen, layouts = [], set()
    for index, part in enumerate(slides):
        layout = context.related(part, "/slideLayout")
        if index == 0 or layout not in layouts:
            chosen.append(index)
            layouts.add(layout)
        if len(chosen) >= DEFAULT_REPRESENTATIVE_PAGES:
            break
    return tuple(chosen)


def _font_substitutions(raw) -> dict:
    """``{casefolded source family: SvgTemplateFontSubstitution}`` from the request."""
    from models.svg_template_frame import SvgTemplateFontSubstitution
    result = {}
    for item in raw or ():
        record = SvgTemplateFontSubstitution.model_validate(item)
        key = record.source_family.casefold()
        if key in result or key == record.target_family.casefold():
            raise ValueError("svg_template_font_substitution_invalid")
        result[key] = record
    return result


def _selected_pages(source_pages, slide_count) -> tuple[int, ...] | None:
    if source_pages is None:
        return None
    pages = tuple(source_pages)
    if (not pages or len(pages) > MAX_TEMPLATE_PAGES or len(set(pages)) != len(pages)
            or any(type(page) is not int or not 1 <= page <= slide_count for page in pages)):
        raise ValueError("svg_template_source_pages_invalid")
    return tuple(sorted(page - 1 for page in pages))


@font_validation_scope()
def extract_template_package(pptx_path: str | Path, *, font_assets=None,
                             frame_reviews: dict[str, dict] | None = None,
                             native_fixed_text_layout: bool = False,
                             source_pages: list[int] | tuple[int, ...] | None = None,
                             font_substitutions: list[dict] | None = None) -> SvgTemplatePackage:
    """Compile frames from selected source pages (1-based ``source_pages``).

    Without ``source_pages`` a deck of at most ``DEFAULT_REPRESENTATIVE_PAGES``
    pages is analysed whole; a longer deck is analysed through its
    representative pages. Frame ids and ``source_page`` keep the original
    page numbers either way.

    ``font_substitutions`` are the person's explicit replacements for source
    fonts the server cannot use (``source_family`` -> ``target_family`` at
    ``target_weight``). They are applied to fixed text and the frame font
    manifests and recorded on the package; nothing is substituted otherwise.
    """
    if type(native_fixed_text_layout) is not bool:
        raise ValueError("svg_template_native_layout_selector_invalid")
    path = Path(pptx_path)
    report = admit_pptx_package(path)
    source_digest = bytes_digest(path.read_bytes())
    frames, evidence, package_issues = [], [], []
    with ZipFile(path) as package:
        all_slides = _ordered_slide_parts(package, report)
        typography = PptxTypographyResolver(package, relationship_map=_relationship_map)
        size = typography.slide_size()
        if size is None:
            raise ValueError("svg_template_source_canvas_missing")
        canvas = document_canvas_from_source_slide_size(width_emu=size["cx"], height_emu=size["cy"])
        candidates = tuple(bundled_font_assets() if font_assets is None else font_assets)
        font_manifest = build_font_manifest(candidates)
        context = OoxmlFrameContext(package, typography, canvas, font_manifest)
        context.native_fixed_text_layout = native_fixed_text_layout
        context.source_digest = source_digest
        substitutions = _font_substitutions(font_substitutions)
        context.font_substitutions = substitutions
        selection = _selected_pages(source_pages, len(all_slides))
        if selection is None:
            selection = (tuple(range(len(all_slides))) if len(all_slides) <= DEFAULT_REPRESENTATIVE_PAGES
                         else representative_pages(context, all_slides))
        if len(selection) > MAX_TEMPLATE_PAGES:
            raise ValueError("svg_template_page_limit_exceeded")
        slides = [all_slides[index] for index in selection]
        page_numbers = dict(zip(slides, selection, strict=True))
        observed = {}
        for part in slides:
            faces = set()
            ancestry = context.ancestry(part)
            latent_visible = _latent_placeholder_visibility(context, ancestry)
            for ancestor in ancestry:
                for shape in context.root(ancestor).findall(".//p:sp", NS):
                    placeholder = placeholder_of(shape)
                    if placeholder is not None and placeholder.type in _LATENT_PLACEHOLDERS:
                        if latent_visible.get(placeholder.type) != ancestor:
                            continue
                    elif ancestor != part and placeholder is not None:
                        continue
                    marker = shape.find(".//p:cNvPr", NS)
                    if marker is not None and marker.get("hidden", "0") in {"1", "true"}:
                        continue
                    text_context = typography.shape_context(shape, part_name=part)
                    for paragraph in shape.findall("p:txBody/a:p", NS):
                        for run in paragraph:
                            if etree.QName(run).localname not in {"r", "fld"}:
                                continue
                            style = text_context.run_style(run, paragraph)
                            if style.get("fontFamily"):
                                faces.add(context.resolve_face(style["fontFamily"], bool(style.get("bold"))))
            observed[part] = faces
        required_faces = set().union(*observed.values()) if observed else set()
        # Explicit supplied bytes have priority, then already verified server
        # bytes, then permissible embedded bytes. Never alias by filename alone.
        embedded = embedded_font_assets(package)
        missing_faces = {face for face in required_faces if not any(
            face[0].casefold() in {name.casefold() for name in (asset.family, *asset.family_aliases)}
            and face[1] == asset.weight for asset in (*candidates, *embedded))}
        font_discovery_diagnostics = {}
        installed = verified_server_font_assets(missing_faces, diagnostics=font_discovery_diagnostics)
        acquired = (*candidates, *installed, *embedded)
        unique = {}
        for asset in acquired:
            aliases = {name.casefold() for name in (asset.family, *asset.family_aliases)}
            if not required_faces or any(f.casefold() in aliases and w == asset.weight for f, w in required_faces):
                unique.setdefault((asset.family.casefold(), asset.weight), asset)
        font_manifest = build_font_manifest(tuple(unique.values()))
        context.font_manifest = font_manifest
        # Repetition is judged over the whole deck: more pages, better evidence.
        repeats = Counter(key for part in all_slides for key in {_repeat_key(shape, package, part) for shape in _children(context.root(part))})
        for slide_part in slides:
            page_index = page_numbers[slide_part]
            frame_id = f"frame-{page_index + 1}"
            ancestry = context.ancestry(slide_part)
            review = (frame_reviews or {}).get(frame_id, {})
            if set(review) - {"fixed_shape_ids", "body_region", "role"}:
                raise ValueError("svg_template_frame_review_field_invalid")
            selected = review.get("fixed_shape_ids")
            selected = {key if "#" in key else slide_part + "#" + key for key in selected} if selected is not None else None
            available = {part + "#" + shape_id(shape) for part in ancestry for shape in _children(context.root(part))}
            if selected is not None and not selected <= available:
                raise ValueError("svg_template_fixed_shape_unknown")
            issues = []
            context.source_slide_part = slide_part
            required_families = {family for family, _ in observed[slide_part]}
            for family, weight in observed[slide_part]:
                try:
                    font_asset_for(font_manifest, family, weight)
                except ValueError:
                    issues.append(FrameIssue(reason_code=font_discovery_diagnostics.get((family, weight),
                        "svg_template_font_unavailable"), severity="blocked", source_part=slide_part))
            elements, fixed_bounds, body_ids, body_bounds, body_kinds = [], [], [], [], []
            try:
                elements.append(_background(context, ancestry, "svg-system-template-" + frame_id))
            except ValueError as error:
                issues.append(FrameIssue(reason_code=str(error), severity="blocked", source_part=slide_part))
            # Placeholder inheritance is instantiated once, at the nearest
            # slide/layout occurrence. Non-placeholder master/layout furniture
            # retains its actual paint order behind the slide.
            nearest = {}
            for part in ancestry:
                for shape in _children(context.root(part)):
                    ph = placeholder_of(shape)
                    if ph is not None:
                        nearest.setdefault((ph.type, ph.idx), (part, shape_id(shape)))
            latent_visible = _latent_placeholder_visibility(context, ancestry)
            for part in reversed(ancestry):
                root = context.root(part)
                slide_root = context.root(slide_part)
                if part != slide_part and slide_root.get("showMasterSp", "1") in {"0", "false"}:
                    continue
                layout_part = next((item for item in ancestry if "/slideLayouts/" in item), None)
                if "/slideMasters/" in part and layout_part and context.root(layout_part).get("showMasterSp", "1") in {"0", "false"}:
                    continue
                for shape in _children(root):
                    marker = shape.find(".//p:cNvPr", NS)
                    if marker is not None and marker.get("hidden", "0") in {"1", "true"}:
                        continue
                    ph = placeholder_of(shape)
                    # Suppressed or never-instantiated footer definitions are
                    # neither fixed content nor body design evidence. Overrides
                    # cannot make an invisible original element count as kept.
                    if ph is not None and ph.type in _LATENT_PLACEHOLDERS and latent_visible.get(ph.type) != part:
                        if selected is not None and part + "#" + shape_id(shape) in selected:
                            issues.append(FrameIssue(reason_code="svg_template_hidden_placeholder_selected",
                                severity="blocked", source_part=part, shape_id=shape_id(shape)))
                        continue
                    kind = etree.QName(shape).localname
                    try:
                        bounds = shape_bounds(shape, typography, part, context.scale)
                    except ValueError:
                        bounds = None
                    fixed = part != slide_part
                    if ph is not None:
                        fixed = ph.type in _FIXED_PLACEHOLDERS and nearest[(ph.type, ph.idx)] == (part, shape_id(shape))
                        # Master title/body prompt text must never become user content.
                        if part != slide_part and ph.type in {"title", "ctrTitle"}:
                            fixed = False
                    elif part == slide_part and bounds is not None:
                        edge = bounds.y + bounds.height <= canvas.height_px * .25 or bounds.y >= canvas.height_px * .78
                        preset = shape.find("p:spPr/a:prstGeom", NS)
                        page_background = (preset is not None and preset.get("prst") == "rect"
                            and abs(bounds.x) <= .01 and abs(bounds.y) <= .01
                            and abs(bounds.width - canvas.width_px) <= .01 and abs(bounds.height - canvas.height_px) <= .01)
                        fixed = page_background or (edge and (repeats[_repeat_key(shape, package, part)] >= 2 or len(all_slides) == 1))
                    if selected is not None:
                        fixed = part + "#" + shape_id(shape) in selected
                    if not fixed:
                        if part == slide_part:
                            body_ids.append(part + "#" + shape_id(shape))
                            body_kinds.append(kind)
                            if bounds is not None:
                                body_bounds.append(bounds)
                        continue
                    if bounds is not None:
                        fixed_bounds.append(bounds)
                        if (kind == "pic" and bounds.width * bounds.height >= canvas.width_px * canvas.height_px * .7):
                            issues.append(FrameIssue(reason_code="svg_template_fullslide_raster_forbidden", severity="blocked",
                                                     source_part=part, shape_id=shape_id(shape)))
                            continue
                    if kind == "grpSp":
                        # A group is flattened: each leaf is converted at its
                        # slide position, so text inside it stays text.
                        for leaf, leaf_bounds, failure in group_leaves(shape, context.scale):
                            if leaf_bounds is not None:
                                fixed_bounds.append(leaf_bounds)
                            if failure is not None:
                                issues.append(FrameIssue(reason_code=failure, severity="blocked",
                                                         source_part=part, shape_id=shape_id(leaf)))
                                continue
                            try:
                                elements.append(context.emit(leaf, part, bounds=leaf_bounds,
                                    element_id=f"svg-system-template-{frame_id}-fixed-{len(elements)}"))
                            except ValueError as error:
                                issues.append(FrameIssue(reason_code=str(error), severity="blocked",
                                                         source_part=part, shape_id=shape_id(leaf)))
                        continue
                    try:
                        element = context.emit(shape, part, element_id=f"svg-system-template-{frame_id}-fixed-{len(elements)}",
                                               dynamic_key=_DYNAMIC.get(ph.type) if ph else None)
                        if (element.kind == "rect" and bounds is not None and abs(bounds.x) <= .01 and abs(bounds.y) <= .01
                                and abs(bounds.width - canvas.width_px) <= .01 and abs(bounds.height - canvas.height_px) <= .01):
                            paint = etree.fromstring(element.svg.encode()).get("fill", "none")
                            if paint != "none" and any(previous.layer == "foreground" for previous in elements):
                                issues.append(FrameIssue(reason_code="svg_template_background_z_order_unsupported",
                                    severity="blocked", source_part=part, shape_id=shape_id(shape)))
                            element = element.model_copy(update={"layer": "background", "size_policy": "span-between-anchors"})
                            fixed_bounds.pop()
                        elements.append(element)
                    except ValueError as error:
                        issues.append(FrameIssue(reason_code=str(error), severity="blocked", source_part=part, shape_id=shape_id(shape)))
            margin = canvas.safe_area.x
            top, bottom = canvas.safe_area.y, canvas.safe_area.y + canvas.safe_area.height
            forbidden = []
            for bounds in fixed_bounds:
                if bounds.y + bounds.height <= canvas.height_px * .4:
                    top = max(top, bounds.y + bounds.height + margin * .15)
                elif bounds.y >= canvas.height_px * .6:
                    bottom = min(bottom, bounds.y - margin * .15)
                else:
                    forbidden.append(bounds)
            body_region = FrameRect(x=margin, y=top, width=canvas.safe_area.width, height=max(0, bottom - top))
            if "body_region" in review:
                body_region = FrameRect.model_validate(review["body_region"])
                if (body_region.x < 0 or body_region.y < 0 or body_region.width <= 0 or body_region.height <= 0
                        or body_region.x + body_region.width > canvas.width_px
                        or body_region.y + body_region.height > canvas.height_px):
                    raise ValueError("svg_template_body_region_invalid")
                forbidden = [box for box in fixed_bounds if
                    box.x < body_region.x + body_region.width and body_region.x < box.x + box.width
                    and box.y < body_region.y + body_region.height and body_region.y < box.y + box.height]
            if body_region.height < canvas.height_px * .15:
                issues.append(FrameIssue(reason_code="svg_template_body_space_insufficient", severity="blocked"))
            if not any(element.layer == "foreground" for element in elements):
                issues.append(FrameIssue(reason_code="svg_template_fixed_furniture_missing", severity="blocked"))
            issues.extend((FrameIssue(reason_code="source_render_review_required", source_part=slide_part),
                           FrameIssue(reason_code="classification_review_required", source_part=slide_part)))
            frame = SvgTemplateFrame(schema_version="svg-template-frame/v2" if native_fixed_text_layout else "svg-template-frame/v1",
                frame_id=frame_id, source_page=page_index, source_digest=source_digest,
                source_canvas=canvas, role=review.get("role", "cover" if page_index == 0 else "body"), elements=tuple(elements),
                body_region=body_region, forbidden_regions=tuple(forbidden),
                dynamic_fields=tuple(sorted({e.dynamic_key for e in elements if e.dynamic_key})),
                font_manifest=build_font_manifest(tuple(asset for asset in font_manifest.assets
                    if not observed[slide_part] or any(family.casefold() in {name.casefold() for name in (asset.family, *asset.family_aliases)}
                           and weight == asset.weight for family, weight in observed[slide_part])),
                    required_families=tuple(required_families)),
                issues=tuple(issues), digest="")
            frames.append(frame)
            evidence.append(SvgTemplateBodyEvidence(source_page=page_index, source_part=slide_part,
                source_digest=xml_digest(context.root(slide_part)), body_shape_ids=tuple(body_ids),
                body_bounds=tuple(body_bounds), body_kinds=tuple(body_kinds)))
        # The manifest records every required family, including unresolved ones.
        font_manifest = build_font_manifest(font_manifest.assets,
            required_families=tuple({family for frame in frames for family in frame.font_manifest.required_families}))
        frames = [frame.model_copy(update={"digest": artifact_digest(frame)}) for frame in frames]
        assets = tuple(sorted(context.assets.values(), key=lambda a: a.asset_id))
        if sum(len(asset.data_base64) * 3 // 4 for asset in assets) > MAX_TEMPLATE_ASSET_BYTES:
            raise ValueError("svg_template_asset_bytes_exceeded")
    if not frames:
        raise ValueError("svg_template_frames_missing")
    if set(frame_reviews or {}) - {frame.frame_id for frame in frames}:
        raise ValueError("svg_template_frame_review_unknown")
    applied = tuple(record for key, record in substitutions.items() if key in context.substituted_families)
    result = SvgTemplatePackage(schema_version="svg-template-package/v2" if native_fixed_text_layout else "svg-template-package/v1",
        font_substitutions=applied,
        compiler_version="ooxml-frame/2" if native_fixed_text_layout else "ooxml-frame/1",
        source_digest=source_digest, source_canvas=canvas, frames=tuple(frames),
        body_evidence=tuple(evidence), assets=assets, font_manifest=font_manifest,
        issues=tuple(package_issues), registration_state="review-required", digest="")
    total_bytes = 0
    for chunk in json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).iterencode(result.model_dump(mode="json")):
        total_bytes += len(chunk.encode("utf-8"))
        if total_bytes > MAX_TEMPLATE_PACKAGE_BYTES:
            raise ValueError("svg_template_package_bytes_exceeded")
    return result.model_copy(update={"digest": artifact_digest(result)})


# Public entrypoints kept together for callers; implementation stays separated.
from services.svg_templates.frame_composition import (  # noqa: E402
    adapt_frame, compose_frame_svg, validate_body_paint,
)

adapt_template_frame = adapt_frame
