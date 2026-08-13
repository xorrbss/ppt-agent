"""Editable text/table patching and deterministic ZIP serialization for native clone."""

from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
import re
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from templates.v2.models.elements import (
    Chart,
    Container,
    Flex,
    Grid,
    Group,
    Image,
    Table,
    Text,
    TextList,
)
from templates.v2.models.layouts import SlideLayout
from templates.v2.pptx.native_compiler import TemplateV2NativePptxCompileError


_FIXED_PACKAGE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
ET.register_namespace("p", _P_NS)
ET.register_namespace("a", _A_NS)
ET.register_namespace("ct", _CT_NS)



def _patch_slide(
    source_xml: bytes,
    *,
    layout: SlideLayout,
    content: Mapping[str, Any],
    slide_index: int,
) -> tuple[bytes, list[str]]:
    try:
        root = ET.fromstring(source_xml)
    except ET.ParseError as error:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_xml_invalid",
            path=f"slides.{slide_index}.source_part",
        ) from error
    shape_map: dict[str, list[ET.Element]] = {}
    for shape in root.iter():
        if shape.tag not in {
            f"{{{_P_NS}}}sp",
            f"{{{_P_NS}}}pic",
            f"{{{_P_NS}}}graphicFrame",
            f"{{{_P_NS}}}grpSp",
        }:
            continue
        name = _shape_name(shape)
        if name:
            shape_map.setdefault(_normalize_slot_name(name), []).append(shape)

    patched: list[str] = []
    for component_index, component in enumerate(layout.components):
        scope = content.get(component.id)
        if not isinstance(scope, Mapping):
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_pptx_content_scope_invalid",
                path=f"slides.{slide_index}.components.{component_index}.content",
            )
        for element_index, element in enumerate(component.elements):
            _patch_element(
                element,
                scope=scope,
                shape_map=shape_map,
                patched=patched,
                path=f"slides.{slide_index}.components.{component_index}.elements.{element_index}",
            )
    return _serialize_xml(root), patched


def _patch_element(
    element: Any,
    *,
    scope: Mapping[str, Any],
    shape_map: Mapping[str, list[ET.Element]],
    patched: list[str],
    path: str,
) -> None:
    if isinstance(element, Container):
        if element.child is not None:
            _patch_element(
                element.child,
                scope=scope,
                shape_map=shape_map,
                patched=patched,
                path=f"{path}.child",
            )
        return
    if isinstance(element, Group):
        nested = scope.get(element.name, scope)
        if not isinstance(nested, Mapping):
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_pptx_content_scope_invalid",
                path=f"{path}.content.{element.name}",
            )
        for index, child in enumerate(element.children):
            _patch_element(
                child,
                scope=nested,
                shape_map=shape_map,
                patched=patched,
                path=f"{path}.children.{index}",
            )
        return
    if isinstance(element, Text):
        if element.decorative:
            return
        value = scope.get(element.name)
        if not isinstance(value, str):
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_pptx_content_value_invalid",
                path=f"{path}.content.{element.name}",
            )
        shape = _unique_shape(shape_map, element.name, path)
        _replace_text(shape, value, path)
        patched.append(element.name)
        return
    if isinstance(element, Table):
        if element.decorative:
            return
        value = scope.get(element.name)
        if not isinstance(value, Mapping):
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_pptx_content_value_invalid",
                path=f"{path}.content.{element.name}",
            )
        columns = value.get("columns")
        rows = value.get("rows")
        if (
            not isinstance(columns, list)
            or not all(isinstance(item, str) for item in columns)
            or not isinstance(rows, list)
            or not all(
                isinstance(row, list) and all(isinstance(item, str) for item in row)
                for row in rows
            )
        ):
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_pptx_content_value_invalid",
                path=f"{path}.content.{element.name}",
            )
        shape = _unique_shape(shape_map, element.name, path)
        _replace_table(shape, [columns, *rows], path)
        patched.append(element.name)
        return
    if isinstance(element, (Flex, Grid)):
        values = scope.get(element.name)
        if isinstance(values, Mapping):
            for index, child in enumerate(element.children):
                _patch_element(
                    child,
                    scope=values,
                    shape_map=shape_map,
                    patched=patched,
                    path=f"{path}.children.{index}",
                )
            return
        if not isinstance(values, list) or len(values) != len(element.children):
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_clone_repeat_scope_invalid",
                path=f"{path}.content.{element.name}",
            )
        for index, (child, item_scope) in enumerate(
            zip(element.children, values, strict=True)
        ):
            if not isinstance(item_scope, Mapping):
                raise TemplateV2NativePptxCompileError(
                    "template_v2_native_clone_repeat_scope_invalid",
                    path=f"{path}.content.{element.name}.{index}",
                )
            _patch_element(
                child,
                scope=item_scope,
                shape_map=shape_map,
                patched=patched,
                path=f"{path}.children.{index}",
            )
        return
    if isinstance(element, TextList):
        if element.decorative:
            return
        value = scope.get(element.name)
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_pptx_content_value_invalid",
                path=f"{path}.content.{element.name}",
            )
        shape = _unique_shape(shape_map, element.name, path)
        _replace_text_list(shape, value, path)
        patched.append(element.name)
        return
    if isinstance(element, Image) and not element.decorative:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_image_relationship_replacement_unsupported",
            path=f"{path}.type=image",
        )
    if isinstance(element, Chart) and not element.decorative:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_chart_relationship_replacement_unsupported",
            path=f"{path}.type=chart",
        )
    if getattr(element, "decorative", False) is True:
        return
    raise TemplateV2NativePptxCompileError(
        "template_v2_native_clone_element_unsupported",
        path=f"{path}.type={element.type}",
    )


def _shape_name(shape: ET.Element) -> str | None:
    for tag in (
        f"{{{_P_NS}}}cNvPr",
    ):
        candidate = shape.find(f".//{tag}")
        if candidate is not None:
            return candidate.get("name")
    return None


def _normalize_slot_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    return normalized[:60]


def _unique_shape(
    shape_map: Mapping[str, list[ET.Element]], name: str, path: str
) -> ET.Element:
    candidates = shape_map.get(_normalize_slot_name(name), [])
    if not candidates:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_shape_missing", path=f"{path}.name={name}"
        )
    if len(candidates) != 1:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_shape_ambiguous", path=f"{path}.name={name}"
        )
    return candidates[0]


def _replace_text(shape: ET.Element, value: str, path: str) -> None:
    runs = list(shape.iter(f"{{{_A_NS}}}t"))
    if not runs:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_text_body_missing", path=path
        )
    runs[0].text = value
    for run in runs[1:]:
        run.text = ""


def _replace_text_list(shape: ET.Element, values: list[str], path: str) -> None:
    """Patch a fixed-cardinality list while retaining all list OOXML structure."""

    if shape.tag != f"{{{_P_NS}}}sp":
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_text_body_missing", path=path
        )
    text_bodies = shape.findall(f"{{{_P_NS}}}txBody")
    if len(text_bodies) != 1:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_text_body_missing", path=path
        )
    text_body = text_bodies[0]
    paragraphs = text_body.findall(f"{{{_A_NS}}}p")
    if not paragraphs:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_text_body_missing", path=path
        )
    if len(values) != len(paragraphs):
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_text_list_shape_change_unsupported",
            path=path,
        )
    for index, (paragraph, value) in enumerate(
        zip(paragraphs, values, strict=True)
    ):
        _replace_plain_paragraph_text(
            paragraph, value, f"{path}.content.items.{index}"
        )


def _replace_plain_paragraph_text(
    paragraph: ET.Element, value: str, path: str
) -> None:
    """Replace ordinary DrawingML runs without changing paragraph/run structure."""

    if any(not _is_safe_plain_xml_character(character) for character in value):
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_text_list_paragraph_unsupported", path=path
        )
    if any(
        node.tag in {f"{{{_A_NS}}}hlinkClick", f"{{{_A_NS}}}hlinkMouseOver"}
        for node in paragraph.iter()
    ):
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_text_list_paragraph_unsupported", path=path
        )
    allowed_paragraph_tags = {
        f"{{{_A_NS}}}pPr",
        f"{{{_A_NS}}}r",
        f"{{{_A_NS}}}endParaRPr",
    }
    if any(child.tag not in allowed_paragraph_tags for child in paragraph):
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_text_list_paragraph_unsupported", path=path
        )
    runs = paragraph.findall(f"{{{_A_NS}}}r")
    if not runs:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_text_list_paragraph_unsupported", path=path
        )
    text_nodes: list[ET.Element] = []
    for run in runs:
        allowed_run_tags = {f"{{{_A_NS}}}rPr", f"{{{_A_NS}}}t"}
        if any(child.tag not in allowed_run_tags for child in run):
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_clone_text_list_paragraph_unsupported", path=path
            )
        texts = run.findall(f"{{{_A_NS}}}t")
        if len(texts) != 1:
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_clone_text_list_paragraph_unsupported", path=path
            )
        text_nodes.append(texts[0])

    text_nodes[0].text = value
    _set_xml_space(text_nodes[0], value)
    for text_node in text_nodes[1:]:
        text_node.text = ""
        text_node.attrib.pop(f"{{{_XML_NS}}}space", None)


def _is_safe_plain_xml_character(character: str) -> bool:
    codepoint = ord(character)
    return (
        codepoint >= 0x20
        and codepoint not in {0x7F, 0x85}
        and not 0xD800 <= codepoint <= 0xDFFF
        and codepoint not in {0xFFFE, 0xFFFF}
    )


def _set_xml_space(text_node: ET.Element, value: str) -> None:
    attribute = f"{{{_XML_NS}}}space"
    if value.startswith(" ") or value.endswith(" "):
        text_node.set(attribute, "preserve")
    else:
        text_node.attrib.pop(attribute, None)


def _replace_table(shape: ET.Element, values: list[list[str]], path: str) -> None:
    table = shape.find(f".//{{{_A_NS}}}tbl")
    if table is None:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_table_body_missing", path=path
        )
    rows = table.findall(f"{{{_A_NS}}}tr")
    if len(rows) != len(values):
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_table_shape_change_unsupported", path=path
        )
    for row_index, row in enumerate(rows):
        cells = row.findall(f"{{{_A_NS}}}tc")
        if len(cells) != len(values[row_index]):
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_pptx_table_shape_change_unsupported", path=path
            )
        for column_index, cell in enumerate(cells):
            _replace_text(cell, values[row_index][column_index], path)


def _serialize_xml(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _serialize_content_types(root: ET.Element) -> bytes:
    """Emit OPC content types with the canonical default namespace.

    LibreOffice rejects packages whose rewritten presentation and content-type
    roots both use ElementTree's anonymous prefix for different namespaces.
    This flat OPC part needs only attributes, so rebuilding it is lossless.
    """

    serialized = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<Types xmlns="{_CT_NS}">',
    ]
    for child in root:
        local_name = child.tag.rsplit("}", 1)[-1]
        if local_name not in {"Default", "Override"}:
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_clone_content_types_invalid",
                path="[Content_Types].xml",
            )
        attributes = " ".join(
            f"{name}={quoteattr(value)}" for name, value in child.attrib.items()
        )
        serialized.append(f"<{local_name} {attributes}/>")
    serialized.append("</Types>")
    return "".join(serialized).encode("utf-8")


def _write_package(parts: Mapping[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(parts):
            info = ZipInfo(name, date_time=_FIXED_PACKAGE_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, parts[name])
    return output.getvalue()

# Explicit names keep native_clone's historical private helper spellings internal.
patch_slide = _patch_slide
serialize_xml = _serialize_xml
serialize_content_types = _serialize_content_types
write_package = _write_package


__all__ = [
    "patch_slide",
    "serialize_content_types",
    "serialize_xml",
    "write_package",
]

