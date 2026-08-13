"""Shared recursive schema normalization helpers."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping


JSON_SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
CONTENT_TYPES = {"text", "math", "image", "text-list", "table", "chart", "infographic"}
_REPEATED_SUFFIX = re.compile(r"_\d+(?=_|$)")



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
    return value.replace(suffix, "", 1) if suffix and suffix in value else value


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

