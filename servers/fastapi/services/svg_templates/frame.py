"""Deterministic fixed-frame extraction; original body layouts remain evidence."""
from __future__ import annotations

from collections import Counter
import json
import re
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from models.svg_template_frame import (
    FrameIssue, FrameRect, SvgTemplateBodyEvidence, SvgTemplateFrame,
    SvgTemplateFrameElement, SvgTemplatePackage, SvgTemplateUnavailableFont,
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


def _slot_key(shape, package, part):
    """``_repeat_key`` without the text: the same text box at the same place."""
    from copy import deepcopy
    copied = deepcopy(shape)
    for text in copied.iterfind(".//a:t", NS):
        text.text = ""
    return _repeat_key(copied, package, part)


def _shape_text(shape) -> str:
    return "".join(text.text or "" for text in shape.iterfind(".//a:t", NS)).strip()


def _variable_slots(context, package, slides) -> set[str]:
    """Slide-level text slots whose text differs between slides of the deck.

    A requirement-ID box drawn at one place on 74 pages with 46 different
    values is page content, even where a value repeats on several pages, so it
    must not become fixed furniture (the real proposal deck's "요구사항 ID").
    """
    texts: dict[str, set[str]] = {}
    for part in slides:
        for shape in _children(context.root(part)):
            if shape.find(".//a:t", NS) is None or placeholder_of(shape) is not None:
                continue
            texts.setdefault(_slot_key(shape, package, part), set()).add(_shape_text(shape))
    return {key for key, values in texts.items() if len(values) > 1}


_CHAPTER_NUMBER = re.compile(r"^(?:[0-9]{1,2}|[\u2160-\u216B]|[IVXivx]{1,4})[.)]?$")


def _simple_text(shape) -> str | None:
    """The text of a one-paragraph, one-run shape (what a dynamic field can replace)."""
    paragraphs = [paragraph for paragraph in shape.iterfind("p:txBody/a:p", NS)
                  if "".join(text.text or "" for text in paragraph.iterfind(".//a:t", NS)).strip()]
    if len(paragraphs) != 1:
        return None
    runs = [run for run in paragraphs[0] if etree.QName(run).localname in {"r", "fld", "br"}]
    if len(runs) != 1 or etree.QName(runs[0]).localname != "r":
        return None
    return _shape_text(shape) or None


class _ChapterSlots:
    """Text-box slots shared by chapter layouts: ``key_for(bounds)`` -> dynamic key.

    Chapter layouts are usually copies of one another, but PowerPoint gives
    every copy its own creation id, may mark it user-drawn, some keep the tab
    inside a group (child coordinates) and a longer name may get a wider box.
    A slot is therefore matched by its centre on the slide (within 8 px) and
    its height (within 4 px).
    """

    def __init__(self):
        self.slots: list[tuple[float, float, float, str]] = []

    @staticmethod
    def _close(slot, bounds) -> bool:
        x, y, height = slot[:3]
        return (abs(x - (bounds.x + bounds.width / 2)) <= 8 and abs(y - (bounds.y + bounds.height / 2)) <= 8
                and abs(height - bounds.height) <= 4)

    def key_for(self, bounds) -> str | None:
        if bounds is None:
            return None
        return next((slot[3] for slot in self.slots if self._close(slot, bounds)), None)

    def add(self, bounds) -> str:
        key = self.key_for(bounds)
        if key is None:
            key = f"slot-{len(self.slots)}"
            self.slots.append((bounds.x + bounds.width / 2, bounds.y + bounds.height / 2, bounds.height, key))
        return key


def _layout_text_boxes(context, typography, part):
    """``(shape, slide bounds)`` of every plain text box of a layout, groups flattened."""
    from services.svg_templates.frame_ooxml import group_leaves, shape_bounds
    for shape in _children(context.root(part)):
        kind = etree.QName(shape).localname
        if kind == "grpSp":
            for leaf, bounds, failure in group_leaves(shape, context.scale):
                if failure is None and etree.QName(leaf).localname == "sp":
                    yield leaf, bounds
        elif kind == "sp" and placeholder_of(shape) is None:
            try:
                yield shape, shape_bounds(shape, typography, part, context.scale)
            except ValueError:
                continue


def _chapter_slots(context, package, typography):
    """Layout text slots that name a chapter: ``(geometry, {slot key: dynamic key})``.

    A deck with one layout per chapter (the real proposal deck: Ⅰ 회사소개 …
    Ⅵ 추가 제안) puts each chapter's number and name in the same text box at
    the same place on every chapter layout. Such a slot is chapter furniture
    whose text the generated deck must replace with its own chapters, not the
    source's. A number-like slot is ``chapter_number``, any other
    ``chapter_title``.
    """
    texts: dict[str, dict[str, str]] = {}
    geometry = _ChapterSlots()
    layouts = sorted(name for name in package.namelist()
                     if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", name))
    for part in layouts:
        for shape, bounds in _layout_text_boxes(context, typography, part):
            text = _simple_text(shape)
            if text is not None and bounds is not None:
                texts.setdefault(geometry.add(bounds), {})[part] = text
    slots = {}
    for key, by_layout in texts.items():
        values = list(by_layout.values())
        if len(values) >= 2 and len(set(values)) >= 2:
            # An appendix layout may say "별첨" where chapters say Ⅰ…Ⅵ.
            numbers = sum(bool(_CHAPTER_NUMBER.match(value)) for value in values)
            slots[key] = "chapter_number" if numbers * 3 >= len(values) * 2 else "chapter_title"
    return geometry, slots


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


#: Conversion failures about how decoration is drawn, not what it says: such a
#: fixed shape may be preserved as an image (design D-1). Text never is.
RASTERIZABLE = frozenset({
    "svg_template_paint_unsupported", "svg_template_effect_unsupported",
    "svg_template_image_crop_unsupported", "svg_template_image_effect_unsupported",
    "svg_template_image_format_unsupported", "svg_template_image_stretch_unsupported",
    "svg_template_primitive_unsupported", "svg_template_geometry_missing",
    "svg_template_transform_unsupported", "svg_template_line_style_unsupported",
    "svg_template_color_transform_unsupported", "svg_template_color_unsupported",
    "svg_template_color_unresolved", "svg_template_style_unresolved",
    "svg_template_background_unsupported", "svg_template_background_style_unsupported",
    "svg_template_image_bytes_exceeded", "svg_template_image_pixels_exceeded",
})


def _emu(bounds, context):
    if bounds is None:
        return None
    return tuple(round(value * context.scale) for value in (bounds.x, bounds.y, bounds.width, bounds.height))


def _overlaps(a, b) -> bool:
    """Overlapping or touching: shapes sharing an edge blend at it, so they
    stay one image."""
    return a.x <= b.x + b.width and b.x <= a.x + a.width and a.y <= b.y + b.height and b.y <= a.y + a.height


class _RasterRuns:
    """Consecutive raster candidates of one frame, in paint order."""

    def __init__(self, *, enabled: bool):
        self.enabled = enabled
        self.runs: list[dict] = []
        self.open: dict | None = None

    def close(self):
        self.open = None

    def background(self, elements, part, reason) -> bool:
        if not self.enabled or reason not in RASTERIZABLE:
            return False
        self.runs.append({"position": len(elements), "shapes": [], "reasons": {reason}, "boxes": [],
                          "background_part": part, "layer": "background", "refs": [part + "#background"]})
        self.open = None
        return True

    def add(self, elements, part, shape, reason, *, bounds=None, emu=None, hide_text=False,
            background=False) -> bool:
        from services.svg_templates.raster_preservation import RasterShape
        if not self.enabled or reason not in RASTERIZABLE:
            return False
        layer = "background" if background else "foreground"
        # Separate images keep paint order exactly, so consecutive decoration
        # is merged only where known boxes overlap or touch (blending at the
        # shared edge). One image spanning a top tab and a bottom logo would
        # reserve the whole body; a shape whose box is unknown (a rotated
        # tab) is never merged.
        separate = (self.open is not None and (
            bounds is None or len(self.open["boxes"]) != len(self.open["shapes"])
            or not any(_overlaps(bounds, box) for box in self.open["boxes"])))
        if self.open is None or self.open["layer"] != layer or separate:
            self.open = {"position": len(elements), "shapes": [], "reasons": set(), "background_part": None,
                         "layer": layer, "refs": [], "boxes": []}
            self.runs.append(self.open)
        if bounds is not None:
            self.open["boxes"].append(bounds)
        self.open["shapes"].append(RasterShape(part=part, element=shape, emu_rect=emu, hide_text=hide_text))
        if bounds is not None and not hide_text:
            # Replaced by the run image's own bounds once it is rendered. A
            # shape whose text stays vector keeps its bounds for that text.
            self.open.setdefault("source_bounds", []).append(bounds)
        self.open["reasons"].add(reason)
        self.open["refs"].append(part + "#" + shape_id(shape))
        return True


def _emit_fixed(context, runs, elements, issues, frame_id, part, shape, bounds, *, emu, dynamic_key=None):
    """Convert one fixed shape. With a renderer, paint the converter cannot draw
    becomes an image and any text the shape carries stays vector text above it."""
    try:
        elements.append(context.emit(shape, part, bounds=bounds, dynamic_key=dynamic_key,
                                     element_id=f"svg-system-template-{frame_id}-fixed-{len(elements)}"))
        runs.close()
        return
    except ValueError as error:
        reason = str(error)
    if reason == "svg_template_text_shape_paint_unsupported" and runs.enabled:
        from copy import deepcopy
        paint = deepcopy(shape)
        body = paint.find("p:txBody", NS)
        if body is not None:
            paint.remove(body)
        try:
            elements.append(context.emit(paint, part, bounds=bounds,
                                         element_id=f"svg-system-template-{frame_id}-fixed-{len(elements)}"))
            runs.close()
        except ValueError as paint_error:
            if not runs.add(elements, part, shape, str(paint_error), bounds=bounds, emu=emu, hide_text=True):
                issues.append(FrameIssue(reason_code=str(paint_error), severity="blocked",
                                         source_part=part, shape_id=shape_id(shape)))
                return
        try:
            elements.append(context.emit(shape, part, bounds=bounds, dynamic_key=dynamic_key, text_only=True,
                                         element_id=f"svg-system-template-{frame_id}-fixed-{len(elements)}"))
            runs.close()
        except ValueError as text_error:
            issues.append(FrameIssue(reason_code=str(text_error), severity="blocked",
                                     source_part=part, shape_id=shape_id(shape)))
        return
    if not runs.add(elements, part, shape, reason, bounds=bounds, emu=emu):
        issues.append(FrameIssue(reason_code=reason, severity="blocked", source_part=part, shape_id=shape_id(shape)))


def _apply_raster_runs(renderer, path, canvas, context, pending) -> None:
    """Render every frame's runs at once and insert the images in paint order."""
    import base64
    from io import BytesIO

    from PIL import Image

    from models.svg_template_frame import SvgTemplateAsset, SvgTemplateRasterPreservation
    from services.svg_templates.raster_preservation import RASTER_SCALE, RasterRequest

    requests = [RasterRequest(key=f"{state['frame_id']}:{index}", shapes=tuple(run["shapes"]),
                              background_part=run["background_part"])
                for state in pending for index, run in enumerate(state["runs"].runs)]
    if not requests:
        return
    try:
        results = renderer(path, canvas, requests)
    except ValueError as error:
        for state in pending:
            for run in state["runs"].runs:
                for ref in run["refs"]:
                    part, shape = ref.split("#", 1)
                    state["issues"].append(FrameIssue(reason_code=str(error), severity="blocked",
                                                      source_part=part, shape_id=shape))
        return
    page_area = canvas.width_px * canvas.height_px
    for state in pending:
        frame_id, elements = state["frame_id"], state["elements"]
        # A foreground run painted before every foreground element and covering
        # most of the page (a picture-filled divider shape) is a background
        # layer under the body (design D-2), decided in paint order.
        foreground_before, cursor = False, 0
        for index, run in enumerate(state["runs"].runs):
            result = results.get(f"{frame_id}:{index}")
            foreground_before = foreground_before or any(
                item.layer == "foreground" for item in elements[cursor:run["position"]])
            cursor = run["position"]
            if (run["layer"] == "foreground" and result is not None and not foreground_before
                    and result.width * result.height >= page_area * .7):
                run["layer"] = "background"
            foreground_before = foreground_before or (run["layer"] == "foreground" and result is not None)
        for run in state["runs"].runs:
            for box in run.get("source_bounds", ()):
                if box in state["fixed_bounds"]:
                    state["fixed_bounds"].remove(box)
        # Insert from the last run back, so earlier positions stay valid.
        for index, run in reversed(list(enumerate(state["runs"].runs))):
            result = results.get(f"{frame_id}:{index}")
            if result is None:
                continue
            digest = bytes_digest(result.data)
            with Image.open(BytesIO(result.data)) as image:
                width, height = image.size
            asset = SvgTemplateAsset(asset_id="image-" + digest[7:23], digest=digest, media_type=result.media_type,
                                     data_base64=base64.b64encode(result.data).decode("ascii"),
                                     width=width, height=height)
            context.assets[asset.asset_id] = asset
            bounds = FrameRect(x=result.x, y=result.y, width=result.width, height=result.height)
            element_id = f"svg-system-template-{frame_id}-raster-{index}"
            node = svg_node("image", id=element_id, x=bounds.x, y=bounds.y, width=round(bounds.width),
                            height=round(bounds.height), href=f"data:{result.media_type};base64,{asset.data_base64}",
                            preserveAspectRatio="xMidYMid meet")
            background = run["layer"] == "background"
            element = SvgTemplateFrameElement(element_id=element_id, source_part=run["refs"][0].split("#")[0],
                source_shape_id=",".join(ref.split("#", 1)[1] for ref in run["refs"])[:200],
                source_xml_digest=artifact_digest({"refs": run["refs"], "image": digest}), kind="image",
                layer=run["layer"], bounds=bounds, svg=etree.tostring(node, encoding="unicode"),
                anchor_x="right" if bounds.x > canvas.width_px * .65 else "left",
                anchor_y="bottom" if bounds.y >= canvas.height_px * .7 else "top",
                size_policy="aspect-locked",
                raster_preservation=SvgTemplateRasterPreservation(renderer=result.renderer,
                    renderer_version=result.renderer_version, scale=float(RASTER_SCALE),
                    approximate=result.approximate, shape_refs=tuple(run["refs"]),
                    reasons=tuple(sorted(run["reasons"]))))
            if background and run["background_part"] is not None:
                # The slide background run replaces the implicit white background.
                elements[:] = [item for item in elements if item.source_shape_id != "implicit-background"]
                elements.insert(0, element)
                continue
            elements.insert(run["position"], element)
            if not background:
                state["fixed_bounds"].append(bounds)


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


#: The server face review suggests for a source font it cannot use (D-3).
SUGGESTED_SUBSTITUTE_FAMILY = "Pretendard"


def suggested_substitute_weight(family: str, weight: int) -> int:
    """Nearest bundled weight (400/700): a weight digit in the family name
    ("Paperlogy 7 Bold") wins over the run's bold flag."""
    import re
    digit = re.search(r"(?<!\d)([1-9])(?!\d)", family)
    value = int(digit.group(1)) * 100 if digit else weight
    return 700 if value >= 600 else 400


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
                             font_substitutions: list[dict] | None = None,
                             raster_renderer=None) -> SvgTemplatePackage:
    """Compile frames from selected source pages (1-based ``source_pages``).

    Without ``source_pages`` a deck of at most ``DEFAULT_REPRESENTATIVE_PAGES``
    pages is analysed whole; a longer deck is analysed through its
    representative pages. Frame ids and ``source_page`` keep the original
    page numbers either way.

    ``font_substitutions`` are the person's explicit replacements for source
    fonts the server cannot use (``source_family`` -> ``target_family`` at
    ``target_weight``). They are applied to fixed text and the frame font
    manifests and recorded on the package; nothing is substituted otherwise.

    ``raster_renderer`` (``raster_preservation.render_raster_requests``)
    preserves unsupported fixed decoration as images cut from a source
    render; without it such decoration blocks the frame as before.
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
        variable_slots = _variable_slots(context, package, all_slides)
        chapter_geometry, chapter_keys = _chapter_slots(context, package, typography)

        def chapter_slot(bounds):
            return chapter_keys.get(chapter_geometry.key_for(bounds))
        pending, unavailable = [], set()
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
            if selected is not None:
                # Review lists what the frame shows: a group's leaves (groups
                # are flattened) and the shapes of a raster run. Each counts
                # as its top-level shape. The slide background is always kept.
                owners = {part + "#" + leaf.get("id"): part + "#" + shape_id(shape)
                          for part in ancestry for shape in _children(context.root(part))
                          if etree.QName(shape).localname == "grpSp"
                          for leaf in shape.iterfind(".//p:cNvPr", NS) if leaf.get("id")}
                selected = {owners.get(key, key) for key in selected
                            if key.rsplit("#", 1)[1] not in {"background", "implicit-background"}}
            if selected is not None and not selected <= available:
                raise ValueError("svg_template_fixed_shape_unknown")
            issues = []
            context.source_slide_part = slide_part
            required_families = {family for family, _ in observed[slide_part]}
            for family, weight in observed[slide_part]:
                try:
                    font_asset_for(font_manifest, family, weight)
                except ValueError:
                    unavailable.add((family, weight))
                    issues.append(FrameIssue(reason_code=font_discovery_diagnostics.get((family, weight),
                        "svg_template_font_unavailable"), severity="blocked", source_part=slide_part))
            elements, fixed_bounds, body_ids, body_bounds, body_kinds = [], [], [], [], []
            runs = _RasterRuns(enabled=raster_renderer is not None)
            try:
                elements.append(_background(context, ancestry, "svg-system-template-" + frame_id))
            except ValueError as error:
                background_part = next((part for part in ancestry
                                        if context.root(part).find("p:cSld/p:bg", NS) is not None), None)
                if not (background_part and runs.background(elements, background_part, str(error))):
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
                        # A picture covering the slide at the bottom of the
                        # slide's own paint order is its background (a cover or
                        # divider photo), not body content (design D-2).
                        page_background = page_background or (
                            runs.enabled and kind == "pic" and shape is _children(root)[0]
                            and bounds.width * bounds.height >= canvas.width_px * canvas.height_px * .7)
                        variable = (shape.find(".//a:t", NS) is not None
                                    and _slot_key(shape, package, part) in variable_slots)
                        fixed = page_background or (edge and not variable and (
                            repeats[_repeat_key(shape, package, part)] >= 2 or len(all_slides) == 1))
                    if selected is not None:
                        fixed = part + "#" + shape_id(shape) in selected
                    if not fixed:
                        if part == slide_part:
                            body_ids.append(part + "#" + shape_id(shape))
                            body_kinds.append(kind)
                            if bounds is not None:
                                body_bounds.append(bounds)
                        continue
                    full_slide_picture = (kind == "pic" and bounds is not None
                                          and bounds.width * bounds.height >= canvas.width_px * canvas.height_px * .7)
                    if bounds is not None and not full_slide_picture:
                        fixed_bounds.append(bounds)
                    if full_slide_picture:
                        # A text-free picture covering the slide is a background
                        # layer under the body (D-2), never furniture over it.
                        if runs.enabled and not any(element.layer == "foreground" for element in elements):
                            try:
                                element = context.emit(shape, part, element_id=f"svg-system-template-{frame_id}-fixed-{len(elements)}")
                                elements.append(element.model_copy(update={"layer": "background",
                                                                           "size_policy": "span-between-anchors"}))
                                runs.close()
                            except ValueError as error:
                                if not runs.add(elements, part, shape, str(error), bounds=bounds,
                                                emu=_emu(bounds, context), background=True):
                                    issues.append(FrameIssue(reason_code=str(error), severity="blocked",
                                                             source_part=part, shape_id=shape_id(shape)))
                        else:
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
                                if not runs.add(elements, part, leaf, failure, bounds=leaf_bounds,
                                                emu=_emu(leaf_bounds, context)):
                                    issues.append(FrameIssue(reason_code=failure, severity="blocked",
                                                             source_part=part, shape_id=shape_id(leaf)))
                                continue
                            leaf_key = (chapter_slot(leaf_bounds)
                                        if "/slideLayouts/" in part and _simple_text(leaf) is not None else None)
                            _emit_fixed(context, runs, elements, issues, frame_id, part, leaf, leaf_bounds,
                                        emu=_emu(leaf_bounds, context), dynamic_key=leaf_key)
                        continue
                    dynamic_key = _DYNAMIC.get(ph.type) if ph else None
                    if dynamic_key is None and "/slideLayouts/" in part and _simple_text(shape) is not None:
                        dynamic_key = chapter_slot(bounds)
                    try:
                        element = context.emit(shape, part, element_id=f"svg-system-template-{frame_id}-fixed-{len(elements)}",
                                               dynamic_key=dynamic_key)
                        if (element.kind == "rect" and bounds is not None and abs(bounds.x) <= .01 and abs(bounds.y) <= .01
                                and abs(bounds.width - canvas.width_px) <= .01 and abs(bounds.height - canvas.height_px) <= .01):
                            paint = etree.fromstring(element.svg.encode()).get("fill", "none")
                            if paint != "none" and any(previous.layer == "foreground" for previous in elements):
                                issues.append(FrameIssue(reason_code="svg_template_background_z_order_unsupported",
                                    severity="blocked", source_part=part, shape_id=shape_id(shape)))
                            element = element.model_copy(update={"layer": "background", "size_policy": "span-between-anchors"})
                            fixed_bounds.pop()
                        elements.append(element)
                        runs.close()
                    except ValueError as error:
                        own_transform = shape.find("p:spPr/a:xfrm", NS) is not None or kind == "grpSp"
                        if str(error) == "svg_template_text_shape_paint_unsupported" and runs.enabled:
                            _emit_fixed(context, runs, elements, issues, frame_id, part, shape, bounds,
                                        emu=None if own_transform else _emu(bounds, context),
                                        dynamic_key=dynamic_key)
                        elif not runs.add(elements, part, shape, str(error), bounds=bounds,
                                          emu=None if own_transform else _emu(bounds, context)):
                            issues.append(FrameIssue(reason_code=str(error), severity="blocked", source_part=part, shape_id=shape_id(shape)))
            pending.append(dict(slide_part=slide_part, page_index=page_index, frame_id=frame_id, review=review,
                                issues=issues, elements=elements, fixed_bounds=fixed_bounds, body_ids=body_ids,
                                body_bounds=body_bounds, body_kinds=body_kinds, runs=runs,
                                required_families=required_families))
        _apply_raster_runs(raster_renderer, path, canvas, context, pending)
        for state in pending:
            slide_part, page_index, frame_id, review = (state["slide_part"], state["page_index"],
                                                        state["frame_id"], state["review"])
            issues, elements, fixed_bounds = state["issues"], state["elements"], state["fixed_bounds"]
            body_ids, body_bounds, body_kinds = state["body_ids"], state["body_bounds"], state["body_kinds"]
            required_families = state["required_families"]
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
            # A background picture is furniture too (a cover, a chapter divider).
            if not any(element.layer == "foreground" or element.kind == "image" for element in elements):
                issues.append(FrameIssue(reason_code="svg_template_fixed_furniture_missing", severity="blocked"))
            if any(element.raster_preservation is not None for element in elements):
                issues.append(FrameIssue(reason_code="svg_template_raster_preservation_review_required",
                                         source_part=slide_part))
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
        unavailable_fonts=tuple(SvgTemplateUnavailableFont(source_family=family, source_weight=weight,
            suggested_family=SUGGESTED_SUBSTITUTE_FAMILY, suggested_weight=suggested_substitute_weight(family, weight))
            for family, weight in sorted(unavailable, key=lambda face: (face[0].casefold(), face[1]))
            if family.casefold() != SUGGESTED_SUBSTITUTE_FAMILY.casefold()),
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
