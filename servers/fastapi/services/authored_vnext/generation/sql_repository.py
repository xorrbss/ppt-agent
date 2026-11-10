"""SQL-backed reads of published masters and decks for generation pins."""

# ruff: noqa: F401 - tests patch module globals such as ``time``.

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from models.authored_vnext.deck import DeckGenerationSnapshot
from models.authored_vnext.master.operations import MasterRevisionResponse
from models.sql.authored_vnext_repository import (
    AuthoredVNextCandidate,
    AuthoredVNextDeckRevision,
    AuthoredVNextEntityHead,
    AuthoredVNextMasterRevision,
    AuthoredVNextPublicationPointer,
)
from services.authored_vnext.generation.errors import GenerationNotFoundError
from services.authored_vnext.integration.sql_repository import (
    SQLITE_LOCK_RETRY_DELAYS,  # noqa: F401  (re-exported for callers)
    AuthoredVNextSqlRepository,
    sqlite_write_retry,
)
from services.authored_vnext.master.errors import (
    DeckAlreadyExistsError,
    MasterRevisionConflictError,
)
from .composition import remember_composition_pin


LOGGER = logging.getLogger(__name__)


_retry_sqlite_write = sqlite_write_retry(lambda repository: repository._base.engine)


class SqlGenerationRepository:
    """Adapts the shared authored SQL schema to the generation contract."""

    def __init__(self, repository: AuthoredVNextSqlRepository) -> None:
        self._base = repository
        self._sessions = sessionmaker(repository.engine, expire_on_commit=False)

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
    def append_imported_master(self, master: MasterRevisionResponse) -> None:
        """Persist an immutable server-owned generation master idempotently."""

        try:
            self._base.append_master(
                master,
                expected_revision=None,
                expected_digest=None,
            )
        except MasterRevisionConflictError:
            current = self.get_master(master.master_id, master.revision)
            if current != master:
                raise

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
