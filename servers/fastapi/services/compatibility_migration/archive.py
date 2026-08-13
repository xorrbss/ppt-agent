"""Content-safe, signed archive manifests for compatibility upgrades."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timezone

from models.compatibility_migration import (
    ArchiveDigest,
    ArchiveManifestPayload,
    SignedArchiveManifest,
)

MINIMUM_SIGNING_KEY_BYTES = 32
_SIGNING_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ArchiveManifestError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require_signing_key(signing_key: bytes) -> None:
    if len(signing_key) < MINIMUM_SIGNING_KEY_BYTES:
        raise ArchiveManifestError("compatibility_archive_signing_key_invalid")


def _canonical_payload_bytes(payload: ArchiveManifestPayload) -> bytes:
    return json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(role: str, value: bytes) -> ArchiveDigest:
    return ArchiveDigest(
        role=role,
        value=hashlib.sha256(value).hexdigest(),
        byte_length=len(value),
    )


def build_signed_archive_manifest(
    *,
    source_presentation_id: uuid.UUID,
    target_presentation_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    source_payload: bytes,
    runtime_payload: bytes,
    artifact_payload: bytes,
    signing_key: bytes,
    signing_key_id: str,
    archive_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> SignedArchiveManifest:
    """Hash payloads and sign metadata without retaining payloads, paths or secrets."""

    _require_signing_key(signing_key)
    if _SIGNING_KEY_ID_PATTERN.fullmatch(signing_key_id) is None:
        raise ArchiveManifestError("compatibility_archive_signing_key_id_invalid")
    owner_scope = "anonymous" if owner_id is None else str(owner_id)
    payload = ArchiveManifestPayload(
        archive_id=archive_id or uuid.uuid4(),
        source_presentation_id=source_presentation_id,
        target_presentation_id=target_presentation_id,
        owner_scope_sha256=hashlib.sha256(owner_scope.encode("utf-8")).hexdigest(),
        created_at=created_at or datetime.now(timezone.utc),
        digests=(
            _digest("source", source_payload),
            _digest("runtime", runtime_payload),
            _digest("artifact", artifact_payload),
        ),
    )
    signature = hmac.new(
        signing_key,
        _canonical_payload_bytes(payload),
        hashlib.sha256,
    ).hexdigest()
    return SignedArchiveManifest(
        payload=payload,
        signing_key_id=signing_key_id,
        signature=signature,
    )


def verify_signed_archive_manifest(
    manifest: SignedArchiveManifest,
    *,
    signing_key: bytes,
) -> bool:
    """Verify with constant-time comparison; never expose the signing key."""

    _require_signing_key(signing_key)
    expected = hmac.new(
        signing_key,
        _canonical_payload_bytes(manifest.payload),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, manifest.signature)


__all__ = [
    "ArchiveManifestError",
    "MINIMUM_SIGNING_KEY_BYTES",
    "build_signed_archive_manifest",
    "verify_signed_archive_manifest",
]
