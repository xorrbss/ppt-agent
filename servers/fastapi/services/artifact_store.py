"""Content-addressed artifact authority and export single-flight operations.

The filesystem implementation in this module is deliberately development-only.
Production callers provide an object-store implementation of
``ArtifactObjectStore``; database rows retain only schema-versioned ArtifactRef
metadata and never an absolute path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Literal, Protocol
import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.public_artifact import (
    ArtifactRef,
    PublicArtifact,
    PublicExportIdentity,
    artifact_storage_key,
    normalize_sha256,
)


class ArtifactStoreError(RuntimeError):
    pass


class ArtifactBindingError(ArtifactStoreError):
    pass


class ArtifactNotFound(ArtifactStoreError):
    pass


class ExportIdentityConflict(ArtifactStoreError):
    pass


class ArtifactStoreMetrics:
    """Thread-safe per-process counters consumed by operation observability."""

    _OPERATIONS = ("put", "read", "head")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, int] = {
            **{
                f"artifact_store_{operation}_{suffix}": 0
                for operation in self._OPERATIONS
                for suffix in (
                    "count",
                    "latency_micros_total",
                    "latency_micros_process_max",
                )
            },
            "artifact_cache_hit_count": 0,
            "artifact_cache_miss_count": 0,
            "artifact_cache_eviction_count": 0,
            "artifact_cache_evicted_bytes": 0,
            "artifact_cache_current_bytes": 0,
            "artifact_cache_current_entries": 0,
        }

    def observe_store(self, operation: str, elapsed_seconds: float) -> None:
        micros = max(0, int(elapsed_seconds * 1_000_000))
        with self._lock:
            self._values[f"artifact_store_{operation}_count"] += 1
            self._values[f"artifact_store_{operation}_latency_micros_total"] += micros
            maximum = f"artifact_store_{operation}_latency_micros_process_max"
            self._values[maximum] = max(self._values[maximum], micros)

    def observe_cache(self, outcome: str, *, count: int = 1, size_bytes: int = 0) -> None:
        with self._lock:
            if outcome in {"hit", "miss"}:
                self._values[f"artifact_cache_{outcome}_count"] += count
            elif outcome == "eviction":
                self._values["artifact_cache_eviction_count"] += count
                self._values["artifact_cache_evicted_bytes"] += max(0, size_bytes)
            else:
                raise ValueError("artifact cache metric outcome is invalid")

    def set_cache_usage(self, *, entries: int, size_bytes: int) -> None:
        with self._lock:
            self._values["artifact_cache_current_entries"] = max(0, entries)
            self._values["artifact_cache_current_bytes"] = max(0, size_bytes)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._values)


_ARTIFACT_STORE_METRICS = ArtifactStoreMetrics()


def public_artifact_store_metrics_snapshot() -> dict[str, int]:
    """Return numeric-only counters safe for state/operation receipt projection."""

    return _ARTIFACT_STORE_METRICS.snapshot()


def public_artifact_store_metrics_delta(
    before: dict[str, int],
    after: dict[str, int] | None = None,
) -> dict[str, int]:
    current = public_artifact_store_metrics_snapshot() if after is None else after
    delta = {
        key: max(0, int(current.get(key, 0)) - int(before.get(key, 0)))
        for key in current
        if not key.endswith((
            "latency_micros_process_max",
            "current_bytes",
            "current_entries",
        ))
    }
    for operation in ArtifactStoreMetrics._OPERATIONS:
        count_key = f"artifact_store_{operation}_count"
        maximum_key = f"artifact_store_{operation}_latency_micros_process_max"
        delta[maximum_key] = (
            int(current.get(maximum_key, 0)) if delta.get(count_key, 0) else 0
        )
    return delta | {
        "artifact_cache_current_bytes": int(
            current.get("artifact_cache_current_bytes", 0)
        ),
        "artifact_cache_current_entries": int(
            current.get("artifact_cache_current_entries", 0)
        ),
    }


class ArtifactObjectStore(Protocol):
    """Immutable object storage addressed only through a validated ArtifactRef."""

    async def put_bytes(self, artifact: ArtifactRef, payload: bytes) -> None: ...

    async def read_bytes(self, artifact: ArtifactRef) -> bytes: ...

    async def exists(self, artifact: ArtifactRef) -> bool: ...


class LocalDevelopmentArtifactObjectStore:
    """Filesystem adapter/cache for local development, never production authority."""

    def __init__(
        self,
        root: Path,
        *,
        max_object_bytes: int = 512 * 1024 * 1024,
        observe_authority_metrics: bool = True,
    ) -> None:
        if max_object_bytes <= 0:
            raise ValueError("artifact object byte limit must be positive")
        self._root = root.resolve()
        self._max_object_bytes = max_object_bytes
        self._observe_authority_metrics = observe_authority_metrics

    def _path(self, artifact: ArtifactRef) -> Path:
        expected = artifact_storage_key(
            owner_id=artifact.owner_id,
            tenant_id=artifact.tenant_id,
            digest=artifact.sha256,
        )
        if artifact.storage_key != expected:
            raise ArtifactBindingError("artifact.storage_key_binding_invalid")
        candidate = (self._root / Path(*artifact.storage_key.split("/"))).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise ArtifactBindingError("artifact.storage_key_escape") from exc
        return candidate

    async def put_bytes(self, artifact: ArtifactRef, payload: bytes) -> None:
        started = time.perf_counter()
        if len(payload) != artifact.size_bytes:
            raise ArtifactBindingError("artifact.size_binding_invalid")
        actual_digest = sha256(payload).hexdigest()
        if actual_digest != artifact.sha256:
            raise ArtifactBindingError("artifact.digest_binding_invalid")
        if len(payload) > self._max_object_bytes:
            raise ArtifactStoreError("artifact.object_too_large")
        try:
            await asyncio.to_thread(self._put_bytes_sync, artifact, payload)
        finally:
            if self._observe_authority_metrics:
                _ARTIFACT_STORE_METRICS.observe_store(
                    "put", time.perf_counter() - started
                )

    def _put_bytes_sync(self, artifact: ArtifactRef, payload: bytes) -> None:
        target = self._path(artifact)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = target.read_bytes()
            if sha256(existing).hexdigest() != artifact.sha256 or len(existing) != artifact.size_bytes:
                raise ArtifactStoreError("artifact.immutable_object_conflict")
            return
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    async def read_bytes(self, artifact: ArtifactRef) -> bytes:
        started = time.perf_counter()
        try:
            payload = await asyncio.to_thread(self._read_bytes_sync, artifact)
            if len(payload) != artifact.size_bytes or sha256(payload).hexdigest() != artifact.sha256:
                raise ArtifactStoreError("artifact.object_readback_mismatch")
            return payload
        finally:
            if self._observe_authority_metrics:
                _ARTIFACT_STORE_METRICS.observe_store(
                    "read", time.perf_counter() - started
                )

    def _read_bytes_sync(self, artifact: ArtifactRef) -> bytes:
        target = self._path(artifact)
        try:
            return target.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactNotFound("artifact.object_missing") from exc

    async def exists(self, artifact: ArtifactRef) -> bool:
        started = time.perf_counter()
        try:
            target = self._path(artifact)
            if not await asyncio.to_thread(target.is_file):
                return False
            try:
                await self.read_bytes(artifact)
            except ArtifactStoreError:
                return False
            return True
        finally:
            if self._observe_authority_metrics:
                _ARTIFACT_STORE_METRICS.observe_store(
                    "head", time.perf_counter() - started
                )


class WorkerArtifactTransportCache(LocalDevelopmentArtifactObjectStore):
    """Digest-verified scratch shared with one-shot network-none workers.

    This adapter is never registered as the production artifact authority.
    PPT Core hydrates authoritative ``ArtifactRef`` bytes into it immediately
    before dispatch and adopts verified worker output back into the configured
    object store before any durable metadata is committed.
    """

    _maintenance_lock = threading.Lock()

    def __init__(
        self,
        root: Path,
        *,
        max_cache_bytes: int,
        ttl_seconds: int,
        max_object_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        if max_cache_bytes <= 0 or ttl_seconds <= 0:
            raise ValueError("worker artifact cache limits must be positive")
        super().__init__(
            root,
            max_object_bytes=max_object_bytes,
            observe_authority_metrics=False,
        )
        self._max_cache_bytes = max_cache_bytes
        self._ttl_seconds = ttl_seconds
        self._known_entries: dict[Path, int] = {}

    def _cache_entries(self) -> list[tuple[Path, float, int]]:
        entries: list[tuple[Path, float, int]] = []
        if not self._root.exists():
            return entries
        for path in self._root.rglob("*"):
            try:
                if path.is_symlink() or not path.is_file() or path.name.startswith("."):
                    continue
                stat = path.stat()
            except OSError:
                continue
            entries.append((path, stat.st_mtime, stat.st_size))
        return entries

    def _maintain_sync(self, *, protected: Path | None = None) -> None:
        with self._maintenance_lock:
            now = time.time()
            entries = self._cache_entries()
            observed = {path: size for path, _, size in entries}
            missing = {
                path: size
                for path, size in self._known_entries.items()
                if path not in observed
            }
            survivors: list[tuple[Path, float, int]] = []
            evicted_count = len(missing)
            evicted_bytes = sum(missing.values())
            for path, modified, size in entries:
                if path != protected and now - modified > self._ttl_seconds:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        survivors.append((path, modified, size))
                    else:
                        evicted_count += 1
                        evicted_bytes += size
                else:
                    survivors.append((path, modified, size))
            total = sum(size for _, _, size in survivors)
            for path, modified, size in sorted(survivors, key=lambda item: item[1]):
                if total <= self._max_cache_bytes:
                    break
                if path == protected:
                    continue
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
                total -= size
                evicted_count += 1
                evicted_bytes += size
            current = self._cache_entries()
            self._known_entries = {path: size for path, _, size in current}
            _ARTIFACT_STORE_METRICS.set_cache_usage(
                entries=len(current),
                size_bytes=sum(size for _, _, size in current),
            )
            if evicted_count:
                _ARTIFACT_STORE_METRICS.observe_cache(
                    "eviction", count=evicted_count, size_bytes=evicted_bytes
                )

    async def maintain(self) -> None:
        await asyncio.to_thread(self._maintain_sync)

    async def put_bytes(self, artifact: ArtifactRef, payload: bytes) -> None:
        if len(payload) > self._max_cache_bytes:
            _ARTIFACT_STORE_METRICS.observe_cache("miss")
            raise ArtifactStoreError("artifact.worker_cache_budget_exceeded")
        target = self._path(artifact)
        existed = await asyncio.to_thread(target.is_file)
        try:
            await super().put_bytes(artifact, payload)
        except ArtifactStoreError:
            _ARTIFACT_STORE_METRICS.observe_cache("miss")
            raise
        await asyncio.to_thread(os.utime, target, None)
        _ARTIFACT_STORE_METRICS.observe_cache("hit" if existed else "miss")
        await asyncio.to_thread(self._maintain_sync, protected=target)

    async def read_bytes(self, artifact: ArtifactRef) -> bytes:
        target = self._path(artifact)
        try:
            modified = await asyncio.to_thread(lambda: target.stat().st_mtime)
            if time.time() - modified > self._ttl_seconds:
                await asyncio.to_thread(target.unlink, missing_ok=True)
                await asyncio.to_thread(self._maintain_sync)
                raise ArtifactNotFound("artifact.cache_entry_expired")
            payload = await super().read_bytes(artifact)
        except ArtifactStoreError:
            _ARTIFACT_STORE_METRICS.observe_cache("miss")
            raise
        except OSError as exc:
            _ARTIFACT_STORE_METRICS.observe_cache("miss")
            raise ArtifactNotFound("artifact.object_missing") from exc
        await asyncio.to_thread(os.utime, target, None)
        _ARTIFACT_STORE_METRICS.observe_cache("hit")
        return payload

    async def exists(self, artifact: ArtifactRef) -> bool:
        try:
            await self.read_bytes(artifact)
        except ArtifactStoreError:
            return False
        return True


class S3ArtifactObjectStore:
    """Production S3-compatible immutable object-store adapter.

    The bucket is an operational secret/configuration concern and is not part
    of ArtifactRef.  SHA-256 and owner bindings are repeated in object metadata
    so readback does not trust a key or ETag alone.
    """

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
        try:
            return self._client.head_object(Bucket=self._bucket, Key=self._key(artifact))
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
        started = time.perf_counter()
        if len(payload) != artifact.size_bytes or sha256(payload).hexdigest() != artifact.sha256:
            raise ArtifactBindingError("artifact.payload_binding_invalid")
        if len(payload) > self._max_object_bytes:
            raise ArtifactStoreError("artifact.object_too_large")
        try:
            await asyncio.to_thread(self._put_bytes_sync, artifact, payload)
        finally:
            _ARTIFACT_STORE_METRICS.observe_store(
                "put", time.perf_counter() - started
            )

    def _put_bytes_sync(self, artifact: ArtifactRef, payload: bytes) -> None:
        existing = self._head_sync(artifact)
        if existing is not None:
            self._validate_head(artifact, existing)
            return
        request: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": self._key(artifact),
            "Body": payload,
            "ContentLength": len(payload),
            "ContentType": artifact.media_type,
            "Metadata": self._metadata(artifact),
            "IfNoneMatch": "*",
        }
        if self._server_side_encryption:
            request["ServerSideEncryption"] = self._server_side_encryption
        if self._kms_key_id:
            request["SSEKMSKeyId"] = self._kms_key_id
        try:
            self._client.put_object(**request)
        except Exception as exc:
            if self._error_code(exc) not in {"409", "412", "ConditionalRequestConflict", "PreconditionFailed"}:
                raise ArtifactStoreError("artifact.object_store_write_failed") from exc
        readback = self._head_sync(artifact)
        if readback is None:
            raise ArtifactStoreError("artifact.object_store_write_readback_missing")
        self._validate_head(artifact, readback)

    async def read_bytes(self, artifact: ArtifactRef) -> bytes:
        started = time.perf_counter()
        try:
            return await asyncio.to_thread(self._read_bytes_sync, artifact)
        finally:
            _ARTIFACT_STORE_METRICS.observe_store(
                "read", time.perf_counter() - started
            )

    def _read_bytes_sync(self, artifact: ArtifactRef) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key(artifact))
            body = response["Body"]
            try:
                payload = body.read()
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
        except Exception as exc:
            if self._error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                raise ArtifactNotFound("artifact.object_missing") from exc
            raise ArtifactStoreError("artifact.object_store_read_failed") from exc
        if len(payload) != artifact.size_bytes or sha256(payload).hexdigest() != artifact.sha256:
            raise ArtifactStoreError("artifact.object_readback_mismatch")
        return payload

    async def exists(self, artifact: ArtifactRef) -> bool:
        started = time.perf_counter()
        try:
            head = await asyncio.to_thread(self._head_sync, artifact)
            if head is None:
                return False
            self._validate_head(artifact, head)
            return True
        finally:
            _ARTIFACT_STORE_METRICS.observe_store(
                "head", time.perf_counter() - started
            )


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

    async def read_ref_bytes(self, reference: ArtifactRef) -> bytes:
        if not await self._object_store.exists(reference):
            raise ArtifactNotFound("artifact.object_missing")
        return await self._object_store.read_bytes(reference)

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
        reference = ArtifactRef(
            artifact_id=uuid.uuid4(),
            owner_id=owner_id,
            tenant_id=tenant_id,
            kind=kind,
            sha256=digest,
            size_bytes=len(payload),
            media_type=media_type,
            storage_key=artifact_storage_key(
                owner_id=owner_id,
                tenant_id=tenant_id,
                digest=digest,
            ),
            created_by_operation_id=created_by_operation_id,
            retention_class=retention_class,
        )
        await self._object_store.put_bytes(reference, payload)
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
        inserted = False
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
            inserted = True
        except IntegrityError:
            inserted = False
        if inserted:
            return row.to_ref()
        existing = await session.scalar(
            select(PublicArtifact).where(
                PublicArtifact.owner_id == owner_id,
                PublicArtifact.tenant_id == tenant_id,
                PublicArtifact.kind == kind,
                PublicArtifact.sha256 == digest,
                PublicArtifact.created_by_operation_id == created_by_operation_id,
                PublicArtifact.retention_class == retention_class,
            )
        )
        if existing is None:
            raise ArtifactStoreError("artifact.metadata_deduplication_failed")
        existing_ref = existing.to_ref()
        if not await self._object_store.exists(existing_ref):
            raise ArtifactStoreError("artifact.metadata_object_missing")
        return existing_ref

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


def configured_public_artifact_service() -> DurableArtifactService:
    """Create the configured authority adapter without accepting request input."""

    backend = (os.getenv("PUBLIC_ARTIFACT_STORE_BACKEND") or "").strip().lower()
    if backend == "local-development":
        root_value = (os.getenv("PUBLIC_WORKER_OBJECT_STORE_ROOT") or "").strip()
        root = Path(root_value)
        if not root_value or not root.is_absolute():
            raise RuntimeError("artifact.local_root_required")
        environment = (os.getenv("ENVIRONMENT") or "production").strip().lower()
        if environment == "local":
            environment = "development"
        return DurableArtifactService(
            require_development_local_artifact_store(
                root,
                runtime_environment=environment,
            )
        )
    if backend == "s3":
        bucket = (os.getenv("PUBLIC_ARTIFACT_S3_BUCKET") or "").strip()
        prefix = (os.getenv("PUBLIC_ARTIFACT_S3_PREFIX") or "presenton-artifacts").strip()
        return DurableArtifactService(
            S3ArtifactObjectStore(bucket=bucket, key_prefix=prefix)
        )
    raise RuntimeError("artifact.backend_required")


def configured_worker_artifact_cache() -> WorkerArtifactTransportCache:
    """Return the local transport cache; never infer it from request data."""

    root_value = (os.getenv("PUBLIC_WORKER_ARTIFACT_CACHE_ROOT") or "").strip()
    if not root_value:
        # Compatibility is limited to the explicitly development-only backend.
        backend = (os.getenv("PUBLIC_ARTIFACT_STORE_BACKEND") or "").strip().lower()
        environment = (os.getenv("ENVIRONMENT") or "production").strip().lower()
        if backend == "local-development" and environment in {
            "development",
            "dev",
            "local",
            "test",
        }:
            root_value = (os.getenv("PUBLIC_WORKER_OBJECT_STORE_ROOT") or "").strip()
    root = Path(root_value)
    if not root_value or not root.is_absolute():
        raise RuntimeError("artifact.worker_cache_root_required")
    try:
        max_cache_bytes = int(
            os.getenv(
                "PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES",
                str(1024 * 1024 * 1024),
            )
        )
        ttl_seconds = int(
            os.getenv("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", "900")
        )
    except ValueError as exc:
        raise RuntimeError("artifact.worker_cache_limits_invalid") from exc
    if max_cache_bytes <= 0 or ttl_seconds <= 0:
        raise RuntimeError("artifact.worker_cache_limits_invalid")
    return WorkerArtifactTransportCache(
        root,
        max_cache_bytes=max_cache_bytes,
        ttl_seconds=ttl_seconds,
    )


@dataclass(frozen=True, slots=True)
class ExportIdentityKey:
    owner_id: uuid.UUID
    tenant_id: str
    presentation_id: uuid.UUID
    revision: int
    canonical_digest: str
    export_mode: Literal["pptx", "pdf"]
    font_manifest_digest: str
    exporter_version: str
    source_artifact_id: uuid.UUID

    def normalized(self) -> "ExportIdentityKey":
        if self.revision < 0:
            raise ValueError("export revision must be nonnegative")
        tenant_id = self.tenant_id.strip()
        exporter_version = self.exporter_version.strip()
        if not tenant_id or len(tenant_id) > 128:
            raise ValueError("export tenant binding is invalid")
        if not exporter_version or len(exporter_version) > 128:
            raise ValueError("exporter version is invalid")
        return ExportIdentityKey(
            owner_id=self.owner_id,
            tenant_id=tenant_id,
            presentation_id=self.presentation_id,
            revision=self.revision,
            canonical_digest=normalize_sha256(self.canonical_digest),
            export_mode=self.export_mode,
            font_manifest_digest=normalize_sha256(self.font_manifest_digest),
            exporter_version=exporter_version,
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


async def _require_source_artifact(session: AsyncSession, key: ExportIdentityKey) -> PublicArtifact:
    source = await session.scalar(
        select(PublicArtifact).where(
            PublicArtifact.id == key.source_artifact_id,
            PublicArtifact.owner_id == key.owner_id,
            PublicArtifact.tenant_id == key.tenant_id,
            PublicArtifact.sha256 == key.canonical_digest,
            PublicArtifact.kind == "checkpoint",
        )
    )
    if source is None:
        raise ArtifactNotFound("export.source_artifact_binding_invalid")
    return source


def _identity_predicate(key: ExportIdentityKey):
    return (
        (PublicExportIdentity.owner_id == key.owner_id)
        & (PublicExportIdentity.presentation_id == key.presentation_id)
        & (PublicExportIdentity.revision == key.revision)
        & (PublicExportIdentity.canonical_digest == key.canonical_digest)
        & (PublicExportIdentity.export_mode == key.export_mode)
        & (PublicExportIdentity.font_manifest_digest == key.font_manifest_digest)
        & (PublicExportIdentity.exporter_version == key.exporter_version)
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
        export_mode=key.export_mode,
        font_manifest_digest=key.font_manifest_digest,
        exporter_version=key.exporter_version,
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
        return ExportClaim("claimed", candidate.id, 1, attempt_token=attempt_token)

    existing = await session.scalar(
        select(PublicExportIdentity)
        .where(_identity_predicate(key))
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
    return result.rowcount == 1


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
    return result.rowcount == 1
