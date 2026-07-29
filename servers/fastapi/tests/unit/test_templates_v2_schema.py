import pytest
from jsonschema import ValidationError, validate

from templates.v2.models.layouts import RawSlideLayout
from templates.v2.schema import (
    extract_slide_schema_from_layout,
    get_component_schema,
    get_template_schema,
)


def _editable_title(name: str = "title") -> dict:
    return {
        "type": "text",
        "runs": [{"text": "Original"}],
        "decorative": False,
        "name": name,
        "min_length": 1,
        "max_length": 80,
    }


def test_slide_schema_ignores_decorative_content_and_validates_editable_slots():
    layout = RawSlideLayout.model_validate(
        {
            "id": "intro",
            "description": "Title slide",
            "elements": [
                _editable_title(),
                {
                    **_editable_title("decoration"),
                    "decorative": True,
                },
            ],
        }
    )

    schema = extract_slide_schema_from_layout(layout)

    assert list(schema["properties"]) == ["title"]
    assert schema["additionalProperties"] is False
    validate({"title": "Phase 1"}, schema)


def test_component_schema_retains_slots_named_like_schema_metadata():
    schema = get_component_schema(
        {
            "id": "hero",
            "description": "Editable metadata-like slot names",
            "elements": [
                _editable_title("title"),
                _editable_title("description"),
            ],
        }
    )

    assert schema is not None
    assert list(schema["properties"]) == ["title", "description"]


def test_template_schema_numbers_duplicate_components_without_mutating_fields():
    component = {
        "id": "metric",
        "description": "Metric card",
        "elements": [_editable_title("value")],
    }
    template = {
        "layouts": [
            {
                "id": "comparison",
                "description": "Comparison slide",
                "components": [component, component],
            }
        ]
    }

    schema = get_template_schema(template)["layouts"][0]["schema"]

    assert schema is not None
    assert list(schema["properties"]) == ["metric_0", "metric_1"]
    assert schema["required"] == ["metric_0", "metric_1"]
    assert schema["properties"]["metric_0"] == schema["properties"]["metric_1"]


def test_complex_slide_schema_preserves_nested_property_contracts():
    layout = RawSlideLayout.model_validate(
        {
            "id": "dashboard",
            "description": "Editable dashboard with nested structured content",
            "elements": [
                _editable_title(),
                {
                    "type": "image",
                    "decorative": False,
                    "name": "hero_image",
                    "data": "/app_data/images/hero.png",
                    "is_icon": False,
                },
                {
                    "type": "group",
                    "name": "details",
                    "children": [
                        {
                            "type": "text-list",
                            "decorative": False,
                            "name": "bullets",
                            "items": [[{"text": "First item"}]],
                            "min_items": 1,
                            "max_items": 3,
                            "min_item_length": 3,
                            "max_item_length": 40,
                        },
                        {
                            "type": "table",
                            "decorative": False,
                            "name": "metrics",
                            "columns": [
                                {"runs": [{"text": "Metric"}]},
                                {"runs": [{"text": "Value"}]},
                            ],
                            "rows": [
                                [
                                    {"runs": [{"text": "Revenue"}]},
                                    {"runs": [{"text": "12"}]},
                                ]
                            ],
                            "min_columns": 2,
                            "max_columns": 3,
                            "min_rows": 1,
                            "max_rows": 3,
                        },
                        {
                            "type": "chart",
                            "decorative": False,
                            "name": "trend",
                            "chart_type": "line",
                            "categories": ["Q1", "Q2"],
                            "series": [
                                {"name": "Revenue", "values": [10, 12]}
                            ],
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
                },
                {
                    "type": "vector",
                    "shape": "ellipse",
                    "points": [{"x": 0, "y": 0}, {"x": 100, "y": 80}],
                    "fill": {"color": "#ffffff"},
                },
            ],
        }
    )

    schema = extract_slide_schema_from_layout(layout)

    assert list(schema["properties"]) == ["title", "hero_image", "details"]
    assert schema["required"] == ["title", "hero_image", "details"]
    details = schema["properties"]["details"]
    assert list(details["properties"]) == [
        "bullets",
        "metrics",
        "trend",
        "progress",
    ]
    assert details["additionalProperties"] is False
    assert details["properties"]["metrics"]["properties"]["rows"]["items"][
        "minItems"
    ] == 2
    assert details["properties"]["trend"]["required"] == [
        "chart_type",
        "categories",
        "series",
    ]
    assert len(details["properties"]["progress"]["properties"]["data"]["oneOf"]) == 2

    content = {
        "title": "Phase 1",
        "hero_image": {"image_prompt": "blue analytics dashboard"},
        "details": {
            "bullets": ["Growth", "Margin"],
            "metrics": {
                "columns": ["Metric", "Value"],
                "rows": [["Revenue", "12"], ["Margin", "24"]],
            },
            "trend": {
                "chart_type": "line",
                "categories": ["Q1", "Q2"],
                "series": [{"name": "Revenue", "values": [10, 12]}],
            },
            "progress": {
                "data": {
                    "type": "progress_bar",
                    "min_value": 0,
                    "max_value": 100,
                    "value": 64,
                },
                "colors": ["#2563eb"],
            },
        },
    }
    validate(content, schema)

    invalid_content = {
        **content,
        "details": {
            **content["details"],
            "unexpected": "must fail closed",
        },
    }
    with pytest.raises(ValidationError):
        validate(invalid_content, schema)


def test_general_and_component_table_schemas_use_the_same_content_shape():
    table = {
        "type": "table",
        "decorative": False,
        "name": "metrics",
        "columns": [
            {"runs": [{"text": "Metric"}]},
            {"runs": [{"text": "Value"}]},
        ],
        "rows": [
            [
                {"runs": [{"text": "Revenue"}]},
                {"runs": [{"text": "12"}]},
            ]
        ],
        "min_columns": 2,
        "max_columns": 3,
        "min_rows": 1,
        "max_rows": 3,
    }
    general = extract_slide_schema_from_layout(
        RawSlideLayout.model_validate(
            {
                "id": "table",
                "description": "General table schema",
                "elements": [table],
            }
        )
    )["properties"]["metrics"]
    component = get_component_schema(
        {
            "id": "table",
            "description": "Editable table component",
            "elements": [table],
        }
    )["properties"]["metrics"]

    assert general == {
        key: value
        for key, value in component.items()
        if key not in {"title", "x-element-type", "x-element-path"}
    }
    valid_content = {
        "columns": ["Metric", "Value"],
        "rows": [["Revenue", "12"]],
    }
    validate(valid_content, general)
    with pytest.raises(ValidationError):
        validate([["Revenue", "12"]], general)
    with pytest.raises(ValidationError):
        validate(
            {
                "columns": ["Metric", "Value"],
                "rows": [["Revenue", "12", "Unexpected"]],
            },
            general,
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
            "categories": ["A", "B"],
            "series": [
                {"name": "First", "values": [10, 20]},
                {"name": "Second", "values": [30, 40]},
            ],
        },
    ],
)
def test_editable_chart_schema_rejects_semantically_invalid_data(chart):
    schema = get_component_schema(
        {
            "id": "chart",
            "description": "Editable chart component",
            "elements": [
                {
                    "type": "chart",
                    "decorative": False,
                    "name": "trend",
                    "chart_type": "line",
                }
            ],
        }
    )

    with pytest.raises(ValidationError):
        validate({"trend": chart}, schema)


def test_component_schema_keeps_editor_metadata_for_complex_properties():
    schema = get_component_schema(
        {
            "id": "feature_card",
            "description": "Reusable feature card with structured properties",
            "elements": [
                _editable_title("headline"),
                {
                    "type": "image",
                    "decorative": False,
                    "name": "icon",
                    "data": "/app_data/icons/icon.svg",
                    "is_icon": True,
                },
                {
                    "type": "table",
                    "decorative": False,
                    "name": "metrics",
                    "min_columns": 2,
                    "max_columns": 4,
                    "min_rows": 1,
                    "max_rows": 3,
                },
                {
                    "type": "chart",
                    "decorative": False,
                    "name": "trend",
                    "chart_type": "line",
                },
            ],
        }
    )

    assert schema is not None
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["headline", "icon", "metrics", "trend"]
    properties = schema["properties"]
    assert {
        name: (
            value["x-element-type"],
            value["x-element-path"],
        )
        for name, value in properties.items()
    } == {
        "headline": ("text", "elements.0"),
        "icon": ("image", "elements.1"),
        "metrics": ("table", "elements.2"),
        "trend": ("chart", "elements.3"),
    }
    assert properties["icon"]["properties"]["icon_query"]["description"] == (
        "Search query for the replacement icon."
    )
    assert properties["metrics"]["properties"]["rows"]["items"]["minItems"] == 2
    assert properties["trend"]["properties"]["series"]["maxItems"] == 12


def test_repeated_flex_children_collapse_to_one_array_property():
    layout = RawSlideLayout.model_validate(
        {
            "id": "cards",
            "description": "Repeated cards normalize numbered child slots",
            "elements": [
                {
                    "type": "flex",
                    "name": "cards",
                    "direction": "row",
                    "min_children": 2,
                    "max_children": 4,
                    "children": [
                        {
                            "type": "group",
                            "name": "card_1",
                            "children": [
                                _editable_title("title_1"),
                                {
                                    "type": "image",
                                    "decorative": False,
                                    "name": "icon_1",
                                    "data": "/app_data/icons/one.svg",
                                    "is_icon": True,
                                },
                            ],
                        },
                        {
                            "type": "group",
                            "name": "card_2",
                            "children": [
                                _editable_title("title_2"),
                                {
                                    "type": "image",
                                    "decorative": False,
                                    "name": "icon_2",
                                    "data": "/app_data/icons/two.svg",
                                    "is_icon": True,
                                },
                            ],
                        },
                    ],
                }
            ],
        }
    )

    cards = extract_slide_schema_from_layout(layout)["properties"]["cards"]

    assert cards["type"] == "array"
    assert (cards["minItems"], cards["maxItems"]) == (2, 4)
    assert list(cards["items"]["properties"]) == ["title", "icon"]
    assert cards["items"]["required"] == ["title", "icon"]
    validate(
        {
            "cards": [
                {"title": "One", "icon": {"icon_query": "circle one"}},
                {"title": "Two", "icon": {"icon_query": "circle two"}},
            ]
        },
        extract_slide_schema_from_layout(layout),
    )
