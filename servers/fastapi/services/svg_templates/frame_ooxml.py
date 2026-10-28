"""Restricted faithful OOXML primitives for fixed template furniture.

Unsupported paint is reported, never replaced by a structural rectangle.
"""
from __future__ import annotations

import base64
import colorsys
from io import BytesIO
from zipfile import ZipFile

from lxml import etree
from PIL import Image

from models.svg_template_frame import FrameRect, SvgTemplateAsset, SvgTemplateFrameElement
from services.source_content.pptx_extraction_constants import NS, R_NS
from services.source_content.pptx_geometry import _geometry
from services.source_content.pptx_package_parts import _relationship_map
from services.source_content.pptx_typography import PptxTypographyResolver, placeholder_of
from services.source_content.security import secure_xml
from services.svg_templates.frame_fonts import bytes_digest, font_asset_for, measure_text

SVG_NS = "http://www.w3.org/2000/svg"
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_777_216


def svg_node(name: str, **attributes) -> etree._Element:
    return etree.Element(f"{{{SVG_NS}}}{name}", nsmap={None: SVG_NS},
                         attrib={key.replace("_", "-"): str(value) for key, value in attributes.items()})


def xml_digest(node: etree._Element) -> str:
    return bytes_digest(etree.tostring(node, method="c14n"))


def shape_id(shape: etree._Element) -> str:
    marker = shape.find(".//p:cNvPr", NS)
    return marker.get("id", "unknown") if marker is not None else "unknown"


def shape_bounds(shape, typography, part, scale):
    geometry = _geometry(shape, inherited_shapes=typography.placeholder_shape_chain(shape, part_name=part))
    if not all(key in geometry for key in ("x", "y", "cx", "cy")):
        raise ValueError("svg_template_geometry_missing")
    if (geometry.get("rotationDegrees", 0) or geometry.get("flipH") or geometry.get("flipV")):
        raise ValueError("svg_template_transform_unsupported")
    return FrameRect(x=geometry["x"] / scale, y=geometry["y"] / scale,
                     width=geometry["cx"] / scale, height=geometry["cy"] / scale)


class OoxmlFrameContext:
    def __init__(self, package: ZipFile, typography: PptxTypographyResolver, canvas, font_manifest):
        self.package = package
        self.typography = typography
        self.canvas = canvas
        self.scale = canvas.emu_per_unit
        self.font_manifest = font_manifest
        self.assets: dict[str, SvgTemplateAsset] = {}
        self.required_families: set[str] = set()

    def root(self, part):
        return secure_xml(self.package.read(part), part_name=part)

    def related(self, part, suffix):
        return next((target for target, kind in _relationship_map(self.package, part).values()
                     if kind.endswith(suffix)), None)

    def ancestry(self, slide_part):
        layout = self.related(slide_part, "/slideLayout")
        master = self.related(layout, "/slideMaster") if layout else None
        return tuple(part for part in (slide_part, layout, master) if part)

    def theme(self, slide_part):
        ancestry = self.ancestry(slide_part)
        master = next((p for p in ancestry if "/slideMasters/" in p), None)
        if master is None and "/slideMasters/" in slide_part:
            master = slide_part
        if master is None and "/slideLayouts/" in slide_part:
            master = self.related(slide_part, "/slideMaster")
        theme = self.related(master, "/theme") if master else None
        return self.root(theme) if theme else None

    def color(self, node, part, *, placeholder=None):
        if node is None:
            raise ValueError("svg_template_color_unresolved")
        local = etree.QName(node).localname
        if local == "solidFill":
            node = next(iter(node), None)
            return self.color(node, part, placeholder=placeholder)
        local = etree.QName(node).localname
        if local == "srgbClr":
            raw = node.get("val", "")
        elif local == "sysClr":
            raw = node.get("lastClr", "")
        elif local == "schemeClr":
            token = node.get("val", "")
            if token == "phClr" and placeholder is not None:
                raw = placeholder.lstrip("#")
            else:
                mapping = dict(bg1="lt1", tx1="dk1", bg2="lt2", tx2="dk2")
                for source in reversed(self.ancestry(part)):
                    root = self.root(source)
                    for color_map in root.findall("p:clrMap", NS) + root.findall("p:clrMapOvr/a:overrideClrMapping", NS):
                        mapping.update(color_map.attrib)
                token = mapping.get(token, token)
                theme = self.theme(part)
                value = theme.find(f"a:themeElements/a:clrScheme/a:{token}", NS) if theme is not None else None
                raw = self.color(next(iter(value), None), part).lstrip("#") if value is not None else ""
        else:
            raise ValueError("svg_template_color_unsupported")
        if len(raw) != 6 or any(c not in "0123456789abcdefABCDEF" for c in raw):
            raise ValueError("svg_template_color_unresolved")
        rgb = [int(raw[index:index + 2], 16) / 255 for index in (0, 2, 4)]
        for operation in node:
            name = etree.QName(operation).localname
            value = int(operation.get("val", "0")) / 100000
            if name in {"lumMod", "lumOff"}:
                hue, light, saturation = colorsys.rgb_to_hls(*rgb)
                light = light * value if name == "lumMod" else light + value
                rgb = list(colorsys.hls_to_rgb(hue, min(1, max(0, light)), saturation))
            elif name in {"tint", "shade"}:
                rgb = [channel * value + (1 - value if name == "tint" else 0) for channel in rgb]
            elif name == "alpha" and value == 1:
                continue
            else:
                raise ValueError("svg_template_color_transform_unsupported")
        return "#" + "".join(f"{round(min(1, max(0, c)) * 255):02X}" for c in rgb)

    def fill(self, shape, part, kind="fill"):
        chain = (shape,) + tuple(item for _, item in self.typography.placeholder_shape_chain(shape, part_name=part))
        for candidate in chain:
            properties = candidate.find("p:spPr", NS)
            if kind == "line":
                properties = properties.find("a:ln", NS) if properties is not None else None
            if properties is not None:
                for child in properties:
                    name = etree.QName(child).localname
                    if name == "noFill":
                        return "none", properties
                    if name == "solidFill":
                        return self.color(child, part), properties
                    if name in {"gradFill", "pattFill", "blipFill", "grpFill"}:
                        raise ValueError("svg_template_paint_unsupported")
            style = candidate.find("p:style/a:" + ("lnRef" if kind == "line" else "fillRef"), NS)
            if style is not None:
                index = int(style.get("idx", "0"))
                if index == 0:
                    return "none", properties
                theme = self.theme(part)
                category = "lnStyleLst" if kind == "line" else "fillStyleLst"
                choices = theme.find("a:themeElements/a:fmtScheme/a:" + category, NS) if theme is not None else None
                if choices is None or index > len(choices) or index < 1:
                    raise ValueError("svg_template_style_unresolved")
                selected = choices[index - 1]
                paint = selected.find("a:solidFill", NS) if kind == "line" else selected
                if paint is None or etree.QName(paint).localname != "solidFill":
                    raise ValueError("svg_template_paint_unsupported")
                placeholder = self.color(next(iter(style), None), part)
                return self.color(paint, part, placeholder=placeholder), selected
        return "none", None

    def text_svg(self, shape, part, bounds, dynamic_key):
        context = self.typography.shape_context(shape, part_name=getattr(self, "source_slide_part", part))
        body = shape.find("p:txBody", NS)
        if body is None:
            raise ValueError("svg_template_text_missing")
        paragraphs = body.findall("a:p", NS)
        # body_properties contains inherited placeholders only. The resolver
        # includes the local bodyPr first, preserving explicit zero insets and
        # local noAutofit overrides even when the element has no children.
        body_layout = context.text_body_layout(shape)
        if body_layout["vertical"] != "horz" or body_layout["rotationDegrees"] not in {None, 0}:
            raise ValueError("svg_template_text_direction_unsupported")
        if body_layout["autoFit"] not in {None, "none"}:
            raise ValueError("svg_template_text_autofit_unsupported")
        left, right = body_layout["marginLeftEmu"] / self.scale, body_layout["marginRightEmu"] / self.scale
        top, bottom = body_layout["marginTopEmu"] / self.scale, body_layout["marginBottomEmu"] / self.scale
        lines = []
        used = set()
        for paragraph in paragraphs:
            ppr = paragraph.find("a:pPr", NS)
            if ppr is not None and (any(etree.QName(c).localname.startswith("bu") and etree.QName(c).localname != "buNone" for c in ppr)
                    or ppr.find("a:tabLst", NS) is not None):
                raise ValueError("svg_template_text_paragraph_unsupported")
            runs = []
            for run in paragraph:
                if etree.QName(run).localname not in {"r", "fld", "br"}:
                    continue
                if etree.QName(run).localname == "br":
                    raise ValueError("svg_template_text_soft_break_unsupported")
                run_props = run.find("a:rPr", NS)
                if run_props is not None and any(run_props.get(key, default) != default
                        for key, default in (("spc", "0"), ("baseline", "0"), ("cap", "none"),
                                             ("u", "none"), ("strike", "noStrike"))):
                    raise ValueError("svg_template_text_decoration_unsupported")
                style = context.run_style(run, paragraph)
                family = style.get("fontFamily")
                if family:
                    self.required_families.add(family)
                if not family or not style.get("sizePt") or style.get("italic"):
                    raise ValueError("svg_template_font_style_unresolved")
                asset = font_asset_for(self.font_manifest, family, 700 if style.get("bold") else 400)
                text = "".join(run.itertext()) if run.find("a:t", NS) is None else run.find("a:t", NS).text or ""
                size = float(style["sizePt"]) * 12700 / self.scale
                width, ascent, descent = measure_text(asset, text, size)
                used.add(asset.asset_id)
                # Resolve inherited run paint, rather than changing scheme colors to black.
                color = style.get("color")
                if color is None:
                    chain = [run.find("a:rPr", NS), *context._paragraph_chain(paragraph)]
                    paint = next((p.find("a:solidFill", NS) for p in chain if p is not None and p.find("a:solidFill", NS) is not None), None)
                    color = self.color(paint, part) if paint is not None else None
                if not color:
                    raise ValueError("svg_template_text_color_unresolved")
                if not color.startswith("#"):
                    color = "#" + color
                runs.append((text, size, asset, width, ascent, descent, color))
            if not runs:
                continue
            if dynamic_key and (len(paragraphs) != 1 or len(runs) != 1):
                raise ValueError("svg_template_dynamic_rich_text_unsupported")
            layout = context.paragraph_layout(paragraph)
            if (any(layout.get(k) for k in ("marginLeftEmu", "indentEmu", "spaceBeforePt", "spaceAfterPt",
                                           "spaceBeforePct", "spaceAfterPct", "lineSpacingPt"))
                    or layout.get("lineSpacingPct") not in {None, 100.0}):
                raise ValueError("svg_template_text_spacing_unsupported")
            lines.append((runs, layout.get("alignment") or "l"))
        if not lines:
            raise ValueError("svg_template_text_empty")
        line_heights = [max(run[4] + run[5] for run in runs) for runs, _ in lines]
        total_height = sum(line_heights)
        available_height = bounds.height - top - bottom
        if total_height > available_height + .1:
            raise ValueError("svg_template_text_overflow")
        anchor = body_layout["anchor"]
        if anchor not in {"t", "ctr", "b"}:
            raise ValueError("svg_template_text_anchor_unsupported")
        y = bounds.y + top + ({"t": 0, "ctr": .5, "b": 1}[anchor] * (available_height - total_height))
        group = svg_node("g")
        for (runs, align), height in zip(lines, line_heights):
            width = sum(run[3] for run in runs)
            available_width = bounds.width - left - right
            if width > available_width + .1 or align not in {"l", "ctr", "r"}:
                raise ValueError("svg_template_text_overflow")
            x = bounds.x + left + {"l": 0, "ctr": .5, "r": 1}[align] * (available_width - width)
            baseline = y + max(run[4] for run in runs)
            for text, size, asset, run_width, _, _, color in runs:
                node = svg_node("text", x=x, y=baseline, font_family=asset.family,
                                font_weight=asset.weight, font_size=size, fill=color)
                node.text = text
                group.append(node)
                x += run_width
            y += height
        region = FrameRect(x=bounds.x + left, y=bounds.y + top,
                           width=max(0, bounds.width - left - right), height=max(0, available_height))
        return group, tuple(sorted(used)), region, lines[0][1]

    def emit(self, shape, part, *, element_id, dynamic_key=None):
        bounds = shape_bounds(shape, self.typography, part, self.scale)
        kind = etree.QName(shape).localname
        for name in ("effectLst", "effectDag", "scene3d", "sp3d", "custGeom"):
            if shape.find(".//a:" + name, NS) is not None:
                node = shape.find(".//a:" + name, NS)
                if len(node) or name not in {"effectLst", "effectDag"}:
                    raise ValueError("svg_template_effect_unsupported")
        used = ()
        text_region, text_align = None, "l"
        if kind == "pic":
            crop = shape.find("p:blipFill/a:srcRect", NS)
            if crop is not None and any(int(value) != 0 for value in crop.attrib.values()):
                raise ValueError("svg_template_image_crop_unsupported")
            blip = shape.find("p:blipFill/a:blip", NS)
            if blip is None or len(blip):
                raise ValueError("svg_template_image_effect_unsupported")
            relation = _relationship_map(self.package, part).get(blip.get(f"{{{R_NS}}}embed", ""))
            if relation is None or not relation[1].endswith("/image"):
                raise ValueError("svg_template_image_reference_invalid")
            content = self.package.read(relation[0])
            if len(content) > MAX_IMAGE_BYTES:
                raise ValueError("svg_template_image_bytes_exceeded")
            try:
                with Image.open(BytesIO(content)) as image:
                    if image.format not in {"PNG", "JPEG"}:
                        raise ValueError("svg_template_image_format_unsupported")
                    if image.width * image.height > MAX_IMAGE_PIXELS or max(image.size) > 8192:
                        raise ValueError("svg_template_image_pixels_exceeded")
                    media_type = "image/png" if image.format == "PNG" else "image/jpeg"
                    width, height = image.size
                    image.verify()
            except (OSError, Image.DecompressionBombError) as error:
                raise ValueError("svg_template_image_invalid") from error
            digest = bytes_digest(content)
            if bounds.height <= 0 or abs(bounds.width / bounds.height - width / height) > .005:
                raise ValueError("svg_template_image_stretch_unsupported")
            asset = SvgTemplateAsset(asset_id="image-" + digest[7:23], digest=digest, media_type=media_type,
                data_base64=base64.b64encode(content).decode("ascii"), width=width, height=height)
            self.assets[asset.asset_id] = asset
            node = svg_node("image", x=bounds.x, y=bounds.y, width=round(bounds.width), height=round(bounds.height),
                            href=f"data:{media_type};base64,{asset.data_base64}", preserveAspectRatio="xMidYMid meet")
            output_kind = "image"
        elif kind in {"sp", "cxnSp"}:
            geometry = shape.find("p:spPr/a:prstGeom", NS)
            preset = geometry.get("prst") if geometry is not None else None
            has_text = shape.find("p:txBody", NS) is not None
            if has_text and shape.find(".//a:t", NS) is not None:
                fill, _ = self.fill(shape, part)
                stroke, _ = self.fill(shape, part, "line")
                if fill != "none" or stroke != "none":
                    raise ValueError("svg_template_text_shape_paint_unsupported")
                node, used, text_region, text_align = self.text_svg(shape, part, bounds, dynamic_key)
                output_kind = "text"
            elif preset in {"rect", "line"} or kind == "cxnSp":
                if preset not in {"rect", "line", None}:
                    raise ValueError("svg_template_primitive_unsupported")
                fill, _ = self.fill(shape, part)
                stroke, line = self.fill(shape, part, "line")
                if line is not None and (line.find("a:prstDash", NS) is not None
                        and line.find("a:prstDash", NS).get("val") != "solid"
                        or line.find("a:custDash", NS) is not None
                        or any(line.find("a:" + n, NS) is not None and line.find("a:" + n, NS).get("type", "none") != "none"
                               for n in ("headEnd", "tailEnd"))):
                    raise ValueError("svg_template_line_style_unsupported")
                stroke_width = float(line.get("w", "12700")) / self.scale if line is not None else 0
                if preset == "rect":
                    node = svg_node("rect", x=bounds.x, y=bounds.y, width=bounds.width, height=bounds.height,
                                    fill=fill, stroke=stroke, stroke_width=stroke_width)
                    output_kind = "rect"
                else:
                    node = svg_node("line", x1=bounds.x, y1=bounds.y, x2=bounds.x + bounds.width,
                                    y2=bounds.y + bounds.height, stroke=stroke, stroke_width=stroke_width)
                    output_kind = "line"
            else:
                raise ValueError("svg_template_primitive_unsupported")
        else:
            raise ValueError("svg_template_primitive_unsupported")
        node.set("id", element_id)
        for index, child in enumerate(node.iterdescendants()):
            child.set("id", f"{element_id}-child-{index}")
        return SvgTemplateFrameElement(element_id=element_id, source_part=part,
            source_shape_id=shape_id(shape), source_xml_digest=xml_digest(shape), kind=output_kind,
            bounds=bounds, svg=etree.tostring(node, encoding="unicode"), dynamic_key=dynamic_key,
            font_asset_ids=used, text_region=text_region, text_align=text_align,
            anchor_x="right" if bounds.x > self.canvas.width_px * .65 else "left",
            anchor_y="bottom" if bounds.y >= self.canvas.height_px * .7 else "top",
            size_policy="span-between-anchors" if output_kind in {"rect", "line"} and bounds.width >= self.canvas.width_px * .65
                else "aspect-locked" if output_kind == "image" else "physical-fixed")
