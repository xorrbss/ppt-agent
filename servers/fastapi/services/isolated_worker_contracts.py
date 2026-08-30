"""Sessionless capability contract for isolated render and export workers.

The wire contract intentionally carries no filesystem path, URL, cookie, bearer
credential, provider secret, or canonical database lookup key.  An operation
claim may delegate only one immutable ``ArtifactRef`` to one role and attempt.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


ARTIFACT_REF_SCHEMA = "artifact-ref/v1"
ISOLATED_JOB_SCHEMA = "ppt-isolated-worker-job/v1"
ISOLATED_RESULT_SCHEMA = "ppt-isolated-worker-result/v1"
CAPABILITY_SCHEMA = "ppt-worker-capability/v1"
WorkerRole = Literal["render", "export"]

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "bearer",
        "cookie",
        "cookieheader",
        "credential",
        "password",
        "session",
        "sessioncookie",
        "sessiontoken",
        "token",
    }
)


class IsolatedWorkerContractError(ValueError):
    """The caller or worker violated the closed public-worker contract."""


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _b64encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _b64decode(payload: str) -> bytes:
    try:
        padding = "=" * (-len(payload) % 4)
        return base64.urlsafe_b64decode(payload + padding)
    except (ValueError, UnicodeEncodeError, binascii.Error) as error:
        raise IsolatedWorkerContractError("worker capability is malformed") from error


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def reject_sensitive_worker_payload(value: Any) -> None:
    """Reject credential-like fields recursively before serialization."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise IsolatedWorkerContractError("worker payload keys must be strings")
            normalized = _normalized_key(key)
            if normalized in _SENSITIVE_KEYS or normalized.endswith("apikey"):
                raise IsolatedWorkerContractError(
                    "worker payload contains a forbidden credential field"
                )
            reject_sensitive_worker_payload(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            reject_sensitive_worker_payload(child)


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    *,
    label: str,
) -> None:
    if frozenset(value) != expected:
        raise IsolatedWorkerContractError(f"{label} has an invalid shape")


def _require_uuid(value: Any, *, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise IsolatedWorkerContractError(f"{label} must be a UUID") from error


def _require_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise IsolatedWorkerContractError(f"{label} must be a lowercase SHA-256")
    return value


def _validate_storage_key(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise IsolatedWorkerContractError("artifact storage key is invalid")
    if (
        "\\" in value
        or "://" in value
        or value.startswith(("/", "."))
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise IsolatedWorkerContractError(
            "artifact storage key must be a relative content-addressed key"
        )
    return value


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: uuid.UUID
    owner_id: uuid.UUID
    tenant_id: str
    kind: str
    sha256: str
    size_bytes: int
    media_type: str
    storage_key: str
    created_by_operation_id: uuid.UUID
    retention_class: str

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "ArtifactRef":
        reject_sensitive_worker_payload(value)
        _require_exact_keys(
            value,
            frozenset(
                {
                    "schemaVersion",
                    "artifactId",
                    "ownerId",
                    "tenantId",
                    "kind",
                    "sha256",
                    "sizeBytes",
                    "mediaType",
                    "storageKey",
                    "createdByOperationId",
                    "retentionClass",
                }
            ),
            label="artifact ref",
        )
        if value.get("schemaVersion") != ARTIFACT_REF_SCHEMA:
            raise IsolatedWorkerContractError("artifact ref schema is unsupported")
        tenant_id = value.get("tenantId")
        kind = value.get("kind")
        media_type = value.get("mediaType")
        retention_class = value.get("retentionClass")
        size_bytes = value.get("sizeBytes")
        for label, candidate in (
            ("tenant id", tenant_id),
            ("artifact kind", kind),
            ("media type", media_type),
            ("retention class", retention_class),
        ):
            if not isinstance(candidate, str) or not candidate or len(candidate) > 255:
                raise IsolatedWorkerContractError(f"{label} is invalid")
        if (
            not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 0
        ):
            raise IsolatedWorkerContractError("artifact size is invalid")
        return cls(
            artifact_id=_require_uuid(value.get("artifactId"), label="artifact id"),
            owner_id=_require_uuid(value.get("ownerId"), label="artifact owner id"),
            tenant_id=tenant_id,
            kind=kind,
            sha256=_require_sha256(value.get("sha256"), label="artifact digest"),
            size_bytes=size_bytes,
            media_type=media_type,
            storage_key=_validate_storage_key(value.get("storageKey")),
            created_by_operation_id=_require_uuid(
                value.get("createdByOperationId"),
                label="artifact operation id",
            ),
            retention_class=retention_class,
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "schemaVersion": ARTIFACT_REF_SCHEMA,
            "artifactId": str(self.artifact_id),
            "ownerId": str(self.owner_id),
            "tenantId": self.tenant_id,
            "kind": self.kind,
            "sha256": self.sha256,
            "sizeBytes": self.size_bytes,
            "mediaType": self.media_type,
            "storageKey": self.storage_key,
            "createdByOperationId": str(self.created_by_operation_id),
            "retentionClass": self.retention_class,
        }


@dataclass(frozen=True, slots=True)
class IsolatedWorkerJob:
    role: WorkerRole
    operation_id: uuid.UUID
    attempt_token: str
    fencing_token: int
    input_digest: str
    input_artifact: ArtifactRef
    capability: str
    deadline_epoch_seconds: int

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "IsolatedWorkerJob":
        reject_sensitive_worker_payload(value)
        _require_exact_keys(
            value,
            frozenset(
                {
                    "schemaVersion",
                    "role",
                    "operationId",
                    "attemptToken",
                    "fencingToken",
                    "inputDigest",
                    "inputArtifact",
                    "capability",
                    "deadlineEpochSeconds",
                }
            ),
            label="isolated worker job",
        )
        if value.get("schemaVersion") != ISOLATED_JOB_SCHEMA:
            raise IsolatedWorkerContractError("isolated worker job schema is unsupported")
        role = value.get("role")
        if role not in {"render", "export"}:
            raise IsolatedWorkerContractError("isolated worker role is invalid")
        attempt_token = value.get("attemptToken")
        capability = value.get("capability")
        fencing_token = value.get("fencingToken")
        deadline = value.get("deadlineEpochSeconds")
        artifact = value.get("inputArtifact")
        if not isinstance(attempt_token, str) or not 16 <= len(attempt_token) <= 256:
            raise IsolatedWorkerContractError("attempt token is invalid")
        if not isinstance(capability, str) or not capability or len(capability) > 4096:
            raise IsolatedWorkerContractError("worker capability is invalid")
        if (
            not isinstance(fencing_token, int)
            or isinstance(fencing_token, bool)
            or fencing_token < 0
        ):
            raise IsolatedWorkerContractError("fencing token is invalid")
        if not isinstance(deadline, int) or isinstance(deadline, bool) or deadline <= 0:
            raise IsolatedWorkerContractError("worker deadline is invalid")
        if not isinstance(artifact, Mapping):
            raise IsolatedWorkerContractError("input artifact is invalid")
        parsed_artifact = ArtifactRef.from_wire(artifact)
        input_digest = _require_sha256(
            value.get("inputDigest"), label="worker input digest"
        )
        if not hmac.compare_digest(input_digest, parsed_artifact.sha256):
            raise IsolatedWorkerContractError("worker input digest does not match artifact")
        operation_id = _require_uuid(value.get("operationId"), label="operation id")
        return cls(
            role=role,
            operation_id=operation_id,
            attempt_token=attempt_token,
            fencing_token=fencing_token,
            input_digest=input_digest,
            input_artifact=parsed_artifact,
            capability=capability,
            deadline_epoch_seconds=deadline,
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "schemaVersion": ISOLATED_JOB_SCHEMA,
            "role": self.role,
            "operationId": str(self.operation_id),
            "attemptToken": self.attempt_token,
            "fencingToken": self.fencing_token,
            "inputDigest": self.input_digest,
            "inputArtifact": self.input_artifact.to_wire(),
            "capability": self.capability,
            "deadlineEpochSeconds": self.deadline_epoch_seconds,
        }


@dataclass(frozen=True, slots=True)
class CapabilityClaims:
    role: WorkerRole
    operation_id: uuid.UUID
    owner_id: uuid.UUID
    tenant_id: str
    artifact_id: uuid.UUID
    input_digest: str
    attempt_token_sha256: str
    fencing_token: int
    expires_at: int
    nonce: str


class CapabilityReplayGuard:
    """Bounded process-local replay defense; the DB fence remains authoritative."""

    def __init__(self, *, max_consumed: int = 4096) -> None:
        self._max_consumed = max_consumed
        self._consumed: dict[str, int] = {}
        self._lock = threading.Lock()

    def consume(self, nonce: str, *, expires_at: int, now: int) -> None:
        with self._lock:
            self._consumed = {
                key: expiry for key, expiry in self._consumed.items() if expiry >= now
            }
            if nonce in self._consumed:
                raise IsolatedWorkerContractError("worker capability was already consumed")
            if len(self._consumed) >= self._max_consumed:
                oldest = min(self._consumed, key=self._consumed.get)
                self._consumed.pop(oldest, None)
            self._consumed[nonce] = expires_at


def issue_worker_capability(
    job: IsolatedWorkerJob,
    *,
    signing_key: bytes,
    ttl_seconds: int = 300,
    now: int | None = None,
    nonce: str | None = None,
) -> str:
    if len(signing_key) < 32:
        raise IsolatedWorkerContractError("worker capability signing key is too short")
    if not 1 <= ttl_seconds <= 600:
        raise IsolatedWorkerContractError("worker capability TTL is invalid")
    issued_at = int(time.time()) if now is None else now
    payload = {
        "schemaVersion": CAPABILITY_SCHEMA,
        "role": job.role,
        "operationId": str(job.operation_id),
        "ownerId": str(job.input_artifact.owner_id),
        "tenantId": job.input_artifact.tenant_id,
        "artifactId": str(job.input_artifact.artifact_id),
        "inputDigest": job.input_digest,
        "attemptTokenSha256": hashlib.sha256(
            job.attempt_token.encode("utf-8")
        ).hexdigest(),
        "fencingToken": job.fencing_token,
        "expiresAt": issued_at + ttl_seconds,
        "nonce": nonce or uuid.uuid4().hex,
    }
    encoded = _b64encode(_canonical_json(payload))
    signature = hmac.new(signing_key, encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_b64encode(signature)}"


def verify_and_consume_worker_capability(
    job: IsolatedWorkerJob,
    *,
    signing_key: bytes,
    replay_guard: CapabilityReplayGuard,
    now: int | None = None,
) -> CapabilityClaims:
    if len(signing_key) < 32:
        raise IsolatedWorkerContractError("worker capability signing key is too short")
    try:
        encoded, supplied_signature = job.capability.split(".", 1)
    except ValueError as error:
        raise IsolatedWorkerContractError("worker capability is malformed") from error
    expected = hmac.new(signing_key, encoded.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64decode(supplied_signature)):
        raise IsolatedWorkerContractError("worker capability signature is invalid")
    try:
        payload = json.loads(_b64decode(encoded))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IsolatedWorkerContractError("worker capability payload is invalid") from error
    if not isinstance(payload, Mapping):
        raise IsolatedWorkerContractError("worker capability payload is invalid")
    _require_exact_keys(
        payload,
        frozenset(
            {
                "schemaVersion",
                "role",
                "operationId",
                "ownerId",
                "tenantId",
                "artifactId",
                "inputDigest",
                "attemptTokenSha256",
                "fencingToken",
                "expiresAt",
                "nonce",
            }
        ),
        label="worker capability",
    )
    if payload.get("schemaVersion") != CAPABILITY_SCHEMA:
        raise IsolatedWorkerContractError("worker capability schema is unsupported")
    expires_at = payload.get("expiresAt")
    nonce = payload.get("nonce")
    current = int(time.time()) if now is None else now
    if not isinstance(expires_at, int) or expires_at < current:
        raise IsolatedWorkerContractError("worker capability is expired")
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32,64}", nonce):
        raise IsolatedWorkerContractError("worker capability nonce is invalid")
    expected_attempt = hashlib.sha256(job.attempt_token.encode("utf-8")).hexdigest()
    bindings = (
        payload.get("role") == job.role,
        payload.get("operationId") == str(job.operation_id),
        payload.get("ownerId") == str(job.input_artifact.owner_id),
        payload.get("tenantId") == job.input_artifact.tenant_id,
        payload.get("artifactId") == str(job.input_artifact.artifact_id),
        payload.get("inputDigest") == job.input_digest,
        payload.get("attemptTokenSha256") == expected_attempt,
        payload.get("fencingToken") == job.fencing_token,
        expires_at <= job.deadline_epoch_seconds,
    )
    if not all(bindings):
        raise IsolatedWorkerContractError("worker capability binding mismatch")
    replay_guard.consume(nonce, expires_at=expires_at, now=current)
    return CapabilityClaims(
        role=job.role,
        operation_id=job.operation_id,
        owner_id=job.input_artifact.owner_id,
        tenant_id=job.input_artifact.tenant_id,
        artifact_id=job.input_artifact.artifact_id,
        input_digest=job.input_digest,
        attempt_token_sha256=expected_attempt,
        fencing_token=job.fencing_token,
        expires_at=expires_at,
        nonce=nonce,
    )


__all__ = [
    "ArtifactRef",
    "CapabilityClaims",
    "CapabilityReplayGuard",
    "IsolatedWorkerContractError",
    "IsolatedWorkerJob",
    "issue_worker_capability",
    "reject_sensitive_worker_payload",
    "verify_and_consume_worker_capability",
]
