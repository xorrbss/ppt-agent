"""Create editable-content JSON schemas from Template V2 layouts.

This is the persistence-neutral portion of upstream's schema extractor.  It
walks the recursive element contract and deliberately ignores decorative
elements while retaining nested/repeated editable regions.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from .models.layouts import RawSlideLayout


JSON_SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
CONTENT_TYPES = {"text", "image", "text-list", "table", "chart", "infographic"}
CHART_TYPE_VALUES = [
    "area",
    "bar",
    "bubble",
    "donut",
    "horizontal_bar",
    "horizontal_stacked_bar",
    "line",
    "pie",
    "polar_area",
    "radar",
    "scatter",
    "stacked_bar",
]
_REPEATED_SUFFIX = re.compile(r"_\d+$")


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


def _properties_schema(elements: list[Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for value in elements:
        node = _node_for_element(value)
        if node is not None:
            _add_property(properties, *node)
    return properties


def _node_for_element(value: Any) -> tuple[str, dict[str, Any]] | None:
    try:
        element = _as_mapping(value)
    except ValueError:
        return None
    element_type = element.get("type")

    if element_type == "container":
        child = element.get("child")
        return _node_for_element(child) if child is not None else None

    if element_type in {"flex", "grid", "group"}:
        children = element.get("children")
        if not isinstance(children, list):
            return None
        nodes = [
            node for child in children if (node := _node_for_element(child)) is not None
        ]
        if not nodes:
            return None
        name = _element_name(element)
        if name is None:
            return None

        if element_type in {"flex", "grid"}:
            repeated = _repeated_children_schema(element, nodes)
            if repeated is not None:
                return name, repeated

        properties: dict[str, Any] = {}
        for child_name, child_schema in nodes:
            _add_property(properties, child_name, child_schema)
        schema = _object_schema(properties)
        if element_type in {"flex", "grid"}:
            if element.get("min_children") is not None:
                schema["minProperties"] = element["min_children"]
            if element.get("max_children") is not None:
                schema["maxProperties"] = element["max_children"]
        return name, schema

    if (
        element_type not in CONTENT_TYPES
        or element.get("decorative") is not False
    ):
        return None
    name = _element_name(element)
    if name is None:
        return None
    return name, _content_schema(element)


def _content_schema(element: Mapping[str, Any]) -> dict[str, Any]:
    element_type = element["type"]
    if element_type == "text":
        return _compact(
            {
                "type": "string",
                "minLength": element.get("min_length"),
                "maxLength": element.get("max_length"),
            }
        )
    if element_type == "image":
        prompt_key = "icon_query" if element.get("is_icon") is True else "image_prompt"
        return _object_schema({prompt_key: {"type": "string"}})
    if element_type == "text-list":
        return _compact(
            {
                "type": "array",
                "minItems": element.get("min_items"),
                "maxItems": element.get("max_items"),
                "items": _compact(
                    {
                        "type": "string",
                        "minLength": element.get("min_item_length"),
                        "maxLength": element.get("max_item_length"),
                    }
                ),
            }
        )
    if element_type == "table":
        return _table_content_schema(element)
    if element_type == "chart":
        return _chart_content_schema()
    if element_type == "infographic":
        return _infographic_content_schema()
    raise ValueError(f"unsupported content element type: {element_type}")


def _repeated_children_schema(
    element: Mapping[str, Any],
    nodes: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any] | None:
    if len(nodes) < 2 and not _can_expand_repeated_children(element, len(nodes)):
        return None
    normalized_names = [_REPEATED_SUFFIX.sub("", name) for name, _ in nodes]
    normalized_schemas = [
        _normalize_repeated_schema(
            copy.deepcopy(schema),
            _repeated_suffix(name),
            strip_metadata=True,
        )
        for name, schema in nodes
    ]
    if len(set(normalized_names)) != 1 or any(
        schema != normalized_schemas[0] for schema in normalized_schemas[1:]
    ):
        return None
    return _compact(
        {
            "type": "array",
            "minItems": element.get("min_children", len(nodes)),
            "maxItems": element.get("max_children", len(nodes)),
            "items": normalized_schemas[0],
        }
    )


def _can_expand_repeated_children(
    element: Mapping[str, Any],
    child_count: int,
) -> bool:
    max_children = element.get("max_children")
    return isinstance(max_children, (int, float)) and max_children > child_count


def _repeated_suffix(name: str) -> str | None:
    match = _REPEATED_SUFFIX.search(name)
    return match.group(0) if match else None


def _normalize_repeated_schema(
    value: Any,
    suffix: str | None,
    *,
    strip_metadata: bool,
) -> Any:
    if isinstance(value, list):
        return [
            _normalize_repeated_schema(
                item,
                suffix,
                strip_metadata=strip_metadata,
            )
            for item in value
        ]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, nested in value.items():
        if strip_metadata and key in {"title", "description", "x-element-path"}:
            continue
        if key == "properties" and isinstance(nested, dict):
            normalized[key] = {
                _strip_repeated_suffix(property_name, suffix): (
                    _normalize_repeated_schema(
                        property_schema,
                        suffix,
                        strip_metadata=strip_metadata,
                    )
                )
                for property_name, property_schema in nested.items()
            }
            continue
        if key == "required" and isinstance(nested, list):
            normalized[key] = [
                _strip_repeated_suffix(item, suffix)
                for item in nested
                if isinstance(item, str)
            ]
            continue
        normalized[key] = _normalize_repeated_schema(
            nested,
            suffix,
            strip_metadata=strip_metadata,
        )
    return normalized


def _strip_repeated_suffix(value: str, suffix: str | None) -> str:
    return value[: -len(suffix)] if suffix and value.endswith(suffix) else value


def _add_property(
    properties: dict[str, Any],
    name: str,
    schema: dict[str, Any],
) -> None:
    candidate = name
    suffix = 2
    while candidate in properties:
        candidate = f"{name}_{suffix}"
        suffix += 1
    properties[candidate] = schema


def _element_name(element: Mapping[str, Any]) -> str | None:
    name = element.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _object_schema(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties) if required is None else required,
    }


def _compact(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json", exclude_none=True)
    raise ValueError("Template V2 value must be an object")


def _strip_schema_metadata(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_schema_metadata(item) for item in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    stripped: dict[str, Any] = {}
    for key, item in value.items():
        if key in {
            "$schema",
            "title",
            "description",
            "x-element-type",
            "x-element-path",
        }:
            continue
        # Property names are user-defined editor slot identifiers. A slot can
        # legitimately be named "title" or "description"; only metadata keys
        # on each schema object are removed.
        if key == "properties" and isinstance(item, dict):
            stripped[key] = {
                property_name: _strip_schema_metadata(property_schema)
                for property_name, property_schema in item.items()
            }
            continue
        stripped[key] = _strip_schema_metadata(item)
    return stripped


def _strip_titles(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_titles(item)
            for key, item in value.items()
            if key not in {"title", "description"}
        }
    if isinstance(value, list):
        return [_strip_titles(item) for item in value]
    return value


def _component_properties_schema(elements: list[Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for name, schema in _component_nodes_for_elements(elements):
        _add_property(properties, name, schema)
    return properties


def _component_nodes_for_elements(
    elements: list[Any],
    *,
    path: str = "elements",
) -> list[tuple[str, dict[str, Any]]]:
    nodes: list[tuple[str, dict[str, Any]]] = []
    for index, value in enumerate(elements):
        if isinstance(value, Mapping):
            nodes.extend(
                _component_nodes_for_element(
                    dict(value),
                    path=f"{path}.{index}",
                )
            )
    return nodes


def _component_nodes_for_element(
    element: dict[str, Any],
    *,
    path: str,
) -> list[tuple[str, dict[str, Any]]]:
    element_type = element.get("type")
    name = _element_name(element)

    if (
        element_type in CONTENT_TYPES
        and element.get("decorative") is False
        and name is not None
    ):
        return [(name, _component_content_schema(element, name=name, path=path))]

    if element_type == "container":
        child = element.get("child")
        child_nodes = (
            _component_nodes_for_element(dict(child), path=f"{path}.child")
            if isinstance(child, Mapping)
            else []
        )
        if name is None or not child_nodes:
            return child_nodes
        return [(name, _component_object_schema(child_nodes))]

    if element_type in {"flex", "grid", "group"}:
        children = element.get("children")
        if not isinstance(children, list):
            return []
        child_node_sets = [
            _component_nodes_for_element(
                dict(child),
                path=f"{path}.children.{index}",
            )
            if isinstance(child, Mapping)
            else []
            for index, child in enumerate(children)
        ]
        child_nodes = [node for node_set in child_node_sets for node in node_set]
        if name is None or not child_nodes:
            return child_nodes
        if element_type in {"flex", "grid"}:
            repeated = _component_repeated_schema(element, child_node_sets)
            if repeated is not None:
                return [(name, repeated)]
        return [(name, _component_object_schema(child_nodes))]
    return []


def _component_object_schema(
    nodes: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for name, schema in nodes:
        _add_property(properties, name, schema)
    return _object_schema(properties)


def _component_repeated_schema(
    element: Mapping[str, Any],
    child_node_sets: list[list[tuple[str, dict[str, Any]]]],
) -> dict[str, Any] | None:
    if any(not node_set for node_set in child_node_sets):
        return None
    if len(child_node_sets) < 2 and not _can_expand_repeated_children(
        element,
        len(child_node_sets),
    ):
        return None

    normalized_items: list[dict[str, Any]] = []
    for node_set in child_node_sets:
        suffix = _repeated_suffix(node_set[0][0])
        item_schema = (
            node_set[0][1]
            if len(node_set) == 1 and node_set[0][1].get("type") == "object"
            else _component_object_schema(node_set)
        )
        normalized = _normalize_repeated_schema(
            copy.deepcopy(item_schema),
            suffix,
            strip_metadata=False,
        )
        normalized = _remove_element_paths(normalized)
        _retitle_properties(normalized)
        normalized_items.append(normalized)

    comparable = [_comparable_schema(item) for item in normalized_items]
    if any(item != comparable[0] for item in comparable[1:]):
        return None
    return _compact(
        {
            "type": "array",
            "minItems": element.get("min_children"),
            "maxItems": element.get("max_children"),
            "items": normalized_items[0],
        }
    )


def _remove_element_paths(value: Any) -> Any:
    if isinstance(value, list):
        return [_remove_element_paths(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _remove_element_paths(item)
        for key, item in value.items()
        if key != "x-element-path"
    }


def _retitle_properties(value: Any) -> None:
    if not isinstance(value, dict):
        return
    properties = value.get("properties")
    if isinstance(properties, dict):
        for name, schema in properties.items():
            if isinstance(schema, dict) and "title" in schema:
                schema["title"] = _field_title(name)
            _retitle_properties(schema)
    for nested in value.values():
        if isinstance(nested, (dict, list)):
            if isinstance(nested, list):
                for item in nested:
                    _retitle_properties(item)
            else:
                _retitle_properties(nested)


def _comparable_schema(value: Any, key: str = "") -> Any:
    if isinstance(value, list):
        items = [_comparable_schema(item) for item in value]
        return sorted(items) if key in {"enum", "required"} else items
    if not isinstance(value, dict):
        return value
    return {
        nested_key: _comparable_schema(value[nested_key], nested_key)
        for nested_key in sorted(value)
        if nested_key != "x-element-path"
    }


def _component_content_schema(
    element: Mapping[str, Any],
    *,
    name: str,
    path: str,
) -> dict[str, Any]:
    element_type = str(element["type"])
    if element_type == "text":
        schema: dict[str, Any] = {
            "type": "string",
            "minLength": element.get("min_length"),
            "maxLength": element.get("max_length"),
        }
    elif element_type == "image":
        prompt_key = "icon_query" if element.get("is_icon") is True else "image_prompt"
        prompt_description = (
            "Search query for the replacement icon."
            if element.get("is_icon") is True
            else "Prompt for the replacement image."
        )
        schema = _object_schema(
            {
                prompt_key: {
                    "type": "string",
                    "description": prompt_description,
                }
            }
        )
    elif element_type == "text-list":
        schema = {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": element.get("min_item_length"),
                "maxLength": element.get("max_item_length"),
            },
            "minItems": element.get("min_items"),
            "maxItems": element.get("max_items"),
        }
    elif element_type == "table":
        schema = _table_content_schema(element)
    elif element_type == "chart":
        schema = _chart_content_schema()
    elif element_type == "infographic":
        schema = _infographic_content_schema()
    else:
        schema = {}
    return {
        **_compact_nested(schema),
        "title": _field_title(name),
        "x-element-type": element_type,
        "x-element-path": path,
    }


def _field_title(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split("_") if part) or name


def _infographic_data_schema(infographic_type: str) -> dict[str, Any]:
    return _object_schema(
        {
            "type": {"const": infographic_type},
            "min_value": {"type": "number"},
            "max_value": {"type": "number"},
            "value": {"type": "number"},
        }
    )


def _infographic_content_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "data": {
                "oneOf": [
                    _infographic_data_schema("progress_bar"),
                    _infographic_data_schema("gauge"),
                ]
            },
            "colors": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
        required=["data"],
    )


def _table_content_schema(element: Mapping[str, Any]) -> dict[str, Any]:
    """Use one table content contract for raw layouts and editable components."""

    schema = _compact_nested(
        _object_schema(
            {
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": element.get("min_columns"),
                    "maxItems": element.get("max_columns"),
                },
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": element.get("min_columns"),
                        "maxItems": element.get("max_columns"),
                    },
                    "minItems": element.get("min_rows"),
                    "maxItems": element.get("max_rows"),
                },
            }
        )
    )
    min_columns = element.get("min_columns")
    max_columns = element.get("max_columns")
    if (
        isinstance(min_columns, int)
        and not isinstance(min_columns, bool)
        and isinstance(max_columns, int)
        and not isinstance(max_columns, bool)
        and 0 <= min_columns <= max_columns
    ):
        schema["oneOf"] = [
            {
                "properties": {
                    "columns": {
                        "minItems": column_count,
                        "maxItems": column_count,
                    },
                    "rows": {
                        "items": {
                            "minItems": column_count,
                            "maxItems": column_count,
                        }
                    },
                }
            }
            for column_count in range(min_columns, max_columns + 1)
        ]
    return schema


def _chart_content_schema() -> dict[str, Any]:
    schema = _object_schema(
        {
            "chart_type": {"type": "string", "enum": CHART_TYPE_VALUES},
            "title": {"type": ["string", "null"]},
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 24,
            },
            "series": {
                "type": "array",
                "items": _object_schema(
                    {
                        "name": {"type": "string"},
                        "values": {
                            "type": "array",
                            "items": {"type": "number"},
                            "maxItems": 24,
                        },
                    }
                ),
                "maxItems": 12,
            },
        },
        required=["chart_type", "categories", "series"],
    )
    schema["allOf"] = [
        {
            "if": {
                "properties": {
                    "chart_type": {"enum": ["pie", "donut"]},
                },
                "required": ["chart_type"],
            },
            "then": {
                "properties": {
                    "series": {"maxItems": 1},
                }
            },
        },
        {
            "oneOf": [
                {
                    "properties": {
                        "categories": {
                            "minItems": item_count,
                            "maxItems": item_count,
                        },
                        "series": {
                            "items": {
                                "properties": {
                                    "values": {
                                        "minItems": item_count,
                                        "maxItems": item_count,
                                    }
                                }
                            }
                        },
                    }
                }
                for item_count in range(25)
            ]
        },
    ]
    return schema


def _compact_nested(value: Any) -> Any:
    if isinstance(value, list):
        return [_compact_nested(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _compact_nested(item)
            for key, item in value.items()
            if item is not None
        }
    return value
