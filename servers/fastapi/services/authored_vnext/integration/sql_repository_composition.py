"""Master, deck, candidate, quality, and render persistence responsibilities."""

from __future__ import annotations

from datetime import timezone
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.authored_vnext.master.operations import (
    MasterCatalogPage,
    MasterRevisionResponse,
)
from models.authored_vnext.master.slide import DeckSnapshot
from models.sql.authored_vnext_repository import (
    AuthoredVNextCandidate,
    AuthoredVNextDeckRevision,
    AuthoredVNextEntityHead,
    AuthoredVNextMasterRevision,
    AuthoredVNextPublicationPointer,
    AuthoredVNextQualityDecision,
    AuthoredVNextRenderReceipt,
    AuthoredVNextSlideRevision,
)
from services.authored_vnext.master.errors import (
    DeckAlreadyExistsError,
    DeckDigestConflictError,
    DeckNotFoundError,
    DeckRevisionConflictError,
    MasterDigestConflictError,
    MasterNotFoundError,
    MasterRevisionConflictError,
)
from services.authored_vnext.master.repository import (
    decode_catalog_cursor,
    encode_catalog_cursor,
    master_catalog_item,
)

from .sql_repository_types import (
    ExportJobConflictError,
    ExportableDeck,
    _hash,
    _json,
    _require_digest,
    validate_exportable_deck_payload,
)


class AuthoredVNextCompositionRepositoryMixin:
    def allocate_master_id(self) -> str:
        with self._sessions() as session:
            while True:
                master_id = f"master:{uuid.uuid4().hex}"
                exists = session.scalar(
                    select(AuthoredVNextMasterRevision.id).where(
                        *self._scope(AuthoredVNextMasterRevision),
                        AuthoredVNextMasterRevision.master_id == master_id,
                    )
                )
                if exists is None:
                    return master_id

    def get_master(self, master_id: str, revision: int | None = None) -> MasterRevisionResponse:
        with self._sessions() as session:
            selected = revision
            if selected is None:
                head = self._head(session, "master", master_id)
                selected = head.revision if head else None
            if selected is None:
                raise MasterNotFoundError()
            row = session.scalar(
                select(AuthoredVNextMasterRevision).where(
                    *self._scope(AuthoredVNextMasterRevision),
                    AuthoredVNextMasterRevision.master_id == master_id,
                    AuthoredVNextMasterRevision.revision == selected,
                )
            )
            if row is None:
                raise MasterNotFoundError()
            return MasterRevisionResponse.model_validate(row.payload)

    def get_published_master(self, master_id: str) -> MasterRevisionResponse:
        with self._sessions() as session:
            pointer = session.scalar(
                select(AuthoredVNextPublicationPointer).where(
                    *self._scope(AuthoredVNextPublicationPointer),
                    AuthoredVNextPublicationPointer.publication_kind == "master",
                    AuthoredVNextPublicationPointer.entity_id == master_id,
                )
            )
            if pointer is None:
                raise MasterNotFoundError()
            row = session.scalar(
                select(AuthoredVNextMasterRevision).where(
                    *self._scope(AuthoredVNextMasterRevision),
                    AuthoredVNextMasterRevision.master_id == master_id,
                    AuthoredVNextMasterRevision.revision == pointer.revision,
                    AuthoredVNextMasterRevision.digest == pointer.digest,
                    AuthoredVNextMasterRevision.status == "published",
                )
            )
            if row is None:
                raise MasterNotFoundError()
            return MasterRevisionResponse.model_validate(row.payload)

    def list_masters(
        self, *, cursor: str | None, limit: int
    ) -> MasterCatalogPage:
        after = decode_catalog_cursor(cursor)
        with self._sessions() as session:
            predicates = [
                *self._scope(AuthoredVNextEntityHead),
                AuthoredVNextEntityHead.entity_kind == "master",
                AuthoredVNextEntityHead.entity_id.not_like(
                    "master:internal-authored-%"
                ),
            ]
            if after is not None:
                predicates.append(AuthoredVNextEntityHead.entity_id > after)
            heads = list(
                session.scalars(
                    select(AuthoredVNextEntityHead)
                    .where(*predicates)
                    .order_by(AuthoredVNextEntityHead.entity_id.asc())
                    .limit(limit + 1)
                )
            )
            has_more = len(heads) > limit
            heads = heads[:limit]
            items = []
            for head in heads:
                pointer = session.scalar(
                    select(AuthoredVNextPublicationPointer).where(
                        *self._scope(AuthoredVNextPublicationPointer),
                        AuthoredVNextPublicationPointer.publication_kind == "master",
                        AuthoredVNextPublicationPointer.entity_id == head.entity_id,
                    )
                )
                selected_revision = pointer.revision if pointer else head.revision
                selected_digest = pointer.digest if pointer else head.digest
                row = session.scalar(
                    select(AuthoredVNextMasterRevision).where(
                        *self._scope(AuthoredVNextMasterRevision),
                        AuthoredVNextMasterRevision.master_id == head.entity_id,
                        AuthoredVNextMasterRevision.revision == selected_revision,
                        AuthoredVNextMasterRevision.digest == selected_digest,
                    )
                )
                if row is None:
                    continue
                display = MasterRevisionResponse.model_validate(row.payload)
                published = display if pointer is not None else None
                updated_time = head.updated_at
                if updated_time.tzinfo is None:
                    updated_time = updated_time.replace(tzinfo=timezone.utc)
                items.append(
                    master_catalog_item(
                        display,
                        published=published,
                        has_draft=pointer is None or head.revision > pointer.revision,
                        updated_time=updated_time,
                    )
                )
            return MasterCatalogPage(
                items=items,
                next_cursor=(
                    encode_catalog_cursor(heads[-1].entity_id)
                    if has_more and heads
                    else None
                ),
            )

    def append_master(
        self,
        record: MasterRevisionResponse,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        try:
            with self._sessions.begin() as session:
                self._active_account_generation(session)
                head = self._head(session, "master", record.master_id)
                if head is None:
                    if expected_revision is not None:
                        raise MasterRevisionConflictError()
                    if expected_digest is not None:
                        raise MasterDigestConflictError()
                    if record.revision != 1:
                        raise MasterRevisionConflictError()
                else:
                    if expected_revision is None or head.revision != expected_revision:
                        raise MasterRevisionConflictError()
                    if expected_digest is None or head.digest != expected_digest:
                        raise MasterDigestConflictError()
                    if record.revision != head.revision + 1:
                        raise MasterRevisionConflictError()
                session.add(
                    AuthoredVNextMasterRevision(
                        owner_id=self.owner_id,
                        owner_scope=self.owner_scope,
                        tenant_id=self.tenant_id,
                        master_id=record.master_id,
                        revision=record.revision,
                        digest=record.digest,
                        status=record.status,
                        payload=_json(record),
                    )
                )
                session.flush()
                if head is None:
                    session.add(
                        AuthoredVNextEntityHead(
                            owner_id=self.owner_id,
                            owner_scope=self.owner_scope,
                            tenant_id=self.tenant_id,
                            entity_kind="master",
                            entity_id=record.master_id,
                            revision=record.revision,
                            digest=record.digest,
                            generation=1,
                        )
                    )
                else:
                    changed = session.execute(
                        update(AuthoredVNextEntityHead)
                        .where(
                            AuthoredVNextEntityHead.id == head.id,
                            AuthoredVNextEntityHead.revision == expected_revision,
                            AuthoredVNextEntityHead.digest == expected_digest,
                        )
                        .values(revision=record.revision, digest=record.digest, generation=head.generation + 1)
                    ).rowcount
                    if changed != 1:
                        raise MasterRevisionConflictError()
                if record.status == "published":
                    self._publish_master(session, record)
        except IntegrityError as exc:
            raise MasterRevisionConflictError() from exc

    def _publish_master(self, session: Session, record: MasterRevisionResponse) -> None:
        pointer = session.scalar(
            select(AuthoredVNextPublicationPointer).where(
                *self._scope(AuthoredVNextPublicationPointer),
                AuthoredVNextPublicationPointer.publication_kind == "master",
                AuthoredVNextPublicationPointer.entity_id == record.master_id,
            )
        )
        if pointer is None:
            session.add(
                AuthoredVNextPublicationPointer(
                    owner_id=self.owner_id,
                    owner_scope=self.owner_scope,
                    tenant_id=self.tenant_id,
                    publication_kind="master",
                    entity_id=record.master_id,
                    revision=record.revision,
                    digest=record.digest,
                    generation=1,
                )
            )
        else:
            session.execute(
                update(AuthoredVNextPublicationPointer)
                .where(
                    AuthoredVNextPublicationPointer.id == pointer.id,
                    AuthoredVNextPublicationPointer.generation == pointer.generation,
                )
                .values(revision=record.revision, digest=record.digest, generation=pointer.generation + 1)
            )

    def get_deck(
        self, deck_id: str, revision: int | None = None
    ) -> ExportableDeck:
        with self._sessions() as session:
            selected = revision
            if selected is None:
                head = self._head(session, "deck", deck_id)
                selected = head.revision if head else None
            if selected is None:
                raise DeckNotFoundError()
            row = session.scalar(
                select(AuthoredVNextDeckRevision).where(
                    *self._scope(AuthoredVNextDeckRevision),
                    AuthoredVNextDeckRevision.deck_id == deck_id,
                    AuthoredVNextDeckRevision.revision == selected,
                )
            )
            if row is None:
                raise DeckNotFoundError()
            return validate_exportable_deck_payload(row.payload)

    def append_deck(
        self,
        deck: ExportableDeck,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        try:
            with self._sessions.begin() as session:
                self._active_account_generation(session)
                head = self._head(session, "deck", deck.deck_id)
                if head is None:
                    if expected_revision is not None or expected_digest is not None:
                        raise DeckNotFoundError()
                    if deck.revision != 1:
                        raise DeckRevisionConflictError()
                else:
                    if expected_revision is None:
                        raise DeckAlreadyExistsError()
                    if head.revision != expected_revision:
                        raise DeckRevisionConflictError()
                    if expected_digest is None or head.digest != expected_digest:
                        raise DeckDigestConflictError()
                    if deck.revision != head.revision + 1:
                        raise DeckRevisionConflictError()
                if isinstance(deck, DeckSnapshot):
                    master_id = deck.master_id
                    master_revision = deck.master_revision
                    master_digest = deck.master_digest
                else:
                    master_id = deck.master.master_id
                    master_revision = deck.master.master_revision
                    master_digest = deck.master.master_digest
                session.add(
                    AuthoredVNextDeckRevision(
                        owner_id=self.owner_id,
                        owner_scope=self.owner_scope,
                        tenant_id=self.tenant_id,
                        deck_id=deck.deck_id,
                        revision=deck.revision,
                        digest=deck.digest,
                        master_id=master_id,
                        master_revision=master_revision,
                        master_digest=master_digest,
                        payload=_json(deck),
                    )
                )
                for item in deck.slides:
                    slide = item if isinstance(deck, DeckSnapshot) else item.slide
                    exists = session.scalar(
                        select(AuthoredVNextSlideRevision.id).where(
                            *self._scope(AuthoredVNextSlideRevision),
                            AuthoredVNextSlideRevision.deck_id == deck.deck_id,
                            AuthoredVNextSlideRevision.slide_id == slide.slide_id,
                            AuthoredVNextSlideRevision.revision == slide.revision,
                        )
                    )
                    if exists is None:
                        session.add(
                            AuthoredVNextSlideRevision(
                                owner_id=self.owner_id,
                                owner_scope=self.owner_scope,
                                tenant_id=self.tenant_id,
                                deck_id=deck.deck_id,
                                slide_id=slide.slide_id,
                                revision=slide.revision,
                                spec_digest=slide.spec_digest,
                                master_digest=slide.master_digest,
                                payload=_json(slide),
                            )
                        )
                session.flush()
                if head is None:
                    session.add(
                        AuthoredVNextEntityHead(
                            owner_id=self.owner_id,
                            owner_scope=self.owner_scope,
                            tenant_id=self.tenant_id,
                            entity_kind="deck",
                            entity_id=deck.deck_id,
                            revision=deck.revision,
                            digest=deck.digest,
                            generation=1,
                        )
                    )
                else:
                    changed = session.execute(
                        update(AuthoredVNextEntityHead)
                        .where(
                            AuthoredVNextEntityHead.id == head.id,
                            AuthoredVNextEntityHead.revision == expected_revision,
                            AuthoredVNextEntityHead.digest == expected_digest,
                        )
                        .values(revision=deck.revision, digest=deck.digest, generation=head.generation + 1)
                    ).rowcount
                    if changed != 1:
                        raise DeckRevisionConflictError()
        except IntegrityError as exc:
            raise DeckRevisionConflictError() from exc

    def save_candidate(self, candidate_digest: str, payload: dict[str, object]) -> None:
        try:
            with self._sessions.begin() as session:
                self._active_account_generation(session)
                existing = session.scalar(
                    select(AuthoredVNextCandidate).where(
                        *self._scope(AuthoredVNextCandidate),
                        AuthoredVNextCandidate.candidate_digest == candidate_digest,
                    )
                )
                if existing is not None:
                    if existing.payload != payload:
                        raise ExportJobConflictError(
                            "candidate idempotency payload changed"
                        )
                    return
                session.add(
                    AuthoredVNextCandidate(
                        owner_id=self.owner_id,
                        owner_scope=self.owner_scope,
                        tenant_id=self.tenant_id,
                        candidate_digest=candidate_digest,
                        payload=payload,
                    )
                )
        except IntegrityError as exc:
            with self._sessions() as session:
                existing = session.scalar(
                    select(AuthoredVNextCandidate).where(
                        *self._scope(AuthoredVNextCandidate),
                        AuthoredVNextCandidate.candidate_digest == candidate_digest,
                    )
                )
                if existing is not None and existing.payload == payload:
                    return
            raise ExportJobConflictError("candidate write raced") from exc

    def record_quality_decision(
        self,
        *,
        deck_id: str,
        deck_revision: int,
        deck_digest: str,
        idempotency_key: str,
        decision: str,
        policy_version: str,
        evidence_digest: str,
        evidence: dict[str, Any],
    ) -> AuthoredVNextQualityDecision:
        key_hash = _hash(idempotency_key)
        try:
            with self._sessions.begin() as session:
                self._active_account_generation(session)
                existing = session.scalar(
                    select(AuthoredVNextQualityDecision).where(
                        *self._scope(AuthoredVNextQualityDecision),
                        AuthoredVNextQualityDecision.decision_key_hash == key_hash,
                    )
                )
                if existing:
                    if (
                        existing.deck_digest,
                        existing.decision,
                        existing.evidence_digest,
                    ) != (deck_digest, decision, evidence_digest):
                        raise ExportJobConflictError("quality idempotency payload changed")
                    return existing
                row = AuthoredVNextQualityDecision(
                    owner_id=self.owner_id,
                    owner_scope=self.owner_scope,
                    tenant_id=self.tenant_id,
                    deck_id=deck_id,
                    deck_revision=deck_revision,
                    deck_digest=deck_digest,
                    decision_key_hash=key_hash,
                    decision=decision,
                    policy_version=policy_version,
                    evidence_digest=evidence_digest,
                    evidence=evidence,
                )
                session.add(row)
                session.flush()
                session.expunge(row)
                return row
        except IntegrityError as exc:
            with self._sessions() as session:
                existing = session.scalar(
                    select(AuthoredVNextQualityDecision).where(
                        *self._scope(AuthoredVNextQualityDecision),
                        AuthoredVNextQualityDecision.decision_key_hash == key_hash,
                    )
                )
                if existing is not None and (
                    existing.deck_digest,
                    existing.decision,
                    existing.evidence_digest,
                ) == (deck_digest, decision, evidence_digest):
                    return existing
            raise ExportJobConflictError("quality decision write raced") from exc

    def record_render_receipt(
        self,
        *,
        deck_id: str,
        deck_revision: int,
        slide_id: str,
        slide_revision: int,
        renderer: str,
        idempotency_key: str,
        input_digest: str,
        output_digest: str,
        storage_key: str,
        width_px: int,
        height_px: int,
    ) -> AuthoredVNextRenderReceipt:
        _require_digest(input_digest, "render input_digest")
        _require_digest(output_digest, "render output_digest")
        key_hash = _hash(idempotency_key)
        try:
            with self._sessions.begin() as session:
                self._active_account_generation(session)
                existing = session.scalar(
                    select(AuthoredVNextRenderReceipt).where(
                        *self._scope(AuthoredVNextRenderReceipt),
                        AuthoredVNextRenderReceipt.idempotency_key_hash == key_hash,
                    )
                )
                if existing is not None:
                    if (
                        existing.input_digest,
                        existing.output_digest,
                        existing.storage_key,
                    ) != (input_digest, output_digest, storage_key):
                        raise ExportJobConflictError("render idempotency payload changed")
                    return existing
                row = AuthoredVNextRenderReceipt(
                    owner_id=self.owner_id,
                    owner_scope=self.owner_scope,
                    tenant_id=self.tenant_id,
                    deck_id=deck_id,
                    deck_revision=deck_revision,
                    slide_id=slide_id,
                    slide_revision=slide_revision,
                    renderer=renderer,
                    idempotency_key_hash=key_hash,
                    input_digest=input_digest,
                    output_digest=output_digest,
                    storage_key=storage_key,
                    width_px=width_px,
                    height_px=height_px,
                )
                session.add(row)
                session.flush()
                session.expunge(row)
                return row
        except IntegrityError as exc:
            with self._sessions() as session:
                existing = session.scalar(
                    select(AuthoredVNextRenderReceipt).where(
                        *self._scope(AuthoredVNextRenderReceipt),
                        AuthoredVNextRenderReceipt.idempotency_key_hash == key_hash,
                    )
                )
                if existing is not None and (
                    existing.input_digest,
                    existing.output_digest,
                    existing.storage_key,
                ) == (input_digest, output_digest, storage_key):
                    return existing
            raise ExportJobConflictError("render receipt write raced") from exc

__all__ = ["AuthoredVNextCompositionRepositoryMixin"]

