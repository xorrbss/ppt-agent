"""Stable SQL/CAS repository facade for authored-vNext composition."""

from __future__ import annotations

import asyncio
from typing import Any
import uuid

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from models.sql.authored_vnext_repository import (
    AUTHORED_VNEXT_SQL_TABLES,
    AuthoredVNextEntityHead,
)
from models.sql.user import User
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


class AsyncAuthoredVNextSqlRepository:
    """Async facade for endpoints that must not block the event loop."""

    def __init__(self, repository: AuthoredVNextSqlRepository) -> None:
        self.repository = repository

    async def get_master(self, master_id: str, revision: int | None = None):
        return await asyncio.to_thread(self.repository.get_master, master_id, revision)

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

    async def get_active_artifact(self, deck_id: str):
        return await asyncio.to_thread(self.repository.get_active_artifact, deck_id)


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

