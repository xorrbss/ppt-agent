"""Readiness-based operation batching with bounded pooled render authorities."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, Sequence
from weakref import WeakKeyDictionary, ref

from sqlalchemy.pool import NullPool, QueuePool

from models.sql.public_artifact import ArtifactRef

MAX_PUBLIC_RENDER_BATCH_SLIDES = 20

class PublicRenderBatchingError(RuntimeError):
    pass


class RenderAuthorityAdmission:
    """Leave pool capacity available for checkpoint and artifact transactions."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise PublicRenderBatchingError("public_render_pool_capacity_invalid")
        self._slots = asyncio.Semaphore(capacity)

    @asynccontextmanager
    async def acquire(
        self, *, on_wait: Callable[[], None], wait_seconds: float
    ) -> AsyncIterator[None]:
        # No suspension between the locked check and an available acquire.
        # A blocked participant cannot keep its operation's ready set open.
        if self._slots.locked():
            on_wait()
        try:
            async with asyncio.timeout(wait_seconds):
                await self._slots.acquire()
        except TimeoutError as error:
            raise PublicRenderBatchingError(
                "public_render_authority_admission_timeout"
            ) from error
        try:
            yield
        finally:
            self._slots.release()


_admissions: WeakKeyDictionary = WeakKeyDictionary()


def pooled_render_authority_admission(bind: object) -> RenderAuthorityAdmission:
    """Share the budget by actual synchronous engine and running event loop.

    Only the caller's PostgreSQL engine belongs here. Explicit unbounded pools
    use a finite batch-sized budget; undersized/unknown pools fail closed.
    Weak keys do not retain disposed engines or completed test/event loops.
    """
    pool = getattr(bind, "pool", None)
    size = getattr(pool, "size", None)
    overflow = getattr(pool, "_max_overflow", None)
    capacity = size() if callable(size) else None
    if isinstance(pool, NullPool):
        limit = MAX_PUBLIC_RENDER_BATCH_SLIDES
    elif isinstance(pool, QueuePool) and (
        capacity == 0 or overflow == -1
    ):
        limit = MAX_PUBLIC_RENDER_BATCH_SLIDES
    elif (
        isinstance(capacity, bool) or not isinstance(capacity, int)
        or capacity < 1 or isinstance(overflow, bool)
        or not isinstance(overflow, int) or overflow < 0
        or capacity + overflow <= 2
    ):
        raise PublicRenderBatchingError("public_render_pool_capacity_invalid")
    else:
        limit = capacity + overflow - 2
    loop = asyncio.get_running_loop()
    engines = _admissions.setdefault(loop, WeakKeyDictionary())
    existing = engines.get(bind)
    admission = existing() if existing is not None else None
    if admission is None:
        admission = RenderAuthorityAdmission(limit)
        engines[bind] = ref(admission)
    return admission


@asynccontextmanager
async def admit_render_authority(
    session: object, *, on_wait: Callable[[], None], wait_seconds: float
) -> AsyncIterator[None]:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite's existing development authority never holds a DB transaction.
        yield
        return
    async with pooled_render_authority_admission(bind).acquire(
        on_wait=on_wait, wait_seconds=wait_seconds
    ):
        yield


class OperationRenderBatcher:
    """Flush only once every input slot is enqueued or explicitly waiting away.

    Readiness is not completion. Checkpoint hits, joined producers and blocked
    authority acquisitions discharge their slot before awaiting those results.
    A later fresh render after external contention forms a truthful new wave.
    """

    def __init__(
        self,
        *,
        participant_count: int,
        render_batch: Callable[[Sequence[ArtifactRef]], Awaitable[Sequence[ArtifactRef]]],
        partition: Callable[[Sequence[ArtifactRef]], Sequence[Sequence[ArtifactRef]]],
        error_type: type[RuntimeError] = PublicRenderBatchingError,
    ) -> None:
        if participant_count < 1:
            raise ValueError("render participants must be positive")
        self._participant_count = participant_count
        self._unsettled = set(range(participant_count))
        self._pending: list[tuple[int, ArtifactRef, asyncio.Future[ArtifactRef]]] = []
        self._submitted: set[int] = set()
        self._wake = asyncio.Event()
        self._closed = False
        self._render_batch = render_batch
        self._partition = partition
        self._error_type = error_type
        self._drain: asyncio.Task[None] | None = None

    def settle(self, slide_index: int) -> None:
        if slide_index < 0 or slide_index >= self._participant_count:
            raise self._error_type("public_render_participant_invalid")
        self._unsettled.discard(slide_index)
        if not self._unsettled and self._pending:
            self._wake.set()

    async def render(self, slide_index: int, reference: ArtifactRef) -> ArtifactRef:
        if self._closed:
            raise self._error_type("public_render_batcher_closed")
        if slide_index in self._submitted:
            raise self._error_type("public_render_participant_duplicate")
        future: asyncio.Future[ArtifactRef] = asyncio.get_running_loop().create_future()
        self._submitted.add(slide_index)
        self._pending.append((slide_index, reference, future))
        self.settle(slide_index)  # enqueue and ready are atomic, before result wait
        if self._drain is None:
            self._drain = asyncio.create_task(self._drain_pending())
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            future.cancel()
            raise

    async def close(self) -> None:
        self._closed = True
        pending, self._pending = self._pending, []
        for _, _, future in pending:
            future.cancel()
        if self._drain is not None:
            self._drain.cancel()
            await asyncio.gather(self._drain, return_exceptions=True)

    async def _drain_pending(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            pending, self._pending = self._pending, []
            pending = sorted(
                (item for item in pending if not item[2].done()),
                key=lambda item: item[0],
            )
            if not pending:
                continue
            batch_tasks: list[asyncio.Task] = []
            try:
                batch_tasks = [
                    asyncio.create_task(self._render_batch(batch))
                    for batch in self._partition([item[1] for item in pending])
                ]
                batches = await asyncio.gather(*batch_tasks)
                outputs = [output for batch in batches for output in batch]
                if len(outputs) != len(pending):
                    raise self._error_type("public_render_batch_output_count_invalid")
                for (_, _, future), output in zip(pending, outputs, strict=True):
                    if not future.done():
                        future.set_result(output)
            except BaseException as error:
                for task in batch_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*batch_tasks, return_exceptions=True)
                for _, _, future in pending:
                    if future.done():
                        continue
                    if isinstance(error, asyncio.CancelledError):
                        future.cancel()
                    else:
                        future.set_exception(error)
                if not isinstance(error, Exception):
                    raise


async def gather_render_members(members: Sequence[Awaitable[ArtifactRef]]) -> list[ArtifactRef]:
    """Ordinary failure must drain DB waiters/holders as well as worker batches."""
    tasks = [asyncio.create_task(member) for member in members]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
