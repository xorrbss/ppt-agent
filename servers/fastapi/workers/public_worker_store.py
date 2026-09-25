"""Digest-verified transport cache shared with one isolated public worker.

This store is deliberately not an artifact authority: it holds the staged
inputs a single sessionless worker may read and the outputs it just produced,
each verified by size and sha256 before use, under a byte and TTL budget.
"""

from __future__ import annotations

import hashlib
import math
import os
import pathlib
import shutil
import time
import uuid

from services.isolated_worker_contracts import ArtifactRef
from services.public_operation_correlation import log_public_operation_correlation


MAX_ARTIFACT_BYTES = 512 * 1024 * 1024


class PublicWorkerExecutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LocalContentAddressedStore:
    """Non-authoritative transport cache shared with one isolated worker."""

    def __init__(
        self,
        input_root: pathlib.Path,
        output_root: pathlib.Path | None = None,
        active_deadline_epoch_seconds: float | None = None,
    ) -> None:
        resolved_output = input_root if output_root is None else output_root
        if not input_root.is_absolute() or not resolved_output.is_absolute():
            raise PublicWorkerExecutionError("public_worker_store_root_not_absolute")
        try:
            self.max_cache_bytes = int(
                os.getenv("PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES", "")
            )
            self.ttl_seconds = int(
                os.getenv("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", "")
            )
        except ValueError as error:
            raise PublicWorkerExecutionError(
                "public_worker_cache_limits_invalid"
            ) from error
        if self.max_cache_bytes <= 0 or self.ttl_seconds <= 0:
            raise PublicWorkerExecutionError("public_worker_cache_limits_invalid")
        if active_deadline_epoch_seconds is not None and (
            not math.isfinite(active_deadline_epoch_seconds)
            or active_deadline_epoch_seconds <= 0
        ):
            raise PublicWorkerExecutionError("public_worker_active_deadline_invalid")
        self.input_root = input_root.resolve(strict=False)
        self.output_root = resolved_output.resolve(strict=False)
        self._operation_scoped = self.input_root != self.output_root
        self._active_deadline_epoch_seconds = active_deadline_epoch_seconds
        self.output_root.mkdir(parents=True, exist_ok=True)

    def _attempt_active(self) -> bool:
        return (
            self._operation_scoped
            and self._active_deadline_epoch_seconds is not None
            and time.time() <= self._active_deadline_epoch_seconds
        )

    def _require_active_deadline(self) -> None:
        if (
            self._operation_scoped
            and self._active_deadline_epoch_seconds is not None
            and not self._attempt_active()
        ):
            raise PublicWorkerExecutionError("public_worker_artifact_expired")

    def _entries(self) -> list[tuple[pathlib.Path, float, int]]:
        entries: list[tuple[pathlib.Path, float, int]] = []
        for path in self.output_root.rglob("*"):
            try:
                if path.is_symlink() or not path.is_file() or path.name.startswith("."):
                    continue
                stat = path.stat()
            except OSError:
                continue
            entries.append((path, stat.st_mtime, stat.st_size))
        return entries

    def _evict(self, *, protected: pathlib.Path | None = None) -> None:
        now = time.time()
        survivors: list[tuple[pathlib.Path, float, int]] = []
        for path, modified, size in self._entries():
            if path != protected and now - modified > self.ttl_seconds:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    survivors.append((path, modified, size))
            else:
                survivors.append((path, modified, size))
        total = sum(size for _, _, size in survivors)
        for path, _, size in sorted(survivors, key=lambda item: item[1]):
            if total <= self.max_cache_bytes:
                break
            if path == protected:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            total -= size

    def _path(self, storage_key: str, *, output: bool = False) -> pathlib.Path:
        root = self.output_root if output else self.input_root
        candidate = (root / storage_key).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise PublicWorkerExecutionError(
                "public_worker_storage_key_escape"
            ) from error
        return candidate

    def verified_path(self, reference: ArtifactRef) -> pathlib.Path:
        self._require_active_deadline()
        path = self._path(reference.storage_key)
        if not path.is_file() and self.output_root != self.input_root:
            path = self._path(reference.storage_key, output=True)
        is_output = self.output_root == self.input_root or path.is_relative_to(
            self.output_root
        )
        try:
            stat = path.stat()
        except OSError as error:
            raise PublicWorkerExecutionError("public_worker_artifact_unavailable") from error
        if not self._attempt_active() and time.time() - stat.st_mtime > self.ttl_seconds:
            if is_output:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise PublicWorkerExecutionError("public_worker_artifact_expired")
        size = stat.st_size
        if size != reference.size_bytes or size > MAX_ARTIFACT_BYTES:
            raise PublicWorkerExecutionError("public_worker_artifact_size_mismatch")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != reference.sha256:
            raise PublicWorkerExecutionError("public_worker_artifact_digest_mismatch")
        if is_output:
            os.utime(path, None)
        return path

    def read(self, reference: ArtifactRef) -> bytes:
        return self.verified_path(reference).read_bytes()

    def put(
        self,
        payload: bytes,
        *,
        source: ArtifactRef,
        operation_id: uuid.UUID,
        kind: str,
        media_type: str,
    ) -> ArtifactRef:
        self._require_active_deadline()
        try:
            configured_limit = int(
                os.getenv("PPT_WORKER_OUTPUT_LIMIT_BYTES", str(MAX_ARTIFACT_BYTES))
            )
        except ValueError as error:
            raise PublicWorkerExecutionError("public_worker_output_limit_invalid") from error
        output_limit = min(MAX_ARTIFACT_BYTES, configured_limit)
        if configured_limit <= 0:
            raise PublicWorkerExecutionError("public_worker_output_limit_invalid")
        if not payload or len(payload) > output_limit:
            raise PublicWorkerExecutionError("public_worker_output_size_invalid")
        if len(payload) > self.max_cache_bytes:
            raise PublicWorkerExecutionError("public_worker_cache_budget_exceeded")
        temporary_source = self.output_root / f".wire-output-{uuid.uuid4().hex}.tmp"
        try:
            temporary_source.write_bytes(payload)
            return self.put_path(
                temporary_source,
                source=source,
                operation_id=operation_id,
                kind=kind,
                media_type=media_type,
            )
        finally:
            temporary_source.unlink(missing_ok=True)

    def put_path(
        self,
        source_path: pathlib.Path,
        *,
        source: ArtifactRef,
        operation_id: uuid.UUID,
        kind: str,
        media_type: str,
    ) -> ArtifactRef:
        self._require_active_deadline()
        size_bytes = source_path.stat().st_size
        if size_bytes <= 0 or size_bytes > MAX_ARTIFACT_BYTES:
            raise PublicWorkerExecutionError("public_worker_output_size_invalid")
        if size_bytes > self.max_cache_bytes:
            raise PublicWorkerExecutionError("public_worker_cache_budget_exceeded")
        digest_state = hashlib.sha256()
        with source_path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest_state.update(chunk)
        digest = digest_state.hexdigest()
        tenant_scope = hashlib.sha256(source.tenant_id.encode("utf-8")).hexdigest()
        storage_key = (
            f"v1/tenant/{tenant_scope}/owner/{source.owner_id}/"
            f"sha256/{digest[:2]}/{digest}"
        )
        path = self._path(storage_key, output=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            if path.exists():
                if path.stat().st_size != size_bytes:
                    raise PublicWorkerExecutionError("public_worker_cas_conflict")
                existing_digest = hashlib.sha256()
                with path.open("rb") as existing:
                    while chunk := existing.read(1024 * 1024):
                        existing_digest.update(chunk)
                if existing_digest.hexdigest() != digest:
                    raise PublicWorkerExecutionError("public_worker_cas_conflict")
            else:
                if self._attempt_active():
                    active_bytes = sum(size for _, _, size in self._entries())
                    if active_bytes + size_bytes > self.max_cache_bytes:
                        raise PublicWorkerExecutionError(
                            "public_worker_cache_budget_exceeded"
                        )
                with temporary.open("xb") as stream:
                    with source_path.open("rb") as source_stream:
                        shutil.copyfileobj(source_stream, stream, 1024 * 1024)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._require_active_deadline()
                os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        os.utime(path, None)
        if not self._attempt_active():
            self._evict(protected=path)
        reference = ArtifactRef(
            artifact_id=uuid.uuid4(),
            owner_id=source.owner_id,
            tenant_id=source.tenant_id,
            kind=kind,
            sha256=digest,
            size_bytes=size_bytes,
            media_type=media_type,
            storage_key=storage_key,
            created_by_operation_id=operation_id,
            retention_class="ephemeral",
        )
        log_public_operation_correlation(
            operation_id=operation_id,
            component="upload",
            action="artifact_uploaded",
            outcome="succeeded",
        )
        return reference


__all__ = [
    "MAX_ARTIFACT_BYTES",
    "LocalContentAddressedStore",
    "PublicWorkerExecutionError",
]
