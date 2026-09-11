"""One capture owner for every Chromium capture of authored slide HTML.

A capture is keyed by ``(expanded-HTML digest, capture profile)`` and is a pure
function of that key: the same bytes rendered under the same policy produce the
same PNG and the same telemetry.  Reuse is therefore a **cache**, never a
verdict, which is why the owner runs in *every* gate mode -- including the
default ``shadow`` -- and why a slide index is needed only to *admit* a capture,
not to reuse one.  Sites that cannot name their slide still route through here
and still coalesce; they simply contribute no admission receipt.

The fitter's verification render (``fit=False`` over HTML that ``fit=True``
changed) carries a different digest *and* a different profile, so it stays the
legitimate second capture the design allows rather than becoming a cache hit.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from services.authored_admission_context import canonical_json, digest_html
from services.authored_admission_session import current_session

#: Captures retain PNG bytes, so the coalescing map is bounded: a deck renders
#: far more documents than it can hold at once, and dropping an entry costs at
#: most one re-render.  Only completed entries are evicted, so the attempt-exit
#: cancellation of in-flight captures still sees every task it started.
RETAINED_CAPTURES = 32


@dataclass
class CaptureLedger:
    """Per-attempt capture accounting, and the PNG-free half of the cache.

    ``metrics`` outlives :data:`RETAINED_CAPTURES` eviction because telemetry is
    small and every deck-wide readback wants it after the PNGs are long gone.
    """

    metrics: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    captures: dict[int | None, int] = field(default_factory=dict)
    reuses: dict[int | None, int] = field(default_factory=dict)
    #: How many callers are still waiting on each in-flight capture.
    waiters: dict[tuple[str, str], int] = field(default_factory=dict)

    def record(self, index: int | None, *, reused: bool) -> None:
        sink = self.reuses if reused else self.captures
        sink[index] = sink.get(index, 0) + 1

    def leave(self, key: tuple[str, str]) -> int:
        remaining = self.waiters.get(key, 1) - 1
        if remaining > 0:
            self.waiters[key] = remaining
        else:
            self.waiters.pop(key, None)
        return remaining


def capture_ledger(session) -> CaptureLedger:
    """The attempt's ledger, created on first use.

    It hangs off the session object rather than a ``ContextVar`` because slides
    are captured inside child tasks, whose context is a copy: a counter set
    there would never reach the receipt the parent writes.
    """

    ledger = getattr(session, "authored_capture_ledger", None)
    if ledger is None:
        ledger = CaptureLedger()
        session.authored_capture_ledger = ledger
    return ledger


def capture_profile(kwargs: Mapping[str, Any]) -> str:
    """Everything about the render that changes the pixels, digested.

    ``timeout`` only bounds the work, so two calls that differ by it are the
    same capture; fit policy, deck scope and ``fresh`` verification are not.
    """

    return digest_html(canonical_json({key: value for key, value in kwargs.items() if key != "timeout"}))


def _retain(session, key) -> None:
    task = session.captures.pop(key, None)
    if task is None:
        return
    session.captures[key] = task
    for stale, pending in list(session.captures.items()):
        if len(session.captures) <= RETAINED_CAPTURES:
            return
        if stale != key and pending.done():
            session.captures.pop(stale, None)


def _admit(session, *, index: int, html: str, result, profile: str, reused: bool) -> None:
    """Bind the capture to the slide it was taken for; enforce mode only.

    Shadow keeps the cache and skips this: an admission is a verdict, and the
    rollout's contract is that shadow observes producers without adding any.
    """

    context = replace(session.context(index, captured=True), capture_profile=profile)
    admission = session.admit(
        context, html, metrics=getattr(result, "browser_metrics", None), png=getattr(result, "png", None)
    )
    session.capture_receipts[(index, digest_html(html))] = replace(
        admission.receipt,
        capture_reused=reused,
        persistence_class="evidence",
    )


async def capture_once(html: str, *, index: int | None = None, capture, **kwargs: Any):
    """Render ``html`` once per ``(digest, profile)`` for the whole attempt."""

    session = current_session()
    if session is None:
        return await capture(html, **kwargs)
    profile = capture_profile(kwargs)
    key = (digest_html(html), profile)
    ledger = capture_ledger(session)
    pending = session.captures.get(key)
    reused = pending is not None
    if pending is None:
        pending = asyncio.create_task(capture(html, **kwargs))
        session.captures[key] = pending
    ledger.waiters[key] = ledger.waiters.get(key, 0) + 1
    try:
        result = await asyncio.shield(pending)
    except BaseException:
        remaining = ledger.leave(key)
        if pending.done():
            # A failed capture is not cached: the next caller renders again.
            session.captures.pop(key, None)
        elif remaining <= 0:
            # The caller's deadline expired and nobody else is consuming this
            # render, so it must not keep a Chromium process past that deadline.
            session.captures.pop(key, None)
            pending.cancel()
        raise
    ledger.leave(key)
    _retain(session, key)
    # A capture adapter may return any payload; only a ``RenderCapture`` carries
    # a fitted document and telemetry, and only those two fields are read here.
    expanded = getattr(result, "fitted_html", None) or html
    metrics = getattr(result, "browser_metrics", None)
    if metrics is not None:
        ledger.metrics[digest_html(expanded)] = metrics
    ledger.record(index, reused=reused)
    if reused:
        session.capture_reuses += 1
    if index is not None and session.contexts and session.mode == "enforce":
        _admit(session, index=index, html=expanded, result=result, profile=profile, reused=reused)
    return result


async def capture_png_once(html: str, *, index: int | None = None, render, read_metrics):
    """A PNG-only renderer adapted to the owner, sharing its default profile."""

    session = current_session()
    if session is None:
        return await render(html)

    async def capture(value: str):
        from utils.slide_capture_result import RenderCapture

        png = await render(value)
        return RenderCapture.from_payload(
            html_digest=digest_html(value),
            png=png,
            browser_metrics=read_metrics(value),
            runtime_counters={},
        )

    return (await capture_once(html, index=index, capture=capture)).png


async def captured_metrics_or_existing(html: str, *, index: int | None = None, ensure):
    """Telemetry for exactly these bytes, from the owner when it has them."""

    session = current_session()
    if session is not None:
        ledger = capture_ledger(session)
        metrics = ledger.metrics.get(digest_html(html))
        if metrics is not None:
            ledger.record(index, reused=True)
            session.capture_reuses += 1
            return metrics
    return await ensure(html)


def capture_ownership_receipt() -> dict[str, Any] | None:
    """Per-slide capture counts for the live series; ``None`` without a session."""

    session = current_session()
    if session is None:
        return None
    ledger = capture_ledger(session)
    indices = sorted(index for index in {*ledger.captures, *ledger.reuses} if index is not None)
    return {
        "mode": session.mode,
        "captureCount": sum(ledger.captures.values()),
        "captureReuseCount": sum(ledger.reuses.values()),
        "retainedCaptures": len(session.captures),
        "slides": [
            {
                "index": index,
                "captureCount": ledger.captures.get(index, 0),
                "captureReuseCount": ledger.reuses.get(index, 0),
            }
            for index in indices
        ],
        # Capture sites that cannot name a slide still coalesce; they are
        # counted here so the deck total stays the real number of renders.
        "unattributed": {
            "captureCount": ledger.captures.get(None, 0),
            "captureReuseCount": ledger.reuses.get(None, 0),
        },
    }


__all__ = [
    "CaptureLedger",
    "RETAINED_CAPTURES",
    "capture_ledger",
    "capture_once",
    "capture_ownership_receipt",
    "capture_png_once",
    "capture_profile",
    "captured_metrics_or_existing",
]
