"""Startup renderer warm-up never fails application startup (A8, authored-
quality-speed programme design section 10 phase 3).

``warm_up_authored_renderer`` is called from ``api.lifespan`` before
``bootstrap_database_admin``; a broken renderer must not prevent the API from
starting, so every outcome (skipped by knob, failed render, success) is
caught internally and only logged.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from utils.renderer_warmup import warm_up_authored_renderer


def test_warmup_skips_and_logs_when_disabled(monkeypatch, caplog):
    monkeypatch.setenv("AUTHORED_RENDERER_WARMUP_ENABLED", "false")

    async def unreachable(_html, _timeout):
        raise AssertionError("render must not run when warm-up is disabled")

    with caplog.at_level(logging.INFO, logger="utils.renderer_warmup"):
        asyncio.run(warm_up_authored_renderer(render=unreachable))

    assert any(
        "renderer_warmup skipped" in record.message for record in caplog.records
    )


def test_warmup_never_raises_when_the_render_call_fails(monkeypatch, caplog):
    monkeypatch.setenv("AUTHORED_RENDERER_WARMUP_ENABLED", "true")

    async def broken(_html, _timeout):
        raise RuntimeError("no chrome/chromium found")

    with caplog.at_level(logging.INFO, logger="utils.renderer_warmup"):
        # Must complete without raising even though the injected render fails.
        asyncio.run(warm_up_authored_renderer(render=broken))

    assert any("renderer_warmup failed" in record.message for record in caplog.records)


def test_warmup_never_raises_on_cancellation_bubbling_from_the_render_call(monkeypatch):
    """A cancellation from a hung renderer must not escape as an unlogged crash.

    ``warm_up_authored_renderer`` only guards against ``Exception``, so a
    ``CancelledError`` (a ``BaseException``) legitimately propagates -- this
    documents that boundary rather than asserting it is swallowed.
    """
    monkeypatch.setenv("AUTHORED_RENDERER_WARMUP_ENABLED", "true")

    async def cancels(_html, _timeout):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(warm_up_authored_renderer(render=cancels))


def test_warmup_logs_ok_and_calls_render_with_a_small_html_document(monkeypatch, caplog):
    monkeypatch.setenv("AUTHORED_RENDERER_WARMUP_ENABLED", "true")
    received: list[tuple[str, int]] = []

    async def fake_render(html, render_timeout):
        received.append((html, render_timeout))
        return b"\x89PNG\r\n\x1a\n"

    with caplog.at_level(logging.INFO, logger="utils.renderer_warmup"):
        asyncio.run(warm_up_authored_renderer(render=fake_render))

    assert len(received) == 1
    html, render_timeout = received[0]
    assert isinstance(html, str) and len(html.encode("utf-8")) < 1024
    assert isinstance(render_timeout, int) and render_timeout > 0
    assert any("renderer_warmup ok" in record.message for record in caplog.records)


def test_warmup_default_render_resolves_to_slide_capture_entry_point(monkeypatch):
    """Without an injected ``render``, production resolves the real entry point."""
    monkeypatch.setenv("AUTHORED_RENDERER_WARMUP_ENABLED", "true")
    calls: list[tuple[str, int]] = []

    async def fake_render_html_to_png(html, render_timeout=60):
        calls.append((html, render_timeout))
        return b"png"

    import utils.slide_capture as slide_capture

    monkeypatch.setattr(slide_capture, "render_html_to_png", fake_render_html_to_png)

    asyncio.run(warm_up_authored_renderer())

    assert len(calls) == 1
