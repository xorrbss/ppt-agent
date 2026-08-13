"""Artifact publication, read-back, and rollback persistence responsibilities."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.sql.authored_vnext_repository import (
    AuthoredVNextExportArtifactReceipt,
    AuthoredVNextExportJob,
    AuthoredVNextMasterRevision,
    AuthoredVNextPublicationPointer,
)

from .sql_repository_types import (
    ExportArtifactInput,
    OperationFenceError,
    PublicationConflictError,
    SourceDigestMismatchError,
    _now,
    _require_digest,
)


class AuthoredVNextPublicationRepositoryMixin:
    def _artifact_pointer(self, session: Session, deck_id: str) -> AuthoredVNextPublicationPointer | None:
        return session.scalar(
            select(AuthoredVNextPublicationPointer).where(
                *self._scope(AuthoredVNextPublicationPointer),
                AuthoredVNextPublicationPointer.publication_kind == "deck_artifact",
                AuthoredVNextPublicationPointer.entity_id == deck_id,
            )
        )

    def complete_export_job(
        self,
        job_id: uuid.UUID,
        *,
        fence_token: int,
        artifact: ExportArtifactInput,
        operation_id: uuid.UUID | None = None,
    ) -> AuthoredVNextExportArtifactReceipt:
        try:
            return self._complete_export_job(
                job_id,
                fence_token=fence_token,
                artifact=artifact,
                operation_id=operation_id,
            )
        except IntegrityError as exc:
            # Most importantly, a concurrent first publication loses its unique
            # pointer insert and the receipt/job transition are rolled back.
            raise PublicationConflictError() from exc

    def _complete_export_job(
        self,
        job_id: uuid.UUID,
        *,
        fence_token: int,
        artifact: ExportArtifactInput,
        operation_id: uuid.UUID | None = None,
    ) -> AuthoredVNextExportArtifactReceipt:
        for field, digest in (
            ("artifact_digest", artifact.artifact_digest),
            ("readback_digest", artifact.readback_digest),
            ("package_digest", artifact.package_digest),
            ("master_digest", artifact.master_digest),
            ("spec_digest", artifact.spec_digest),
            ("asset_manifest_digest", artifact.asset_manifest_digest),
            ("font_manifest_digest", artifact.font_manifest_digest),
        ):
            _require_digest(digest, field)
        if artifact.size_bytes < 0 or not artifact.storage_key.strip():
            raise SourceDigestMismatchError("artifact receipt is incomplete")
        if artifact.artifact_digest != artifact.readback_digest:
            raise SourceDigestMismatchError("artifact read-back digest mismatch")
        with self._sessions.begin() as session:
            job = session.scalar(
                select(AuthoredVNextExportJob).where(
                    *self._scope(AuthoredVNextExportJob), AuthoredVNextExportJob.id == job_id
                )
            )
            if job is None:
                raise KeyError("authored_vnext_export_job_not_found")
            self._active_account_generation(
                session,
                expected_generation=job.account_deletion_generation,
            )
            if job.state != "running" or job.fence_token != fence_token or job.cancel_requested:
                raise OperationFenceError()
            actual = (
                artifact.master_digest,
                artifact.spec_digest,
                artifact.asset_manifest_digest,
                artifact.font_manifest_digest,
            )
            expected = (job.master_digest, job.spec_digest, job.asset_manifest_digest, job.font_manifest_digest)
            if actual != expected or (
                job.expected_package_digest and artifact.package_digest != job.expected_package_digest
            ):
                raise SourceDigestMismatchError()
            deck_head = self._head(session, "deck", job.deck_id)
            if deck_head is None or (deck_head.revision, deck_head.digest) != (job.deck_revision, job.deck_digest):
                raise SourceDigestMismatchError("deck changed during export")
            pointer = self._artifact_pointer(session, job.deck_id)
            current = (pointer.revision, pointer.digest) if pointer else (None, None)
            expected_publication = (job.expected_publication_revision, job.expected_publication_digest)
            if current != expected_publication:
                raise PublicationConflictError()
            receipt = AuthoredVNextExportArtifactReceipt(
                owner_id=self.owner_id,
                job_id=job.id,
                owner_scope=self.owner_scope,
                tenant_id=self.tenant_id,
                deck_id=job.deck_id,
                deck_revision=job.deck_revision,
                storage_key=artifact.storage_key,
                artifact_digest=artifact.artifact_digest,
                readback_digest=artifact.readback_digest,
                package_digest=artifact.package_digest,
                size_bytes=artifact.size_bytes,
                master_digest=artifact.master_digest,
                spec_digest=artifact.spec_digest,
                asset_manifest_digest=artifact.asset_manifest_digest,
                font_manifest_digest=artifact.font_manifest_digest,
                readback_verified=True,
            )
            session.add(receipt)
            session.flush()
            next_revision = 1 if pointer is None else pointer.revision + 1
            if pointer is None:
                session.add(
                    AuthoredVNextPublicationPointer(
                        owner_id=self.owner_id,
                        owner_scope=self.owner_scope,
                        tenant_id=self.tenant_id,
                        publication_kind="deck_artifact",
                        entity_id=job.deck_id,
                        revision=next_revision,
                        digest=artifact.artifact_digest,
                        artifact_id=receipt.id,
                        generation=1,
                        operation_id=operation_id or uuid.uuid4(),
                    )
                )
            else:
                changed = session.execute(
                    update(AuthoredVNextPublicationPointer)
                    .where(
                        AuthoredVNextPublicationPointer.id == pointer.id,
                        AuthoredVNextPublicationPointer.generation == pointer.generation,
                        AuthoredVNextPublicationPointer.digest == job.expected_publication_digest,
                    )
                    .values(
                        revision=next_revision,
                        digest=artifact.artifact_digest,
                        artifact_id=receipt.id,
                        generation=pointer.generation + 1,
                        operation_id=operation_id or uuid.uuid4(),
                    )
                ).rowcount
                if changed != 1:
                    raise PublicationConflictError()
            changed = session.execute(
                update(AuthoredVNextExportJob)
                .where(
                    AuthoredVNextExportJob.id == job.id,
                    AuthoredVNextExportJob.state == "running",
                    AuthoredVNextExportJob.fence_token == fence_token,
                )
                .values(state="succeeded", worker_id=None, lease_expires_at=None, completed_at=_now())
            ).rowcount
            if changed != 1:
                raise OperationFenceError()
            return receipt

    def get_active_artifact(
        self, deck_id: str
    ) -> tuple[AuthoredVNextPublicationPointer, AuthoredVNextExportArtifactReceipt] | None:
        with self._sessions() as session:
            pointer = self._artifact_pointer(session, deck_id)
            if pointer is None or pointer.artifact_id is None:
                return None
            receipt = session.scalar(
                select(AuthoredVNextExportArtifactReceipt).where(
                    *self._scope(AuthoredVNextExportArtifactReceipt),
                    AuthoredVNextExportArtifactReceipt.id == pointer.artifact_id,
                )
            )
            if receipt is None or receipt.artifact_digest != pointer.digest:
                raise SourceDigestMismatchError("publication receipt is missing or corrupt")
            session.expunge(pointer)
            session.expunge(receipt)
            return pointer, receipt

    def get_artifact_receipt(
        self,
        *,
        deck_id: str,
        artifact_id: uuid.UUID,
    ) -> AuthoredVNextExportArtifactReceipt:
        """Return an owner-scoped rollback target before publication CAS."""

        with self._sessions() as session:
            receipt = session.scalar(
                select(AuthoredVNextExportArtifactReceipt).where(
                    *self._scope(AuthoredVNextExportArtifactReceipt),
                    AuthoredVNextExportArtifactReceipt.id == artifact_id,
                    AuthoredVNextExportArtifactReceipt.deck_id == deck_id,
                )
            )
            if receipt is None:
                raise KeyError("authored_vnext_artifact_not_found")
            session.expunge(receipt)
            return receipt

    def rollback_publication(
        self,
        *,
        deck_id: str,
        target_artifact_id: uuid.UUID,
        expected_generation: int,
        expected_digest: str,
        operation_id: uuid.UUID | None = None,
    ) -> AuthoredVNextPublicationPointer:
        with self._sessions.begin() as session:
            pointer = self._artifact_pointer(session, deck_id)
            target = session.scalar(
                select(AuthoredVNextExportArtifactReceipt).where(
                    *self._scope(AuthoredVNextExportArtifactReceipt),
                    AuthoredVNextExportArtifactReceipt.id == target_artifact_id,
                    AuthoredVNextExportArtifactReceipt.deck_id == deck_id,
                )
            )
            if pointer is None or target is None:
                raise PublicationConflictError()
            changed = session.execute(
                update(AuthoredVNextPublicationPointer)
                .where(
                    AuthoredVNextPublicationPointer.id == pointer.id,
                    AuthoredVNextPublicationPointer.generation == expected_generation,
                    AuthoredVNextPublicationPointer.digest == expected_digest,
                )
                .values(
                    revision=pointer.revision + 1,
                    digest=target.artifact_digest,
                    artifact_id=target.id,
                    generation=pointer.generation + 1,
                    operation_id=operation_id or uuid.uuid4(),
                )
            ).rowcount
            if changed != 1:
                raise PublicationConflictError()
            session.flush()
            return session.get(AuthoredVNextPublicationPointer, pointer.id)

    def rollback_master_publication(
        self,
        *,
        master_id: str,
        target_revision: int,
        target_digest: str,
        expected_generation: int,
        expected_revision: int,
        expected_digest: str,
        operation_id: uuid.UUID | None = None,
    ) -> AuthoredVNextPublicationPointer:
        """Move the published-master pointer to an immutable prior revision."""
        with self._sessions.begin() as session:
            target = session.scalar(
                select(AuthoredVNextMasterRevision).where(
                    *self._scope(AuthoredVNextMasterRevision),
                    AuthoredVNextMasterRevision.master_id == master_id,
                    AuthoredVNextMasterRevision.revision == target_revision,
                    AuthoredVNextMasterRevision.digest == target_digest,
                    AuthoredVNextMasterRevision.status == "published",
                )
            )
            pointer = session.scalar(
                select(AuthoredVNextPublicationPointer).where(
                    *self._scope(AuthoredVNextPublicationPointer),
                    AuthoredVNextPublicationPointer.publication_kind == "master",
                    AuthoredVNextPublicationPointer.entity_id == master_id,
                )
            )
            if target is None or pointer is None:
                raise PublicationConflictError()
            changed = session.execute(
                update(AuthoredVNextPublicationPointer)
                .where(
                    AuthoredVNextPublicationPointer.id == pointer.id,
                    AuthoredVNextPublicationPointer.generation == expected_generation,
                    AuthoredVNextPublicationPointer.revision == expected_revision,
                    AuthoredVNextPublicationPointer.digest == expected_digest,
                )
                .values(
                    revision=target_revision,
                    digest=target_digest,
                    generation=pointer.generation + 1,
                    operation_id=operation_id or uuid.uuid4(),
                )
            ).rowcount
            if changed != 1:
                raise PublicationConflictError()
            session.flush()
            return session.get(AuthoredVNextPublicationPointer, pointer.id)

__all__ = ["AuthoredVNextPublicationRepositoryMixin"]

