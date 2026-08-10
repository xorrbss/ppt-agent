from __future__ import annotations

import json
from pathlib import Path

import pytest

from templates.v2.ast import (
    TemplateCompilerError,
    compile_template_render_plan,
    decode_template_ast,
    materialize_render_plan,
    project_content_schema,
)


ROOT = Path(__file__).resolve().parents[4]
FIXTURE = (
    ROOT
    / "contracts"
    / "template_v2"
    / "fixtures"
    / "repeatable-grouped-content.a3352f3c.json"
)


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _content(positioned_count: int = 2) -> dict:
    return {
        "regular_cards": {"cards": [{"card_title": "A"}, {"card_title": "B"}]},
        "positioned_callouts": {
            "items": [
                {"item_title": f"Item {index}"}
                for index in range(positioned_count)
            ]
        },
    }


def test_a335_fixture_decodes_to_regular_and_positioned_repeat_nodes() -> None:
    payload = _payload()
    ast = decode_template_ast(payload, source_version="a3352f3c")

    assert ast.source_dialect == "a3352f3c"
    assert ast.source == payload
    repeats = [
        repeat
        for component in ast.layouts[0].components
        for repeat in component.repeats
    ]
    assert [(repeat.arrangement, repeat.min_items, repeat.max_items) for repeat in repeats] == [
        ("flow", 1, 3),
        ("positioned", 1, 3),
    ]
    positioned = repeats[1]
    assert positioned.source_indices == (1, 2, 3)
    assert [anchor.geometry.x for anchor in positioned.anchors] == [320.0, 120.0, 520.0]


def test_one_render_plan_preserves_flow_and_irregular_geometry() -> None:
    ast = decode_template_ast(_payload(), source_version="a3352f3c")
    plan = compile_template_render_plan(
        ast, layout_id="canonical_repeat_fixture", content=_content()
    )
    rendered = materialize_render_plan(plan)

    regular = rendered["components"][0]["elements"][0]["children"]
    positioned = rendered["components"][1]["elements"]
    assert len(regular) == 2
    assert [item["position"]["x"] for item in positioned[1:]] == [320, 120]
    assert positioned[0]["type"] == "vector"
    assert all("__canonical_source_index" not in item for item in positioned)


def test_content_schema_is_projected_from_the_same_positioned_repeat_node() -> None:
    ast = decode_template_ast(_payload(), source_version="a3352f3c")
    schema = project_content_schema(ast, layout_id="canonical_repeat_fixture")

    positioned = schema["properties"]["positioned_callouts"]
    items = positioned["properties"]["items"]
    assert items["type"] == "array"
    assert (items["minItems"], items["maxItems"]) == (1, 3)
    assert "item_title_1" in items["items"]["properties"]


def test_positioned_repeat_never_duplicates_last_anchor() -> None:
    ast = decode_template_ast(_payload(), source_version="a3352f3c")
    with pytest.raises(TemplateCompilerError, match="count_out_of_bounds"):
        compile_template_render_plan(
            ast, layout_id="canonical_repeat_fixture", content=_content(4)
        )


def test_unknown_upstream_discriminator_is_lossless_but_review_required() -> None:
    payload = _payload()
    payload["layouts"][0]["components"][0]["elements"].append(
        {"type": "future-widget", "x-new-field": {"preserved": True}}
    )
    ast = decode_template_ast(payload, source_version="future-sha")

    assert ast.source == payload
    assert ast.capabilities.review_required is True
    assert ast.capabilities.html_render is False
    with pytest.raises(TemplateCompilerError, match="review_required"):
        compile_template_render_plan(
            ast, layout_id="canonical_repeat_fixture", content=_content()
        )
