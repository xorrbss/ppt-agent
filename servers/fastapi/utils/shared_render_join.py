"""Joining a render task somebody else started.

Two caches in ``utils/slide_capture.py`` deduplicate browser work: the first
caller for a digest creates the task, later callers join it. Joining means
awaiting work you do not own, which is why the wait must be bounded. An
unbounded join is not one slow caller waiting a bit longer -- when the browser
wedges, every joiner is stranded at once, on the very event loop that would
otherwise have timed the renderer out.

So ``timeout`` here is keyword-only with no default and no ``None`` form: a
caller that cannot name a bound has not decided what happens when the shared
task never finishes. Pass the renderer's own timeout plus slack, so the inner
bound fires first and reports a real error rather than a generic outer one.

The shared task stays alive exactly as long as someone still wants its result.
The last waiter to leave cancels it, so an abandoned render does not keep a
browser slot held.
"""

from __future__ import annotations

import asyncio
from typing import Any

#: How many callers are currently awaiting each shared task.
_waiters: dict[asyncio.Task[Any], int] = {}


# ASYNC109 says to drop the parameter and let callers wrap the await in
# asyncio.timeout. That is the defect this module exists to close: it puts the
# bound back in the caller's hands, optional again, and the caller that forgets
# it strands every other joiner. The parameter is the enforcement.
async def await_shared_render_task(
    task: asyncio.Task[Any],
    inflight: dict,
    key: tuple[str, str, str],
    *,
    timeout: float,  # noqa: ASYNC109 -- see the note above
) -> Any:
    """Await ``task``, keeping it alive only while a caller still needs it."""
    if task not in _waiters:

        def completed(done: asyncio.Task[Any]) -> None:
            if inflight.get(key) is done:
                inflight.pop(key, None)
            if not done.cancelled():
                done.exception()  # Retrieve errors even if every caller departed.

        task.add_done_callback(completed)
    _waiters[task] = _waiters.get(task, 0) + 1
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    finally:
        remaining = _waiters[task] - 1
        if remaining:
            _waiters[task] = remaining
        else:
            _waiters.pop(task, None)
            if inflight.get(key) is task:
                inflight.pop(key, None)
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


__all__ = ["await_shared_render_task"]
