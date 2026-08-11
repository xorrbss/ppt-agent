"""Process-local async document decomposition jobs.

This registry is intentionally scoped to the single-process desktop runtime. Jobs
are not durable across a backend restart and must not be used with multiple API
workers without replacing the registry with shared storage and a worker queue.

Cancellation is cooperative. A running LibreOffice/ImageMagick/LiteParse call is
allowed to finish because those calls currently execute in worker threads and
cannot be killed safely from the event loop. Cancellation takes effect before the
next file is started, and job-owned output is then removed.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import shutil
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from constants.documents import TEXT_EXTENSIONS
from models.decompose_job import (
    DecomposeJobError,
    DecomposeJobProgress,
    DecomposeJobSnapshot,
    DecomposeJobStatus,
)
from models.decomposed_file_info import DecomposedFileInfo
from services.document_conversion_service import DocumentConversionError
from services.database import async_session_maker
from services.documents_loader import DocumentsLoader
from services.references import ReferenceProviderError, admit_reference_snapshot
from services.temp_file_service import TEMP_FILE_SERVICE
from utils.owner_context import reset_current_owner_id, set_current_owner_id


LOGGER = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


def _positive_int_env(name: str, default: int, *, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("Ignoring invalid %s=%r", name, raw)
        return default
    if value < 1 or value > maximum:
        LOGGER.warning("Ignoring out-of-range %s=%r", name, raw)
        return default
    return value


class DecomposeJobConflictError(Exception):
    pass


class DecomposeJobCapacityError(Exception):
    pass


@dataclass
class _DecomposeJobRecord:
    job_id: uuid.UUID
    owner_key: str
    file_paths: tuple[str, ...]
    reference_snapshot_id: Optional[uuid.UUID]
    document_count: int
    language: Optional[str]
    request_hash: str
    idempotency_hash: Optional[str]
    status: DecomposeJobStatus
    created_at: datetime
    updated_at: datetime
    completed_files: int = 0
    current_file: Optional[str] = None
    result: Optional[list[DecomposedFileInfo]] = None
    error: Optional[DecomposeJobError] = None
    expires_at: Optional[datetime] = None
    temp_dir: Optional[str] = None
    cancel_requested: bool = False
    task: Optional[asyncio.Task[None]] = field(default=None, repr=False)
    expiry_task: Optional[asyncio.Task[None]] = field(default=None, repr=False)


class DecomposeJobService:
    def __init__(
        self,
        *,
        max_concurrency: Optional[int] = None,
        max_pending_jobs: Optional[int] = None,
        result_ttl_seconds: Optional[int] = None,
    ) -> None:
        self.max_concurrency = max_concurrency or _positive_int_env(
            "DOCUMENT_DECOMPOSE_JOB_MAX_CONCURRENCY", 1, maximum=8
        )
        self.max_pending_jobs = max_pending_jobs or _positive_int_env(
            "DOCUMENT_DECOMPOSE_JOB_MAX_PENDING", 32, maximum=512
        )
        self.result_ttl_seconds = result_ttl_seconds or _positive_int_env(
            "DOCUMENT_DECOMPOSE_JOB_TTL_SECONDS", 3600, maximum=86400
        )
        self._jobs: dict[uuid.UUID, _DecomposeJobRecord] = {}
        self._idempotency: dict[tuple[str, str], uuid.UUID] = {}
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    @staticmethod
    def _request_hash(
        file_paths: list[str],
        reference_snapshot_id: Optional[uuid.UUID],
        language: Optional[str],
    ) -> str:
        payload = json.dumps(
            {
                "file_paths": file_paths,
                "reference_snapshot_id": (
                    str(reference_snapshot_id) if reference_snapshot_id else None
                ),
                "language": language,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _idempotency_hash(idempotency_key: Optional[str]) -> Optional[str]:
        if idempotency_key is None:
            return None
        normalized = idempotency_key.strip()
        if not normalized or len(normalized) > 255:
            raise ValueError("Idempotency-Key must be between 1 and 255 characters")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _snapshot(record: _DecomposeJobRecord) -> DecomposeJobSnapshot:
        return DecomposeJobSnapshot(
            job_id=record.job_id,
            status=record.status,
            progress=DecomposeJobProgress(
                completed_files=record.completed_files,
                total_files=record.document_count,
                current_file=record.current_file,
            ),
            result=record.result,
            error=record.error,
            created_at=record.created_at,
            updated_at=record.updated_at,
            expires_at=record.expires_at,
        )

    async def create_job(
        self,
        *,
        owner_key: str,
        language: Optional[str],
        idempotency_key: Optional[str],
        file_paths: Optional[list[str]] = None,
        reference_snapshot_id: Optional[uuid.UUID] = None,
        reference_document_count: Optional[int] = None,
    ) -> tuple[DecomposeJobSnapshot, bool]:
        normalized_paths = file_paths or []
        if bool(normalized_paths) == bool(reference_snapshot_id):
            raise ValueError(
                "Exactly one of file_paths or reference_snapshot_id is required"
            )
        document_count = (
            len(normalized_paths)
            if reference_snapshot_id is None
            else int(reference_document_count or 0)
        )
        if document_count < 1:
            raise ValueError("Document decomposition source is empty")
        await self.cleanup_expired()
        idempotency_hash = self._idempotency_hash(idempotency_key)
        request_hash = self._request_hash(
            normalized_paths,
            reference_snapshot_id,
            language,
        )
        now = datetime.now(timezone.utc)

        async with self._lock:
            if idempotency_hash is not None:
                scope = (owner_key, idempotency_hash)
                existing_id = self._idempotency.get(scope)
                existing = self._jobs.get(existing_id) if existing_id else None
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise DecomposeJobConflictError
                    return self._snapshot(existing), False

            active_jobs = sum(
                record.status not in _TERMINAL_STATUSES
                for record in self._jobs.values()
            )
            if active_jobs >= self.max_pending_jobs:
                raise DecomposeJobCapacityError

            job_id = uuid.uuid4()
            record = _DecomposeJobRecord(
                job_id=job_id,
                owner_key=owner_key,
                file_paths=tuple(normalized_paths),
                reference_snapshot_id=reference_snapshot_id,
                document_count=document_count,
                language=language,
                request_hash=request_hash,
                idempotency_hash=idempotency_hash,
                status="queued",
                created_at=now,
                updated_at=now,
            )
            self._jobs[job_id] = record
            if idempotency_hash is not None:
                self._idempotency[(owner_key, idempotency_hash)] = job_id
            record.task = asyncio.create_task(
                self._run_job(job_id),
                name=f"decompose-job-{job_id}",
            )
            return self._snapshot(record), True

    async def get_job(
        self, *, owner_key: str, job_id: uuid.UUID
    ) -> Optional[DecomposeJobSnapshot]:
        await self.cleanup_expired()
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.owner_key != owner_key:
                return None
            return self._snapshot(record)

    async def cancel_job(
        self, *, owner_key: str, job_id: uuid.UUID
    ) -> tuple[Optional[DecomposeJobSnapshot], bool]:
        await self.cleanup_expired()
        cleanup_path: Optional[str] = None
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.owner_key != owner_key:
                return None, False
            if record.status in _TERMINAL_STATUSES:
                return self._snapshot(record), False

            record.cancel_requested = True
            record.updated_at = datetime.now(timezone.utc)
            if record.status == "queued":
                # No converter owns job output yet. The queued worker will notice
                # the terminal state after it acquires capacity and exit.
                record.status = "cancelled"
                record.current_file = None
                record.expires_at = record.updated_at + timedelta(
                    seconds=self.result_ttl_seconds
                )
                self._schedule_expiry(record)
                cleanup_path = record.temp_dir
            else:
                record.status = "cancelling"
            snapshot = self._snapshot(record)

        if cleanup_path:
            self._cleanup_temp_dir(cleanup_path)
        return snapshot, True

    async def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc)
        expired: list[_DecomposeJobRecord] = []
        async with self._lock:
            for job_id, record in list(self._jobs.items()):
                if record.expires_at is None or record.expires_at > now:
                    continue
                expired.append(self._jobs.pop(job_id))
                if record.idempotency_hash is not None:
                    self._idempotency.pop(
                        (record.owner_key, record.idempotency_hash), None
                    )

        for record in expired:
            if record.temp_dir:
                self._cleanup_temp_dir(record.temp_dir)
        return len(expired)

    async def _run_job(self, job_id: uuid.UUID) -> None:
        try:
            async with self._semaphore:
                async with self._lock:
                    record = self._jobs.get(job_id)
                    if record is None or record.status == "cancelled":
                        return
                    record.status = "running"
                    record.updated_at = datetime.now(timezone.utc)

                try:
                    if record.reference_snapshot_id is not None:
                        await self._run_reference_snapshot_job(job_id, record)
                        return

                    temp_dir = TEMP_FILE_SERVICE.create_temp_dir(str(job_id))
                    async with self._lock:
                        record = self._jobs.get(job_id)
                        if record is None:
                            self._cleanup_temp_dir(temp_dir)
                            return
                        record.temp_dir = temp_dir

                    results: list[DecomposedFileInfo] = []
                    for file_path in record.file_paths:
                        if await self._stop_if_cancelled(job_id):
                            return
                        await self._set_current_file(job_id, os.path.basename(file_path))

                        loader = DocumentsLoader(
                            file_paths=[file_path],
                            presentation_language=record.language,
                        )
                        await loader.load_documents(temp_dir)
                        if await self._stop_if_cancelled(job_id):
                            return

                        parsed_document = loader.documents[0]
                        if Path(file_path).suffix.lower() not in TEXT_EXTENSIONS:
                            parsed_document = parsed_document.replace("<br>", "\n")
                        result_path = TEMP_FILE_SERVICE.create_temp_file_path(
                            f"{uuid.uuid4()}.txt", temp_dir
                        )
                        with open(
                            result_path, "w", encoding="utf-8", newline="\n"
                        ) as text_file:
                            text_file.write(parsed_document)
                        results.append(
                            DecomposedFileInfo(
                                name=os.path.basename(file_path),
                                file_path=result_path,
                            )
                        )
                        await self._mark_file_complete(job_id)

                    if await self._stop_if_cancelled(job_id):
                        return
                    await self._mark_succeeded(job_id, results)
                except asyncio.CancelledError:
                    await self._mark_cancelled(job_id)
                except DocumentConversionError as exc:
                    await self._mark_failed(
                        job_id,
                        self._safe_conversion_error(exc),
                        cause=exc,
                    )
                except ReferenceProviderError as exc:
                    await self._mark_failed(
                        job_id,
                        DecomposeJobError(
                            code=exc.code,
                            message="The admitted document reference is no longer available.",
                            retryable=exc.retryable,
                        ),
                        cause=exc,
                    )
                except HTTPException as exc:
                    await self._mark_failed(
                        job_id,
                        self._safe_http_error(exc),
                        cause=exc,
                    )
                except Exception as exc:
                    await self._mark_failed(
                        job_id,
                        DecomposeJobError(
                            code="DOCUMENT_DECOMPOSE_FAILED",
                            message="Document processing failed. Please try again.",
                            retryable=False,
                        ),
                        cause=exc,
                    )
        except asyncio.CancelledError:
            # Application shutdown can cancel a task while it is waiting for
            # capacity. Keep registry state consistent for any final snapshot.
            await self._mark_cancelled(job_id)

    async def _run_reference_snapshot_job(
        self,
        job_id: uuid.UUID,
        record: _DecomposeJobRecord,
    ) -> None:
        """Project a re-admitted immutable snapshot without exposing paths.

        The worker receives only the server-issued snapshot identity. Owner,
        expiry, snapshot digest, live asset digest and lifecycle state are all
        replayed immediately before the result is published.
        """

        try:
            owner_id = (
                None
                if record.owner_key == "anonymous"
                else uuid.UUID(record.owner_key)
            )
        except ValueError as error:
            raise ReferenceProviderError(
                "reference_owner_scope_invalid", status_code=409
            ) from error

        token = set_current_owner_id(owner_id)
        try:
            async with async_session_maker() as session:
                snapshot = await admit_reference_snapshot(
                    session,
                    snapshot_id=record.reference_snapshot_id,
                )
        finally:
            reset_current_owner_id(token)

        if snapshot.provider_id != "uploaded_document":
            raise ReferenceProviderError(
                "uploaded_reference_snapshot_required", status_code=400
            )
        documents = snapshot.payload().get("documents")
        if not isinstance(documents, list) or len(documents) != record.document_count:
            raise ReferenceProviderError(
                "reference_snapshot_payload_invalid", status_code=409
            )

        results: list[DecomposedFileInfo] = []
        for index, document in enumerate(documents):
            if await self._stop_if_cancelled(job_id):
                return
            if not isinstance(document, dict):
                raise ReferenceProviderError(
                    "reference_snapshot_payload_invalid", status_code=409
                )
            try:
                asset_id = uuid.UUID(str(document["assetId"]))
                text = str(document["text"])
                expected_digest = str(document["contentSha256"])
            except (KeyError, TypeError, ValueError) as error:
                raise ReferenceProviderError(
                    "reference_snapshot_payload_invalid", status_code=409
                ) from error
            actual_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(actual_digest, expected_digest):
                raise ReferenceProviderError(
                    "reference_snapshot_integrity_failed", status_code=409
                )
            name = str(document.get("name") or f"document-{index + 1}")[:512]
            await self._set_current_file(job_id, name)
            results.append(
                DecomposedFileInfo(
                    name=name,
                    asset_id=asset_id,
                    content_sha256=actual_digest,
                )
            )
            await self._mark_file_complete(job_id)

        if await self._stop_if_cancelled(job_id):
            return
        await self._mark_succeeded(job_id, results)

    async def _set_current_file(self, job_id: uuid.UUID, filename: str) -> None:
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                record.current_file = filename
                record.updated_at = datetime.now(timezone.utc)

    async def _mark_file_complete(self, job_id: uuid.UUID) -> None:
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                record.completed_files += 1
                record.current_file = None
                record.updated_at = datetime.now(timezone.utc)

    async def _stop_if_cancelled(self, job_id: uuid.UUID) -> bool:
        async with self._lock:
            record = self._jobs.get(job_id)
            should_stop = record is None or record.cancel_requested
        if should_stop and record is not None:
            await self._mark_cancelled(job_id)
        return should_stop

    async def _mark_succeeded(
        self, job_id: uuid.UUID, result: list[DecomposedFileInfo]
    ) -> None:
        cleanup_path: Optional[str] = None
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            now = datetime.now(timezone.utc)
            if record.cancel_requested:
                record.status = "cancelled"
                record.result = None
                record.error = None
                record.current_file = None
                record.updated_at = now
                record.expires_at = now + timedelta(seconds=self.result_ttl_seconds)
                self._schedule_expiry(record)
                cleanup_path = record.temp_dir
                record.temp_dir = None
            else:
                record.status = "succeeded"
                record.result = result
                record.error = None
                record.current_file = None
                record.updated_at = now
                record.expires_at = now + timedelta(seconds=self.result_ttl_seconds)
                self._schedule_expiry(record)
        if cleanup_path:
            self._cleanup_temp_dir(cleanup_path)

    async def _mark_failed(
        self,
        job_id: uuid.UUID,
        error: DecomposeJobError,
        *,
        cause: BaseException,
    ) -> None:
        cleanup_path: Optional[str] = None
        diagnostic_path: Optional[str] = None
        current_file: Optional[str] = None
        completed_files = 0
        total_files = 0
        expires_at: Optional[datetime] = None
        cancelled = False
        diagnostics_file: Optional[str] = None
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            now = datetime.now(timezone.utc)
            cancelled = record.cancel_requested
            current_file = record.current_file
            completed_files = record.completed_files
            total_files = record.document_count
            record.status = "cancelled" if cancelled else "failed"
            if cancelled:
                record.error = None
            else:
                record.error = error.model_copy(
                    update={
                        "filename": error.filename or current_file,
                        "request_id": str(job_id),
                    }
                )
            record.result = None
            # Keep the failing filename visible in the terminal snapshot. It is
            # essential context when a multi-file request fails part-way through.
            if cancelled:
                record.current_file = None
            record.updated_at = now
            record.expires_at = now + timedelta(seconds=self.result_ttl_seconds)
            expires_at = record.expires_at
            self._schedule_expiry(record)
            if cancelled:
                cleanup_path = record.temp_dir
                record.temp_dir = None
            else:
                diagnostic_path = record.temp_dir
                if diagnostic_path and expires_at is not None:
                    # Write diagnostics before releasing the lock so a terminal
                    # snapshot is never observable before its evidence exists.
                    diagnostics_file = self._write_failure_diagnostics(
                        temp_dir=diagnostic_path,
                        job_id=job_id,
                        error=error,
                        cause=cause,
                        current_file=current_file,
                        completed_files=completed_files,
                        total_files=total_files,
                        failed_at=now,
                        expires_at=expires_at,
                    )

        log_message = (
            "document_decompose_failed job_id=%s error_code=%s "
            "exception_type=%s current_file=%r completed_files=%s "
            "total_files=%s cancelled=%s diagnostic_dir=%r "
            "diagnostics_file=%r retained_until=%s"
        )
        LOGGER.error(
            log_message,
            job_id,
            error.code,
            type(cause).__name__,
            current_file,
            completed_files,
            total_files,
            cancelled,
            diagnostic_path,
            diagnostics_file,
            expires_at.isoformat() if expires_at else None,
            exc_info=(type(cause), cause, cause.__traceback__),
        )
        if cleanup_path:
            self._cleanup_temp_dir(cleanup_path)

    @staticmethod
    def _write_failure_diagnostics(
        *,
        temp_dir: str,
        job_id: uuid.UUID,
        error: DecomposeJobError,
        cause: BaseException,
        current_file: Optional[str],
        completed_files: int,
        total_files: int,
        failed_at: datetime,
        expires_at: datetime,
    ) -> Optional[str]:
        """Persist internal-only failure details beside retained job artifacts."""

        try:
            root = os.path.realpath(TEMP_FILE_SERVICE.base_dir)
            target = os.path.realpath(temp_dir)
            if target == root or os.path.commonpath((root, target)) != root:
                LOGGER.error(
                    "Refusing unsafe decompose diagnostics target=%s job_id=%s",
                    target,
                    job_id,
                )
                return None

            diagnostics_path = Path(target) / "failure-diagnostics.json"
            payload = {
                "job_id": str(job_id),
                "error_code": error.code,
                "current_file": current_file,
                "completed_files": completed_files,
                "total_files": total_files,
                "failed_at": failed_at.isoformat(),
                "retained_until": expires_at.isoformat(),
                "exception": {
                    "type": type(cause).__name__,
                    "message": str(cause),
                    "traceback": "".join(
                        traceback.format_exception(
                            type(cause), cause, cause.__traceback__
                        )
                    ),
                },
            }
            diagnostics_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return str(diagnostics_path)
        except (OSError, ValueError):
            LOGGER.exception(
                "Failed to persist decompose diagnostics job_id=%s temp_dir=%s",
                job_id,
                temp_dir,
            )
            return None

    async def _mark_cancelled(self, job_id: uuid.UUID) -> None:
        cleanup_path: Optional[str] = None
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            now = datetime.now(timezone.utc)
            record.status = "cancelled"
            record.result = None
            record.error = None
            record.current_file = None
            record.updated_at = now
            record.expires_at = now + timedelta(seconds=self.result_ttl_seconds)
            self._schedule_expiry(record)
            cleanup_path = record.temp_dir
            record.temp_dir = None
        if cleanup_path:
            self._cleanup_temp_dir(cleanup_path)

    @staticmethod
    def _safe_conversion_error(exc: DocumentConversionError) -> DecomposeJobError:
        filename = exc.filename
        if exc.code == "IMAGE_CONVERTER_UNAVAILABLE":
            message = "Image conversion is unavailable. Upload a PNG image or install ImageMagick."
        elif filename:
            message = f"Unable to convert '{filename}'."
        else:
            message = "Unable to convert one of the uploaded documents."
        return DecomposeJobError(
            code=exc.code,
            message=message,
            filename=filename,
            retryable=exc.retryable,
        )

    @staticmethod
    def _safe_http_error(exc: HTTPException) -> DecomposeJobError:
        if exc.status_code < 500 and isinstance(exc.detail, str):
            return DecomposeJobError(
                code="DOCUMENT_DECOMPOSE_REJECTED",
                message=exc.detail,
                retryable=exc.status_code in {408, 429},
            )
        if exc.status_code < 500 and isinstance(exc.detail, dict):
            return DecomposeJobError(
                code=str(exc.detail.get("code") or "DOCUMENT_DECOMPOSE_REJECTED"),
                message=str(exc.detail.get("message") or "The document was rejected."),
                filename=(
                    str(exc.detail["filename"])
                    if exc.detail.get("filename")
                    else None
                ),
                retryable=bool(exc.detail.get("retryable", False)),
            )
        return DecomposeJobError(
            code="DOCUMENT_DECOMPOSE_FAILED",
            message="Document processing failed. Please try again.",
            retryable=exc.status_code >= 500,
        )

    def _schedule_expiry(self, record: _DecomposeJobRecord) -> None:
        if record.expiry_task is not None and not record.expiry_task.done():
            return

        async def expire_after_ttl() -> None:
            await asyncio.sleep(self.result_ttl_seconds)
            await self.cleanup_expired()

        record.expiry_task = asyncio.create_task(
            expire_after_ttl(),
            name=f"decompose-job-expiry-{record.job_id}",
        )

    @staticmethod
    def _cleanup_temp_dir(temp_dir: str) -> None:
        """Remove only a job-created directory under the configured temp root."""

        try:
            root = os.path.realpath(TEMP_FILE_SERVICE.base_dir)
            target = os.path.realpath(temp_dir)
            if target == root or os.path.commonpath((root, target)) != root:
                LOGGER.error("Refusing unsafe decompose job cleanup target=%s", target)
                return
            shutil.rmtree(target, ignore_errors=False)
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            LOGGER.exception("Failed to clean decompose job temp directory=%s", temp_dir)


DECOMPOSE_JOB_SERVICE = DecomposeJobService()
