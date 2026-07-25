"""Convert the export runtime's ``pptx-to-json`` layouts into fork Template V2 models.

The in-repo OOXML parser (:mod:`.ooxml_parser` -> :mod:`.assembler`) produces
``CandidateAnalysis`` shapes and loses per-run text styling, pictures and vector
geometry.  The bundled export runtime's ``pptx-to-json`` task instead emits
payloads already shaped like :class:`RawSlideLayout` -- id, description and a flat
list of element objects drawn from the same upstream element schema this fork
models strictly.

Every runtime element is validated against the fork's 11-member discriminated
union.  A separate, conservative classifier may change only ``decorative=True``
to ``False`` for unambiguous text/image placeholder names; every other element
field remains verbatim.  That preserves run-level
``bold``/``italic``/``size``/``color``, ``image.data`` URLs and vector
``points``/``fill``/``corner_radii``.  The remaining layout-level shape
difference is that ``RawSlideLayout`` carries ``elements`` while ``SlideLayout``
carries ``components``.

It is a sibling of :mod:`.assembler` rather than part of it because the two share
no conversion logic -- ``assembler`` maps ``ShapeCandidate`` -> element, and this
maps an already-element-shaped payload -> component.
"""

from __future__ import annotations

import copy
import re
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

# The runtime does not expose OOXML ``p:ph`` metadata. These patterns are
# deliberately narrower than a general "content-looking" heuristic: accept only
# names PowerPoint assigns to clear text placeholders, or image names that
# explicitly contain ``placeholder``. Ordinary text boxes and pictures remain
# decorative until a reviewer or an OOXML-aware classifier can identify them.
_TEXT_PLACEHOLDER_NAME = re.compile(
    r"^(?:title|subtitle|body)(?:_placeholder)?(?:_\d+)?$"
    r"|^(?:content|text)_placeholder(?:_\d+)?$"
)
_IMAGE_PLACEHOLDER_NAME = re.compile(
    r"^(?:picture|image|photo)_placeholder(?:_\d+)?$"
)
_DECORATIVE_NAME_TOKENS = frozenset(
    {
        "background",
        "bg",
        "brand",
        "date",
        "footer",
        "header",
        "icon",
        "logo",
        "page",
        "slide",
        "watermark",
    }
)


@dataclass(frozen=True)
class RuntimeImportedLayouts:
    """Both layout projections the Template V2 record persists for an import."""

    raw_layouts: RawSlideLayouts
    layouts: SlideLayouts


@dataclass(frozen=True)
class RuntimeFillableClassification:
    """Bounded audit summary for conservative runtime placeholder promotion."""

    version: int
    strategy: str
    fillable_element_count: int
    text_placeholder_count: int
    image_placeholder_count: int

    def as_manifest(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "strategy": self.strategy,
            "fillable_element_count": self.fillable_element_count,
            "text_placeholder_count": self.text_placeholder_count,
            "image_placeholder_count": self.image_placeholder_count,
        }


def classify_runtime_fillable_layouts(
    layouts: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], RuntimeFillableClassification]:
    """Promote only unambiguous runtime text/image placeholders.

    Returned layouts are deep copies; converter output and caller-owned fixtures
    are never mutated. A name must match a strict allowlist and must not contain a
    decorative token. Missing, malformed, ambiguous and future elements remain
    exactly as emitted (normally ``decorative=True``).
    """

    classified = copy.deepcopy(list(layouts))
    counts = {"text": 0, "image": 0}
    for layout in classified:
        elements = layout.get("elements") if isinstance(layout, dict) else None
        if not isinstance(elements, list):
            continue
        for element in elements:
            _classify_element(element, counts)
    return classified, RuntimeFillableClassification(
        version=1,
        strategy="conservative-placeholder-name",
        fillable_element_count=counts["text"] + counts["image"],
        text_placeholder_count=counts["text"],
        image_placeholder_count=counts["image"],
    )


def _classify_element(element: Any, counts: dict[str, int]) -> None:
    if not isinstance(element, dict):
        return
    element_type = element.get("type")
    name = element.get("name")
    if (
        element.get("decorative") is True
        and isinstance(name, str)
        and _is_fillable_placeholder_name(element_type, name)
    ):
        element["decorative"] = False
        counts[element_type] += 1

    # The current converter flattens groups. Recurse defensively so a future
    # runtime cannot bypass this fail-safe policy when it preserves nesting.
    child = element.get("child")
    if isinstance(child, dict):
        _classify_element(child, counts)
    children = element.get("children")
    if isinstance(children, list):
        for nested in children:
            _classify_element(nested, counts)


def _is_fillable_placeholder_name(element_type: Any, name: str) -> bool:
    normalized = re.sub(r"[^\w]+", "_", name.casefold()).strip("_")
    tokens = frozenset(normalized.split("_"))
    if tokens & _DECORATIVE_NAME_TOKENS:
        return False
    if element_type == "text":
        return _TEXT_PLACEHOLDER_NAME.fullmatch(normalized) is not None
    if element_type == "image":
        return _IMAGE_PLACEHOLDER_NAME.fullmatch(normalized) is not None
    return False


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
