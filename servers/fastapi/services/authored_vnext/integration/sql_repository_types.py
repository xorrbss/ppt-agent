"""Shared contracts and canonical helpers for authored-vNext SQL persistence."""

from __future__ import annotations

from typing import Any

from models.authored_vnext.deck.operations import DeckGenerationSnapshot
from models.authored_vnext.master.slide import DeckSnapshot


ExportableDeck = DeckSnapshot | DeckGenerationSnapshot


def validate_exportable_deck_payload(payload: object) -> ExportableDeck:
    if not isinstance(payload, dict):
        raise ValueError("authored_vnext_deck_payload_invalid")
    schema_version = payload.get("schemaVersion")
    if schema_version == "deck-document/v1":
        return DeckSnapshot.model_validate(payload)
    if schema_version == "deck-generation-snapshot/v1":
        return DeckGenerationSnapshot.model_validate(payload)
    raise ValueError("authored_vnext_deck_schema_unsupported")


class ExportJobConflictError(RuntimeError):
    code = "authored_vnext_export_job_conflict"


class OperationFenceError(RuntimeError):
    code = "authored_vnext_operation_fenced"


def _json(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


__all__ = [
    "ExportJobConflictError",
    "ExportableDeck",
    "OperationFenceError",
    "validate_exportable_deck_payload",
]

