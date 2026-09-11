"""Capture ownership with one in-flight capture per expanded digest/profile."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from services.authored_admission_context import canonical_json, digest_html
from services.authored_admission_session import current_session


async def capture_once(html: str, *, index: int | None, capture, **kwargs: Any):
    session = current_session()
    if index is None or session is None or session.mode == "shadow" or not session.contexts:
        return await capture(html, **kwargs)
    # Timeout bounds work; fit policy/deck scope/fresh verification change the profile.
    profile = digest_html(canonical_json({key: value for key, value in kwargs.items() if key != "timeout"}))
    context = replace(session.context(index, captured=True), capture_profile=profile)
    key = (digest_html(html), profile)
    pending = session.captures.get(key)
    reused = pending is not None
    if pending is None:
        pending = asyncio.create_task(capture(html, **kwargs))
        session.captures[key] = pending
    try:
        result = await asyncio.shield(pending)
    except BaseException:
        if pending.done():
            session.captures.pop(key, None)
        raise
    expanded = result.fitted_html or html
    admission = session.admit(context, expanded, metrics=result.browser_metrics, png=result.png)
    if reused:
        session.capture_reuses += 1
    session.capture_receipts[(index, digest_html(expanded))] = replace(
        admission.receipt,
        capture_reused=reused,
        persistence_class="evidence",
    )
    return result


async def capture_png_once(html: str, *, index: int, render, read_metrics):
    session = current_session()
    if session is None or session.mode == "shadow":
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


async def captured_metrics_or_existing(html: str, *, index: int, ensure):
    session = current_session()
    if session is not None and session.mode == "enforce":
        pending = session.captures.get((digest_html(html), digest_html(canonical_json({}))))
        if pending is not None:
            return (await asyncio.shield(pending)).browser_metrics
    return await ensure(html)
