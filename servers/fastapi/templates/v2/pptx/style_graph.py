from __future__ import annotations

from collections import defaultdict

from .models import (
    LayoutEvidence,
    MasterEvidence,
    RelationshipGraphEvidence,
    SlideStyleBinding,
    StyleGraphEvidence,
    ThemeEvidence,
)
from .package_reader import PptxPackageReader


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}


def _targets(
    graph: RelationshipGraphEvidence,
) -> dict[str, dict[str, list[str]]]:
    targets: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for relationship in graph.relationships:
        if (
            relationship.external
            or relationship.missing
            or relationship.target_part is None
        ):
            continue
        targets[relationship.source_part][relationship.relationship_kind].append(
            relationship.target_part
        )
    return targets


def _first(
    targets: dict[str, dict[str, list[str]]],
    source: str,
    kind: str,
) -> str | None:
    values = targets.get(source, {}).get(kind, [])
    return values[0] if values else None


def _placeholder_types(root) -> list[str]:
    values = {
        placeholder.get("type") or "body"
        for placeholder in root.findall(".//p:ph", NS)
    }
    return sorted(values)


def _theme_color(node) -> str | None:
    if node is None or not list(node):
        return None
    color = list(node)[0]
    return color.get("lastClr") or color.get("val")


def _theme_evidence(reader: PptxPackageReader, part: str) -> ThemeEvidence:
    root = reader.read_xml(part)
    scheme = root.find(".//a:themeElements/a:clrScheme", NS)
    colors: dict[str, str] = {}
    if scheme is not None:
        for entry in list(scheme):
            value = _theme_color(entry)
            if value:
                colors[entry.tag.rsplit("}", 1)[-1]] = value.upper()
    major = root.find(".//a:fontScheme/a:majorFont/a:latin", NS)
    minor = root.find(".//a:fontScheme/a:minorFont/a:latin", NS)
    return ThemeEvidence(
        part=part,
        name=root.get("name"),
        major_font=major.get("typeface") if major is not None else None,
        minor_font=minor.get("typeface") if minor is not None else None,
        colors=colors,
    )


def build_style_graph_evidence(
    reader: PptxPackageReader,
    relationship_graph: RelationshipGraphEvidence,
) -> StyleGraphEvidence:
    """Resolve bounded slide/layout/master/theme inheritance evidence."""

    targets = _targets(relationship_graph)
    theme_parts = sorted(
        {
            target
            for by_kind in targets.values()
            for target in by_kind.get("theme", [])
        }
    )
    master_parts = sorted(
        {
            target
            for by_kind in targets.values()
            for target in by_kind.get("slide_master", [])
        }
    )
    layout_parts = sorted(
        {
            target
            for by_kind in targets.values()
            for target in by_kind.get("slide_layout", [])
        }
    )
    themes = [_theme_evidence(reader, part) for part in theme_parts]
    masters = []
    for part in master_parts:
        root = reader.read_xml(part)
        masters.append(
            MasterEvidence(
                part=part,
                theme_part=_first(targets, part, "theme"),
                placeholder_types=_placeholder_types(root),
            )
        )
    layouts = []
    for part in layout_parts:
        root = reader.read_xml(part)
        master_part = _first(targets, part, "slide_master")
        layouts.append(
            LayoutEvidence(
                part=part,
                name=(
                    root.find("./p:cSld", NS).get("name")
                    if root.find("./p:cSld", NS) is not None
                    else None
                ),
                master_part=master_part,
                theme_part=(
                    _first(targets, master_part, "theme")
                    if master_part
                    else None
                ),
                placeholder_types=_placeholder_types(root),
            )
        )
    bindings = []
    slide_parts = sorted(
        part
        for part in relationship_graph.nodes
        if part.startswith("ppt/slides/slide") and part.endswith(".xml")
    )
    for slide_part in slide_parts:
        layout_part = _first(targets, slide_part, "slide_layout")
        master_part = (
            _first(targets, layout_part, "slide_master")
            if layout_part
            else None
        )
        bindings.append(
            SlideStyleBinding(
                slide_part=slide_part,
                layout_part=layout_part,
                master_part=master_part,
                theme_part=(
                    _first(targets, master_part, "theme")
                    if master_part
                    else None
                ),
            )
        )
    return StyleGraphEvidence(
        themes=themes,
        masters=masters,
        layouts=layouts,
        slide_bindings=bindings,
    )


def style_graph_manifest_summary(
    evidence: StyleGraphEvidence,
) -> dict[str, object]:
    return evidence.model_dump(mode="json")
