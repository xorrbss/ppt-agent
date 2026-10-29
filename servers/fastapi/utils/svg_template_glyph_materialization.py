"""Private, source-bound display projection for proved template fixed glyphs.

The canonical SVG remains text and is validated by its existing admission path.
Only an already sealed server-owned glyph authority may reach this projection.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import re

from lxml import etree

from models.svg_template_glyph import SvgTemplateFixedGlyphPreview
from utils.hashing import sha256_prefixed


_SVG = "http://www.w3.org/2000/svg"
_IMAGE_HREF = ("href", "{http://www.w3.org/1999/xlink}href")
_QID = re.compile(r"svg-[0-9]+\Z")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")


@dataclass(frozen=True)
class MaterializedGlyphPreview:
    svg: str
    display_svg: str
    canonical_digest: str
    render_digest: str
    display_digest: str
    projection_digest: str
    trusted_font_css_digest: str
    authority_digest: str
    materializer_code_digest: str


def materializer_code_digest() -> str:
    """Bind source analysis and rendering to these exact materializer bytes."""
    return sha256_prefixed(Path(__file__).read_bytes())


def _parse(source: str, *, max_bytes: int = 16 * 1024 * 1024):
    if not isinstance(source, str) or not source or len(source.encode("utf-8")) > max_bytes:
        raise ValueError("svg_template_glyph_svg_size_invalid")
    if "<!" in source or "<?" in source:
        raise ValueError("svg_template_glyph_xml_unsupported")
    try:
        root = etree.fromstring(source.encode("utf-8"), parser=etree.XMLParser(
            resolve_entities=False, load_dtd=False, no_network=True))
    except etree.XMLSyntaxError as error:
        raise ValueError("svg_template_glyph_xml_invalid") from error
    if root.tag != f"{{{_SVG}}}svg":
        raise ValueError("svg_template_glyph_root_invalid")
    return root


def _structure(node):
    return (node.tag, sorted(node.attrib.items()), node.text or "", tuple(_structure(child) for child in node))


def _digest_structure(node) -> str:
    from json import dumps

    encoded = dumps(_structure(node), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return sha256_prefixed(encoded)


def _without_qids(root):
    clone = deepcopy(root)
    for node in clone.iter():
        qid = node.attrib.pop("data-qid", None)
        if qid is not None and _QID.fullmatch(qid) is None:
            raise ValueError("svg_template_glyph_qid_invalid")
    return clone


def _image_neutralized(root):
    clone = deepcopy(root)
    for node in clone.iter():
        if node.tag == f"{{{_SVG}}}image":
            for name in _IMAGE_HREF:
                node.attrib.pop(name, None)
    return _structure(clone)


def _default_namespace(root):
    """Emit a real inline SVG root even when an image resolver used ns0."""
    output = etree.Element(root.tag, nsmap={None: _SVG})
    output.attrib.update(root.attrib)
    output.text, output.tail = root.text, root.tail
    for child in list(root):
        output.append(child)
    etree.cleanup_namespaces(output)
    return output


def _canvas(root, authority):
    expected_width, expected_height = authority.source_canvas_px
    if set(root.attrib) != {"width", "height", "viewBox"}:
        raise ValueError("svg_template_glyph_canvas_invalid")
    try:
        width, height = float(root.get("width")), float(root.get("height"))
        view = tuple(float(part) for part in root.get("viewBox", "").split())
    except ValueError as error:
        raise ValueError("svg_template_glyph_canvas_invalid") from error
    if (width, height) != (expected_width, expected_height) or view != (0, 0, width, height):
        raise ValueError("svg_template_glyph_canvas_invalid")


def _context(text, object_record):
    if text.tag != f"{{{_SVG}}}text" or len(text) or text.tail and text.tail.strip():
        raise ValueError("svg_template_glyph_text_context_unsupported")
    allowed_text = {"id", "x", "y", "fill", "font-size", "font-family", "font-weight", "data-qid"}
    if set(text.attrib) - allowed_text or not {"id", "x", "y", "fill", "font-size", "font-family", "font-weight"} <= set(text.attrib):
        raise ValueError("svg_template_glyph_text_context_unsupported")
    color = text.get("fill", "")
    if _COLOR.fullmatch(color) is None:
        raise ValueError("svg_template_glyph_paint_unsupported")
    expected = tuple(bytes.fromhex(color[1:])) + (255,)
    if any(tuple(char.fill_rgba) != expected for char in object_record.characters):
        raise ValueError("svg_template_glyph_paint_mismatch")
    ancestor = text.getparent()
    if ancestor is None:
        raise ValueError("svg_template_glyph_ancestor_unsupported")
    while ancestor is not None:
        if ancestor.tag == f"{{{_SVG}}}g":
            if set(ancestor.attrib) - {"id", "data-qid"} or not ancestor.get("id"):
                raise ValueError("svg_template_glyph_ancestor_unsupported")
        elif ancestor.tag == f"{{{_SVG}}}svg":
            if set(ancestor.attrib) - {"width", "height", "viewBox", "data-qid"}:
                raise ValueError("svg_template_glyph_ancestor_unsupported")
        else:
            raise ValueError("svg_template_glyph_ancestor_unsupported")
        ancestor = ancestor.getparent()


def _number(value: float) -> str:
    # 17 digits round-trip the PDFium double. No fitting or geometry tolerance.
    return format(value, ".17g")


def _path_data(character, authority) -> str:
    width_px, height_px = authority.source_canvas_px
    width_pt, height_pt = authority.physical_canvas_pt
    def point(segment):
        _kind, x, y, _close = segment
        return _number(x * width_px / width_pt) + " " + _number((height_pt - y) * height_px / height_pt)

    parts = []
    segments = character.path_pdf
    cursor = 0
    while cursor < len(segments):
        kind = segments[cursor][0]
        if kind == 2:
            parts.append("M" + point(segments[cursor]))
            cursor += 1
        elif kind == 0:
            parts.append("L" + point(segments[cursor]))
            if segments[cursor][3]:
                parts.append("Z")
            cursor += 1
        elif kind == 1:
            group = segments[cursor:cursor + 3]
            if len(group) != 3:
                raise ValueError("svg_template_glyph_path_invalid")
            parts.append("C" + " ".join(point(item) for item in group))
            if group[-1][3]:
                parts.append("Z")
            cursor += 3
        else:
            raise ValueError("svg_template_glyph_path_invalid")
    return " ".join(parts)


def materialize_fixed_glyph_svg(
    canonical_svg: str, resolved_svg: str, authority_value: SvgTemplateFixedGlyphPreview | dict,
    *, trusted_font_css: str,
) -> MaterializedGlyphPreview:
    """Project only the complete proved fixed-text set into inert SVG paths."""
    authority = SvgTemplateFixedGlyphPreview.model_validate(authority_value)
    code_digest = materializer_code_digest()
    if authority.materializer_code_digest != code_digest:
        raise ValueError("svg_template_glyph_materializer_changed")
    if (not isinstance(trusted_font_css, str) or not trusted_font_css.startswith("@font-face{")
            or len(trusted_font_css.encode("ascii")) > 24 * 1024 * 1024
            or any(char in trusted_font_css for char in "<>&")):
        raise ValueError("svg_template_glyph_font_css_invalid")
    font_css_digest = sha256_prefixed(trusted_font_css.encode("ascii"))
    canonical = _parse(canonical_svg)
    resolved = _parse(resolved_svg)
    normalized = _without_qids(resolved)
    if any(node.get("data-presenton-fixed-glyph") is not None for node in canonical.iter()):
        raise ValueError("svg_template_glyph_reserved_marker_collision")
    if any(node.tag == f"{{{_SVG}}}style" for node in canonical.iter()):
        raise ValueError("svg_template_glyph_css_unsupported")
    _canvas(canonical, authority)
    _canvas(normalized, authority)
    if _image_neutralized(canonical) != _image_neutralized(normalized):
        raise ValueError("svg_template_glyph_resolved_source_mismatch")
    mapping = {item.svg_text_id: item for item in authority.text_objects}
    reserved_ids = {f"{item.svg_text_id}-glyph-{index}"
                    for item in authority.text_objects for index in range(len(item.characters))}
    if any(node.get("id") in reserved_ids for node in canonical.iter()):
        raise ValueError("svg_template_glyph_reserved_id_collision")
    selected = [node for node in normalized.iter() if node.get("id") in mapping]
    if len(selected) != len(mapping) or any(node.tag != f"{{{_SVG}}}text" for node in selected):
        raise ValueError("svg_template_glyph_text_set_mismatch")
    for node in selected:
        item = mapping[node.get("id")]
        if _digest_structure(node) != item.svg_text_digest:
            raise ValueError("svg_template_glyph_text_structure_mismatch")
        if (node.text or "") != "".join(chr(char.unicode) for char in item.characters):
            raise ValueError("svg_template_glyph_text_content_mismatch")
        _context(node, item)
    actual = [node for node in resolved.iter() if node.get("id") in mapping]
    if len(actual) != len(mapping):
        raise ValueError("svg_template_glyph_text_set_mismatch")
    for node in actual:
        item = mapping[node.get("id")]
        parent = node.getparent()
        if parent is None:
            raise ValueError("svg_template_glyph_text_context_unsupported")
        group = etree.Element(f"{{{_SVG}}}g")
        group.set("id", item.svg_text_id)
        group.set("data-presenton-fixed-glyph", "v1")
        group.tail = node.tail
        if node.get("data-qid") is not None:
            group.set("data-qid", node.get("data-qid"))
        for index, char in enumerate(item.characters):
            if not char.path_pdf:
                continue
            path = etree.SubElement(group, f"{{{_SVG}}}path")
            path.set("id", f"{item.svg_text_id}-glyph-{index}")
            path.set("d", _path_data(char, authority))
            path.set("fill", "#" + bytes(char.fill_rgba[:3]).hex())
        parent.replace(node, group)
    style = etree.Element(f"{{{_SVG}}}style")
    style.set("data-presenton-trusted-font-css", font_css_digest)
    style.text = trusted_font_css
    resolved.insert(0, style)
    display = _default_namespace(_without_qids(resolved))
    display_svg = etree.tostring(display, encoding="unicode")
    actual_svg = etree.tostring(_default_namespace(resolved), encoding="unicode")
    projected = etree.tostring(_default_namespace(_without_qids(
        _parse(actual_svg, max_bytes=32 * 1024 * 1024))), encoding="unicode")
    if projected != display_svg:
        raise ValueError("svg_template_glyph_projection_mismatch")
    render_digest = sha256_prefixed(actual_svg.encode())
    display_digest = sha256_prefixed(display_svg.encode())
    removed_qids = [(node.get("id"), node.get("data-qid")) for node in resolved.iter()
                    if node.get("data-qid") is not None]
    projection_digest = sha256_prefixed(json.dumps({
        "version": "svg-fixed-glyph-qid-projection/v1",
        "codeDigest": code_digest,
        "renderDigest": render_digest,
        "displayDigest": display_digest,
        "fontCssDigest": font_css_digest,
        "removedQids": removed_qids,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
    return MaterializedGlyphPreview(
        svg=actual_svg, display_svg=display_svg,
        canonical_digest=sha256_prefixed(canonical_svg.encode()),
        render_digest=render_digest, display_digest=display_digest,
        projection_digest=projection_digest,
        trusted_font_css_digest=font_css_digest,
        authority_digest=authority.authority_digest, materializer_code_digest=code_digest,
    )
