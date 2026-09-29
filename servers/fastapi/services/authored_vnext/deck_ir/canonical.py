"""Canonical JSON, digest sealing, and deterministic Deck IR QIDs."""

from __future__ import annotations

from collections.abc import Mapping
import re
import unicodedata
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from utils.naming import snake_to_camel
from services.authored_vnext.semantic.canonical import (
    canonical_digest as _canonical_digest,
    canonical_json as _canonical_json,
)

from .contracts import DeckIRV1, SEALED_CONTRACT_DIGEST_FIELDS


_QID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\.[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
ModelT = TypeVar("ModelT", bound=BaseModel)


def canonical_json(value: Any) -> str:
    """Use the repository canonical-json-unicode-nfc-f64-v1 implementation."""

    return _canonical_json(value)


def canonical_digest(value: Any) -> str:
    return _canonical_digest(value)


def derive_qid(slide_key: str, element_key: str) -> str:
    """Derive identity from validated semantic keys; never accept a model QID."""

    value = unicodedata.normalize("NFC", f"{slide_key}.{element_key}").casefold()
    if _QID_RE.fullmatch(value) is None:
        raise ValueError("deck_ir_qid_invalid")
    return value


def derive_qid_registry(deck: DeckIRV1) -> dict[str, str]:
    registry: dict[str, str] = {}
    for slide in deck.slides:
        for component in slide.components:
            qid = derive_qid(slide.slide_key, component.element_key)
            if qid in registry:
                raise ValueError("deck_ir_qid_duplicate")
            registry[qid] = f"{slide.slide_key}:{component.element_key}"
    return registry


def seal_contract(
    value: ModelT | Mapping[str, Any],
    digest_field: str | None = None,
    *,
    model_type: type[ModelT] | None = None,
) -> ModelT | dict[str, Any]:
    """Return a copy with its self-digest calculated over canonical JSON.

    Mappings need either ``digest_field`` or ``model_type``.  Model instances
    use the registry in ``contracts`` unless a field is supplied explicitly.
    """

    if isinstance(value, BaseModel):
        field = digest_field or SEALED_CONTRACT_DIGEST_FIELDS.get(type(value))
        if field is None:
            raise ValueError("deck_ir_digest_field_unknown")
        alias = value.__class__.model_fields[field].alias or snake_to_camel(field)
        payload = value.model_dump(mode="json", by_alias=True)
        payload.pop(alias, None)
        return cast(ModelT, value.model_copy(update={field: canonical_digest(payload)}))

    if model_type is not None:
        field = digest_field or SEALED_CONTRACT_DIGEST_FIELDS.get(model_type)
        if field is None:
            raise ValueError("deck_ir_digest_field_unknown")
        alias = model_type.model_fields[field].alias or snake_to_camel(field)
    elif digest_field is not None:
        field = digest_field
        alias = snake_to_camel(field)
    else:
        raise ValueError("deck_ir_digest_field_unknown")
    payload = dict(value)
    payload.pop(field, None)
    payload.pop(alias, None)
    if model_type is None:
        return {**payload, alias: canonical_digest(payload)}

    # Canonical identity is over the normalized wire contract, including
    # Pydantic defaults.  Provider output may omit optional fields, so sealing
    # the raw mapping first would produce a digest that changes immediately on
    # model validation.  A valid placeholder lets the strict model materialize
    # its complete canonical payload before the real digest is calculated.
    placeholder = "sha256:" + ("0" * 64)
    normalized = model_type.model_validate({**payload, alias: placeholder})
    normalized_payload = normalized.model_dump(mode="json", by_alias=True)
    normalized_payload.pop(alias, None)
    return normalized.model_copy(
        update={field: canonical_digest(normalized_payload)}
    )


__all__ = [
    "canonical_digest",
    "canonical_json",
    "derive_qid",
    "derive_qid_registry",
    "seal_contract",
]
