from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .package_reader import PptxPackageReader, UnsafePptxPackage
from .relationship_graph import build_relationship_graph_evidence

_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
}
_EMU_PER_PX = 9525.0
_MAX_SHAPES = 10_000
_MAX_MANIFEST_SHAPES = 500
_MAX_MANIFEST_ISSUES = 100


@dataclass(frozen=True)
class PlaceholderAttributes:
    part: str
    idx: str | None
    type: str | None
    orient: str | None
    size: str | None

    def as_manifest(self) -> dict[str, Any]:
        return {
            "part": self.part,
            "idx": self.idx,
            "type": self.type,
            "orient": self.orient,
            "size": self.size,
        }


@dataclass(frozen=True)
class ShapePlaceholderEvidence:
    slide_index: int
    slide_part: str
    shape_path: str
    shape_name: str
    shape_kind: str
    geometry: dict[str, float] | None
    slide_placeholder: PlaceholderAttributes | None
    layout_placeholder: PlaceholderAttributes | None
    master_placeholder: PlaceholderAttributes | None
    resolved_type: str | None
    status: str
    reason: str
    resolved_idx: str | None = None
    resolved_orient: str | None = None
    resolved_size: str | None = None

    def as_manifest(self) -> dict[str, Any]:
        return {
            "slide_index": self.slide_index,
            "slide_part": self.slide_part,
            "shape_path": self.shape_path,
            "shape_name": self.shape_name,
            "shape_kind": self.shape_kind,
            "geometry": self.geometry,
            "slide_placeholder": (
                self.slide_placeholder.as_manifest() if self.slide_placeholder else None
            ),
            "layout_placeholder": (
                self.layout_placeholder.as_manifest() if self.layout_placeholder else None
            ),
            "master_placeholder": (
                self.master_placeholder.as_manifest() if self.master_placeholder else None
            ),
            "resolved_type": self.resolved_type,
            "resolved_idx": self.resolved_idx,
            "resolved_orient": self.resolved_orient,
            "resolved_size": self.resolved_size,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RuntimePlaceholderEvidence:
    version: int = 1
    status: str = "available"
    reason: str = "ooxml_placeholder_evidence_extracted"
    slide_count: int = 0
    shapes: tuple[ShapePlaceholderEvidence, ...] = field(default_factory=tuple)
    issues: tuple[str, ...] = field(default_factory=tuple)

    def as_manifest(self) -> dict[str, Any]:
        visible_shapes = self.shapes[:_MAX_MANIFEST_SHAPES]
        visible_issues = self.issues[:_MAX_MANIFEST_ISSUES]
        return {
            "version": self.version,
            "status": self.status,
            "reason": self.reason,
            "slide_count": self.slide_count,
            "shape_count": len(self.shapes),
            "shapes_omitted": len(self.shapes) - len(visible_shapes),
            "shapes": [shape.as_manifest() for shape in visible_shapes],
            "issue_count": len(self.issues),
            "issues_omitted": len(self.issues) - len(visible_issues),
            "issues": list(visible_issues),
        }


def unavailable_runtime_placeholder_evidence(
    reason: str = "placeholder_evidence_unavailable",
) -> RuntimePlaceholderEvidence:
    return RuntimePlaceholderEvidence(status="unavailable", reason=reason)


@dataclass(frozen=True)
class _PartShape:
    part: str
    path: str
    name: str
    kind: str
    geometry: dict[str, float] | None
    placeholder: PlaceholderAttributes | None


def _relationship_targets(
    reader: PptxPackageReader,
) -> tuple[dict[tuple[str, str], str], dict[str, dict[str, list[str]]]]:
    graph = build_relationship_graph_evidence(reader)
    by_id: dict[tuple[str, str], str] = {}
    by_kind: dict[str, dict[str, list[str]]] = {}
    for relationship in graph.relationships:
        if relationship.external or relationship.missing or not relationship.target_part:
            continue
        by_id[(relationship.source_part, relationship.relationship_id)] = (
            relationship.target_part
        )
        by_kind.setdefault(relationship.source_part, {}).setdefault(
            relationship.relationship_kind, []
        ).append(relationship.target_part)
    return by_id, by_kind


def _first_target(
    by_kind: dict[str, dict[str, list[str]]], source: str, kind: str
) -> str | None:
    targets = by_kind.get(source, {}).get(kind, [])
    return targets[0] if len(targets) == 1 else None


def _shape_name(shape: ET.Element) -> str:
    for path in (
        "./p:nvSpPr/p:cNvPr",
        "./p:nvPicPr/p:cNvPr",
        "./p:nvGraphicFramePr/p:cNvPr",
        "./p:nvCxnSpPr/p:cNvPr",
        "./p:nvGrpSpPr/p:cNvPr",
    ):
        node = shape.find(path, _NS)
        if node is not None:
            return node.get("name") or ""
    return ""


def _placeholder_attributes(
    shape: ET.Element, part: str
) -> PlaceholderAttributes | None:
    node = shape.find(".//p:nvPr/p:ph", _NS)
    if node is None:
        return None
    return PlaceholderAttributes(
        part=part,
        idx=node.get("idx"),
        type=node.get("type"),
        orient=node.get("orient"),
        size=node.get("sz"),
    )


def _shape_kind(
    shape: ET.Element, placeholder: PlaceholderAttributes | None
) -> str:
    local_name = str(shape.tag).rsplit("}", 1)[-1]
    if local_name == "pic":
        return "image"
    if local_name == "graphicFrame":
        if shape.find(".//a:tbl", _NS) is not None:
            return "table"
        if shape.find(".//c:chart", _NS) is not None:
            return "chart"
        return "object"
    if local_name in {"cxnSp", "grpSp"}:
        return "vector"
    if placeholder and placeholder.type == "pic":
        return "image"
    if shape.find("./p:txBody", _NS) is not None:
        return "text"
    return "vector"


def _number(node: ET.Element | None, key: str) -> float | None:
    if node is None:
        return None
    value = node.get(key)
    if value is None:
        return None
    try:
        return round(int(value) / _EMU_PER_PX, 4)
    except ValueError:
        return None


def _geometry(shape: ET.Element) -> dict[str, float] | None:
    for path in ("./p:spPr/a:xfrm", "./p:xfrm"):
        xfrm = shape.find(path, _NS)
        if xfrm is None:
            continue
        off = xfrm.find("./a:off", _NS)
        ext = xfrm.find("./a:ext", _NS)
        values = {
            "x": _number(off, "x"),
            "y": _number(off, "y"),
            "width": _number(ext, "cx"),
            "height": _number(ext, "cy"),
        }
        if all(value is not None for value in values.values()):
            return {key: float(value) for key, value in values.items()}
    return None


def _part_shapes(reader: PptxPackageReader, part: str) -> list[_PartShape]:
    root = reader.read_xml(part)
    tree = root.find(".//p:cSld/p:spTree", _NS)
    if tree is None:
        return []
    result: list[_PartShape] = []

    def visit(parent: ET.Element, prefix: str, nested: bool) -> None:
        leaf_index = 0
        for child in parent:
            local_name = str(child.tag).rsplit("}", 1)[-1]
            if local_name not in {"sp", "pic", "graphicFrame", "cxnSp", "grpSp"}:
                continue
            leaf_index += 1
            path = f"{prefix}/{local_name}[{leaf_index}]"
            placeholder = _placeholder_attributes(child, part)
            result.append(
                _PartShape(
                    part=part,
                    path=path,
                    name=_shape_name(child),
                    kind=_shape_kind(child, placeholder),
                    geometry=None if nested else _geometry(child),
                    placeholder=placeholder,
                )
            )
            if local_name == "grpSp":
                visit(child, path, True)

    visit(tree, "spTree", False)
    return result


def _candidate(
    source: PlaceholderAttributes,
    candidates: list[_PartShape],
) -> tuple[_PartShape | None, str | None]:
    placeholders = [candidate for candidate in candidates if candidate.placeholder]
    # ECMA-376 defines idx=0 when the attribute is omitted. Real-world
    # producers frequently omit the default on one hierarchy level but emit it
    # explicitly on another, so raw-attribute comparison creates false
    # ambiguity. Shape names are intentionally not consulted: they are
    # localized and user-editable metadata, not placeholder identity.
    effective_idx = source.idx or "0"
    indexed = [
        candidate
        for candidate in placeholders
        if candidate.placeholder
        and (candidate.placeholder.idx or "0") == effective_idx
    ]
    if len(indexed) == 1:
        return indexed[0], None
    if len(indexed) > 1:
        if source.type is not None:
            indexed_and_typed = [
                candidate
                for candidate in indexed
                if candidate.placeholder
                and candidate.placeholder.type == source.type
            ]
            if len(indexed_and_typed) == 1:
                return indexed_and_typed[0], None
        return None, "duplicate_placeholder_idx"
    if source.type is not None:
        typed = [
            candidate
            for candidate in placeholders
            if candidate.placeholder and candidate.placeholder.type == source.type
        ]
        if len(typed) == 1:
            return typed[0], None
        if len(typed) > 1:
            return None, "duplicate_placeholder_type"
    if len(placeholders) == 1:
        return placeholders[0], None
    if not placeholders:
        return None, "inherited_placeholder_not_found"
    return None, "inherited_placeholder_ambiguous"


def _resolved_evidence(
    slide_index: int,
    slide_part: str,
    slide_shape: _PartShape,
    layout_shapes: list[_PartShape],
    master_shapes: list[_PartShape],
) -> ShapePlaceholderEvidence:
    slide_placeholder = slide_shape.placeholder
    if slide_placeholder is None:
        return ShapePlaceholderEvidence(
            slide_index=slide_index,
            slide_part=slide_part,
            shape_path=slide_shape.path,
            shape_name=slide_shape.name,
            shape_kind=slide_shape.kind,
            geometry=slide_shape.geometry,
            slide_placeholder=None,
            layout_placeholder=None,
            master_placeholder=None,
            resolved_type=None,
            status="not_placeholder",
            reason="slide_shape_has_no_placeholder",
        )

    layout_shape, layout_issue = _candidate(slide_placeholder, layout_shapes)
    layout_placeholder = layout_shape.placeholder if layout_shape else None
    master_source = layout_placeholder or slide_placeholder
    master_shape, master_issue = _candidate(master_source, master_shapes)
    master_placeholder = master_shape.placeholder if master_shape else None
    types = [
        placeholder.type
        for placeholder in (slide_placeholder, layout_placeholder, master_placeholder)
        if placeholder and placeholder.type
    ]
    if len(set(types)) > 1:
        status = "conflict"
        reason = "placeholder_type_conflict"
    elif layout_issue and slide_placeholder.type is None:
        status = "ambiguous" if "ambiguous" in layout_issue or "duplicate" in layout_issue else "unresolved"
        reason = layout_issue
    elif master_issue and not (slide_placeholder.type or (layout_placeholder and layout_placeholder.type)):
        status = "ambiguous" if "ambiguous" in master_issue or "duplicate" in master_issue else "unresolved"
        reason = master_issue
    else:
        status = "resolved"
        reason = "placeholder_type_resolved"
    resolved_type = (
        (types[0] if types else "obj") if status == "resolved" else None
    )
    resolved_idx = next(
        (
            placeholder.idx
            for placeholder in (
                slide_placeholder,
                layout_placeholder,
                master_placeholder,
            )
            if placeholder and placeholder.idx is not None
        ),
        "0",
    )
    resolved_orient = next(
        (
            placeholder.orient
            for placeholder in (
                slide_placeholder,
                layout_placeholder,
                master_placeholder,
            )
            if placeholder and placeholder.orient is not None
        ),
        "horz",
    )
    resolved_size = next(
        (
            placeholder.size
            for placeholder in (
                slide_placeholder,
                layout_placeholder,
                master_placeholder,
            )
            if placeholder and placeholder.size is not None
        ),
        "full",
    )
    geometry = (
        slide_shape.geometry
        or (layout_shape.geometry if layout_shape else None)
        or (master_shape.geometry if master_shape else None)
    )
    return ShapePlaceholderEvidence(
        slide_index=slide_index,
        slide_part=slide_part,
        shape_path=slide_shape.path,
        shape_name=slide_shape.name,
        shape_kind=slide_shape.kind,
        geometry=geometry,
        slide_placeholder=slide_placeholder,
        layout_placeholder=layout_placeholder,
        master_placeholder=master_placeholder,
        resolved_type=resolved_type,
        status=status,
        reason=reason,
        resolved_idx=resolved_idx,
        resolved_orient=resolved_orient,
        resolved_size=resolved_size,
    )


def extract_runtime_placeholder_evidence(
    source_path: str | Path,
) -> RuntimePlaceholderEvidence:
    reader = PptxPackageReader(Path(source_path))
    reader.preflight()
    by_id, by_kind = _relationship_targets(reader)
    presentation_part = "ppt/presentation.xml"
    presentation = reader.read_xml(presentation_part)
    slide_parts: list[str] = []
    for slide_id in presentation.findall("./p:sldIdLst/p:sldId", _NS):
        relationship_id = slide_id.get(f"{{{_NS['r']}}}id")
        if not relationship_id:
            continue
        target = by_id.get((presentation_part, relationship_id))
        if target:
            slide_parts.append(str(PurePosixPath(target)))

    evidence: list[ShapePlaceholderEvidence] = []
    issues: list[str] = []
    for slide_index, slide_part in enumerate(slide_parts, start=1):
        layout_part = _first_target(by_kind, slide_part, "slide_layout")
        master_part = (
            _first_target(by_kind, layout_part, "slide_master") if layout_part else None
        )
        slide_shapes = _part_shapes(reader, slide_part)
        layout_shapes = _part_shapes(reader, layout_part) if layout_part else []
        master_shapes = _part_shapes(reader, master_part) if master_part else []
        if layout_part is None:
            issues.append(f"slide_{slide_index}:slide_layout_unavailable")
        if master_part is None:
            issues.append(f"slide_{slide_index}:slide_master_unavailable")
        for slide_shape in slide_shapes:
            evidence.append(
                _resolved_evidence(
                    slide_index,
                    slide_part,
                    slide_shape,
                    layout_shapes,
                    master_shapes,
                )
            )
            if len(evidence) > _MAX_SHAPES:
                raise UnsafePptxPackage("placeholder_shape_limit_exceeded")

    return RuntimePlaceholderEvidence(
        slide_count=len(slide_parts),
        shapes=tuple(evidence),
        issues=tuple(issues),
    )
