"""One capture owner for the authored path: reuse, profiles, counters, bounds.

The shipped reuse path is ``services/authored_admission_capture``, not the
``AdmissionCaptureStore`` class the receipt module still exports, so the
capture-profile and identity guarantees are asserted here against the code that
actually runs (review finding B8).  Everything is offline: captures are async
stubs over synthetic metrics, and no Chromium process is started.
"""

from __future__ import annotations

import asyncio
from functools import wraps

import pytest

from services.authored_admission_capture import (
    RETAINED_CAPTURES,
    capture_once,
    capture_ownership_receipt,
    capture_png_once,
    capture_profile,
    captured_metrics_or_existing,
)
from services.authored_admission_session import AdmissionSession, _SESSION
from utils.slide_capture_result import RenderCapture


def sync_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


HTML = "<!doctype html><html><body><h1>원문</h1></body></html>"
OTHER = "<!doctype html><html><body><h1>다른 원문</h1></body></html>"


def render_capture(html: str, *, png: bytes = b"png", fitted: str | None = None) -> RenderCapture:
    return RenderCapture.from_payload(
        html_digest="sha256:" + html[:8],
        png=png,
        browser_metrics={"slide": {"sourceDigest": html[:8]}},
        runtime_counters={},
        fitted_html=fitted,
    )


class Renderer:
    """An async capture stub that counts the renders it was actually asked for."""

    def __init__(self, *, fitted: str | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._fitted = fitted

    async def __call__(self, html: str, **kwargs):
        self.calls.append((html, kwargs))
        await asyncio.sleep(0)
        return render_capture(html, fitted=self._fitted)


def session_scope(mode: str = "shadow", slides: int = 0):
    session = AdmissionSession(mode=mode, contexts=() if not slides else tuple(range(slides)))
    return session, _SESSION.set(session)


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
@sync_async
async def test_identical_calls_render_once_in_every_gate_mode(mode):
    """Reuse is a cache, so it may not wait for the gate to be enforcing."""

    session, token = session_scope(mode)
    render = Renderer()
    try:
        first, second = await asyncio.gather(
            capture_once(HTML, capture=render),
            capture_once(HTML, capture=render),
        )
        third = await capture_once(HTML, capture=render)
    finally:
        _SESSION.reset(token)
    assert len(render.calls) == 1
    assert first is second is third
    assert session.capture_reuses == 2


@sync_async
async def test_the_fit_verification_render_is_a_distinct_capture():
    """``fit=False`` over fitted HTML differs in both digest and profile."""

    fitted = OTHER
    render = Renderer(fitted=fitted)
    _, token = session_scope()
    try:
        first = await capture_once(HTML, index=0, capture=render, fit=True, fit_policy={"a": 1})
        assert first.fitted_html == fitted
        await capture_once(fitted, index=0, capture=render, fit=False, fresh=True)
        # The same document under the first pass' profile is still the first
        # capture, so only the genuinely new render was paid for.
        await capture_once(HTML, index=0, capture=render, fit=True, fit_policy={"a": 1})
    finally:
        _SESSION.reset(token)
    assert len(render.calls) == 2


@sync_async
async def test_a_different_profile_or_document_is_never_served_from_the_cache():
    render = Renderer()
    _, token = session_scope()
    try:
        await capture_once(HTML, capture=render)
        await capture_once(HTML, capture=render, fit=False)
        await capture_once(HTML, capture=render, deck_scope="deck-1")
        await capture_once(OTHER, capture=render)
        # ``timeout`` bounds the work rather than changing the pixels.
        await capture_once(HTML, capture=render, timeout=17)
    finally:
        _SESSION.reset(token)
    assert len(render.calls) == 4
    assert capture_profile({"timeout": 1}) == capture_profile({})
    assert capture_profile({"fit": True}) != capture_profile({})


@sync_async
async def test_metrics_for_unchanged_bytes_reuse_the_capture():
    render = Renderer()
    ensured: list[str] = []

    async def ensure(html: str):
        ensured.append(html)
        return {"fresh": True}

    _, token = session_scope()
    try:
        await capture_once(HTML, index=0, capture=render)
        reused = await captured_metrics_or_existing(HTML, index=0, ensure=ensure)
        missing = await captured_metrics_or_existing(OTHER, index=1, ensure=ensure)
    finally:
        _SESSION.reset(token)
    assert reused == {"slide": {"sourceDigest": HTML[:8]}}
    assert ensured == [OTHER] and missing == {"fresh": True}


@sync_async
async def test_metrics_are_keyed_by_the_fitted_document():
    """The fitter's output, not its input, is what downstream readbacks gate."""

    render = Renderer(fitted=OTHER)
    _, token = session_scope()
    ensured: list[str] = []

    async def ensure(html: str):
        ensured.append(html)
        return None

    try:
        await capture_once(HTML, index=0, capture=render, fit=True)
        assert await captured_metrics_or_existing(OTHER, index=0, ensure=ensure) is not None
    finally:
        _SESSION.reset(token)
    assert ensured == []


@sync_async
async def test_the_receipt_counts_renders_and_reuses_per_slide():
    render = Renderer()
    _, token = session_scope()
    try:
        await capture_once(HTML, index=0, capture=render)
        await capture_once(HTML, index=0, capture=render)
        await capture_once(OTHER, index=1, capture=render)
        await capture_once(HTML, capture=render)
        receipt = capture_ownership_receipt()
    finally:
        _SESSION.reset(token)
    assert receipt["captureCount"] == 2
    assert receipt["captureReuseCount"] == 2
    assert receipt["slides"] == [
        {"index": 0, "captureCount": 1, "captureReuseCount": 1},
        {"index": 1, "captureCount": 1, "captureReuseCount": 0},
    ]
    assert receipt["unattributed"] == {"captureCount": 0, "captureReuseCount": 1}


@sync_async
async def test_a_failed_capture_is_not_cached():
    attempts: list[str] = []

    async def capture(html: str, **kwargs):
        attempts.append(html)
        if len(attempts) == 1:
            raise RuntimeError("renderer unavailable")
        return render_capture(html)

    _, token = session_scope()
    try:
        with pytest.raises(RuntimeError):
            await capture_once(HTML, capture=capture)
        assert (await capture_once(HTML, capture=capture)).png == b"png"
    finally:
        _SESSION.reset(token)
    assert len(attempts) == 2


@sync_async
async def test_an_abandoned_capture_with_no_other_waiter_is_cancelled():
    """A caller's deadline must not leave a browser render running behind it."""

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def capture(html: str, **kwargs):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return render_capture(html)

    session, token = session_scope()
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await capture_once(HTML, capture=capture)
        await asyncio.sleep(0)
        assert started.is_set() and cancelled.is_set()
        assert session.captures == {}
    finally:
        _SESSION.reset(token)


@sync_async
async def test_completed_captures_are_evicted_but_their_metrics_are_not():
    render = Renderer()
    _, token = session_scope()
    ensured: list[str] = []

    async def ensure(html: str):
        ensured.append(html)
        return None

    try:
        for index in range(RETAINED_CAPTURES + 4):
            await capture_once(f"{HTML}<!--{index}-->", index=index, capture=render)
        session = _SESSION.get()
        assert len(session.captures) <= RETAINED_CAPTURES
        # The earliest slide's pixels are gone; its telemetry is still owned.
        assert await captured_metrics_or_existing(f"{HTML}<!--0-->", index=0, ensure=ensure) is not None
    finally:
        _SESSION.reset(token)
    assert ensured == []


@sync_async
async def test_a_png_only_renderer_shares_the_default_profile():
    renders: list[str] = []

    async def render(html: str) -> bytes:
        renders.append(html)
        return b"png"

    capture = Renderer()
    _, token = session_scope()
    try:
        png = await capture_png_once(HTML, index=0, render=render, read_metrics=lambda _html: {"m": 1})
        again = await capture_once(HTML, index=0, capture=capture)
    finally:
        _SESSION.reset(token)
    assert png == b"png"
    assert renders == [HTML] and capture.calls == []
    assert again.browser_metrics == {"m": 1}


@sync_async
async def test_without_a_session_the_owner_is_a_pass_through():
    render = Renderer()
    assert _SESSION.get() is None
    await capture_once(HTML, capture=render)
    await capture_once(HTML, capture=render)
    assert len(render.calls) == 2
    assert capture_ownership_receipt() is None


@sync_async
async def test_final_readback_does_not_rewrite_a_checkpoint_that_already_publishes(monkeypatch):
    """Review B7: the enforce tail re-persisted every slide it had already written."""

    from services import authored_checkpoint_html, authored_checkpoint_state
    from services import authored_presentation_artifacts as artifacts
    from services.authored_admission_publication import _persist_publishable_checkpoints

    htmls = [f"{HTML}<!--{index}-->" for index in range(3)]
    stored = {0: htmls[0], 1: None, 2: htmls[2]}
    written: list[int] = []

    async def get_checkpoint(job_id, slide_index, **kwargs):
        return ("checkpoint", slide_index)

    def stage_html(checkpoint, *, stage="render", publishable=False):
        assert publishable
        return stored[checkpoint[1]]

    async def complete(**kwargs):
        written.append(kwargs["slide_index"])
        stored[kwargs["slide_index"]] = kwargs["html"]
        return ("checkpoint", kwargs["slide_index"])

    monkeypatch.setattr(authored_checkpoint_state, "get_checkpoint", get_checkpoint)
    monkeypatch.setattr(authored_checkpoint_html, "checkpoint_stage_html", stage_html)
    monkeypatch.setattr(artifacts, "_complete_stage_after_external_success", complete)

    job = type("Job", (), {"id": "job", "attempt_token": "attempt-1", "_workspace_fencing_token": 4})()
    receipts = [type("Admission", (), {"receipt": index})() for index in range(3)]
    await _persist_publishable_checkpoints(
        generation_job=job, admissions=receipts, htmls=htmls, pngs=[b"p"] * 3, metrics=[{}] * 3,
    )
    assert written == [1]
