"""Strict editable-title adapter over the existing SVG text converter and trace."""
from __future__ import annotations

from dataclasses import replace
from xml.etree import ElementTree as ET
import zipfile

from services.svg_common_header import GROUP_ID, TITLE_ID
from services.svg_contract import source_digest
from utils.hashing import canonical_json_sha256

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def header_title_lines(svg):
    if GROUP_ID not in svg:
        return {}
    root = ET.fromstring(svg)
    if any(node.get("id") == "svg-system-common-header-v2-contract" for node in root.iter()):
        return {}
    groups = [node for node in root.iter() if node.get("id") == GROUP_ID]
    if not groups:
        return {}
    if len(groups) != 1 or groups[0] not in list(root):
        raise ValueError("svg_common_header_export_binding_invalid")
    group = groups[0]
    if any(node.get("transform") for node in group.iter()):
        raise ValueError("svg_common_header_export_binding_invalid")
    lines = [node for node in group.iter() if node.get("id", "").startswith(TITLE_ID + "-line-")]
    if not 1 <= len(lines) <= 2:
        raise ValueError("svg_common_header_export_binding_invalid")
    result = {}
    for index, node in enumerate(lines, 1):
        identity = TITLE_ID + f"-line-{index}"
        if node.get("id") != identity or node.tag.rsplit("}", 1)[-1] != "text" or list(node):
            raise ValueError("svg_common_header_export_binding_invalid")
        result[identity] = {"text": node.text or "", "attributes": dict(node.attrib)}
    return result


class FrozenV1CommonHeaderExport:
    def __init__(self, sources, pt_per_px):
        self.sources = sources
        self.lines = [header_title_lines(source) for source in sources]
        self.pt_per_px = pt_per_px
        self.captured = {}
        self.converter = self.original = None

    def install(self, converter):
        if not any(self.lines):
            return
        self.converter, self.original = converter, converter._CONVERTERS["text"]
        ET.register_namespace("p", P)
        ET.register_namespace("a", A)

        def convert(element, context):
            page, identity = context.slide_num - 1, element.get("id")
            expected = self.lines[page].get(identity)
            result = self.original(element, context)
            if expected is None:
                return result
            if (result is None or dict(element.attrib) != expected["attributes"]
                    or (element.text or "") != expected["text"]
                    or context.scale_x != 1 or context.scale_y != 1
                    or context.translate_x != 0 or context.translate_y != 0):
                raise ValueError("svg_common_header_export_binding_invalid")
            wrapper = ET.fromstring(
                f'<root xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}">' + result.xml + '</root>')
            if len(wrapper) != 1:
                raise ValueError("svg_common_header_export_not_editable")
            shape = wrapper[0]
            body = shape.find(f"{{{P}}}txBody/{{{A}}}bodyPr")
            identity_node = shape.find(f"{{{P}}}nvSpPr/{{{P}}}cNvPr")
            if body is None or identity_node is None or shape.tag != f"{{{P}}}sp":
                raise ValueError("svg_common_header_export_not_editable")
            texts = shape.findall(f"{{{P}}}txBody/{{{A}}}p/{{{A}}}r/{{{A}}}t")
            if len(texts) != 1 or (texts[0].text or "").strip() != expected["text"].strip():
                raise ValueError("svg_common_header_export_title_mismatch")
            # The generic converter trims plain runs. Frozen title wrapping can
            # retain a boundary space; preserve that exact bound line in OOXML.
            texts[0].text = expected["text"]
            if expected["text"] != expected["text"].strip() or "  " in expected["text"]:
                texts[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            for child in list(body):
                if child.tag in {f"{{{A}}}spAutoFit", f"{{{A}}}normAutofit", f"{{{A}}}noAutofit"}:
                    body.remove(child)
            ET.SubElement(body, f"{{{A}}}noAutofit")
            # Retain the frozen face explicitly; the generic converter may map
            # an unfamiliar family to a platform fallback for the EA run.
            for properties in shape.findall(f"{{{P}}}txBody/{{{A}}}p/{{{A}}}r/{{{A}}}rPr"):
                for name in ("latin", "ea", "cs"):
                    face = properties.find(f"{{{A}}}{name}")
                    if face is None:
                        face = ET.SubElement(properties, f"{{{A}}}{name}")
                    face.set("typeface", expected["attributes"]["font-family"])
            self.captured[(page, identity)] = {"shapeId": int(identity_node.get("id")),
                "boundsEmu": list(result.bounds_emu), "source": expected}
            return replace(result, xml=ET.tostring(shape, encoding="unicode"))

        converter._CONVERTERS["text"] = convert

    def restore(self):
        if self.original is not None:
            self.converter._CONVERTERS["text"] = self.original

    def verify(self, target):
        if not any(self.lines):
            return None
        pages = []
        with zipfile.ZipFile(target) as archive:
            for index, expected_lines in enumerate(self.lines):
                root = ET.fromstring(archive.read(f"ppt/slides/slide{index + 1}.xml"))
                shapes = {int(node.find(f"{{{P}}}nvSpPr/{{{P}}}cNvPr").get("id")): node
                    for node in root.iter(f"{{{P}}}sp")}
                records = []
                for identity, expected in expected_lines.items():
                    captured = self.captured.get((index, identity))
                    if captured is None or captured["shapeId"] not in shapes:
                        raise ValueError("svg_common_header_export_not_editable")
                    shape = shapes[captured["shapeId"]]
                    attrs = expected["attributes"]
                    body = shape.find(f"{{{P}}}txBody/{{{A}}}bodyPr")
                    runs = shape.findall(f"{{{P}}}txBody/{{{A}}}p/{{{A}}}r")
                    text = "".join(run.findtext(f"{{{A}}}t", "") for run in runs)
                    if (body is None or body.find(f"{{{A}}}noAutofit") is None
                            or body.find(f"{{{A}}}spAutoFit") is not None
                            or body.find(f"{{{A}}}normAutofit") is not None
                            or text != expected["text"] or not runs):
                        raise ValueError("svg_common_header_export_title_mismatch")
                    for run in runs:
                        properties = run.find(f"{{{A}}}rPr")
                        if properties is None:
                            raise ValueError("svg_common_header_export_font_mismatch")
                        faces = [properties.find(f"{{{A}}}{name}") for name in ("latin", "ea")]
                        size = int(properties.get("sz", "0")) / 100
                        color = properties.find(f"{{{A}}}solidFill/{{{A}}}srgbClr")
                        if (any(face is None or face.get("typeface") != attrs["font-family"] for face in faces)
                                or abs(size - float(attrs["font-size"]) * self.pt_per_px) > .051
                                or (properties.get("b") == "1") != (attrs.get("font-weight") in {"bold", "600", "700", "800", "900"})
                                or color is None or color.get("val", "").lower() != attrs["fill"].lstrip("#").lower()):
                            raise ValueError("svg_common_header_export_font_mismatch")
                    paragraphs = shape.findall(f"{{{P}}}txBody/{{{A}}}p")
                    align = {"start": "l", "middle": "ctr", "end": "r"}[attrs.get("text-anchor", "start")]
                    paragraph_style = paragraphs[0].find(f"{{{A}}}pPr") if len(paragraphs) == 1 else None
                    if paragraph_style is None or paragraph_style.get("algn", "l") != align:
                        raise ValueError("svg_common_header_export_title_mismatch")
                    transform = shape.find(f"{{{P}}}spPr/{{{A}}}xfrm")
                    off = None if transform is None else transform.find(f"{{{A}}}off")
                    ext = None if transform is None else transform.find(f"{{{A}}}ext")
                    if off is None or ext is None:
                        raise ValueError("svg_common_header_export_geometry_mismatch")
                    bounds = [int(off.get("x")), int(off.get("y"))]
                    bounds += [bounds[0] + int(ext.get("cx")), bounds[1] + int(ext.get("cy"))]
                    if any(abs(actual - wanted) > 6350 for actual, wanted in zip(bounds, captured["boundsEmu"], strict=True)):
                        raise ValueError("svg_common_header_export_geometry_mismatch")
                    records.append({"elementId": identity, "shapeId": captured["shapeId"],
                        "text": text, "fontFamily": attrs["font-family"], "fontSizePt": size,
                        "fontSizePx": float(attrs["font-size"]),
                        "fontWeight": attrs.get("font-weight", "400"), "color": attrs["fill"],
                        "alignment": attrs.get("text-anchor", "start"),
                        "svgAnchor": {"x": float(attrs["x"]), "baselineY": float(attrs["y"])},
                        "boundsEmu": bounds, "autoFit": False})
                pages.append({"pageIndex": index, "svgDigest": source_digest(self.sources[index]), "lines": records})
        value = {"schemaVersion": "svg-common-header-ooxml/v1", "passed": True,
                 "origin": "generated-common-header", "pages": pages,
                 "pointPerPx": self.pt_per_px, "officeRenderMeasured": False}
        return {**value, "digest": "sha256:" + canonical_json_sha256(value)}


class CommonHeaderExport:
    """Dispatch explicitly marked v2 scenes while retaining the frozen v1 adapter."""

    def __init__(self, sources, pt_per_px):
        from workers.svg_common_header_export_v2 import CommonHeaderV2Export

        self.legacy = FrozenV1CommonHeaderExport(sources, pt_per_px)
        self.v2 = CommonHeaderV2Export(sources, pt_per_px)
        self.lines = self.legacy.lines if not self.v2.active else [True]

    def install(self, converter):
        self.legacy.install(converter)
        self.v2.install(converter)

    def restore(self):
        self.v2.restore()
        self.legacy.restore()

    def verify(self, target):
        return self.v2.verify(target, legacy_readback=self.legacy.verify(target))
