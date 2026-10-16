"""User-authored header instructions; execution policy remains server-owned."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_serializer


HeaderValueName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$", strict=True)]
HeaderValueText = Annotated[str, StringConstraints(min_length=1, max_length=512, strict=True)]
HeaderValues = Annotated[dict[HeaderValueName, HeaderValueText], Field(max_length=16)]


def validate_header_values(value: dict[str, str]) -> dict[str, str]:
    if set(value) & {"title", "page_number", "page_count"}:
        raise ValueError("svg_common_header_reserved_value")
    if any(not item.strip() for item in value.values()):
        raise ValueError("svg_common_header_empty_value")
    return value


class CommonHeaderPageValues(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    page_index: int = Field(ge=0, le=99, strict=True)
    values: HeaderValues

    @field_validator("values")
    @classmethod
    def validate_values(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_header_values(value)


class CommonHeaderDesignInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    instructions: str | None = Field(default=None, max_length=4_000)
    reference_snapshot_ids: list[UUID] = Field(default_factory=list, max_length=4)
    document_values: HeaderValues = Field(default_factory=dict)
    page_values: list[CommonHeaderPageValues] = Field(
        default_factory=list, max_length=100,
    )

    @model_serializer(mode="wrap")
    def serialize_supplied_values(self, handler):
        payload = handler(self)
        for key in ("document_values", "page_values"):
            if key not in self.model_fields_set and not payload.get(key):
                payload.pop(key, None)
        return payload

    @field_validator("document_values")
    @classmethod
    def validate_document_values(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_header_values(value)

    @field_validator("page_values")
    @classmethod
    def distinct_pages(cls, value: list[CommonHeaderPageValues]) -> list[CommonHeaderPageValues]:
        if len({item.page_index for item in value}) != len(value):
            raise ValueError("svg_common_header_page_values_duplicate")
        return value

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("reference_snapshot_ids")
    @classmethod
    def distinct_references(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("svg_common_header_design_reference_duplicate")
        return value
