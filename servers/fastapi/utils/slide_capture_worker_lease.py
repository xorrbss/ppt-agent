"""Per-deck lease over the isolated authored renderer worker.

Groups authored slide renders for one deck behind a single logical lease
instead of reasoning about worker/Chromium bring-up per render. Every render
still goes through ``render_admitted_htmls_over_pipe`` semantics for exactly
one admitted document -- a fresh CDP ``BrowserContext``, target, and session
per slide (see ``utils/slide_capture_pipe_render.py``) -- so the per-render
isolation attestation (network-none, fresh profile, fresh target) is
unchanged; only the bookkeeping around when a worker session opens and closes
is new.

A lease works in one of two modes.

* **Session mode** (``session_factory`` given) is the production path. The
  lease owns a small pool of framed worker sessions
  (``utils.slide_capture_worker_session``); a session keeps one worker process
  and one Chromium alive across its renders, so a deck pays one cold start per
  pool slot rather than one per slide. Admission is round-robin over the pool,
  so up to ``pool_size`` slides of a deck render concurrently while each
  individual worker still handles one request at a time.
* **Direct mode** (plain ``render_fn``) keeps the pre-framing behaviour: one
  render at a time for the deck, each performed by whatever the caller passed
  (in production, one isolated-worker subprocess exchange; in tests, a fake).

Both modes close on the same three signals: an idle timeout, a render-count
ceiling, and retire-on-error -- a failed exchange leaves no guarantee that the
worker/Chromium is still usable, so the lease is retired rather than handing
the next slide a poisoned session.
"""
# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Tuple

from utils.get_env_programme import (
    get_authored_renderer_lease_idle_seconds,
    get_authored_renderer_lease_max_renders,
)

logger = logging.getLogger(__name__)

RenderFn = Callable[[str, int], Awaitable[Tuple[bytes, Optional[Mapping[str, Any]]]]]
SessionFactory = Callable[[], Any]


class RendererLeaseClosedError(RuntimeError):
    """A render was attempted on a lease that already closed."""


class _PoolSlot:
    """One worker session of a lease's pool, plus its single-flight lock."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.session: Any = None


class RendererWorkerLease:
    """One logical per-deck renderer worker session (or small pool of them)."""

    def __init__(
        self,
        deck_scope: str,
        render_fn: RenderFn,
        *,
        idle_seconds: int | None = None,
        max_renders: int | None = None,
        session_factory: SessionFactory | None = None,
        pool_size: int = 1,
    ) -> None:
        if not deck_scope:
            raise ValueError("deck_scope must be a non-empty opaque string")
        self.deck_scope = deck_scope
        self._render_fn = render_fn
        self._session_factory = session_factory
        self._idle_seconds = (
            get_authored_renderer_lease_idle_seconds()
            if idle_seconds is None
            else idle_seconds
        )
        self._max_renders = (
            get_authored_renderer_lease_max_renders()
            if max_renders is None
            else max_renders
        )
        self._lock = asyncio.Lock()
        self._pool: List[_PoolSlot] = [
            _PoolSlot() for _ in range(max(1, pool_size) if session_factory else 1)
        ]
        self._next_slot = 0
        self._render_count = 0
        self._closed = False
        self._idle_handle: asyncio.TimerHandle | None = None
        self._opened_monotonic = time.monotonic()
        self.on_close: Callable[["RendererWorkerLease"], None] | None = None

    @property
    def render_count(self) -> int:
        return self._render_count

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def pool_size(self) -> int:
        return len(self._pool)

    @property
    def open_session_count(self) -> int:
        return sum(1 for slot in self._pool if slot.session is not None)

    async def render(
        self, html: str, timeout: int = 60
    ) -> Tuple[bytes, Optional[Mapping[str, Any]]]:
        """Render one admitted HTML document through this lease."""

        if self._session_factory is None:
            return await self._render_direct(html, timeout)
        return await self._render_pooled(html, timeout)

    async def _render_direct(
        self, html: str, timeout: int
    ) -> Tuple[bytes, Optional[Mapping[str, Any]]]:
        async with self._lock:
            self._admit_locked()
            try:
                result = await self._render_fn(html, timeout)
            except BaseException:
                self._retire_locked("render_error")
                raise
            self._settle_locked()
            return result

    async def _render_pooled(
        self, html: str, timeout: int
    ) -> Tuple[bytes, Optional[Mapping[str, Any]]]:
        async with self._lock:
            self._admit_locked()
            slot = self._pool[self._next_slot % len(self._pool)]
            self._next_slot = (self._next_slot + 1) % len(self._pool)
        async with slot.lock:
            if self._closed:
                raise RendererLeaseClosedError(
                    f"renderer worker lease for deck_scope={self.deck_scope!r} is closed"
                )
            session = slot.session
            if session is None or session.is_closed:
                assert self._session_factory is not None
                session = self._session_factory()
                slot.session = session
            try:
                result = await session.render(html, timeout)
            except BaseException:
                slot.session = None
                await _close_session(session)
                async with self._lock:
                    self._retire_locked("render_error")
                raise
            if session.is_closed:
                # The session hit its own per-process request ceiling.
                slot.session = None
        async with self._lock:
            self._settle_locked()
        return result

    def _admit_locked(self) -> None:
        if self._closed:
            raise RendererLeaseClosedError(
                f"renderer worker lease for deck_scope={self.deck_scope!r} is closed"
            )
        self._cancel_idle_timer()

    def _settle_locked(self) -> None:
        self._render_count += 1
        if self._render_count >= self._max_renders:
            logger.info(
                "renderer_lease_closing deck_scope=%s reason=max_renders count=%d",
                self.deck_scope,
                self._render_count,
            )
            self._close_locked()
        else:
            self._arm_idle_timer()

    def _retire_locked(self, reason: str) -> None:
        logger.warning(
            "renderer_lease_closing deck_scope=%s reason=%s", self.deck_scope, reason
        )
        self._close_locked()

    def _arm_idle_timer(self) -> None:
        if self._closed or self._idle_seconds <= 0:
            return
        loop = asyncio.get_event_loop()
        self._idle_handle = loop.call_later(self._idle_seconds, self._on_idle_timeout)

    def _cancel_idle_timer(self) -> None:
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

    def _on_idle_timeout(self) -> None:
        logger.info(
            "renderer_lease_closing deck_scope=%s reason=idle_timeout idle_seconds=%d",
            self.deck_scope,
            self._idle_seconds,
        )
        self._close_locked()

    def _close_locked(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_idle_timer()
        self._detach_sessions()
        if self.on_close is not None:
            self.on_close(self)

    def _detach_sessions(self) -> None:
        """Hand every open worker session to a background close.

        ``_close_locked`` also runs from the synchronous idle-timer callback,
        which cannot await; the sessions must still be closed so no worker
        process outlives its lease.
        """

        for slot in self._pool:
            session, slot.session = slot.session, None
            if session is None:
                continue
            try:
                asyncio.get_event_loop().create_task(_close_session(session))
            except RuntimeError:  # pragma: no cover - no running loop at teardown
                pass

    async def close(self) -> None:
        """Close the lease and its worker sessions. Idempotent."""

        async with self._lock:
            sessions = [slot.session for slot in self._pool if slot.session is not None]
            for slot in self._pool:
                slot.session = None
            was_open = not self._closed
            self._closed = True
            self._cancel_idle_timer()
            if was_open and self.on_close is not None:
                self.on_close(self)
        for session in sessions:
            await _close_session(session)


async def _close_session(session: Any) -> None:
    try:
        await session.close()
    except Exception:
        logger.warning("renderer_worker_session_close_failed", exc_info=True)


_leases: Dict[str, RendererWorkerLease] = {}
_registry_lock = asyncio.Lock()


async def acquire_lease(
    deck_scope: str,
    render_fn: RenderFn,
    *,
    idle_seconds: int | None = None,
    max_renders: int | None = None,
    session_factory: SessionFactory | None = None,
    pool_size: int = 1,
) -> RendererWorkerLease:
    """Return the open lease for ``deck_scope``, creating one if needed."""

    async with _registry_lock:
        existing = _leases.get(deck_scope)
        if existing is not None and not existing.is_closed:
            return existing
        lease = RendererWorkerLease(
            deck_scope,
            render_fn,
            idle_seconds=idle_seconds,
            max_renders=max_renders,
            session_factory=session_factory,
            pool_size=pool_size,
        )
        lease.on_close = lambda closed_lease: _leases.pop(deck_scope, None)
        _leases[deck_scope] = lease
        return lease


async def close_all_leases() -> None:
    """Close every currently open lease. Used by shutdown and tests."""

    async with _registry_lock:
        leases = list(_leases.values())
    for lease in leases:
        await lease.close()


__all__ = [
    "RendererLeaseClosedError",
    "RendererWorkerLease",
    "acquire_lease",
    "close_all_leases",
]
