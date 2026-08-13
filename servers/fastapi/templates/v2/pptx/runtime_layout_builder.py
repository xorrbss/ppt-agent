"""Default-content restoration and strict runtime layout model construction."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import copy
import math
from typing import Any

from pydantic import TypeAdapter, ValidationError

from templates.v2.models.elements import Position, SlideElement
from templates.v2.models.layouts import (
    Component,
    RawSlideLayout,
    RawSlideLayouts,
    SlideLayout,
    SlideLayouts,
)
from templates.v2.pptx.runtime_layout_classification import RuntimeImportedLayouts


_MAX_ID_LENGTH = 80
_MAX_DESCRIPTION_LENGTH = 300
_ELEMENT_ADAPTER: TypeAdapter[SlideElement] = TypeAdapter(SlideElement)



def runtime_default_contents(layouts: SlideLayouts) -> list[dict[str, Any]]:
    """Build confirm-time seed content for classified runtime placeholders."""

    contents: list[dict[str, Any]] = []
    for layout in layouts.layouts:
        content: dict[str, Any] = {}
        for component in layout.components:
            properties: dict[str, Any] = {}
            for element in component.elements:
                node = _default_content_node(element)
                if node is not None:
                    name, value = node
                    properties[name] = value
            if properties:
                content[component.id] = properties
        contents.append(content)
    return contents


def restore_runtime_default_contents(
    layouts: SlideLayouts,
    payload: Any,
) -> list[dict[str, Any]]:
    """Restore persisted seeds, preserving compatibility with pre-classifier jobs."""

    if payload is None:
        return [{} for _ in layouts.layouts]
    if (
        not isinstance(payload, list)
        or len(payload) != len(layouts.layouts)
        or any(not isinstance(content, dict) for content in payload)
    ):
        raise ValueError("template_v2_import_runtime_contents_invalid")
    return copy.deepcopy(payload)


def _default_content_node(element: SlideElement) -> tuple[str, Any] | None:
    element_type = element.type
    if element_type == "container":
        return (
            _default_content_node(element.child)
            if element.child is not None
            else None
        )
    if element_type in {"group", "flex", "grid"}:
        children: dict[str, Any] = {}
        for child in element.children:
            node = _default_content_node(child)
            if node is not None:
                name, value = node
                children[name] = value
        return (element.name, children) if children else None
    if element_type == "text" and element.decorative is False:
        return element.name, "".join(run.text for run in element.runs)
    if element_type == "image" and element.decorative is False:
        prompt_key = "icon_query" if element.is_icon else "image_prompt"
        return element.name, {prompt_key: element.prompt or ""}
    return None


def build_runtime_slide_layouts(
    layouts: Sequence[Mapping[str, Any]],
) -> RuntimeImportedLayouts:
    """Validate `pptx-to-json` layouts into the fork's strict layout models.

    ``layouts`` is ``PptxToJsonDocument.layouts`` exactly as the runtime emitted
    it; element payloads -- including ``image.data`` URLs -- are never rewritten.

    Raises ``ValueError`` (``pydantic.ValidationError`` is one) naming the layout,
    the element index and the failing constraint whenever a runtime element cannot
    be represented, so an unconvertible deck fails closed instead of importing a
    silently truncated template.
    """

    raw_layouts: list[RawSlideLayout] = []
    slide_layouts: list[SlideLayout] = []
    for layout_index, layout in enumerate(layouts):
        if not isinstance(layout, Mapping):
            raise ValueError(f"runtime_layout_must_be_an_object:{layout_index}")
        layout_id = layout.get("id")
        if not isinstance(layout_id, str):
            raise ValueError(f"runtime_layout_requires_string_id:{layout_index}")
        raw_elements = layout.get("elements")
        if not isinstance(raw_elements, list):
            raise ValueError(f"runtime_layout_requires_elements_array:{layout_id}")

        elements = [
            _validated_element(element, layout_id=layout_id, index=index)
            for index, element in enumerate(raw_elements)
        ]
        description = _layout_description(layout_id, elements)
        raw_layouts.append(
            RawSlideLayout(id=layout_id, description=description, elements=elements)
        )
        slide_layouts.append(
            SlideLayout(
                id=layout_id,
                description=description,
                components=[
                    _component(layout_id, index, element)
                    for index, element in enumerate(elements)
                ],
            )
        )
    return RuntimeImportedLayouts(
        raw_layouts=RawSlideLayouts(layouts=raw_layouts),
        layouts=SlideLayouts(layouts=slide_layouts),
    )


def _validated_element(
    element: Any,
    *,
    layout_id: str,
    index: int,
) -> SlideElement:
    try:
        return _ELEMENT_ADAPTER.validate_python(
            _canonicalize_runtime_element(element)
        )
    except ValidationError as error:
        raise ValueError(
            "runtime_element_not_representable:"
            f"{layout_id}:{index}:{_failure_detail(error)}"
        ) from error


def _canonicalize_runtime_element(element: Any) -> Any:
    """Translate the runtime's sampled ellipse into the strict bounding-pair form."""

    if not isinstance(element, Mapping):
        return element
    if element.get("type") != "vector" or element.get("shape") != "ellipse":
        return element
    points = element.get("points")
    if not isinstance(points, list) or len(points) != 8:
        return element

    coordinates: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, Mapping):
            return element
        x = point.get("x")
        y = point.get("y")
        if (
            isinstance(x, bool)
            or not isinstance(x, (int, float))
            or isinstance(y, bool)
            or not isinstance(y, (int, float))
            or not math.isfinite(float(x))
            or not math.isfinite(float(y))
        ):
            return element
        coordinates.append((float(x), float(y)))

    normalized = copy.deepcopy(dict(element))
    normalized["points"] = [
        {
            "x": min(x for x, _ in coordinates),
            "y": min(y for _, y in coordinates),
        },
        {
            "x": max(x for x, _ in coordinates),
            "y": max(y for _, y in coordinates),
        },
    ]
    return normalized


def _failure_detail(error: ValidationError) -> str:
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "element"
    return f"{location}:{first['msg']}"


def _component(layout_id: str, index: int, element: SlideElement) -> Component:
    # The render plan adds ``component.position`` to the geometry of the elements
    # it wraps, so a zero origin keeps the runtime's absolute slide coordinates
    # untouched -- no arithmetic, no drift.
    return Component(
        id=_component_id(layout_id, index),
        description=_component_description(index, element),
        position=Position(x=0, y=0),
        elements=[element],
    )


def _component_id(layout_id: str, index: int) -> str:
    suffix = f"_component_{index + 1}"
    return f"{layout_id[: _MAX_ID_LENGTH - len(suffix)]}{suffix}"


def _component_description(index: int, element: SlideElement) -> str:
    return (
        f"Runtime PPTX import: component {index + 1} wraps a single "
        f"{element.type} element in absolute slide coordinates."
    )[:_MAX_DESCRIPTION_LENGTH]


def _layout_description(layout_id: str, elements: Sequence[SlideElement]) -> str:
    counts = Counter(element.type for element in elements)
    breakdown = ", ".join(
        f"{count}x {name}" for name, count in sorted(counts.items())
    )
    summary = f" ({breakdown})" if breakdown else ""
    return (
        f"Runtime PPTX import of layout {layout_id}: "
        f"{len(elements)} element(s){summary}."
    )[:_MAX_DESCRIPTION_LENGTH]


__all__ = [
    "build_runtime_slide_layouts",
    "restore_runtime_default_contents",
    "runtime_default_contents",
]

