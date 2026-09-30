"""Phase 3: overlap the per-slide tail with the deck's authoring pass.

``docs/authored-quality-speed-programme-design-20260905.md`` section 10 (phase
3, "streaming author->tail"), against the phase barrier recorded in section
1.1. The job lane used to author every slide first and only then start the
worker pool that illustrates, renders, gates and checkpoints them, so the last
slide's authoring call delayed the first slide's render by the whole authoring
stage.

With ``AUTHORED_TAIL_STREAMING_ENABLED`` the pool starts *with* the authoring
pass and is fed one slide index at a time, the moment that slide's own HTML is
checkpointed. Ordering, recovery and failure semantics are unchanged:

* a slide is published exactly once, and only after its HTML is persisted;
* a slide whose first call failed is not published until its recovery call
  succeeds -- the pool must never claim a half-authored slide;
* a slide that fails both calls is never published: the authoring pass fails
  the deck exactly as it does today, and the pool is then cancelled and
  drained rather than left waiting on a queue nothing will fill;
* whatever the pass leaves unpublished (a slide resumed from an earlier
  attempt, a slide no call covered) is flushed in index order when the pass
  ends, so the pool still sees every index exactly once and ``process_slide``
  keeps its own authoring branch for those slides.

``run_authored_recovery_pass`` lives here too: the recovery window is the
authoring pass's second publish point, and keeping both publish points in one
module is what lets the pipeline hold a single, small streaming seam.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Iterable, MutableMapping, Sequence

from services.authored_batch_policy import plan_authored_recovery_batches
from services.authored_slide_ledger import (
    assert_authored_ledger_disjoint,
    batch_recorder,
)
from services.authored_slide_worker_pool import run_slide_worker_pool
from services.generation_core.authoring.author_deck import AuthoredBatchFailure

logger = logging.getLogger(__name__)


class AuthoredTailStreamCoordinator:
    """Hands slide indexes to the tail worker pool as their authoring lands.

    The coordinator starts *paused*: the deck's style anchor is authored before
    anything else and may be re-authored once by its own gate, so its first
    result may still be replaced. Publishing while paused records the slide and
    holds it back; :meth:`resume`, called once the anchor stage has finished,
    releases everything held in index order.
    """

    def __init__(self, slide_count: int) -> None:
        self._slide_count = max(0, int(slide_count))
        self._queue: asyncio.Queue[int | None] = asyncio.Queue()
        self._published: set[int] = set()
        self._held: list[int] = []
        self._paused = True
        self._closed = False

    @property
    def published(self) -> tuple[int, ...]:
        """Every slide index handed to (or held for) the pool, in order."""

        return tuple(sorted(self._published))

    @property
    def closed(self) -> bool:
        return self._closed

    def publish(self, index: int) -> bool:
        """Offer one authored slide to the tail. ``False`` means it was not new."""

        if self._closed:
            # The pass has already flushed the deck; a late publish would hand
            # the pool a second copy of an index it has claimed.
            logger.warning("authored_tail_stream_publish_after_close index=%s", index)
            return False
        try:
            slide_index = int(index)
        except (TypeError, ValueError):
            return False
        if not 0 <= slide_index < self._slide_count:
            return False
        if slide_index in self._published:
            return False
        self._published.add(slide_index)
        if self._paused:
            self._held.append(slide_index)
            return True
        self._queue.put_nowait(slide_index)
        return True

    def publish_many(self, indexes: Iterable[int]) -> None:
        for index in sorted(indexes):
            self.publish(index)

    def resume(self) -> None:
        """Release the slides held during the anchor stage, in index order."""

        if not self._paused:
            return
        self._paused = False
        for index in sorted(self._held):
            self._queue.put_nowait(index)
        self._held.clear()

    def close(self) -> None:
        """Flush every slide the pass never published, then end the stream.

        A slide resumed from an earlier attempt, or one no authoring call
        covered, still belongs to the pool: ``process_slide`` reads its
        checkpoint and, where nothing is authored, authors it itself. Closing
        without them would hang the pool on a deck it can never complete.
        """

        if self._closed:
            return
        self.resume()
        for index in range(self._slide_count):
            if index not in self._published:
                self._published.add(index)
                self._queue.put_nowait(index)
        self._closed = True
        self._queue.put_nowait(None)

    async def next_index(self) -> int | None:
        """The next slide the pool may claim; ``None`` ends the stream."""

        return await self._queue.get()


async def run_streamed_slide_tail(
    process_slide: Callable[[int], Awaitable[Any]],
    *,
    author_pass: Callable[[], Awaitable[None]],
    coordinator: AuthoredTailStreamCoordinator,
    slide_count: int,
    worker_count: int,
) -> list[Any]:
    """Run the authoring pass and the slide tail concurrently.

    Returns the pool's results in slide order. A failing authoring pass raises
    its own error after the pool has been cancelled and drained; a failing
    slide raises the worker's error after the authoring pass has been
    cancelled, so neither side can leave the other waiting.
    """

    author_task = asyncio.create_task(author_pass())
    pool_task = asyncio.create_task(
        run_slide_worker_pool(
            process_slide,
            slide_count=slide_count,
            worker_count=worker_count,
            index_source=coordinator.next_index,
        )
    )
    author_error: BaseException | None = None
    try:
        pending = {author_task, pool_task}
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            if author_task in done:
                author_error = author_task.exception()
                if author_error is not None:
                    break
                # Everything the pass authored is queued; the flush below tells
                # the pool which slides it will never be handed.
                coordinator.close()
            if pool_task in done and pool_task.exception() is not None:
                break
        if author_error is not None:
            raise author_error
        return pool_task.result()
    finally:
        for task in (author_task, pool_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(author_task, pool_task, return_exceptions=True)


async def run_authored_recovery_pass(
    failed_for_recovery: Sequence[int],
    *,
    schedule: Any,
    run_batch: Callable[[tuple[int, ...]], Awaitable[Any]],
    successful: MutableMapping[int, str] | set,
    failures: MutableMapping[int, AuthoredBatchFailure],
    publish: AuthoredTailStreamCoordinator | None = None,
) -> None:
    """Spend each still-failed slide's one recovery call, at the live width.

    Lifted verbatim from the pipeline's pre-authoring pass; ``publish`` is the
    only addition, and it fires exactly where the initial pass's own publish
    fires -- on HTML this call persisted.
    """

    assert_authored_ledger_disjoint(
        successful, failures, stage="authored_recovery_pass"
    )
    # The same booking rule as the initial pass, including its publish point:
    # a slide is published on the HTML this call persisted, and a call that
    # fails for a slide the deck already accepted leaves it accepted.
    record = batch_recorder(
        successful=successful, failures=failures, publish=publish
    )
    recovery_batches = plan_authored_recovery_batches(
        failed_for_recovery,
        successful_slide_indices=successful,
        profile=schedule.policy.profile,
    )
    for offset in range(0, len(recovery_batches), schedule.policy.concurrency):
        await schedule.wait_out_cooldown(f"recovery-{offset}")
        window = recovery_batches[offset : offset + schedule.policy.concurrency]
        outcomes = await asyncio.gather(
            *(run_batch(batch.slide_indices) for batch in window),
            return_exceptions=True,
        )
        for batch, outcome in zip(window, outcomes, strict=True):
            schedule.advance(outcome, succeeded=record(batch.slide_indices, outcome))


__all__ = [
    "AuthoredTailStreamCoordinator",
    "run_authored_recovery_pass",
    "run_streamed_slide_tail",
]
