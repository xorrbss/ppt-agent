"""Narrow source-owned native text authority; never a general OOXML import API."""
from __future__ import annotations

from copy import deepcopy
import re
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from lxml import etree

from models.svg_template_frame import SvgTemplateNativeFixedText
from services.source_content.security import secure_xml
from services.svg_templates.frame_fonts import artifact_digest, bytes_digest, font_asset_for, resolve_font_manifest

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
S = "http://www.w3.org/2000/svg"
NS = {"a": A, "p": P}
_ALLOWED = {
    (P, "spPr"): set(), (P, "txBody"): set(),
    (A, "xfrm"): set(), (A, "off"): {"x", "y"}, (A, "ext"): {"cx", "cy"},
    (A, "prstGeom"): {"prst"}, (A, "avLst"): set(), (A, "noFill"): set(),
    (A, "bodyPr"): {"wrap", "anchor", "lIns", "rIns", "tIns", "bIns"},
    (A, "noAutofit"): set(), (A, "lstStyle"): set(), (A, "p"): set(),
    (A, "pPr"): {"algn"}, (A, "lnSpc"): set(), (A, "spcPct"): {"val"},
    (A, "spcBef"): set(), (A, "spcAft"): set(), (A, "spcPts"): {"val"},
    (A, "r"): set(), (A, "rPr"): {"sz", "b", "i"}, (A, "solidFill"): set(),
    (A, "srgbClr"): {"val"}, (A, "latin"): {"typeface"},
    (A, "ea"): {"typeface"}, (A, "cs"): {"typeface"}, (A, "t"): set(),
}


def _require(value):
    if not value:
        raise ValueError("svg_template_native_fixed_text_unsupported")


def _structure(node):
    return (node.tag, sorted(node.attrib.items()), node.text or "", tuple(_structure(child) for child in node))


def _xml(value):
    _require(isinstance(value, str) and len(value.encode()) <= 32768)
    _require("<!" not in value and "<?" not in value)
    node = secure_xml(value.encode(), part_name="native-fixed-text.xml")
    for child in node.iter():
        _require(isinstance(child.tag, str))
        name = etree.QName(child)
        allowed = _ALLOWED.get((name.namespace, name.localname))
        _require(allowed is not None and set(child.attrib) == allowed)
        _require(name.localname == "t" or not (child.text or "").strip())
        _require(not (child.tail or "").strip())
        _require(not any(ord(char) < 32 and char not in "\t\r\n" for char in (child.text or "")))
    return node


def _children(node, names):
    _require([etree.QName(child).localname for child in node] == names)


def validated_parts(record, canvas, font_manifest):
    """Validate the complete closed subset again at every trust boundary."""
    record = SvgTemplateNativeFixedText.model_validate(record)
    _require(record.digest == artifact_digest(record))
    _require(record.source_canvas_digest == artifact_digest(canvas))
    sp, body = _xml(record.sp_pr_xml), _xml(record.tx_body_xml)
    _require(sp.tag == f"{{{P}}}spPr" and body.tag == f"{{{P}}}txBody")
    _children(sp, ["xfrm", "prstGeom", "noFill"])
    transform, geometry, _paint = sp
    _children(transform, ["off", "ext"])
    _children(geometry, ["avLst"])
    _require(geometry.get("prst") == "rect" and len(geometry[0]) == 0)
    x, y = (int(transform[0].get(key)) for key in ("x", "y"))
    cx, cy = (int(transform[1].get(key)) for key in ("cx", "cy"))
    _require(0 <= x and 0 <= y and cx > 0 and cy > 0
             and x + cx <= canvas.physical_size.width_emu and y + cy <= canvas.physical_size.height_emu)
    _children(body, ["bodyPr", "lstStyle", "p"])
    props, listing, paragraph = body
    _require(props.get("wrap") == "none" and props.get("anchor") in {"t", "ctr", "b"})
    _require(all(0 <= int(props.get(key)) for key in ("lIns", "rIns", "tIns", "bIns")))
    _require(int(props.get("lIns")) + int(props.get("rIns")) < cx
             and int(props.get("tIns")) + int(props.get("bIns")) < cy)
    _children(props, ["noAutofit"])
    _require(len(props[0]) == 0 and len(listing) == 0)
    _children(paragraph, ["pPr", "r"])
    paragraph_props, run = paragraph
    _require(paragraph_props.get("algn") in {"l", "ctr", "r"})
    _children(paragraph_props, ["lnSpc", "spcBef", "spcAft"])
    for spacing, unit in zip(paragraph_props, ("spcPct", "spcPts", "spcPts"), strict=True):
        _children(spacing, [unit])
        _require(len(spacing[0]) == 0 and 0 <= int(spacing[0].get("val")) <= 1000000)
    _children(run, ["rPr", "t"])
    run_props, text_node = run
    _require(len(text_node) == 0 and run_props.get("b") in {"0", "1"} and run_props.get("i") == "0")
    _require(100 <= int(run_props.get("sz")) <= 40000)
    _children(run_props, ["solidFill", "latin", "ea", "cs"])
    _children(run_props[0], ["srgbClr"])
    color = run_props[0][0]
    _require(len(color) == 0 and re.fullmatch(r"[0-9A-Fa-f]{6}", color.get("val")) is not None)
    family = run_props[1].get("typeface")
    _require(family and all(len(face) == 0 and face.get("typeface") == family for face in run_props[1:]))
    asset = font_asset_for(font_manifest, family, 700 if run_props.get("b") == "1" else 400)
    _require(record.font_asset_ids == (asset.asset_id,) and record.font_digests == (asset.digest,))
    text = text_node.text or ""
    _require(text.strip() == text and text and len(text) <= 2000 and not any(char in text for char in "\r\n\t"))
    _require(record.text_digest == bytes_digest(text.encode()))
    # Every childless structural node must remain empty; reject hidden nested payloads.
    for node in (transform[0], transform[1], sp[2], text_node, *run_props[1:]):
        _require(len(node) == 0)
    return record, sp, body, (x, y, x + cx, y + cy)


def extract_native_fixed_text(shape, element, canvas, manifest, source_digest):
    _require(element.kind == "text" and element.dynamic_key is None)
    _require(shape.getparent() is not None and etree.QName(shape.getparent()).localname == "spTree")
    _require(shape.find("p:style", NS) is None and shape.find("p:nvSpPr/p:nvPr/p:ph", NS) is None)
    sp, body = shape.find("p:spPr", NS), shape.find("p:txBody", NS)
    _require(sp is not None and body is not None)
    svg = secure_xml(element.svg.encode(), part_name="fixed-text.svg")
    texts = list(svg.iter(f"{{{S}}}text"))
    _require(len(texts) == 1 and len(texts[0]) == 0)
    _require(not any(node.get("transform") for node in svg.iter()))
    text = texts[0]
    assets = {asset.asset_id: asset for asset in manifest.assets}
    record = SvgTemplateNativeFixedText(
        source_digest=source_digest, source_xml_digest=element.source_xml_digest,
        source_canvas_digest=artifact_digest(canvas), svg_text_id=text.get("id"),
        svg_text_digest=artifact_digest(_structure(text)), text_digest=bytes_digest((text.text or "").encode()),
        font_asset_ids=element.font_asset_ids,
        font_digests=tuple(assets[key].digest for key in element.font_asset_ids),
        sp_pr_xml=etree.tostring(sp, encoding="unicode", with_tail=False),
        tx_body_xml=etree.tostring(body, encoding="unicode", with_tail=False), digest="")
    record = record.model_copy(update={"digest": artifact_digest(record)})
    validated_parts(record, canvas, manifest)
    return record


def native_frame_mapping(frame):
    result = {}
    for element in frame.elements:
        if element.kind != "text":
            _require(element.native_fixed_text is None)
            continue
        record, _sp, _body, _bounds = validated_parts(element.native_fixed_text, frame.source_canvas, frame.font_manifest)
        _require(record.source_digest == frame.source_digest and record.source_xml_digest == element.source_xml_digest)
        _require(record.svg_text_id not in result and element.dynamic_key is None)
        result[record.svg_text_id] = record.model_dump(mode="json")
    return result


def validate_mapping(svg, mapping, canvas, manifest):
    root = ET.fromstring(svg) if isinstance(svg, str) else svg
    texts = list(root.iter(f"{{{S}}}text"))
    for identity, value in mapping.items():
        record, _sp, _body, _bounds = validated_parts(value, canvas, manifest)
        matches = [node for node in texts if node.get("id") == identity]
        _require(identity == record.svg_text_id and len(matches) == 1)
        _require(artifact_digest(_structure(matches[0])) == record.svg_text_digest)
    return mapping


class NativeFixedTextExport:
    """Invocation-local adapter consumed only by canonical convert_text."""
    def __init__(self, sources, receipts, canvas, manifest):
        _require(len(sources) == len(receipts))
        self.pages, self.used, self.emitted = [], set(), {}
        self.canvas, self.manifest = canvas, manifest
        resolve_font_manifest(manifest)
        for source, receipt in zip(sources, receipts, strict=True):
            binding = (receipt or {}).get("generated_template_frame", {})
            mapping = binding.get("native_fixed_text", {})
            if binding.get("schema_version") in {"svg-template-furniture/v2", "svg-template-furniture/v3"}:
                from services.svg_master_furniture import attested_svg_furniture
                attested_svg_furniture(source, receipt, width=canvas.width_px, height=canvas.height_px)
                _require(binding.get("font_manifest_digest") == manifest.digest and bool(mapping))
                validate_mapping(source, mapping, canvas, manifest)
            else:
                _require(not mapping)
            self.pages.append(mapping)
        _require(any(self.pages))

    def convert(self, element, context):
        page = context.slide_num - 1
        _require(0 <= page < len(self.pages))
        value = self.pages[page].get(element.get("id"))
        if value is None:
            return None
        key = (page, element.get("id"))
        _require(key not in self.used and artifact_digest(_structure(element)) == value["svg_text_digest"])
        _require(context.translate_x == context.translate_y == 0 and context.scale_x == context.scale_y == 1
                 and context.transform_matrix == (1, 0, 0, 1, 0, 0) and context.opacity_multiplier == 1)
        _record, sp, body, bounds = validated_parts(value, self.canvas, self.manifest)
        shape = etree.Element(f"{{{P}}}sp", nsmap={"p": P, "a": A})
        nv = etree.SubElement(shape, f"{{{P}}}nvSpPr")
        shape_id = context.next_id()
        etree.SubElement(nv, f"{{{P}}}cNvPr", id=str(shape_id), name="Source fixed text")
        etree.SubElement(nv, f"{{{P}}}cNvSpPr", txBox="1")
        etree.SubElement(nv, f"{{{P}}}nvPr")
        shape.extend((deepcopy(sp), deepcopy(body)))
        self.used.add(key)
        self.emitted[key] = shape_id
        from svg_to_pptx.drawingml.context import ShapeResult
        return ShapeResult(xml=etree.tostring(shape, encoding="unicode"), bounds_emu=bounds)

    def verify(self, target):
        _require(self.used == {(index, key) for index, page in enumerate(self.pages) for key in page})
        with ZipFile(target) as archive:
            for (page, identity), shape_id in self.emitted.items():
                root = secure_xml(archive.read(f"ppt/slides/slide{page + 1}.xml"), part_name="fixed-text-readback.xml")
                matches = [shape for shape in root.iter(f"{{{P}}}sp")
                           if shape.find("p:nvSpPr/p:cNvPr", NS).get("id") == str(shape_id)]
                _require(len(matches) == 1)
                for ancestor in matches[0].iterancestors(f"{{{P}}}grpSp"):
                    transform = ancestor.find("p:grpSpPr/a:xfrm", NS)
                    _require(transform is not None and not transform.attrib)
                    _children(transform, ["off", "ext", "chOff", "chExt"])
                    _require([set(node.attrib) for node in transform] == [
                        {"x", "y"}, {"cx", "cy"}, {"x", "y"}, {"cx", "cy"}])
                    _require(all(len(node) == 0 for node in transform))
                    off, ext, child_off, child_ext = [dict(node.attrib) for node in transform]
                    _require({key: int(value) for key, value in off.items()} ==
                             {key: int(value) for key, value in child_off.items()})
                    _require({key: int(value) for key, value in ext.items()} ==
                             {key: int(value) for key, value in child_ext.items()}
                             and all(int(value) > 0 for value in ext.values()))
                _record, sp, body, _bounds = validated_parts(self.pages[page][identity], self.canvas, self.manifest)
                _require(_structure(matches[0].find("p:spPr", NS)) == _structure(sp)
                         and _structure(matches[0].find("p:txBody", NS)) == _structure(body))
