"""Shared contracts and canonical helpers for authored-vNext SQL persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
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


class PublicationConflictError(RuntimeError):
    code = "authored_vnext_publication_conflict"


class SourceDigestMismatchError(RuntimeError):
    code = "authored_vnext_source_digest_mismatch"


@dataclass(frozen=True)
class ExportArtifactInput:
    storage_key: str
    artifact_digest: str
    readback_digest: str
    package_digest: str
    size_bytes: int
    master_digest: str
    spec_digest: str
    asset_manifest_digest: str
    font_manifest_digest: str


def _json(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _export_request_hash(
    request_payload: dict[str, Any],
    *,
    deck_revision: int,
    deck_digest: str,
    master_id: str | None,
    master_revision: int,
    master_digest: str,
    spec_digest: str,
    asset_manifest_digest: str,
    font_manifest_digest: str,
    expected_package_digest: str | None,
    expected_publication_revision: int | None,
    expected_publication_digest: str | None,
    timeout_ms: int,
    max_attempts: int,
    operation_fence: int,
) -> str:
    canonical = {
        "request": request_payload,
        "deckRevision": deck_revision,
        "deckDigest": deck_digest,
        "masterId": master_id,
        "masterRevision": master_revision,
        "masterDigest": master_digest,
        "specDigest": spec_digest,
        "assetManifestDigest": asset_manifest_digest,
        "fontManifestDigest": font_manifest_digest,
        "expectedPackageDigest": expected_package_digest,
        "expectedPublicationRevision": expected_publication_revision,
        "expectedPublicationDigest": expected_publication_digest,
        "timeoutMs": timeout_ms,
        "maxAttempts": max_attempts,
        "operationFence": operation_fence,
    }
    return _hash(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )


def _require_digest(value: str, field: str) -> None:
    if not (
        value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    ):
        raise SourceDigestMismatchError(f"{field} must be canonical sha256")


__all__ = [
    "ExportArtifactInput",
    "ExportJobConflictError",
    "ExportableDeck",
    "OperationFenceError",
    "PublicationConflictError",
    "SourceDigestMismatchError",
    "validate_exportable_deck_payload",
]

