"""Generation repository contract and deterministic test implementation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from functools import wraps
from threading import RLock
from typing import Any, Callable, Protocol, runtime_checkable

from models.authored_vnext.master.operations import MasterRevisionResponse

from .errors import GenerationConflictError, GenerationNotFoundError


@runtime_checkable
class GenerationRepository(Protocol):
    """Minimum persistence contract required by the generation pipeline."""

    def get_master(self, master_id: str, revision: int | None = None) -> MasterRevisionResponse: ...
    def get_master_head(self, master_id: str) -> MasterRevisionResponse: ...
    def get_deck(self, deck_id: str, revision: int | None = None) -> Any: ...
    def append_imported_master(self, master: MasterRevisionResponse) -> None: ...
    def append_imported_deck(self, deck: Any) -> None: ...
    def get_operation(self, operation_id: str) -> Any: ...
    def save_candidate(self, candidate_digest: str, payload: dict[str, object]) -> None: ...
    def get_candidate(self, candidate_digest: str) -> dict[str, object]: ...
    def publish_result(
        self,
        deck: Any,
        result: Any,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None: ...


class AsyncGenerationRepository:
    """The one boundary between async callers and the synchronous CAS.

    Every method of this repository reaches the database synchronously, so
    calling one from a coroutine does exactly what it looks like: the event
    loop stops until the statement returns.  On the SQLite profile, where the
    whole process shares a single database, that is not a stall but a
    deadlock -- the loop's own async writer cannot commit and release the
    write lock while the loop is blocked, and the blocked synchronous call is
    waiting for precisely that lock.  It surfaced as ``database is locked``
    raised from a read-then-write CAS at the very end of a fully generated
    deck.

    So async code holds this facade instead, and every method it forwards runs
    in a worker thread.  ``asyncio.to_thread`` copies the current context, so
    the owner/tenant scoping stays effective there: the hooks are registered on
    ``sqlalchemy.orm.Session`` itself and read the owner contextvars.
    """

    __slots__ = ("_repository",)

    def __init__(self, repository: GenerationRepository) -> None:
        self._repository = repository

    async def run(self, work: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """Hand one composite unit of repository work to the worker thread.

        A read-then-write CAS must not be interleaved with the loop between its
        halves, so its caller passes the whole sequence rather than awaiting
        each half separately.  ``work`` receives the synchronous repository as
        its first argument and is the only code that ever sees it.
        """

        return await asyncio.to_thread(work, self._repository, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        attribute = getattr(self._repository, name)
        if not callable(attribute):
            return attribute

        @wraps(attribute)
        async def off_the_loop(*args: Any, **kwargs: Any) -> Any:
            return await asyncio.to_thread(attribute, *args, **kwargs)

        return off_the_loop


def async_generation_repository(
    repository: GenerationRepository | AsyncGenerationRepository,
) -> AsyncGenerationRepository:
    """Adapt a synchronous repository to the async boundary, idempotently."""

    if isinstance(repository, AsyncGenerationRepository):
        return repository
    return AsyncGenerationRepository(repository)


class InMemoryGenerationRepository:
    """Stores immutable master/operation/deck revisions and CAS head pointers."""

    def __init__(self) -> None:
        self._masters: dict[str, dict[int, MasterRevisionResponse]] = {}
        self._master_heads: dict[str, int] = {}
        self._operations: dict[str, Any] = {}
        self._deck_revisions: dict[str, dict[int, Any]] = {}
        self._deck_heads: dict[str, int] = {}
        self._candidates: dict[str, dict[str, object]] = {}
        self._lock = RLock()

    def save_master(self, master: MasterRevisionResponse) -> None:
        with self._lock:
            self._masters.setdefault(master.master_id, {})[master.revision] = deepcopy(
                master
            )
            self._master_heads[master.master_id] = max(
                master.revision, self._master_heads.get(master.master_id, 0)
            )

    save_published_master = save_master

    def append_imported_master(self, master: MasterRevisionResponse) -> None:
        """Creation-only helper matching the product SQL repository."""

        with self._lock:
            existing = self._masters.get(master.master_id, {}).get(master.revision)
            if existing is not None:
                if existing != master:
                    raise GenerationConflictError("immutable master revision conflict")
                return
            if master.revision != 1 or master.master_id in self._master_heads:
                raise GenerationConflictError("imported master must start at revision 1")
            self._masters.setdefault(master.master_id, {})[master.revision] = deepcopy(
                master
            )
            self._master_heads[master.master_id] = master.revision

    def get_master(
        self, master_id: str, revision: int | None = None
    ) -> MasterRevisionResponse:
        with self._lock:
            selected = revision or self._master_heads.get(master_id)
            try:
                return deepcopy(self._masters[master_id][selected])
            except (KeyError, TypeError) as error:
                raise GenerationNotFoundError("master revision not found") from error

    def get_master_head(self, master_id: str) -> MasterRevisionResponse:
        return self.get_master(master_id)

    def save_operation(self, operation_id: str, operation: Any) -> None:
        with self._lock:
            if operation_id in self._operations:
                raise GenerationConflictError("operation already exists")
            self._operations[operation_id] = deepcopy(operation)

    def replace_operation(self, operation_id: str, operation: Any) -> None:
        with self._lock:
            if operation_id not in self._operations:
                raise GenerationNotFoundError("operation not found")
            self._operations[operation_id] = deepcopy(operation)

    def get_operation(self, operation_id: str) -> Any:
        with self._lock:
            if operation_id not in self._operations:
                raise GenerationNotFoundError("operation not found")
            return deepcopy(self._operations[operation_id])

    def publish_result(
        self,
        deck: Any,
        result: Any,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        """Atomically advance the test head and store the idempotent result."""
        with self._lock:
            self.append_deck(
                deck,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            self.save_operation(result.operation_id, result)

    def save_candidate(self, candidate_digest: str, payload: dict[str, object]) -> None:
        with self._lock:
            existing = self._candidates.get(candidate_digest)
            if existing is not None and existing != payload:
                raise GenerationConflictError("candidate digest payload conflict")
            self._candidates[candidate_digest] = deepcopy(payload)

    def get_candidate(self, candidate_digest: str) -> dict[str, object]:
        with self._lock:
            try:
                return deepcopy(self._candidates[candidate_digest])
            except KeyError as error:
                raise GenerationNotFoundError("candidate not found") from error

    def append_deck(
        self,
        deck: Any,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        with self._lock:
            head_revision = self._deck_heads.get(deck.deck_id)
            if head_revision is None:
                if expected_revision is not None or expected_digest is not None:
                    raise GenerationConflictError("deck does not exist")
                if deck.revision != 1:
                    raise GenerationConflictError("first deck revision must be 1")
            else:
                current = self._deck_revisions[deck.deck_id][head_revision]
                if current.revision != expected_revision:
                    raise GenerationConflictError("deck revision conflict")
                if current.digest != expected_digest:
                    raise GenerationConflictError("deck digest conflict")
                if deck.revision != current.revision + 1:
                    raise GenerationConflictError("deck revision must be monotonic")
            revisions = self._deck_revisions.setdefault(deck.deck_id, {})
            if deck.revision in revisions:
                raise GenerationConflictError("immutable deck revision exists")
            revisions[deck.revision] = deepcopy(deck)
            self._deck_heads[deck.deck_id] = deck.revision

    def append_imported_deck(self, deck: Any) -> None:
        with self._lock:
            existing = self._deck_revisions.get(deck.deck_id, {}).get(deck.revision)
            if existing is not None:
                if existing != deck:
                    raise GenerationConflictError("immutable deck revision conflict")
                return
        self.append_deck(deck, expected_revision=None, expected_digest=None)

    def get_deck(self, deck_id: str, revision: int | None = None) -> Any:
        with self._lock:
            selected = revision or self._deck_heads.get(deck_id)
            try:
                return deepcopy(self._deck_revisions[deck_id][selected])
            except (KeyError, TypeError) as error:
                raise GenerationNotFoundError("deck revision not found") from error
