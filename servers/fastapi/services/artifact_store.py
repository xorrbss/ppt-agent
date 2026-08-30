"""Content-addressed artifact authority and export single-flight operations.

The filesystem implementation in this module is deliberately development-only.
Production callers provide an object-store implementation of
``ArtifactObjectStore``; database rows retain only schema-versioned ArtifactRef
metadata and never an absolute path.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import io
import json
import os
from pathlib import Path
import secrets
from typing import Any, BinaryIO, Iterator, Literal, Protocol
from urllib.parse import urlencode, urlsplit
import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.public_artifact import (
    ArtifactRef,
    PublicArtifact,
    PublicArtifactPublication,
    PublicExportIdentity,
    PublicSlideCheckpoint,
    artifact_storage_key,
    normalize_sha256,
)
from models.sql.public_admission import PublicOperationAdmission
from models.sql.workspace import WorkspaceOperation


class ArtifactStoreError(RuntimeError):
    pass


class ArtifactBindingError(ArtifactStoreError):
    pass


class ArtifactNotFound(ArtifactStoreError):
    pass


class ExportIdentityConflict(ArtifactStoreError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactByteRange:
    """One validated inclusive HTTP byte range."""

    start: int
    end_inclusive: int | None = None

    def __post_init__(self) -> None:
        if self.start < 0 or (
            self.end_inclusive is not None and self.end_inclusive < self.start
        ):
            raise ValueError("artifact byte range is invalid")

    def request_header(self) -> str:
        suffix = "" if self.end_inclusive is None else str(self.end_inclusive)
        return f"bytes={self.start}-{suffix}"

    def content_length(self, object_size: int) -> int:
        if self.start >= object_size:
            raise ArtifactBindingError("artifact.range_not_satisfiable")
        end = object_size - 1 if self.end_inclusive is None else self.end_inclusive
        if end >= object_size:
            raise ArtifactBindingError("artifact.range_not_satisfiable")
        return end - self.start + 1


@dataclass(frozen=True, slots=True)
class ArtifactObjectMetadata:
    size_bytes: int
    media_type: str
    etag: str | None = None
    last_modified: datetime | None = None


@dataclass(slots=True)
class ArtifactReadStream:
    """Caller-owned streaming response; callers must close ``body``."""

    body: BinaryIO
    content_length: int
    media_type: str
    etag: str | None
    content_range: str | None = None

    def close(self) -> None:
        self.body.close()

    def iter_chunks(self, *, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        """Yield bounded response chunks and close the remote body on completion."""

        if chunk_size <= 0:
            raise ValueError("artifact stream chunk size must be positive")
        try:
            while True:
                chunk = self.body.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            self.close()


@dataclass(frozen=True, slots=True)
class SignedArtifactDownload:
    url: str
    expires_at: datetime
    etag: str | None
    range_supported: bool = True


def provisional_artifact_storage_key(
    *, owner_id: uuid.UUID, tenant_id: str, upload_id: uuid.UUID
) -> str:
    normalized_tenant = tenant_id.strip()
    if not normalized_tenant:
        raise ValueError("artifact tenant binding is required")
    tenant_digest = sha256(normalized_tenant.encode("utf-8")).hexdigest()
    return (
        f"provisional/v1/tenant/{tenant_digest}/owner/{owner_id}/upload/{upload_id}"
    )


@dataclass(frozen=True, slots=True)
class ProvisionalArtifactRef:
    """Opaque staging identity bound to exactly one final immutable ArtifactRef."""

    upload_id: uuid.UUID
    artifact: ArtifactRef
    storage_key: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        artifact: ArtifactRef,
        *,
        upload_id: uuid.UUID | None = None,
        created_at: datetime | None = None,
    ) -> "ProvisionalArtifactRef":
        resolved_upload_id = upload_id or uuid.uuid4()
        return cls(
            upload_id=resolved_upload_id,
            artifact=artifact,
            storage_key=provisional_artifact_storage_key(
                owner_id=artifact.owner_id,
                tenant_id=artifact.tenant_id,
                upload_id=resolved_upload_id,
            ),
            created_at=created_at or datetime.now(timezone.utc),
        )

    def validate_binding(self) -> None:
        expected = provisional_artifact_storage_key(
            owner_id=self.artifact.owner_id,
            tenant_id=self.artifact.tenant_id,
            upload_id=self.upload_id,
        )
        if self.storage_key != expected:
            raise ArtifactBindingError("artifact.provisional_binding_invalid")


class _BoundedReader(io.RawIOBase):
    def __init__(self, source: BinaryIO, remaining: int) -> None:
        self._source = source
        self._remaining = remaining

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        requested = self._remaining if size is None or size < 0 else min(size, self._remaining)
        chunk = self._source.read(requested)
        self._remaining -= len(chunk)
        return chunk

    def close(self) -> None:
        try:
            self._source.close()
        finally:
            super().close()


class ArtifactObjectStore(Protocol):
    """Immutable object storage addressed only through a validated ArtifactRef."""

    async def put_bytes(self, artifact: ArtifactRef, payload: bytes) -> None: ...

    async def put_stream(self, artifact: ArtifactRef, source: BinaryIO) -> None: ...

    async def read_bytes(self, artifact: ArtifactRef) -> bytes: ...

    async def open_read_stream(
        self, artifact: ArtifactRef, *, byte_range: ArtifactByteRange | None = None
    ) -> ArtifactReadStream: ...

    async def head(self, artifact: ArtifactRef) -> ArtifactObjectMetadata | None: ...

    async def exists(self, artifact: ArtifactRef) -> bool: ...

    async def delete(self, artifact: ArtifactRef) -> None: ...

    async def create_signed_download(
        self, artifact: ArtifactRef, *, expires_seconds: int
    ) -> SignedArtifactDownload: ...

    async def put_provisional_stream(
        self, provisional: ProvisionalArtifactRef, source: BinaryIO
    ) -> None: ...

    async def commit_provisional(self, provisional: ProvisionalArtifactRef) -> None: ...

    async def delete_provisional(self, provisional: ProvisionalArtifactRef) -> None: ...

    async def sweep_provisional_orphans(
        self, *, older_than: datetime, limit: int
    ) -> int: ...


class LocalDevelopmentArtifactObjectStore:
    """Filesystem adapter/cache for local development, never production authority."""

    def __init__(
        self,
        root: Path,
        *,
        max_object_bytes: int = 512 * 1024 * 1024,
        download_signing_key: str | None = None,
        download_origin: str | None = None,
    ) -> None:
        if max_object_bytes <= 0:
            raise ValueError("artifact object byte limit must be positive")
        if (download_signing_key is None) != (download_origin is None):
            raise ValueError("artifact.local_download_configuration_incomplete")
        self._root = root.resolve()
        self._max_object_bytes = max_object_bytes
        self._download_signing_key = (
            download_signing_key.encode("utf-8") if download_signing_key is not None else None
        )
        self._download_origin = (
            self._normalize_loopback_download_origin(download_origin)
            if download_origin is not None
            else None
        )
        if self._download_signing_key is not None and len(self._download_signing_key) < 32:
            raise ValueError("artifact.local_download_signing_key_too_short")

    @staticmethod
    def _normalize_loopback_download_origin(value: str) -> str:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "http"
            or hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("artifact.local_download_origin_must_be_loopback")
        return value.rstrip("/")

    def _require_download_configuration(self) -> tuple[bytes, str]:
        if self._download_signing_key is None or self._download_origin is None:
            raise ArtifactStoreError("artifact.signed_download_unavailable_for_local_adapter")
        return self._download_signing_key, self._download_origin

    @staticmethod
    def _token_part(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_token_part(value: str) -> bytes:
        try:
            return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ArtifactBindingError("artifact.download_token_invalid") from exc

    def _issue_download_token(self, artifact: ArtifactRef, *, expires_at: datetime) -> str:
        signing_key, _ = self._require_download_configuration()
        payload = json.dumps(
            {
                "artifact": artifact.model_dump(mode="json", by_alias=True),
                "expiresAt": int(expires_at.timestamp()),
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(signing_key, payload, sha256).digest()
        return f"{self._token_part(payload)}.{self._token_part(signature)}"

    def _resolve_download_token(self, *, artifact_id: uuid.UUID, token: str) -> ArtifactRef:
        signing_key, _ = self._require_download_configuration()
        try:
            encoded_payload, encoded_signature = token.split(".", 1)
            payload = self._decode_token_part(encoded_payload)
            signature = self._decode_token_part(encoded_signature)
        except ValueError as exc:
            raise ArtifactBindingError("artifact.download_token_invalid") from exc
        expected_signature = hmac.new(signing_key, payload, sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ArtifactBindingError("artifact.download_token_invalid")
        try:
            decoded = json.loads(payload)
            expires_at = int(decoded["expiresAt"])
            reference = ArtifactRef.model_validate(decoded["artifact"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactBindingError("artifact.download_token_invalid") from exc
        if expires_at <= int(datetime.now(timezone.utc).timestamp()):
            raise ArtifactBindingError("artifact.download_token_expired")
        if reference.artifact_id != artifact_id:
            raise ArtifactBindingError("artifact.download_token_artifact_mismatch")
        return reference

    def _path(self, artifact: ArtifactRef) -> Path:
        expected = artifact_storage_key(
            owner_id=artifact.owner_id,
            tenant_id=artifact.tenant_id,
            digest=artifact.sha256,
        )
        if artifact.storage_key != expected:
            raise ArtifactBindingError("artifact.storage_key_binding_invalid")
        return self._path_for_key(artifact.storage_key)

    def _path_for_key(self, storage_key: str) -> Path:
        candidate = (self._root / Path(*storage_key.split("/"))).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise ArtifactBindingError("artifact.storage_key_escape") from exc
        return candidate

    def _provisional_path(self, provisional: ProvisionalArtifactRef) -> Path:
        provisional.validate_binding()
        return self._path_for_key(provisional.storage_key)

    async def put_bytes(self, artifact: ArtifactRef, payload: bytes) -> None:
        await self.put_stream(artifact, io.BytesIO(payload))

    async def put_stream(self, artifact: ArtifactRef, source: BinaryIO) -> None:
        await asyncio.to_thread(self._put_stream_sync, artifact, source)

    def _put_stream_sync(self, artifact: ArtifactRef, source: BinaryIO) -> None:
        target = self._path(artifact)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            size, digest = self._file_binding(target)
            if digest != artifact.sha256 or size != artifact.size_bytes:
                raise ArtifactStoreError("artifact.immutable_object_conflict")
            return
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            digest = sha256()
            size = 0
            with temporary.open("xb") as handle:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self._max_object_bytes:
                        raise ArtifactStoreError("artifact.object_too_large")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if size != artifact.size_bytes or digest.hexdigest() != artifact.sha256:
                raise ArtifactBindingError("artifact.payload_binding_invalid")
            try:
                os.link(temporary, target)
            except FileExistsError:
                existing_size, existing_digest = self._file_binding(target)
                if existing_size != artifact.size_bytes or existing_digest != artifact.sha256:
                    raise ArtifactStoreError("artifact.immutable_object_conflict")
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _file_binding(path: Path) -> tuple[int, str]:
        digest = sha256()
        size = 0
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    async def read_bytes(self, artifact: ArtifactRef) -> bytes:
        opened = await self.open_read_stream(artifact)
        try:
            payload = await asyncio.to_thread(opened.body.read)
        finally:
            opened.close()
        if len(payload) != artifact.size_bytes or sha256(payload).hexdigest() != artifact.sha256:
            raise ArtifactStoreError("artifact.object_readback_mismatch")
        return payload

    async def head(self, artifact: ArtifactRef) -> ArtifactObjectMetadata | None:
        return await asyncio.to_thread(self._head_sync, artifact)

    def _head_sync(self, artifact: ArtifactRef) -> ArtifactObjectMetadata | None:
        target = self._path(artifact)
        if not target.is_file():
            return None
        size, digest = self._file_binding(target)
        if size != artifact.size_bytes or digest != artifact.sha256:
            raise ArtifactStoreError("artifact.immutable_object_conflict")
        stat = target.stat()
        return ArtifactObjectMetadata(
            size_bytes=size,
            media_type=artifact.media_type,
            etag=f'"sha256:{digest}"',
            last_modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        )

    async def open_read_stream(
        self, artifact: ArtifactRef, *, byte_range: ArtifactByteRange | None = None
    ) -> ArtifactReadStream:
        return await asyncio.to_thread(self._open_read_stream_sync, artifact, byte_range)

    def _open_read_stream_sync(
        self, artifact: ArtifactRef, byte_range: ArtifactByteRange | None
    ) -> ArtifactReadStream:
        metadata = self._head_sync(artifact)
        if metadata is None:
            raise ArtifactNotFound("artifact.object_missing")
        handle = self._path(artifact).open("rb")
        if byte_range is None:
            return ArtifactReadStream(
                body=handle,
                content_length=metadata.size_bytes,
                media_type=metadata.media_type,
                etag=metadata.etag,
            )
        content_length = byte_range.content_length(metadata.size_bytes)
        handle.seek(byte_range.start)
        end = byte_range.start + content_length - 1
        return ArtifactReadStream(
            body=_BoundedReader(handle, content_length),
            content_length=content_length,
            media_type=metadata.media_type,
            etag=metadata.etag,
            content_range=f"bytes {byte_range.start}-{end}/{metadata.size_bytes}",
        )

    async def exists(self, artifact: ArtifactRef) -> bool:
        try:
            return await self.head(artifact) is not None
        except ArtifactStoreError:
            return False

    async def delete(self, artifact: ArtifactRef) -> None:
        await asyncio.to_thread(self._path(artifact).unlink, missing_ok=True)

    async def create_signed_download(
        self, artifact: ArtifactRef, *, expires_seconds: int
    ) -> SignedArtifactDownload:
        if expires_seconds < 120 or expires_seconds > 300:
            raise ValueError("artifact signed download expiry must be between 120 and 300 seconds")
        _, origin = self._require_download_configuration()
        metadata = await self.head(artifact)
        if metadata is None:
            raise ArtifactNotFound("artifact.object_missing")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)
        token = self._issue_download_token(artifact, expires_at=expires_at)
        return SignedArtifactDownload(
            url=(
                f"{origin}/api/v2/artifacts/{artifact.artifact_id}/local-download?"
                f"{urlencode({'token': token})}"
            ),
            expires_at=expires_at,
            etag=metadata.etag,
        )

    async def open_signed_download(
        self,
        *,
        artifact_id: uuid.UUID,
        token: str,
        byte_range: ArtifactByteRange | None = None,
    ) -> ArtifactReadStream:
        reference = self._resolve_download_token(artifact_id=artifact_id, token=token)
        return await self.open_read_stream(reference, byte_range=byte_range)

    async def put_provisional_stream(
        self, provisional: ProvisionalArtifactRef, source: BinaryIO
    ) -> None:
        await asyncio.to_thread(self._put_provisional_stream_sync, provisional, source)

    def _put_provisional_stream_sync(
        self, provisional: ProvisionalArtifactRef, source: BinaryIO
    ) -> None:
        target = self._provisional_path(provisional)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            size, digest = self._file_binding(target)
            if size != provisional.artifact.size_bytes or digest != provisional.artifact.sha256:
                raise ArtifactStoreError("artifact.provisional_object_conflict")
            return
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            digest = sha256()
            size = 0
            with temporary.open("xb") as handle:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self._max_object_bytes:
                        raise ArtifactStoreError("artifact.object_too_large")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if size != provisional.artifact.size_bytes or digest.hexdigest() != provisional.artifact.sha256:
                raise ArtifactBindingError("artifact.payload_binding_invalid")
            try:
                os.link(temporary, target)
            except FileExistsError:
                existing_size, existing_digest = self._file_binding(target)
                if (
                    existing_size != provisional.artifact.size_bytes
                    or existing_digest != provisional.artifact.sha256
                ):
                    raise ArtifactStoreError("artifact.provisional_object_conflict")
        finally:
            temporary.unlink(missing_ok=True)

    async def commit_provisional(self, provisional: ProvisionalArtifactRef) -> None:
        await asyncio.to_thread(self._commit_provisional_sync, provisional)

    def _commit_provisional_sync(self, provisional: ProvisionalArtifactRef) -> None:
        source = self._provisional_path(provisional)
        if not source.is_file():
            raise ArtifactNotFound("artifact.provisional_object_missing")
        size, digest = self._file_binding(source)
        if size != provisional.artifact.size_bytes or digest != provisional.artifact.sha256:
            raise ArtifactStoreError("artifact.provisional_readback_mismatch")
        destination = self._path(provisional.artifact)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, destination)
        except FileExistsError:
            existing_size, existing_digest = self._file_binding(destination)
            if existing_size != size or existing_digest != digest:
                raise ArtifactStoreError("artifact.immutable_object_conflict")
        source.unlink(missing_ok=True)

    async def delete_provisional(self, provisional: ProvisionalArtifactRef) -> None:
        await asyncio.to_thread(self._provisional_path(provisional).unlink, missing_ok=True)

    async def sweep_provisional_orphans(
        self, *, older_than: datetime, limit: int
    ) -> int:
        if older_than.tzinfo is None or limit <= 0:
            raise ValueError("artifact orphan sweep bounds are invalid")
        return await asyncio.to_thread(self._sweep_provisional_orphans_sync, older_than, limit)

    def _sweep_provisional_orphans_sync(self, older_than: datetime, limit: int) -> int:
        root = self._root / "provisional" / "v1"
        if not root.is_dir():
            return 0
        removed = 0
        for candidate in sorted(root.rglob("*")):
            if removed >= limit:
                break
            if not candidate.is_file():
                continue
            modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
            if modified <= older_than:
                candidate.unlink(missing_ok=True)
                removed += 1
        return removed


class S3ArtifactObjectStore:
    """Production S3-compatible immutable object-store adapter.

    The bucket is an operational secret/configuration concern and is not part
    of ArtifactRef.  SHA-256 and owner bindings are repeated in object metadata
    so readback does not trust a key or ETag alone.
    """

    _MULTIPART_PART_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        *,
        bucket: str,
        client: Any | None = None,
        key_prefix: str = "presenton-artifacts",
        server_side_encryption: str = "AES256",
        kms_key_id: str | None = None,
        max_object_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        bucket = bucket.strip()
        key_prefix = key_prefix.strip().strip("/")
        if not bucket:
            raise ValueError("artifact S3 bucket is required")
        if not key_prefix or ".." in key_prefix or "\\" in key_prefix:
            raise ValueError("artifact S3 key prefix is invalid")
        if max_object_bytes <= 0:
            raise ValueError("artifact object byte limit must be positive")
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError("artifact.s3_client_unavailable") from exc
            client = boto3.client("s3")
        self._bucket = bucket
        self._client = client
        self._key_prefix = key_prefix
        self._server_side_encryption = server_side_encryption.strip()
        self._kms_key_id = kms_key_id.strip() if kms_key_id else None
        self._max_object_bytes = max_object_bytes

    def _key(self, artifact: ArtifactRef) -> str:
        expected = artifact_storage_key(
            owner_id=artifact.owner_id,
            tenant_id=artifact.tenant_id,
            digest=artifact.sha256,
        )
        if artifact.storage_key != expected:
            raise ArtifactBindingError("artifact.storage_key_binding_invalid")
        return f"{self._key_prefix}/{artifact.storage_key}"

    def _provisional_key(self, provisional: ProvisionalArtifactRef) -> str:
        provisional.validate_binding()
        return f"{self._key_prefix}/{provisional.storage_key}"

    @staticmethod
    def _metadata(artifact: ArtifactRef) -> dict[str, str]:
        return {
            "sha256": artifact.sha256,
            "owner-id": str(artifact.owner_id),
            "tenant-binding": sha256(artifact.tenant_id.encode("utf-8")).hexdigest(),
            "artifact-kind": artifact.kind,
        }

    @staticmethod
    def _error_code(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return ""
        error = response.get("Error")
        return str(error.get("Code", "")) if isinstance(error, dict) else ""

    def _head_sync(self, artifact: ArtifactRef) -> dict[str, Any] | None:
        return self._head_key_sync(self._key(artifact))

    def _head_key_sync(self, key: str) -> dict[str, Any] | None:
        try:
            return self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if self._error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise ArtifactStoreError("artifact.object_store_head_failed") from exc

    def _validate_head(self, artifact: ArtifactRef, head: dict[str, Any]) -> None:
        metadata = {str(key).lower(): str(value) for key, value in (head.get("Metadata") or {}).items()}
        expected = self._metadata(artifact)
        if int(head.get("ContentLength", -1)) != artifact.size_bytes or any(
            metadata.get(key) != value for key, value in expected.items()
        ):
            raise ArtifactStoreError("artifact.immutable_object_conflict")

    async def put_bytes(self, artifact: ArtifactRef, payload: bytes) -> None:
        await self.put_stream(artifact, io.BytesIO(payload))

    async def put_stream(self, artifact: ArtifactRef, source: BinaryIO) -> None:
        await asyncio.to_thread(self._put_stream_sync, artifact, source)

    def _put_stream_sync(self, artifact: ArtifactRef, source: BinaryIO) -> None:
        existing = self._head_sync(artifact)
        if existing is not None:
            self._validate_head(artifact, existing)
            return
        provisional = ProvisionalArtifactRef.create(artifact)
        self._put_provisional_stream_sync(provisional, source)
        self._commit_provisional_sync(provisional)

    def _multipart_stream_sync(
        self,
        *,
        artifact: ArtifactRef,
        key: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        create_request: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            "ContentType": artifact.media_type,
            "Metadata": metadata,
        }
        if self._server_side_encryption:
            create_request["ServerSideEncryption"] = self._server_side_encryption
        if self._kms_key_id:
            create_request["SSEKMSKeyId"] = self._kms_key_id
        try:
            return self._client.create_multipart_upload(**create_request)
        except Exception as exc:
            raise ArtifactStoreError("artifact.multipart_create_failed") from exc

    def _upload_stream_parts_sync(
        self,
        *,
        artifact: ArtifactRef,
        key: str,
        upload_id: str,
        source: BinaryIO,
    ) -> list[dict[str, Any]]:
        digest = sha256()
        size = 0
        parts: list[dict[str, Any]] = []
        try:
            while True:
                part_payload = bytearray()
                while len(part_payload) < self._MULTIPART_PART_BYTES:
                    chunk = source.read(self._MULTIPART_PART_BYTES - len(part_payload))
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise ArtifactStoreError("artifact.stream_chunk_invalid")
                    part_payload.extend(chunk)
                if not part_payload:
                    break
                size += len(part_payload)
                if size > self._max_object_bytes:
                    raise ArtifactStoreError("artifact.object_too_large")
                digest.update(part_payload)
                part_number = len(parts) + 1
                response = self._client.upload_part(
                    Bucket=self._bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=bytes(part_payload),
                )
                etag = response.get("ETag")
                if not etag:
                    raise ArtifactStoreError("artifact.multipart_part_etag_missing")
                parts.append({"ETag": str(etag), "PartNumber": part_number})
            if size != artifact.size_bytes or digest.hexdigest() != artifact.sha256:
                raise ArtifactBindingError("artifact.payload_binding_invalid")
            return parts
        except ArtifactStoreError:
            raise
        except Exception as exc:
            raise ArtifactStoreError("artifact.multipart_upload_failed") from exc

    def _put_verified_stream_sync(
        self,
        *,
        artifact: ArtifactRef,
        key: str,
        metadata: dict[str, str],
        source: BinaryIO,
    ) -> None:
        if artifact.size_bytes == 0:
            if source.read(1):
                raise ArtifactBindingError("artifact.payload_binding_invalid")
            request: dict[str, Any] = {
                "Bucket": self._bucket,
                "Key": key,
                "Body": b"",
                "ContentLength": 0,
                "ContentType": artifact.media_type,
                "Metadata": metadata,
                "IfNoneMatch": "*",
            }
            if self._server_side_encryption:
                request["ServerSideEncryption"] = self._server_side_encryption
            if self._kms_key_id:
                request["SSEKMSKeyId"] = self._kms_key_id
            try:
                self._client.put_object(**request)
            except Exception as exc:
                raise ArtifactStoreError("artifact.object_store_write_failed") from exc
            return

        created = self._multipart_stream_sync(
            artifact=artifact,
            key=key,
            metadata=metadata,
        )
        upload_id = str(created.get("UploadId") or "")
        if not upload_id:
            raise ArtifactStoreError("artifact.multipart_upload_id_missing")
        completed = False
        try:
            parts = self._upload_stream_parts_sync(
                artifact=artifact,
                key=key,
                upload_id=upload_id,
                source=source,
            )
            self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
            completed = True
        except Exception as exc:
            if not completed:
                try:
                    self._client.abort_multipart_upload(
                        Bucket=self._bucket,
                        Key=key,
                        UploadId=upload_id,
                    )
                except Exception:
                    pass
            if isinstance(exc, ArtifactStoreError):
                raise
            raise ArtifactStoreError("artifact.multipart_complete_failed") from exc

    async def read_bytes(self, artifact: ArtifactRef) -> bytes:
        opened = await self.open_read_stream(artifact)
        try:
            payload = await asyncio.to_thread(opened.body.read)
        finally:
            opened.close()
        if len(payload) != artifact.size_bytes or sha256(payload).hexdigest() != artifact.sha256:
            raise ArtifactStoreError("artifact.object_readback_mismatch")
        return payload

    async def head(self, artifact: ArtifactRef) -> ArtifactObjectMetadata | None:
        response = await asyncio.to_thread(self._head_sync, artifact)
        if response is None:
            return None
        self._validate_head(artifact, response)
        return ArtifactObjectMetadata(
            size_bytes=int(response["ContentLength"]),
            media_type=str(response.get("ContentType") or artifact.media_type),
            etag=str(response["ETag"]) if response.get("ETag") else None,
            last_modified=response.get("LastModified"),
        )

    async def open_read_stream(
        self, artifact: ArtifactRef, *, byte_range: ArtifactByteRange | None = None
    ) -> ArtifactReadStream:
        return await asyncio.to_thread(self._open_read_stream_sync, artifact, byte_range)

    def _open_read_stream_sync(
        self, artifact: ArtifactRef, byte_range: ArtifactByteRange | None
    ) -> ArtifactReadStream:
        head = self._head_sync(artifact)
        if head is None:
            raise ArtifactNotFound("artifact.object_missing")
        self._validate_head(artifact, head)
        request: dict[str, Any] = {"Bucket": self._bucket, "Key": self._key(artifact)}
        expected_length = artifact.size_bytes
        content_range = None
        if byte_range is not None:
            expected_length = byte_range.content_length(artifact.size_bytes)
            request["Range"] = byte_range.request_header()
            end = byte_range.start + expected_length - 1
            content_range = f"bytes {byte_range.start}-{end}/{artifact.size_bytes}"
        try:
            response = self._client.get_object(**request)
        except Exception as exc:
            if self._error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                raise ArtifactNotFound("artifact.object_missing") from exc
            raise ArtifactStoreError("artifact.object_store_read_failed") from exc
        response_length = int(response.get("ContentLength", expected_length))
        if response_length != expected_length:
            close = getattr(response.get("Body"), "close", None)
            if callable(close):
                close()
            raise ArtifactStoreError("artifact.object_range_readback_mismatch")
        return ArtifactReadStream(
            body=response["Body"],
            content_length=response_length,
            media_type=artifact.media_type,
            etag=str(head["ETag"]) if head.get("ETag") else None,
            content_range=str(response.get("ContentRange") or content_range) if content_range else None,
        )

    async def exists(self, artifact: ArtifactRef) -> bool:
        return await self.head(artifact) is not None

    async def delete(self, artifact: ArtifactRef) -> None:
        try:
            await asyncio.to_thread(
                self._client.delete_object,
                Bucket=self._bucket,
                Key=self._key(artifact),
            )
        except Exception as exc:
            raise ArtifactStoreError("artifact.object_store_delete_failed") from exc

    async def create_signed_download(
        self, artifact: ArtifactRef, *, expires_seconds: int
    ) -> SignedArtifactDownload:
        if expires_seconds < 1 or expires_seconds > 300:
            raise ValueError("artifact signed download expiry must be between 1 and 300 seconds")
        metadata = await self.head(artifact)
        if metadata is None:
            raise ArtifactNotFound("artifact.object_missing")
        try:
            url = await asyncio.to_thread(
                self._client.generate_presigned_url,
                "get_object",
                Params={"Bucket": self._bucket, "Key": self._key(artifact)},
                ExpiresIn=expires_seconds,
            )
        except Exception as exc:
            raise ArtifactStoreError("artifact.signed_download_failed") from exc
        return SignedArtifactDownload(
            url=str(url),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_seconds),
            etag=metadata.etag,
        )

    async def put_provisional_stream(
        self, provisional: ProvisionalArtifactRef, source: BinaryIO
    ) -> None:
        await asyncio.to_thread(self._put_provisional_stream_sync, provisional, source)

    def _put_provisional_stream_sync(
        self, provisional: ProvisionalArtifactRef, source: BinaryIO
    ) -> None:
        provisional.validate_binding()
        key = self._provisional_key(provisional)
        existing = self._head_key_sync(key)
        expected_metadata = {
            **self._metadata(provisional.artifact),
            "lifecycle-state": "provisional",
            "upload-id": str(provisional.upload_id),
        }
        if existing is not None:
            metadata = {str(k).lower(): str(v) for k, v in (existing.get("Metadata") or {}).items()}
            if int(existing.get("ContentLength", -1)) != provisional.artifact.size_bytes or any(
                metadata.get(k) != v for k, v in expected_metadata.items()
            ):
                raise ArtifactStoreError("artifact.provisional_object_conflict")
            return
        self._put_verified_stream_sync(
            artifact=provisional.artifact,
            key=key,
            metadata=expected_metadata,
            source=source,
        )
        readback = self._head_key_sync(key)
        if readback is None:
            raise ArtifactStoreError("artifact.object_store_write_readback_missing")
        readback_metadata = {
            str(k).lower(): str(v)
            for k, v in (readback.get("Metadata") or {}).items()
        }
        if int(readback.get("ContentLength", -1)) != provisional.artifact.size_bytes or any(
            readback_metadata.get(k) != v for k, v in expected_metadata.items()
        ):
            raise ArtifactStoreError("artifact.provisional_readback_mismatch")

    async def commit_provisional(self, provisional: ProvisionalArtifactRef) -> None:
        await asyncio.to_thread(self._commit_provisional_sync, provisional)

    def _commit_provisional_sync(self, provisional: ProvisionalArtifactRef) -> None:
        provisional.validate_binding()
        provisional_key = self._provisional_key(provisional)
        staged = self._head_key_sync(provisional_key)
        if staged is None:
            raise ArtifactNotFound("artifact.provisional_object_missing")
        staged_metadata = {
            str(key).lower(): str(value)
            for key, value in (staged.get("Metadata") or {}).items()
        }
        expected_metadata = {
            **self._metadata(provisional.artifact),
            "lifecycle-state": "provisional",
            "upload-id": str(provisional.upload_id),
        }
        if int(staged.get("ContentLength", -1)) != provisional.artifact.size_bytes or any(
            staged_metadata.get(key) != value for key, value in expected_metadata.items()
        ):
            raise ArtifactStoreError("artifact.provisional_readback_mismatch")
        committed = self._head_sync(provisional.artifact)
        if committed is None:
            copy_request: dict[str, Any] = {
                "Bucket": self._bucket,
                "Key": self._key(provisional.artifact),
                "CopySource": {"Bucket": self._bucket, "Key": provisional_key},
                "ContentType": provisional.artifact.media_type,
                "Metadata": self._metadata(provisional.artifact),
                "MetadataDirective": "REPLACE",
            }
            if self._server_side_encryption:
                copy_request["ServerSideEncryption"] = self._server_side_encryption
            if self._kms_key_id:
                copy_request["SSEKMSKeyId"] = self._kms_key_id
            try:
                self._client.copy_object(**copy_request)
            except Exception as exc:
                raise ArtifactStoreError("artifact.object_store_copy_failed") from exc
        else:
            self._validate_head(provisional.artifact, committed)
        readback = self._head_sync(provisional.artifact)
        if readback is None:
            raise ArtifactStoreError("artifact.object_store_write_readback_missing")
        self._validate_head(provisional.artifact, readback)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=provisional_key)
        except Exception as exc:
            raise ArtifactStoreError("artifact.provisional_delete_failed") from exc

    async def delete_provisional(self, provisional: ProvisionalArtifactRef) -> None:
        provisional.validate_binding()
        try:
            await asyncio.to_thread(
                self._client.delete_object,
                Bucket=self._bucket,
                Key=self._provisional_key(provisional),
            )
        except Exception as exc:
            raise ArtifactStoreError("artifact.provisional_delete_failed") from exc

    async def sweep_provisional_orphans(
        self, *, older_than: datetime, limit: int
    ) -> int:
        if older_than.tzinfo is None or limit <= 0:
            raise ValueError("artifact orphan sweep bounds are invalid")
        return await asyncio.to_thread(self._sweep_provisional_orphans_sync, older_than, limit)

    def _sweep_provisional_orphans_sync(self, older_than: datetime, limit: int) -> int:
        prefix = f"{self._key_prefix}/provisional/v1/"
        try:
            response = self._client.list_objects_v2(
                Bucket=self._bucket,
                Prefix=prefix,
                MaxKeys=limit,
            )
        except Exception as exc:
            raise ArtifactStoreError("artifact.provisional_list_failed") from exc
        removed = 0
        for item in response.get("Contents") or ():
            modified = item.get("LastModified")
            key = str(item.get("Key") or "")
            if not key.startswith(prefix) or not isinstance(modified, datetime):
                continue
            if modified <= older_than and removed < limit:
                try:
                    self._client.delete_object(Bucket=self._bucket, Key=key)
                except Exception as exc:
                    raise ArtifactStoreError("artifact.provisional_delete_failed") from exc
                removed += 1
        return removed


def require_development_local_artifact_store(
    root: Path,
    *,
    runtime_environment: str,
    max_object_bytes: int = 512 * 1024 * 1024,
) -> LocalDevelopmentArtifactObjectStore:
    """Fail closed if the local adapter is accidentally selected for production."""

    if runtime_environment.strip().lower() not in {"development", "dev", "test"}:
        raise RuntimeError("artifact.local_adapter_development_only")
    return LocalDevelopmentArtifactObjectStore(root, max_object_bytes=max_object_bytes)


class DurableArtifactService:
    """Coordinates immutable object writes with durable owner-scoped metadata."""

    def __init__(self, object_store: ArtifactObjectStore) -> None:
        self._object_store = object_store

    @property
    def object_store(self) -> ArtifactObjectStore:
        """Expose the authority adapter only to composition/lifecycle code."""

        return self._object_store

    async def register_ref(
        self,
        session: AsyncSession,
        reference: ArtifactRef,
    ) -> ArtifactRef:
        """Register worker-produced immutable bytes after object readback."""

        if not await self._object_store.exists(reference):
            raise ArtifactNotFound("artifact.worker_output_missing")
        row = PublicArtifact(
            id=reference.artifact_id,
            schema_version=reference.schema_version,
            owner_id=reference.owner_id,
            tenant_id=reference.tenant_id,
            kind=reference.kind,
            sha256=reference.sha256,
            size_bytes=reference.size_bytes,
            media_type=reference.media_type,
            storage_key=reference.storage_key,
            created_by_operation_id=reference.created_by_operation_id,
            retention_class=reference.retention_class,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
            return reference
        except IntegrityError:
            existing = await session.scalar(
                select(PublicArtifact).where(
                    PublicArtifact.owner_id == reference.owner_id,
                    PublicArtifact.tenant_id == reference.tenant_id,
                    PublicArtifact.kind == reference.kind,
                    PublicArtifact.sha256 == reference.sha256,
                    PublicArtifact.created_by_operation_id
                    == reference.created_by_operation_id,
                    PublicArtifact.retention_class == reference.retention_class,
                )
            )
            if existing is None:
                raise ArtifactStoreError("artifact.metadata_deduplication_failed")
            return existing.to_ref()

    async def stage_ref_to_path(
        self,
        reference: ArtifactRef,
        destination: Path,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        """Stream one exact immutable ref into a trusted sandbox input path.

        The destination is a staging copy only.  Object storage remains the
        byte authority, and a digest/length mismatch never leaves a usable
        destination behind.
        """

        target = Path(destination)
        if not target.is_absolute() or chunk_size <= 0:
            raise ValueError("artifact staging destination is invalid")
        opened = await self.open_read_stream(reference)

        def copy_verified() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            digest = sha256()
            size = 0
            try:
                with temporary.open("xb") as output:
                    for chunk in opened.iter_chunks(chunk_size=chunk_size):
                        size += len(chunk)
                        if size > reference.size_bytes:
                            raise ArtifactBindingError(
                                "artifact.staged_input_binding_invalid"
                            )
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if size != reference.size_bytes or digest.hexdigest() != reference.sha256:
                    raise ArtifactBindingError("artifact.staged_input_binding_invalid")
                if target.exists():
                    existing_size, existing_digest = (
                        LocalDevelopmentArtifactObjectStore._file_binding(target)
                    )
                    if (
                        existing_size != reference.size_bytes
                        or existing_digest != reference.sha256
                    ):
                        raise ArtifactStoreError("artifact.staged_input_conflict")
                else:
                    os.replace(temporary, target)
            finally:
                opened.close()
                temporary.unlink(missing_ok=True)

        await asyncio.to_thread(copy_verified)

    async def publish_worker_output_path(
        self,
        reference: ArtifactRef,
        source_path: Path,
    ) -> None:
        """Stream a verified sandbox result into the object-store authority.

        Metadata registration intentionally remains a later DB transaction.
        A transport must await this method before returning a success wire
        result so an S3 failure cannot degrade into local-only success.
        """

        source = Path(source_path)
        if not source.is_absolute() or not source.is_file():
            raise ArtifactNotFound("artifact.worker_output_path_missing")
        with source.open("rb") as stream:
            await self._object_store.put_stream(reference, stream)
        metadata = await self._object_store.head(reference)
        if (
            metadata is None
            or metadata.size_bytes != reference.size_bytes
        ):
            raise ArtifactStoreError("artifact.worker_output_readback_mismatch")

    async def read_ref_bytes(self, reference: ArtifactRef) -> bytes:
        if not await self._object_store.exists(reference):
            raise ArtifactNotFound("artifact.object_missing")
        return await self._object_store.read_bytes(reference)

    async def open_read_stream(
        self,
        reference: ArtifactRef,
        *,
        byte_range: ArtifactByteRange | None = None,
    ) -> ArtifactReadStream:
        return await self._object_store.open_read_stream(reference, byte_range=byte_range)

    async def create_signed_download(
        self,
        reference: ArtifactRef,
        *,
        expires_seconds: int = 180,
    ) -> SignedArtifactDownload:
        return await self._object_store.create_signed_download(
            reference,
            expires_seconds=expires_seconds,
        )

    async def persist_stream(
        self,
        session: AsyncSession,
        *,
        owner_id: uuid.UUID,
        tenant_id: str,
        kind: Literal[
            "checkpoint",
            "slide-html",
            "slide-png",
            "export-bundle",
            "presentation-pptx",
            "presentation-pdf",
        ],
        source: BinaryIO,
        expected_digest: str,
        size_bytes: int,
        media_type: str,
        created_by_operation_id: uuid.UUID,
        retention_class: Literal["checkpoint", "ephemeral", "published"],
    ) -> ArtifactRef:
        digest = normalize_sha256(expected_digest)
        reference = ArtifactRef(
            artifact_id=uuid.uuid4(),
            owner_id=owner_id,
            tenant_id=tenant_id,
            kind=kind,
            sha256=digest,
            size_bytes=size_bytes,
            media_type=media_type,
            storage_key=artifact_storage_key(
                owner_id=owner_id,
                tenant_id=tenant_id,
                digest=digest,
            ),
            created_by_operation_id=created_by_operation_id,
            retention_class=retention_class,
        )
        provisional = ProvisionalArtifactRef.create(reference)
        try:
            await self._object_store.put_provisional_stream(provisional, source)
            await self._object_store.commit_provisional(provisional)
        except BaseException:
            try:
                await self._object_store.delete_provisional(provisional)
            except ArtifactStoreError:
                pass
            raise
        return await self.register_ref(session, reference)

    async def stage_stream(
        self,
        reference: ArtifactRef,
        source: BinaryIO,
        *,
        enabled: bool = False,
    ) -> ProvisionalArtifactRef:
        """Upload to the provisional namespace behind the product feature gate."""

        if not enabled:
            raise ArtifactStoreError("artifact.provisional_lifecycle_feature_disabled")
        provisional = ProvisionalArtifactRef.create(reference)
        await self._object_store.put_provisional_stream(provisional, source)
        return provisional

    async def commit_staged_object(
        self,
        provisional: ProvisionalArtifactRef,
        *,
        enabled: bool = False,
    ) -> ArtifactRef:
        """Promote bytes only; caller still owns the fenced DB transaction/pointer."""

        if not enabled:
            raise ArtifactStoreError("artifact.provisional_lifecycle_feature_disabled")
        await self._object_store.commit_provisional(provisional)
        return provisional.artifact

    async def persist_bytes(
        self,
        session: AsyncSession,
        *,
        owner_id: uuid.UUID,
        tenant_id: str,
        kind: Literal[
            "checkpoint",
            "slide-html",
            "slide-png",
            "export-bundle",
            "presentation-pptx",
            "presentation-pdf",
        ],
        payload: bytes,
        media_type: str,
        created_by_operation_id: uuid.UUID,
        retention_class: Literal["checkpoint", "ephemeral", "published"],
    ) -> ArtifactRef:
        digest = sha256(payload).hexdigest()
        return await self.persist_stream(
            session,
            owner_id=owner_id,
            tenant_id=tenant_id,
            kind=kind,
            source=io.BytesIO(payload),
            expected_digest=digest,
            size_bytes=len(payload),
            media_type=media_type,
            created_by_operation_id=created_by_operation_id,
            retention_class=retention_class,
        )

    async def resolve_ref(
        self,
        session: AsyncSession,
        *,
        artifact_id: uuid.UUID,
        owner_id: uuid.UUID,
        tenant_id: str,
        expected_digest: str,
    ) -> ArtifactRef:
        digest = normalize_sha256(expected_digest)
        artifact = await session.scalar(
            select(PublicArtifact).where(
                PublicArtifact.id == artifact_id,
                PublicArtifact.owner_id == owner_id,
                PublicArtifact.tenant_id == tenant_id,
                PublicArtifact.sha256 == digest,
            )
        )
        if artifact is None:
            # Deliberately do not distinguish not-found from cross-owner access.
            raise ArtifactNotFound("artifact.ref_not_found")
        reference = artifact.to_ref()
        if not await self._object_store.exists(reference):
            raise ArtifactNotFound("artifact.object_missing")
        return reference

    async def read_bytes(
        self,
        session: AsyncSession,
        *,
        artifact_id: uuid.UUID,
        owner_id: uuid.UUID,
        tenant_id: str,
        expected_digest: str,
    ) -> bytes:
        reference = await self.resolve_ref(
            session,
            artifact_id=artifact_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
            expected_digest=expected_digest,
        )
        return await self._object_store.read_bytes(reference)


class DurableArtifactWorkerObjectPort:
    """Duck-typed trusted-executor port backed by the configured authority.

    The wire layer uses its own frozen ``ArtifactRef`` type.  Re-validating the
    wire representation here prevents the transport from bypassing the public
    owner/tenant/digest/storage-key contract.
    """

    def __init__(self, service: DurableArtifactService) -> None:
        self._service = service

    @staticmethod
    def _public_reference(reference: object) -> ArtifactRef:
        to_wire = getattr(reference, "to_wire", None)
        if not callable(to_wire):
            raise ArtifactBindingError("artifact.worker_reference_invalid")
        try:
            return ArtifactRef.model_validate(to_wire())
        except Exception as error:
            raise ArtifactBindingError("artifact.worker_reference_invalid") from error

    async def stage(self, *, reference: object, destination: Path) -> None:
        await self._service.stage_ref_to_path(
            self._public_reference(reference),
            destination,
        )

    async def publish(self, *, reference: object, source: Path) -> None:
        await self._service.publish_worker_output_path(
            self._public_reference(reference),
            source,
        )


def configured_public_artifact_service() -> DurableArtifactService:
    """Create the configured authority adapter without accepting request input."""

    backend = (os.getenv("PUBLIC_ARTIFACT_STORE_BACKEND") or "").strip().lower()
    if backend == "local-development":
        return DurableArtifactService(configured_local_development_artifact_store())
    if backend == "s3":
        bucket = (os.getenv("PUBLIC_ARTIFACT_S3_BUCKET") or "").strip()
        prefix = (os.getenv("PUBLIC_ARTIFACT_S3_PREFIX") or "presenton-artifacts").strip()
        endpoint_url = (os.getenv("PUBLIC_ARTIFACT_S3_ENDPOINT_URL") or "").strip()
        region_name = (os.getenv("PUBLIC_ARTIFACT_S3_REGION") or "us-east-1").strip()
        addressing_style = (
            os.getenv("PUBLIC_ARTIFACT_S3_ADDRESSING_STYLE") or "virtual"
        ).strip().lower()
        if addressing_style not in {"virtual", "path"}:
            raise RuntimeError("artifact.s3_addressing_style_invalid")
        verify_raw = (os.getenv("PUBLIC_ARTIFACT_S3_TLS_VERIFY") or "true").strip().lower()
        if verify_raw not in {"true", "false"}:
            raise RuntimeError("artifact.s3_tls_verify_invalid")
        try:
            import boto3
            from botocore.config import Config
        except ImportError as error:
            raise RuntimeError("artifact.s3_client_unavailable") from error
        client = boto3.client(
            "s3",
            endpoint_url=(endpoint_url or None),
            region_name=region_name,
            verify=verify_raw == "true",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": addressing_style},
            ),
        )
        return DurableArtifactService(
            S3ArtifactObjectStore(
                bucket=bucket,
                key_prefix=prefix,
                client=client,
                server_side_encryption=(
                    os.getenv("PUBLIC_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION")
                    if "PUBLIC_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION" in os.environ
                    else "AES256"
                )
                or "",
            )
        )
    raise RuntimeError("artifact.backend_required")


def configured_local_development_artifact_store() -> LocalDevelopmentArtifactObjectStore:
    """Build the loopback-only development store; never fall back in production."""

    backend = (os.getenv("PUBLIC_ARTIFACT_STORE_BACKEND") or "").strip().lower()
    environment = (os.getenv("ENVIRONMENT") or "production").strip().lower()
    if backend != "local-development" or environment != "local":
        raise RuntimeError("artifact.local_download_development_only")
    root_value = (os.getenv("PUBLIC_WORKER_OBJECT_STORE_ROOT") or "").strip()
    root = Path(root_value)
    if not root_value or not root.is_absolute():
        raise RuntimeError("artifact.local_root_required")
    signing_key = os.getenv("PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY")
    if not signing_key:
        raise RuntimeError("artifact.local_download_signing_key_required")
    origin = (
        os.getenv("PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_ORIGIN") or "http://127.0.0.1:8010"
    ).strip()
    return LocalDevelopmentArtifactObjectStore(
        root,
        download_signing_key=signing_key,
        download_origin=origin,
    )


def configured_public_artifact_worker_object_port() -> DurableArtifactWorkerObjectPort:
    """Return the composition-root S3 stage/publish port for trusted workers."""

    return DurableArtifactWorkerObjectPort(configured_public_artifact_service())


@dataclass(frozen=True, slots=True)
class ExportIdentityKey:
    owner_id: uuid.UUID
    tenant_id: str
    presentation_id: uuid.UUID
    revision: int
    canonical_digest: str
    scene_digest: str
    semantic_digest: str
    quality_digest: str
    exporter_digest: str
    export_mode: Literal["pptx", "pdf"]
    output_mode: Literal["fidelity-raster", "editable-hybrid"]
    font_manifest_digest: str
    exporter_version: str
    render_adapter: str
    source_artifact_id: uuid.UUID

    def normalized(self) -> "ExportIdentityKey":
        if self.revision < 0:
            raise ValueError("export revision must be nonnegative")
        tenant_id = self.tenant_id.strip()
        exporter_version = self.exporter_version.strip()
        render_adapter = self.render_adapter.strip()
        if not tenant_id or len(tenant_id) > 128:
            raise ValueError("export tenant binding is invalid")
        if not exporter_version or len(exporter_version) > 128:
            raise ValueError("exporter version is invalid")
        if not render_adapter or len(render_adapter) > 128:
            raise ValueError("export render adapter is invalid")
        if self.export_mode not in {"pptx", "pdf"}:
            raise ValueError("export format is invalid")
        if self.output_mode not in {"fidelity-raster", "editable-hybrid"}:
            raise ValueError("export output mode is invalid")
        if self.export_mode == "pdf" and self.output_mode != "fidelity-raster":
            raise ValueError("PDF export output mode is invalid")
        return ExportIdentityKey(
            owner_id=self.owner_id,
            tenant_id=tenant_id,
            presentation_id=self.presentation_id,
            revision=self.revision,
            canonical_digest=normalize_sha256(self.canonical_digest),
            scene_digest=normalize_sha256(self.scene_digest),
            semantic_digest=normalize_sha256(self.semantic_digest),
            quality_digest=normalize_sha256(self.quality_digest),
            exporter_digest=normalize_sha256(self.exporter_digest),
            export_mode=self.export_mode,
            output_mode=self.output_mode,
            font_manifest_digest=normalize_sha256(self.font_manifest_digest),
            exporter_version=exporter_version,
            render_adapter=render_adapter,
            source_artifact_id=self.source_artifact_id,
        )


@dataclass(frozen=True, slots=True)
class ExportClaim:
    disposition: Literal["claimed", "in_flight", "completed"]
    identity_id: uuid.UUID
    fencing_token: int
    attempt_token: str | None = None
    artifact: ArtifactRef | None = None


def _attempt_token_hash(token: str) -> str:
    return "sha256:" + sha256(token.encode("utf-8")).hexdigest()


async def _retain_artifact_reference(
    session: AsyncSession,
    artifact_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> None:
    retained = await session.execute(
        update(PublicArtifact)
        .where(
            PublicArtifact.id == artifact_id,
            PublicArtifact.lifecycle_state == "committed",
        )
        .values(
            refcount=PublicArtifact.refcount + 1,
            lifecycle_revision=PublicArtifact.lifecycle_revision + 1,
            updated_at=now or datetime.now(timezone.utc),
        )
        .execution_options(synchronize_session=False)
    )
    if retained.rowcount != 1:
        raise ArtifactBindingError("artifact.reference_target_not_committed")


async def _release_artifact_reference(
    session: AsyncSession,
    artifact_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> None:
    released = await session.execute(
        update(PublicArtifact)
        .where(
            PublicArtifact.id == artifact_id,
            PublicArtifact.lifecycle_state.in_(("committed", "tombstoned")),
            PublicArtifact.refcount > 0,
        )
        .values(
            refcount=PublicArtifact.refcount - 1,
            lifecycle_revision=PublicArtifact.lifecycle_revision + 1,
            updated_at=now or datetime.now(timezone.utc),
        )
        .execution_options(synchronize_session=False)
    )
    if released.rowcount != 1:
        raise ArtifactStoreError("artifact.reference_count_underflow")


async def _require_source_artifact(session: AsyncSession, key: ExportIdentityKey) -> PublicArtifact:
    source = await session.scalar(
        select(PublicArtifact).where(
            PublicArtifact.id == key.source_artifact_id,
            PublicArtifact.owner_id == key.owner_id,
            PublicArtifact.tenant_id == key.tenant_id,
            PublicArtifact.sha256 == key.canonical_digest,
            PublicArtifact.kind == "checkpoint",
            PublicArtifact.lifecycle_state == "committed",
        )
    )
    if source is None:
        raise ArtifactNotFound("export.source_artifact_binding_invalid")
    return source


def _identity_predicate(key: ExportIdentityKey):
    return (
        (PublicExportIdentity.owner_id == key.owner_id)
        & (PublicExportIdentity.tenant_id == key.tenant_id)
        & (PublicExportIdentity.presentation_id == key.presentation_id)
        & (PublicExportIdentity.revision == key.revision)
        & (PublicExportIdentity.scene_digest == key.scene_digest)
        & (PublicExportIdentity.semantic_digest == key.semantic_digest)
        & (PublicExportIdentity.quality_digest == key.quality_digest)
        & (PublicExportIdentity.exporter_digest == key.exporter_digest)
        & (PublicExportIdentity.export_mode == key.export_mode)
        & (PublicExportIdentity.output_mode == key.output_mode)
        & (PublicExportIdentity.font_manifest_digest == key.font_manifest_digest)
        & (PublicExportIdentity.render_adapter == key.render_adapter)
        & (PublicExportIdentity.identity_contract_version == "digest-bound/v2")
        & PublicExportIdentity.reuse_eligible.is_(True)
    )


async def _completed_claim(
    session: AsyncSession,
    identity: PublicExportIdentity,
) -> ExportClaim:
    if identity.result_artifact_id is None:
        raise ArtifactStoreError("export.completed_artifact_missing")
    artifact = await session.scalar(
        select(PublicArtifact).where(
            PublicArtifact.id == identity.result_artifact_id,
            PublicArtifact.owner_id == identity.owner_id,
            PublicArtifact.tenant_id == identity.tenant_id,
            PublicArtifact.lifecycle_state == "committed",
        )
    )
    if artifact is None:
        raise ArtifactStoreError("export.completed_artifact_binding_invalid")
    return ExportClaim(
        disposition="completed",
        identity_id=identity.id,
        fencing_token=identity.fencing_token,
        artifact=artifact.to_ref(),
    )


async def claim_export_identity(
    session: AsyncSession,
    *,
    key: ExportIdentityKey,
    operation_id: uuid.UUID,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> ExportClaim:
    """Acquire or observe the unique cluster-wide export execution."""

    key = key.normalized()
    worker_id = worker_id.strip()
    if not worker_id or len(worker_id) > 160:
        raise ValueError("export worker identity is invalid")
    if lease_seconds < 1 or lease_seconds > 3600:
        raise ValueError("export lease must be between 1 and 3600 seconds")
    await _require_source_artifact(session, key)
    now = now or datetime.now(timezone.utc)
    lease_expires_at = now + timedelta(seconds=lease_seconds)
    attempt_token = secrets.token_urlsafe(32)
    candidate = PublicExportIdentity(
        owner_id=key.owner_id,
        tenant_id=key.tenant_id,
        presentation_id=key.presentation_id,
        revision=key.revision,
        canonical_digest=key.canonical_digest,
        scene_digest=key.scene_digest,
        semantic_digest=key.semantic_digest,
        quality_digest=key.quality_digest,
        exporter_digest=key.exporter_digest,
        export_mode=key.export_mode,
        output_mode=key.output_mode,
        font_manifest_digest=key.font_manifest_digest,
        exporter_version=key.exporter_version,
        render_adapter=key.render_adapter,
        identity_contract_version="digest-bound/v2",
        reuse_eligible=True,
        source_artifact_id=key.source_artifact_id,
        state="running",
        claimed_by_operation_id=operation_id,
        worker_id=worker_id,
        attempt_token_hash=_attempt_token_hash(attempt_token),
        fencing_token=1,
        attempt_count=1,
        lease_expires_at=lease_expires_at,
    )
    inserted = False
    try:
        async with session.begin_nested():
            session.add(candidate)
            await session.flush()
        inserted = True
    except IntegrityError:
        inserted = False
    if inserted:
        await _retain_artifact_reference(session, key.source_artifact_id, now=now)
        await session.flush()
        return ExportClaim("claimed", candidate.id, 1, attempt_token=attempt_token)

    existing = await session.scalar(
        select(PublicExportIdentity)
        .where(_identity_predicate(key))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing is None:
        raise ExportIdentityConflict("export.identity_owner_tenant_conflict")
    if existing.tenant_id != key.tenant_id:
        raise ExportIdentityConflict("export.identity_owner_tenant_conflict")
    if existing.source_artifact_id != key.source_artifact_id:
        source = await session.get(PublicArtifact, existing.source_artifact_id)
        if source is None or source.sha256 != key.canonical_digest:
            raise ExportIdentityConflict("export.identity_source_conflict")
    if existing.state == "succeeded":
        return await _completed_claim(session, existing)

    reclaimed_token = secrets.token_urlsafe(32)
    source_reference_released = existing.state == "failed"
    reclaim = await session.execute(
        update(PublicExportIdentity)
        .where(
            PublicExportIdentity.id == existing.id,
            or_(
                PublicExportIdentity.state == "failed",
                (
                    (PublicExportIdentity.state == "running")
                    & (PublicExportIdentity.lease_expires_at <= now)
                ),
            ),
        )
        .values(
            state="running",
            claimed_by_operation_id=operation_id,
            worker_id=worker_id,
            attempt_token_hash=_attempt_token_hash(reclaimed_token),
            fencing_token=PublicExportIdentity.fencing_token + 1,
            attempt_count=PublicExportIdentity.attempt_count + 1,
            lease_expires_at=lease_expires_at,
            error_code=None,
            completed_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    await session.flush()
    current = await session.scalar(
        select(PublicExportIdentity)
        .where(PublicExportIdentity.id == existing.id)
        .execution_options(populate_existing=True)
    )
    if current is None:
        raise ArtifactStoreError("export.identity_disappeared")
    if reclaim.rowcount == 1:
        if source_reference_released:
            await _retain_artifact_reference(
                session,
                current.source_artifact_id,
                now=now,
            )
            await session.flush()
        return ExportClaim(
            "claimed",
            current.id,
            current.fencing_token,
            attempt_token=reclaimed_token,
        )
    if current.state == "succeeded":
        return await _completed_claim(session, current)
    return ExportClaim("in_flight", current.id, current.fencing_token)


async def heartbeat_export_identity(
    session: AsyncSession,
    *,
    identity_id: uuid.UUID,
    worker_id: str,
    attempt_token: str,
    fencing_token: int,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    if lease_seconds < 1 or lease_seconds > 3600:
        raise ValueError("export lease must be between 1 and 3600 seconds")
    now = now or datetime.now(timezone.utc)
    result = await session.execute(
        update(PublicExportIdentity)
        .where(
            PublicExportIdentity.id == identity_id,
            PublicExportIdentity.state == "running",
            PublicExportIdentity.worker_id == worker_id,
            PublicExportIdentity.attempt_token_hash == _attempt_token_hash(attempt_token),
            PublicExportIdentity.fencing_token == fencing_token,
        )
        .values(
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    await session.flush()
    return result.rowcount == 1


async def complete_export_identity(
    session: AsyncSession,
    *,
    identity_id: uuid.UUID,
    worker_id: str,
    attempt_token: str,
    fencing_token: int,
    result_artifact_id: uuid.UUID,
    now: datetime | None = None,
) -> bool:
    """Commit a fenced export only when its output ArtifactRef is owner-bound."""

    now = now or datetime.now(timezone.utc)
    identity = await session.scalar(
        select(PublicExportIdentity).where(
            PublicExportIdentity.id == identity_id,
            PublicExportIdentity.state == "running",
            PublicExportIdentity.worker_id == worker_id,
            PublicExportIdentity.attempt_token_hash == _attempt_token_hash(attempt_token),
            PublicExportIdentity.fencing_token == fencing_token,
        )
    )
    if identity is None:
        return False
    expected_kind = f"presentation-{identity.export_mode}"
    artifact = await session.scalar(
        select(PublicArtifact).where(
            PublicArtifact.id == result_artifact_id,
            PublicArtifact.owner_id == identity.owner_id,
            PublicArtifact.tenant_id == identity.tenant_id,
            PublicArtifact.kind == expected_kind,
            PublicArtifact.created_by_operation_id == identity.claimed_by_operation_id,
            PublicArtifact.lifecycle_state == "committed",
        )
    )
    if artifact is None:
        raise ArtifactBindingError("export.result_artifact_binding_invalid")
    result = await session.execute(
        update(PublicExportIdentity)
        .where(
            PublicExportIdentity.id == identity_id,
            PublicExportIdentity.state == "running",
            PublicExportIdentity.worker_id == worker_id,
            PublicExportIdentity.attempt_token_hash == _attempt_token_hash(attempt_token),
            PublicExportIdentity.fencing_token == fencing_token,
        )
        .values(
            state="succeeded",
            result_artifact_id=result_artifact_id,
            error_code=None,
            completed_at=now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    await session.flush()
    if result.rowcount != 1:
        return False
    referenced = await session.execute(
        update(PublicArtifact)
        .where(
            PublicArtifact.id == result_artifact_id,
            PublicArtifact.lifecycle_state == "committed",
        )
        .values(
            refcount=PublicArtifact.refcount + 1,
            lifecycle_revision=PublicArtifact.lifecycle_revision + 1,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if referenced.rowcount != 1:
        raise ArtifactBindingError("export.result_artifact_not_committed")
    await session.flush()
    return True


async def _locked_current_operation(
    session: AsyncSession,
    *,
    operation_id: uuid.UUID,
    owner_id: uuid.UUID,
    tenant_id: str,
    attempt_number: int,
    attempt_token: str,
    fencing_token: int,
) -> WorkspaceOperation | None:
    """Lock the exact live operation attempt used by pointer mutations."""

    if attempt_number < 1 or fencing_token < 1 or not attempt_token:
        return None
    operation = await session.scalar(
        select(WorkspaceOperation)
        .where(
            WorkspaceOperation.id == operation_id,
            WorkspaceOperation.owner_id == owner_id,
            WorkspaceOperation.state == "running",
            WorkspaceOperation.attempt_number == attempt_number,
            WorkspaceOperation.attempt_token == attempt_token,
            WorkspaceOperation.fencing_token == fencing_token,
        )
        .with_for_update()
    )
    if operation is None:
        return None
    admission = await session.scalar(
        select(PublicOperationAdmission)
        .where(
            PublicOperationAdmission.operation_id == operation_id,
            PublicOperationAdmission.owner_id == owner_id,
            PublicOperationAdmission.tenant_id == tenant_id,
            PublicOperationAdmission.operation_kind == "export",
            PublicOperationAdmission.state == "active",
            PublicOperationAdmission.fence == fencing_token,
        )
        .with_for_update()
    )
    return operation if admission is not None else None


async def _locked_committed_artifact(
    session: AsyncSession,
    *,
    artifact_id: uuid.UUID,
    owner_id: uuid.UUID,
    tenant_id: str,
) -> PublicArtifact | None:
    return await session.scalar(
        select(PublicArtifact)
        .where(
            PublicArtifact.id == artifact_id,
            PublicArtifact.owner_id == owner_id,
            PublicArtifact.tenant_id == tenant_id,
            PublicArtifact.lifecycle_state == "committed",
        )
        .with_for_update()
    )


async def _move_pointer_reference(
    session: AsyncSession,
    *,
    previous_artifact_id: uuid.UUID | None,
    artifact_id: uuid.UUID,
) -> None:
    """Move one durable reference in the same transaction as its pointer."""

    if previous_artifact_id == artifact_id:
        return
    if previous_artifact_id is not None:
        released = await session.execute(
            update(PublicArtifact)
            .where(
                PublicArtifact.id == previous_artifact_id,
                PublicArtifact.refcount > 0,
            )
            .values(
                refcount=PublicArtifact.refcount - 1,
                lifecycle_revision=PublicArtifact.lifecycle_revision + 1,
                updated_at=datetime.now(timezone.utc),
            )
            .execution_options(synchronize_session=False)
        )
        if released.rowcount != 1:
            raise ArtifactStoreError("artifact.pointer_refcount_underflow")
    retained = await session.execute(
        update(PublicArtifact)
        .where(
            PublicArtifact.id == artifact_id,
            PublicArtifact.lifecycle_state == "committed",
        )
        .values(
            refcount=PublicArtifact.refcount + 1,
            lifecycle_revision=PublicArtifact.lifecycle_revision + 1,
            updated_at=datetime.now(timezone.utc),
        )
        .execution_options(synchronize_session=False)
    )
    if retained.rowcount != 1:
        raise ArtifactBindingError("artifact.pointer_target_not_committed")


async def promote_artifact_publication_if_current(
    session: AsyncSession,
    *,
    operation_id: uuid.UUID,
    owner_id: uuid.UUID,
    tenant_id: str,
    attempt_number: int,
    attempt_token: str,
    fencing_token: int,
    publication_name: str,
    revision: int,
    quality_digest: str,
    artifact_id: uuid.UUID,
    now: datetime | None = None,
) -> PublicArtifactPublication | None:
    """CAS one publication pointer against the current operation fence.

    ``None`` is the stale-fence result.  The caller owns the surrounding
    transaction; pointer replacement and refcount movement are flushed
    together and must be committed together.
    """

    name = publication_name.strip()
    normalized_quality_digest = normalize_sha256(quality_digest)
    if not name or len(name) > 64 or revision < 0:
        raise ValueError("artifact publication identity is invalid")
    operation = await _locked_current_operation(
        session,
        operation_id=operation_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
        attempt_number=attempt_number,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
    )
    if operation is None:
        return None
    artifact = await _locked_committed_artifact(
        session,
        artifact_id=artifact_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
    )
    if artifact is None:
        raise ArtifactBindingError("artifact.publication_target_not_committed")
    pointer = await session.scalar(
        select(PublicArtifactPublication)
        .where(
            PublicArtifactPublication.operation_id == operation_id,
            PublicArtifactPublication.publication_name == name,
        )
        .with_for_update()
    )
    attempt_hash = _attempt_token_hash(attempt_token)
    if pointer is not None and pointer.fencing_token > fencing_token:
        return None
    previous_artifact_id = pointer.artifact_id if pointer is not None else None
    if pointer is None:
        pointer = PublicArtifactPublication(
            operation_id=operation_id,
            publication_name=name,
            revision=revision,
            artifact_id=artifact_id,
            quality_digest=normalized_quality_digest,
            attempt_token_hash=attempt_hash,
            fencing_token=fencing_token,
            published_at=now or datetime.now(timezone.utc),
        )
        session.add(pointer)
    else:
        pointer.revision = revision
        pointer.artifact_id = artifact_id
        pointer.quality_digest = normalized_quality_digest
        pointer.attempt_token_hash = attempt_hash
        pointer.fencing_token = fencing_token
        pointer.published_at = now or datetime.now(timezone.utc)
        session.add(pointer)
    await _move_pointer_reference(
        session,
        previous_artifact_id=previous_artifact_id,
        artifact_id=artifact_id,
    )
    await session.flush()
    return pointer


async def put_slide_checkpoint_if_current(
    session: AsyncSession,
    *,
    operation_id: uuid.UUID,
    owner_id: uuid.UUID,
    tenant_id: str,
    attempt_number: int,
    attempt_token: str,
    fencing_token: int,
    revision: int,
    slide_index: int,
    input_digest: str,
    stage_version: str,
    artifact_id: uuid.UUID,
    now: datetime | None = None,
) -> PublicSlideCheckpoint | None:
    """CAS a committed checkpoint and its refcount under the operation fence."""

    digest = normalize_sha256(input_digest)
    stage = stage_version.strip()
    if revision < 0 or slide_index < 0 or not stage or len(stage) > 64:
        raise ValueError("artifact checkpoint identity is invalid")
    operation = await _locked_current_operation(
        session,
        operation_id=operation_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
        attempt_number=attempt_number,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
    )
    if operation is None:
        return None
    artifact = await _locked_committed_artifact(
        session,
        artifact_id=artifact_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
    )
    if artifact is None:
        raise ArtifactBindingError("artifact.checkpoint_target_not_committed")
    pointer = await session.scalar(
        select(PublicSlideCheckpoint)
        .where(
            PublicSlideCheckpoint.operation_id == operation_id,
            PublicSlideCheckpoint.revision == revision,
            PublicSlideCheckpoint.slide_index == slide_index,
            PublicSlideCheckpoint.input_digest == digest,
            PublicSlideCheckpoint.stage_version == stage,
        )
        .with_for_update()
    )
    if pointer is not None and pointer.fencing_token > fencing_token:
        return None
    previous_artifact_id = pointer.artifact_id if pointer is not None else None
    if pointer is None:
        pointer = PublicSlideCheckpoint(
            operation_id=operation_id,
            revision=revision,
            slide_index=slide_index,
            input_digest=digest,
            stage_version=stage,
            artifact_id=artifact_id,
            attempt_token_hash=_attempt_token_hash(attempt_token),
            fencing_token=fencing_token,
            committed_at=now or datetime.now(timezone.utc),
        )
        session.add(pointer)
    else:
        pointer.artifact_id = artifact_id
        pointer.attempt_token_hash = _attempt_token_hash(attempt_token)
        pointer.fencing_token = fencing_token
        pointer.committed_at = now or datetime.now(timezone.utc)
        session.add(pointer)
    await _move_pointer_reference(
        session,
        previous_artifact_id=previous_artifact_id,
        artifact_id=artifact_id,
    )
    await session.flush()
    return pointer


async def fail_export_identity(
    session: AsyncSession,
    *,
    identity_id: uuid.UUID,
    worker_id: str,
    attempt_token: str,
    fencing_token: int,
    error_code: str,
    now: datetime | None = None,
) -> bool:
    error_code = error_code.strip()
    if not error_code or len(error_code) > 128:
        raise ValueError("export error code is invalid")
    now = now or datetime.now(timezone.utc)
    identity = await session.scalar(
        select(PublicExportIdentity)
        .where(
            PublicExportIdentity.id == identity_id,
            PublicExportIdentity.state == "running",
            PublicExportIdentity.worker_id == worker_id,
            PublicExportIdentity.attempt_token_hash
            == _attempt_token_hash(attempt_token),
            PublicExportIdentity.fencing_token == fencing_token,
        )
        .with_for_update()
    )
    if identity is None:
        return False
    result = await session.execute(
        update(PublicExportIdentity)
        .where(
            PublicExportIdentity.id == identity_id,
            PublicExportIdentity.state == "running",
            PublicExportIdentity.worker_id == worker_id,
            PublicExportIdentity.attempt_token_hash == _attempt_token_hash(attempt_token),
            PublicExportIdentity.fencing_token == fencing_token,
        )
        .values(state="failed", error_code=error_code, completed_at=now, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    await session.flush()
    if result.rowcount != 1:
        return False
    await _release_artifact_reference(
        session,
        identity.source_artifact_id,
        now=now,
    )
    await session.flush()
    return True
