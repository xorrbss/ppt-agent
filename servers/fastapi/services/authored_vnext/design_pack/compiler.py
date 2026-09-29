"""Read-only compilers for PPTX and authored HTML/Scene design sources."""

from __future__ import annotations

import json
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
    CapacityEnvelope,
    Canvas,
    DesignFrame,
    DesignPackStructure,
    DesignTokenSummary,
    EditableSlot,
    FallbackPolicy,
    FrameCapabilities,
    NativeElement,
    Paint,
    PatchHandle,
    Rect,
    RepresentativeRenderRef,
    SlotConstraint,
    Spacing,
    TextStyle,
    UnsupportedElement,
    seal_structure,
)
from .structural_render import structural_svg
from utils.hashing import sha256_hex


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
_NON_CONTENT_PLACEHOLDERS = frozenset({"dt", "ftr", "sldnum", "hdr"})
_TEXT_PLACEHOLDERS = frozenset(
    {"title", "ctrtitle", "subtitle", "body", "vertbody", "verttitle"}
)
_IMAGE_PLACEHOLDERS = frozenset({"pic", "clipart", "media"})
_ROLE_HINTS = {
    "timeline": ("timeline", "milestone", "chronology"),
    "architecture": ("architecture", "system map", "topology", "diagram"),
    "process": ("process", "workflow", "flow", "step"),
    "data": ("data", "chart", "table", "metric", "kpi", "stat"),
    "section": ("section", "divider", "chapter", "agenda"),
    "closing": ("closing", "thank", "contact", "q&a", "questions"),
    "cover": ("cover", "opening", "front page"),
}


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
    return sha256_hex(value)


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


def _bounds(
    element: ET.Element, fallbacks: tuple[ET.Element, ...] = ()
) -> Rect | None:
    for candidate in (element, *fallbacks):
        transform = candidate.find("p:spPr/a:xfrm", _NS)
        if transform is None:
            transform = candidate.find("p:xfrm", _NS)
        if transform is None:
            transform = candidate.find("p:grpSpPr/a:xfrm", _NS)
        if transform is None:
            continue
        offset = transform.find("a:off", _NS)
        extent = transform.find("a:ext", _NS)
        if offset is None or extent is None:
            continue
        try:
            width = float(extent.get("cx", "0"))
            height = float(extent.get("cy", "0"))
            if width <= 0 or height <= 0:
                continue
            return Rect(
                x=max(0.0, float(offset.get("x", "0"))),
                y=max(0.0, float(offset.get("y", "0"))),
                width=width,
                height=height,
            )
        except (TypeError, ValueError):
            continue
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


def _paint(element: ET.Element, fallbacks: tuple[ET.Element, ...] = ()) -> Paint:
    fill = line_color = None
    width = None
    for candidate in (element, *fallbacks):
        properties = candidate.find("p:spPr", _NS)
        if properties is None:
            properties = candidate.find("p:grpSpPr", _NS)
        if properties is None:
            continue
        line = properties.find("a:ln", _NS)
        if fill is None:
            fill = _color(properties.find("a:solidFill", _NS))
        if line_color is None:
            line_color = _color(line)
        if width is None and line is not None:
            try:
                width = float(line.get("w")) if line.get("w") is not None else None
            except ValueError:
                width = None
    return Paint(fill=fill, line=line_color, line_width=width)


def _text_style(
    element: ET.Element, fallbacks: tuple[ET.Element, ...] = ()
) -> TextStyle | None:
    candidates = (element, *fallbacks)
    runs: list[ET.Element] = []
    for candidate in candidates:
        for path in (".//a:rPr", ".//a:defRPr", ".//a:endParaRPr"):
            run = candidate.find(path, _NS)
            if run is not None:
                runs.append(run)
    if not runs and not any(candidate.find("p:txBody", _NS) is not None for candidate in candidates):
        return None

    def first_attr(name: str) -> str | None:
        return next((run.get(name) for run in runs if run.get(name) is not None), None)

    def first_typeface(tag: str) -> str | None:
        for run in runs:
            node = run.find(tag, _NS)
            if node is not None and node.get("typeface"):
                return node.get("typeface")
        return None

    size = letter_spacing = None
    try:
        raw_size = first_attr("sz")
        size = float(raw_size) / 100 if raw_size else None
    except ValueError:
        size = None
    try:
        raw_spacing = first_attr("spc")
        letter_spacing = float(raw_spacing) / 100 if raw_spacing else None
    except ValueError:
        letter_spacing = None
    bold = first_attr("b")
    italic = first_attr("i")
    underline = first_attr("u")
    paragraph = next(
        (
            candidate.find(".//a:pPr", _NS)
            for candidate in candidates
            if candidate.find(".//a:pPr", _NS) is not None
        ),
        None,
    )
    body = next(
        (
            candidate.find("p:txBody/a:bodyPr", _NS)
            for candidate in candidates
            if candidate.find("p:txBody/a:bodyPr", _NS) is not None
        ),
        None,
    )
    alignment = {
        "l": "left",
        "ctr": "center",
        "r": "right",
        "just": "justify",
        "dist": "justify",
    }.get(paragraph.get("algn", "") if paragraph is not None else "", "unknown")
    vertical_alignment = {
        "t": "top",
        "ctr": "middle",
        "b": "bottom",
    }.get(body.get("anchor", "") if body is not None else "", "unknown")
    return TextStyle(
        latin_font=first_typeface("a:latin"),
        east_asian_font=first_typeface("a:ea"),
        font_size=size,
        font_weight=(700 if bold in {"1", "true"} else 400 if bold is not None else None),
        italic=(italic in {"1", "true"}) if italic is not None else None,
        underline=(underline not in {"none", "0", "false"}) if underline is not None else None,
        letter_spacing=letter_spacing,
        color=next((_color(run) for run in runs if _color(run) is not None), None),
        alignment=alignment,
        vertical_alignment=vertical_alignment,
    )


def _spacing(
    element: ET.Element, fallbacks: tuple[ET.Element, ...] = ()
) -> Spacing | None:
    candidates = (element, *fallbacks)
    body = next(
        (
            candidate.find("p:txBody/a:bodyPr", _NS)
            for candidate in candidates
            if candidate.find("p:txBody/a:bodyPr", _NS) is not None
        ),
        None,
    )
    paragraph = next(
        (
            candidate.find(".//a:pPr", _NS)
            for candidate in candidates
            if candidate.find(".//a:pPr", _NS) is not None
        ),
        None,
    )
    if body is None and paragraph is None:
        return None

    def body_number(name: str) -> float:
        if body is None:
            return 0.0
        try:
            return max(0.0, float(body.get(name, "0")))
        except ValueError:
            return 0.0

    def paragraph_spacing(name: str) -> float | None:
        if paragraph is None:
            return None
        node = paragraph.find(f"a:{name}", _NS)
        points = node.find("a:spcPts", _NS) if node is not None else None
        percent = node.find("a:spcPct", _NS) if node is not None else None
        try:
            if points is not None:
                return max(0.0, float(points.get("val", "0")) / 100)
            if percent is not None:
                return max(0.0, float(percent.get("val", "0")) / 100000)
        except ValueError:
            return None
        return None

    return Spacing(
        inset_left=body_number("lIns"),
        inset_top=body_number("tIns"),
        inset_right=body_number("rIns"),
        inset_bottom=body_number("bIns"),
        paragraph_before=paragraph_spacing("spcBef"),
        paragraph_after=paragraph_spacing("spcAft"),
        line_spacing=paragraph_spacing("lnSpc"),
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


def _placeholder_key(element: ET.Element) -> tuple[str, str] | None:
    value = element.find(".//p:nvPr/p:ph", _NS)
    if value is None:
        return None
    return (value.get("type", "body").casefold(), value.get("idx", "0"))


def _placeholder_fallbacks(
    element: ET.Element, roots: tuple[ET.Element, ...]
) -> tuple[ET.Element, ...]:
    key = _placeholder_key(element)
    if key is None:
        return ()
    exact: list[ET.Element] = []
    same_type: list[ET.Element] = []
    for root in roots:
        for candidate in _shape_nodes(root):
            candidate_key = _placeholder_key(candidate)
            if candidate_key == key:
                exact.append(candidate)
            elif candidate_key is not None and candidate_key[0] == key[0]:
                same_type.append(candidate)
    return tuple(exact or same_type)


def _accepted_kinds(kind: str, placeholder: str | None) -> tuple[str, ...]:
    normalized = (placeholder or "").casefold()
    if normalized in _IMAGE_PLACEHOLDERS:
        return ("image",)
    if normalized == "chart":
        return ("chart",)
    if normalized == "tbl":
        return ("table",)
    if normalized == "obj":
        return ("text", "image", "table", "chart")
    if normalized in _TEXT_PLACEHOLDERS:
        return ("text",)
    if kind in {"text", "image", "table", "chart"}:
        return (kind,)
    return ()


def _semantic_role(
    kind: str,
    placeholder: str | None,
    name: str,
    *,
    text: str = "",
) -> str:
    normalized = f"{placeholder or ''} {name} {text}".casefold()
    placeholder_normalized = (placeholder or "").casefold()
    if placeholder_normalized in {"title", "ctrtitle", "verttitle"} or re.search(
        r"\b(title|headline|heading)\b", normalized
    ):
        return "title"
    if placeholder_normalized == "subtitle" or re.search(
        r"\b(subtitle|subheading|tagline)\b", normalized
    ):
        return "subtitle"
    if placeholder_normalized in _IMAGE_PLACEHOLDERS or kind == "image":
        return "visual"
    if placeholder_normalized in {"chart", "tbl"} or kind in {"table", "chart"}:
        return "data"
    if any(token in normalized for token in _ROLE_HINTS["data"]):
        return "data"
    return "body"


def _physical_font_size(style: TextStyle | None, unit: str) -> float:
    font_pt = style.font_size if style is not None and style.font_size else 18.0
    return font_pt * (12700.0 if unit == "emu" else 96.0 / 72.0)


def _inner_dimensions(bounds: Rect, spacing: Spacing | None) -> tuple[float, float]:
    if spacing is None:
        return bounds.width, bounds.height
    return (
        max(1.0, bounds.width - spacing.inset_left - spacing.inset_right),
        max(1.0, bounds.height - spacing.inset_top - spacing.inset_bottom),
    )


def _capacity(
    bounds: Rect,
    style: TextStyle | None,
    kinds: tuple[str, ...],
    *,
    unit: str,
    spacing: Spacing | None = None,
) -> tuple[int, str, tuple[SlotConstraint, ...]]:
    base = [
        SlotConstraint(
            constraint_id="constraint:source-geometry",
            kind="source-geometry",
            value=True,
        )
    ]
    if "text" not in kinds:
        if "image" in kinds:
            base.append(
                SlotConstraint(
                    constraint_id="constraint:aspect-ratio",
                    kind="aspect-ratio",
                    value=round(bounds.width / bounds.height, 6),
                )
            )
        return 1, "medium", tuple(base)
    font_pt = style.font_size if style is not None and style.font_size else 18.0
    font_size = _physical_font_size(style, unit)
    inner_width, inner_height = _inner_dimensions(bounds, spacing)
    line_multiplier = (
        spacing.line_spacing
        if spacing is not None and spacing.line_spacing is not None and spacing.line_spacing <= 4
        else 1.25
    )
    approximate_lines = max(1, int(inner_height / max(1.0, font_size * line_multiplier)))
    approximate_columns = max(4, int(inner_width / max(1.0, font_size * 0.55)))
    characters = max(8, min(10000, approximate_lines * approximate_columns))
    density = "low" if characters < 120 else "high" if characters > 500 else "medium"
    return characters, density, tuple(base) + (
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


def _capacity_envelope(
    bounds: Rect,
    style: TextStyle | None,
    kinds: tuple[str, ...],
    *,
    unit: str,
    spacing: Spacing | None = None,
    measurement_basis: str = "source-geometry",
    element: ET.Element | None = None,
) -> CapacityEnvelope:
    capacity, _, constraints = _capacity(
        bounds, style, kinds, unit=unit, spacing=spacing
    )
    maximum_lines = next(
        (
            int(constraint.value)
            for constraint in constraints
            if constraint.kind == "maximum-lines"
        ),
        None,
    )
    rows = columns = numeric_items = None
    if element is not None and "table" in kinds:
        rows = len(element.findall(".//a:tr", _NS)) or None
        columns = len(element.findall(".//a:tblGrid/a:gridCol", _NS)) or None
    if element is not None and "chart" in kinds:
        numeric_items = len(
            element.findall(
                ".//{http://schemas.openxmlformats.org/drawingml/2006/chart}pt"
            )
        ) or None
    inner_width, inner_height = _inner_dimensions(bounds, spacing)
    physical_font = _physical_font_size(style, unit)
    if "table" in kinds:
        rows = rows or max(1, min(1000, int(inner_height / max(1.0, physical_font * 1.65))))
        columns = columns or max(1, min(100, int(inner_width / max(1.0, physical_font * 7))))
    if "chart" in kinds:
        numeric_items = numeric_items or max(
            1,
            min(
                10000,
                int((inner_width * inner_height) / max(1.0, physical_font**2 * 8)),
            ),
        )
    ratio = bounds.width / bounds.height if "image" in kinds else None
    return CapacityEnvelope(
        measurement_basis=measurement_basis,
        content_area=bounds.width * bounds.height,
        max_characters=capacity if "text" in kinds else None,
        max_lines=maximum_lines,
        max_table_rows=rows,
        max_table_columns=columns,
        max_numeric_items=numeric_items,
        max_images=1 if "image" in kinds else None,
        aspect_ratio_min=ratio,
        aspect_ratio_max=ratio,
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


def _slots(
    root: ET.Element,
    part: str,
    canvas: Canvas,
    inherited_roots: tuple[ET.Element, ...] = (),
) -> tuple[EditableSlot, ...]:
    result: list[EditableSlot] = []
    for ordinal, element in enumerate(_shape_nodes(root), start=1):
        kind = _element_kind(element)
        placeholder = _placeholder(element)
        if placeholder is not None and placeholder.casefold() in _NON_CONTENT_PLACEHOLDERS:
            continue
        accepted_kinds = _accepted_kinds(kind, placeholder)
        if not accepted_kinds:
            continue
        # A text/image shape without an OOXML placeholder is authored content,
        # not evidence that the shape is a template variable.  Tables and charts
        # are exact typed objects and can be patched through their shape handle.
        if kind in {"text", "image"} and placeholder is None:
            continue
        fallbacks = _placeholder_fallbacks(element, inherited_roots)
        bounds = _bounds(element, fallbacks)
        if bounds is None:
            continue
        shape_id = _shape_id(element, ordinal)
        style = _text_style(element, fallbacks) if "text" in accepted_kinds else None
        spacing = _spacing(element, fallbacks)
        capacity, density, constraints = _capacity(
            bounds,
            style,
            accepted_kinds,
            unit=canvas.unit,
            spacing=spacing,
        )
        selector = f"pptx:{part}#shape-{shape_id}"
        if placeholder:
            selector += f"[placeholder={placeholder}]"
        source_digest = _digest_bytes(ET.tostring(element, encoding="utf-8"))
        semantic_role = _semantic_role(
            kind,
            placeholder,
            _shape_name(element),
            text=" ".join(
                node.text or "" for node in element.findall(".//a:t", _NS)
            ),
        )
        required = semantic_role == "title"
        result.append(
            EditableSlot(
                slot_id=f"slot:{PurePosixPath(part).stem}-{shape_id}",
                semantic_role=semantic_role,
                selector=selector,
                bounds=bounds,
                accepted_kinds=accepted_kinds,
                source_shape_id=shape_id,
                source_xml_digest=source_digest,
                text_style=style,
                spacing=spacing,
                paint=_paint(element, fallbacks),
                capacity_units=capacity,
                density=density,
                constraints=constraints,
                required=required,
                capacity_envelope=_capacity_envelope(
                    bounds,
                    style,
                    accepted_kinds,
                    unit=canvas.unit,
                    spacing=spacing,
                    element=element,
                ),
                patch_handle=PatchHandle(
                    kind="pptx-shape",
                    source_part=part,
                    element_id=shape_id,
                    selector=selector,
                    source_digest=source_digest,
                ),
                container_id=f"pptx:{part}",
                hide_policy="never" if required else "when-optional-empty",
                fallback_policy="reject-frame",
                extraction_confidence=1.0,
            )
        )
    return tuple(result)


def _slide_chrome(
    root: ET.Element, part: str, slots: tuple[EditableSlot, ...]
) -> tuple[NativeElement, ...]:
    slot_shape_ids = {slot.source_shape_id for slot in slots}
    result: list[NativeElement] = []
    for ordinal, element in enumerate(_shape_nodes(root), start=1):
        if _shape_id(element, ordinal) in slot_shape_ids:
            continue
        native = _native_element(element, part, ordinal, editable=False)
        if native is not None:
            result.append(native)
    return tuple(result)


def _chrome(archive: ZipFile, parts: tuple[str | None, ...]) -> tuple[NativeElement, ...]:
    result: list[NativeElement] = []
    for part in parts:
        if part is None or part not in archive.namelist():
            continue
        root, _ = _xml(archive, part)
        for ordinal, element in enumerate(_shape_nodes(root), start=1):
            # Layout/master placeholders provide inherited slot defaults.  They are
            # not protected artwork and must not be duplicated as frame chrome.
            if _placeholder(element) is not None:
                continue
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


def _role(
    index: int,
    total: int,
    slots: tuple[EditableSlot, ...],
    layout_hint: str | None,
) -> str:
    roles = {slot.semantic_role for slot in slots}
    kinds = {kind for slot in slots for kind in slot.accepted_kinds}
    normalized_hint = (layout_hint or "").casefold()
    for role in ("timeline", "architecture", "process"):
        if any(token in normalized_hint for token in _ROLE_HINTS[role]):
            return role
    if kinds & {"table", "chart"}:
        return "data"
    if any(token in normalized_hint for token in _ROLE_HINTS["data"]):
        return "data"
    title_only = bool(slots) and roles <= {"title", "subtitle"} and len(slots) <= 3
    if title_only:
        if any(token in normalized_hint for token in _ROLE_HINTS["closing"]):
            return "closing"
        if any(token in normalized_hint for token in _ROLE_HINTS["cover"]):
            return "cover"
        if index == 0:
            return "cover"
        if index == total - 1:
            return "closing"
        return "section"
    if any(token in normalized_hint for token in _ROLE_HINTS["section"]):
        return "section"
    return "content"


def _frame_density(slots: tuple[EditableSlot, ...], canvas: Canvas) -> str:
    occupied = sum(slot.bounds.width * slot.bounds.height for slot in slots)
    share = min(1.0, occupied / (canvas.width * canvas.height))
    complexity = len(slots) + sum(
        1 for slot in slots if set(slot.accepted_kinds) & {"table", "chart", "image"}
    )
    if share < 0.25 and complexity <= 2:
        return "low"
    if share > 0.65 or complexity >= 6:
        return "high"
    return "medium"


def _layout_pattern(slots: tuple[EditableSlot, ...]) -> str:
    content = tuple(
        slot for slot in slots if slot.semantic_role not in {"title", "subtitle"}
    )
    if not slots:
        return "none"
    if not content:
        return "title-only"
    if len(content) == 1:
        return "single-region"
    if len(content) >= 4:
        return "grid"
    centers_x = [slot.bounds.x + slot.bounds.width / 2 for slot in content]
    centers_y = [slot.bounds.y + slot.bounds.height / 2 for slot in content]
    x_span = max(centers_x) - min(centers_x)
    y_span = max(centers_y) - min(centers_y)
    return "horizontal-split" if x_span >= y_span else "vertical-stack"


def _frame_capabilities(
    slots: tuple[EditableSlot, ...], canvas: Canvas
) -> FrameCapabilities:
    kinds = tuple(
        kind
        for kind in ("text", "image", "table", "chart")
        if any(kind in slot.accepted_kinds for slot in slots)
    )
    envelopes = tuple(
        slot.capacity_envelope
        for slot in slots
        if slot.capacity_envelope is not None
    )

    def total(field: str) -> int | None:
        values = [getattr(envelope, field) for envelope in envelopes]
        present = [value for value in values if value is not None]
        return sum(present) if present else None

    occupied = sum(slot.bounds.width * slot.bounds.height for slot in slots)
    non_title = tuple(
        slot for slot in slots if slot.semantic_role not in {"title", "subtitle"}
    )
    role_counts = Counter(slot.semantic_role for slot in slots)
    return FrameCapabilities(
        semantic_roles=tuple(dict.fromkeys(slot.semantic_role for slot in slots)),
        accepted_kinds=kinds,
        slot_count=len(slots),
        text_slot_count=sum("text" in slot.accepted_kinds for slot in slots),
        image_slot_count=sum("image" in slot.accepted_kinds for slot in slots),
        data_slot_count=sum(
            bool(set(slot.accepted_kinds) & {"table", "chart"}) for slot in slots
        ),
        max_text_characters=total("max_characters"),
        max_text_lines=total("max_lines"),
        max_images=total("max_images"),
        max_table_rows=total("max_table_rows"),
        max_table_columns=total("max_table_columns"),
        max_numeric_items=total("max_numeric_items"),
        content_area_share=min(1.0, occupied / (canvas.width * canvas.height)),
        layout_pattern=_layout_pattern(slots),
        supports_mixed_content=len(kinds) > 1,
        supports_comparison=(
            len(non_title) >= 2
            and (
                max(role_counts.values(), default=0) >= 2
                or _layout_pattern(slots) in {"horizontal-split", "grid"}
            )
        ),
    )


def _layout_hint(root: ET.Element | None, part: str | None) -> str:
    if root is None:
        return part or ""
    c_slide = root.find("p:cSld", _NS)
    values = [part or "", *root.attrib.values()]
    if c_slide is not None:
        values.extend(c_slide.attrib.values())
    return " ".join(str(value) for value in values if value)


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
            layout_root = None
            master_root = None
            if layout_part is not None:
                layout_root, _ = _xml(archive, layout_part)
                layout_rels = _relationships(archive, layout_part)
                master_part = next(
                    (
                        target
                        for target, relation_type in layout_rels.values()
                        if relation_type.endswith("/slideMaster")
                    ),
                    None,
                )
                if master_part is not None:
                    master_root, _ = _xml(archive, master_part)
            inherited_roots = tuple(
                root for root in (layout_root, master_root) if root is not None
            )
            slots = _slots(slide, slide_part, canvas, inherited_roots)
            frame = DesignFrame(
                frame_id=f"frame:slide-{index + 1}",
                source_index=index,
                role=_role(
                    index,
                    len(slide_parts),
                    slots,
                    _layout_hint(layout_root, layout_part),
                ),
                frame_type="pptx-native",
                source_part=slide_part,
                layout_part=layout_part,
                master_part=master_part,
                source_digest=_digest_bytes(slide_bytes),
                background=Paint(fill=_color(slide.find("p:cSld/p:bg", _NS))),
                density=_frame_density(slots, canvas),
                capabilities=_frame_capabilities(slots, canvas),
                slots=slots,
                chrome=_slide_chrome(slide, slide_part, slots)
                + _chrome(archive, (master_part, layout_part)),
                unsupported=_unsupported(slide, slide_part),
                constraints=(
                    SlotConstraint(
                        constraint_id="constraint:inside-canvas",
                        kind="inside-canvas",
                        value=True,
                    ),
                ),
                representative_render_ref=f"representative/slide-{index + 1}.svg",
                editability="source-native" if slots else "fidelity-only",
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
            content = structural_svg(frame, canvas)
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


def _authored_contract(
    model: type[TextStyle] | type[Spacing] | type[Paint],
    value: object,
    error_code: str,
) -> TextStyle | Spacing | Paint | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DesignPackCompileError(error_code)
    try:
        return model.model_validate(value)
    except ValueError as error:
        raise DesignPackCompileError(error_code) from error


def _authored_accepted_kinds(kind: str, value: object) -> tuple[str, ...]:
    raw = value if isinstance(value, (list, tuple)) else (kind,)
    accepted = tuple(
        candidate
        for candidate in ("text", "image", "table", "chart")
        if candidate in raw
    )
    if not accepted or len(accepted) != len(set(str(item) for item in raw)):
        raise DesignPackCompileError("design_pack_authored_accepted_kinds_invalid")
    return accepted


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
    font_counts: Counter[str] = Counter()
    color_counts: Counter[str] = Counter()
    line_counts: Counter[str] = Counter()
    spacing_counts: Counter[float] = Counter()
    for index, frame_value in enumerate(frames_value):
        if not isinstance(frame_value, Mapping):
            raise DesignPackCompileError("design_pack_authored_frame_invalid")
        frame_id = str(frame_value.get("id") or f"frame-{index + 1}")
        frame_type = str(frame_value.get("type") or "scene")
        if frame_type not in {"html", "scene"}:
            raise DesignPackCompileError("design_pack_authored_frame_type_invalid")
        explicit_role = frame_value.get("role")
        if explicit_role is not None and str(explicit_role) not in {"cover", "section", "content", "data", "timeline", "architecture", "process", "closing"}:
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
            style = _authored_contract(
                TextStyle,
                element_value.get("textStyle"),
                "design_pack_authored_text_style_invalid",
            )
            spacing = _authored_contract(
                Spacing,
                element_value.get("spacing"),
                "design_pack_authored_spacing_invalid",
            )
            paint = _authored_contract(
                Paint,
                element_value.get("paint"),
                "design_pack_authored_paint_invalid",
            )
            editable = (
                element_value.get("editable") is True
                and element_value.get("slotApproved") is True
            )
            selector = f"{frame_type}:{frame_id}#{element_id}"
            element_bytes = _canonical_bytes(element_value)
            if editable and kind in {"text", "image", "table", "chart"} and bounds is not None:
                accepted_kinds = _authored_accepted_kinds(
                    kind, element_value.get("acceptedKinds")
                )
                capacity, density, constraints = _capacity(
                    bounds,
                    style if isinstance(style, TextStyle) else None,
                    accepted_kinds,
                    unit=canvas.unit,
                    spacing=spacing if isinstance(spacing, Spacing) else None,
                )
                semantic_role = str(element_value.get("semanticRole") or "").strip()
                slot_id = str(element_value.get("slotId") or "").strip()
                confidence_value = element_value.get("extractionConfidence")
                if not semantic_role:
                    semantic_role = _semantic_role(
                        kind,
                        None,
                        element_id,
                        text=str(element_value.get("text") or ""),
                    )
                if not slot_id:
                    slot_id = f"slot:{frame_id}-{element_id}"
                try:
                    confidence = (
                        float(confidence_value)
                        if confidence_value is not None
                        else 1.0
                    )
                except (TypeError, ValueError):
                    confidence = 0.0
                if confidence < 0.95:
                    raise DesignPackCompileError(
                        "design_pack_authored_slot_approval_invalid"
                    )
                source_digest = _digest_bytes(element_bytes)
                envelope_value = element_value.get("capacityEnvelope")
                try:
                    envelope = (
                        CapacityEnvelope.model_validate(envelope_value)
                        if isinstance(envelope_value, Mapping)
                        else _capacity_envelope(
                            bounds,
                            style if isinstance(style, TextStyle) else None,
                            accepted_kinds,
                            unit=canvas.unit,
                            spacing=spacing if isinstance(spacing, Spacing) else None,
                        )
                    )
                except ValueError as error:
                    raise DesignPackCompileError(
                        "design_pack_authored_capacity_invalid"
                    ) from error
                required = (
                    element_value.get("required") is True
                    or semantic_role == "title"
                )
                slots.append(
                    EditableSlot(
                        slot_id=slot_id,
                        semantic_role=semantic_role,
                        selector=selector,
                        bounds=bounds,
                        accepted_kinds=accepted_kinds,
                        source_shape_id=element_id,
                        source_xml_digest=source_digest,
                        text_style=style if isinstance(style, TextStyle) else None,
                        spacing=spacing if isinstance(spacing, Spacing) else None,
                        paint=paint if isinstance(paint, Paint) else None,
                        capacity_units=capacity,
                        density=density,
                        constraints=constraints,
                        required=required,
                        capacity_envelope=envelope,
                        patch_handle=PatchHandle(
                            kind=(
                                "authored-qid"
                                if frame_type == "html"
                                else "scene-element"
                            ),
                            source_part=f"authored/{frame_id}",
                            element_id=element_id,
                            selector=selector,
                            source_digest=source_digest,
                        ),
                        container_id=str(
                            element_value.get("containerId")
                            or f"{frame_type}:{frame_id}"
                        ),
                        hide_policy=(
                            "never" if required else "when-optional-empty"
                        ),
                        fallback_policy="reject-frame",
                        extraction_confidence=confidence,
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
                        text_style=style if isinstance(style, TextStyle) else None,
                        spacing=spacing if isinstance(spacing, Spacing) else None,
                        paint=paint if isinstance(paint, Paint) else None,
                        container_id=str(
                            element_value.get("containerId")
                            or f"{frame_type}:{frame_id}"
                        ),
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
        slot_tuple = tuple(slots)
        role = (
            str(explicit_role)
            if explicit_role is not None
            else _role(
                index,
                len(frames_value),
                slot_tuple,
                f"{frame_id} {frame_type}",
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
                density=_frame_density(slot_tuple, canvas),
                capabilities=_frame_capabilities(slot_tuple, canvas),
                slots=slot_tuple,
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
                editability="source-native" if slots else "fidelity-only",
            )
        )
        for slot in slot_tuple:
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
    if not frames or len(frames) > _MAX_SLIDES:
        raise DesignPackCompileError("design_pack_frame_count_invalid")
    assets: dict[str, bytes] = {}
    render_refs: list[RepresentativeRenderRef] = []
    for frame in frames:
        relative = frame.representative_render_ref
        assert relative is not None
        content = structural_svg(frame, canvas)
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
