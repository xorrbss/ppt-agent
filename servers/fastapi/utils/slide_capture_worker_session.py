"""Parent-side framed session over one isolated renderer worker process.

One instance owns one worker subprocess and exchanges length-prefixed frames
with it (``utils.renderer_frame_codec``), so N renders of one deck cost one
process and one Chromium cold start instead of N. Every render still crosses
the same non-root / read-only / network-none boundary, is admitted twice (here
and inside the worker), and is bound to its response by source digest; the
framed protocol adds a ``request_ordinal`` so a response can also be bound to
its position in the session.

Failure policy is retire-on-error: any protocol, timeout, or render failure
kills the process tree and marks the session closed. A worker that failed once
carries no guarantee that its Chromium is still usable, and handing the next
slide a poisoned browser is worse than paying another cold start.
"""
# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import atexit
import os
import signal
import subprocess
from typing import Any, Mapping

from utils.process_kill_on_parent_death import bind_to_parent_lifetime
from utils.renderer_frame_codec import (
    DEFAULT_MAX_REQUESTS_PER_PROCESS,
    RendererFrameError,
    clamp_max_requests,
    encode_framed_request,
    read_frame_async,
)
from utils.renderer_isolation import MAX_RESPONSE_BYTES
from utils.renderer_worker_policy import build_worker_environment, worker_command
from utils.renderer_worker_response import (
    RendererIsolationError,
    assert_response_identity,
    decode_worker_response,
    response_ordinal,
    source_digest_for,
)
from models.canvas_defaults import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX
from constants.timeouts import CAPTURE_WORKER_TERMINATE_GRACE_SECONDS

# Wait for the worker to exit on its own after its stdin is closed.
_WORKER_CLEAN_EXIT_TIMEOUT_SECONDS = 10



#: Worker processes this process has started and not yet reaped. A hard exit
#: cannot await ``close()``, and an orphaned worker holds a Chromium (in
#: production, a container) open, so the exit hook kills whatever is left.
_live_processes: set[Any] = set()


def _kill_live_workers_at_exit() -> None:
    for process in list(_live_processes):
        try:
            if process.returncode is None:
                process.kill()
        except Exception:
            pass
        _live_processes.discard(process)


atexit.register(_kill_live_workers_at_exit)


async def terminate_process_tree(process: Any) -> None:
    """Kill a renderer worker and every child it spawned, then reap it."""

    if process.returncode is None:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=CAPTURE_WORKER_TERMINATE_GRACE_SECONDS)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, PermissionError):
            pass
    await process.wait()


class RendererWorkerSession:
    """A reusable framed exchange with one isolated renderer worker process."""

    def __init__(
        self, *, max_requests: int | None = None,
        width_px: int = DEFAULT_CANVAS_WIDTH_PX, height_px: int = DEFAULT_CANVAS_HEIGHT_PX,
    ) -> None:
        self._max_requests = clamp_max_requests(
            DEFAULT_MAX_REQUESTS_PER_PROCESS if max_requests is None else max_requests
        )
        self._process: Any = None
        self._served = 0
        self._closed = False
        self._width_px = int(width_px)
        self._height_px = int(height_px)

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def render_count(self) -> int:
        return self._served

    @property
    def is_started(self) -> bool:
        return self._process is not None

    @property
    def max_requests(self) -> int:
        return self._max_requests

    async def _start(self) -> None:
        command = worker_command()
        environment = build_worker_environment()
        kwargs: dict[str, Any] = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.DEVNULL,
            "env": environment,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
        self._process = await asyncio.create_subprocess_exec(*command, **kwargs)
        # The exit hook below only runs on a graceful exit. Binding the worker
        # to this process's lifetime makes the OS reap the worker -- and the
        # Chromium it owns -- even when the owner is force-killed.
        bind_to_parent_lifetime(self._process)
        _live_processes.add(self._process)

    async def render(  # noqa: ASYNC109
        self, html: str, timeout: int = 60, *,
        width_px: int | None = None, height_px: int | None = None,
    ) -> tuple[bytes, Mapping[str, Any] | None]:
        """Render one authored document over this session's worker process."""

        if self._closed:
            raise RendererIsolationError("authored renderer worker session is closed")
        from utils.slide_capture import _admit_render_source, _cache_browser_metrics

        admitted = _admit_render_source(html, materialize_fonts=False)
        width_px = self._width_px if width_px is None else int(width_px)
        height_px = self._height_px if height_px is None else int(height_px)
        first = self._process is None
        if first:
            await self._start()
        request = encode_framed_request(
            html, timeout, max_requests=self._max_requests if first else None,
            width_px=width_px, height_px=height_px,
        )
        try:
            payload = await asyncio.wait_for(
                self._exchange(request), timeout=max(25, timeout + 25)
            )
        except asyncio.CancelledError:
            await self.close()
            raise
        except asyncio.TimeoutError as exc:
            await self.close()
            raise RendererIsolationError("authored renderer worker timed out") from exc
        except (RendererFrameError, OSError) as exc:
            await self.close()
            raise RendererIsolationError("authored renderer framing failed") from exc
        except BaseException:
            await self.close()
            raise
        expected_ordinal = self._served + 1
        try:
            png, metrics = decode_worker_response(payload, allow_ordinal=True)
            if response_ordinal(payload) != expected_ordinal:
                raise RendererIsolationError("authored renderer response ordinal mismatch")
            assert_response_identity(payload, html)
            if admitted.source_digest != source_digest_for(html):
                raise RendererIsolationError("authored renderer response identity mismatch")
        except BaseException:
            await self.close()
            raise
        self._served = expected_ordinal
        if metrics is not None:
            _cache_browser_metrics(
                html, metrics, width_px=width_px, height_px=height_px
            )
        if self._served >= self._max_requests:
            # The worker exits on its own after this many renders; close in
            # step with it so the next render opens a fresh process.
            await self.close()
        return png, metrics

    async def _exchange(self, request: bytes) -> bytes:
        process = self._process
        assert process is not None and process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(request)
        await process.stdin.drain()
        payload = await read_frame_async(process.stdout, limit=MAX_RESPONSE_BYTES)
        if payload is None:
            raise RendererIsolationError("authored renderer worker closed its stream")
        return payload

    async def close(self) -> None:
        """Close the worker's stdin, then reap it. Idempotent."""

        self._closed = True
        process, self._process = self._process, None
        if process is None:
            return
        _live_processes.discard(process)
        try:
            if process.stdin is not None and not process.stdin.is_closing():
                process.stdin.close()
                try:
                    await process.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            await asyncio.wait_for(process.wait(), timeout=_WORKER_CLEAN_EXIT_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, ProcessLookupError, OSError):
            await terminate_process_tree(process)
        except asyncio.CancelledError:
            await terminate_process_tree(process)
            raise


__all__ = ["RendererWorkerSession", "terminate_process_tree"]
