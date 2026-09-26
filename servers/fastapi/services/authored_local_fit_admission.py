"""Restore a rejected local-fit candidate before its tuple is accepted."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from services.authored_admission_capture import capture_once
from services.authored_local_fit_evidence import (
    AcceptedLocalFit,
    AuthoredLocalFitRejected,
    accept_original_capture,
    accept_unchanged_capture,
    accept_verified_capture,
    retain_publishable_unresolved_capture,
    validate_changed_capture,
)
from utils.authored_dom_categories import BLOCKING_SAFETY_CATEGORIES
from utils.slide_capture_result import RenderCapture
from services.authored_local_fit_policy import html_digest


def _capture_error(error: Exception, *, phase: str, prefix: str = "original") -> AuthoredLocalFitRejected:
    message = str(error).lower()
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or "timed out" in message:
        reason = f"{phase}_timeout"
    elif any(
        word in message
        for word in ("browser closed", "target closed", "browser has been closed", "browser disconnected")
    ):
        reason = "browser_closed"
    elif isinstance(error, (AuthoredLocalFitRejected, ValueError, TypeError)) or any(
        word in message for word in ("digest_mismatch", "html_changed", "change_unreported")
    ):
        reason = "invalid_output"
    else:
        reason = "capture_failed"
    classified = AuthoredLocalFitRejected(f"{prefix}_{reason}")
    classified.cause_reason = getattr(error, "reason", type(error).__name__)
    return classified


async def _bounded_capture(
    *,
    original_html: str,
    policy: Mapping[str, Any],
    fit: bool,
    render: Callable[..., Any],
    render_gate: asyncio.Semaphore,
    deadline: float,
    deck_scope: str | None,
    slide_index: int | None,
) -> RenderCapture:
    remaining = deadline - time.monotonic()
    prefix = "candidate" if fit else "original"
    if remaining <= 0:
        raise AuthoredLocalFitRejected(f"{prefix}_gate_timeout")
    try:
        await asyncio.wait_for(render_gate.acquire(), timeout=remaining)
    except Exception as error:
        raise _capture_error(error, phase="gate", prefix=prefix) from error
    try:
        remaining = deadline - time.monotonic()
        async with asyncio.timeout(max(0.001, remaining)):
            return await capture_once(
                original_html,
                index=slide_index,
                capture=render,
                defer_fit_proposal_admission=fit,
                timeout=max(1, min(60, int(remaining))),
                fit=fit,
                fit_policy=dict(policy),
                fresh=True,
                deck_scope=deck_scope,
            )
    except Exception as error:
        raise _capture_error(error, phase="capture", prefix=prefix) from error
    finally:
        render_gate.release()


def _retained_original(
    original: RenderCapture,
    *,
    original_html: str,
    policy: Mapping[str, Any],
    reason: str,
    rejected_report: Mapping[str, Any] | None = None,
) -> AcceptedLocalFit:
    # A restored checkpoint cannot nominate its own HTML or policy identity.
    if original.prepared_html != original_html or original.html_digest != html_digest(original_html):
        raise AuthoredLocalFitRejected("original_invalid_output")
    try:
        return retain_publishable_unresolved_capture(
            original,
            original_html=original_html,
            policy=policy,
            unresolved_reasons=(reason,),
            rejected_report=rejected_report,
        )
    except Exception as error:
        raise _capture_error(error, phase="capture") from error


async def capture_original_first(
    *,
    original_html: str,
    policy: Mapping[str, Any],
    evaluate: Callable[..., tuple[Any, Mapping[str, Any] | None]],
    render: Callable[..., Any],
    render_gate: asyncio.Semaphore,
    slide_deadline: float,
    verify_seconds: float,
    deck_scope: str | None,
    slide_index: int | None = None,
    persist_original: Callable[[RenderCapture, Mapping[str, Any]], Awaitable[None]] | None = None,
    restored_original: RenderCapture | None = None,
) -> AcceptedLocalFit:
    """Persist an independent original before spending time on optional fitting."""

    original = restored_original
    if original is None:
        for attempt in range(2):
            try:
                original = await _bounded_capture(
                    original_html=original_html,
                    policy=policy,
                    fit=False,
                    render=render,
                    render_gate=render_gate,
                    deadline=min(slide_deadline, time.monotonic() + 60),
                    deck_scope=deck_scope,
                    slide_index=slide_index,
                )
                break
            except AuthoredLocalFitRejected as error:
                if (
                    attempt
                    or error.reason not in {"original_capture_timeout", "original_browser_closed"}
                    or slide_deadline - time.monotonic() < 1
                ):
                    raise
        # Preparation is completed by the capture owner before rendering.
        original_html = original.prepared_html or original_html
        policy = original.prepared_fit_policy or policy
    _retained_original(
        original,
        original_html=original_html,
        policy=policy,
        reason="original_quality_pending",
    )
    quality, failure = evaluate(html=original_html, browser_metrics=original.browser_metrics)
    if failure:
        issues = failure.get("issues")
        allowed = BLOCKING_SAFETY_CATEGORIES | {"capacity", "font"}
        if (
            not isinstance(issues, (list, tuple))
            or not issues
            or any(not isinstance(issue, Mapping) or issue.get("category") not in allowed for issue in issues)
        ):
            raise AuthoredLocalFitRejected("original_safety_failed")
    if persist_original is not None and restored_original is None:
        await persist_original(original, policy)
    telemetry_status = (
        quality.get("telemetry_status") if isinstance(quality, Mapping) else getattr(quality, "telemetry_status", None)
    )
    if telemetry_status is not None and telemetry_status != "available":
        raise AuthoredLocalFitRejected("original_quality_unavailable")
    if quality is not None and not failure:
        try:
            return accept_original_capture(original, original_html=original_html, policy=policy)
        except AuthoredLocalFitRejected:
            # Pixel identity was validated above. Incomplete font/fit evidence
            # disables this optimization; it does not discard the original.
            pass
    try:
        candidate = await _bounded_capture(
            original_html=original_html,
            policy=policy,
            fit=True,
            render=render,
            render_gate=render_gate,
            deadline=min(slide_deadline, time.monotonic() + max(1.0, verify_seconds)),
            deck_scope=deck_scope,
            slide_index=slide_index,
        )
        accepted = await admit_local_fit_capture(
            candidate,
            original_html=original_html,
            policy=policy,
            evaluate=evaluate,
            render=render,
            render_gate=render_gate,
            slide_deadline=slide_deadline,
            verify_seconds=verify_seconds,
            deck_scope=deck_scope,
            slide_index=slide_index,
            retained_original=original,
        )
        if quality is None and failure is None:
            return _retained_original(
                original,
                original_html=original_html,
                policy=policy,
                reason="quality_evaluation_unavailable",
            )
        return accepted
    except Exception as error:
        return _retained_original(
            original,
            original_html=original_html,
            policy=policy,
            reason=local_fit_failure_reason(error),
        )


def local_fit_failure_reason(error: Exception) -> str:
    if isinstance(error, AuthoredLocalFitRejected):
        return error.reason
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "deadline_exceeded"
    message = str(error).lower()
    if any(token in message for token in ("font", "observation unavailable", "metrics", "telemetry")):
        return "unverifiable_font_size"
    return "repair_exception"


def geometry_failures(failure: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Keep the existing whole-page gate's blocking geometry classification."""

    issues = (failure or {}).get("issues")
    if not isinstance(issues, (list, tuple)):
        return []
    return [
        dict(issue)
        for issue in issues
        if isinstance(issue, Mapping) and issue.get("category") in BLOCKING_SAFETY_CATEGORIES
    ]


async def admit_local_fit_capture(
    capture: RenderCapture,
    *,
    original_html: str,
    policy: Mapping[str, Any],
    evaluate: Callable[..., tuple[Any, Mapping[str, Any] | None]],
    render: Callable[..., Any],
    render_gate: asyncio.Semaphore,
    slide_deadline: float,
    verify_seconds: float,
    deck_scope: str | None,
    slide_index: int | None = None,
    retained_original: RenderCapture | None = None,
) -> AcceptedLocalFit:
    """Select verification or original restoration before accepting one tuple."""

    async def admit_candidate() -> AcceptedLocalFit:
        prepared_original_html = capture.prepared_html or original_html
        prepared_policy = capture.prepared_fit_policy or policy
        report = capture.fit_report or {}
        changed = (
            bool(report.get("fit_proposal"))
            or bool((capture.browser_metrics or {}).get("local_fit_candidate_only"))
            or (
                bool(report.get("changed"))
                or bool(capture.fitted_html and capture.fitted_html != prepared_original_html)
            )
        )
        if not changed:
            if retained_original is not None:
                _, failure = evaluate(html=prepared_original_html, browser_metrics=capture.browser_metrics)
                if failure:
                    raise AuthoredLocalFitRejected("reconstruction_mismatch")
            return accept_unchanged_capture(capture, original_html=prepared_original_html, policy=prepared_policy)
        validate_changed_capture(capture, original_html=prepared_original_html, policy=prepared_policy)
        # Candidate metrics describe the proposal run; quality is decided on the final capture.
        # Fit edits may append attributes out of canonical order. Keep the
        # first tuple intact and independently render the final policy bytes.
        queued_at = time.monotonic()
        original_reserve = 0.0 if retained_original is not None else max(1.0, verify_seconds / 2)
        deadline = min(
            slide_deadline - original_reserve,
            queued_at + max(0.001, verify_seconds - original_reserve),
        )
        await asyncio.wait_for(render_gate.acquire(), timeout=max(0.001, deadline - queued_at))
        try:
            started_at = time.monotonic()
            remaining = max(0.001, deadline - started_at)
            async with asyncio.timeout(remaining):
                # The verification render carries the fitted digest and a
                # fit-disabled profile, so the owner keeps it as the design's
                # legitimate second capture instead of serving the first one.
                second = await capture_once(
                    capture.fitted_html,
                    index=slide_index,
                    capture=render,
                    timeout=max(1, min(60, int(remaining))),
                    fit=False,
                    fit_policy={
                        **dict(policy),
                        "baseline": dict(report.get("baseline") or {}),
                    },
                    fresh=True,
                    deck_scope=deck_scope,
                )
            finished_at = time.monotonic()
        finally:
            render_gate.release()
        _, failure = evaluate(html=second.prepared_html, browser_metrics=second.browser_metrics)
        if failure:
            raise AuthoredLocalFitRejected("reconstruction_mismatch")
        return accept_verified_capture(
            capture,
            second,
            prepared_html=second.prepared_html,
            original_html=prepared_original_html,
            policy=prepared_policy,
            verify_queued_at=queued_at,
            verify_started_at=started_at,
            verify_finished_at=finished_at,
        )

    try:
        return await admit_candidate()
    except Exception as error:
        if retained_original is not None:
            return _retained_original(
                retained_original,
                original_html=original_html,
                policy=policy,
                reason=local_fit_failure_reason(error),
                rejected_report=capture.fit_report,
            )
        return await preserve_original_capture(
            original_html=capture.prepared_html or original_html,
            policy=capture.prepared_fit_policy or policy,
            reason=local_fit_failure_reason(error),
            rejected_report=capture.fit_report,
            render=render,
            render_gate=render_gate,
            slide_deadline=slide_deadline,
            verify_seconds=verify_seconds,
            deck_scope=deck_scope,
            slide_index=slide_index,
        )


async def preserve_original_capture(
    *,
    original_html: str,
    policy: Mapping[str, Any],
    reason: str,
    render: Callable[..., Any],
    render_gate: asyncio.Semaphore,
    slide_deadline: float,
    verify_seconds: float,
    deck_scope: str | None,
    rejected_report: Mapping[str, Any] | None = None,
    slide_index: int | None = None,
) -> AcceptedLocalFit:
    """Render the independent original tuple reserved for failed local fit."""

    queued_at = time.monotonic()
    deadline = min(slide_deadline, queued_at + verify_seconds)
    acquired = False
    try:
        await asyncio.wait_for(render_gate.acquire(), timeout=max(0.001, deadline - queued_at))
        acquired = True
        remaining = max(0.001, deadline - time.monotonic())
        async with asyncio.timeout(remaining):
            original = await capture_once(
                original_html,
                index=slide_index,
                capture=render,
                timeout=max(1, min(60, int(remaining))),
                fit=False,
                fit_policy=dict(policy),
                fresh=True,
                deck_scope=deck_scope,
            )
        return retain_publishable_unresolved_capture(
            original,
            original_html=original.prepared_html or original_html,
            policy=original.prepared_fit_policy or policy,
            unresolved_reasons=(reason,),
            rejected_report=rejected_report,
        )
    except Exception as error:
        raise _capture_error(error, phase="capture" if acquired else "gate") from error
    finally:
        if acquired:
            render_gate.release()
