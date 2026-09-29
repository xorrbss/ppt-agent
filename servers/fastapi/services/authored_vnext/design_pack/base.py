"""Shared primitives for the design-pack contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from utils.hashing import canonical_json_digest


Digest = str


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def canonical_digest(value: Any) -> str:
    return canonical_json_digest(value)


class PackModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


__all__ = ["Digest", "PackModel", "canonical_digest"]
