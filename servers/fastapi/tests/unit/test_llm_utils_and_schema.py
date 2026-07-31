from utils.llm_utils import extract_structured_content, serialize_structured_content
from utils.schema_utils import (
    ensure_array_schemas_have_items,
    get_schema_validation_errors,
)


def test_extract_structured_content_from_json_text():
    payload = extract_structured_content('{"slides": [{"content": "A"}]}')
    assert payload == {"slides": [{"content": "A"}]}


def test_serialize_structured_content_prefers_json_serialization():
    serialized = serialize_structured_content({"slides": [{"content": "A"}]})
    assert serialized == '{"slides": [{"content": "A"}]}'


def test_get_schema_validation_errors_reports_path_and_message():
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "maxLength": 5},
        },
        "required": ["title"],
        "additionalProperties": False,
    }
    errors = get_schema_validation_errors(schema, {"title": "too long title"}, strict=False)
    assert errors
    assert any("too long" in e.lower() for e in errors)


def test_ensure_array_schemas_have_items_adds_missing_items_recursively():
    schema = {
        "type": "object",
        "properties": {
            "slides": {
                "type": "array",
                "items": {"type": "object", "properties": {"tags": {"type": "array"}}},
            }
        },
    }

    fixed = ensure_array_schemas_have_items(schema)

    assert fixed["properties"]["slides"]["items"]["properties"]["tags"]["items"] == {
        "type": "string"
    }


def test_legacy_slide_prompt_includes_selected_layout_text_budgets():
    from utils.llm_calls.generate_slide_content import get_system_prompt

    prompt = get_system_prompt(
        response_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 50},
                "cards": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "body": {"type": "string", "maxLength": 100},
                        },
                    },
                },
            },
        }
    )

    assert "`title`: recommended <= 40 characters; absolute maximum 50" in prompt
    assert "`cards[].body`: recommended <= 80 characters; absolute maximum 100" in prompt
