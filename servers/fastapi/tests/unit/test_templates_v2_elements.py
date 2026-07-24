import pytest
from pydantic import ValidationError

from templates.v2.models.elements import Chart, Infographic, Text, Vector
from templates.v2.models.layouts import RawSlideLayout


def _text_element(name: str = "text") -> dict:
    return {
        "type": "text",
        "runs": [{"text": "Text"}],
        "decorative": False,
        "name": name,
        "min_length": 1,
        "max_length": 20,
    }


def _validate_element(element: dict) -> RawSlideLayout:
    return RawSlideLayout.model_validate(
        {
            "id": "validation",
            "description": "Validate one native element",
            "elements": [element],
        }
    )


def test_discriminated_layout_preserves_supported_native_element_shapes():
    layout = RawSlideLayout.model_validate(
        {
            "id": "native",
            "description": "Selected Phase 1 native elements",
            "elements": [
                {
                    "type": "text",
                    "runs": [{"text": "Title"}],
                    "decorative": False,
                    "name": "title",
                    "min_length": 1,
                    "max_length": 80,
                },
                {
                    "type": "vector",
                    "shape": "ellipse",
                    "points": [{"x": 0, "y": 0}, {"x": 100, "y": 80}],
                    "fill": {"color": "#ffffff"},
                },
                {
                    "type": "chart",
                    "decorative": False,
                    "name": "revenue",
                    "chart_type": "bubble",
                    "data_labels": True,
                },
                {
                    "type": "infographic",
                    "decorative": False,
                    "name": "progress",
                    "data": {
                        "type": "progress_bar",
                        "min_value": 0,
                        "max_value": 100,
                        "value": 64,
                    },
                    "colors": ["#e5e7eb", "#2563eb"],
                },
            ],
        }
    )

    assert isinstance(layout.elements[0], Text)
    assert isinstance(layout.elements[1], Vector)
    assert isinstance(layout.elements[2], Chart)
    assert layout.elements[2].chart_type.value == "bubble"
    assert layout.elements[2].data_labels.value == "top"
    assert isinstance(layout.elements[3], Infographic)
    assert layout.model_dump(mode="json")["elements"][3]["type"] == "infographic"


@pytest.mark.parametrize(
    "element",
    [
        {
            "type": "text",
            "runs": [{"text": "Title"}],
            "decorative": False,
            "name": "title",
            "min_length": 5,
            "max_length": 4,
        },
        {
            "type": "vector",
            "points": [{"x": 0, "y": 0}],
        },
        {
            "type": "chart",
            "decorative": False,
            "name": "chart",
            "chart_type": "bar",
            "unknown_export_field": True,
        },
    ],
)
def test_invalid_or_lossy_element_payloads_are_rejected(element):
    with pytest.raises(ValidationError):
        _validate_element(element)


@pytest.mark.parametrize(
    "element",
    [
        {
            "type": "text-list",
            "items": [[{"text": "Only item"}]],
            "decorative": False,
            "name": "bullets",
            "min_items": 2,
            "max_items": 3,
            "min_item_length": 1,
            "max_item_length": 40,
        },
        {
            "type": "table",
            "columns": [{"runs": [{"text": "Only column"}]}],
            "rows": [[{"runs": [{"text": "Only value"}]}]],
            "decorative": False,
            "name": "metrics",
            "min_columns": 2,
            "max_columns": 3,
            "min_rows": 1,
            "max_rows": 2,
        },
        {
            "type": "table",
            "columns": [
                {"runs": [{"text": "A"}]},
                {"runs": [{"text": "B"}]},
            ],
            "rows": [[{"runs": [{"text": "One row"}]}, {"runs": [{"text": "1"}]}]],
            "decorative": False,
            "name": "metrics",
            "min_columns": 2,
            "max_columns": 2,
            "min_rows": 2,
            "max_rows": 3,
        },
        {
            "type": "flex",
            "direction": "row",
            "children": [_text_element()],
            "name": "cards",
            "min_children": 2,
            "max_children": 3,
        },
        {
            "type": "grid",
            "columns": 2,
            "children": [_text_element()],
            "name": "cards",
            "min_children": 2,
            "max_children": 3,
        },
    ],
)
def test_collection_elements_reject_counts_outside_declared_bounds(element):
    with pytest.raises(ValidationError):
        _validate_element(element)


def test_table_rejects_ragged_rows():
    with pytest.raises(ValidationError, match="column count"):
        _validate_element(
            {
                "type": "table",
                "columns": [
                    {"runs": [{"text": "A"}]},
                    {"runs": [{"text": "B"}]},
                ],
                "rows": [[{"runs": [{"text": "Missing second value"}]}]],
                "decorative": False,
                "name": "metrics",
                "min_columns": 2,
                "max_columns": 2,
                "min_rows": 1,
                "max_rows": 2,
            }
        )


@pytest.mark.parametrize("element_type", ["flex", "grid"])
@pytest.mark.parametrize("gap_name", ["gap", "column_gap", "row_gap"])
def test_layout_elements_reject_negative_gaps(element_type, gap_name):
    element = {
        "type": element_type,
        "children": [_text_element()],
        "name": "layout",
        "min_children": 1,
        "max_children": 1,
        gap_name: -0.1,
    }
    if element_type == "flex":
        element["direction"] = "row"
    else:
        element["columns"] = 1

    with pytest.raises(ValidationError, match="non-negative"):
        _validate_element(element)


@pytest.mark.parametrize(
    "dimensions",
    [
        {"columns": 0},
        {"columns": 1, "rows": 0},
        {"columns": 2, "rows": 2, "max_children": 5},
    ],
)
def test_grid_rejects_invalid_dimensions_or_capacity(dimensions):
    element = {
        "type": "grid",
        "columns": 1,
        "rows": 1,
        "children": [_text_element()],
        "name": "grid",
        "min_children": 1,
        "max_children": 1,
        **dimensions,
    }

    with pytest.raises(ValidationError):
        _validate_element(element)


@pytest.mark.parametrize(
    "data",
    [
        {"type": "progress_bar", "min_value": 10, "max_value": 10, "value": 10},
        {"type": "progress_bar", "min_value": 0, "max_value": 10, "value": 11},
        {"type": "gauge", "min_value": 5, "max_value": 1, "value": 3},
        {"type": "gauge", "min_value": 0, "max_value": 10, "value": -1},
    ],
)
def test_infographic_rejects_invalid_ranges_and_out_of_bounds_values(data):
    with pytest.raises(ValidationError):
        _validate_element(
            {
                "type": "infographic",
                "decorative": False,
                "name": "metric",
                "data": data,
            }
        )


def test_ellipse_requires_exactly_two_bounding_points():
    with pytest.raises(ValidationError, match="exactly two"):
        _validate_element(
            {
                "type": "vector",
                "shape": "ellipse",
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 100, "y": 80},
                    {"x": 50, "y": 40},
                ],
            }
        )


@pytest.mark.parametrize(
    "chart",
    [
        {
            "chart_type": "line",
            "categories": ["Q1", "Q2"],
            "series": [{"name": "Revenue", "values": [10]}],
        },
        {
            "chart_type": "pie",
            "categories": ["A", "B"],
            "series": [
                {"name": "First", "values": [10, 20]},
                {"name": "Second", "values": [30, 40]},
            ],
        },
        {
            "chart_type": "donut",
            "categories": ["A"],
            "series": [
                {"name": "First", "values": [10]},
                {"name": "Second", "values": [20]},
            ],
        },
    ],
)
def test_chart_rejects_mismatched_data_and_multiple_circular_series(chart):
    with pytest.raises(ValidationError):
        _validate_element(
            {
                "type": "chart",
                "decorative": False,
                "name": "chart",
                **chart,
            }
        )
