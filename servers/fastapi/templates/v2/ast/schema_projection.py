"""JSON content-schema projection from the canonical AST."""

from __future__ import annotations

import copy
from typing import Any

from templates.v2.schema import get_component_schema

from .models import CanonicalTemplateAst


def project_content_schema(
    ast: CanonicalTemplateAst, *, layout_id: str
) -> dict[str, Any]:
    if not ast.capabilities.content_schema:
        raise ValueError("template_ast_content_schema_unsupported")
    layout = next((item for item in ast.layouts if item.layout_id == layout_id), None)
    if layout is None:
        raise ValueError(f"template_ast_layout_not_found:{layout_id}")
    properties: dict[str, Any] = {}
    for component in layout.components:
        source = copy.deepcopy(component.source)
        top_level_positioned = [
            repeat
            for repeat in component.repeats
            if repeat.arrangement == "positioned"
            and repeat.source_path == f"{component.source_path}.elements"
        ]
        elements = source.get("elements")
        if top_level_positioned and isinstance(elements, list):
            repeated_indexes = {
                index
                for repeat in top_level_positioned
                for index in repeat.source_indices
            }
            source["elements"] = [
                element
                for index, element in enumerate(elements)
                if index not in repeated_indexes
            ]
        schema = get_component_schema(source)
        if schema is None and component.repeats:
            schema = {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "title": component.component_id,
                "additionalProperties": False,
                "properties": {},
                "required": [],
            }
        if schema is None:
            continue
        component_schema = copy.deepcopy(schema)
        component_properties = component_schema.get("properties")
        if isinstance(component_properties, dict):
            for repeat in component.repeats:
                if repeat.arrangement == "positioned" and repeat.anchors:
                    item_schema = get_component_schema(
                        {
                            "id": f"{component.component_id}_repeat_item",
                            "elements": [copy.deepcopy(repeat.anchors[0].source)],
                        }
                    )
                    if item_schema is None:
                        raise ValueError(
                            f"template_ast_repeat_item_schema_missing:{repeat.repeat_id}"
                        )
                    projected_item = {
                        key: copy.deepcopy(value)
                        for key, value in item_schema.items()
                        if key not in {"$schema", "title", "description"}
                    }
                    item_properties = projected_item.get("properties")
                    if isinstance(item_properties, dict) and len(item_properties) == 1:
                        only_value = next(iter(item_properties.values()))
                        if isinstance(only_value, dict) and only_value.get("type") == "object":
                            projected_item = copy.deepcopy(only_value)
                    component_properties[repeat.content_field] = {
                        "type": "array",
                        "minItems": repeat.min_items,
                        "maxItems": repeat.max_items,
                        "items": projected_item,
                    }
                    required = component_schema.setdefault("required", [])
                    if repeat.content_field not in required:
                        required.append(repeat.content_field)
                elif repeat.content_field in component_properties:
                    repeated = component_properties[repeat.content_field]
                    if isinstance(repeated, dict):
                        repeated["minItems"] = repeat.min_items
                        repeated["maxItems"] = repeat.max_items
        properties[component.component_id] = component_schema
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "title": layout.layout_id,
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


__all__ = ["project_content_schema"]
