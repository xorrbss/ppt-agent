"""Editable component and repeated-group schema projection."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from templates.v2.schema_common import (
    CONTENT_TYPES,
    _add_property,
    _can_expand_repeated_children,
    _compact,
    _element_name,
    _normalize_repeated_schema,
    _object_schema,
    _repeated_suffix,
    _strip_repeated_suffix,
)
from templates.v2.schema_content import _component_content_schema, _field_title



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
    repeated = _component_repeated_top_level_group_node(elements, path=path)
    if repeated is not None:
        return [repeated]

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
        supports_repeated_children = element_type in {"flex", "grid"} or (
            element_type == "group"
            and all(
                isinstance(child, Mapping) and child.get("type") == "group"
                for child in children
            )
        )
        if supports_repeated_children:
            repeated = _component_repeated_schema(element, child_node_sets)
            if repeated is not None:
                return [(name, repeated)]
        return [(name, _component_object_schema(child_nodes))]
    return []


def _component_repeated_top_level_group_node(
    elements: list[Any],
    *,
    path: str,
) -> tuple[str, dict[str, Any]] | None:
    groups = [dict(element) for element in elements if isinstance(element, Mapping)]
    if (
        len(groups) != len(elements)
        or not groups
        or any(group.get("type") != "group" for group in groups)
    ):
        return None

    node_sets = [
        _component_nodes_for_element(group, path=f"{path}.{index}")
        for index, group in enumerate(groups)
    ]
    repeated = _component_repeated_schema(
        {"type": "group", "children": groups},
        node_sets,
    )
    if repeated is None or not node_sets[0]:
        return None
    first_name = node_sets[0][0][0]
    return _strip_repeated_suffix(
        first_name,
        _repeated_suffix(first_name),
    ), repeated


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
            "minItems": (
                max(1, len(child_node_sets) // 2)
                if element.get("type") == "group"
                else element.get("min_children")
            ),
            "maxItems": (
                len(child_node_sets)
                if element.get("type") == "group"
                else element.get("max_children")
            ),
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


__all__ = [
    "_component_properties_schema",
    "_component_repeated_top_level_group_node",
]
