"""Opaque content-addressed artifact keys."""

from __future__ import annotations

import re
import uuid
from utils.hashing import sha256_hex

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError("artifact digest must be a lowercase SHA-256 value")
    return normalized


def artifact_storage_key(*, owner_id: uuid.UUID, tenant_id: str, digest: str) -> str:
    """Build an opaque, owner-bound object key without exposing a local path."""


    normalized_digest = normalize_sha256(digest)
    normalized_tenant = tenant_id.strip()
    if not normalized_tenant:
        raise ValueError("artifact tenant binding is required")
    tenant_digest = sha256_hex(normalized_tenant.encode("utf-8"))
    return (
        f"v1/tenant/{tenant_digest}/owner/{owner_id}/"
        f"sha256/{normalized_digest[:2]}/{normalized_digest}"
    )
