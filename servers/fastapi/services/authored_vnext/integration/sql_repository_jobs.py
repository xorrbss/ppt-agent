"""Export job creation, claim, cancellation, retry, and failure responsibilities."""

from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from models.sql.authored_vnext_repository import (
    AuthoredVNextDeckRevision,
    AuthoredVNextExportJob,
)

from .sql_repository_types import (
    ExportJobConflictError,
    OperationFenceError,
    PublicationConflictError,
    SourceDigestMismatchError,
    _export_request_hash,
    _hash,
    _now,
    _require_digest,
)


class AuthoredVNextExportJobRepositoryMixin:
    def create_export_job(
        self,
        *,
        deck_id: str,
        deck_revision: int,
        deck_digest: str,
        master_revision: int,
        master_digest: str,
        spec_digest: str,
        asset_manifest_digest: str,
        font_manifest_digest: str,
        idempotency_key: str,
        request_payload: dict[str, Any],
        master_id: str | None = None,
        expected_package_digest: str | None = None,
        expected_publication_revision: int | None = None,
        expected_publication_digest: str | None = None,
        timeout_ms: int = 120000,
        max_attempts: int = 3,
        operation_fence: int = 1,
    ) -> AuthoredVNextExportJob:
        try:
            return self._create_export_job(
                deck_id=deck_id,
                deck_revision=deck_revision,
                deck_digest=deck_digest,
                master_id=master_id,
                master_revision=master_revision,
                master_digest=master_digest,
                spec_digest=spec_digest,
                asset_manifest_digest=asset_manifest_digest,
                font_manifest_digest=font_manifest_digest,
                idempotency_key=idempotency_key,
                request_payload=request_payload,
                expected_package_digest=expected_package_digest,
                expected_publication_revision=expected_publication_revision,
                expected_publication_digest=expected_publication_digest,
                timeout_ms=timeout_ms,
                max_attempts=max_attempts,
                operation_fence=operation_fence,
            )
        except IntegrityError as exc:
            request_hash = _export_request_hash(
                request_payload,
                deck_revision=deck_revision,
                deck_digest=deck_digest,
                master_id=master_id,
                master_revision=master_revision,
                master_digest=master_digest,
                spec_digest=spec_digest,
                asset_manifest_digest=asset_manifest_digest,
                font_manifest_digest=font_manifest_digest,
                expected_package_digest=expected_package_digest,
                expected_publication_revision=expected_publication_revision,
                expected_publication_digest=expected_publication_digest,
                timeout_ms=timeout_ms,
                max_attempts=max_attempts,
                operation_fence=operation_fence,
            )
            with self._sessions() as session:
                existing = session.scalar(
                    select(AuthoredVNextExportJob).where(
                        *self._scope(AuthoredVNextExportJob),
                        AuthoredVNextExportJob.deck_id == deck_id,
                        AuthoredVNextExportJob.idempotency_key_hash == _hash(idempotency_key),
                    )
                )
                if existing is not None and existing.request_hash == request_hash:
                    return existing
            raise ExportJobConflictError("concurrent idempotency conflict") from exc

    def _create_export_job(
        self,
        *,
        deck_id: str,
        deck_revision: int,
        deck_digest: str,
        master_revision: int,
        master_digest: str,
        spec_digest: str,
        asset_manifest_digest: str,
        font_manifest_digest: str,
        idempotency_key: str,
        request_payload: dict[str, Any],
        master_id: str | None = None,
        expected_package_digest: str | None = None,
        expected_publication_revision: int | None = None,
        expected_publication_digest: str | None = None,
        timeout_ms: int = 120000,
        max_attempts: int = 3,
        operation_fence: int = 1,
    ) -> AuthoredVNextExportJob:
        for field, digest in (
            ("deck_digest", deck_digest),
            ("master_digest", master_digest),
            ("spec_digest", spec_digest),
            ("asset_manifest_digest", asset_manifest_digest),
            ("font_manifest_digest", font_manifest_digest),
        ):
            _require_digest(digest, field)
        if expected_package_digest is not None:
            _require_digest(expected_package_digest, "expected_package_digest")
        if expected_publication_digest is not None:
            _require_digest(expected_publication_digest, "expected_publication_digest")
        if timeout_ms <= 0 or max_attempts <= 0 or operation_fence <= 0:
            raise ValueError("timeout_ms, max_attempts, and operation_fence must be positive")
        request_hash = _export_request_hash(
            request_payload,
            deck_revision=deck_revision,
            deck_digest=deck_digest,
            master_id=master_id,
            master_revision=master_revision,
            master_digest=master_digest,
            spec_digest=spec_digest,
            asset_manifest_digest=asset_manifest_digest,
            font_manifest_digest=font_manifest_digest,
            expected_package_digest=expected_package_digest,
            expected_publication_revision=expected_publication_revision,
            expected_publication_digest=expected_publication_digest,
            timeout_ms=timeout_ms,
            max_attempts=max_attempts,
            operation_fence=operation_fence,
        )
        key_hash = _hash(idempotency_key)
        with self._sessions.begin() as session:
            account_generation = self._active_account_generation(session)
            existing = session.scalar(
                select(AuthoredVNextExportJob).where(
                    *self._scope(AuthoredVNextExportJob),
                    AuthoredVNextExportJob.deck_id == deck_id,
                    AuthoredVNextExportJob.idempotency_key_hash == key_hash,
                )
            )
            if existing:
                if existing.request_hash != request_hash:
                    raise ExportJobConflictError("idempotency payload changed")
                return existing
            deck_head = self._head(session, "deck", deck_id)
            if deck_head is None or deck_head.revision != deck_revision or deck_head.digest != deck_digest:
                raise SourceDigestMismatchError("deck head changed")
            deck_row = session.scalar(
                select(AuthoredVNextDeckRevision).where(
                    *self._scope(AuthoredVNextDeckRevision),
                    AuthoredVNextDeckRevision.deck_id == deck_id,
                    AuthoredVNextDeckRevision.revision == deck_revision,
                )
            )
            if (
                deck_row is None
                or deck_row.master_revision != master_revision
                or (master_id is not None and deck_row.master_id != master_id)
                or (master_id is None and deck_row.master_digest != master_digest)
            ):
                raise SourceDigestMismatchError("master source changed")
            resolved_master_id = deck_row.master_id
            pointer = self._artifact_pointer(session, deck_id)
            actual_revision = pointer.revision if pointer else None
            actual_digest = pointer.digest if pointer else None
            if expected_publication_revision is None and expected_publication_digest is None:
                expected_publication_revision, expected_publication_digest = actual_revision, actual_digest
            elif (actual_revision, actual_digest) != (expected_publication_revision, expected_publication_digest):
                raise PublicationConflictError()
            row = AuthoredVNextExportJob(
                owner_id=self.owner_id,
                owner_scope=self.owner_scope,
                tenant_id=self.tenant_id,
                deck_id=deck_id,
                deck_revision=deck_revision,
                deck_digest=deck_digest,
                master_id=resolved_master_id,
                master_revision=master_revision,
                master_digest=master_digest,
                spec_digest=spec_digest,
                asset_manifest_digest=asset_manifest_digest,
                font_manifest_digest=font_manifest_digest,
                expected_package_digest=expected_package_digest,
                expected_publication_revision=expected_publication_revision,
                expected_publication_digest=expected_publication_digest,
                timeout_ms=timeout_ms,
                max_attempts=max_attempts,
                operation_fence=operation_fence,
                account_deletion_generation=account_generation,
                request_payload=request_payload,
                request_hash=request_hash,
                idempotency_key_hash=key_hash,
            )
            session.add(row)
            return row

    def get_export_job(self, job_id: uuid.UUID) -> AuthoredVNextExportJob:
        with self._sessions() as session:
            row = session.scalar(
                select(AuthoredVNextExportJob).where(
                    *self._scope(AuthoredVNextExportJob),
                    AuthoredVNextExportJob.id == job_id,
                )
            )
            if row is None:
                raise KeyError("authored_vnext_export_job_not_found")
            return row

    def claim_export_job(self, job_id: uuid.UUID, *, worker_id: str, lease_seconds: int = 60) -> AuthoredVNextExportJob:
        now = _now()
        with self._sessions.begin() as session:
            self._active_account_generation(session)
            row = session.scalar(
                select(AuthoredVNextExportJob).where(
                    *self._scope(AuthoredVNextExportJob), AuthoredVNextExportJob.id == job_id
                )
            )
            if row is None:
                raise KeyError("authored_vnext_export_job_not_found")
            claimable = row.state == "queued" or (
                row.state == "running"
                and row.lease_expires_at is not None
                and row.lease_expires_at.replace(tzinfo=row.lease_expires_at.tzinfo or timezone.utc) <= now
            )
            if not claimable or row.cancel_requested or row.attempt_count >= row.max_attempts:
                raise OperationFenceError()
            changed = session.execute(
                update(AuthoredVNextExportJob)
                .where(
                    AuthoredVNextExportJob.id == row.id,
                    AuthoredVNextExportJob.fence_token == row.fence_token,
                    AuthoredVNextExportJob.state == row.state,
                    AuthoredVNextExportJob.cancel_requested.is_(False),
                    AuthoredVNextExportJob.attempt_count == row.attempt_count,
                    # A heartbeat may renew a running lease after this claimant
                    # read it.  Fence the exact lease snapshot as well as the
                    # state/token so a stale claimant cannot steal live work.
                    AuthoredVNextExportJob.lease_expires_at
                    == row.lease_expires_at,
                )
                .values(
                    state="running",
                    worker_id=worker_id,
                    fence_token=row.fence_token + 1,
                    attempt_count=row.attempt_count + 1,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    failure_code=None,
                    failure_detail=None,
                )
            ).rowcount
            if changed != 1:
                raise OperationFenceError()
            session.flush()
            return session.get(AuthoredVNextExportJob, row.id)

    def heartbeat_export_job(
        self, job_id: uuid.UUID, *, fence_token: int, worker_id: str, lease_seconds: int = 60
    ) -> None:
        with self._sessions.begin() as session:
            changed = session.execute(
                update(AuthoredVNextExportJob)
                .where(
                    *self._scope(AuthoredVNextExportJob),
                    AuthoredVNextExportJob.id == job_id,
                    AuthoredVNextExportJob.state == "running",
                    AuthoredVNextExportJob.fence_token == fence_token,
                    AuthoredVNextExportJob.worker_id == worker_id,
                    AuthoredVNextExportJob.cancel_requested.is_(False),
                )
                .values(lease_expires_at=_now() + timedelta(seconds=lease_seconds))
            ).rowcount
            if changed != 1:
                raise OperationFenceError()

    def request_cancel(
        self,
        job_id: uuid.UUID,
        *,
        expected_operation_fence: int | None = None,
    ) -> AuthoredVNextExportJob:
        with self._sessions.begin() as session:
            row = session.scalar(
                select(AuthoredVNextExportJob).where(
                    *self._scope(AuthoredVNextExportJob), AuthoredVNextExportJob.id == job_id
                )
            )
            if row is None:
                raise KeyError("authored_vnext_export_job_not_found")
            if expected_operation_fence is not None and row.operation_fence != expected_operation_fence:
                raise OperationFenceError()
            if row.state == "queued":
                values = {
                    "state": "cancelled",
                    "cancel_requested": True,
                    "completed_at": _now(),
                }
            elif row.state == "running":
                values = {"state": "cancel_requested", "cancel_requested": True}
            else:
                return row
            changed = session.execute(
                update(AuthoredVNextExportJob)
                .where(
                    AuthoredVNextExportJob.id == row.id,
                    AuthoredVNextExportJob.state == row.state,
                    AuthoredVNextExportJob.operation_fence == row.operation_fence,
                )
                .values(**values, operation_fence=row.operation_fence + 1)
            ).rowcount
            if changed != 1:
                raise OperationFenceError()
            session.flush()
            return session.get(AuthoredVNextExportJob, row.id)

    def retry_export_job(
        self,
        job_id: uuid.UUID,
        *,
        expected_operation_fence: int | None = None,
        reset_attempts: bool = False,
    ) -> AuthoredVNextExportJob:
        with self._sessions.begin() as session:
            row = session.scalar(
                select(AuthoredVNextExportJob).where(
                    *self._scope(AuthoredVNextExportJob), AuthoredVNextExportJob.id == job_id
                )
            )
            if row is None:
                raise KeyError("authored_vnext_export_job_not_found")
            if expected_operation_fence is not None and row.operation_fence != expected_operation_fence:
                raise OperationFenceError()
            if row.state not in {"failed", "cancelled"} or (
                row.attempt_count >= row.max_attempts and not reset_attempts
            ):
                raise OperationFenceError()
            changed = session.execute(
                update(AuthoredVNextExportJob)
                .where(
                    AuthoredVNextExportJob.id == row.id,
                    AuthoredVNextExportJob.state == row.state,
                    AuthoredVNextExportJob.operation_fence == row.operation_fence,
                )
                .values(
                    state="queued",
                    cancel_requested=False,
                    worker_id=None,
                    lease_expires_at=None,
                    completed_at=None,
                    attempt_count=0 if reset_attempts else row.attempt_count,
                    failure_code=None,
                    failure_detail=None,
                    operation_fence=row.operation_fence + 1,
                )
            ).rowcount
            if changed != 1:
                raise OperationFenceError()
            session.flush()
            return session.get(AuthoredVNextExportJob, row.id)

    def fail_export_job(
        self,
        job_id: uuid.UUID,
        *,
        fence_token: int,
        failure_code: str,
        failure_detail: str | None = None,
        cancelled: bool = False,
    ) -> None:
        with self._sessions.begin() as session:
            changed = session.execute(
                update(AuthoredVNextExportJob)
                .where(
                    *self._scope(AuthoredVNextExportJob),
                    AuthoredVNextExportJob.id == job_id,
                    AuthoredVNextExportJob.state.in_(("running", "cancel_requested")),
                    AuthoredVNextExportJob.fence_token == fence_token,
                )
                .values(
                    state="cancelled" if cancelled else "failed",
                    failure_code=failure_code,
                    failure_detail=failure_detail,
                    worker_id=None,
                    lease_expires_at=None,
                    completed_at=_now(),
                )
            ).rowcount
            if changed != 1:
                raise OperationFenceError()

__all__ = ["AuthoredVNextExportJobRepositoryMixin"]

