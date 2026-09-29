"""Shared primitives for the design-pack contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from utils.naming import snake_to_camel
from utils.hashing import canonical_json_digest


Digest = str


def canonical_digest(value: Any) -> str:
    return canonical_json_digest(value)


class PackModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=snake_to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


__all__ = ["Digest", "PackModel", "canonical_digest"]
