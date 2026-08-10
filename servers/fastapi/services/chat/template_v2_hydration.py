"""Fail-closed hydration of Chat content into native Template V2 slide UI."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from templates.v2.persistence import canonicalize_slide_ui
from templates.v2.ast import (
    compile_template_render_plan,
    decode_template_ast,
    materialize_render_plan,
)


class TemplateV2HydrationError(ValueError):
    """Raised when a layout cannot be resolved or hydrated canonically."""


@dataclass(frozen=True)
class HydratedTemplateV2State:
    """A fully validated state that is safe to apply to a SlideModel."""

    content: dict[str, Any]
    ui: dict[str, Any]


def resolve_template_v2_layout(
    layout_payload: dict[str, Any], layout_id: str
) -> dict[str, Any]:
    """Return a detached layout by id from a Template V2 layouts payload."""
    layouts = layout_payload.get("layouts")
    if isinstance(layouts, dict):
        layouts = layouts.get("layouts")
    if not isinstance(layouts, list):
        raise TemplateV2HydrationError("Template V2 payload has no layouts list")
    for layout in layouts:
        if isinstance(layout, dict) and str(layout.get("id")) == str(layout_id):
            return copy.deepcopy(layout)
    raise TemplateV2HydrationError(f"Template V2 layout not found: {layout_id}")


def hydrate_template_v2_slide(
    source_layout: dict[str, Any],
    content: dict[str, Any],
) -> HydratedTemplateV2State:
    """Clone, hydrate and strictly canonicalize a layout without mutating inputs."""
    if not isinstance(content, dict):
        raise TemplateV2HydrationError("Template V2 content must be an object")
    ui = copy.deepcopy(source_layout)
    try:
        layout_id = str(ui.get("id") or "hydrated_layout")
        ui["id"] = layout_id
        ast = decode_template_ast(
            {"layouts": [ui]},
            source_version="a3352f3c-compatible",
        )
        plan = compile_template_render_plan(
            ast,
            layout_id=layout_id,
            content=content,
        )
        ui = materialize_render_plan(
            plan,
            item_hydrator=_hydrate_repeat_item,
        )
        _apply_content(ui, content)
        canonical = canonicalize_slide_ui(ui)
    except Exception as exc:
        raise TemplateV2HydrationError(
            f"Template V2 hydration produced invalid native UI: {exc}"
        ) from exc
    if canonical is None:
        raise TemplateV2HydrationError("Template V2 hydration produced empty UI")
    return HydratedTemplateV2State(
        content=copy.deepcopy(content),
        ui=canonical,
    )


def _hydrate_repeat_item(source: dict[str, Any], value: Any) -> dict[str, Any]:
    _apply_elements([source], value, {}, True)
    return source


def apply_hydrated_template_v2_state(
    slide: Any, state: HydratedTemplateV2State
) -> None:
    """Apply an already validated state, restoring the object on assignment failure."""
    previous = (
        copy.deepcopy(getattr(slide, "content", None)),
        copy.deepcopy(getattr(slide, "ui", None)),
        getattr(slide, "html_content", None),
    )
    try:
        slide.content = copy.deepcopy(state.content)
        slide.html_content = None
        slide.ui = copy.deepcopy(state.ui)
    except Exception:
        slide.content, slide.ui, slide.html_content = previous
        raise


def _apply_content(ui: dict[str, Any], content: dict[str, Any]) -> None:
    components = ui.get("components")
    if not isinstance(components, list):
        return
    totals: dict[str, int] = {}
    for component in components:
        if isinstance(component, dict):
            component_id = str(component.get("id") or "")
            totals[component_id] = totals.get(component_id, 0) + 1
    seen: dict[str, int] = {}
    used: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            continue
        component_id = str(component.get("id") or "")
        occurrence = seen.get(component_id, 0)
        seen[component_id] = occurrence + 1
        keys = [component_id]
        if totals.get(component_id, 0) > 1:
            keys.insert(0, f"{component_id}_{occurrence}")
        component_content = None
        for key in keys:
            if key not in used and isinstance(content.get(key), dict):
                component_content = content[key]
                used.add(key)
                break
        if component_content is not None:
            _apply_elements(component.get("elements"), component_content, {})


def _apply_elements(
    elements: Any, content: Any, occurrences: dict[str, int], direct: bool = False
) -> None:
    if not isinstance(elements, list):
        return
    values = content if isinstance(content, dict) else {}
    for element in elements:
        if not isinstance(element, dict):
            continue
        name = element.get("name")
        found = False
        value = None
        if isinstance(name, str):
            index = occurrences.get(name, 0)
            occurrences[name] = index + 1
            preferred = f"{name}_{index + 1}" if index else None
            for key in ([preferred] if preferred else []) + _name_candidates(name):
                if key in values:
                    found, value = True, values[key]
                    break
        generated = element.get("type") in {
            "text",
            "math",
            "image",
            "text-list",
            "table",
            "chart",
            "infographic",
        }
        if element.get("decorative") is False and generated and (found or direct):
            _set_value(element, value if found else content)
            continue
        nested = value if found and isinstance(value, dict) else values
        child = element.get("child")
        if isinstance(child, dict):
            _apply_elements([child], nested, {}, direct and not found)
        children = element.get("children")
        if isinstance(children, list):
            if found and isinstance(value, list) and children:
                replacements = []
                for index, item in enumerate(value):
                    clone = copy.deepcopy(children[min(index, len(children) - 1)])
                    _apply_elements([clone], item, {}, True)
                    replacements.append(clone)
                element["children"] = replacements
            else:
                _apply_elements(children, nested, occurrences, direct and not found)


def _name_candidates(name: str) -> list[str]:
    no_number = re.sub(r"_\d+(?=_|$)", "", name)
    no_prefix = no_number.split("_", 1)[1] if "_" in no_number else no_number
    return list(dict.fromkeys((name, no_number, no_prefix)))


def _set_value(element: dict[str, Any], value: Any) -> None:
    kind = element.get("type")
    if kind == "text":
        text = _text(value)
        if text:
            element["runs"] = _runs(element.get("runs"), text, element.get("font"))
    elif kind == "math":
        latex = (_text(value) or "").strip()
        if latex.startswith("$$") and latex.endswith("$$") and len(latex) > 4:
            latex = latex[2:-2].strip()
        elif latex.startswith(r"\[") and latex.endswith(r"\]") and len(latex) > 4:
            latex = latex[2:-2].strip()
        if latex:
            element["latex"] = latex[:4000]
    elif kind == "text-list" and isinstance(value, list):
        old = element.get("items") if isinstance(element.get("items"), list) else []
        element["items"] = [
            _runs(
                old[i] if i < len(old) else None, _text(item) or "", element.get("font")
            )
            for i, item in enumerate(value)
        ]
    elif kind == "image":
        url = _asset_url(value)
        if url:
            element["data"] = url
            if _ordinary_raster(element, url):
                element["fit"] = "cover"
            prompt = _asset_prompt(value, element.get("is_icon") is True)
            if prompt:
                element["prompt"] = prompt
    elif kind == "table":
        _set_table(element, value)
    elif kind == "chart" and isinstance(value, dict):
        for key in (
            "chart_type",
            "title",
            "title_color",
            "legend_color",
            "colors",
            "x_axis",
            "y_axis",
            "x_axis_title",
            "y_axis_title",
            "axis_color",
            "categories",
            "series",
            "data_labels",
            "legend",
            "x_axis_grid",
            "y_axis_grid",
            "grid_color",
            "source",
        ):
            if key in value:
                element[key] = copy.deepcopy(value[key])
    elif kind == "infographic" and isinstance(value, dict):
        data = value.get("data")
        if isinstance(data, dict) and data.get("type") in {"progress_bar", "gauge"}:
            numeric = {
                key: data.get(key) for key in ("min_value", "max_value", "value")
            }
            if all(
                isinstance(number, (int, float)) and not isinstance(number, bool)
                for number in numeric.values()
            ):
                element["data"] = {
                    "type": data["type"],
                    **{key: float(number) for key, number in numeric.items()},
                }
        if isinstance(value.get("colors"), list):
            element["colors"] = [
                color
                for color in value["colors"]
                if isinstance(color, str) and color.strip()
            ]


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        return _text(value.get("text"))
    return None


def _runs(existing: Any, text: str, font: Any) -> list[dict[str, Any]]:
    run = (
        copy.deepcopy(existing[0])
        if isinstance(existing, list) and existing and isinstance(existing[0], dict)
        else {"text": ""}
    )
    run["text"] = text
    if "font" not in run and isinstance(font, dict):
        run["font"] = copy.deepcopy(font)
    return [run]


def _asset_url(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in (
            "data",
            "url",
            "image_url",
            "icon_url",
            "__image_url__",
            "__icon_url__",
        ):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
    return None


def _asset_prompt(value: Any, icon: bool) -> str | None:
    if not isinstance(value, dict):
        return None
    keys = (
        ("icon_query", "__icon_query__", "query", "prompt", "source")
        if icon
        else ("image_prompt", "__image_prompt__", "prompt", "query", "source")
    )
    return next(
        (
            value[key].strip()
            for key in keys
            if isinstance(value.get(key), str) and value[key].strip()
        ),
        None,
    )


def _ordinary_raster(element: dict[str, Any], url: str) -> bool:
    suffix = PurePosixPath(urlsplit(url).path.lower()).suffix
    return (
        element.get("is_icon") is not True
        and not element.get("clip_path")
        and suffix != ".svg"
        and not url.lower().startswith("data:image/svg")
    )


def _set_table(element: dict[str, Any], value: Any) -> None:
    if isinstance(value, list):
        value = {"rows": value}
    if not isinstance(value, dict):
        return
    old_columns = element.get("columns")
    old_rows = element.get("rows")
    old_shape = (
        (
            len(old_columns),
            tuple(
                len(row) if isinstance(row, list) else -1
                for row in old_rows
            ),
        )
        if isinstance(old_columns, list) and isinstance(old_rows, list)
        else None
    )
    for key in ("columns", "rows"):
        rows = value.get(key)
        if not isinstance(rows, list):
            continue
        old_rows = element.get(key) if isinstance(element.get(key), list) else []
        if key == "columns":
            element[key] = _cells(old_rows, rows)
        else:
            element[key] = [
                _cells(
                    old_rows[i]
                    if i < len(old_rows) and isinstance(old_rows[i], list)
                    else [],
                    row,
                )
                for i, row in enumerate(rows)
                if isinstance(row, list)
            ]
    new_columns = element.get("columns")
    new_rows = element.get("rows")
    new_shape = (
        (
            len(new_columns),
            tuple(
                len(row) if isinstance(row, list) else -1
                for row in new_rows
            ),
        )
        if isinstance(new_columns, list) and isinstance(new_rows, list)
        else None
    )
    if old_shape != new_shape:
        for row in [new_columns, *(new_rows or [])]:
            if not isinstance(row, list):
                continue
            for cell in row:
                if isinstance(cell, dict):
                    cell.pop("rowspan", None)
                    cell.pop("colspan", None)
                    cell.pop("covered_by", None)


def _cells(old: list[Any], values: list[Any]) -> list[dict[str, Any]]:
    fallback = old[-1] if old else {}
    result = []
    for index, value in enumerate(values):
        cell = copy.deepcopy(old[index] if index < len(old) else fallback)
        if not isinstance(cell, dict):
            cell = {}
        cell["runs"] = _runs(cell.get("runs"), _text(value) or "", cell.get("font"))
        result.append(cell)
    return result
