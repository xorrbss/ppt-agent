"""Patch editable values into a retained PPTX without rebuilding its design graph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import posixpath
import re
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from templates.v2.generation import build_generated_slide
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
from templates.v2.pptx.package_reader import UnsafePptxPackage
from templates.v2.pptx.part_targets import resolve_internal_part_target
from templates.v2.pptx.repeat_adapter import (
    TemplateV2PptxRepeatAdapterError,
    prepare_repeatable_pptx_slide,
)


TEMPLATE_V2_NATIVE_CLONE_SCHEMA_VERSION = "presenton.template-v2-native-clone/v1"
TEMPLATE_V2_NATIVE_CLONE_COMPILER_NAME = "presenton-template-v2-native-ooxml-clone"
TEMPLATE_V2_NATIVE_CLONE_COMPILER_VERSION = "1"
_FIXED_PACKAGE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_SLIDE_PART = re.compile(r"^ppt/slides/slide[1-9][0-9]*\.xml$")
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_SLIDE_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)
_SLIDE_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
)
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_PRIVATE_PRESENTATION_RELATIONSHIP_SUFFIXES = (
    "/notesSlide",
    "/notesMaster",
    "/comments",
    "/commentAuthors",
)

# ElementTree's anonymous ``ns0`` prefix is legal XML, but LibreOffice rejects
# a package when independently serialized OPC roots reuse it for different
# namespaces. Stable package-specific prefixes keep every rewritten part
# interoperable without changing any namespace URI or relationship identity.
ET.register_namespace("p", _P_NS)
ET.register_namespace("a", _A_NS)
ET.register_namespace("r", _R_NS)
ET.register_namespace("pr", _REL_NS)
ET.register_namespace("ct", _CT_NS)


@dataclass(frozen=True, slots=True)
class NativeCloneCompilation:
    pptx: bytes
    manifest: dict[str, Any]
    structural_sha256: str
    package_sha256: str


def compile_template_v2_native_clone(
    source_pptx: bytes,
    slides: Sequence[tuple[SlideLayout, Mapping[str, Any], str]],
) -> NativeCloneCompilation:
    """Clone source slide parts and patch only supported editable text/table values."""

    if not slides:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_empty", path="slides"
        )
    parts = _read_package(source_pptx)
    presentation = _xml_part(parts, "ppt/presentation.xml")
    presentation_rels = _xml_part(parts, "ppt/_rels/presentation.xml.rels")
    content_types = _xml_part(parts, "[Content_Types].xml")
    source_slide_rids = _source_slide_relationships(presentation_rels)
    source_slide_ids = _source_slide_identifiers(presentation)

    slide_numbers = [
        int(match.group(1))
        for name in parts
        if (match := re.fullmatch(r"ppt/slides/slide([1-9][0-9]*)\.xml", name))
    ]
    next_slide_number = max(slide_numbers, default=0) + 1
    next_slide_id = max(source_slide_ids.values(), default=255) + 1
    used_parts: set[str] = set()
    content_types_changed = False
    output_slide_ids: list[tuple[int, str]] = []
    selected_parts: list[str] = []
    patched_slots: list[list[str]] = []

    for slide_index, (layout, raw_content, source_part) in enumerate(slides):
        if (
            not isinstance(source_part, str)
            or _SLIDE_PART.fullmatch(source_part) is None
        ):
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_clone_source_part_invalid",
                path=f"slides.{slide_index}.source_part",
            )
        if source_part not in parts or source_part not in source_slide_rids:
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_clone_source_part_missing",
                path=f"slides.{slide_index}.source_part",
            )
        generated = build_generated_slide(layout, dict(raw_content))
        try:
            prepared = prepare_repeatable_pptx_slide(
                layout,
                generated.content,
                preservation_mode="native_clone",
            )
        except TemplateV2PptxRepeatAdapterError as error:
            raise TemplateV2NativePptxCompileError(
                error.code,
                path=error.path,
            ) from error
        source_xml = parts[source_part]
        target_part = source_part
        if source_part in used_parts:
            target_part = f"ppt/slides/slide{next_slide_number}.xml"
            next_slide_number += 1
            source_rels_part = _slide_relationship_part(source_part)
            target_rels_part = _slide_relationship_part(target_part)
            if source_rels_part in parts:
                parts[target_rels_part] = parts[source_rels_part]
            _ensure_slide_content_type(content_types, target_part)
            content_types_changed = True
            relationship_id = _append_slide_relationship(
                presentation_rels, target_part
            )
            slide_id = next_slide_id
            next_slide_id += 1
        else:
            relationship_id = source_slide_rids[source_part]
            slide_id = source_slide_ids.get(relationship_id)
            if slide_id is None:
                raise TemplateV2NativePptxCompileError(
                    "template_v2_native_clone_slide_identity_missing",
                    path=f"slides.{slide_index}.source_part",
                )

        patched, names = _patch_slide(
            source_xml,
            layout=prepared.layout,
            content=prepared.content,
            slide_index=slide_index,
        )
        parts[target_part] = patched
        used_parts.add(source_part)
        output_slide_ids.append((slide_id, relationship_id))
        selected_parts.append(target_part)
        patched_slots.append(names)

    _replace_slide_order(presentation, output_slide_ids)
    parts["ppt/presentation.xml"] = _serialize_xml(presentation)
    parts["ppt/_rels/presentation.xml.rels"] = _serialize_xml(presentation_rels)
    if content_types_changed:
        parts["[Content_Types].xml"] = _serialize_content_types(content_types)
    parts = _retain_generated_package(parts, output_slide_ids)
    package = _write_package(parts)
    manifest = {
        "schema_version": TEMPLATE_V2_NATIVE_CLONE_SCHEMA_VERSION,
        "compiler_name": TEMPLATE_V2_NATIVE_CLONE_COMPILER_NAME,
        "compiler_version": TEMPLATE_V2_NATIVE_CLONE_COMPILER_VERSION,
        "preservation_mode": "native_clone",
        "slide_count": len(slides),
        "source_parts": [source_part for _, _, source_part in slides],
        "output_parts": selected_parts,
        "patched_slots": patched_slots,
        "preserved_part_sha256": {
            name: sha256(value).hexdigest()
            for name, value in sorted(parts.items())
            if name.startswith(
                (
                    "ppt/slideMasters/",
                    "ppt/slideLayouts/",
                    "ppt/theme/",
                    "ppt/media/",
                )
            )
        },
    }
    structural = json.dumps(
        manifest,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return NativeCloneCompilation(
        pptx=package,
        manifest=manifest,
        structural_sha256=sha256(structural).hexdigest(),
        package_sha256=sha256(package).hexdigest(),
    )


def _read_package(source: bytes) -> dict[str, bytes]:
    if not isinstance(source, bytes) or not source.startswith(b"PK"):
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_package_invalid", path="source_pptx"
        )
    try:
        with ZipFile(BytesIO(source), "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise TemplateV2NativePptxCompileError(
                    "template_v2_native_clone_duplicate_part", path="source_pptx"
                )
            return {name: archive.read(name) for name in names if not name.endswith("/")}
    except (BadZipFile, KeyError, ET.ParseError) as error:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_package_invalid", path="source_pptx"
        ) from error


def _xml_part(parts: Mapping[str, bytes], name: str) -> ET.Element:
    raw = parts.get(name)
    if raw is None:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_required_part_missing", path=name
        )
    try:
        return ET.fromstring(raw)
    except ET.ParseError as error:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_xml_invalid", path=name
        ) from error


def _source_slide_relationships(root: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for relationship in root.findall(f"{{{_REL_NS}}}Relationship"):
        if relationship.get("Type") != _SLIDE_REL_TYPE:
            continue
        relationship_id = relationship.get("Id")
        target = relationship.get("Target")
        if not relationship_id or not target or relationship.get("TargetMode") == "External":
            continue
        part = _relationship_target("ppt/presentation.xml", target)
        result[part] = relationship_id
    return result


def _source_slide_identifiers(root: ET.Element) -> dict[str, int]:
    result: dict[str, int] = {}
    slide_id_list = root.find(f"{{{_P_NS}}}sldIdLst")
    if slide_id_list is None:
        return result
    for slide_id in slide_id_list.findall(f"{{{_P_NS}}}sldId"):
        relationship_id = slide_id.get(f"{{{_R_NS}}}id")
        raw_id = slide_id.get("id")
        if relationship_id and raw_id and raw_id.isdigit():
            result[relationship_id] = int(raw_id)
    return result


def _relationship_target(source_part: str, target: str) -> str:
    try:
        return resolve_internal_part_target(source_part, target)
    except UnsafePptxPackage as error:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_relationship_target_invalid",
            path=source_part,
        ) from error


def _slide_relationship_part(slide_part: str) -> str:
    directory, filename = posixpath.split(slide_part)
    return f"{directory}/_rels/{filename}.rels"


def _append_slide_relationship(root: ET.Element, target_part: str) -> str:
    used = {
        item.get("Id")
        for item in root.findall(f"{{{_REL_NS}}}Relationship")
        if item.get("Id")
    }
    sequence = 1
    while f"rId{sequence}" in used:
        sequence += 1
    relationship_id = f"rId{sequence}"
    ET.SubElement(
        root,
        f"{{{_REL_NS}}}Relationship",
        {
            "Id": relationship_id,
            "Type": _SLIDE_REL_TYPE,
            "Target": posixpath.relpath(target_part, "ppt"),
        },
    )
    return relationship_id


def _ensure_slide_content_type(root: ET.Element, slide_part: str) -> None:
    part_name = f"/{slide_part}"
    for override in root.findall(f"{{{_CT_NS}}}Override"):
        if override.get("PartName") == part_name:
            return
    ET.SubElement(
        root,
        f"{{{_CT_NS}}}Override",
        {"PartName": part_name, "ContentType": _SLIDE_CONTENT_TYPE},
    )


def _replace_slide_order(root: ET.Element, slides: Sequence[tuple[int, str]]) -> None:
    slide_id_list = root.find(f"{{{_P_NS}}}sldIdLst")
    if slide_id_list is None:
        slide_id_list = ET.Element(f"{{{_P_NS}}}sldIdLst")
        root.insert(0, slide_id_list)
    for child in list(slide_id_list):
        slide_id_list.remove(child)
    for slide_id, relationship_id in slides:
        ET.SubElement(
            slide_id_list,
            f"{{{_P_NS}}}sldId",
            {"id": str(slide_id), f"{{{_R_NS}}}id": relationship_id},
        )


def _retain_generated_package(
    parts: Mapping[str, bytes],
    output_slides: Sequence[tuple[int, str]],
) -> dict[str, bytes]:
    """Keep only generated-deck reachable parts and remove notes/comments."""

    retained = dict(parts)
    presentation_rels_name = "ppt/_rels/presentation.xml.rels"
    presentation_rels = _xml_part(retained, presentation_rels_name)
    output_relationship_ids = {relationship_id for _, relationship_id in output_slides}
    for relationship in list(
        presentation_rels.findall(f"{{{_REL_NS}}}Relationship")
    ):
        if (
            relationship.get("Type") == _SLIDE_REL_TYPE
            and relationship.get("Id") not in output_relationship_ids
        ):
            presentation_rels.remove(relationship)
    retained[presentation_rels_name] = _serialize_xml(presentation_rels)

    reachable: set[str] = set()
    relationship_parts: set[str] = set()
    pending = ["ppt/presentation.xml"]
    root_rels_name = "_rels/.rels"
    if root_rels_name in retained:
        relationship_parts.add(root_rels_name)
        pending.extend(
            _sanitize_relationships(
                retained,
                root_rels_name,
                source_part="",
            )
        )

    while pending:
        part = pending.pop()
        if part in reachable:
            continue
        if part not in retained:
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_clone_relationship_part_missing",
                path=part,
            )
        reachable.add(part)
        rels_name = _part_relationship_name(part)
        if rels_name not in retained:
            continue
        relationship_parts.add(rels_name)
        pending.extend(
            _sanitize_relationships(
                retained,
                rels_name,
                source_part=part,
            )
        )

    keep = reachable | relationship_parts | {"[Content_Types].xml"}
    retained = {name: payload for name, payload in retained.items() if name in keep}
    content_types = _xml_part(retained, "[Content_Types].xml")
    content_types_changed = False
    for override in list(content_types.findall(f"{{{_CT_NS}}}Override")):
        part_name = (override.get("PartName") or "").lstrip("/")
        if part_name and part_name not in retained:
            content_types.remove(override)
            content_types_changed = True
    if content_types_changed:
        retained["[Content_Types].xml"] = _serialize_content_types(content_types)
    return retained


def _sanitize_relationships(
    parts: dict[str, bytes],
    rels_name: str,
    *,
    source_part: str,
) -> list[str]:
    root = _xml_part(parts, rels_name)
    targets: list[str] = []
    changed = False
    for relationship in list(root.findall(f"{{{_REL_NS}}}Relationship")):
        relationship_type = relationship.get("Type", "")
        if relationship_type.endswith(_PRIVATE_PRESENTATION_RELATIONSHIP_SUFFIXES):
            root.remove(relationship)
            changed = True
            continue
        if relationship.get("TargetMode") == "External":
            continue
        target = relationship.get("Target")
        if not target:
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_clone_relationship_target_invalid",
                path=rels_name,
            )
        targets.append(_relationship_target(source_part, target))
    if changed:
        parts[rels_name] = _serialize_xml(root)
    return targets


def _part_relationship_name(part: str) -> str:
    directory, filename = posixpath.split(part)
    return posixpath.join(directory, "_rels", f"{filename}.rels")


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


__all__ = [
    "NativeCloneCompilation",
    "TEMPLATE_V2_NATIVE_CLONE_COMPILER_NAME",
    "TEMPLATE_V2_NATIVE_CLONE_COMPILER_VERSION",
    "TEMPLATE_V2_NATIVE_CLONE_SCHEMA_VERSION",
    "compile_template_v2_native_clone",
]
