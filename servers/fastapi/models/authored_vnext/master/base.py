"""Shared strict wire-model primitives."""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _to_camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.capitalize() for part in rest)


class StrictCamelModel(BaseModel):
    """Closed Pydantic model with a camelCase JSON surface."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
        str_strip_whitespace=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _normalize_unicode(cls, value: object) -> object:
        return _normalize_strings(value)


def _normalize_strings(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize_strings(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_strings(item) for item in value)
    if isinstance(value, dict):
        return {
            unicodedata.normalize("NFC", key) if isinstance(key, str) else key: _normalize_strings(item)
            for key, item in value.items()
        }
    return value


Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
MasterId = Annotated[str, Field(pattern=r"^master:[a-z0-9][a-z0-9._-]*$")]
LayoutId = Annotated[str, Field(pattern=r"^layout:[a-z0-9][a-z0-9._-]*$")]
SlideId = Annotated[str, Field(pattern=r"^slide:[a-z0-9][a-z0-9._-]*$")]
DeckId = Annotated[str, Field(pattern=r"^deck:[a-z0-9][a-z0-9._-]*$")]


def stable_slug(value: str) -> str:
    """Return a filesystem-safe stable suffix for already validated IDs."""

    return re.sub(r"[^a-z0-9._-]", "_", value.lower())
