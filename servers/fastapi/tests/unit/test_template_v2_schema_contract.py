from __future__ import annotations

from copy import deepcopy

from template_v2_schema_contract import (
    chart_contract_capabilities,
    normalize_chart_contract,
    normalize_table_contract,
    validate_chart_contract,
    validate_table_contract,
)


def cell(text: str, extension=None):
    return {
        "runs": [{"text": text, "run_extension": extension}],
        "cell_extension": extension,
    }


def test_chart_contract_preserves_unknown_json_without_mutating_input():
    source = {
        "type": "chart",
        "chart_type": "bar",
        "categories": ["Q1"],
        "series": [
            {
                "name": "Revenue",
                "values": [10],
                "series_extension": {"unit": "USD"},
            }
        ],
        "data_labels": "mid",
        "future_chart": {"enabled": True},
    }
    original = deepcopy(source)

    result = validate_chart_contract(source)

    assert result["ok"] is True
    assert result["value"] == source
    assert result["value"] is not source
    assert result["value"]["future_chart"] is not source["future_chart"]
    assert source == original


def test_chart_transition_clears_only_known_stale_options():
    source = {
        "type": "chart",
        "chart_type": "radar",
        "categories": ["Q1"],
        "series": [{"name": "Revenue", "values": [10]}],
        "x_axis": True,
        "y_axis_grid": False,
        "data_labels": "mid",
        "future_axis_policy": {"retained": True},
    }

    result = normalize_chart_contract(source, previous_type="bar")

    assert result["ok"] is True
    assert "x_axis" not in result["value"]
    assert "y_axis_grid" not in result["value"]
    assert "data_labels" not in result["value"]
    assert result["value"]["future_axis_policy"] == {"retained": True}
    assert source["x_axis"] is True


def test_chart_contract_fails_closed_for_stale_or_lossy_values():
    assert validate_chart_contract(
        {"type": "chart", "chart_type": "radar", "x_axis": True}
    ) == {"ok": False, "reason": "stale_chart_axis_options"}
    assert normalize_chart_contract(
        {"type": "chart", "chart_type": "line", "data_labels": "mid"}
    ) == {"ok": False, "reason": "stale_chart_data_labels"}
    assert normalize_chart_contract(
        {
            "type": "chart",
            "chart_type": "pie",
            "categories": ["A"],
            "series": [
                {"name": "One", "values": [1]},
                {"name": "Two", "values": [2]},
            ],
        },
        previous_type="bar",
    ) == {"ok": False, "reason": "chart_series_limit"}


def test_chart_legacy_labels_and_capabilities_match_export_contract():
    result = normalize_chart_contract(
        {"type": "chart", "chart_type": "bar", "data_labels": True}
    )
    assert result["ok"] is True
    assert result["value"]["data_labels"] == "top"
    assert chart_contract_capabilities("line")["value"][
        "data_label_positions"
    ] == ["top", "outside"]
    assert chart_contract_capabilities("pie")["value"] == {
        "axis_options": False,
        "data_label_positions": [],
        "multiple_series": False,
    }


def test_table_bounds_count_body_rows_excluding_columns_header():
    source = {
        "type": "table",
        "columns": [cell("Header 1"), cell("Header 2")],
        "rows": [[cell("A"), cell("B")]],
        "min_columns": 2,
        "max_columns": 2,
        "min_rows": 1,
        "max_rows": 1,
    }
    assert validate_table_contract(source)["ok"] is True
    invalid = {**source, "min_rows": 2, "max_rows": 2}
    assert validate_table_contract(invalid) == {
        "ok": False,
        "reason": "table_body_row_count_out_of_bounds",
    }


def test_table_bound_migration_is_exact_lossless_and_non_mutating():
    source = {
        "type": "table",
        "columns": [cell("Header", {"role": "dimension"})],
        "rows": [[cell("Body", {"source": "crm"})]],
        "future_table": {"retained": True},
    }
    result = normalize_table_contract(source)

    assert result["ok"] is True
    assert {
        key: result["value"][key]
        for key in ("min_columns", "max_columns", "min_rows", "max_rows")
    } == {
        "min_columns": 1,
        "max_columns": 1,
        "min_rows": 1,
        "max_rows": 1,
    }
    assert result["value"]["future_table"] == {"retained": True}
    assert "min_rows" not in source


def test_partial_bounds_and_non_rectangular_tables_fail_closed():
    assert normalize_table_contract(
        {
            "type": "table",
            "columns": [cell("Header")],
            "rows": [[cell("Body")]],
            "min_rows": 0,
        }
    ) == {"ok": False, "reason": "partial_table_bounds"}
    assert normalize_table_contract(
        {
            "type": "table",
            "columns": [cell("One"), cell("Two")],
            "rows": [[cell("Only one")]],
        }
    ) == {"ok": False, "reason": "table_not_rectangular"}
