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
        for segment in patch["path"][:-1]:
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

    unsafe = _chart()
    unsafe["series"].append({"name": "Cost", "values": [5, 8]})
    rejected = build_chart_contract_edit_patches(
        unsafe, {"kind": "chart-type", "value": "pie"}
    )
    assert not rejected["ok"]


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
