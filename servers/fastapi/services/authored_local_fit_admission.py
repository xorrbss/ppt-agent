"""Restore a rejected local-fit candidate before its tuple is accepted."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import Any

from services.authored_local_fit_evidence import (
    AcceptedLocalFit,
    AuthoredLocalFitRejected,
    accept_unchanged_capture,
    accept_verified_capture,
    retain_publishable_unresolved_capture,
    validate_changed_capture,
)
from utils.authored_dom_categories import BLOCKING_SAFETY_CATEGORIES
from utils.slide_capture_result import RenderCapture


def local_fit_failure_reason(error: Exception) -> str:
    if isinstance(error, AuthoredLocalFitRejected):
        return error.reason
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "deadline_exceeded"
    message = str(error).lower()
    if any(
        token in message
        for token in ("font", "observation unavailable", "metrics", "telemetry")
    ):
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
        if isinstance(issue, Mapping)
        and issue.get("category") in BLOCKING_SAFETY_CATEGORIES
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
) -> AcceptedLocalFit:
    """Select verification or original restoration before accepting one tuple."""

    async def admit_candidate() -> AcceptedLocalFit:
        report = capture.fit_report or {}
        changed = bool(report.get("changed")) or bool(
            capture.fitted_html and capture.fitted_html != original_html
        )
        if not changed:
            return accept_unchanged_capture(
                capture, original_html=original_html, policy=policy
            )
        validate_changed_capture(capture, original_html=original_html, policy=policy)
        _, failure = evaluate(
            html=capture.fitted_html, browser_metrics=capture.browser_metrics
        )
        rejected = geometry_failures(failure)
        if rejected:
            raise AuthoredLocalFitRejected("whole_page_geometry_unresolved")
        queued_at = time.monotonic()
        original_reserve = max(1.0, verify_seconds / 2)
        deadline = min(
            slide_deadline - original_reserve,
            queued_at + max(0.001, verify_seconds - original_reserve),
        )
        await asyncio.wait_for(
            render_gate.acquire(), timeout=max(0.001, deadline - queued_at)
        )
        try:
            started_at = time.monotonic()
            remaining = max(0.001, deadline - started_at)
            async with asyncio.timeout(remaining):
                second = await render(
                    capture.fitted_html,
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
        _, failure = evaluate(
            html=capture.fitted_html, browser_metrics=second.browser_metrics
        )
        if geometry_failures(failure):
            raise AuthoredLocalFitRejected("reconstruction_mismatch")
        return accept_verified_capture(
            capture,
            second,
            original_html=original_html,
            policy=policy,
            verify_queued_at=queued_at,
            verify_started_at=started_at,
            verify_finished_at=finished_at,
        )

    try:
        return await admit_candidate()
    except Exception as error:
        return await preserve_original_capture(
            original_html=original_html,
            policy=policy,
            reason=local_fit_failure_reason(error),
            rejected_report=capture.fit_report,
            render=render,
            render_gate=render_gate,
            slide_deadline=slide_deadline,
            verify_seconds=verify_seconds,
            deck_scope=deck_scope,
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
) -> AcceptedLocalFit:
    """Render the independent original tuple reserved for failed local fit."""

    queued_at = time.monotonic()
    deadline = min(slide_deadline, queued_at + verify_seconds)
    acquired = False
    try:
        await asyncio.wait_for(
            render_gate.acquire(), timeout=max(0.001, deadline - queued_at)
        )
        acquired = True
        remaining = max(0.001, deadline - time.monotonic())
        async with asyncio.timeout(remaining):
            original = await render(
                original_html,
                timeout=max(1, min(60, int(remaining))),
                fit=False,
                fit_policy=dict(policy),
                fresh=True,
                deck_scope=deck_scope,
            )
        return retain_publishable_unresolved_capture(
            original,
            original_html=original_html,
            policy=policy,
            unresolved_reasons=(reason,),
            rejected_report=rejected_report,
        )
    except Exception as error:
        raise AuthoredLocalFitRejected("original_output_unavailable") from error
    finally:
        if acquired:
            render_gate.release()
