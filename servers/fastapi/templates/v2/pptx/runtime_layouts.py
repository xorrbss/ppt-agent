"""Convert the export runtime's ``pptx-to-json`` layouts into fork Template V2 models.

The in-repo OOXML parser (:mod:`.ooxml_parser` -> :mod:`.assembler`) produces
``CandidateAnalysis`` shapes and loses per-run text styling, pictures and vector
geometry.  The bundled export runtime's ``pptx-to-json`` task instead emits
payloads already shaped like :class:`RawSlideLayout` -- id, description and a flat
list of element objects drawn from the same upstream element schema this fork
models strictly.

Every runtime element is validated against the fork's 11-member discriminated
union. A separate conservative classifier joins the runtime shapes to an OOXML
placeholder evidence sidecar and may change only ``decorative=True`` to ``False``
for structurally proven text/image slots. Every other element field remains
verbatim. That preserves run-level
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
import hashlib
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
from templates.v2.pptx.placeholder_evidence import RuntimePlaceholderEvidence


# ``SlideLayout.id`` / ``Component.id`` are both bounded at 80 characters.
_MAX_ID_LENGTH = 80
# ``Component.description`` / ``SlideLayout.description`` are bounded at 300.
_MAX_DESCRIPTION_LENGTH = 300

_ELEMENT_ADAPTER: TypeAdapter[SlideElement] = TypeAdapter(SlideElement)

# Legacy decks may lack readable OOXML sidecar evidence. These fallback patterns
# are deliberately narrower than a general "content-looking" heuristic and are
# never consulted when structural evidence is available.
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
_AUTO_TEXT_PLACEHOLDERS = frozenset({"title", "ctrTitle", "subTitle", "body"})
_MAX_REVIEW_ITEMS = 200
_MAX_SLOT_EVIDENCE = 500


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
    structural_match_count: int = 0
    structural_fillable_count: int = 0
    legacy_name_fallback_count: int = 0
    review_item_count: int = 0
    slot_evidence_count: int = 0
    review_items: tuple[dict[str, Any], ...] = ()
    slot_evidence: tuple[dict[str, Any], ...] = ()

    def as_manifest(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "strategy": self.strategy,
            "fillable_element_count": self.fillable_element_count,
            "text_placeholder_count": self.text_placeholder_count,
            "image_placeholder_count": self.image_placeholder_count,
            "structural_match_count": self.structural_match_count,
            "structural_fillable_count": self.structural_fillable_count,
            "legacy_name_fallback_count": self.legacy_name_fallback_count,
            "review_item_count": self.review_item_count,
            "review_items_omitted": max(
                0, self.review_item_count - len(self.review_items)
            ),
            "review_items": list(self.review_items),
            "slot_evidence_count": self.slot_evidence_count,
            "slot_evidence_omitted": max(
                0, self.slot_evidence_count - len(self.slot_evidence)
            ),
            "slot_evidence": list(self.slot_evidence),
            "visual_evidence": {
                "status": "not_evaluated",
                "reason": "semantic_visual_provider_not_configured",
            },
        }


def classify_runtime_fillable_layouts(
    layouts: Sequence[Mapping[str, Any]],
    placeholder_evidence: RuntimePlaceholderEvidence | None = None,
) -> tuple[list[dict[str, Any]], RuntimeFillableClassification]:
    """Promote only structurally proven runtime text/image placeholders.

    Returned layouts are deep copies; converter output and caller-owned fixtures
    are never mutated. OOXML placeholder evidence wins whenever it is available;
    the legacy name allowlist is used only when the sidecar is unavailable.
    """

    classified = copy.deepcopy(list(layouts))
    counts = {"text": 0, "image": 0}
    stats = {
        "matched": 0,
        "structural": 0,
        "legacy": 0,
        "review_total": 0,
        "slot_total": 0,
    }
    reviews: list[dict[str, Any]] = []
    slots: list[dict[str, Any]] = []
    structural = (
        placeholder_evidence is not None
        and placeholder_evidence.status == "available"
    )
    for layout_index, layout in enumerate(classified):
        elements = layout.get("elements") if isinstance(layout, dict) else None
        if not isinstance(elements, list):
            continue
        slide_evidence = (
            [
                shape
                for shape in placeholder_evidence.shapes
                if shape.slide_index == layout_index + 1
            ]
            if structural and placeholder_evidence
            else []
        )
        for element_index, element in enumerate(elements):
            _classify_element(
                element,
                counts,
                stats,
                reviews,
                slots,
                slide_index=layout_index + 1,
                element_path=str(element_index),
                structural=structural,
                slide_evidence=slide_evidence,
            )
    return classified, RuntimeFillableClassification(
        version=2,
        strategy="ooxml-placeholder-structure-with-legacy-name-fallback",
        fillable_element_count=counts["text"] + counts["image"],
        text_placeholder_count=counts["text"],
        image_placeholder_count=counts["image"],
        structural_match_count=stats["matched"],
        structural_fillable_count=stats["structural"],
        legacy_name_fallback_count=stats["legacy"],
        review_item_count=stats["review_total"],
        slot_evidence_count=stats["slot_total"],
        review_items=tuple(reviews),
        slot_evidence=tuple(slots),
    )


def _classify_element(
    element: Any,
    counts: dict[str, int],
    stats: dict[str, int],
    reviews: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    *,
    slide_index: int,
    element_path: str,
    structural: bool,
    slide_evidence: list[Any],
) -> None:
    if not isinstance(element, dict):
        return
    element_type = element.get("type")
    name = element.get("name")
    if element_type in {"text", "image"}:
        slot_id = _slot_id(slide_index, element_path, element_type, name)
        if structural:
            matched, confidence, reason = _match_structural_evidence(
                element, slide_evidence
            )
            fillable = False
            placeholder_type = None
            if matched is not None:
                stats["matched"] += 1
                placeholder_type = matched.resolved_type
                if matched.status == "resolved":
                    fillable = (
                        element_type == "text"
                        and placeholder_type in _AUTO_TEXT_PLACEHOLDERS
                    ) or (element_type == "image" and placeholder_type == "pic")
                    reason = (
                        "structural_placeholder_allowlisted"
                        if fillable
                        else "structural_placeholder_requires_review"
                    )
                else:
                    reason = matched.reason
            if fillable and element.get("decorative") is True:
                element["decorative"] = False
                counts[element_type] += 1
                stats["structural"] += 1
            elif not fillable:
                element["decorative"] = True
                _append_bounded(
                    reviews,
                    {
                        "slot_id": slot_id,
                        "slide_index": slide_index,
                        "element_path": element_path,
                        "reason": reason,
                    },
                    limit=_MAX_REVIEW_ITEMS,
                )
                stats["review_total"] += 1
            _append_bounded(
                slots,
                {
                    "slot_id": slot_id,
                    "slide_index": slide_index,
                    "element_path": element_path,
                    "element_type": element_type,
                    "placeholder_type": placeholder_type,
                    "fillable": fillable,
                    "confidence": confidence,
                    "reason": reason,
                },
                limit=_MAX_SLOT_EVIDENCE,
            )
            stats["slot_total"] += 1
        elif (
            element.get("decorative") is True
            and isinstance(name, str)
            and _is_fillable_placeholder_name(element_type, name)
        ):
            element["decorative"] = False
            counts[element_type] += 1
            stats["legacy"] += 1
            _append_bounded(
                reviews,
                {
                    "slot_id": slot_id,
                    "slide_index": slide_index,
                    "element_path": element_path,
                    "reason": "legacy_name_fallback",
                },
                limit=_MAX_REVIEW_ITEMS,
            )
            stats["review_total"] += 1
            _append_bounded(
                slots,
                {
                    "slot_id": slot_id,
                    "slide_index": slide_index,
                    "element_path": element_path,
                    "element_type": element_type,
                    "placeholder_type": None,
                    "fillable": True,
                    "confidence": "low",
                    "reason": "legacy_name_fallback",
                },
                limit=_MAX_SLOT_EVIDENCE,
            )
            stats["slot_total"] += 1

    # The current converter flattens groups. Recurse defensively so a future
    # runtime cannot bypass this fail-safe policy when it preserves nesting.
    child = element.get("child")
    if isinstance(child, dict):
        _classify_element(
            child,
            counts,
            stats,
            reviews,
            slots,
            slide_index=slide_index,
            element_path=f"{element_path}.child",
            structural=structural,
            slide_evidence=slide_evidence,
        )
    children = element.get("children")
    if isinstance(children, list):
        for index, nested in enumerate(children):
            _classify_element(
                nested,
                counts,
                stats,
                reviews,
                slots,
                slide_index=slide_index,
                element_path=f"{element_path}.children.{index}",
                structural=structural,
                slide_evidence=slide_evidence,
            )


def _slot_id(
    slide_index: int, element_path: str, element_type: Any, name: Any
) -> str:
    stable = f"{slide_index}|{element_path}|{element_type}|{_normalized_name(name)}"
    digest = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:12]
    return f"slot_s{slide_index}_{digest}"


def _append_bounded(
    items: list[dict[str, Any]], item: dict[str, Any], *, limit: int
) -> None:
    if len(items) < limit:
        items.append(item)


def _normalized_name(name: Any) -> str:
    if not isinstance(name, str):
        return ""
    return re.sub(r"[^\w]+", "_", name.casefold()).strip("_")


def _runtime_geometry(element: dict[str, Any]) -> dict[str, float] | None:
    position = element.get("position")
    size = element.get("size")
    if not isinstance(position, Mapping) or not isinstance(size, Mapping):
        return None
    values = {
        "x": position.get("x"),
        "y": position.get("y"),
        "width": size.get("width"),
        "height": size.get("height"),
    }
    if not all(isinstance(value, (int, float)) for value in values.values()):
        return None
    return {key: float(value) for key, value in values.items()}


def _geometry_matches(
    left: dict[str, float] | None, right: dict[str, float] | None
) -> bool:
    if left is None or right is None:
        return False
    return all(
        abs(left[key] - right[key]) <= max(2.0, abs(right[key]) * 0.01)
        for key in ("x", "y", "width", "height")
    )


def _match_structural_evidence(
    element: dict[str, Any], slide_evidence: list[Any]
) -> tuple[Any | None, str, str]:
    element_type = element.get("type")
    compatible = [
        candidate
        for candidate in slide_evidence
        if candidate.shape_kind == element_type
    ]
    normalized = _normalized_name(element.get("name"))
    name_matches = [
        candidate
        for candidate in compatible
        if normalized and _normalized_name(candidate.shape_name) == normalized
    ]
    geometry = _runtime_geometry(element)
    geometry_matches = [
        candidate
        for candidate in compatible
        if _geometry_matches(geometry, candidate.geometry)
    ]
    name_match = name_matches[0] if len(name_matches) == 1 else None
    geometry_match = geometry_matches[0] if len(geometry_matches) == 1 else None
    if name_match is not None and geometry_match is not None:
        if name_match == geometry_match:
            return name_match, "high", "unique_name_and_geometry_match"
        return None, "none", "name_geometry_match_conflict"
    if len(name_matches) > 1 or len(geometry_matches) > 1:
        return None, "none", "structural_match_ambiguous"
    if name_match is not None:
        return name_match, "medium", "unique_name_match"
    if geometry_match is not None:
        return geometry_match, "medium", "unique_geometry_match"
    return None, "none", "structural_match_not_found"


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
