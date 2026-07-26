from __future__ import annotations

import posixpath
from pathlib import PurePosixPath

from .chart_parser import parse_cached_chart
from .models import (
    PresentationCandidates,
    ShapeCandidate,
    SlideCandidate,
    TextRunCandidate,
)
from .package_reader import PptxPackageReader, UnsafePptxPackage
from .relationship_graph import build_relationship_graph_evidence
from .style_graph import build_style_graph_evidence


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
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


def _shape_transform(
    shape,
    slide_cx: float,
    slide_cy: float,
    *,
    scale_x: float | None = None,
    scale_y: float | None = None,
    origin_x: float = 0,
    origin_y: float = 0,
) -> dict[str, float]:
    xfrm = shape.find("./p:spPr/a:xfrm", NS)
    if xfrm is None:
        xfrm = shape.find("./p:xfrm", NS)
    if xfrm is None:
        xfrm = shape.find("./p:grpSpPr/a:xfrm", NS)
    if xfrm is None:
        return {"x": 0, "y": 0, "width": 0, "height": 0, "rotation": 0}
    off = xfrm.find("a:off", NS)
    ext = xfrm.find("a:ext", NS)
    scale_x = scale_x if scale_x is not None else CANVAS_WIDTH / slide_cx
    scale_y = scale_y if scale_y is not None else scale_x
    canvas_height = slide_cy * scale_y
    return {
        "x": (
            _number(off.get("x") if off is not None else None) - origin_x
        )
        * scale_x,
        "y": (
            _number(off.get("y") if off is not None else None) - origin_y
        )
        * scale_y,
        "width": _number(ext.get("cx") if ext is not None else None) * scale_x,
        "height": _number(ext.get("cy") if ext is not None else None) * scale_y,
        "rotation": _number(xfrm.get("rot")) / 60000,
        "_canvas_height": canvas_height,
    }


def _shape_identity(shape, index: int) -> tuple[str, str]:
    props = shape.find("./p:nvSpPr/p:cNvPr", NS)
    if props is None:
        props = shape.find("./p:nvGraphicFramePr/p:cNvPr", NS)
    if props is None:
        props = shape.find("./p:nvGrpSpPr/p:cNvPr", NS)
    if props is None:
        return str(index), f"shape_{index}"
    return props.get("id") or str(index), props.get("name") or f"shape_{index}"


def _ooxml_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.lower() in {"1", "true", "on"}


def _run_candidate(node) -> TextRunCandidate | None:
    text = node.find("./a:t", NS)
    if text is None:
        return None
    properties = node.find("./a:rPr", NS)
    size = _number(properties.get("sz")) / 100 if properties is not None else 0
    family = properties.find("./a:latin", NS) if properties is not None else None
    color = (
        properties.find("./a:solidFill/a:srgbClr", NS)
        if properties is not None
        else None
    )
    raw_color = color.get("val") if color is not None else None
    underline = properties.get("u") if properties is not None else None
    return TextRunCandidate(
        text=text.text or "",
        font_size=size or None,
        font_family=family.get("typeface") if family is not None else None,
        font_color=(
            f"#{raw_color[:6].upper()}"
            if raw_color and len(raw_color) in {6, 8}
            else None
        ),
        bold=_ooxml_bool(properties.get("b") if properties is not None else None),
        italic=_ooxml_bool(properties.get("i") if properties is not None else None),
        underline=(underline != "none") if underline is not None else None,
    )


def _text_runs(shape) -> list[TextRunCandidate]:
    runs: list[TextRunCandidate] = []
    paragraphs = shape.findall(".//a:p", NS)
    for paragraph_index, paragraph in enumerate(paragraphs):
        paragraph_runs: list[TextRunCandidate] = []
        for child in list(paragraph):
            tag = child.tag.rsplit("}", 1)[-1]
            if tag in {"r", "fld"}:
                candidate = _run_candidate(child)
                if candidate is not None:
                    paragraph_runs.append(candidate)
            elif tag == "br":
                paragraph_runs.append(TextRunCandidate(text="\n"))
        if not paragraph_runs:
            continue
        if runs and paragraph_index > 0:
            runs.append(TextRunCandidate(text="\n"))
        runs.extend(paragraph_runs)
    return runs


def _text(shape) -> str:
    return "".join(run.text for run in _text_runs(shape))


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


def _parse_shape(
    shape,
    index: int,
    slide_cx: float,
    slide_cy: float,
    **space,
) -> ShapeCandidate:
    source_id, name = _shape_identity(shape, index)
    transform = _shape_transform(shape, slide_cx, slide_cy, **space)
    transform.pop("_canvas_height", None)
    text_runs = _text_runs(shape)
    text = "".join(run.text for run in text_runs)
    if text:
        return ShapeCandidate(
            source_id=source_id,
            name=name,
            kind="text",
            text=text,
            text_runs=text_runs,
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
    reader: PptxPackageReader,
    relationships: dict[str, tuple[str, str]],
    **space,
) -> ShapeCandidate:
    source_id, name = _shape_identity(shape, index)
    transform = _shape_transform(shape, slide_cx, slide_cy, **space)
    transform.pop("_canvas_height", None)
    table = shape.find("./a:graphic/a:graphicData/a:tbl", NS)
    if table is None:
        smart_art = shape.find("./a:graphic/a:graphicData/dgm:relIds", NS)
        if smart_art is not None:
            return ShapeCandidate(
                source_id=source_id,
                name=name,
                kind="unsupported",
                confidence=0,
                unsupported_reason="unsupported_ooxml:smartArt",
                **transform,
            )
        chart_ref = shape.find("./a:graphic/a:graphicData/c:chart", NS)
        relationship_id = (
            chart_ref.get(f"{REL_NS}id") if chart_ref is not None else None
        )
        relationship = relationships.get(relationship_id or "")
        parsed = (
            parse_cached_chart(reader.read_xml(relationship[1]))
            if relationship is not None
            and relationship[0].endswith("/chart")
            else None
        )
        if parsed is not None and transform["width"] >= 80 and transform["height"] >= 60:
            chart_type, categories, series = parsed
            return ShapeCandidate(
                source_id=source_id,
                name=name,
                kind="chart",
                chart_type=chart_type,
                chart_categories=categories,
                chart_series=series,
                confidence=0.86,
                **transform,
            )
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


def _parse_group(
    shape,
    index: int,
    slide_cx: float,
    slide_cy: float,
    reader: PptxPackageReader,
    relationships: dict[str, tuple[str, str]],
    **space,
) -> ShapeCandidate:
    source_id, name = _shape_identity(shape, index)
    transform = _shape_transform(shape, slide_cx, slide_cy, **space)
    transform.pop("_canvas_height", None)
    xfrm = shape.find("./p:grpSpPr/a:xfrm", NS)
    child_off = xfrm.find("a:chOff", NS) if xfrm is not None else None
    child_ext = xfrm.find("a:chExt", NS) if xfrm is not None else None
    child_cx = _number(child_ext.get("cx") if child_ext is not None else None)
    child_cy = _number(child_ext.get("cy") if child_ext is not None else None)
    if child_cx <= 0 or child_cy <= 0:
        return ShapeCandidate(
            source_id=source_id,
            name=name,
            kind="unsupported",
            confidence=0,
            unsupported_reason="invalid_group_coordinate_space",
            **transform,
        )
    child_space = {
        "scale_x": transform["width"] / child_cx,
        "scale_y": transform["height"] / child_cy,
        "origin_x": _number(
            child_off.get("x") if child_off is not None else None
        ),
        "origin_y": _number(
            child_off.get("y") if child_off is not None else None
        ),
    }
    children: list[ShapeCandidate] = []
    for child_index, child in enumerate(list(shape), start=1):
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "sp":
            children.append(
                _parse_shape(child, child_index, slide_cx, slide_cy, **child_space)
            )
        elif tag == "graphicFrame":
            children.append(
                _parse_graphic_frame(
                    child,
                    child_index,
                    slide_cx,
                    slide_cy,
                    reader,
                    relationships,
                    **child_space,
                )
            )
        elif tag == "grpSp":
            children.append(
                _parse_group(
                    child,
                    child_index,
                    slide_cx,
                    slide_cy,
                    reader,
                    relationships,
                    **child_space,
                )
            )
    supported = [child for child in children if child.kind != "unsupported"]
    if not supported:
        return ShapeCandidate(
            source_id=source_id,
            name=name,
            kind="unsupported",
            confidence=0,
            unsupported_reason="group_contains_no_supported_shapes",
            **transform,
        )
    return ShapeCandidate(
        source_id=source_id,
        name=name,
        kind="group",
        children=supported,
        confidence=min(child.confidence for child in supported),
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
        slide_relationships: dict[str, tuple[str, str]] = {}
        if rels_part in reader.member_names:
            slide_relationships, slide_external = _relationships(
                reader,
                rels_part,
                slide_part,
            )
        shapes: list[ShapeCandidate] = []
        shape_tree = slide.find("./p:cSld/p:spTree", NS)
        if shape_tree is not None:
            for index, child in enumerate(list(shape_tree), start=1):
                tag = child.tag.rsplit("}", 1)[-1]
                if tag == "sp":
                    shapes.append(_parse_shape(child, index, slide_cx, slide_cy))
                elif tag == "graphicFrame":
                    shapes.append(
                        _parse_graphic_frame(
                            child,
                            index,
                            slide_cx,
                            slide_cy,
                            reader,
                            slide_relationships,
                        )
                    )
                elif tag == "grpSp":
                    shapes.append(
                        _parse_group(
                            child,
                            index,
                            slide_cx,
                            slide_cy,
                            reader,
                            slide_relationships,
                        )
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
