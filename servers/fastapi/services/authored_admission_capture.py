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
from services.authored_canvas import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX

#: Captures retain PNG bytes, so the coalescing map is bounded: a deck renders
#: far more documents than it can hold at once, and dropping an entry costs at
#: most one re-render.  Only completed entries are evicted, so the attempt-exit
#: cancellation of in-flight captures still sees every task it started.
RETAINED_CAPTURES = 32


def canonical_capture_html(
    html: str,
    *,
    index: int | None = None,
    _context=None,
    _normalization_mode: str | None = None,
) -> str:
    """Finish policy preparation with the context used to admit this capture."""
    from utils.llm_calls.author_slide import validate_authored_slide_html

    session = current_session()
    context = _context
    if context is None and session is not None and index is not None:
        context = session.context(index, captured=True)
    normalization_mode = _normalization_mode
    if normalization_mode is None and session is not None:
        normalization_mode = session.profile.html_normalization
    allow_illustration_slots = context.allow_illustration_slots if context is not None else False
    finalized = context.finalized if context is not None else False
    prepared = validate_authored_slide_html(
        html,
        allow_illustration_slots=allow_illustration_slots,
        finalized=finalized,
        normalization_mode=normalization_mode,
    )
    validate_authored_slide_html(
        prepared,
        allow_illustration_slots=allow_illustration_slots,
        finalized=finalized,
        read_only=True,
        normalization_mode=normalization_mode,
    )
    return prepared


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
    #: Cancelled captures remain owned until their asynchronous cleanup finishes.
    draining: set[asyncio.Task] = field(default_factory=set)

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


def _admit(session, *, context, html: str, result, profile: str, reused: bool) -> None:
    """Bind the capture to the slide it was taken for; enforce mode only.

    Shadow keeps the cache and skips this: an admission is a verdict, and the
    rollout's contract is that shadow observes producers without adding any.
    """

    context = replace(context, capture_profile=profile)
    admission = session.admit(
        context, html, metrics=getattr(result, "browser_metrics", None), png=getattr(result, "png", None)
    )
    session.capture_receipts[(context.slide_index, digest_html(html))] = replace(
        admission.receipt,
        capture_reused=reused,
        persistence_class="evidence",
    )


def _bind_prepared_html(
    result,
    prepared_html: str,
    prepared_digest: str,
    prepared_fit_policy: Mapping[str, Any] | None,
):
    """Attach the owner's exact input to the atomic capture contract."""
    from utils.slide_capture_result import RenderCapture

    if not isinstance(result, RenderCapture):
        return result
    if result.html_digest != prepared_digest:
        raise RuntimeError("authored_capture_input_digest_mismatch")
    return replace(
        result,
        prepared_html=prepared_html,
        prepared_fit_policy=(dict(prepared_fit_policy) if prepared_fit_policy is not None else None),
    )


def _prepare_capture_kwargs(html: str, prepared_html: str, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild HTML-bound local-fit policy for the owner's prepared bytes."""
    prepared_kwargs = dict(kwargs)
    policy_value = prepared_kwargs.get("fit_policy")
    if prepared_html == html or not isinstance(policy_value, Mapping):
        return prepared_kwargs
    original_digest = digest_html(html)
    if not any(policy_value.get(name) == original_digest for name in ("author_revision", "original_html_digest")):
        return prepared_kwargs

    from services.authored_local_fit_policy import POLICY_REVISION, local_fit_policy

    scale = policy_value.get("export_scale_manifest")
    scale = scale if isinstance(scale, Mapping) else {}
    prepared_digest = digest_html(prepared_html)
    rebuilt = local_fit_policy(
        author_revision=(
            prepared_digest
            if policy_value.get("author_revision") == original_digest
            else str(policy_value.get("author_revision") or "")
        ),
        original_html_digest=(
            prepared_digest
            if policy_value.get("original_html_digest") == original_digest
            else str(policy_value.get("original_html_digest") or "")
        ),
        export_width_pt=float(scale.get("width_pt", 960)),
        export_height_pt=float(scale.get("height_pt", 540)),
        viewport_width=int(scale.get("viewport_width", DEFAULT_CANVAS_WIDTH_PX)),
        viewport_height=int(scale.get("viewport_height", DEFAULT_CANVAS_HEIGHT_PX)),
        baseline=(policy_value.get("baseline") if isinstance(policy_value.get("baseline"), Mapping) else None),
        slide_index=int(policy_value.get("slide_index", 0)),
        fit_budget_ms=float(policy_value.get("fit_budget_ms", 6000)),
        policy_revision=str(policy_value.get("policy_revision") or POLICY_REVISION),
    )
    prepared_policy = dict(policy_value)
    for field_name in ("author_revision", "original_html_digest"):
        if policy_value.get(field_name) == original_digest:
            prepared_policy[field_name] = rebuilt[field_name]
    prepared_kwargs["fit_policy"] = prepared_policy
    return prepared_kwargs


async def capture_once(
    html: str, *, index: int | None = None, capture, defer_fit_proposal_admission: bool = False, **kwargs: Any
):
    """Prepare then render one immutable document per attempt capture key."""

    session = current_session()
    context = session.context(index, captured=True) if session is not None and index is not None else None
    normalization_mode = session.profile.html_normalization if session is not None else None
    prepared_html = canonical_capture_html(
        html,
        index=index,
        _context=context,
        _normalization_mode=normalization_mode,
    )
    prepared_digest = digest_html(prepared_html)
    kwargs = _prepare_capture_kwargs(html, prepared_html, kwargs)
    prepared_fit_policy = kwargs.get("fit_policy")
    if not isinstance(prepared_fit_policy, Mapping):
        prepared_fit_policy = None
    if session is None:
        result = await capture(prepared_html, **kwargs)
        return _bind_prepared_html(result, prepared_html, prepared_digest, prepared_fit_policy)
    profile = capture_profile(kwargs)
    key = (prepared_digest, profile)
    ledger = capture_ledger(session)
    pending = session.captures.get(key)
    reused = pending is not None
    if pending is None:
        pending = asyncio.create_task(capture(prepared_html, **kwargs))
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
            ledger.draining.add(pending)
            pending.add_done_callback(ledger.draining.discard)
            # Shield cleanup from a second cancellation of this waiter. The
            # attempt scope still owns the task if that cancellation arrives.
            await asyncio.shield(asyncio.gather(pending, return_exceptions=True))
        raise
    ledger.leave(key)
    _retain(session, key)
    # A capture adapter may return any payload; only a ``RenderCapture`` carries
    # a fitted document and telemetry, and only those two fields are read here.
    result = _bind_prepared_html(result, prepared_html, prepared_digest, prepared_fit_policy)
    fitted_html = getattr(result, "fitted_html", None)
    expanded = fitted_html or prepared_html
    metrics = getattr(result, "browser_metrics", None)
    if metrics is not None:
        ledger.metrics[digest_html(expanded)] = metrics
    ledger.record(index, reused=reused)
    if reused:
        session.capture_reuses += 1
    fit_report = getattr(result, "fit_report", None) or {}
    fitted_changed = isinstance(fitted_html, str) and fitted_html != prepared_html
    fit_enabled = bool(kwargs.get("fit"))
    reported_proposal = bool(
        fit_report.get("changed")
        or fit_report.get("fit_proposal")
        or (metrics is not None and metrics.get("local_fit_candidate_only"))
    )
    if fitted_changed and not fit_enabled:
        raise RuntimeError("authored_final_capture_html_changed")
    if fitted_changed and not reported_proposal:
        raise RuntimeError("authored_fit_candidate_change_unreported")
    is_fit_proposal = fit_enabled and (fitted_changed or reported_proposal or defer_fit_proposal_admission)
    # Changed fit proposals are not admitted publication evidence. The caller
    # canonicalizes and independently recaptures them through the ordinary gate.
    if index is not None and session.contexts and session.mode == "enforce" and not is_fit_proposal:
        _admit(session, context=context, html=expanded, result=result, profile=profile, reused=reused)
    return result


async def capture_png_once(html: str, *, index: int | None = None, render, read_metrics):
    """A PNG-only renderer adapted to the owner, sharing its default profile."""

    session = current_session()
    if session is None:
        prepared_html = canonical_capture_html(html, index=index)
        if prepared_html != html:
            raise RuntimeError("authored_png_capture_requires_prepared_html")
        return await render(prepared_html)

    prepared_html = canonical_capture_html(html, index=index)
    if prepared_html != html:
        raise RuntimeError("authored_png_capture_requires_prepared_html")

    async def capture(value: str):
        from utils.slide_capture_result import RenderCapture

        png = await render(value)
        return RenderCapture.from_payload(
            html_digest=digest_html(value),
            png=png,
            browser_metrics=read_metrics(value),
            runtime_counters={},
        )

    return (await capture_once(prepared_html, index=index, capture=capture)).png


async def captured_metrics_or_existing(html: str, *, index: int | None = None, ensure):
    """Telemetry for exactly these bytes, from the owner when it has them."""

    prepared_html = canonical_capture_html(html, index=index)
    if prepared_html != html:
        raise RuntimeError("authored_metrics_require_prepared_html")
    session = current_session()
    if session is not None:
        ledger = capture_ledger(session)
        metrics = ledger.metrics.get(digest_html(html))
        if metrics is not None:
            ledger.record(index, reused=True)
            session.capture_reuses += 1
            return metrics
    return await ensure(prepared_html)


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
    "canonical_capture_html",
    "capture_once",
    "capture_ownership_receipt",
    "capture_png_once",
    "capture_profile",
    "captured_metrics_or_existing",
]
