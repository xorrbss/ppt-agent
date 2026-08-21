"""Stable SQL/CAS repository facade for authored-vNext composition."""

from __future__ import annotations

import asyncio
from functools import wraps
import time
from typing import Any, Callable
import uuid

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from models.sql.authored_vnext_repository import (
    AUTHORED_VNEXT_SQL_TABLES,
    AuthoredVNextEntityHead,
    AuthoredVNextExportArtifactReceipt,
    AuthoredVNextExportJob,
    AuthoredVNextPublicationPointer,
    AuthoredVNextRenderReceipt,
)
from models.sql.user import User
from services.authored_vnext.master.digests import canonical_digest
from utils.db_utils import get_database_url_and_connect_args, to_sync_sqlalchemy_url

from .sql_repository_composition import AuthoredVNextCompositionRepositoryMixin
from .sql_repository_jobs import AuthoredVNextExportJobRepositoryMixin
from .sql_repository_publication import AuthoredVNextPublicationRepositoryMixin
from .sql_repository_types import (
    ExportArtifactInput,
    ExportJobConflictError,
    ExportableDeck,
    OperationFenceError,
    PublicationConflictError,
    SourceDigestMismatchError,
    validate_exportable_deck_payload,
)


SQLITE_LOCK_RETRY_DELAYS = (0.025, 0.075, 0.15)


def _is_sqlite_lock(error: OperationalError, dialect_name: str) -> bool:
    if dialect_name != "sqlite":
        return False
    original = error.orig
    sqlite_error_code = getattr(original, "sqlite_errorcode", None)
    if isinstance(sqlite_error_code, int) and sqlite_error_code & 0xFF in {5, 6}:
        return True
    message = str(original).lower()
    return "database is locked" in message or "database table is locked" in message


def _retry_sqlite_write(method: Callable[..., Any]) -> Callable[..., Any]:
    """Bound SQLite lock recovery without consuming an export job attempt."""

    @wraps(method)
    def wrapped(self: "AuthoredVNextSqlRepository", *args: Any, **kwargs: Any) -> Any:
        for attempt in range(len(SQLITE_LOCK_RETRY_DELAYS) + 1):
            try:
                return method(self, *args, **kwargs)
            except OperationalError as error:
                if (
                    not _is_sqlite_lock(error, self.engine.dialect.name)
                    or attempt == len(SQLITE_LOCK_RETRY_DELAYS)
                ):
                    raise
                time.sleep(SQLITE_LOCK_RETRY_DELAYS[attempt])
        raise AssertionError("unreachable sqlite retry state")

    return wrapped


class AuthoredVNextSqlRepository(
    AuthoredVNextCompositionRepositoryMixin,
    AuthoredVNextExportJobRepositoryMixin,
    AuthoredVNextPublicationRepositoryMixin,
):
    """Owner/tenant scoped synchronous repository with database-native CAS."""

    def __init__(
        self,
        engine: Engine,
        *,
        owner_scope: str,
        tenant_id: str,
        owner_id: uuid.UUID | None = None,
    ) -> None:
        if not owner_scope.strip() or not tenant_id.strip():
            raise ValueError("owner_scope and tenant_id are required")
        if owner_id is None:
            raise ValueError("owner_id is required for authored vNext persistence")
        if owner_scope != str(owner_id):
            raise ValueError("owner_scope must equal the canonical owner_id")
        self.engine = engine
        self.owner_scope = owner_scope
        self.tenant_id = tenant_id
        self.owner_id = owner_id
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_application_database(
        cls,
        *,
        owner_scope: str,
        tenant_id: str,
        owner_id: uuid.UUID | None = None,
    ) -> "AuthoredVNextSqlRepository":
        url, connect_args = get_database_url_and_connect_args()
        sync_url = to_sync_sqlalchemy_url(url)
        if "sqlite" in sync_url:
            connect_args = {"check_same_thread": False, "timeout": 30.0}
        else:
            connect_args = {}
        return cls(
            create_engine(sync_url, connect_args=connect_args),
            owner_scope=owner_scope,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )

    @staticmethod
    def create_schema(engine: Engine) -> None:
        """Test/dev helper; production must use the Alembic migration."""
        for table in AUTHORED_VNEXT_SQL_TABLES:
            table.create(engine, checkfirst=True)

    def _scope(self, model: type[Any]) -> tuple[Any, ...]:
        return (
            model.owner_id == self.owner_id,
            model.owner_scope == self.owner_scope,
            model.tenant_id == self.tenant_id,
        )

    def _head(
        self, session: Session, kind: str, entity_id: str
    ) -> AuthoredVNextEntityHead | None:
        return session.scalar(
            select(AuthoredVNextEntityHead).where(
                *self._scope(AuthoredVNextEntityHead),
                AuthoredVNextEntityHead.entity_kind == kind,
                AuthoredVNextEntityHead.entity_id == entity_id,
            )
        )

    def _active_account_generation(
        self,
        session: Session,
        *,
        expected_generation: int | None = None,
    ) -> int:
        predicates = [
            User.id == self.owner_id,
            User.is_active.is_(True),
            User.deletion_state == "active",
        ]
        if expected_generation is not None:
            predicates.append(User.deletion_generation == expected_generation)
        generation = session.scalar(
            select(User.deletion_generation).where(*predicates).with_for_update()
        )
        if generation is None:
            raise OperationFenceError()
        return int(generation)

    # The mixins keep each mutation in its own transaction. Retrying here means
    # SQLite writer contention is resolved inside the original user operation;
    # it never resets a failed job to queued or increments attempt_count twice.
    @_retry_sqlite_write
    def append_master(self, *args: Any, **kwargs: Any) -> None:
        return super().append_master(*args, **kwargs)

    @_retry_sqlite_write
    def append_deck(self, *args: Any, **kwargs: Any) -> None:
        return super().append_deck(*args, **kwargs)

    @_retry_sqlite_write
    def create_export_job(self, *args: Any, **kwargs: Any):
        return super().create_export_job(*args, **kwargs)

    @_retry_sqlite_write
    def claim_export_job(self, *args: Any, **kwargs: Any):
        return super().claim_export_job(*args, **kwargs)

    @_retry_sqlite_write
    def request_cancel(self, *args: Any, **kwargs: Any):
        return super().request_cancel(*args, **kwargs)

    @_retry_sqlite_write
    def retry_export_job(self, *args: Any, **kwargs: Any):
        return super().retry_export_job(*args, **kwargs)

    @_retry_sqlite_write
    def fail_export_job(self, *args: Any, **kwargs: Any) -> None:
        return super().fail_export_job(*args, **kwargs)

    @_retry_sqlite_write
    def complete_export_job(self, *args: Any, **kwargs: Any):
        return super().complete_export_job(*args, **kwargs)

    @_retry_sqlite_write
    def record_render_receipt(self, *args: Any, **kwargs: Any):
        return super().record_render_receipt(*args, **kwargs)

    def get_active_export_evidence(self, deck_id: str) -> dict[str, Any] | None:
        """Return one atomically joined generation/publication evidence bundle."""

        with self._sessions.begin() as session:
            row = session.execute(
                select(
                    AuthoredVNextPublicationPointer,
                    AuthoredVNextExportArtifactReceipt,
                    AuthoredVNextExportJob,
                )
                .join(
                    AuthoredVNextExportArtifactReceipt,
                    AuthoredVNextExportArtifactReceipt.id
                    == AuthoredVNextPublicationPointer.artifact_id,
                )
                .join(
                    AuthoredVNextExportJob,
                    AuthoredVNextExportJob.id
                    == AuthoredVNextExportArtifactReceipt.job_id,
                )
                .where(
                    *self._scope(AuthoredVNextPublicationPointer),
                    AuthoredVNextPublicationPointer.publication_kind
                    == "deck_artifact",
                    AuthoredVNextPublicationPointer.entity_id == deck_id,
                    *self._scope(AuthoredVNextExportArtifactReceipt),
                    *self._scope(AuthoredVNextExportJob),
                )
            ).one_or_none()
            render_receipts = (
                []
                if row is None
                else list(
                    session.scalars(
                        select(AuthoredVNextRenderReceipt)
                        .where(
                            *self._scope(AuthoredVNextRenderReceipt),
                            AuthoredVNextRenderReceipt.deck_id == deck_id,
                            AuthoredVNextRenderReceipt.deck_revision
                            == row[2].deck_revision,
                            AuthoredVNextRenderReceipt.renderer.like(
                                "powerpoint-com/%"
                            ),
                        )
                        .order_by(
                            AuthoredVNextRenderReceipt.created_at,
                            AuthoredVNextRenderReceipt.slide_id,
                        )
                    )
                )
            )
        if row is None:
            return None
        pointer, receipt, job = row
        if len({item.slide_id for item in render_receipts}) != len(render_receipts):
            raise SourceDigestMismatchError(
                "duplicate PowerPoint render evidence"
            )
        if any(item.input_digest != receipt.artifact_digest for item in render_receipts):
            raise SourceDigestMismatchError(
                "PowerPoint render evidence is stale"
            )
        png_items = [
            {
                "slideId": item.slide_id,
                "slideRevision": item.slide_revision,
                "pngSha256": item.output_digest,
                "storageKey": item.storage_key,
                "renderer": item.renderer,
            }
            for item in render_receipts
        ]
        if (
            job.state != "succeeded"
            or job.deck_id != receipt.deck_id
            or job.deck_revision != receipt.deck_revision
            or pointer.entity_id != job.deck_id
            or pointer.artifact_id != receipt.id
            or pointer.digest != receipt.artifact_digest
            or receipt.readback_digest != receipt.artifact_digest
            or not receipt.readback_verified
            or (
                job.master_digest,
                job.spec_digest,
                job.asset_manifest_digest,
                job.font_manifest_digest,
            )
            != (
                receipt.master_digest,
                receipt.spec_digest,
                receipt.asset_manifest_digest,
                receipt.font_manifest_digest,
            )
        ):
            raise SourceDigestMismatchError(
                "active export evidence pins are inconsistent"
            )
        evidence = {
            "schemaVersion": "authored-vnext-export-evidence/v1",
            "deckId": job.deck_id,
            "deckRevision": job.deck_revision,
            "deckDigest": job.deck_digest,
            "sceneDigest": job.spec_digest,
            "masterRevision": job.master_revision,
            "masterDigest": job.master_digest,
            "publicationRevision": pointer.revision,
            "publicationDigest": pointer.digest,
            "publicationGeneration": pointer.generation,
            "artifactDigest": receipt.artifact_digest,
            "pptxSha256": receipt.artifact_digest,
            "pngSha256": [item["pngSha256"] for item in png_items],
            "pngSlides": png_items,
            "readbackDigest": receipt.readback_digest,
            "packageDigest": receipt.package_digest,
            "assetManifestDigest": receipt.asset_manifest_digest,
            "fontManifestDigest": receipt.font_manifest_digest,
            "jobId": str(job.id),
            "attemptCount": job.attempt_count,
            "readBackVerified": receipt.readback_verified,
        }
        return {**evidence, "evidenceDigest": canonical_digest(evidence)}


class AsyncAuthoredVNextSqlRepository:
    """Async facade for endpoints that must not block the event loop."""

    def __init__(self, repository: AuthoredVNextSqlRepository) -> None:
        self.repository = repository

    async def allocate_master_id(self) -> str:
        return await asyncio.to_thread(self.repository.allocate_master_id)

    async def get_master(self, master_id: str, revision: int | None = None):
        return await asyncio.to_thread(self.repository.get_master, master_id, revision)

    async def get_published_master(self, master_id: str):
        return await asyncio.to_thread(self.repository.get_published_master, master_id)

    async def list_masters(self, *, cursor: str | None, limit: int):
        return await asyncio.to_thread(
            self.repository.list_masters,
            cursor=cursor,
            limit=limit,
        )

    async def append_master(self, record, *, expected_revision, expected_digest) -> None:
        await asyncio.to_thread(
            self.repository.append_master,
            record,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )

    async def get_deck(self, deck_id: str, revision: int | None = None):
        return await asyncio.to_thread(self.repository.get_deck, deck_id, revision)

    async def append_deck(self, deck, *, expected_revision, expected_digest) -> None:
        await asyncio.to_thread(
            self.repository.append_deck,
            deck,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )

    async def create_export_job(self, **kwargs):
        return await asyncio.to_thread(self.repository.create_export_job, **kwargs)

    async def get_export_job(self, job_id: uuid.UUID):
        return await asyncio.to_thread(self.repository.get_export_job, job_id)

    async def claim_export_job(self, job_id: uuid.UUID, **kwargs):
        return await asyncio.to_thread(self.repository.claim_export_job, job_id, **kwargs)

    async def request_cancel(self, job_id: uuid.UUID, **kwargs):
        return await asyncio.to_thread(self.repository.request_cancel, job_id, **kwargs)

    async def retry_export_job(self, job_id: uuid.UUID, **kwargs):
        return await asyncio.to_thread(self.repository.retry_export_job, job_id, **kwargs)

    async def fail_export_job(self, job_id: uuid.UUID, **kwargs) -> None:
        await asyncio.to_thread(self.repository.fail_export_job, job_id, **kwargs)

    async def complete_export_job(self, job_id: uuid.UUID, **kwargs):
        return await asyncio.to_thread(self.repository.complete_export_job, job_id, **kwargs)

    async def record_render_receipt(self, **kwargs):
        return await asyncio.to_thread(
            self.repository.record_render_receipt,
            **kwargs,
        )

    async def get_active_artifact(self, deck_id: str):
        return await asyncio.to_thread(self.repository.get_active_artifact, deck_id)

    async def get_active_export_evidence(self, deck_id: str):
        return await asyncio.to_thread(
            self.repository.get_active_export_evidence,
            deck_id,
        )


__all__ = [
    "AsyncAuthoredVNextSqlRepository",
    "AuthoredVNextSqlRepository",
    "ExportArtifactInput",
    "ExportJobConflictError",
    "ExportableDeck",
    "OperationFenceError",
    "PublicationConflictError",
    "SourceDigestMismatchError",
    "validate_exportable_deck_payload",
]

