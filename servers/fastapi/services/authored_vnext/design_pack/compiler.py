"""Read-only compilers for PPTX and authored HTML/Scene design sources."""

from __future__ import annotations

import hashlib
import html
import json
import math
import posixpath
import re
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .models import (
    Canvas,
    DesignFrame,
    DesignPackStructure,
    DesignTokenSummary,
    EditableSlot,
    FallbackPolicy,
    NativeElement,
    Paint,
    Rect,
    RepresentativeRenderRef,
    SlotConstraint,
    Spacing,
    TextStyle,
    UnsupportedElement,
    canonical_digest,
    seal_structure,
)


_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PR = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS = {"p": _P, "a": _A, "r": _R, "pr": _PR}
_MAX_SOURCE_BYTES = 100 * 1024 * 1024
_MAX_XML_BYTES = 16 * 1024 * 1024
_MAX_SLIDES = 1000
_ACTIVE_PART_MARKERS = (
    "vbaproject",
    "activex",
    "embeddings/",
    "oleobject",
)
_UNSUPPORTED_TAGS = frozenset(
    {
        "oleObj",
        "videoFile",
        "audioFile",
        "contentPart",
        "control",
    }
)


class DesignPackCompileError(ValueError):
    """A source cannot be represented by the bounded design-pack contract."""


@dataclass(frozen=True, slots=True)
class CompiledDesignSource:
    structure: DesignPackStructure
    source_bytes: bytes
    source_filename: str
    source_media_type: str
    representative_assets: Mapping[str, bytes]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_bytes(value: bytes) -> str:
    return f"sha256:{_sha256(value)}"


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _xml(archive: ZipFile, part: str) -> tuple[ET.Element, bytes]:
    try:
        info = archive.getinfo(part)
    except KeyError as error:
        raise DesignPackCompileError(f"design_pack_part_missing:{part}") from error
    if info.file_size > _MAX_XML_BYTES:
        raise DesignPackCompileError("design_pack_xml_too_large")
    value = archive.read(info)
    upper = value[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise DesignPackCompileError("design_pack_xml_active_content")
    try:
        return ET.fromstring(value), value
    except ET.ParseError as error:
        raise DesignPackCompileError(f"design_pack_xml_invalid:{part}") from error


def _relationships(archive: ZipFile, source_part: str) -> dict[str, tuple[str, str]]:
    logical = PurePosixPath(source_part)
    rels_part = str(logical.parent / "_rels" / f"{logical.name}.rels")
    if rels_part not in archive.namelist():
        return {}
    root, _ = _xml(archive, rels_part)
    result: dict[str, tuple[str, str]] = {}
    for relation in root.findall("pr:Relationship", _NS):
        relation_id = relation.get("Id")
        target = relation.get("Target")
        relation_type = relation.get("Type", "")
        mode = relation.get("TargetMode", "Internal")
        if not relation_id or not target:
            continue
        if mode.lower() == "external":
            raise DesignPackCompileError("design_pack_external_relationship")
        resolved = posixpath.normpath(posixpath.join(str(logical.parent), target))
        if resolved.startswith("../") or resolved.startswith("/"):
            raise DesignPackCompileError("design_pack_relationship_escape")
        result[relation_id] = (resolved, relation_type)
    return result


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _shape_id(element: ET.Element, fallback: int) -> str:
    node = element.find(".//p:cNvPr", _NS)
    return (node.get("id") if node is not None else None) or str(fallback)


def _shape_name(element: ET.Element) -> str:
    node = element.find(".//p:cNvPr", _NS)
    return (node.get("name") if node is not None else None) or ""


def _bounds(element: ET.Element) -> Rect | None:
    transform = element.find("p:spPr/a:xfrm", _NS)
    if transform is None:
        transform = element.find("p:xfrm", _NS)
    if transform is None:
        transform = element.find("p:grpSpPr/a:xfrm", _NS)
    if transform is None:
        return None
    offset = transform.find("a:off", _NS)
    extent = transform.find("a:ext", _NS)
    if offset is None or extent is None:
        return None
    try:
        width = float(extent.get("cx", "0"))
        height = float(extent.get("cy", "0"))
        if width <= 0 or height <= 0:
            return None
        return Rect(
            x=max(0.0, float(offset.get("x", "0"))),
            y=max(0.0, float(offset.get("y", "0"))),
            width=width,
            height=height,
        )
    except (TypeError, ValueError):
        return None


def _color(parent: ET.Element | None) -> str | None:
    if parent is None:
        return None
    rgb = parent.find(".//a:srgbClr", _NS)
    if rgb is not None and re.fullmatch(r"[0-9A-Fa-f]{6}", rgb.get("val", "")):
        return f"#{rgb.get('val').upper()}"
    scheme = parent.find(".//a:schemeClr", _NS)
    if scheme is not None and re.fullmatch(r"[A-Za-z0-9_-]+", scheme.get("val", "")):
        return f"scheme:{scheme.get('val')}"
    return None


def _paint(element: ET.Element) -> Paint:
    properties = element.find("p:spPr", _NS)
    if properties is None:
        properties = element.find("p:grpSpPr", _NS)
    line = properties.find("a:ln", _NS) if properties is not None else None
    width = None
    if line is not None:
        try:
            width = float(line.get("w", "0"))
        except ValueError:
            width = None
    return Paint(
        fill=_color(properties.find("a:solidFill", _NS) if properties is not None else None),
        line=_color(line),
        line_width=width,
    )


def _text_style(element: ET.Element) -> TextStyle | None:
    text = "".join(node.text or "" for node in element.findall(".//a:t", _NS)).strip()
    if not text:
        return None
    run = element.find(".//a:rPr", _NS)
    if run is None:
        run = element.find(".//a:defRPr", _NS)
    latin = run.find("a:latin", _NS) if run is not None else None
    east_asian = run.find("a:ea", _NS) if run is not None else None
    size = None
    weight = None
    if run is not None:
        try:
            size = float(run.get("sz", "0")) / 100 or None
        except ValueError:
            size = None
        weight = 700 if run.get("b", "0") in {"1", "true"} else 400
    paragraph = element.find(".//a:pPr", _NS)
    alignment = {
        "l": "left",
        "ctr": "center",
        "r": "right",
        "just": "justify",
    }.get(paragraph.get("algn", "") if paragraph is not None else "", "unknown")
    return TextStyle(
        latin_font=latin.get("typeface") if latin is not None else None,
        east_asian_font=east_asian.get("typeface") if east_asian is not None else None,
        font_size=size,
        font_weight=weight,
        color=_color(run),
        alignment=alignment,
    )


def _spacing(element: ET.Element) -> Spacing | None:
    body = element.find("p:txBody/a:bodyPr", _NS)
    if body is None:
        return None
    def number(name: str) -> float:
        try:
            return max(0.0, float(body.get(name, "0")))
        except ValueError:
            return 0.0
    return Spacing(
        inset_left=number("lIns"),
        inset_top=number("tIns"),
        inset_right=number("rIns"),
        inset_bottom=number("bIns"),
    )


def _element_kind(element: ET.Element) -> str:
    local = _local_name(element)
    if local == "pic":
        return "image"
    if local == "graphicFrame":
        if element.find(".//a:tbl", _NS) is not None:
            return "table"
        if element.find(".//{http://schemas.openxmlformats.org/drawingml/2006/chart}chart") is not None:
            return "chart"
        return "unsupported"
    if local == "grpSp":
        return "group"
    if local == "cxnSp":
        return "connector"
    if element.findall(".//a:t", _NS):
        return "text"
    return "shape"


def _placeholder(element: ET.Element) -> str | None:
    value = element.find(".//p:nvPr/p:ph", _NS)
    if value is None:
        return None
    return value.get("type", "body")


def _semantic_role(kind: str, placeholder: str | None, name: str) -> str:
    normalized = f"{placeholder or ''} {name}".casefold()
    if any(value in normalized for value in ("title", "ctrtitle")):
        return "title"
    if "subTitle".casefold() in normalized:
        return "subtitle"
    if kind == "image":
        return "visual"
    if kind in {"table", "chart"}:
        return "data"
    return "body"


def _capacity(bounds: Rect, style: TextStyle | None, kind: str) -> tuple[int, str, tuple[SlotConstraint, ...]]:
    if kind != "text":
        return 1, "medium", (
            SlotConstraint(
                constraint_id="constraint:source-geometry",
                kind="source-geometry",
                value=True,
            ),
        )
    font_pt = style.font_size if style is not None and style.font_size else 18.0
    approximate_lines = max(1, int(bounds.height / max(1.0, font_pt * 12700 * 1.25)))
    approximate_columns = max(4, int(bounds.width / max(1.0, font_pt * 12700 * 0.55)))
    characters = max(8, min(10000, approximate_lines * approximate_columns))
    density = "low" if characters < 120 else "high" if characters > 500 else "medium"
    return characters, density, (
        SlotConstraint(
            constraint_id="constraint:source-geometry",
            kind="source-geometry",
            value=True,
        ),
        SlotConstraint(
            constraint_id="constraint:maximum-lines",
            kind="maximum-lines",
            value=approximate_lines,
        ),
        SlotConstraint(
            constraint_id="constraint:maximum-characters",
            kind="maximum-characters",
            value=characters,
        ),
        SlotConstraint(
            constraint_id="constraint:minimum-font",
            kind="minimum-font",
            value=max(8.0, min(font_pt, 18.0)),
        ),
    )


def _shape_nodes(root: ET.Element) -> list[ET.Element]:
    tree = root.find(".//p:spTree", _NS)
    if tree is None:
        return []
    return [
        child
        for child in list(tree)
        if _local_name(child) in {"sp", "pic", "graphicFrame", "grpSp", "cxnSp"}
    ]


def _native_element(element: ET.Element, part: str, ordinal: int, *, editable: bool) -> NativeElement | None:
    bounds = _bounds(element)
    element_bytes = ET.tostring(element, encoding="utf-8")
    shape_id = _shape_id(element, ordinal)
    kind = _element_kind(element)
    selector = f"pptx:{part}#shape-{shape_id}"
    if bounds is None and kind != "unsupported":
        return None
    return NativeElement(
        element_id=f"element:{PurePosixPath(part).stem}-{shape_id}",
        kind=kind,
        selector=selector,
        bounds=bounds,
        source_part=part,
        source_xml_digest=_digest_bytes(element_bytes),
        text_style=_text_style(element),
        spacing=_spacing(element),
        paint=_paint(element),
        editable=editable,
        fallback="raster" if kind == "unsupported" else "native",
    )


def _slots(root: ET.Element, part: str) -> tuple[EditableSlot, ...]:
    result: list[EditableSlot] = []
    for ordinal, element in enumerate(_shape_nodes(root), start=1):
        kind = _element_kind(element)
        if kind not in {"text", "image", "table", "chart"}:
            continue
        bounds = _bounds(element)
        if bounds is None:
            continue
        shape_id = _shape_id(element, ordinal)
        placeholder = _placeholder(element)
        style = _text_style(element)
        capacity, density, constraints = _capacity(bounds, style, kind)
        selector = f"pptx:{part}#shape-{shape_id}"
        if placeholder:
            selector += f"[placeholder={placeholder}]"
        result.append(
            EditableSlot(
                slot_id=f"slot:{PurePosixPath(part).stem}-{shape_id}",
                semantic_role=_semantic_role(
                    kind, placeholder, _shape_name(element)
                ),
                selector=selector,
                bounds=bounds,
                accepted_kinds=(kind,),
                source_shape_id=shape_id,
                source_xml_digest=_digest_bytes(ET.tostring(element, encoding="utf-8")),
                text_style=style,
                spacing=_spacing(element),
                paint=_paint(element),
                capacity_units=capacity,
                density=density,
                constraints=constraints,
            )
        )
    return tuple(result)


def _chrome(archive: ZipFile, parts: tuple[str | None, ...]) -> tuple[NativeElement, ...]:
    result: list[NativeElement] = []
    for part in parts:
        if part is None or part not in archive.namelist():
            continue
        root, _ = _xml(archive, part)
        for ordinal, element in enumerate(_shape_nodes(root), start=1):
            native = _native_element(element, part, ordinal, editable=False)
            if native is not None:
                result.append(native)
    return tuple(result)


def _unsupported(root: ET.Element, part: str) -> tuple[UnsupportedElement, ...]:
    result: list[UnsupportedElement] = []
    for element in root.iter():
        local = _local_name(element)
        if local in _UNSUPPORTED_TAGS:
            result.append(
                UnsupportedElement(
                    selector=f"pptx:{part}#{local}-{len(result) + 1}",
                    reason=f"unsupported OOXML element: {local}",
                    fallback="reject" if local in {"oleObj", "control"} else "raster",
                )
            )
    return tuple(result)


def _role(index: int, total: int, slots: tuple[EditableSlot, ...], layout_part: str | None) -> str:
    if index == 0:
        return "cover"
    if index == total - 1:
        return "closing"
    roles = {slot.semantic_role for slot in slots}
    kinds = {kind for slot in slots for kind in slot.accepted_kinds}
    layout_name = (layout_part or "").casefold()
    if "section" in layout_name or (roles <= {"title", "subtitle"} and len(slots) <= 3):
        return "section"
    if kinds & {"table", "chart"}:
        return "data"
    return "content"


def _frame_density(slots: tuple[EditableSlot, ...], canvas: Canvas) -> str:
    occupied = sum(slot.bounds.width * slot.bounds.height for slot in slots)
    share = occupied / (canvas.width * canvas.height)
    return "low" if share < 0.25 else "high" if share > 0.65 else "medium"


def _structural_svg(frame: DesignFrame, canvas: Canvas) -> bytes:
    target_width, target_height = 1280.0, 720.0
    sx, sy = target_width / canvas.width, target_height / canvas.height
    boxes: list[str] = []
    for slot in frame.slots:
        bounds = slot.bounds
        boxes.append(
            '<rect x="{:.2f}" y="{:.2f}" width="{:.2f}" height="{:.2f}" '
            'fill="none" stroke="#2563EB" stroke-width="2"/>'.format(
                bounds.x * sx,
                bounds.y * sy,
                bounds.width * sx,
                bounds.height * sy,
            )
        )
        boxes.append(
            '<text x="{:.2f}" y="{:.2f}" font-family="Arial" font-size="12" '
            'fill="#0F172A">{}</text>'.format(
                bounds.x * sx + 4,
                bounds.y * sy + 16,
                html.escape(slot.semantic_role),
            )
        )
    document = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
        'viewBox="0 0 1280 720"><rect width="1280" height="720" fill="#FFFFFF"/>'
        f'<text x="24" y="32" font-family="Arial" font-size="18" fill="#111827">'
        f'{html.escape(frame.frame_id)} · {html.escape(frame.role)}</text>'
        + "".join(boxes)
        + "</svg>\n"
    )
    return document.encode("utf-8")


def _part_for_relationship(
    relationships: Mapping[str, tuple[str, str]], relation_id: str | None, suffix: str
) -> str | None:
    if not relation_id:
        return None
    value = relationships.get(relation_id)
    if value is None or not value[1].endswith(suffix):
        return None
    return value[0]


def compile_pptx_design_source(path: Path | str) -> CompiledDesignSource:
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".pptx":
        raise DesignPackCompileError("design_pack_pptx_required")
    raw = source.read_bytes()
    if not raw or len(raw) > _MAX_SOURCE_BYTES:
        raise DesignPackCompileError("design_pack_source_size_invalid")
    try:
        archive = ZipFile(BytesIO(raw))
    except BadZipFile as error:
        raise DesignPackCompileError("design_pack_pptx_invalid") from error
    with archive:
        names = archive.namelist()
        folded = [name.casefold() for name in names]
        if any(marker in name for marker in _ACTIVE_PART_MARKERS for name in folded):
            raise DesignPackCompileError("design_pack_active_or_embedded_content")
        presentation, _ = _xml(archive, "ppt/presentation.xml")
        size = presentation.find("p:sldSz", _NS)
        if size is None:
            raise DesignPackCompileError("design_pack_canvas_missing")
        try:
            canvas = Canvas(
                width=float(size.get("cx", "0")),
                height=float(size.get("cy", "0")),
                unit="emu",
            )
        except ValueError as error:
            raise DesignPackCompileError("design_pack_canvas_invalid") from error
        presentation_rels = _relationships(archive, "ppt/presentation.xml")
        slide_parts: list[str] = []
        for slide_id in presentation.findall("p:sldIdLst/p:sldId", _NS):
            part = _part_for_relationship(
                presentation_rels, slide_id.get(f"{{{_R}}}id"), "/slide"
            )
            if part is None:
                raise DesignPackCompileError("design_pack_slide_relationship_invalid")
            slide_parts.append(part)
        if not slide_parts or len(slide_parts) > _MAX_SLIDES:
            raise DesignPackCompileError("design_pack_slide_count_invalid")

        frames: list[DesignFrame] = []
        font_counts: Counter[str] = Counter()
        color_counts: Counter[str] = Counter()
        line_counts: Counter[str] = Counter()
        spacing_counts: Counter[float] = Counter()
        for index, slide_part in enumerate(slide_parts):
            slide, slide_bytes = _xml(archive, slide_part)
            slide_rels = _relationships(archive, slide_part)
            layout_part = next(
                (
                    target
                    for target, relation_type in slide_rels.values()
                    if relation_type.endswith("/slideLayout")
                ),
                None,
            )
            master_part = None
            if layout_part is not None:
                layout_rels = _relationships(archive, layout_part)
                master_part = next(
                    (
                        target
                        for target, relation_type in layout_rels.values()
                        if relation_type.endswith("/slideMaster")
                    ),
                    None,
                )
            slots = _slots(slide, slide_part)
            frame = DesignFrame(
                frame_id=f"frame:slide-{index + 1}",
                source_index=index,
                role=_role(index, len(slide_parts), slots, layout_part),
                frame_type="pptx-native",
                source_part=slide_part,
                layout_part=layout_part,
                master_part=master_part,
                source_digest=_digest_bytes(slide_bytes),
                background=Paint(fill=_color(slide.find("p:cSld/p:bg", _NS))),
                density=_frame_density(slots, canvas),
                slots=slots,
                chrome=_chrome(archive, (master_part, layout_part)),
                unsupported=_unsupported(slide, slide_part),
                constraints=(
                    SlotConstraint(
                        constraint_id="constraint:inside-canvas",
                        kind="inside-canvas",
                        value=True,
                    ),
                ),
                representative_render_ref=f"representative/slide-{index + 1}.svg",
            )
            frames.append(frame)
            for slot in slots:
                if slot.text_style is not None:
                    for font in (
                        slot.text_style.latin_font,
                        slot.text_style.east_asian_font,
                    ):
                        if font:
                            font_counts[font] += 1
                    if slot.text_style.color:
                        color_counts[slot.text_style.color] += 1
                if slot.paint is not None:
                    if slot.paint.fill:
                        color_counts[slot.paint.fill] += 1
                    if slot.paint.line:
                        line_counts[slot.paint.line] += 1
                if slot.spacing is not None:
                    for value in (
                        slot.spacing.inset_left,
                        slot.spacing.inset_top,
                        slot.spacing.inset_right,
                        slot.spacing.inset_bottom,
                    ):
                        spacing_counts[value] += 1

        assets: dict[str, bytes] = {}
        render_refs: list[RepresentativeRenderRef] = []
        for frame in frames:
            relative = frame.representative_render_ref
            assert relative is not None
            content = _structural_svg(frame, canvas)
            assets[relative] = content
            render_refs.append(
                RepresentativeRenderRef(
                    role=frame.role,
                    asset_path=relative,
                    sha256=_sha256(content),
                    media_type="image/svg+xml",
                    evidence_level="structural",
                )
            )
        structure = seal_structure(
            source_kind="pptx",
            source_digest=_digest_bytes(raw),
            canvas=canvas,
            frames=tuple(frames),
            tokens=DesignTokenSummary(
                typography=tuple(value for value, _ in font_counts.most_common(256)),
                colors=tuple(value for value, _ in color_counts.most_common(256)),
                line_styles=tuple(value for value, _ in line_counts.most_common(256)),
                spacing_values=tuple(value for value, _ in spacing_counts.most_common(512)),
            ),
            fallback_policy=FallbackPolicy(),
            representative_renders=tuple(render_refs),
        )
        return CompiledDesignSource(
            structure=structure,
            source_bytes=raw,
            source_filename=source.name,
            source_media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            representative_assets=assets,
        )


def _authored_rect(value: Mapping[str, Any]) -> Rect:
    try:
        return Rect(
            x=float(value["x"]),
            y=float(value["y"]),
            width=float(value["width"]),
            height=float(value["height"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DesignPackCompileError("design_pack_authored_bounds_invalid") from error


def compile_authored_design_source(source: Mapping[str, Any]) -> CompiledDesignSource:
    if source.get("schemaVersion") != "authored-design-source/v1":
        raise DesignPackCompileError("design_pack_authored_schema_invalid")
    raw = _canonical_bytes(source)
    if len(raw) > _MAX_SOURCE_BYTES:
        raise DesignPackCompileError("design_pack_source_size_invalid")
    canvas_value = source.get("canvas")
    frames_value = source.get("frames")
    if not isinstance(canvas_value, Mapping) or not isinstance(frames_value, list):
        raise DesignPackCompileError("design_pack_authored_source_invalid")
    try:
        canvas = Canvas(
            width=float(canvas_value["width"]),
            height=float(canvas_value["height"]),
            unit="px",
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DesignPackCompileError("design_pack_canvas_invalid") from error
    frames: list[DesignFrame] = []
    for index, frame_value in enumerate(frames_value):
        if not isinstance(frame_value, Mapping):
            raise DesignPackCompileError("design_pack_authored_frame_invalid")
        frame_id = str(frame_value.get("id") or f"frame-{index + 1}")
        frame_type = str(frame_value.get("type") or "scene")
        if frame_type not in {"html", "scene"}:
            raise DesignPackCompileError("design_pack_authored_frame_type_invalid")
        role = str(frame_value.get("role") or ("cover" if index == 0 else "closing" if index == len(frames_value) - 1 else "content"))
        if role not in {"cover", "section", "content", "data", "timeline", "architecture", "process", "closing"}:
            raise DesignPackCompileError("design_pack_authored_role_invalid")
        elements = frame_value.get("elements")
        if not isinstance(elements, list):
            raise DesignPackCompileError("design_pack_authored_elements_invalid")
        slots: list[EditableSlot] = []
        chrome: list[NativeElement] = []
        unsupported: list[UnsupportedElement] = []
        frame_payload = _canonical_bytes(frame_value)
        for ordinal, element_value in enumerate(elements, start=1):
            if not isinstance(element_value, Mapping):
                raise DesignPackCompileError("design_pack_authored_element_invalid")
            element_id = str(element_value.get("id") or f"element-{ordinal}")
            kind = str(element_value.get("kind") or "unsupported")
            bounds_value = element_value.get("bounds")
            bounds = _authored_rect(bounds_value) if isinstance(bounds_value, Mapping) else None
            editable = element_value.get("editable") is True
            selector = f"{frame_type}:{frame_id}#{element_id}"
            element_bytes = _canonical_bytes(element_value)
            if editable and kind in {"text", "image", "table", "chart"} and bounds is not None:
                capacity, density, constraints = _capacity(bounds, None, kind)
                slots.append(
                    EditableSlot(
                        slot_id=f"slot:{frame_id}-{element_id}",
                        semantic_role=str(element_value.get("semanticRole") or ("data" if kind in {"table", "chart"} else "visual" if kind == "image" else "body")),
                        selector=selector,
                        bounds=bounds,
                        accepted_kinds=(kind,),
                        source_shape_id=element_id,
                        source_xml_digest=_digest_bytes(element_bytes),
                        capacity_units=capacity,
                        density=density,
                        constraints=constraints,
                    )
                )
            elif kind in {"text", "image", "table", "chart", "shape", "group", "connector", "background"}:
                chrome.append(
                    NativeElement(
                        element_id=f"element:{frame_id}-{element_id}",
                        kind=kind,
                        selector=selector,
                        bounds=bounds,
                        source_part=f"authored/{frame_id}",
                        source_xml_digest=_digest_bytes(element_bytes),
                        editable=False,
                    )
                )
            else:
                unsupported.append(
                    UnsupportedElement(
                        selector=selector,
                        reason=f"unsupported authored element: {kind}",
                        fallback="raster",
                    )
                )
        frames.append(
            DesignFrame(
                frame_id=f"frame:{frame_id}",
                source_index=index,
                role=role,
                frame_type=frame_type,
                source_part=f"authored/{frame_id}",
                source_digest=_digest_bytes(frame_payload),
                density=_frame_density(tuple(slots), canvas),
                slots=tuple(slots),
                chrome=tuple(chrome),
                unsupported=tuple(unsupported),
                constraints=(
                    SlotConstraint(
                        constraint_id="constraint:inside-canvas",
                        kind="inside-canvas",
                        value=True,
                    ),
                ),
                representative_render_ref=f"representative/frame-{index + 1}.svg",
            )
        )
    if not frames or len(frames) > _MAX_SLIDES:
        raise DesignPackCompileError("design_pack_frame_count_invalid")
    assets: dict[str, bytes] = {}
    render_refs: list[RepresentativeRenderRef] = []
    for frame in frames:
        relative = frame.representative_render_ref
        assert relative is not None
        content = _structural_svg(frame, canvas)
        assets[relative] = content
        render_refs.append(
            RepresentativeRenderRef(
                role=frame.role,
                asset_path=relative,
                sha256=_sha256(content),
                media_type="image/svg+xml",
                evidence_level="structural",
            )
        )
    structure = seal_structure(
        source_kind="authored",
        source_digest=_digest_bytes(raw),
        canvas=canvas,
        frames=tuple(frames),
        tokens=DesignTokenSummary(),
        fallback_policy=FallbackPolicy(),
        representative_renders=tuple(render_refs),
    )
    return CompiledDesignSource(
        structure=structure,
        source_bytes=raw,
        source_filename="authored-source.json",
        source_media_type="application/json",
        representative_assets=assets,
    )


def compile_design_source(source: Path | str | Mapping[str, Any]) -> CompiledDesignSource:
    if isinstance(source, Mapping):
        return compile_authored_design_source(source)
    path = Path(source)
    if path.suffix.lower() == ".pptx":
        return compile_pptx_design_source(path)
    if path.suffix.lower() == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DesignPackCompileError("design_pack_authored_source_invalid") from error
        if not isinstance(value, Mapping):
            raise DesignPackCompileError("design_pack_authored_source_invalid")
        return compile_authored_design_source(value)
    raise DesignPackCompileError("design_pack_source_type_unsupported")


__all__ = [
    "CompiledDesignSource",
    "DesignPackCompileError",
    "compile_authored_design_source",
    "compile_design_source",
    "compile_pptx_design_source",
]
