"""Read-only helpers for inspecting native Template V2 slide UI trees.

Paths intentionally use a small grammar (``components[n].elements[n]`` plus
``children[n]``/``child`` descendants).  Chat arguments never become arbitrary
object traversal expressions.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from copy import deepcopy
from hashlib import sha256
from typing import Any

from templates.v2.persistence import canonicalize_slide_ui

_PATH_SEGMENT = re.compile(
    r"^(?P<key>components|elements|children)(?:\[(?P<index>[0-9]+)\])$"
)
_SINGULAR_SEGMENTS = {"child"}


def slide_ui_revision(ui: dict[str, Any]) -> str:
    """Return a stable optimistic-concurrency token for a canonical UI tree."""

    canonical = canonicalize_slide_ui(ui)
    if canonical is None:
        raise ValueError("slide_ui_required")
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def parse_element_path(path: str) -> list[tuple[str, int | None]]:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("element_path_required")
    segments: list[tuple[str, int | None]] = []
    for raw_segment in path.strip().split("."):
        match = _PATH_SEGMENT.fullmatch(raw_segment)
        if match:
            segments.append((match.group("key"), int(match.group("index"))))
            continue
        if raw_segment in _SINGULAR_SEGMENTS:
            segments.append((raw_segment, None))
            continue
        raise ValueError(f"element_path_segment_not_allowed:{raw_segment}")
    if len(segments) < 2 or segments[0][0] != "components":
        raise ValueError("element_path_must_start_with_component")
    if segments[1][0] != "elements":
        raise ValueError("element_path_must_select_component_element")
    return segments


def resolve_element_path(
    ui: dict[str, Any],
    path: str,
) -> tuple[dict[str, Any], list[Any], int | str]:
    """Return the selected element and its parent container/key."""

    current: Any = ui
    parent: list[Any] | dict[str, Any] | None = None
    key: int | str | None = None
    for segment_key, index in parse_element_path(path):
        if not isinstance(current, dict):
            raise TypeError("element_path_traverses_non_object")
        if segment_key == "child":
            child = current.get("child")
            if not isinstance(child, dict):
                raise ValueError("element_path_child_not_found")
            parent = current
            key = "child"
            current = child
            continue
        collection = current.get(segment_key)
        if not isinstance(collection, list) or index is None:
            raise ValueError(f"element_path_collection_not_found:{segment_key}")
        if index >= len(collection):
            raise ValueError(f"element_path_index_out_of_range:{segment_key}[{index}]")
        parent = collection
        key = index
        current = collection[index]
    if not isinstance(current, dict) or parent is None or key is None:
        raise ValueError("element_path_did_not_resolve_element")
    return current, parent, key


def _walk_element(
    element: dict[str, Any],
    path: str,
) -> Iterator[tuple[str, dict[str, Any]]]:
    yield path, element
    child = element.get("child")
    if isinstance(child, dict):
        yield from _walk_element(child, f"{path}.child")
    children = element.get("children")
    if isinstance(children, list):
        for index, nested in enumerate(children):
            if isinstance(nested, dict):
                yield from _walk_element(nested, f"{path}.children[{index}]")


def iter_elements(
    ui: dict[str, Any],
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    components = ui.get("components")
    if not isinstance(components, list):
        return
    for component_index, component in enumerate(components):
        if not isinstance(component, dict):
            continue
        component_id = str(component.get("id") or f"component-{component_index}")
        elements = component.get("elements")
        if not isinstance(elements, list):
            continue
        for element_index, element in enumerate(elements):
            if not isinstance(element, dict):
                continue
            base = (
                f"components[{component_index}].elements[{element_index}]"
            )
            for path, nested in _walk_element(element, base):
                yield component_id, path, nested


def compact_slide_ui(
    ui: dict[str, Any],
    *,
    include_full_json: bool = False,
) -> dict[str, Any]:
    canonical = canonicalize_slide_ui(ui)
    if canonical is None:
        raise ValueError("slide_ui_required")
    elements: list[dict[str, Any]] = []
    for component_id, path, element in iter_elements(canonical):
        entry: dict[str, Any] = {
            "component_id": component_id,
            "path": path,
            "type": element.get("type"),
            "name": element.get("name"),
            "decorative": element.get("decorative"),
        }
        for field in ("position", "size", "rotation"):
            if field in element:
                entry[field] = deepcopy(element[field])
        if element.get("type") == "text":
            entry["text"] = "".join(
                str(run.get("text") or "")
                for run in element.get("runs", [])
                if isinstance(run, dict)
            )
        elif element.get("type") == "text-list":
            entry["items"] = [
                "".join(
                    str(run.get("text") or "")
                    for run in item
                    if isinstance(run, dict)
                )
                for item in element.get("items", [])
                if isinstance(item, list)
            ]
        elif element.get("type") == "image":
            entry["data"] = element.get("data")
            entry["prompt"] = element.get("prompt")
            entry["is_icon"] = element.get("is_icon")
        elif element.get("type") in {"chart", "table", "infographic"}:
            entry["content"] = {
                key: deepcopy(value)
                for key, value in element.items()
                if key
                in {
                    "categories",
                    "series",
                    "columns",
                    "rows",
                    "data",
                    "chart_type",
                    "legend",
                    "data_labels",
                }
            }
        elements.append(entry)

    components = [
        {
            "id": component.get("id"),
            "description": component.get("description"),
            "position": deepcopy(component.get("position")),
            "element_count": len(component.get("elements") or []),
        }
        for component in canonical.get("components", [])
        if isinstance(component, dict)
    ]
    result: dict[str, Any] = {
        "layout_id": canonical.get("id"),
        "description": canonical.get("description"),
        "revision": slide_ui_revision(canonical),
        "component_count": len(components),
        "components": components,
        "editable_count": len(elements),
        "elements": elements,
    }
    if include_full_json:
        result["ui"] = deepcopy(canonical)
    return result
