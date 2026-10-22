"""Digest-fenced AI text-zone resolutions and their runtime materialization."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from utils.naming import snake_to_camel
from models.authored_vnext.master.contract import (
    AITextBinding,
    DeckMasterContract,
    SemanticMasterElement,
)

from utils.hashing import sha256_prefixed


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=snake_to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class AITextZoneDefinition(_FrozenModel):
    element_id: str
    binding: AITextBinding


class AITextZoneResolvedValue(_FrozenModel):
    element_id: str
    value: str = Field(max_length=10000)
    source: Literal["model", "user", "empty", "fallback"]
    hidden: bool = False


class ResolvedAITextZoneSet(_FrozenModel):
    schema_version: Literal["authored-vnext-ai-text-zones/v1"] = (
        "authored-vnext-ai-text-zones/v1"
    )
    deck_id: str
    master_digest: str
    context_digest: str
    bindings_digest: str
    override_digest: str
    values: tuple[AITextZoneResolvedValue, ...]
    digest: str


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_prefixed(payload)


def discover_ai_text_zones(
    contract: DeckMasterContract,
) -> tuple[AITextZoneDefinition, ...]:
    zones = []
    for element in contract.semantic_elements:
        binding = element.content.ai_binding
        if (
            element.content.role == "ai-text-zone"
            and binding is not None
            and element.editable_by_ai
        ):
            zones.append(AITextZoneDefinition(element_id=element.id, binding=binding))
    return tuple(sorted(zones, key=lambda zone: zone.element_id))


def materialize_ai_text_zone_contract(
    contract: DeckMasterContract,
    resolved: ResolvedAITextZoneSet,
    *,
    expected_master_digest: str | None = None,
) -> DeckMasterContract:
    """Return a runtime-only contract with text/visibility changed, never geometry/style."""

    core = resolved.model_dump(mode="json", exclude={"digest"})
    if resolved.digest != _digest(core):
        raise ValueError("ai text zone resolution digest mismatch")
    if (
        expected_master_digest is not None
        and resolved.master_digest != expected_master_digest
    ):
        raise ValueError("ai text zone resolution master mismatch")
    zones = discover_ai_text_zones(contract)
    if resolved.bindings_digest != _digest(
        [zone.model_dump(mode="json") for zone in zones]
    ):
        raise ValueError("ai text zone resolution binding mismatch")
    expected_ids = {zone.element_id for zone in zones}
    actual_ids = [value.element_id for value in resolved.values]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != expected_ids:
        raise ValueError("ai text zone resolution element mismatch")
    bindings = {zone.element_id: zone.binding for zone in zones}
    if any(
        bindings[value.element_id].max_chars is not None
        and len(value.value) > bindings[value.element_id].max_chars
        for value in resolved.values
    ):
        raise ValueError("ai text zone resolution exceeds maxChars")
    by_id = {value.element_id: value for value in resolved.values}
    elements: list[SemanticMasterElement] = []
    for element in contract.semantic_elements:
        value = by_id.get(element.id)
        if value is None:
            elements.append(element)
            continue
        content = element.content.model_copy(update={"text": value.value})
        elements.append(
            element.model_copy(
                update={
                    "content": content,
                    "visibility": "hidden" if value.hidden else element.visibility,
                }
            )
        )
    return contract.model_copy(update={"semantic_elements": elements})


__all__ = [
    "AITextZoneDefinition",
    "AITextZoneResolvedValue",
    "ResolvedAITextZoneSet",
    "discover_ai_text_zones",
    "materialize_ai_text_zone_contract",
]
