"""The async boundary in front of the synchronous generation repository.

Regression cover for the publication deadlock: the vNext CAS repository is
synchronous, and calling it from a coroutine stops the event loop until the
statement returns.  With one shared database that is not a stall but a
deadlock -- the loop's own async writer cannot commit and release SQLite's
write lock while the loop is blocked, and the blocked synchronous call is
waiting for exactly that lock.
"""

from __future__ import annotations

import asyncio
import threading
import uuid

from services.authored_vnext.generation.repository import (
    AsyncGenerationRepository,
    InMemoryGenerationRepository,
    async_generation_repository,
)
from utils.owner_context import (
    get_current_owner_id,
    reset_current_owner_id,
    set_current_owner_id,
)


class _BlockingRepository:
    """A synchronous repository whose one call is observably blocking."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.threads: list[int] = []
        self.owner_ids: list[uuid.UUID | None] = []
        self.name = "blocking"

    def get_candidate(self, candidate_digest: str) -> dict[str, object]:
        self.threads.append(threading.get_ident())
        self.owner_ids.append(get_current_owner_id())
        self.entered.set()
        self.release.wait(10)
        return {"digest": candidate_digest}


def test_a_blocking_repository_call_leaves_the_event_loop_free() -> None:
    async def scenario() -> None:
        repository = _BlockingRepository()
        facade = async_generation_repository(repository)
        loop_thread = threading.get_ident()

        call = asyncio.ensure_future(facade.get_candidate("sha256:blocked"))
        # The loop keeps advancing while the synchronous call sits inside the
        # database.  That is the property the publication path needs: an async
        # writer can still commit and release the SQLite write lock.
        for _ in range(400):
            if repository.entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert repository.entered.is_set()

        repository.release.set()
        assert await asyncio.wait_for(call, 10) == {"digest": "sha256:blocked"}
        assert repository.threads and loop_thread not in repository.threads

    asyncio.run(scenario())


def test_owner_scope_context_reaches_the_worker_thread() -> None:
    """The ORM scoping hooks read contextvars, which ``to_thread`` copies."""

    async def scenario() -> None:
        repository = _BlockingRepository()
        repository.release.set()
        owner_id = uuid.uuid4()
        token = set_current_owner_id(owner_id)
        try:
            await async_generation_repository(repository).get_candidate("sha256:scoped")
        finally:
            reset_current_owner_id(token)
        assert repository.owner_ids == [owner_id]

    asyncio.run(scenario())


def test_run_hands_one_composite_unit_to_the_worker_thread() -> None:
    """A read-then-write CAS crosses the boundary whole, not half at a time."""

    async def scenario() -> None:
        repository = InMemoryGenerationRepository()
        facade = async_generation_repository(repository)
        assert async_generation_repository(facade) is facade

        def read_then_write(handle, candidate_digest: str) -> dict[str, object]:
            assert handle is repository
            handle.save_candidate(
                candidate_digest, {"thread": threading.get_ident()}
            )
            return handle.get_candidate(candidate_digest)

        stored = await facade.run(read_then_write, "sha256:composite")
        assert stored["thread"] != threading.get_ident()
        assert await facade.get_candidate("sha256:composite") == stored

    asyncio.run(scenario())


def test_non_callable_repository_attributes_pass_straight_through() -> None:
    facade = AsyncGenerationRepository(_BlockingRepository())
    assert facade.name == "blocking"
