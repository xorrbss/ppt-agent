from __future__ import annotations

import posixpath
from pathlib import PurePosixPath

from .models import PresentationCandidates, ShapeCandidate, SlideCandidate
from .package_reader import PptxPackageReader, UnsafePptxPackage
from .relationship_graph import build_relationship_graph_evidence
from .style_graph import build_style_graph_evidence


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
REL_NS = f"{{{NS['r']}}}"
SLIDE_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)
CANVAS_WIDTH = 1280.0


def _safe_part_target(source_part: str, target: str) -> str:
    if "\\" in target or target.startswith("/") or ":" in target:
        raise UnsafePptxPackage("unsafe_relationship_target")
    base = posixpath.dirname(source_part)
    resolved = posixpath.normpath(posixpath.join(base, target))
    if resolved == ".." or resolved.startswith("../"):
        raise UnsafePptxPackage("unsafe_relationship_target")
    return str(PurePosixPath(resolved))


def _relationships(
    reader: PptxPackageReader,
    rels_part: str,
    source_part: str,
) -> tuple[dict[str, tuple[str, str]], list[str]]:
    root = reader.read_xml(rels_part)
    internal: dict[str, tuple[str, str]] = {}
    external: list[str] = []
    for rel in root.findall("pr:Relationship", NS):
        rel_id = rel.get("Id")
        rel_type = rel.get("Type")
        target = rel.get("Target")
        if not rel_id or not rel_type or not target:
            raise UnsafePptxPackage("invalid_ooxml_relationship")
        if rel.get("TargetMode") == "External":
            external.append(rel_id)
            continue
        internal[rel_id] = (
            rel_type,
            _safe_part_target(source_part, target),
        )
    return internal, external


def _number(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def _shape_transform(shape, slide_cx: float, slide_cy: float) -> dict[str, float]:
    xfrm = shape.find("./p:spPr/a:xfrm", NS)
    if xfrm is None:
        xfrm = shape.find("./p:xfrm", NS)
    if xfrm is None:
        return {"x": 0, "y": 0, "width": 0, "height": 0, "rotation": 0}
    off = xfrm.find("a:off", NS)
    ext = xfrm.find("a:ext", NS)
    scale_x = CANVAS_WIDTH / slide_cx
    canvas_height = slide_cy * scale_x
    return {
        "x": _number(off.get("x") if off is not None else None) * scale_x,
        "y": _number(off.get("y") if off is not None else None) * scale_x,
        "width": _number(ext.get("cx") if ext is not None else None) * scale_x,
        "height": _number(ext.get("cy") if ext is not None else None) * scale_x,
        "rotation": _number(xfrm.get("rot")) / 60000,
        "_canvas_height": canvas_height,
    }


def _shape_identity(shape, index: int) -> tuple[str, str]:
    props = shape.find("./p:nvSpPr/p:cNvPr", NS)
    if props is None:
        props = shape.find("./p:nvGraphicFramePr/p:cNvPr", NS)
    if props is None:
        return str(index), f"shape_{index}"
    return props.get("id") or str(index), props.get("name") or f"shape_{index}"


def _text(shape) -> str:
    paragraphs: list[str] = []
    for paragraph in shape.findall(".//a:p", NS):
        value = "".join(node.text or "" for node in paragraph.findall(".//a:t", NS))
        if value:
            paragraphs.append(value)
    return "\n".join(paragraphs)


def _fill_color(shape) -> str | None:
    color = shape.find("./p:spPr/a:solidFill/a:srgbClr", NS)
    value = color.get("val") if color is not None else None
    if value and len(value) in {6, 8}:
        return f"#{value[:6].upper()}"
    return None


def _unsupported_shape(shape, index: int, reason: str) -> ShapeCandidate:
    tag = shape.tag.rsplit("}", 1)[-1]
    return ShapeCandidate(
        source_id=f"{tag}-{index}",
        name=f"{tag}_{index}",
        kind="unsupported",
        confidence=0,
        unsupported_reason=reason,
    )


def _parse_shape(shape, index: int, slide_cx: float, slide_cy: float) -> ShapeCandidate:
    source_id, name = _shape_identity(shape, index)
    transform = _shape_transform(shape, slide_cx, slide_cy)
    transform.pop("_canvas_height", None)
    text = _text(shape)
    if text:
        return ShapeCandidate(
            source_id=source_id,
            name=name,
            kind="text",
            text=text,
            confidence=0.92,
            **transform,
        )
    geometry = shape.find("./p:spPr/a:prstGeom", NS)
    preset = geometry.get("prst") if geometry is not None else None
    if preset in {"rect", "roundRect"}:
        return ShapeCandidate(
            source_id=source_id,
            name=name,
            kind="container",
            fill_color=_fill_color(shape),
            confidence=0.88,
            **transform,
        )
    return ShapeCandidate(
        source_id=source_id,
        name=name,
        kind="unsupported",
        confidence=0,
        unsupported_reason=f"unsupported_shape_geometry:{preset or 'unknown'}",
        **transform,
    )


def _parse_graphic_frame(
    shape,
    index: int,
    slide_cx: float,
    slide_cy: float,
) -> ShapeCandidate:
    source_id, name = _shape_identity(shape, index)
    transform = _shape_transform(shape, slide_cx, slide_cy)
    transform.pop("_canvas_height", None)
    table = shape.find("./a:graphic/a:graphicData/a:tbl", NS)
    if table is None:
        return ShapeCandidate(
            source_id=source_id,
            name=name,
            kind="unsupported",
            confidence=0,
            unsupported_reason="unsupported_ooxml:graphicFrame",
            **transform,
        )
    rows = [
        [_text(cell) for cell in row.findall("./a:tc", NS)]
        for row in table.findall("./a:tr", NS)
    ]
    has_merges = any(
        table.find(f".//a:{tag}", NS) is not None
        for tag in ("gridSpan", "vMerge", "hMerge")
    )
    if (
        not rows
        or not rows[0]
        or any(len(row) != len(rows[0]) for row in rows)
        or has_merges
    ):
        return ShapeCandidate(
            source_id=source_id,
            name=name,
            kind="unsupported",
            confidence=0,
            unsupported_reason="unsupported_table_structure",
            **transform,
        )
    return ShapeCandidate(
        source_id=source_id,
        name=name,
        kind="table",
        table_rows=rows,
        confidence=0.9,
        **transform,
    )


def parse_presentation_candidates(
    reader: PptxPackageReader,
    *,
    source_sha256: str,
) -> PresentationCandidates:
    reader.preflight()
    relationship_graph = build_relationship_graph_evidence(reader)
    style_graph = build_style_graph_evidence(reader, relationship_graph)
    if not (style_graph.themes or style_graph.masters or style_graph.layouts):
        style_graph = None
    presentation = reader.read_xml("ppt/presentation.xml")
    rels, presentation_external = _relationships(
        reader,
        "ppt/_rels/presentation.xml.rels",
        "ppt/presentation.xml",
    )
    size = presentation.find("p:sldSz", NS)
    slide_cx = _number(size.get("cx") if size is not None else None)
    slide_cy = _number(size.get("cy") if size is not None else None)
    if slide_cx <= 0 or slide_cy <= 0:
        raise UnsafePptxPackage("invalid_slide_size")
    scale = CANVAS_WIDTH / slide_cx
    canvas_height = slide_cy * scale
    slides: list[SlideCandidate] = []
    for slide_id in presentation.findall("./p:sldIdLst/p:sldId", NS):
        relationship_id = slide_id.get(f"{REL_NS}id")
        relationship = rels.get(relationship_id or "")
        if (
            not relationship_id
            or relationship is None
            or relationship[0] != SLIDE_REL_TYPE
        ):
            raise UnsafePptxPackage("invalid_slide_relationship")
        slide_part = relationship[1]
        slide = reader.read_xml(slide_part)
        rels_part = (
            f"{posixpath.dirname(slide_part)}/_rels/"
            f"{posixpath.basename(slide_part)}.rels"
        )
        slide_external: list[str] = []
        if rels_part in reader.member_names:
            _, slide_external = _relationships(reader, rels_part, slide_part)
        shapes: list[ShapeCandidate] = []
        shape_tree = slide.find("./p:cSld/p:spTree", NS)
        if shape_tree is not None:
            for index, child in enumerate(list(shape_tree), start=1):
                tag = child.tag.rsplit("}", 1)[-1]
                if tag == "sp":
                    shapes.append(_parse_shape(child, index, slide_cx, slide_cy))
                elif tag == "graphicFrame":
                    shapes.append(
                        _parse_graphic_frame(child, index, slide_cx, slide_cy)
                    )
                elif tag not in {"nvGrpSpPr", "grpSpPr"}:
                    shapes.append(
                        _unsupported_shape(child, index, f"unsupported_ooxml:{tag}")
                    )
        slides.append(
            SlideCandidate(
                source_part=slide_part,
                relationship_id=relationship_id,
                width=CANVAS_WIDTH,
                height=canvas_height,
                shapes=shapes,
                external_relationships=sorted(
                    set(presentation_external + slide_external)
                ),
            )
        )
    if not slides:
        raise UnsafePptxPackage("pptx_contains_no_slides")
    return PresentationCandidates(
        source_sha256=source_sha256,
        slides=slides,
        relationship_graph=relationship_graph,
        style_graph=style_graph,
    )
