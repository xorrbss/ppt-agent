"""Collector-level tests for the SVG driver's terminal policy (c1-quality).

Drives the real ``_generate`` page loop (``services/generation_core/drivers/svg/
collect.py``) for an ordinary (non-source-reflow) three-slide deck, with every
provider/render/publish boundary faked -- the same monkeypatch style as
``test_svg_source_generation_flow.py``, but through ``services.svg_presentation_service``
(the facade) instead of driving a source-reflow deck.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from services import svg_presentation_service as generation
from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.drivers.svg.fallback import FALLBACK_GROUP_ID
from services.generation_core.runtime.profile import resolve_svg_runtime_profile
from services.svg_presentation_prepare import GenerationContext
from services.generation_core.authoring.author_slide_svg import SvgAuthorResult

FAIL_INDEX = 1
SLIDE_COUNT = 3


def _ctx(runtime_profile):
    return GenerationContext(
        source_reflow=False, raster_reconstruction=False, raster_analysis=None, raster_pin=None,
        source_document=None, source_units=None, canonical_assets=False,
        canvas=SimpleNamespace(width_px=1280, height_px=720), physical_kwargs={}, snapshot={},
        source_font_profile=None, count=SLIDE_COUNT,
        roles=["cover", "content", "content"], manifests=[{}, {}, {}], planned=[{}, {}, {}],
        source_manifest=None, source_pointer=None, asset_context=None, asset_revision=None,
        service=None, semaphore=asyncio.Semaphore(SLIDE_COUNT), render_semaphore=asyncio.Semaphore(SLIDE_COUNT),
        runtime_profile=runtime_profile,
    )


def _request():
    return SimpleNamespace(instructions=None, codex_generation_preset=None, authored_style="default",
                           include_title_slide=False, content="deck content", reference_snapshot_ids=[],
                           strategy="svg", _source_content_svg_reflow=False)


def _outline():
    return SimpleNamespace(slides=[SimpleNamespace(content=f"slide {i} content") for i in range(SLIDE_COUNT)])


def _install_harness(monkeypatch, *, runtime_profile, fail_index=FAIL_INDEX, saved=None):
    """Wire the facade hooks collect._generate reads at call time.

    Every page's contract check fails for ``fail_index`` on both attempts
    (``services.svg_contract.finalize_svg`` raises), so that page always runs
    out of attempts; every other page authors and renders successfully.
    Returns ``(author_mock, publish_mock, advisory_mock, saved)``.
    """
    saved = {} if saved is None else saved
    monkeypatch.setattr("services.generation_core.drivers.svg.collect._prepare_generation",
                        AsyncMock(return_value=_ctx(runtime_profile)))

    async def checkpoint(_job, index, *, payload=None):
        if payload is not None:
            saved[index] = deepcopy(payload)
        return deepcopy(saved.get(index, {}))

    monkeypatch.setattr(generation, "_checkpoint", checkpoint)
    monkeypatch.setattr(generation, "_read_png", lambda payload: b"png" if payload.get("svg_content") else None)
    monkeypatch.setattr(generation, "_write_png", lambda _png: {"png_sha256": "test", "png_path": "test"})
    publish_mock = AsyncMock(return_value="published")
    monkeypatch.setattr(generation, "_publish", publish_mock)
    advisory_mock = AsyncMock(return_value={"slides": [{"index": i, "issues": []} for i in range(SLIDE_COUNT)],
                                            "promptVersion": "v1", "model": "test-model"})
    monkeypatch.setattr(generation, "run_vision_advisory", advisory_mock)

    async def fake_author_slide_svg(*, index, before_dispatch, **_kwargs):
        await before_dispatch()
        return SvgAuthorResult(f"<svg data-index='{index}'/>", "raw", {"validation_error": None})

    author_mock = AsyncMock(side_effect=fake_author_slide_svg)
    monkeypatch.setattr("services.generation_core.authoring.author_slide_svg.author_slide_svg", author_mock)

    def fake_finalize_svg(svg, *, index, **_kwargs):
        if index == fail_index:
            raise ValueError("mock_contract_violation")
        return svg

    monkeypatch.setattr("services.svg_contract.finalize_svg", fake_finalize_svg)
    monkeypatch.setattr("services.svg_renderer.render_svg_with_metrics", AsyncMock(
        side_effect=lambda *_a, **_kw: SimpleNamespace(
            png=b"png", quality={"passed": True, "verified": False, "failures": []})))
    return author_mock, publish_mock, advisory_mock, saved


def _run(request=None, outline=None):
    return asyncio.run(generation._generate(request or _request(), uuid.uuid4(), outline or _outline(), "ko",
                                            None, None))


def test_a_default_env_rescues_the_exhausted_page_with_a_fallback_and_publishes(monkeypatch):
    profile = resolve_svg_runtime_profile(source_reflow=False)
    author_mock, publish_mock, advisory_mock, _saved = _install_harness(monkeypatch, runtime_profile=profile)

    assert _run() == "published"

    publish_mock.assert_awaited_once()
    pages = publish_mock.await_args.args[-1]
    quality_summary = publish_mock.await_args.kwargs["quality_summary"]
    advisory = publish_mock.await_args.kwargs["advisory"]

    for index in (0, 2):
        payload, _png = pages[index]
        assert payload["quality"]["classification"] == "passed"

    flagged_payload, _png = pages[FAIL_INDEX]
    assert flagged_payload["terminal"]["reason"] == "author:attempts_exhausted"
    assert flagged_payload["terminal"]["remakeOutcome"] == "svg_fallback_page"
    assert FALLBACK_GROUP_ID in flagged_payload["svg_content"]
    assert flagged_payload["quality"]["classification"] == "review_required"
    assert flagged_payload["quality"]["verified"] is False

    assert quality_summary["flaggedPages"] == 1
    assert quality_summary["passedPages"] == 2

    assert len(advisory["pageDigests"]) == SLIDE_COUNT
    advisory_mock.assert_awaited_once()
    assert len(advisory_mock.await_args.args[0]) == SLIDE_COUNT


def test_b_knob_disabled_keeps_the_deck_fail_closed(monkeypatch):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "false")
    profile = resolve_svg_runtime_profile(source_reflow=False)
    _author_mock, publish_mock, _advisory_mock, _saved = _install_harness(monkeypatch, runtime_profile=profile)

    with pytest.raises(AuthoredStageError) as failed:
        _run()
    assert failed.value.code == "svg_validation_failed"
    publish_mock.assert_not_awaited()


def test_c_a_skipped_advisory_still_publishes(monkeypatch):
    profile = resolve_svg_runtime_profile(source_reflow=False)
    # No page fails its contract in this deck: the advisory itself reports a
    # provider skip, which must not gate publication either way.
    _author_mock, publish_mock, advisory_mock, _saved = _install_harness(
        monkeypatch, runtime_profile=profile, fail_index=-1)
    advisory_mock.return_value = {"skipped": "provider_failed"}

    assert _run() == "published"

    publish_mock.assert_awaited_once()
    advisory = publish_mock.await_args.kwargs["advisory"]
    assert advisory["vision"]["skipped"] == "provider_failed"


def test_d_a_checkpointed_fallback_page_resumes_without_reauthoring(monkeypatch):
    profile = resolve_svg_runtime_profile(source_reflow=False)
    rescued_payload = {
        "schema_version": "svg-slide-checkpoint/v1", "source_format": "svg", "attempts": [],
        "svg_content": f"<svg id='{FALLBACK_GROUP_ID}'/>",
        "quality": {"passed": True, "verified": False, "classification": "review_required"},
        "png_sha256": "existing", "png_path": "existing",
        "terminal": {"index": FAIL_INDEX, "reason": "author:attempts_exhausted",
                     "remakeAttempted": False, "remakeOutcome": "svg_fallback_page"},
    }
    saved = {FAIL_INDEX: deepcopy(rescued_payload)}
    author_mock, publish_mock, _advisory_mock, _saved = _install_harness(
        monkeypatch, runtime_profile=profile, fail_index=-1, saved=saved)

    assert _run() == "published"

    assert author_mock.await_count == SLIDE_COUNT - 1  # never called for the resumed page
    pages = publish_mock.await_args.args[-1]
    resumed_payload, _png = pages[FAIL_INDEX]
    assert resumed_payload["svg_content"] == rescued_payload["svg_content"]
    assert resumed_payload["terminal"]["reason"] == "author:attempts_exhausted"
