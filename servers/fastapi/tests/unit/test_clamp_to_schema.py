"""Tests for clamp_to_schema — the last-resort salvage that trims over-cap LLM
output to the schema so composition never hard-fails on a slightly-too-long field."""

from utils.get_dynamic_models import get_composition_model_with_n_slides
from utils.llm_utils import (
    clamp_to_schema,
    compact_to_schema,
    extract_text_length_limits,
    format_text_length_guidance,
    get_schema_validation_errors,
)
from utils.schema_utils import prepare_schema_for_validation


def _composition_schema(n_slides: int = 1) -> dict:
    model = get_composition_model_with_n_slides(n_slides)
    return prepare_schema_for_validation(model.model_json_schema(), strict=False)


def test_truncates_string_over_maxlength():
    schema = {"type": "string", "maxLength": 5}
    assert clamp_to_schema("abcdefgh", schema, schema) == "abcde"


def test_trims_array_over_maxitems():
    schema = {"type": "array", "maxItems": 2, "items": {"type": "string", "maxLength": 10}}
    assert clamp_to_schema(["a", "b", "c", "d"], schema, schema) == ["a", "b"]


def test_leaves_valid_value_unchanged():
    schema = {"type": "string", "maxLength": 10}
    assert clamp_to_schema("short", schema, schema) == "short"


def test_clamps_over_cap_slide_via_discriminated_union():
    schema = _composition_schema(1)
    content = {
        "slides": [
            {
                "archetype": "one-column-bullets",
                "title": "t",
                "bullets": [
                    {"text": "x" * 200},  # over the 120 cap
                    {"text": "y" * 90},
                    {"text": "z" * 90},
                    {"text": "a" * 90},
                    {"text": "b" * 90},
                    {"text": "c" * 90},
                    {"text": "d" * 90},  # 7 bullets, over the 6 cap
                ],
                "speaker_note": "",
            }
        ]
    }
    assert get_schema_validation_errors(schema, content, strict=False)  # invalid before
    clamped = clamp_to_schema(content, schema, schema)
    assert not get_schema_validation_errors(schema, clamped, strict=False)  # valid after
    bullets = clamped["slides"][0]["bullets"]
    assert len(bullets) == 6
    assert len(bullets[0]["text"]) == 120


def test_extracts_referenced_nested_union_and_array_text_limits():
    schema = {
        "$defs": {
            "Card": {
                "type": "object",
                "properties": {
                    "archetype": {"const": "card"},
                    "title": {"type": "string", "maxLength": 40},
                    "items": {
                        "type": "array",
                        "items": {
                            "anyOf": [
                                {"type": "string", "maxLength": 25},
                                {
                                    "type": "object",
                                    "properties": {
                                        "caption": {
                                            "type": "string",
                                            "maxLength": 100,
                                        }
                                    },
                                },
                            ]
                        },
                    },
                },
            }
        },
        "type": "object",
        "properties": {
            "slides": {
                "type": "array",
                "items": {
                    "oneOf": [
                        {"$ref": "#/$defs/Card"},
                        {
                            "type": "object",
                            "properties": {
                                "archetype": {"const": "quote"},
                                "text": {"type": "string", "maxLength": 200},
                            },
                        },
                    ]
                },
            }
        },
    }

    limits = {
        item.path: (item.recommended, item.maximum)
        for item in extract_text_length_limits(schema)
    }

    assert limits["slides[].<card>.title"] == (32, 40)
    assert limits["slides[].<card>.items[]"] == (20, 25)
    assert limits["slides[].<card>.items[].caption"] == (80, 100)
    assert limits["slides[].<quote>.text"] == (160, 200)


def test_formats_recommended_and_absolute_budgets_for_prompt():
    guidance = format_text_length_guidance(
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 80},
            },
        }
    )

    assert "`title`: recommended <= 64 characters; absolute maximum 80" in guidance
    assert "Remove repetition, filler, and decorative modifiers" in guidance
    assert "Never end a title or sentence mid-word" in guidance


def test_field_compaction_preserves_siblings_and_complete_sentence():
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "maxLength": 30},
            "body": {"type": "string", "maxLength": 25},
        },
    }
    content = {
        "title": "Unchanged title",
        "body": "Keep this fact. Decorative explanation that does not fit.",
    }

    compacted = compact_to_schema(content, schema, schema)

    assert compacted == {
        "title": "Unchanged title",
        "body": "Keep this fact.",
    }
