"""Run-level typography and placeholder inheritance for PPTX source text.

``pptx.py`` reads shape geometry and text; this module resolves the font
properties the manifest used to discard (size, weight, style, color) by
walking the DrawingML inheritance chain in this order: the run ``a:rPr``,
the paragraph ``a:pPr/a:defRPr``, the shape's ``a:lstStyle`` entry for the
paragraph level, the paragraph ``a:endParaRPr``, the matching slide-layout
placeholder, the matching slide-master placeholder, the master
``p:txStyles`` family, and ``p:defaultTextStyle`` of the presentation.
Everything is deterministic and network-free; each part is parsed once.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
import re
from typing import Any
from zipfile import ZipFile

from lxml import etree

from services.source_content.security import secure_xml

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS = {"p": P_NS, "a": A_NS}

PRESENTATION_PART = "ppt/presentation.xml"

#: ``{"sizePt": float | None, "bold": bool, "italic": bool, "color": str | None}``
RunStyle = dict[str, Any]

#: ``(package, part_name) -> {rel_id: (target_part, rel_type)}``
RelationshipMap = Callable[[ZipFile, str], dict[str, tuple[str, str]]]

#: ECMA-376 defaults for a ``p:ph`` that omits the attribute.
DEFAULT_PLACEHOLDER_TYPE = "obj"
DEFAULT_PLACEHOLDER_IDX = 0
MAX_LIST_LEVEL = 8

_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")
_SOLID_FILL_TAG = f"{{{A_NS}}}solidFill"
_FILL_TAGS = frozenset(
    f"{{{A_NS}}}{name}" for name in ("solidFill", "gradFill", "pattFill", "blipFill", "noFill")
)

#: A layout placeholder inherits from the master placeholder of this type.
_MASTER_BASE_TYPE: dict[str, str] = {
    **{name: "title" for name in ("title", "ctrTitle")},
    **{
        name: "body"
        for name in (
            "body", "subTitle", "obj", "chart", "tbl", "clipArt", "dgm", "media", "sldImg", "pic",
        )
    },
    **{name: name for name in ("dt", "ftr", "sldNum", "hdr")},
}
_TX_STYLE_FAMILY: dict[str, str] = {"title": "titleStyle", "body": "bodyStyle"}


def _int_attr(element: etree._Element, name: str, default: int) -> int:
    try:
        return int(element.get(name, default))
    except (TypeError, ValueError):
        return default


def _flag(value: str | None) -> bool | None:
    if value in {"1", "true"}:
        return True
    if value in {"0", "false"}:
        return False
    return None


def _size_pt(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        hundredths = int(value)
    except ValueError:
        return None
    return hundredths / 100.0 if hundredths > 0 else None


def _fill_color(properties: etree._Element) -> tuple[bool, str | None]:
    """``(fill defined, "#RRGGBB" when it is a literal sRGB solid fill)``."""

    for child in properties:
        if child.tag not in _FILL_TAGS:
            continue
        if child.tag != _SOLID_FILL_TAG:
            return True, None
        srgb = child.find("a:srgbClr", namespaces=NS)
        value = srgb.get("val", "") if srgb is not None else ""
        return True, (f"#{value.upper()}" if _HEX_RE.match(value) else None)
    return False, None


def resolve_run_style(chain: Iterable[etree._Element | None]) -> RunStyle:
    """First defined value wins per property, walking ``chain`` outwards.

    Scheme, gradient and pattern fills define the color without a literal
    hex value, so they stop the walk and yield ``None``.
    """

    size: float | None = None
    bold: bool | None = None
    italic: bool | None = None
    color: str | None = None
    color_defined = False
    for properties in chain:
        if properties is None:
            continue
        if size is None:
            size = _size_pt(properties.get("sz"))
        if bold is None:
            bold = _flag(properties.get("b"))
        if italic is None:
            italic = _flag(properties.get("i"))
        if not color_defined:
            color_defined, color = _fill_color(properties)
    return {"sizePt": size, "bold": bool(bold), "italic": bool(italic), "color": color}


@dataclass(frozen=True, slots=True)
class Placeholder:
    """Effective ``p:ph`` identity with the schema defaults applied."""

    type: str
    idx: int

    @property
    def master_type(self) -> str:
        return _MASTER_BASE_TYPE.get(self.type, "body")


def _placeholder_from(ph: etree._Element) -> Placeholder:
    return Placeholder(
        type=ph.get("type") or DEFAULT_PLACEHOLDER_TYPE,
        idx=_int_attr(ph, "idx", DEFAULT_PLACEHOLDER_IDX),
    )


def placeholder_of(shape: etree._Element) -> Placeholder | None:
    ph = shape.find(".//p:nvPr/p:ph", namespaces=NS)
    return None if ph is None else _placeholder_from(ph)


def paragraph_level(paragraph: etree._Element) -> int:
    properties = paragraph.find("a:pPr", namespaces=NS)
    if properties is None:
        return 0
    return min(max(_int_attr(properties, "lvl", 0), 0), MAX_LIST_LEVEL)


def _level_properties(
    list_style: etree._Element | None, level: int
) -> Iterator[etree._Element]:
    """``a:lvlNpPr/a:defRPr`` of a ``CT_TextListStyle``, then its ``a:defPPr``."""

    if list_style is None:
        return
    for path in (f"a:lvl{level + 1}pPr/a:defRPr", "a:defPPr/a:defRPr"):
        found = list_style.find(path, namespaces=NS)
        if found is not None:
            yield found


@dataclass(frozen=True, slots=True)
class ShapeTextContext:
    """Inherited text properties of one shape, resolved once per shape."""

    placeholder: Placeholder | None = None
    #: The shape's own ``a:lstStyle``; consulted before ``a:endParaRPr``.
    list_style: etree._Element | None = None
    #: Layout placeholder, master placeholder, master ``p:txStyles`` family and
    #: ``p:defaultTextStyle`` list styles, nearest first.
    inherited: tuple[etree._Element, ...] = ()

    def _paragraph_chain(self, paragraph: etree._Element) -> Iterator[etree._Element | None]:
        level = paragraph_level(paragraph)
        properties = paragraph.find("a:pPr", namespaces=NS)
        yield properties.find("a:defRPr", namespaces=NS) if properties is not None else None
        yield from _level_properties(self.list_style, level)
        yield paragraph.find("a:endParaRPr", namespaces=NS)
        for list_style in self.inherited:
            yield from _level_properties(list_style, level)

    def run_style(self, run: etree._Element, paragraph: etree._Element) -> RunStyle:
        """Style of one ``a:r``, ``a:fld`` or ``a:br`` child of ``paragraph``."""
        return resolve_run_style(
            [run.find("a:rPr", namespaces=NS), *self._paragraph_chain(paragraph)]
        )

    def mark_style(self, paragraph: etree._Element) -> RunStyle:
        """Style of the paragraph mark: ``a:endParaRPr`` first, then the chain."""
        return resolve_run_style(
            [paragraph.find("a:endParaRPr", namespaces=NS), *self._paragraph_chain(paragraph)]
        )


def _placeholder_shapes(
    root: etree._Element | None,
) -> list[tuple[Placeholder, etree._Element]]:
    if root is None:
        return []
    result: list[tuple[Placeholder, etree._Element]] = []
    for ph in root.iter(f"{{{P_NS}}}ph"):
        nv_pr = ph.getparent()
        nv_shape_pr = nv_pr.getparent() if nv_pr is not None else None
        shape = nv_shape_pr.getparent() if nv_shape_pr is not None else None
        if shape is not None:
            result.append((_placeholder_from(ph), shape))
    return result


def _layout_placeholder(
    root: etree._Element | None, placeholder: Placeholder
) -> etree._Element | None:
    """Slide to layout: PowerPoint matches by ``idx``; fall back to the type."""

    candidates = _placeholder_shapes(root)
    by_idx = [(ph, shape) for ph, shape in candidates if ph.idx == placeholder.idx]
    for ph, shape in by_idx:
        if ph.type == placeholder.type:
            return shape
    if by_idx:
        return by_idx[0][1]
    for ph, shape in candidates:
        if ph.type == placeholder.type:
            return shape
    return None


def _master_placeholder(
    root: etree._Element | None, master_type: str
) -> etree._Element | None:
    for ph, shape in _placeholder_shapes(root):
        if ph.master_type == master_type:
            return shape
    return None


def _list_style_of(shape: etree._Element | None) -> etree._Element | None:
    if shape is None:
        return None
    return shape.find("p:txBody/a:lstStyle", namespaces=NS)


class PptxTypographyResolver:
    """Resolves shape text contexts against the package's layouts and masters.

    Parts are parsed once per package with ``secure_xml``; relationship
    traversal reuses the extractor's admitted relationship map.
    """

    def __init__(self, package: ZipFile, *, relationship_map: RelationshipMap) -> None:
        self._package = package
        self._relationship_map = relationship_map
        self._names = frozenset(package.namelist())
        self._roots: dict[str, etree._Element | None] = {}
        self._relationships: dict[str, dict[str, tuple[str, str]]] = {}

    def _root(self, part_name: str | None) -> etree._Element | None:
        if part_name is None:
            return None
        if part_name not in self._roots:
            self._roots[part_name] = (
                secure_xml(self._package.read(part_name), part_name=part_name)
                if part_name in self._names
                else None
            )
        return self._roots[part_name]

    def _related_part(self, part_name: str | None, suffix: str) -> str | None:
        if part_name is None:
            return None
        if part_name not in self._relationships:
            self._relationships[part_name] = self._relationship_map(self._package, part_name)
        relationships = self._relationships[part_name]
        for rel_id in sorted(relationships):
            target, rel_type = relationships[rel_id]
            if rel_type.endswith(suffix):
                return target
        return None

    def slide_size(self) -> dict[str, int] | None:
        """``p:sldSz`` of the presentation in EMU, or ``None`` when absent."""
        root = self._root(PRESENTATION_PART)
        size = root.find("p:sldSz", namespaces=NS) if root is not None else None
        if size is None:
            return None
        cx, cy = _int_attr(size, "cx", 0), _int_attr(size, "cy", 0)
        return {"cx": cx, "cy": cy} if cx > 0 and cy > 0 else None

    def shape_context(self, shape: etree._Element, *, part_name: str) -> ShapeTextContext:
        """Resolve the inheritance chain of one ``p:sp`` on ``part_name``.

        Notes parts relate to a notes master rather than a slide layout, so
        their shapes inherit only from ``p:defaultTextStyle``.
        """
        placeholder = placeholder_of(shape)
        layout_part = self._related_part(part_name, "/slideLayout")
        master_root = self._root(self._related_part(layout_part, "/slideMaster"))
        inherited: list[etree._Element] = []
        master_type: str | None = None
        if placeholder is not None:
            layout_shape = _layout_placeholder(self._root(layout_part), placeholder)
            layout_placeholder = placeholder_of(layout_shape) if layout_shape is not None else None
            master_type = (layout_placeholder or placeholder).master_type
            for candidate in (layout_shape, _master_placeholder(master_root, master_type)):
                list_style = _list_style_of(candidate)
                if list_style is not None:
                    inherited.append(list_style)
        if master_root is not None:
            family = _TX_STYLE_FAMILY.get(master_type or "", "otherStyle")
            tx_style = master_root.find(f"p:txStyles/p:{family}", namespaces=NS)
            if tx_style is not None:
                inherited.append(tx_style)
        presentation = self._root(PRESENTATION_PART)
        default_style = (
            presentation.find("p:defaultTextStyle", namespaces=NS)
            if presentation is not None
            else None
        )
        if default_style is not None:
            inherited.append(default_style)
        return ShapeTextContext(
            placeholder=placeholder,
            list_style=_list_style_of(shape),
            inherited=tuple(inherited),
        )


__all__ = [
    "A_NS",
    "NS",
    "P_NS",
    "PRESENTATION_PART",
    "Placeholder",
    "PptxTypographyResolver",
    "RunStyle",
    "ShapeTextContext",
    "paragraph_level",
    "placeholder_of",
    "resolve_run_style",
]
