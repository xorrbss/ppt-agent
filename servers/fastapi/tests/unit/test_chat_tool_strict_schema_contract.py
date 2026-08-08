"""Every tool registered as `strict` must satisfy the provider's strict rules.

`llmai.shared.schema.get_schema_as_dict` discards its `strict` argument and
forwards the raw Pydantic schema, so nothing downstream repairs a tool
definition. A single invalid tool makes the provider reject the entire request
with a 400, which surfaces to the user as a chat turn that silently does
nothing — not as a problem with that one tool.

Strict mode requires every object to list all of its properties in `required`
and to set `additionalProperties: false`. A free-form mapping cannot express
that, so a tool taking one must be registered non-strict instead.
"""

from types import SimpleNamespace

from services.chat.schemas import SaveSlideInput
from services.chat.tools import ChatTools


def _tools():
    memory = SimpleNamespace(sql_session=None, presentation_id=None)
    return ChatTools(memory).get_tool_definitions()


def _objects(schema: object):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for key, value in schema.items():
            if key in {"properties", "$defs"} and isinstance(value, dict):
                for nested in value.values():
                    yield from _objects(nested)
            else:
                yield from _objects(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _objects(value)


def _strict_violations(schema: dict) -> list[str]:
    problems: list[str] = []
    for node in _objects(schema):
        properties = set(node.get("properties", {}))
        optional = properties - set(node.get("required", []))
        if optional:
            problems.append(f"{sorted(optional)} missing from `required`")
        if node.get("additionalProperties") is not False:
            problems.append("object without additionalProperties=false")
    return problems


def test_registered_strict_tools_have_strict_valid_schemas():
    offenders: dict[str, list[str]] = {}
    for tool in _tools():
        if not getattr(tool, "strict", False):
            continue
        schema = tool.input_schema
        schema = (
            schema.model_json_schema()
            if hasattr(schema, "model_json_schema")
            else dict(schema or {})
        )
        problems = _strict_violations(schema)
        if problems:
            offenders[tool.name] = sorted(set(problems))

    assert not offenders, (
        "strict tools with provider-invalid schemas: "
        f"{offenders}. Give the field a concrete shape, or register the tool "
        "with strict=False if it genuinely takes free-form JSON."
    )


def test_the_tool_set_is_not_accidentally_empty():
    """A registry that returns nothing would make the check above vacuous."""

    assert len(_tools()) > 5


def test_save_slide_accepts_an_omitted_nullable_field():
    """`expectedRevision` is required in the schema but may be sent as null."""

    parsed = SaveSlideInput.model_validate(
        {
            "content": '{"title": "제목"}',
            "layoutId": "authored:content",
            "index": 0,
            "replaceOldSlideAtIndex": True,
        }
    )
    assert parsed.expected_revision is None

    supplied = SaveSlideInput.model_validate(
        {
            "content": '{"title": "제목"}',
            "layoutId": "authored:content",
            "index": 0,
            "replaceOldSlideAtIndex": False,
            "expectedRevision": "7",
        }
    )
    assert supplied.expected_revision == "7"
