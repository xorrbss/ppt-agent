"""Convert the export runtime's ``pptx-to-json`` layouts into fork Template V2 models.

The in-repo OOXML parser (:mod:`.ooxml_parser` -> :mod:`.assembler`) produces
``CandidateAnalysis`` shapes and loses per-run text styling, pictures and vector
geometry.  The bundled export runtime's ``pptx-to-json`` task instead emits
payloads already shaped like :class:`RawSlideLayout` -- id, description and a flat
list of element objects drawn from the same upstream element schema this fork
models strictly.

So this module performs no element rewriting at all: every runtime element is
validated verbatim against the fork's 11-member discriminated union and kept as
emitted.  That is what preserves run-level ``bold``/``italic``/``size``/``color``,
``image.data`` URLs and ``vector`` ``points``/``fill``/``corner_radii``.  The only
work left is the layout-level shape difference: ``RawSlideLayout`` carries
``elements`` while ``SlideLayout`` carries ``components``.

It is a sibling of :mod:`.assembler` rather than part of it because the two share
no conversion logic -- ``assembler`` maps ``ShapeCandidate`` -> element, and this
maps an already-element-shaped payload -> component.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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


# ``SlideLayout.id`` / ``Component.id`` are both bounded at 80 characters.
_MAX_ID_LENGTH = 80
# ``Component.description`` / ``SlideLayout.description`` are bounded at 300.
_MAX_DESCRIPTION_LENGTH = 300

_ELEMENT_ADAPTER: TypeAdapter[SlideElement] = TypeAdapter(SlideElement)


@dataclass(frozen=True)
class RuntimeImportedLayouts:
    """Both layout projections the Template V2 record persists for an import."""

    raw_layouts: RawSlideLayouts
    layouts: SlideLayouts


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
        return _ELEMENT_ADAPTER.validate_python(element)
    except ValidationError as error:
        raise ValueError(
            "runtime_element_not_representable:"
            f"{layout_id}:{index}:{_failure_detail(error)}"
        ) from error


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
