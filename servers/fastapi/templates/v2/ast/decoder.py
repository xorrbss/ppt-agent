"""Versioned upstream wire payload decoder for the canonical Template AST."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from templates.v2.wire_codec import ELEMENT_DISCRIMINATORS, decode_wire_layouts

from .models import (
    CanonicalComponent,
    CanonicalLayout,
    CanonicalTemplateAst,
    Geometry,
    RepeatAnchor,
    RepeatNode,
    TemplateCapabilityReport,
)


_REPEATED_SUFFIX = re.compile(r"_\d+(?=_|$)")
_CONTENT_TYPES = {
    "text",
    "math",
    "image",
    "text-list",
    "table",
    "chart",
    "infographic",
}


class TemplateAstDecodeError(ValueError):
    pass


def decode_template_ast(
    payload: str | bytes | bytearray | Mapping[str, Any],
    *,
    source_version: str | None = None,
) -> CanonicalTemplateAst:
    """Decode losslessly, infer the dialect, and lower repeat semantics once."""

    wire = decode_wire_layouts(payload).to_wire_value()
    layouts_value = wire.get("layouts")
    if isinstance(layouts_value, dict):
        layouts_value = layouts_value.get("layouts")
    if not isinstance(layouts_value, list):
        raise TemplateAstDecodeError("template_ast_layouts_required")

    issues: list[str] = []
    layouts: list[CanonicalLayout] = []
    saw_positioned_repeat = False
    for layout_index, layout_value in enumerate(layouts_value):
        if not isinstance(layout_value, dict):
            raise TemplateAstDecodeError("template_ast_layout_must_be_object")
        layout_id = str(layout_value.get("id") or f"layout_{layout_index + 1}")
        components_value = layout_value.get("components")
        if not isinstance(components_value, list):
            raise TemplateAstDecodeError(
                f"template_ast_components_required:{layout_id}"
            )
        components: list[CanonicalComponent] = []
        for component_index, component_value in enumerate(components_value):
            if not isinstance(component_value, dict):
                raise TemplateAstDecodeError(
                    f"template_ast_component_must_be_object:{layout_id}"
                )
            component_id = str(
                component_value.get("id") or f"component_{component_index + 1}"
            )
            component_path = (
                f"layouts.{layout_index}.components.{component_index}"
            )
            repeats = _decode_component_repeats(
                component_value,
                component_id=component_id,
                component_path=component_path,
            )
            saw_positioned_repeat = saw_positioned_repeat or any(
                repeat.arrangement == "positioned" for repeat in repeats
            )
            components.append(
                CanonicalComponent(
                    component_id=component_id,
                    source_path=component_path,
                    source=copy.deepcopy(component_value),
                    repeats=tuple(repeats),
                )
            )
            _collect_unknown_types(
                component_value.get("elements"),
                path=f"{component_path}.elements",
                issues=issues,
            )
        layouts.append(
            CanonicalLayout(
                layout_id=layout_id,
                source_path=f"layouts.{layout_index}",
                source=copy.deepcopy(layout_value),
                components=tuple(components),
            )
        )

    dialect = _dialect(source_version, saw_positioned_repeat, issues)
    semantic = not issues
    capabilities = TemplateCapabilityReport(
        validation=semantic,
        content_schema=semantic,
        hydration=semantic,
        editor_projection=semantic,
        html_render=semantic,
        pptx_compile=semantic,
        review_required=not semantic,
        issues=tuple(dict.fromkeys(issues)),
    )
    return CanonicalTemplateAst(
        source_dialect=dialect,
        source_version=source_version,
        source=copy.deepcopy(wire),
        layouts=tuple(layouts),
        capabilities=capabilities,
    )


def _dialect(
    source_version: str | None, saw_positioned_repeat: bool, issues: list[str]
) -> str:
    if source_version and source_version.startswith("57b194b"):
        return "57b194b"
    if source_version and source_version.startswith("a3352f3c"):
        return "a3352f3c"
    if source_version:
        issues.append(f"unsupported_source_version:{source_version}")
        return "future"
    return "a3352f3c" if saw_positioned_repeat else "57b194b"


def _decode_component_repeats(
    component: dict[str, Any], *, component_id: str, component_path: str
) -> list[RepeatNode]:
    elements = component.get("elements")
    if not isinstance(elements, list):
        return []
    repeats: list[RepeatNode] = []
    positioned = _positioned_repeat(
        elements,
        component_id=component_id,
        path=f"{component_path}.elements",
    )
    if positioned is not None:
        repeats.append(positioned)
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            continue
        repeats.extend(
            _nested_repeats(
                element,
                component_id=component_id,
                path=f"{component_path}.elements.{index}",
            )
        )
    return repeats


def _nested_repeats(
    element: dict[str, Any], *, component_id: str, path: str
) -> list[RepeatNode]:
    repeats: list[RepeatNode] = []
    kind = element.get("type")
    children = element.get("children")
    if kind in {"flex", "grid"} and isinstance(children, list) and children:
        minimum = element.get("min_children")
        maximum = element.get("max_children")
        if isinstance(minimum, int) and isinstance(maximum, int):
            repeats.append(
                RepeatNode(
                    repeat_id=_repeat_id(component_id, path),
                    component_id=component_id,
                    source_path=f"{path}.children",
                    content_field=_content_field(element, children),
                    arrangement="grid" if kind == "grid" else "flow",
                    selection="prototype",
                    min_items=minimum,
                    max_items=maximum,
                    item_signature=_signature(children[0]),
                    source_indices=tuple(range(len(children))),
                    prototype=copy.deepcopy(children[0]),
                    extensions=_extensions(
                        element,
                        {
                            "type",
                            "name",
                            "children",
                            "min_children",
                            "max_children",
                        },
                    ),
                )
            )
    elif kind == "group" and isinstance(children, list):
        positioned = _positioned_repeat(
            children,
            component_id=component_id,
            path=f"{path}.children",
            parent_name=element.get("name"),
        )
        if positioned is not None:
            repeats.append(positioned)
    if isinstance(children, list):
        for index, child in enumerate(children):
            if isinstance(child, dict):
                repeats.extend(
                    _nested_repeats(
                        child,
                        component_id=component_id,
                        path=f"{path}.children.{index}",
                    )
                )
    child = element.get("child")
    if isinstance(child, dict):
        repeats.extend(
            _nested_repeats(
                child,
                component_id=component_id,
                path=f"{path}.child",
            )
        )
    return repeats


def _positioned_repeat(
    values: list[Any],
    *,
    component_id: str,
    path: str,
    parent_name: Any = None,
) -> RepeatNode | None:
    candidates = [
        (index, value)
        for index, value in enumerate(values)
        if isinstance(value, dict) and value.get("type") == "group"
    ]
    if len(candidates) < 2:
        return None
    signature_groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, group in candidates:
        signature_groups.setdefault(_signature(group), []).append((index, group))
    selected = max(signature_groups.values(), key=len)
    if len(selected) < 2:
        return None
    signature = _signature(selected[0][1])
    maximum = len(selected)
    minimum = max(1, maximum // 2)
    field = (
        parent_name.strip()
        if isinstance(parent_name, str) and parent_name.strip()
        else _field_from_group(selected[0][1])
    )
    return RepeatNode(
        repeat_id=_repeat_id(component_id, path),
        component_id=component_id,
        source_path=path,
        content_field=field,
        arrangement="positioned",
        selection="prefix",
        min_items=minimum,
        max_items=maximum,
        item_signature=signature,
        source_indices=tuple(index for index, _group in selected),
        anchors=tuple(
            RepeatAnchor(
                anchor_id=f"{component_id}:{index}",
                source_index=index,
                geometry=_geometry(group),
                source=copy.deepcopy(group),
            )
            for index, group in selected
        ),
    )


def _content_field(parent: dict[str, Any], children: list[Any]) -> str:
    name = parent.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    first = children[0]
    return _field_from_group(first) if isinstance(first, dict) else "items"


def _field_from_group(group: dict[str, Any]) -> str:
    names: list[str] = []
    _editable_names(group, names)
    if not names:
        return "items"
    normalized = _REPEATED_SUFFIX.sub("", names[0])
    if "_" in normalized:
        normalized = normalized.split("_", 1)[0]
    return normalized if normalized.endswith("s") else f"{normalized}s"


def _editable_names(value: Any, names: list[str]) -> None:
    if isinstance(value, dict):
        if value.get("type") in _CONTENT_TYPES and value.get("decorative") is False:
            name = value.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        for key in ("child", "children"):
            _editable_names(value.get(key), names)
    elif isinstance(value, list):
        for item in value:
            _editable_names(item, names)


def _signature(value: dict[str, Any]) -> str:
    semantic: list[tuple[str, str]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            kind = item.get("type")
            name = item.get("name")
            if kind in _CONTENT_TYPES and item.get("decorative") is False:
                semantic.append((str(kind), _REPEATED_SUFFIX.sub("", str(name or ""))))
            for key in ("child", "children"):
                visit(item.get(key))
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    encoded = json.dumps(semantic, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _geometry(value: dict[str, Any]) -> Geometry:
    position = value.get("position") if isinstance(value.get("position"), dict) else {}
    size = value.get("size") if isinstance(value.get("size"), dict) else {}
    return Geometry(
        x=_number(position.get("x")),
        y=_number(position.get("y")),
        width=_number(size.get("width")),
        height=_number(size.get("height")),
        rotation=_number(value.get("rotation")),
    )


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _repeat_id(component_id: str, path: str) -> str:
    digest = hashlib.sha256(path.encode()).hexdigest()[:12]
    return f"{component_id}:{digest}"


def _extensions(value: dict[str, Any], known: set[str]) -> dict[str, Any]:
    return {key: copy.deepcopy(item) for key, item in value.items() if key not in known}


def _collect_unknown_types(value: Any, *, path: str, issues: list[str]) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _collect_unknown_types(item, path=f"{path}.{index}", issues=issues)
        return
    if not isinstance(value, dict):
        return
    kind = value.get("type")
    if isinstance(kind, str) and kind not in ELEMENT_DISCRIMINATORS:
        issues.append(f"unknown_element_type:{path}:{kind}")
    child = value.get("child")
    if child is not None:
        _collect_unknown_types(child, path=f"{path}.child", issues=issues)
    children = value.get("children")
    if children is not None:
        _collect_unknown_types(children, path=f"{path}.children", issues=issues)


__all__ = ["TemplateAstDecodeError", "decode_template_ast"]
