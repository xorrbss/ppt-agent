"""SQL-backed generation repository with lease-fenced atomic publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import time
from typing import Any, Callable

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from models.authored_vnext.deck import (
    AuthoredCompositionPin,
    DeckGenerationSnapshot,
    DeckMutationResult,
    GenerationResult,
    RegenerationResult,
    RollbackResult,
    SceneMutationResult,
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
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PresentationModel,
)
from models.sql.slide import SlideModel
from services.authored_vnext.generation.errors import (
    GenerationConflictError,
    GenerationNotFoundError,
)
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
)
from services.authored_vnext.master.digests import canonical_json
from services.authored_vnext.master.errors import DeckAlreadyExistsError
from .composition import remember_composition_pin
from .sql_publication import append_generation_deck


SQLITE_LOCK_RETRY_DELAYS = (0.025, 0.075, 0.15)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    """Retry only transient SQLite writer contention inside one API attempt."""

    @wraps(method)
    def wrapped(self: "SqlGenerationRepository", *args: Any, **kwargs: Any) -> Any:
        for attempt in range(len(SQLITE_LOCK_RETRY_DELAYS) + 1):
            try:
                return method(self, *args, **kwargs)
            except OperationalError as error:
                if (
                    not _is_sqlite_lock(error, self._base.engine.dialect.name)
                    or attempt == len(SQLITE_LOCK_RETRY_DELAYS)
                ):
                    raise
                time.sleep(SQLITE_LOCK_RETRY_DELAYS[attempt])
        raise AssertionError("unreachable sqlite retry state")

    return wrapped


def _payload(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


def _payload_composition(payload: dict[str, Any]) -> AuthoredCompositionPin | None:
    value = payload.get("composition")
    return None if value is None else AuthoredCompositionPin.model_validate(value)


def _assert_generation_composition(
    request_payload: dict[str, Any],
    deck: DeckGenerationSnapshot,
    result: Any,
) -> None:
    """Fail closed before persisting a generation result with a changed pin."""

    request_pin = _payload_composition(request_payload)
    if isinstance(result, GenerationResult):
        pins = (
            request_pin,
            deck.composition,
            result.composition,
            result.checkpoint.composition,
        )
        if any(pin is not None for pin in pins):
            expected = pins[0]
            if any(pin is None or pin != expected for pin in pins):
                raise GenerationConflictError(
                    "generation composition changed before publication"
                )
            remember_composition_pin(result.composition)
        if deck.scene is None:
            raise GenerationConflictError("new generation scene is missing")
        if result.checkpoint.scene_digest != deck.scene.scene_digest:
            raise GenerationConflictError(
                "generation scene changed before publication"
            )
        if (
            result.composition is not None
            and deck.scene.composition_digest != result.composition.digest
        ):
            raise GenerationConflictError(
                "generation scene composition changed before publication"
            )
    else:
        remember_composition_pin(deck.composition)


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
            deck = DeckGenerationSnapshot.model_validate(row.payload)
            remember_composition_pin(deck.composition)
            return deck

    @_retry_sqlite_write
    def append_imported_deck(self, deck: DeckGenerationSnapshot) -> None:
        """Persist a sealed product-path deck that was authored outside the planner.

        The normal Authored generator owns its semantic Scene, while this
        repository owns the durable deck CAS log used by Workbench and export.
        Imported publication is intentionally creation-only: a retry may read
        the byte-identical revision, but it can never overwrite an existing
        revision or advance a head without the normal mutation operation fence.
        """

        try:
            self._base.append_deck(
                deck,
                expected_revision=None,
                expected_digest=None,
            )
        except DeckAlreadyExistsError:
            current = self.get_deck(deck.deck_id, deck.revision)
            if current != deck:
                raise

    @_retry_sqlite_write
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
        remember_composition_pin(_payload_composition(request_payload))
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
            with self._sessions() as session:
                existing = session.scalar(
                    select(AuthoredVNextGenerationOperation).where(
                        *self._scope(AuthoredVNextGenerationOperation),
                        AuthoredVNextGenerationOperation.kind == kind,
                        AuthoredVNextGenerationOperation.idempotency_key_hash
                        == idempotency_hash,
                    )
                )
                if existing is not None and existing.request_hash == request_hash:
                    return existing
            raise GenerationConflictError("operation already exists") from error

    @_retry_sqlite_write
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
        # Do not expose a claim whose transaction failed during context-manager
        # commit. This is what keeps an internal lock retry inside attempt 1.
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

    @_retry_sqlite_write
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
            "scene-mutation-result/v1": SceneMutationResult,
        }.get(schema)
        if model is None:
            raise GenerationConflictError("operation result schema is unsupported")
        result = model.model_validate(row.result_payload)
        if isinstance(result, GenerationResult):
            remember_composition_pin(result.composition)
        elif hasattr(result, "deck"):
            remember_composition_pin(result.deck.composition)
        return result

    @_retry_sqlite_write
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
                _assert_generation_composition(operation.request_payload, deck, result)
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

    @_retry_sqlite_write
    def publish_legacy_master_apply(
        self,
        deck: DeckGenerationSnapshot,
        result: Any,
        *,
        presentation_id,
        expected_presentation_revision: int,
        expected_revision: int,
        expected_digest: str,
        authored_projection: dict[str, Any],
        slide_updates: dict[int, dict[str, Any]],
    ) -> None:
        """Atomically advance vNext and its legacy editor projection.

        The preview images may already exist as immutable content-addressed
        blobs, but neither deck head nor Presentation/Slide rows move unless
        every owner/revision/count CAS predicate succeeds in this transaction.
        """

        claim = self._claim
        if claim is None or claim.operation_id != result.operation_id:
            raise GenerationConflictError("operation claim required")
        now = _utcnow()
        try:
            with self._sessions.begin() as session:
                operation = session.scalar(
                    select(AuthoredVNextGenerationOperation).where(
                        *self._scope(AuthoredVNextGenerationOperation),
                        AuthoredVNextGenerationOperation.operation_id
                        == claim.operation_id,
                    )
                )
                owner = (
                    session.scalar(
                        select(User).where(
                            User.id == self._base.owner_id,
                            User.is_active.is_(True),
                            User.deletion_state == "active",
                            User.deletion_generation
                            == operation.account_deletion_generation,
                        )
                    )
                    if operation is not None
                    else None
                )
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

                _assert_generation_composition(operation.request_payload, deck, result)

                presentation = session.scalar(
                    select(PresentationModel).where(
                        PresentationModel.id == presentation_id,
                        PresentationModel.owner_id == self._base.owner_id,
                        PresentationModel.lifecycle_status
                        == PRESENTATION_LIFECYCLE_PUBLISHED,
                        PresentationModel.revision == expected_presentation_revision,
                    )
                )
                if presentation is None:
                    raise GenerationConflictError("legacy presentation CAS failed")
                slides = list(
                    session.scalars(
                        select(SlideModel)
                        .where(
                            SlideModel.presentation == presentation_id,
                            SlideModel.owner_id == self._base.owner_id,
                        )
                        .order_by(SlideModel.index, SlideModel.id)
                    )
                )
                if not slide_updates or any(
                    index < 0 or index >= len(slides) for index in slide_updates
                ):
                    raise GenerationConflictError("legacy slide target changed")

                append_generation_deck(
                    session,
                    owner_id=self._base.owner_id,
                    owner_scope=self.owner_scope,
                    tenant_id=self.tenant_id,
                    deck=deck,
                    expected_revision=expected_revision,
                    expected_digest=expected_digest,
                )
                theme = dict(presentation.theme or {})
                theme["authored_vnext"] = authored_projection
                presentation.theme = theme
                presentation.revision = expected_presentation_revision + 1
                presentation.updated_at = now
                for index, values in slide_updates.items():
                    row = slides[index]
                    row.layout = values["layout"]
                    row.layout_group = values["layout_group"]
                    row.content = values["content"]
                    row.html_content = values["html_content"]
                    row.properties = values["properties"]
                changed = session.execute(
                    update(AuthoredVNextGenerationOperation)
                    .where(
                        AuthoredVNextGenerationOperation.id == operation.id,
                        AuthoredVNextGenerationOperation.version == operation.version,
                        AuthoredVNextGenerationOperation.fencing_token
                        == claim.fencing_token,
                    )
                    .values(
                        state="succeeded",
                        result_payload=_payload(result),
                        checkpoint_payload=(
                            _payload(result.checkpoint)
                            if hasattr(result, "checkpoint")
                            else None
                        ),
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
            raise GenerationConflictError("legacy master apply publication race") from error

    @_retry_sqlite_write
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

    @_retry_sqlite_write
    def save_candidate(self, candidate_digest: str, payload: dict[str, object]) -> None:
        """Persist one idempotent candidate without consuming an outer job retry.

        Authored generation stores AI-zone resolution before it publishes the
        deck. A short-lived SQLite writer (for example, the preceding Workbench
        edit) must not turn that first operation attempt back into ``queued``.
        Each retry opens a fresh bounded transaction in the base repository;
        digest+payload equality makes an ambiguous prior commit safe to replay.
        """

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
