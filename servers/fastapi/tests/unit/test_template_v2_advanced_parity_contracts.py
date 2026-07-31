"""Characterization tests for advanced Template V2 editor contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from templates.v2.models.elements import Chart, Table
from templates.v2.persistence import canonicalize_slide_dump


def _table_payload() -> dict[str, object]:
    return {
        "type": "table",
        "columns": [
            {"runs": [{"text": "Quarter"}]},
            {"runs": [{"text": "Revenue"}]},
        ],
        "rows": [[{"runs": [{"text": "Q1"}]}, {"runs": [{"text": "42"}]}]],
        "decorative": False,
        "name": "revenue-table",
        "min_columns": 1,
        "max_columns": 4,
        "min_rows": 0,
        "max_rows": 8,
    }


def test_chart_metadata_is_editable_and_preserved_by_the_native_contract() -> None:
    chart = Chart.model_validate(
        {
            "type": "chart",
            "chart_type": "bar",
            "title": "Revenue by quarter",
            "x_axis": True,
            "y_axis": True,
            "x_axis_title": "Quarter",
            "y_axis_title": "Revenue",
            "axis_color": "#123456",
            "x_axis_grid": True,
            "y_axis_grid": False,
            "grid_color": "#E5E7EB",
            "legend": True,
            "legend_color": "#334155",
            "data_labels": "top",
            "categories": ["Q1", "Q2"],
            "series": [{"name": "Actual", "values": [42, 51.5]}],
            "decorative": False,
            "name": "revenue-chart",
        }
    )

    payload = chart.model_dump(mode="json")

    assert payload["data_labels"] == "top"
    assert payload["x_axis_title"] == "Quarter"
    assert payload["series"] == [{"name": "Actual", "values": [42.0, 51.5]}]


def test_table_spans_round_trip_with_preserved_covered_cells() -> None:
    payload = _table_payload()
    payload["columns"][0]["rowspan"] = 2  # type: ignore[index]
    payload["rows"][0][0]["covered_by"] = {"row": 0, "column": 0}  # type: ignore[index]

    table = Table.model_validate(payload)
    canonical = table.model_dump(mode="json")

    assert canonical["columns"][0]["rowspan"] == 2
    assert canonical["rows"][0][0]["runs"] == [{"text": "Q1", "font": None}]
    assert canonical["rows"][0][0]["covered_by"] == {"row": 0, "column": 0}


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["columns"][0].update({"rowspan": 3}),  # type: ignore[index,union-attr]
            "outside the table grid",
        ),
        (
            lambda payload: payload["columns"][0].update({"colspan": 2}),  # type: ignore[index,union-attr]
            "must reference its span anchor",
        ),
        (
            lambda payload: payload["rows"][0][1].update(  # type: ignore[index,union-attr]
                {"covered_by": {"row": 0, "column": 0}}
            ),
            "missing span anchor",
        ),
        (
            lambda payload: payload["columns"][0].update({"rowspan": True}),  # type: ignore[index,union-attr]
            "valid integer",
        ),
        (
            lambda payload: payload["columns"][0].update({"colspan": "2"}),  # type: ignore[index,union-attr]
            "valid integer",
        ),
    ],
)
def test_table_spans_fail_closed_for_invalid_grid_contracts(
    mutate,
    message: str,
) -> None:
    payload = _table_payload()
    mutate(payload)

    with pytest.raises(ValidationError, match=message):
        Table.model_validate(payload)


def test_slide_snapshot_rejects_mixed_native_ui_and_authored_html() -> None:
    ui = {
        "id": "native-slide",
        "description": "Native slide used to characterize snapshot validation",
        "components": [
            {
                "id": "hero",
                "description": "Editable hero title component",
                "position": {"x": 0, "y": 0},
                "elements": [
                    {
                        "type": "text",
                        "runs": [{"text": "Native title"}],
                        "decorative": False,
                        "name": "title",
                        "min_length": 1,
                        "max_length": 80,
                    }
                ],
            }
        ],
    }

    with pytest.raises(ValueError, match="slide_ui_and_authored_html_cannot_coexist"):
        canonicalize_slide_dump({"ui": ui, "html_content": "<p>legacy</p>"})
