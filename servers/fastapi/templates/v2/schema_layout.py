"""Raw-layout recursive schema projection."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from templates.v2.schema_common import (
    CONTENT_TYPES,
    _REPEATED_SUFFIX,
    _add_property,
    _as_mapping,
    _can_expand_repeated_children,
    _compact,
    _element_name,
    _normalize_repeated_schema,
    _object_schema,
    _repeated_suffix,
)
from templates.v2.schema_content import _content_schema



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


__all__ = ["_properties_schema"]
