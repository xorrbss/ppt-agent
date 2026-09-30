"""The draft render's in-render-fit call site (D1a).

Design: ``docs/authored-in-render-fit-design-20260906.md`` sections 5 and 6.
Drives the real ``_author_illustrate_render_pipeline`` against a scratch
checkpoint database the way ``test_authored_checkpoint_service.py`` does, with
Chromium faked, so the claim is about the shipped call site rather than a
model of it. Reuses that file's database/job/outline scaffolding.

This repo has no ``pytest-asyncio`` plugin; async scenarios are driven with
``asyncio.run`` from an ordinary sync test function.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from services import authored_checkpoint_service as checkpoints
from services import authored_presentation_pipeline as pipeline
from tests.unit.test_authored_checkpoint_service import (
    _checkpoint_database,
    _one_slide_outline,
    _pipeline_brand,
    _running_authored_job,
)

_RESUMABLE_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'></head>"
    "<body><main>fit call site</main></body></html>"
)


async def _completed_author_job(maker):
    """One slide whose author stage is already checkpointed, ready to render."""

    _, job = await _running_authored_job(maker, total_slides=1)
    await checkpoints.initialize_checkpoints(
        job_id=job.id, attempt_token="attempt-a", roles=["title"]
    )
    await checkpoints.start_stage(
        job_id=job.id, slide_index=0, stage="author", attempt_token="attempt-a"
    )
    await checkpoints.complete_stage(
        job_id=job.id,
        slide_index=0,
        stage="author",
        attempt_token="attempt-a",
        html=_RESUMABLE_HTML,
    )
    return job


async def _render_one_slide(tmp_path, monkeypatch, *, capture):
    """Runs the real pipeline for one slide with ``render_html_to_png_with_metrics``
    faked to return ``capture``; the plain ``render_html_to_png`` path is faked
    to fail the test if reached, so a knob-on run cannot silently fall back to
    it. Returns ``(result, rendered_html)`` -- ``rendered_html`` is the exact
    string the pipeline handed to the renderer (post semantic-role/qid
    canonicalization), so "unchanged" assertions compare against what the
    pipeline actually produced rather than the raw checkpoint fixture.
    """

    engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
    monkeypatch.setattr(
        pipeline, "prepare_authored_deck", lambda *_a, **_k: SimpleNamespace(
            contents=("fit call site",),
            design_system="TEST DESIGN SYSTEM",
            reference_mode="style_reference",
        )
    )

    rendered: list[str] = []

    async def unexpected_author(*_args, **_kwargs):
        raise AssertionError("a completed author checkpoint must be resumed")

    async def unexpected_plain_render(_html):
        raise AssertionError("the knob-on path must not call the plain renderer")

    async def fake_render_with_metrics(html, *, fit=False, **_kwargs):
        assert fit is True
        rendered.append(html)
        return capture

    async def no_recording(**_kwargs):
        return None

    monkeypatch.setattr(pipeline, "author_planned_slide", unexpected_author)
    monkeypatch.setattr(pipeline, "render_html_to_png", unexpected_plain_render)
    monkeypatch.setattr(
        pipeline, "render_html_to_png_with_metrics", fake_render_with_metrics
    )
    monkeypatch.setattr(pipeline, "record_local_stage", no_recording)
    # The in-render fit is the non-local-fit render path; local fit (on by
    # default for an ordinary deck) would route around this seam.
    monkeypatch.setattr(pipeline, "local_fit_applies", lambda **_kwargs: False)

    try:
        job = await _completed_author_job(maker)
        result = await pipeline._author_illustrate_render_pipeline(
            _one_slide_outline(),
            _pipeline_brand(),
            SimpleNamespace(reference_mode="style_reference", illustration_prompt=None),
            generation_job=job,
        )
        return result, rendered[0]
    finally:
        await engine.dispose()


def _fitted_document() -> str:
    """A fitted variant of the resumable slide: the same document with the
    fitter's inline declaration appended, stamped and canonicalized the way the
    pipeline stamps the draft, so the scene envelope can be resealed over it."""
    from utils.authored_dom_quality import ensure_authored_qids
    from services.authored_semantic_role_repair import canonicalize_authored_roles

    variant = _RESUMABLE_HTML.replace("<main>", '<main style="font-size:15px">', 1)
    return canonicalize_authored_roles(ensure_authored_qids(variant))


def test_the_fitted_html_becomes_the_string_of_record(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "true")
    capture = SimpleNamespace(
        png=b"fitted-png",
        browser_metrics={"fit": {"edits": ["font-size:15px"]}},
        fitted_html=_fitted_document(),
        fit_report={"edits": ["font-size:15px"]},
    )

    result, _rendered = asyncio.run(
        _render_one_slide(tmp_path, monkeypatch, capture=capture)
    )

    assert result.deck.htmls == [capture.fitted_html]
    assert result.pngs == [b"fitted-png"]
    assert result.slide_fit_reports == {0: {"edits": ["font-size:15px"]}}
    # The scene envelope must follow the fitted document, or the final metrics
    # readback compares fitted metrics against the pre-fit projection (run 43).
    assert result.scene_envelope.html_projections == (capture.fitted_html,)


def test_a_fit_report_with_no_edits_keeps_the_authored_html(tmp_path, monkeypatch):
    """``fitted_html`` is ``None`` when the fitter changed nothing (section 5)."""

    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "true")
    capture = SimpleNamespace(
        png=b"unfitted-png",
        browser_metrics={"fit": {"edits": []}},
        fitted_html=None,
        fit_report={"edits": []},
    )

    result, rendered = asyncio.run(
        _render_one_slide(tmp_path, monkeypatch, capture=capture)
    )

    assert result.deck.htmls == [rendered]
    assert result.slide_fit_reports == {0: {"edits": []}}


def test_a_capture_missing_the_new_fields_leaves_the_html_untouched(
    tmp_path, monkeypatch
):
    """A ``RenderCapture`` shaped like the current worktree's, pre-merge."""

    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "true")

    class OldCapture:
        def __init__(self):
            self.png = b"old-png"
            self.browser_metrics = {"legacy": True}

    result, rendered = asyncio.run(
        _render_one_slide(tmp_path, monkeypatch, capture=OldCapture())
    )

    assert result.deck.htmls == [rendered]
    assert result.slide_fit_reports == {}


def test_the_knob_off_path_is_byte_identical_to_before(tmp_path, monkeypatch):
    """With the knob off, the render call must never request a fit: the
    non-local-fit path captures through ``render_html_to_png_with_metrics``
    with ``fit=False`` (the merged capture owner's single render seam)."""

    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "false")
    rendered: list[str] = []

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        monkeypatch.setattr(
            pipeline, "prepare_authored_deck", lambda *_a, **_k: SimpleNamespace(
                contents=("fit call site",),
                design_system="TEST DESIGN SYSTEM",
                reference_mode="style_reference",
            )
        )

        async def unexpected_author(*_args, **_kwargs):
            raise AssertionError("a completed author checkpoint must be resumed")

        async def unexpected_plain_render(_html):
            raise AssertionError("the capture owner renders through the metrics seam")

        async def fake_capture(html, *, fit=False, **_kwargs):
            from services.authored_local_fit_policy import html_digest
            from utils.slide_capture_result import RenderCapture

            if fit:
                raise AssertionError("the knob-off path must not request a fit render")
            rendered.append(html)
            return RenderCapture.from_payload(
                html_digest=html_digest(html),
                png=b"plain-png",
                browser_metrics={},
                runtime_counters={},
                prepared_html=html,
            )

        async def no_recording(**_kwargs):
            return None

        monkeypatch.setattr(pipeline, "author_planned_slide", unexpected_author)
        monkeypatch.setattr(pipeline, "render_html_to_png", unexpected_plain_render)
        monkeypatch.setattr(
            pipeline, "render_html_to_png_with_metrics", fake_capture
        )
        monkeypatch.setattr(pipeline, "record_local_stage", no_recording)
        # The in-render fit is the non-local-fit render path; local fit (on by
        # default for an ordinary deck) would route around this seam.
        monkeypatch.setattr(pipeline, "local_fit_applies", lambda **_kwargs: False)

        try:
            job = await _completed_author_job(maker)
            return await pipeline._author_illustrate_render_pipeline(
                _one_slide_outline(),
                _pipeline_brand(),
                SimpleNamespace(
                    reference_mode="style_reference", illustration_prompt=None
                ),
                generation_job=job,
            )
        finally:
            await engine.dispose()

    result = asyncio.run(scenario())

    # The knob-off path renders once, never with ``fit=True`` (``fake_capture``
    # above raises on it), and records no fit report at all.
    assert len(rendered) == 1
    assert result.deck.htmls == rendered
    assert result.pngs == [b"plain-png"]
    assert result.slide_fit_reports == {}
