import json
from typing import Any

import dirtyjson  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictSchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NoArgsInput(StrictSchemaModel):
    pass


class GetSlideAtIndexInput(StrictSchemaModel):
    index: int = Field(ge=0, le=1000)


class SearchSlidesInput(StrictSchemaModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(ge=1, le=10)


class GetContentSchemaFromLayoutIdInput(StrictSchemaModel):
    layout_id: str = Field(alias="layoutId", min_length=1, max_length=200)

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class GenerateImageInput(StrictSchemaModel):
    prompt: str = Field(min_length=1, max_length=4000)


class GenerateIconInput(StrictSchemaModel):
    query: str = Field(min_length=1, max_length=1000)


class SaveSlideInput(StrictSchemaModel):
    content: str = Field(
        min_length=2,
        max_length=200000,
        description=(
            "A JSON-serialized object for slide content. "
            "Example: '{\"title\": \"Q4 Revenue\", \"bullets\": [\"North America +22%\"]}'"
        ),
    )
    layout_id: str = Field(alias="layoutId", min_length=1, max_length=200)
    index: int = Field(ge=0, le=1000)
    replace_old_slide_at_index: bool = Field(alias="replaceOldSlideAtIndex")

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        try:
            parsed: Any = dirtyjson.loads(value)
        except Exception:
            parsed = json.loads(value)

        if not isinstance(parsed, dict):
            raise ValueError("'content' must be a JSON object.")

        return value
