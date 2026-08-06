"""Every `strict` chat tool must satisfy the provider's strict-schema rules.

`llmai.shared.schema.get_schema_as_dict` discards its `strict` argument and
forwards the raw Pydantic schema, so nothing downstream repairs a tool schema.
A property that is optional in the model is therefore omitted from `required`
and the provider rejects the whole request with a 400 — which surfaces to the
user as a chat turn that silently fails.
"""

import pytest
from pydantic import BaseModel

from services.chat import schemas as chat_schemas


def _strict_tool_models() -> list[type[BaseModel]]:
    models: list[type[BaseModel]] = []
    for name in dir(chat_schemas):
        candidate = getattr(chat_schemas, name)
        if not isinstance(candidate, type) or not issubclass(candidate, BaseModel):
            continue
        if candidate.__name__ in {
            "BaseModel",
            "StrictSchemaModel",
            "OpenAIStrictSchemaModel",
        }:
            continue
        models.append(candidate)
    return models


def _objects(schema: object):
    if isinstance(schema, dict):
        if schema.get("type") == "object" or "properties" in schema:
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


@pytest.mark.parametrize(
    "model", _strict_tool_models(), ids=lambda model: model.__name__
)
def test_strict_tool_schema_requires_every_property(model: type[BaseModel]) -> None:
    for node in _objects(model.model_json_schema()):
        properties = set(node.get("properties", {}))
        if not properties:
            continue
        optional = properties - set(node.get("required", []))
        assert not optional, (
            f"{model.__name__} leaves {sorted(optional)} out of `required`; "
            "declare it `T | None = Field(...)` on OpenAIStrictSchemaModel "
            "instead of giving it a default."
        )
        assert node.get("additionalProperties") is False, (
            f"{model.__name__} must forbid additional properties."
        )


def test_save_slide_input_accepts_an_omitted_expected_revision() -> None:
    """The model may leave the nullable field out; validation fills it in."""

    parsed = chat_schemas.SaveSlideInput.model_validate(
        {
            "content": '{"title": "제목"}',
            "layoutId": "authored:content",
            "index": 0,
            "replaceOldSlideAtIndex": True,
        }
    )
    assert parsed.expected_revision is None


def test_save_slide_input_still_reads_an_explicit_expected_revision() -> None:
    parsed = chat_schemas.SaveSlideInput.model_validate(
        {
            "content": '{"title": "제목"}',
            "layoutId": "authored:content",
            "index": 0,
            "replaceOldSlideAtIndex": False,
            "expectedRevision": "7",
        }
    )
    assert parsed.expected_revision == "7"
