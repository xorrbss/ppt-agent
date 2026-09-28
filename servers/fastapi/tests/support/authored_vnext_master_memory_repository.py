"""Deterministic in-memory authored vNext master repository for tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from models.authored_vnext.master.operations import (
    MasterCatalogPage,
    MasterRevisionResponse,
)
from models.authored_vnext.master.slide import DeckSnapshot
from services.authored_vnext.master.errors import (
    DeckNotFoundError,
    DeckRevisionConflictError,
    MasterNotFoundError,
    MasterRevisionConflictError,
)
from services.authored_vnext.master.repository import (
    _check_deck_cas,
    _check_master_cas,
    decode_catalog_cursor,
    encode_catalog_cursor,
    master_catalog_item,
)
from services.authored_vnext.master.visibility import is_internal_authored_master_id


class InMemoryAuthoredVNextMasterRepository:
    """Thread-safe injected repository used by focused tests and local composition."""

    def __init__(self) -> None:
        self._masters: dict[str, dict[int, MasterRevisionResponse]] = {}
        self._master_heads: dict[str, int] = {}
        self._master_published: dict[str, int] = {}
        self._master_updated: dict[str, datetime] = {}
        self._decks: dict[str, dict[int, DeckSnapshot]] = {}
        self._deck_heads: dict[str, int] = {}
        self.candidates: dict[str, dict[str, object]] = {}
        self._lock = RLock()

    def allocate_master_id(self) -> str:
        with self._lock:
            while True:
                master_id = f"master:{uuid4().hex}"
                if master_id not in self._masters:
                    return master_id

    def get_master(
        self, master_id: str, revision: int | None = None
    ) -> MasterRevisionResponse:
        with self._lock:
            selected = revision if revision is not None else self._master_heads.get(master_id)
            if selected is None or selected not in self._masters.get(master_id, {}):
                raise MasterNotFoundError()
            return deepcopy(self._masters[master_id][selected])

    def get_published_master(self, master_id: str) -> MasterRevisionResponse:
        with self._lock:
            selected = self._master_published.get(master_id)
            if selected is None:
                raise MasterNotFoundError()
            return deepcopy(self._masters[master_id][selected])

    def list_masters(
        self, *, cursor: str | None, limit: int
    ) -> MasterCatalogPage:
        after = decode_catalog_cursor(cursor)
        with self._lock:
            master_ids = sorted(
                master_id
                for master_id in self._master_heads
                if not is_internal_authored_master_id(master_id)
                and (after is None or master_id > after)
            )
            selected_ids = master_ids[: limit + 1]
            has_more = len(selected_ids) > limit
            selected_ids = selected_ids[:limit]
            items = [
                master_catalog_item(
                    self._masters[master_id][
                        self._master_published.get(
                            master_id, self._master_heads[master_id]
                        )
                    ],
                    published=(
                        self._masters[master_id][self._master_published[master_id]]
                        if master_id in self._master_published
                        else None
                    ),
                    has_draft=(
                        master_id not in self._master_published
                        or self._master_heads[master_id]
                        > self._master_published[master_id]
                    ),
                    updated_time=self._master_updated[master_id],
                )
                for master_id in selected_ids
            ]
            return MasterCatalogPage(
                items=items,
                next_cursor=(
                    encode_catalog_cursor(selected_ids[-1])
                    if has_more and selected_ids
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
            self._master_updated[record.master_id] = datetime.now(timezone.utc)
            if record.status == "published":
                self._master_published[record.master_id] = record.revision

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
