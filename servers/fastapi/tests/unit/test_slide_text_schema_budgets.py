from utils.llm_calls.compose_slides import get_system_prompt as composition_prompt
from utils.llm_calls.generate_slide_content import (
    get_system_prompt as slide_content_prompt,
)
from utils.llm_utils import (
    TextLengthLimit,
    extract_text_length_limits,
    format_text_length_guidance,
)


def _schema_with_text_budgets() -> dict:
    return {
        "type": "object",
        "properties": {
            "slides": {
                "type": "array",
                "items": {
                    "oneOf": [
                        {"$ref": "#/$defs/card~1v1"},
                    ]
                },
            }
        },
        "$defs": {
            "card/v1": {
                "type": "object",
                "properties": {
                    "archetype": {"type": "string", "const": "card-grid"},
                    "title": {"type": "string", "maxLength": 40},
                    "body": {"type": "string", "maxLength": 100},
                },
            }
        },
    }


def test_extracts_deterministic_limits_through_array_union_and_escaped_ref():
    assert extract_text_length_limits(_schema_with_text_budgets()) == [
        TextLengthLimit(
            path="slides[].<card-grid>.body",
            recommended=80,
            maximum=100,
        ),
        TextLengthLimit(
            path="slides[].<card-grid>.title",
            recommended=32,
            maximum=40,
        ),
    ]


def test_ref_sibling_max_length_merges_with_referenced_limit():
    schema = {
        "type": "object",
        "properties": {
            "stricter_sibling": {
                "$ref": "#/$defs/copy",
                "maxLength": 10,
            },
            "stricter_target": {
                "$ref": "#/$defs/copy",
                "maxLength": 200,
            },
        },
        "$defs": {
            "copy": {
                "type": "string",
                "maxLength": 100,
            },
        },
    }

    assert extract_text_length_limits(schema) == [
        TextLengthLimit(
            path="stricter_sibling",
            recommended=8,
            maximum=10,
        ),
        TextLengthLimit(
            path="stricter_target",
            recommended=80,
            maximum=100,
        ),
    ]


def test_formats_explicit_rephrase_guidance_without_inventing_limits():
    guidance = format_text_length_guidance(_schema_with_text_budgets())

    assert "# Text Length Budgets" in guidance
    assert (
        "`slides[].<card-grid>.title`: recommended <= 32 characters; "
        "absolute maximum 40 characters."
    ) in guidance
    assert "never end a title or sentence mid-word" in guidance
    assert format_text_length_guidance({"type": "object"}) == ""


def test_both_slide_generation_prompts_receive_schema_text_budgets():
    schema = _schema_with_text_budgets()

    for prompt in (
        slide_content_prompt(response_schema=schema),
        composition_prompt(response_schema=schema),
    ):
        assert "# Text Length Budgets" in prompt
        assert "recommended <= 80 characters" in prompt
        assert "absolute maximum 100 characters" in prompt
