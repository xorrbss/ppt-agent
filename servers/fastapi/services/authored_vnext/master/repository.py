"""Replaceable immutable repository contract and deterministic memory fixture."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Protocol

from models.authored_vnext.master.operations import MasterRevisionResponse
from models.authored_vnext.master.slide import DeckSnapshot

from .errors import (
    DeckAlreadyExistsError,
    DeckDigestConflictError,
    DeckNotFoundError,
    DeckRevisionConflictError,
    MasterDigestConflictError,
    MasterNotFoundError,
    MasterRevisionConflictError,
)


class AuthoredVNextMasterRepository(Protocol):
    def get_master(
        self, master_id: str, revision: int | None = None
    ) -> MasterRevisionResponse: ...

    def append_master(
        self,
        record: MasterRevisionResponse,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None: ...

    def get_deck(self, deck_id: str, revision: int | None = None) -> DeckSnapshot: ...

    def append_deck(
        self,
        deck: DeckSnapshot,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None: ...

    def save_candidate(self, candidate_digest: str, payload: dict[str, object]) -> None: ...


def _check_master_cas(
    current: MasterRevisionResponse | None,
    expected_revision: int | None,
    expected_digest: str | None,
) -> None:
    if current is None:
        if expected_revision is not None:
            raise MasterRevisionConflictError()
        if expected_digest is not None:
            raise MasterDigestConflictError()
        return
    if expected_revision is None or current.revision != expected_revision:
        raise MasterRevisionConflictError()
    if expected_digest is None or current.digest != expected_digest:
        raise MasterDigestConflictError()


def _check_deck_cas(
    current: DeckSnapshot | None,
    expected_revision: int | None,
    expected_digest: str | None,
) -> None:
    if current is None:
        if expected_revision is not None or expected_digest is not None:
            raise DeckNotFoundError()
        return
    if expected_revision is None:
        raise DeckAlreadyExistsError()
    if current.revision != expected_revision:
        raise DeckRevisionConflictError()
    if expected_digest is None or current.digest != expected_digest:
        raise DeckDigestConflictError()


class InMemoryAuthoredVNextMasterRepository:
    """Thread-safe injected repository used by focused tests and local composition."""

    def __init__(self) -> None:
        self._masters: dict[str, dict[int, MasterRevisionResponse]] = {}
        self._master_heads: dict[str, int] = {}
        self._decks: dict[str, dict[int, DeckSnapshot]] = {}
        self._deck_heads: dict[str, int] = {}
        self.candidates: dict[str, dict[str, object]] = {}
        self._lock = RLock()

    def get_master(
        self, master_id: str, revision: int | None = None
    ) -> MasterRevisionResponse:
        with self._lock:
            selected = revision if revision is not None else self._master_heads.get(master_id)
            if selected is None or selected not in self._masters.get(master_id, {}):
                raise MasterNotFoundError()
            return deepcopy(self._masters[master_id][selected])

    def append_master(
        self,
        record: MasterRevisionResponse,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        with self._lock:
            current = None
            if record.master_id in self._master_heads:
                current = self._masters[record.master_id][self._master_heads[record.master_id]]
            _check_master_cas(current, expected_revision, expected_digest)
            expected_next = 1 if current is None else current.revision + 1
            if record.revision != expected_next:
                raise MasterRevisionConflictError()
            revisions = self._masters.setdefault(record.master_id, {})
            if record.revision in revisions:
                raise MasterRevisionConflictError()
            revisions[record.revision] = deepcopy(record)
            self._master_heads[record.master_id] = record.revision

    def get_deck(self, deck_id: str, revision: int | None = None) -> DeckSnapshot:
        with self._lock:
            selected = revision if revision is not None else self._deck_heads.get(deck_id)
            if selected is None or selected not in self._decks.get(deck_id, {}):
                raise DeckNotFoundError()
            return deepcopy(self._decks[deck_id][selected])

    def append_deck(
        self,
        deck: DeckSnapshot,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        with self._lock:
            current = None
            if deck.deck_id in self._deck_heads:
                current = self._decks[deck.deck_id][self._deck_heads[deck.deck_id]]
            _check_deck_cas(current, expected_revision, expected_digest)
            expected_next = 1 if current is None else current.revision + 1
            if deck.revision != expected_next:
                raise DeckRevisionConflictError()
            revisions = self._decks.setdefault(deck.deck_id, {})
            if deck.revision in revisions:
                raise DeckRevisionConflictError()
            revisions[deck.revision] = deepcopy(deck)
            self._deck_heads[deck.deck_id] = deck.revision

    def save_candidate(self, candidate_digest: str, payload: dict[str, object]) -> None:
        with self._lock:
            self.candidates.setdefault(candidate_digest, deepcopy(payload))
