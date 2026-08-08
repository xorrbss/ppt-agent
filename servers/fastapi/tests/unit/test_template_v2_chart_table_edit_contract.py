from copy import deepcopy

from template_v2_schema_contract import (
    build_chart_contract_edit_patches,
    build_table_contract_edit_patches,
    validate_chart_contract,
    validate_table_contract,
)


def _apply(source, patches):
    result = deepcopy(source)
    for patch in patches:
        cursor = result
        for index, segment in enumerate(patch["path"][:-1]):
            if isinstance(cursor, dict) and segment not in cursor:
                cursor[segment] = (
                    [] if isinstance(patch["path"][index + 1], int) else {}
                )
            cursor = cursor[segment]
        cursor[patch["path"][-1]] = deepcopy(patch["value"])
    return result


def _chart():
    return {
        "type": "chart",
        "chart_type": "bar",
        "categories": ["Q1", "Q2"],
        "series": [
            {
                "name": "Revenue",
                "values": [10, 20],
                "extension": {"retained": True},
            }
        ],
        "x_axis": True,
        "axis_color": "#334155",
        "data_labels": "top",
        "extension": {"retained": True},
    }


def _table():
    return {
        "type": "table",
        "columns": [
            {
                "runs": [{"text": "A", "font": {"bold": True}}],
                "extension": {"retained": True},
            },
            {"runs": [{"text": "B"}]},
        ],
        "rows": [
            [
                {"runs": [{"text": "1"}]},
                {"runs": [{"text": "2"}]},
            ]
        ],
        "min_columns": 1,
        "max_columns": 3,
        "min_rows": 0,
        "max_rows": 2,
        "extension": {"retained": True},
    }


def test_chart_edits_are_validated_non_mutating_and_preserve_unknown_fields():
    source = _chart()
    snapshot = deepcopy(source)
    changed = build_chart_contract_edit_patches(
        source,
        {
            "kind": "series-value",
            "seriesIndex": 0,
            "categoryIndex": 1,
            "value": 25.5,
        },
    )
    assert changed["ok"]
    assert source == snapshot
    result = _apply(source, changed["value"])
    assert validate_chart_contract(result)["ok"]
    assert result["extension"] == {"retained": True}
    assert result["series"][0]["extension"] == {"retained": True}


def test_chart_type_edit_clears_proven_stale_fields_and_rejects_unsafe_pie():
    source = _chart()
    changed = build_chart_contract_edit_patches(
        source, {"kind": "chart-type", "value": "donut"}
    )
    assert changed["ok"]
    assert [patch["path"][0] for patch in changed["value"]] == [
        "chart_type",
        "x_axis",
        "axis_color",
        "data_labels",
    ]
    assert validate_chart_contract(_apply(source, changed["value"]))["ok"]

    stale_label = _chart()
    stale_label["data_labels"] = "mid"
    line = build_chart_contract_edit_patches(
        stale_label, {"kind": "chart-type", "value": "line"}
    )
    assert line["ok"]
    assert [patch["path"][0] for patch in line["value"]] == [
        "chart_type",
        "data_labels",
    ]
    assert validate_chart_contract(_apply(stale_label, line["value"]))["ok"]

    unsafe = _chart()
    unsafe["series"].append({"name": "Cost", "values": [5, 8]})
    rejected = build_chart_contract_edit_patches(
        unsafe, {"kind": "chart-type", "value": "pie"}
    )
    assert not rejected["ok"]


def test_chart_label_and_count_edits_preserve_shape_and_metadata():
    source = _chart()
    labelled = build_chart_contract_edit_patches(
        source, {"kind": "data-labels", "value": "outside"}
    )
    assert labelled == {
        "ok": True,
        "value": [{"path": ["data_labels"], "value": "outside"}],
    }

    expanded = build_chart_contract_edit_patches(
        source, {"kind": "category-count", "value": 3}
    )
    assert expanded["ok"]
    expanded_chart = _apply(source, expanded["value"])
    assert expanded_chart["categories"] == ["Q1", "Q2", "Category 3"]
    assert expanded_chart["series"][0]["values"] == [10, 20, 0]
    assert expanded_chart["series"][0]["extension"] == {"retained": True}
    assert validate_chart_contract(expanded_chart)["ok"]

    more_series = build_chart_contract_edit_patches(
        expanded_chart, {"kind": "series-count", "value": 2}
    )
    assert more_series["ok"]
    result = _apply(expanded_chart, more_series["value"])
    assert result["series"][1] == {
        "name": "Series 2",
        "values": [0, 0, 0],
    }
    assert validate_chart_contract(result)["ok"]


def test_chart_count_and_label_edits_reject_unsupported_values():
    assert build_chart_contract_edit_patches(
        _chart(), {"kind": "data-labels", "value": "unsupported"}
    ) == {"ok": False, "reason": "invalid_chart_data_labels_edit"}
    pie = {
        **_chart(),
        "chart_type": "pie",
        "x_axis": None,
        "axis_color": None,
        "data_labels": None,
    }
    assert build_chart_contract_edit_patches(
        pie, {"kind": "series-count", "value": 2}
    ) == {"ok": False, "reason": "invalid_chart_series_count_edit"}
    assert build_chart_contract_edit_patches(
        _chart(), {"kind": "category-count", "value": 25}
    ) == {"ok": False, "reason": "invalid_chart_category_count_edit"}


def test_table_text_and_resize_preserve_metadata_and_rectangularity():
    source = _table()
    snapshot = deepcopy(source)
    text = build_table_contract_edit_patches(
        source,
        {
            "kind": "run-text",
            "section": "header",
            "columnIndex": 0,
            "runIndex": 0,
            "value": "Account",
        },
    )
    assert text["ok"]
    assert source == snapshot

    wider = build_table_contract_edit_patches(
        source, {"kind": "column-count", "value": 3}
    )
    assert wider["ok"]
    result = _apply(source, wider["value"])
    assert validate_table_contract(result)["ok"]
    assert result["columns"][0]["extension"] == {"retained": True}
    assert result["columns"][2] == {"runs": [{"text": ""}]}

    taller = build_table_contract_edit_patches(
        result, {"kind": "row-count", "value": 2}
    )
    assert taller["ok"]
    assert validate_table_contract(_apply(result, taller["value"]))["ok"]


def test_imported_exact_table_bounds_fail_closed():
    source = _table()
    source["min_columns"] = source["max_columns"] = len(source["columns"])
    source["min_rows"] = source["max_rows"] = len(source["rows"])
    assert not build_table_contract_edit_patches(
        source,
        {"kind": "column-count", "value": len(source["columns"]) + 1},
    )["ok"]
    assert not build_table_contract_edit_patches(
        source,
        {"kind": "row-count", "value": len(source["rows"]) - 1},
    )["ok"]


def test_table_cell_font_edits_create_canonical_font_without_metadata_loss():
    source = _table()
    changed = build_table_contract_edit_patches(
        source,
        {
            "kind": "cell-font",
            "section": "body",
            "rowIndex": 0,
            "columnIndex": 1,
            "field": "color",
            "value": "#dc2626",
        },
    )
    assert changed == {
        "ok": True,
        "value": [
            {
                "path": ["rows", 0, 1, "font", "color"],
                "value": "#dc2626",
            }
        ],
    }
    result = _apply(source, changed["value"])
    assert validate_table_contract(result)["ok"]
    assert result["rows"][0][1]["font"] == {"color": "#dc2626"}
    assert result["extension"] == {"retained": True}

    rejected = build_table_contract_edit_patches(
        source,
        {
            "kind": "cell-font",
            "section": "body",
            "rowIndex": 0,
            "columnIndex": 1,
            "field": "bold",
            "value": "true",
        },
    )
    assert rejected == {"ok": False, "reason": "invalid_table_font_edit"}
