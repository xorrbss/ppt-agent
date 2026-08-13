"""Element-specific text, infographic, table, and chart content schemas."""

from __future__ import annotations

from typing import Any, Mapping

from templates.v2.models.elements import ChartType
from templates.v2.schema_common import _compact, _object_schema


CHART_TYPE_VALUES = [chart_type.value for chart_type in ChartType]



def _content_schema(element: Mapping[str, Any]) -> dict[str, Any]:
    element_type = element["type"]
    if element_type in {"text", "math"}:
        return _compact(
            {
                "type": "string",
                "minLength": element.get("min_length"),
                "maxLength": element.get("max_length"),
            }
        )
    if element_type == "image":
        prompt_key = "icon_query" if element.get("is_icon") is True else "image_prompt"
        url_key = "icon_url" if element.get("is_icon") is True else "image_url"
        return _object_schema(
            {
                prompt_key: {"type": "string"},
                url_key: {"type": "string"},
            },
            required=[prompt_key],
        )
    if element_type == "text-list":
        return _compact(
            {
                "type": "array",
                "minItems": element.get("min_items"),
                "maxItems": element.get("max_items"),
                "items": _compact(
                    {
                        "type": "string",
                        "minLength": element.get("min_item_length"),
                        "maxLength": element.get("max_item_length"),
                    }
                ),
            }
        )
    if element_type == "table":
        return _table_content_schema(element)
    if element_type == "chart":
        return _chart_content_schema()
    if element_type == "infographic":
        return _infographic_content_schema()
    raise ValueError(f"unsupported content element type: {element_type}")



def _component_content_schema(
    element: Mapping[str, Any],
    *,
    name: str,
    path: str,
) -> dict[str, Any]:
    element_type = str(element["type"])
    if element_type in {"text", "math"}:
        schema: dict[str, Any] = {
            "type": "string",
            "minLength": element.get("min_length"),
            "maxLength": element.get("max_length"),
        }
    elif element_type == "image":
        prompt_key = "icon_query" if element.get("is_icon") is True else "image_prompt"
        url_key = "icon_url" if element.get("is_icon") is True else "image_url"
        prompt_description = (
            "Search query for the replacement icon."
            if element.get("is_icon") is True
            else "Prompt for the replacement image."
        )
        schema = _object_schema(
            {
                prompt_key: {
                    "type": "string",
                    "description": prompt_description,
                },
                url_key: {"type": "string"},
            },
            required=[prompt_key],
        )
    elif element_type == "text-list":
        schema = {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": element.get("min_item_length"),
                "maxLength": element.get("max_item_length"),
            },
            "minItems": element.get("min_items"),
            "maxItems": element.get("max_items"),
        }
    elif element_type == "table":
        schema = _table_content_schema(element)
    elif element_type == "chart":
        schema = _chart_content_schema()
    elif element_type == "infographic":
        schema = _infographic_content_schema()
    else:
        schema = {}
    return {
        **_compact_nested(schema),
        "title": _field_title(name),
        "x-element-type": element_type,
        "x-element-path": path,
    }


def _field_title(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split("_") if part) or name


def _infographic_data_schema(infographic_type: str) -> dict[str, Any]:
    return _object_schema(
        {
            "type": {"const": infographic_type},
            "min_value": {"type": "number"},
            "max_value": {"type": "number"},
            "value": {"type": "number"},
        }
    )


def _infographic_content_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "data": {
                "oneOf": [
                    _infographic_data_schema("progress_bar"),
                    _infographic_data_schema("gauge"),
                ]
            },
            "colors": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
        required=["data"],
    )


def _table_content_schema(element: Mapping[str, Any]) -> dict[str, Any]:
    """Use one table content contract for raw layouts and editable components."""

    schema = _compact_nested(
        _object_schema(
            {
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": element.get("min_columns"),
                    "maxItems": element.get("max_columns"),
                },
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": element.get("min_columns"),
                        "maxItems": element.get("max_columns"),
                    },
                    "minItems": element.get("min_rows"),
                    "maxItems": element.get("max_rows"),
                },
            }
        )
    )
    min_columns = element.get("min_columns")
    max_columns = element.get("max_columns")
    if (
        isinstance(min_columns, int)
        and not isinstance(min_columns, bool)
        and isinstance(max_columns, int)
        and not isinstance(max_columns, bool)
        and 0 <= min_columns <= max_columns
    ):
        schema["oneOf"] = [
            {
                "properties": {
                    "columns": {
                        "minItems": column_count,
                        "maxItems": column_count,
                    },
                    "rows": {
                        "items": {
                            "minItems": column_count,
                            "maxItems": column_count,
                        }
                    },
                }
            }
            for column_count in range(min_columns, max_columns + 1)
        ]
    return schema


def _chart_content_schema() -> dict[str, Any]:
    schema = _object_schema(
        {
            "chart_type": {"type": "string", "enum": CHART_TYPE_VALUES},
            "title": {"type": ["string", "null"]},
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 24,
            },
            "series": {
                "type": "array",
                "items": _object_schema(
                    {
                        "name": {"type": "string"},
                        "values": {
                            "type": "array",
                            "items": {"type": "number"},
                            "maxItems": 24,
                        },
                    }
                ),
                "maxItems": 12,
            },
        },
        required=["chart_type", "categories", "series"],
    )
    schema["allOf"] = [
        {
            "if": {
                "properties": {
                    "chart_type": {"enum": ["pie", "donut"]},
                },
                "required": ["chart_type"],
            },
            "then": {
                "properties": {
                    "series": {"maxItems": 1},
                }
            },
        },
        {
            "oneOf": [
                {
                    "properties": {
                        "categories": {
                            "minItems": item_count,
                            "maxItems": item_count,
                        },
                        "series": {
                            "items": {
                                "properties": {
                                    "values": {
                                        "minItems": item_count,
                                        "maxItems": item_count,
                                    }
                                }
                            }
                        },
                    }
                }
                for item_count in range(25)
            ]
        },
    ]
    return schema


def _compact_nested(value: Any) -> Any:
    if isinstance(value, list):
        return [_compact_nested(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _compact_nested(item)
            for key, item in value.items()
            if item is not None
        }
    return value
