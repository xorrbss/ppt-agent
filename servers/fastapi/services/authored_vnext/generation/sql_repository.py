"""SQL-backed generation repository with lease-fenced atomic publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from models.authored_vnext.deck import (
    DeckGenerationSnapshot,
    DeckMutationResult,
    GenerationResult,
    RegenerationResult,
    RollbackResult,
)
from models.authored_vnext.master.operations import MasterRevisionResponse
from models.sql.authored_vnext_generation_operation import (
    AuthoredVNextGenerationOperation,
)
from models.sql.authored_vnext_repository import (
    AuthoredVNextCandidate,
    AuthoredVNextDeckRevision,
    AuthoredVNextEntityHead,
    AuthoredVNextMasterRevision,
    AuthoredVNextPublicationPointer,
)
from models.sql.user import User
from services.authored_vnext.generation.errors import (
    GenerationConflictError,
    GenerationNotFoundError,
)
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
)
from services.authored_vnext.master.digests import canonical_json
from .sql_publication import append_generation_deck


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _payload(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


@dataclass(frozen=True)
class GenerationClaim:
    operation_id: str
    worker_id: str
    fencing_token: int
    lease_expires_at: datetime


class SqlGenerationRepository:
    """Adapts the shared authored SQL schema to the generation contract."""

    def __init__(self, repository: AuthoredVNextSqlRepository) -> None:
        self._base = repository
        self._sessions = sessionmaker(repository.engine, expire_on_commit=False)
        self._claim: GenerationClaim | None = None

    @property
    def owner_scope(self) -> str:
        return self._base.owner_scope

    @property
    def tenant_id(self) -> str:
        return self._base.tenant_id

    def _scope(self, model: type[Any]) -> tuple[Any, Any]:
        return (
            model.owner_scope == self.owner_scope,
            model.tenant_id == self.tenant_id,
        )

    def get_master(self, master_id: str, revision: int | None = None) -> MasterRevisionResponse:
        return self._base.get_master(master_id, revision)

    def get_master_head(self, master_id: str) -> MasterRevisionResponse:
        with self._sessions() as session:
            pointer = session.scalar(
                select(AuthoredVNextPublicationPointer).where(
                    *self._scope(AuthoredVNextPublicationPointer),
                    AuthoredVNextPublicationPointer.publication_kind == "master",
                    AuthoredVNextPublicationPointer.entity_id == master_id,
                )
            )
            if pointer is None:
                raise GenerationNotFoundError("published master not found")
            row = session.scalar(
                select(AuthoredVNextMasterRevision).where(
                    *self._scope(AuthoredVNextMasterRevision),
                    AuthoredVNextMasterRevision.master_id == master_id,
                    AuthoredVNextMasterRevision.revision == pointer.revision,
                    AuthoredVNextMasterRevision.digest == pointer.digest,
                )
            )
            if row is None:
                raise GenerationNotFoundError("published master revision not found")
            return MasterRevisionResponse.model_validate(row.payload)

    def get_deck(self, deck_id: str, revision: int | None = None) -> DeckGenerationSnapshot:
        with self._sessions() as session:
            selected = revision
            if selected is None:
                head = session.scalar(
                    select(AuthoredVNextEntityHead).where(
                        *self._scope(AuthoredVNextEntityHead),
                        AuthoredVNextEntityHead.entity_kind == "deck",
                        AuthoredVNextEntityHead.entity_id == deck_id,
                    )
                )
                selected = head.revision if head else None
            row = None if selected is None else session.scalar(
                select(AuthoredVNextDeckRevision).where(
                    *self._scope(AuthoredVNextDeckRevision),
                    AuthoredVNextDeckRevision.deck_id == deck_id,
                    AuthoredVNextDeckRevision.revision == selected,
                )
            )
            if row is None or row.payload.get("schemaVersion") != "deck-generation-snapshot/v1":
                raise GenerationNotFoundError("generation deck revision not found")
            return DeckGenerationSnapshot.model_validate(row.payload)

    def begin_operation(
        self,
        *,
        operation_id: str,
        deck_id: str,
        kind: str,
        idempotency_key: str,
        request: Any,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> AuthoredVNextGenerationOperation:
        request_payload = _payload(request)
        request_hash = _hash(canonical_json(request_payload))
        idempotency_hash = _hash(idempotency_key)
        try:
            with self._sessions.begin() as session:
                owner = session.scalar(
                    select(User).where(
                        User.id == self._base.owner_id,
                        User.is_active.is_(True),
                        User.deletion_state == "active",
                    )
                )
                if owner is None:
                    raise GenerationConflictError("active account required")
                existing = session.scalar(
                    select(AuthoredVNextGenerationOperation).where(
                        *self._scope(AuthoredVNextGenerationOperation),
                        AuthoredVNextGenerationOperation.kind == kind,
                        AuthoredVNextGenerationOperation.idempotency_key_hash == idempotency_hash,
                    )
                )
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise GenerationConflictError("idempotency payload conflict")
                    return existing
                row = AuthoredVNextGenerationOperation(
                    owner_id=self._base.owner_id,
                    owner_scope=self.owner_scope,
                    tenant_id=self.tenant_id,
                    operation_id=operation_id,
                    deck_id=deck_id,
                    kind=kind,
                    idempotency_key_hash=idempotency_hash,
                    request_hash=request_hash,
                    request_payload=request_payload,
                    expected_revision=expected_revision,
                    expected_digest=expected_digest,
                    account_deletion_generation=owner.deletion_generation,
                )
                session.add(row)
                session.flush()
                return row
        except IntegrityError as error:
            raise GenerationConflictError("operation already exists") from error

    def claim_operation(
        self,
        operation_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> GenerationClaim:
        if not 5 <= lease_seconds <= 900:
            raise ValueError("lease_seconds must be between 5 and 900")
        now = _utcnow()
        expires = now + timedelta(seconds=lease_seconds)
        with self._sessions.begin() as session:
            row = session.scalar(
                select(AuthoredVNextGenerationOperation).where(
                    *self._scope(AuthoredVNextGenerationOperation),
                    AuthoredVNextGenerationOperation.operation_id == operation_id,
                )
            )
            if row is None:
                raise GenerationNotFoundError("operation not found")
            if row.state == "succeeded":
                raise GenerationConflictError("operation already succeeded")
            next_attempt = row.attempt_count + 1
            next_fence = row.fencing_token + 1
            changed = session.execute(
                update(AuthoredVNextGenerationOperation)
                .where(
                    AuthoredVNextGenerationOperation.id == row.id,
                    AuthoredVNextGenerationOperation.version == row.version,
                    or_(
                        AuthoredVNextGenerationOperation.state == "queued",
                        AuthoredVNextGenerationOperation.state == "failed",
                        and_(
                            AuthoredVNextGenerationOperation.state == "running",
                            AuthoredVNextGenerationOperation.lease_expires_at <= now,
                        ),
                    ),
                )
                .values(
                    state="running",
                    worker_id=worker_id,
                    attempt_count=next_attempt,
                    fencing_token=next_fence,
                    lease_expires_at=expires,
                    heartbeat_at=now,
                    version=row.version + 1,
                    error_code=None,
                    error_detail=None,
                )
            ).rowcount
            if changed != 1:
                raise GenerationConflictError("operation lease unavailable")
            claim = GenerationClaim(operation_id, worker_id, next_fence, expires)
            self._claim = claim
            return claim

    def get_operation_row(self, operation_id: str) -> AuthoredVNextGenerationOperation:
        with self._sessions() as session:
            row = session.scalar(
                select(AuthoredVNextGenerationOperation).where(
                    *self._scope(AuthoredVNextGenerationOperation),
                    AuthoredVNextGenerationOperation.operation_id == operation_id,
                )
            )
            if row is None:
                raise GenerationNotFoundError("operation not found")
            return row

    def complete_operation(self, operation_id: str, result_payload: dict[str, Any]) -> None:
        """Complete a lease-fenced operation that does not advance a deck head."""
        claim = self._claim
        if claim is None or claim.operation_id != operation_id:
            raise GenerationConflictError("operation claim required")
        now = _utcnow()
        with self._sessions.begin() as session:
            row = session.scalar(
                select(AuthoredVNextGenerationOperation).where(
                    *self._scope(AuthoredVNextGenerationOperation),
                    AuthoredVNextGenerationOperation.operation_id == operation_id,
                )
            )
            owner = session.scalar(
                select(User).where(
                    User.id == self._base.owner_id,
                    User.is_active.is_(True),
                    User.deletion_state == "active",
                    User.deletion_generation == row.account_deletion_generation,
                )
            ) if row is not None else None
            if (
                row is None
                or owner is None
                or row.state != "running"
                or row.worker_id != claim.worker_id
                or row.fencing_token != claim.fencing_token
                or row.lease_expires_at is None
                or _as_utc(row.lease_expires_at) <= now
            ):
                raise GenerationConflictError("stale operation fence")
            changed = session.execute(
                update(AuthoredVNextGenerationOperation)
                .where(
                    AuthoredVNextGenerationOperation.id == row.id,
                    AuthoredVNextGenerationOperation.version == row.version,
                )
                .values(
                    state="succeeded",
                    result_payload=result_payload,
                    lease_expires_at=None,
                    completed_at=now,
                    version=row.version + 1,
                )
            ).rowcount
            if changed != 1:
                raise GenerationConflictError("operation completion race")

    def get_operation(self, operation_id: str) -> Any:
        row = self.get_operation_row(operation_id)
        if row.state != "succeeded" or row.result_payload is None:
            raise GenerationConflictError("operation has no durable result")
        schema = row.result_payload.get("schemaVersion")
        model = {
            "generation-result/v1": GenerationResult,
            "regeneration-result/v1": RegenerationResult,
            "rollback-result/v1": RollbackResult,
            "deck-mutation-result/v1": DeckMutationResult,
        }.get(schema)
        if model is None:
            raise GenerationConflictError("operation result schema is unsupported")
        return model.model_validate(row.result_payload)

    def publish_result(
        self,
        deck: DeckGenerationSnapshot,
        result: Any,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        claim = self._claim
        if claim is None or claim.operation_id != result.operation_id:
            raise GenerationConflictError("operation claim required")
        now = _utcnow()
        try:
            with self._sessions.begin() as session:
                operation = session.scalar(
                    select(AuthoredVNextGenerationOperation).where(
                        *self._scope(AuthoredVNextGenerationOperation),
                        AuthoredVNextGenerationOperation.operation_id == claim.operation_id,
                    )
                )
                owner = session.scalar(
                    select(User).where(
                        User.id == self._base.owner_id,
                        User.is_active.is_(True),
                        User.deletion_state == "active",
                        User.deletion_generation == operation.account_deletion_generation,
                    )
                ) if operation is not None else None
                if (
                    operation is None
                    or owner is None
                    or operation.state != "running"
                    or operation.worker_id != claim.worker_id
                    or operation.fencing_token != claim.fencing_token
                    or operation.lease_expires_at is None
                    or _as_utc(operation.lease_expires_at) <= now
                ):
                    raise GenerationConflictError("stale operation fence")
                append_generation_deck(
                    session,
                    owner_id=self._base.owner_id,
                    owner_scope=self.owner_scope,
                    tenant_id=self.tenant_id,
                    deck=deck,
                    expected_revision=expected_revision,
                    expected_digest=expected_digest,
                )
                changed = session.execute(
                    update(AuthoredVNextGenerationOperation)
                    .where(
                        AuthoredVNextGenerationOperation.id == operation.id,
                        AuthoredVNextGenerationOperation.version == operation.version,
                        AuthoredVNextGenerationOperation.fencing_token == claim.fencing_token,
                    )
                    .values(
                        state="succeeded",
                        result_payload=_payload(result),
                        checkpoint_payload=_payload(result.checkpoint) if hasattr(result, "checkpoint") else None,
                        result_revision=deck.revision,
                        result_digest=deck.digest,
                        lease_expires_at=None,
                        completed_at=now,
                        version=operation.version + 1,
                    )
                ).rowcount
                if changed != 1:
                    raise GenerationConflictError("late operation publication rejected")
        except IntegrityError as error:
            raise GenerationConflictError("deck publication race") from error

    def fail_operation(self, operation_id: str, error: Exception) -> None:
        claim = self._claim
        if claim is None or claim.operation_id != operation_id:
            return
        with self._sessions.begin() as session:
            session.execute(
                update(AuthoredVNextGenerationOperation)
                .where(
                    *self._scope(AuthoredVNextGenerationOperation),
                    AuthoredVNextGenerationOperation.operation_id == operation_id,
                    AuthoredVNextGenerationOperation.state == "running",
                    AuthoredVNextGenerationOperation.worker_id == claim.worker_id,
                    AuthoredVNextGenerationOperation.fencing_token == claim.fencing_token,
                )
                .values(
                    state="failed",
                    error_code=getattr(error, "code", "authored_vnext_generation_failed"),
                    error_detail=str(error)[:2000],
                    lease_expires_at=None,
                    completed_at=_utcnow(),
                    version=AuthoredVNextGenerationOperation.version + 1,
                )
            )

    def save_candidate(self, candidate_digest: str, payload: dict[str, object]) -> None:
        self._base.save_candidate(candidate_digest, payload)

    def get_candidate(self, candidate_digest: str) -> dict[str, Any]:
        with self._sessions() as session:
            row = session.scalar(
                select(AuthoredVNextCandidate).where(
                    *self._scope(AuthoredVNextCandidate),
                    AuthoredVNextCandidate.candidate_digest == candidate_digest,
                )
            )
            if row is None:
                raise GenerationNotFoundError("candidate not found")
            return dict(row.payload)
