"""Generation repository contract and its async boundary."""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any, Callable, Protocol, runtime_checkable

from models.authored_vnext.master.operations import MasterRevisionResponse


@runtime_checkable
class GenerationRepository(Protocol):
    """Minimum persistence contract required by the generation pipeline."""

    def get_master(self, master_id: str, revision: int | None = None) -> MasterRevisionResponse: ...
    def get_master_head(self, master_id: str) -> MasterRevisionResponse: ...
    def get_deck(self, deck_id: str, revision: int | None = None) -> Any: ...
    def append_imported_master(self, master: MasterRevisionResponse) -> None: ...
    def append_imported_deck(self, deck: Any) -> None: ...
    def save_candidate(self, candidate_digest: str, payload: dict[str, object]) -> None: ...
    def get_candidate(self, candidate_digest: str) -> dict[str, object]: ...


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
