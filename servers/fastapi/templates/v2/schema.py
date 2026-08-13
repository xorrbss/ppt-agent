"""Create editable-content JSON schemas from Template V2 layouts.

This stable facade coordinates raw-layout and component-specific projections.
"""

from __future__ import annotations

from typing import Any, Mapping

from templates.v2.models.layouts import RawSlideLayout
from templates.v2.schema_common import (
    JSON_SCHEMA_URI,
    _add_property,
    _as_mapping,
    _object_schema,
    _strip_schema_metadata,
)
from templates.v2.schema_component import (
    _component_properties_schema,
    _component_repeated_top_level_group_node,
)
from templates.v2.schema_layout import _properties_schema



def extract_slide_schema_from_layout(layout: RawSlideLayout) -> dict[str, Any]:
    return _object_schema(_properties_schema(layout.elements))


def get_component_schema(component: Any | Mapping[str, Any]) -> dict[str, Any] | None:
    component_data = _as_mapping(component)
    elements = component_data.get("elements")
    if not isinstance(elements, list):
        raise ValueError("component must contain an elements array")
    properties = _component_properties_schema(elements)
    if not properties:
        return None
    return {
        "$schema": JSON_SCHEMA_URI,
        "type": "object",
        "title": component_data.get("id", "component_content"),
        "description": component_data.get("description"),
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def get_repeated_top_level_group_schema_name(elements: list[Any]) -> str | None:
    """Return the content-array name for one repeated positioned group region.

    This is intentionally derived by the same schema projection used during
    generation.  Callers must not infer a repeat from visual similarity alone.
    """

    repeated = _component_repeated_top_level_group_node(
        elements,
        path="elements",
    )
    return repeated[0] if repeated is not None else None


def get_template_schema(
    template_json: Any | Mapping[str, Any],
    *,
    source_file: str = "template.json",
) -> dict[str, Any]:
    template_data = _as_mapping(template_json)
    layouts = template_data.get("layouts")
    if not isinstance(layouts, list):
        raise ValueError("template JSON must contain a layouts array")

    generated_layouts: list[dict[str, Any]] = []
    for index, layout_value in enumerate(layouts, start=1):
        if not isinstance(layout_value, Mapping):
            raise ValueError("each template layout must be an object")
        components = layout_value.get("components")
        if not isinstance(components, list):
            raise ValueError("each template layout must contain components")

        properties: dict[str, Any] = {}
        entries: list[tuple[str, dict[str, Any]]] = []
        for component in components:
            schema = get_component_schema(component)
            if schema is None:
                continue
            component_data = _as_mapping(component)
            component_id = component_data.get("id")
            if not isinstance(component_id, str):
                raise ValueError("component must include a string id")
            entries.append((component_id, schema))

        counts = {
            component_id: sum(
                1 for candidate_id, _schema in entries if candidate_id == component_id
            )
            for component_id, _schema in entries
        }
        indexes: dict[str, int] = {}
        for component_id, schema in entries:
            occurrence = indexes.get(component_id, 0)
            indexes[component_id] = occurrence + 1
            key = (
                f"{component_id}_{occurrence}"
                if counts[component_id] > 1
                else component_id
            )
            _add_property(properties, key, _strip_schema_metadata(schema))

        layout_id = str(layout_value.get("id") or f"layout_{index}")
        content_schema = None
        if properties:
            content_schema = {
                "$schema": JSON_SCHEMA_URI,
                "type": "object",
                "title": layout_id,
                "description": layout_value.get("description"),
                "additionalProperties": False,
                "properties": properties,
                "required": list(properties),
            }
        generated_layouts.append(
            {
                "slide": index,
                "layout_id": layout_value.get("id"),
                "schema": content_schema,
            }
        )

    return {
        "source_file": source_file,
        "layout_count": len(generated_layouts),
        "layouts": generated_layouts,
    }


__all__ = [
    "extract_slide_schema_from_layout",
    "get_component_schema",
    "get_repeated_top_level_group_schema_name",
    "get_template_schema",
]
