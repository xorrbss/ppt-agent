"""Type-aware, fail-closed content/style updates for native slide elements."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from services.chat.slide_ui_geometry import apply_geometry
from templates.v2.models.elements import Table

_TYPE_STYLE_FIELDS: dict[str, set[str]] = {
    "text": {
        "alignment",
        "decorative",
        "fill",
        "font",
        "name",
        "shadow",
        "stroke",
    },
    "math": {
        "alignment",
        "decorative",
        "font",
        "name",
        "opacity",
        "shadow",
    },
    "text-list": {"decorative", "font", "marker", "name"},
    "image": {
        "border_radius",
        "clip_path",
        "color",
        "decorative",
        "fit",
        "flip_h",
        "flip_v",
        "focus_x",
        "focus_y",
        "icon_type",
        "is_icon",
        "name",
        "opacity",
    },
    "chart": {
        "axis_color",
        "colors",
        "data_labels",
        "decorative",
        "grid_color",
        "legend",
        "legend_color",
        "name",
        "title_color",
        "x_axis",
        "x_axis_grid",
        "x_axis_title",
        "y_axis",
        "y_axis_grid",
        "y_axis_title",
    },
    "table": {"decorative", "name"},
    "infographic": {"colors", "decorative", "name"},
    "vector": {"closed", "corner_radii", "curve", "fill", "opacity", "shadow", "shape", "stroke"},
    "container": {
        "alignment",
        "border_radius",
        "fill",
        "padding",
        "shadow",
        "stroke",
    },
    "flex": {"align_items", "column_gap", "direction", "gap", "justify_content", "name", "row_gap", "wrap"},
    "grid": {"align_items", "column_gap", "columns", "gap", "justify_items", "name", "row_gap", "rows"},
    "group": {"name"},
}
_CHART_CONTENT_FIELDS = {"categories", "chart_type", "series", "source", "title"}


def _text_runs(text: str, font: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not isinstance(text, str):
        raise TypeError("text_must_be_string")
    run: dict[str, Any] = {"text": text}
    if font is not None:
        if not isinstance(font, dict):
            raise ValueError("run_font_must_be_object")
        run["font"] = deepcopy(font)
    return [run]


def _bounded_text(element: dict[str, Any], text: str) -> None:
    minimum = int(element.get("min_length", 0))
    maximum = int(element.get("max_length", 200000))
    if not minimum <= len(text) <= maximum:
        raise ValueError(f"text_length_out_of_bounds:{minimum}:{maximum}")
    prior_runs = element.get("runs")
    prior_font = None
    if (
        isinstance(prior_runs, list)
        and prior_runs
        and isinstance(prior_runs[0], dict)
        and isinstance(prior_runs[0].get("font"), dict)
    ):
        prior_font = prior_runs[0]["font"]
    element["runs"] = _text_runs(text, prior_font)


def _normalize_math_latex(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("math_latex_must_be_string")
    latex = value.strip()
    if latex.startswith("$$") and latex.endswith("$$"):
        latex = latex[2:-2].strip()
    elif latex.startswith(r"\[") and latex.endswith(r"\]"):
        latex = latex[2:-2].strip()
    return latex


def _update_math(element: dict[str, Any], content: dict[str, Any]) -> None:
    unknown = set(content) - {"latex", "display_mode"}
    if unknown:
        raise ValueError(
            f"math_content_fields_not_allowed:{','.join(sorted(unknown))}"
        )

    candidate: dict[str, Any] = {}
    if "latex" in content:
        latex = _normalize_math_latex(content["latex"])
        minimum = int(element.get("min_length", 1))
        maximum = int(element.get("max_length", 4000))
        if not minimum <= len(latex) <= maximum:
            raise ValueError(f"math_length_out_of_bounds:{minimum}:{maximum}")
        candidate["latex"] = latex
    if "display_mode" in content:
        display_mode = content["display_mode"]
        if not isinstance(display_mode, bool):
            raise TypeError("math_display_mode_must_be_boolean")
        candidate["display_mode"] = display_mode

    element.update(candidate)


def _update_text_run_range(
    element: dict[str, Any],
    selection: dict[str, Any],
) -> None:
    """Apply a bounded style to a character range while preserving text."""

    if not isinstance(selection, dict) or set(selection) - {"start", "end", "font"}:
        raise ValueError("run_selection_fields_not_allowed")
    start = selection.get("start")
    end = selection.get("end")
    font = selection.get("font")
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not isinstance(font, dict)
    ):
        raise TypeError("run_selection_requires_integer_bounds_and_font")
    runs = element.get("runs")
    if not isinstance(runs, list):
        raise TypeError("text_runs_missing")
    normalized_runs: list[tuple[dict[str, Any], str]] = []
    for run in runs:
        if not isinstance(run, dict):
            raise TypeError("text_runs_must_be_objects")
        run_text = run.get("text")
        if not isinstance(run_text, str):
            raise TypeError("text_run_text_must_be_string")
        normalized_runs.append((run, run_text))
    full_text = "".join(run_text for _, run_text in normalized_runs)
    if start < 0 or end <= start or end > len(full_text):
        raise ValueError("run_selection_out_of_bounds")

    segments: list[dict[str, Any]] = []
    cursor = 0
    for run, run_text in normalized_runs:
        run_start = cursor
        run_end = cursor + len(run_text)
        cursor = run_end

        if run_end <= start or run_start >= end:
            segments.append(deepcopy(run))
            continue

        local_start = max(start, run_start) - run_start
        local_end = min(end, run_end) - run_start
        if local_start:
            prefix = deepcopy(run)
            prefix["text"] = run_text[:local_start]
            segments.append(prefix)

        selected = deepcopy(run)
        selected["text"] = run_text[local_start:local_end]
        selected_font = selected.get("font")
        merged_font = deepcopy(selected_font) if isinstance(selected_font, dict) else {}
        merged_font.update(deepcopy(font))
        selected["font"] = merged_font
        segments.append(selected)

        if local_end < len(run_text):
            suffix = deepcopy(run)
            suffix["text"] = run_text[local_end:]
            segments.append(suffix)

    element["runs"] = segments


def _normalize_asset_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("image_data_required")
    candidate = value.strip()
    if candidate.startswith(("data:image/", "/static/", "/app-data/", "app-data:")):
        return candidate
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("image_url_scheme_not_allowed")
    if parsed.username or parsed.password:
        raise ValueError("image_url_credentials_not_allowed")
    return candidate


def _cell(value: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(value, str):
        result = deepcopy(fallback) if isinstance(fallback, dict) else {}
        result["runs"] = _text_runs(value)
        return result
    if not isinstance(value, dict):
        raise TypeError("table_cell_must_be_string_or_object")
    return deepcopy(value)


def _table_grid(
    columns: list[dict[str, Any]],
    rows: list[list[dict[str, Any]]],
) -> list[list[dict[str, Any]]]:
    return [columns, *rows]


def _table_coordinate(
    value: Any,
    *,
    name: str,
    allow_header: bool,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"table_{name}_must_be_integer")
    if allow_header:
        if value < -1:
            raise ValueError("table_row_index_out_of_range")
        return value + 1
    if value < 0:
        raise ValueError(f"table_{name}_out_of_range")
    return value


def _has_table_spans(grid: list[list[dict[str, Any]]]) -> bool:
    return any(
        cell.get("covered_by") is not None
        or cell.get("rowspan", 1) != 1
        or cell.get("colspan", 1) != 1
        for row in grid
        for cell in row
    )


def _merge_table_cells(
    grid: list[list[dict[str, Any]]],
    update: dict[str, Any],
) -> None:
    anchor_row = _table_coordinate(
        update.get("row"),
        name="row",
        allow_header=True,
    )
    anchor_column = _table_coordinate(
        update.get("column"),
        name="column",
        allow_header=False,
    )
    rowspan = _table_coordinate(
        update.get("rowspan"),
        name="rowspan",
        allow_header=False,
    )
    colspan = _table_coordinate(
        update.get("colspan"),
        name="colspan",
        allow_header=False,
    )
    if rowspan < 1 or colspan < 1 or (rowspan == 1 and colspan == 1):
        raise ValueError("table_merge_requires_a_multi_cell_rectangle")
    column_count = len(grid[0]) if grid else 0
    if (
        anchor_row >= len(grid)
        or anchor_column >= column_count
        or anchor_row + rowspan > len(grid)
        or anchor_column + colspan > column_count
    ):
        raise ValueError("table_merge_rectangle_out_of_range")

    coordinates = [
        (row_index, column_index)
        for row_index in range(anchor_row, anchor_row + rowspan)
        for column_index in range(anchor_column, anchor_column + colspan)
    ]
    if any(
        grid[row_index][column_index].get("covered_by") is not None
        or grid[row_index][column_index].get("rowspan", 1) != 1
        or grid[row_index][column_index].get("colspan", 1) != 1
        for row_index, column_index in coordinates
    ):
        raise ValueError("table_merge_rectangle_overlaps_existing_span")

    anchor = grid[anchor_row][anchor_column]
    anchor["rowspan"] = rowspan
    anchor["colspan"] = colspan
    for row_index, column_index in coordinates:
        if (row_index, column_index) == (anchor_row, anchor_column):
            continue
        grid[row_index][column_index]["covered_by"] = {
            "row": anchor_row,
            "column": anchor_column,
        }


def _split_table_cell(
    grid: list[list[dict[str, Any]]],
    update: dict[str, Any],
) -> None:
    selected_row = _table_coordinate(
        update.get("row"),
        name="row",
        allow_header=True,
    )
    selected_column = _table_coordinate(
        update.get("column"),
        name="column",
        allow_header=False,
    )
    column_count = len(grid[0]) if grid else 0
    if selected_row >= len(grid) or selected_column >= column_count:
        raise ValueError("table_cell_index_out_of_range")

    selected = grid[selected_row][selected_column]
    marker = selected.get("covered_by")
    if marker is not None:
        if (
            not isinstance(marker, dict)
            or isinstance(marker.get("row"), bool)
            or isinstance(marker.get("column"), bool)
            or not isinstance(marker.get("row"), int)
            or not isinstance(marker.get("column"), int)
        ):
            raise ValueError("table_covered_cell_marker_invalid")
        anchor_row = marker["row"]
        anchor_column = marker["column"]
    else:
        anchor_row = selected_row
        anchor_column = selected_column
    if (
        not 0 <= anchor_row < len(grid)
        or not 0 <= anchor_column < column_count
    ):
        raise ValueError("table_span_anchor_out_of_range")

    anchor = grid[anchor_row][anchor_column]
    rowspan = anchor.get("rowspan", 1)
    colspan = anchor.get("colspan", 1)
    if (
        isinstance(rowspan, bool)
        or isinstance(colspan, bool)
        or not isinstance(rowspan, int)
        or not isinstance(colspan, int)
        or rowspan < 1
        or colspan < 1
        or (rowspan == 1 and colspan == 1)
    ):
        raise ValueError("table_cell_is_not_merged")
    if anchor_row + rowspan > len(grid) or anchor_column + colspan > column_count:
        raise ValueError("table_span_anchor_out_of_range")

    anchor["rowspan"] = 1
    anchor["colspan"] = 1
    for row_index in range(anchor_row, anchor_row + rowspan):
        for column_index in range(anchor_column, anchor_column + colspan):
            if (row_index, column_index) != (anchor_row, anchor_column):
                grid[row_index][column_index].pop("covered_by", None)


def _update_table(element: dict[str, Any], update: dict[str, Any]) -> None:
    if not isinstance(update, dict):
        raise TypeError("table_update_must_be_object")
    action = update.get("action")
    columns = deepcopy(element.get("columns"))
    rows = deepcopy(element.get("rows"))
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise TypeError("table_shape_missing")
    if any(not isinstance(cell, dict) for cell in columns) or any(
        not isinstance(row, list)
        or any(not isinstance(cell, dict) for cell in row)
        for row in rows
    ):
        raise TypeError("table_cells_must_be_objects")
    grid = _table_grid(columns, rows)

    if action == "replace":
        new_columns = update.get("columns")
        new_rows = update.get("rows")
        if not isinstance(new_columns, list) or not isinstance(new_rows, list):
            raise ValueError("table_replace_requires_columns_and_rows")
        columns = [_cell(value) for value in new_columns]
        if any(not isinstance(row, list) for row in new_rows):
            raise TypeError("table_rows_must_be_arrays")
        rows = [[_cell(value) for value in row] for row in new_rows]
    elif action == "set-cell":
        row_index = update.get("row")
        column_index = update.get("column")
        if (
            isinstance(row_index, bool)
            or isinstance(column_index, bool)
            or not isinstance(row_index, int)
            or not isinstance(column_index, int)
        ):
            raise ValueError("table_cell_indices_required")
        if row_index == -1:
            if not 0 <= column_index < len(columns):
                raise ValueError("table_column_index_out_of_range")
            if columns[column_index].get("covered_by") is not None:
                raise ValueError("table_cell_is_covered")
            columns[column_index] = _cell(update.get("value"), columns[column_index])
        else:
            if not 0 <= row_index < len(rows) or not 0 <= column_index < len(columns):
                raise ValueError("table_cell_index_out_of_range")
            if rows[row_index][column_index].get("covered_by") is not None:
                raise ValueError("table_cell_is_covered")
            rows[row_index][column_index] = _cell(
                update.get("value"),
                rows[row_index][column_index],
            )
    elif action == "merge":
        _merge_table_cells(grid, update)
    elif action == "split":
        _split_table_cell(grid, update)
    elif action == "add-row":
        if _has_table_spans(grid):
            raise ValueError("table_structure_change_requires_split")
        at = update.get("index", len(rows))
        values = update.get("values", [""] * len(columns))
        if isinstance(at, bool) or not isinstance(at, int) or not 0 <= at <= len(rows):
            raise ValueError("table_row_insert_index_out_of_range")
        if not isinstance(values, list) or len(values) != len(columns):
            raise ValueError("table_row_width_mismatch")
        fallback = rows[max(0, min(at - 1, len(rows) - 1))] if rows else columns
        rows.insert(
            at,
            [
                _cell(value, fallback[index] if index < len(fallback) else None)
                for index, value in enumerate(values)
            ],
        )
    elif action == "delete-row":
        if _has_table_spans(grid):
            raise ValueError("table_structure_change_requires_split")
        at = update.get("index")
        if isinstance(at, bool) or not isinstance(at, int) or not 0 <= at < len(rows):
            raise ValueError("table_row_index_out_of_range")
        rows.pop(at)
    elif action == "move-row":
        if _has_table_spans(grid):
            raise ValueError("table_structure_change_requires_split")
        source, target = update.get("from"), update.get("to")
        if (
            isinstance(source, bool)
            or isinstance(target, bool)
            or not isinstance(source, int)
            or not isinstance(target, int)
        ):
            raise ValueError("table_row_move_indices_required")
        if not 0 <= source < len(rows) or not 0 <= target < len(rows):
            raise ValueError("table_row_move_index_out_of_range")
        rows.insert(target, rows.pop(source))
    elif action == "add-column":
        if _has_table_spans(grid):
            raise ValueError("table_structure_change_requires_split")
        at = update.get("index", len(columns))
        header = update.get("header", "")
        values = update.get("values", [""] * len(rows))
        if (
            isinstance(at, bool)
            or not isinstance(at, int)
            or not 0 <= at <= len(columns)
        ):
            raise ValueError("table_column_insert_index_out_of_range")
        if not isinstance(values, list) or len(values) != len(rows):
            raise ValueError("table_column_height_mismatch")
        fallback_header = columns[max(0, min(at - 1, len(columns) - 1))] if columns else None
        columns.insert(at, _cell(header, fallback_header))
        for row_index, row in enumerate(rows):
            fallback = row[max(0, min(at - 1, len(row) - 1))] if row else None
            row.insert(at, _cell(values[row_index], fallback))
    elif action == "delete-column":
        if _has_table_spans(grid):
            raise ValueError("table_structure_change_requires_split")
        at = update.get("index")
        if (
            isinstance(at, bool)
            or not isinstance(at, int)
            or not 0 <= at < len(columns)
        ):
            raise ValueError("table_column_index_out_of_range")
        columns.pop(at)
        for row in rows:
            row.pop(at)
    elif action == "move-column":
        if _has_table_spans(grid):
            raise ValueError("table_structure_change_requires_split")
        source, target = update.get("from"), update.get("to")
        if (
            isinstance(source, bool)
            or isinstance(target, bool)
            or not isinstance(source, int)
            or not isinstance(target, int)
        ):
            raise ValueError("table_column_move_indices_required")
        if not 0 <= source < len(columns) or not 0 <= target < len(columns):
            raise ValueError("table_column_move_index_out_of_range")
        columns.insert(target, columns.pop(source))
        for row in rows:
            row.insert(target, row.pop(source))
    else:
        raise ValueError("table_action_not_supported")
    candidate = {**deepcopy(element), "columns": columns, "rows": rows}
    Table.model_validate(candidate)
    element["columns"] = columns
    element["rows"] = rows


def apply_content_update(
    element: dict[str, Any],
    content: dict[str, Any],
) -> None:
    if not isinstance(content, dict):
        raise TypeError("content_update_must_be_object")
    element_type = element.get("type")
    if element_type == "text":
        if "text" in content:
            _bounded_text(element, content["text"])
        if "run_selection" in content:
            _update_text_run_range(element, content["run_selection"])
        if set(content) - {"text", "run_selection"}:
            raise ValueError("text_content_fields_not_allowed")
        return
    if element_type == "math":
        _update_math(element, content)
        return
    if element_type == "text-list":
        if set(content) != {"items"} or not isinstance(content["items"], list):
            raise ValueError("text_list_update_requires_items")
        items = content["items"]
        minimum = int(element.get("min_items", 0))
        maximum = int(element.get("max_items", 1000))
        if not minimum <= len(items) <= maximum:
            raise ValueError("text_list_item_count_out_of_bounds")
        max_length = int(element.get("max_item_length", 200000))
        min_length = int(element.get("min_item_length", 0))
        normalized: list[list[dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, str) or not min_length <= len(item) <= max_length:
                raise ValueError("text_list_item_length_out_of_bounds")
            normalized.append(_text_runs(item))
        element["items"] = normalized
        return
    if element_type == "image":
        if set(content) - {"data", "prompt", "source"} or "data" not in content:
            raise ValueError("image_content_fields_not_allowed")
        element["data"] = _normalize_asset_url(content["data"])
        if "prompt" in content:
            element["prompt"] = content["prompt"]
        if "source" in content:
            # The strict Image model has no source field; preserve it within the
            # supported prompt metadata instead of introducing an invalid key.
            prompt = str(element.get("prompt") or "")
            source = str(content["source"] or "")
            element["prompt"] = f"{prompt}\nsource:{source}".strip()
        if not element.get("is_icon") and not element.get("clip_path"):
            element["fit"] = "cover"
        return
    if element_type == "chart":
        unknown = set(content) - _CHART_CONTENT_FIELDS
        if unknown:
            raise ValueError(f"chart_content_fields_not_allowed:{','.join(sorted(unknown))}")
        for key, value in content.items():
            element[key] = deepcopy(value)
        return
    if element_type == "table":
        _update_table(element, content)
        return
    if element_type == "infographic":
        if set(content) - {"data", "colors"} or "data" not in content:
            raise ValueError("infographic_content_fields_not_allowed")
        element["data"] = deepcopy(content["data"])
        if "colors" in content:
            element["colors"] = deepcopy(content["colors"])
        return
    if element_type == "vector":
        allowed = {"points", "closed", "curve", "corner_radii", "shape"}
        if set(content) - allowed:
            raise ValueError("vector_content_fields_not_allowed")
        for key, value in content.items():
            element[key] = deepcopy(value)
        return
    raise ValueError(f"content_update_not_supported_for_type:{element_type}")


def apply_style_patch(element: dict[str, Any], patch: dict[str, Any]) -> None:
    if not isinstance(patch, dict):
        raise TypeError("style_patch_must_be_object")
    element_type = str(element.get("type") or "")
    allowed = _TYPE_STYLE_FIELDS.get(element_type, set())
    unknown = set(patch) - allowed
    if unknown:
        raise ValueError(f"style_fields_not_allowed:{','.join(sorted(unknown))}")
    for key, value in patch.items():
        element[key] = deepcopy(value)


def update_element(
    element: dict[str, Any],
    *,
    content: dict[str, Any] | None = None,
    style: dict[str, Any] | None = None,
    position: dict[str, Any] | None = None,
    size: dict[str, Any] | None = None,
    rotation: float | None = None,
) -> None:
    candidate = deepcopy(element)
    if content is not None:
        apply_content_update(candidate, content)
    if style is not None:
        apply_style_patch(candidate, style)
    apply_geometry(candidate, position=position, size=size, rotation=rotation)
    element.clear()
    element.update(candidate)
