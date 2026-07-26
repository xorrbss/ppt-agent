from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from templates.v2.models.elements import (
    Chart,
    ChartSeries,
    ChartType,
    Container,
    Fill,
    Font,
    Group,
    Position,
    Size,
    Table,
    TableCell,
    Text,
    TextRun,
)
from templates.v2.models.layouts import (
    Component,
    RawSlideLayout,
    RawSlideLayouts,
    SlideLayout,
    SlideLayouts,
)

from .models import PresentationCandidates, ShapeCandidate
from .relationship_graph import relationship_graph_manifest_summary
from .style_graph import style_graph_manifest_summary


@dataclass(frozen=True)
class AssembledTemplateV2Draft:
    raw_layouts: RawSlideLayouts
    layouts: SlideLayouts
    contents: list[dict[str, Any]]
    manifest: dict[str, Any]


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _slot_name(candidate: ShapeCandidate) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", candidate.name).strip("_").lower()
    return (value or f"shape_{candidate.source_id}")[:60]


def _element(
    candidate: ShapeCandidate,
    *,
    relative: bool,
    origin: tuple[float, float] = (0, 0),
):
    position = Position(
        x=candidate.x - origin[0] if relative else candidate.x,
        y=candidate.y - origin[1] if relative else candidate.y,
    )
    size = Size(width=max(candidate.width, 1), height=max(candidate.height, 1))
    if candidate.kind == "text":
        text = candidate.text or ""
        runs = [
            TextRun(
                text=run.text,
                font=Font(
                    size=run.font_size,
                    family=run.font_family,
                    color=run.font_color,
                    bold=run.bold,
                    italic=run.italic,
                    underline=run.underline,
                ),
            )
            for run in candidate.text_runs or []
        ]
        return Text(
            type="text",
            position=position,
            size=size,
            rotation=candidate.rotation,
            runs=runs or [TextRun(text=text)],
            decorative=False,
            name=_slot_name(candidate),
            min_length=0,
            max_length=max(1_000, len(text) * 4),
        )
    if candidate.kind == "container":
        return Container(
            type="container",
            position=position,
            size=size,
            rotation=candidate.rotation,
            fill=Fill(color=candidate.fill_color or "#FFFFFF"),
        )
    if candidate.kind == "table":
        rows = candidate.table_rows or []
        cells = [
            [TableCell(runs=[TextRun(text=value)]) for value in row]
            for row in rows
        ]
        column_count = len(cells[0])
        return Table(
            type="table",
            position=position,
            size=size,
            rotation=candidate.rotation,
            columns=cells[0],
            rows=cells[1:],
            decorative=False,
            name=_slot_name(candidate),
            min_columns=column_count,
            max_columns=column_count,
            min_rows=max(0, len(cells) - 1),
            max_rows=max(0, len(cells) - 1),
        )
    if candidate.kind == "chart":
        return Chart(
            type="chart",
            position=position,
            size=size,
            rotation=candidate.rotation,
            chart_type=ChartType(candidate.chart_type),
            categories=candidate.chart_categories,
            series=[
                ChartSeries(name=series.name, values=series.values)
                for series in candidate.chart_series or []
            ],
            decorative=False,
            name=_slot_name(candidate),
        )
    if candidate.kind == "group":
        return Group(
            type="group",
            position=position,
            size=size,
            children=[
                _element(child, relative=False)
                for child in candidate.children or []
            ],
            name=_slot_name(candidate),
        )
    raise ValueError("unsupported_candidate_cannot_be_assembled")


def _accepted_repeat_groups(
    slide_shapes: list[ShapeCandidate],
    suggestions: list[dict[str, Any]],
    source_part: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    by_source_id = {shape.source_id: shape for shape in slide_shapes}
    groups: dict[str, dict[str, Any]] = {}
    applied: list[dict[str, Any]] = []
    for suggestion in suggestions:
        if suggestion.get("source_part") != source_part:
            continue
        source_ids = suggestion.get("source_ids")
        if not isinstance(source_ids, list) or len(source_ids) < 2:
            raise ValueError("invalid_repeat_suggestion")
        shapes = [by_source_id.get(str(source_id)) for source_id in source_ids]
        if any(shape is None or shape.kind == "unsupported" for shape in shapes):
            raise ValueError("repeat_suggestion_source_missing")
        if any(str(source_id) in groups for source_id in source_ids):
            raise ValueError("overlapping_repeat_suggestions")
        group = {
            **suggestion,
            "status": "applied",
            "shapes": shapes,
        }
        for source_id in source_ids:
            groups[str(source_id)] = group
        applied.append(group)
    return groups, applied


def _repeat_component(
    candidates: PresentationCandidates,
    relationship_id: str,
    group: dict[str, Any],
) -> tuple[Component, dict[str, Any]]:
    shapes: list[ShapeCandidate] = group["shapes"]
    min_x = min(shape.x for shape in shapes)
    min_y = min(shape.y for shape in shapes)
    max_x = max(shape.x + shape.width for shape in shapes)
    max_y = max(shape.y + shape.height for shape in shapes)
    suggestion_id = str(group["id"])
    component_id = _stable_id(
        "repeat",
        candidates.source_sha256,
        relationship_id,
        suggestion_id,
    )
    group_name = component_id[:60]
    component = Component(
        id=component_id,
        description=f"Accepted repeated PPTX block {suggestion_id}"[:300],
        position=Position(x=min_x, y=min_y),
        elements=[
            Group(
                type="group",
                position=Position(x=0, y=0),
                size=Size(width=max_x - min_x, height=max_y - min_y),
                children=[
                    _element(shape, relative=True, origin=(min_x, min_y))
                    for shape in shapes
                ],
                name=group_name,
            )
        ],
    )
    nested_content = {
        _slot_name(shape): shape.text or ""
        for shape in shapes
        if shape.kind == "text"
    }
    content = {group_name: nested_content} if nested_content else {}
    return component, content


def assemble_template_v2_draft(
    candidates: PresentationCandidates,
    *,
    accepted_repeat_suggestions: list[dict[str, Any]] | None = None,
) -> AssembledTemplateV2Draft:
    accepted_repeat_suggestions = accepted_repeat_suggestions or []
    raw_layouts: list[RawSlideLayout] = []
    layouts: list[SlideLayout] = []
    contents: list[dict[str, Any]] = []
    slide_manifests: list[dict[str, Any]] = []
    for slide_index, slide in enumerate(candidates.slides, start=1):
        layout_id = _stable_id(
            "pptx_layout",
            candidates.source_sha256,
            slide.relationship_id,
        )
        supported = [shape for shape in slide.shapes if shape.kind != "unsupported"]
        unsupported = [shape for shape in slide.shapes if shape.kind == "unsupported"]
        repeat_groups, applied_groups = _accepted_repeat_groups(
            slide.shapes,
            accepted_repeat_suggestions,
            slide.source_part,
        )
        raw_layouts.append(
            RawSlideLayout(
                id=layout_id,
                description=f"Imported PPTX slide {slide_index}",
                elements=[_element(shape, relative=False) for shape in supported],
            )
        )
        components: list[Component] = []
        content: dict[str, Any] = {}
        for shape in supported:
            repeat_group = repeat_groups.get(shape.source_id)
            if repeat_group is not None:
                if shape.source_id != repeat_group["source_ids"][0]:
                    continue
                component, repeat_content = _repeat_component(
                    candidates,
                    slide.relationship_id,
                    repeat_group,
                )
                components.append(component)
                if repeat_content:
                    content[component.id] = repeat_content
                continue
            component_id = _stable_id(
                "component",
                candidates.source_sha256,
                slide.relationship_id,
                shape.source_id,
            )
            components.append(
                Component(
                    id=component_id,
                    description=f"Imported editable PPTX shape {shape.name}"[:300],
                    position=Position(x=shape.x, y=shape.y),
                    elements=[_element(shape, relative=True)],
                )
            )
            if shape.kind == "text":
                content[component_id] = {_slot_name(shape): shape.text or ""}
            elif shape.kind == "table":
                content[component_id] = {
                    _slot_name(shape): {
                        "columns": (shape.table_rows or [[]])[0],
                        "rows": (shape.table_rows or [])[1:],
                    }
                }
            elif shape.kind == "chart":
                content[component_id] = {
                    _slot_name(shape): {
                        "chart_type": shape.chart_type,
                        "categories": shape.chart_categories,
                        "series": [
                            series.model_dump(mode="json")
                            for series in shape.chart_series or []
                        ],
                    }
                }
        layouts.append(
            SlideLayout(
                id=layout_id,
                description=f"Deterministic OOXML import for slide {slide_index}",
                components=components,
            )
        )
        slide_manifests.append(
            {
                "slide": slide_index,
                "source_part": slide.source_part,
                "editable_shape_count": len(supported),
                "confidence": (
                    min((shape.confidence for shape in supported), default=0)
                    if not unsupported
                    else 0.5
                ),
                "unsupported": [
                    {
                        "source_id": shape.source_id,
                        "name": shape.name,
                        "reason": shape.unsupported_reason,
                        "contract": {
                            "editable": False,
                            "source_preserved": True,
                            "action": "manual_rebuild",
                        },
                    }
                    for shape in unsupported
                ],
                "external_relationship_ids_ignored": slide.external_relationships,
                "repeat_blocks": [
                    {
                        key: value
                        for key, value in group.items()
                        if key != "shapes"
                    }
                    for group in applied_groups
                ],
                "fallback": {
                    "kind": "manual_review",
                    "reason": "render_and_vision_provider_unavailable",
                },
            }
        )
        contents.append(content)
    strict_raw = RawSlideLayouts(layouts=raw_layouts)
    strict_layouts = SlideLayouts(layouts=layouts)
    needs_review = True
    manifest = {
        "schema_version": 1,
        "source_sha256": candidates.source_sha256,
        "parser": {
            "name": "deterministic-ooxml",
            "network_access": False,
        },
        "render": {
            "available": False,
            "reason": "libreoffice_not_invoked_by_foundation",
        },
        "vision": {
            "available": False,
            "reason": "vision_provider_not_configured",
            "network_access": False,
        },
        "review": {
            "required": needs_review,
            "reason": "visual_fidelity_not_verified",
        },
        "fallback": {
            "kind": "manual_review",
            "reason": "render_and_vision_provider_unavailable",
        },
        "slides": slide_manifests,
    }
    if candidates.relationship_graph is not None:
        manifest["structure_evidence"] = relationship_graph_manifest_summary(
            candidates.relationship_graph
        )
    if candidates.style_graph is not None:
        manifest["style_evidence"] = style_graph_manifest_summary(
            candidates.style_graph
        )
    return AssembledTemplateV2Draft(
        raw_layouts=strict_raw,
        layouts=strict_layouts,
        contents=contents,
        manifest=manifest,
    )
