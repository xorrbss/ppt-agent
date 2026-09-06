"""Keep a Server-Sent Events connection warm while its producer is silent.

An SSE generator that awaits a long LLM call writes nothing to the socket for
the whole of that await. Every hop in front of it applies its own idle timeout
to that silence -- undici's 300s body timeout in the Next.js proxy route, and
nginx ``proxy_read_timeout`` in the Docker path -- so a slow stage is killed
mid-flight even though the server is healthy and still working.

The fix is a comment frame. ``: ping\n\n`` is a conformant SSE comment: the
HTML spec has every parser discard a line starting with ``:`` without
dispatching an event, so the frame reaches no application handler while still
being bytes on the wire. Wrapping a generator here makes its *whole* body
heartbeated rather than only the one stage that is slow today.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from utils.get_env import get_setting_env
from utils.runtime_settings import RuntimeSettings

#: The comment frame written when the wrapped source has gone quiet.
SSE_HEARTBEAT_FRAME = ": ping\n\n"

#: Internal operational knob, resolved through the settings access layer. It is
#: not a managed/public provider setting, so it carries no registry entry.
SSE_HEARTBEAT_INTERVAL_ENV = "SSE_HEARTBEAT_INTERVAL_SECONDS"

#: Comfortably inside every idle timeout in front of this backend (undici's
#: 300s body timeout in the Next.js proxy route, nginx's ``proxy_read_timeout``)
#: while costing one comment frame per twelve silent seconds.
DEFAULT_SSE_HEARTBEAT_INTERVAL_SECONDS = 12.0

_MIN_SSE_HEARTBEAT_INTERVAL_SECONDS = 1.0
_MAX_SSE_HEARTBEAT_INTERVAL_SECONDS = 120.0

#: Returned by :func:`_pull` instead of raising, so a completed source is an
#: ordinary task result rather than an exception carried across a task boundary.
_EXHAUSTED: Any = object()


def get_sse_heartbeat_interval_seconds(
    settings: RuntimeSettings | None = None,
) -> float:
    """Silent seconds after which an SSE stream writes a comment frame.

    Unset or unparseable takes the default. A value of zero or less disables
    heartbeats altogether; anything else is clamped into ``[1, 120]`` so a
    typo can neither flood the socket nor push the gap past a proxy timeout.
    """

    raw = (get_setting_env(SSE_HEARTBEAT_INTERVAL_ENV, settings) or "").strip()
    if not raw:
        return DEFAULT_SSE_HEARTBEAT_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SSE_HEARTBEAT_INTERVAL_SECONDS
    if value <= 0:
        return 0.0
    return max(
        _MIN_SSE_HEARTBEAT_INTERVAL_SECONDS,
        min(_MAX_SSE_HEARTBEAT_INTERVAL_SECONDS, value),
    )


async def _pull(iterator: AsyncIterator[str]) -> Any:
    """Await one chunk, reporting exhaustion as ``_EXHAUSTED``."""

    try:
        return await iterator.__anext__()
    except StopAsyncIteration:
        return _EXHAUSTED


async def _shutdown(
    pending: asyncio.Future[Any] | None,
    iterator: AsyncIterator[str],
) -> None:
    """Cancel an in-flight pull and close the source, leaking neither."""

    try:
        if pending is not None and not pending.done():
            pending.cancel()
            # ``asyncio.wait`` never re-raises the awaited task's own outcome,
            # so a cancelled pull cannot be mistaken here for this coroutine
            # being cancelled -- an outward cancellation still propagates, and
            # the pull is already cancelled by then, so nothing is left pending.
            await asyncio.wait((pending,))
    finally:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except RuntimeError:
                # The pull was cancelled without the loop resuming the source,
                # which leaves the async generator flagged as still running.
                # It holds no further frames for us; garbage collection
                # finalises it.
                pass


async def with_sse_heartbeat(
    source: AsyncIterator[str],
    *,
    interval_seconds: float,
) -> AsyncIterator[str]:
    """Pass ``source`` through, emitting a comment frame during silent gaps.

    A heartbeat is written only once ``interval_seconds`` have passed with no
    chunk from ``source``; every real chunk restarts that clock, so a chatty
    source never sees one. Chunks are forwarded unchanged and in order,
    exceptions raised by ``source`` propagate, and closing this generator
    cancels the in-flight read and closes ``source``.

    ``interval_seconds`` of zero or less disables the heartbeat and leaves a
    plain pass-through, with no extra task per chunk.
    """

    iterator = source.__aiter__()
    pending: asyncio.Future[Any] | None = None
    try:
        if interval_seconds <= 0:
            async for chunk in iterator:
                yield chunk
            return

        while True:
            if pending is None:
                # Created once per *chunk*, never once per heartbeat. Starting
                # a second read while one is in flight would either raise
                # (the source is already running) or claim a chunk that the
                # abandoned read then drops -- this single carried-over future
                # is what makes the wrapper lossless.
                pending = asyncio.ensure_future(_pull(iterator))
            done, _ = await asyncio.wait((pending,), timeout=interval_seconds)
            if not done:
                # A timed-out ``asyncio.wait`` leaves its task running, so the
                # same read is carried into the next iteration untouched.
                yield SSE_HEARTBEAT_FRAME
                continue
            settled, pending = pending, None
            chunk = settled.result()
            if chunk is _EXHAUSTED:
                return
            yield chunk
    finally:
        await _shutdown(pending, iterator)
