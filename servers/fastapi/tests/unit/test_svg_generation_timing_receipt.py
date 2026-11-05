"""The SVG driver records every public timing stage its attempt executes.

Drives the real ``_generate`` page loop (``services/generation_core/drivers/svg/
collect.py``) with the recorder the workspace generation handler installs, then
seals the recorder through the handler's own receipt writer
(``_persist_terminal_attempt_timing``). Before the SVG driver recorded its
stages, a succeeded SVG attempt observed only ``provider`` and the receipt was
always ``rc_invalid`` (outline/authoring/chromiumReview/publication:
``executed_duration_missing``).
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET
import time
import uuid

import pytest

from services import svg_presentation_service as generation
from services.authored_generation_timing_receipt import GenerationTimingRecorder
from services.generation_core.runtime.profile import resolve_svg_runtime_profile
from services.presentation_generation_attempt_timing import (
    _persist_failed_attempt_timing,
    _persist_terminal_attempt_timing,
)
from services.svg_presentation_prepare import GenerationContext
from services.generation_core.authoring.author_slide_svg import SvgAuthorResult

SLIDE_COUNT = 3
# Above the ~15.6 ms resolution of time.monotonic() on Windows.
_TICK = 0.03
_PUBLIC_STAGES = ("outline", "authoring", "chromiumReview", "publication")


def _ctx():
    return GenerationContext(
        source_reflow=False, raster_reconstruction=False, raster_analysis=None, raster_pin=None,
        source_document=None, source_units=None, canonical_assets=False,
        canvas=SimpleNamespace(width_px=1280, height_px=720), physical_kwargs={}, snapshot={},
        source_font_profile=None, count=SLIDE_COUNT, roles=["cover", "content", "content"],
        manifests=[{}, {}, {}], planned=[{}, {}, {}], source_manifest=None, source_pointer=None,
        asset_context=None, asset_revision=None, service=None,
        semaphore=asyncio.Semaphore(SLIDE_COUNT), render_semaphore=asyncio.Semaphore(SLIDE_COUNT),
        runtime_profile=resolve_svg_runtime_profile(source_reflow=False),
    )


def _request():
    request = SimpleNamespace(
        template="authored", strategy="svg", instructions=None, codex_generation_preset=None,
        aspect_ratio="16:9",
        authored_style="default", include_title_slide=False, content="deck content",
        reference_snapshot_ids=[], _source_content_svg_reflow=False,
        # What the handler pins before the upstream outline call and installs
        # before dispatching the driver.
        _authored_outline_started_at=time.monotonic(),
        _authored_generation_timing_recorder=GenerationTimingRecorder(),
    )
    request._authored_timing_stage = "outline"
    request._authored_timing_stage_started_at = request._authored_outline_started_at
    return request


def _install_harness(monkeypatch, *, saved=None, render_error=None, repair_page=None):
    saved = {} if saved is None else saved

    async def prepare(*_args, **_kwargs):
        await asyncio.sleep(_TICK)
        return _ctx()

    monkeypatch.setattr("services.generation_core.drivers.svg.collect._prepare_generation", prepare)

    async def checkpoint(_job, index, *, payload=None):
        if payload is not None:
            saved[index] = deepcopy(payload)
        return deepcopy(saved.get(index, {}))

    monkeypatch.setattr(generation, "_checkpoint", checkpoint)
    monkeypatch.setattr(generation, "_read_png", lambda payload: b"png" if payload.get("svg_content") else None)
    monkeypatch.setattr(generation, "_write_png", lambda _png: {"png_sha256": "test", "png_path": "test"})

    async def publish(*_args, **_kwargs):
        await asyncio.sleep(_TICK)
        return "published"

    publish_mock = AsyncMock(side_effect=publish)
    monkeypatch.setattr(generation, "_publish", publish_mock)
    monkeypatch.setattr(generation, "run_vision_advisory", AsyncMock(return_value={"skipped": "provider_failed"}))

    async def fake_author_slide_svg(*, index, before_dispatch, **_kwargs):
        await before_dispatch()
        await asyncio.sleep(_TICK)
        return SvgAuthorResult(f"<svg data-index='{index}'/>", "raw", {"validation_error": None})

    author_mock = AsyncMock(side_effect=fake_author_slide_svg)
    monkeypatch.setattr("services.generation_core.authoring.author_slide_svg.author_slide_svg", author_mock)
    monkeypatch.setattr("services.svg_contract.finalize_svg", lambda svg, **_kwargs: svg)

    rejected_once = set()

    async def fake_render(*_args, **_kwargs):
        await asyncio.sleep(_TICK)
        if render_error is not None:
            raise render_error
        if repair_page is not None:
            index = int(ET.fromstring(_args[0]).get("data-index"))
            if index == repair_page and index not in rejected_once:
                rejected_once.add(index)
                return SimpleNamespace(png=b"png", quality={"passed": False, "verified": True,
                    "failures": [{"kind": "text_overflow", "text": "repair this page"}]})
        return SimpleNamespace(png=b"png", quality={"passed": True, "verified": True, "failures": []})

    monkeypatch.setattr("services.svg_renderer.render_svg_with_metrics", fake_render)
    return author_mock, publish_mock, saved


def _usage(logical_calls: int, latency_ms: int):
    totals = {
        "logicalCalls": logical_calls, "physicalCalls": logical_calls, "retryCalls": 0,
        "reportedUsageCalls": logical_calls, "unavailableUsageCalls": 0, "pricedCalls": 0,
        "latencyMs": latency_ms, "inputTokens": 0, "outputTokens": 0, "cachedTokens": 0,
        "actualCostMicrounits": 0, "estimatedCostMicrounits": 0,
    }
    return SimpleNamespace(snapshot=lambda: {"totals": totals, "stages": {}})


def _context(attempt_token: str):
    return SimpleNamespace(claim=SimpleNamespace(
        operation_id=uuid.uuid4(), attempt_token=attempt_token, attempt_number=1,
        expected_revision=0, fencing_token=1))


def _seal_success(request, *, logical_calls: int):
    return asyncio.run(_persist_terminal_attempt_timing(
        job_id=uuid.uuid4(), context=_context("private-svg-timing-attempt"),
        recorder=request._authored_generation_timing_recorder,
        provider_usage=_usage(logical_calls, 5 * logical_calls), queue_wait_ms=0,
        outcome="succeeded", retry_reason=None, terminal_error=None, request=request))


def _run(request):
    outline = SimpleNamespace(slides=[SimpleNamespace(content=f"slide {i}") for i in range(SLIDE_COUNT)])
    return asyncio.run(generation._generate(request, uuid.uuid4(), outline, "ko", None, None))


@pytest.fixture(autouse=True)
def _receipt_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))


def test_a_generated_svg_deck_observes_every_executed_stage_and_seals_valid(monkeypatch):
    author_mock, publish_mock, _saved = _install_harness(monkeypatch)
    request = _request()

    assert _run(request) == "published"
    assert author_mock.await_count == SLIDE_COUNT
    publish_mock.assert_awaited_once()

    observations = request._authored_generation_timing_recorder.stage_observations()
    assert {"outline", "author", "render", "quality", "publication"} <= set(observations)
    # Measured windows, not placeholders: every page authored and rendered.
    assert observations["author"]["durationMillis"] >= SLIDE_COUNT * 1
    assert observations["render"]["durationMillis"] >= SLIDE_COUNT * 1
    assert observations["publication"]["durationMillis"] >= 1
    assert observations["outline"]["endMillis"] <= observations["author"]["startMillis"] + 1
    assert observations["publication"]["startMillis"] >= observations["quality"]["startMillis"]

    receipt = _seal_success(request, logical_calls=SLIDE_COUNT)
    assert receipt is not None
    assert receipt["validity"] == "valid"
    assert receipt["invalidReasons"] == []
    for name in (*_PUBLIC_STAGES, "provider"):
        assert receipt["stages"][name]["state"] == "observed", name
    assert receipt["stages"]["repair"] == {"state": "not_started"}
    chromium_review = receipt["stages"]["chromiumReview"]["durationMillis"]
    assert chromium_review == observations["render"]["durationMillis"] + observations["quality"]["durationMillis"]


def test_header_time_is_its_own_stage_and_outline_excludes_it(monkeypatch):
    _install_harness(monkeypatch)
    request = _request()
    recorder = request._authored_generation_timing_recorder

    async def prepare_with_header(*_args, **_kwargs):
        for name in ("header_design", "header_representative", "header_selection"):
            with recorder.observe(f"setup.{name}"):
                await asyncio.sleep(_TICK)
        return _ctx()

    monkeypatch.setattr(
        "services.generation_core.drivers.svg.collect._prepare_generation", prepare_with_header)
    assert _run(request) == "published"

    observations = recorder.stage_observations()
    for name in ("header_design", "header_representative", "header_selection"):
        assert observations[name]["durationMillis"] >= _TICK * 1000 * 0.5, name
    assert observations["outline"]["durationMillis"] < _TICK * 1000 * 2
    receipt = _seal_success(request, logical_calls=SLIDE_COUNT)
    assert receipt["validity"] == "valid"
    for name in ("headerDesign", "headerRepresentative", "headerSelection"):
        assert receipt["stages"][name]["state"] == "observed", name


def test_a_receipt_without_header_stages_still_validates(monkeypatch):
    _install_harness(monkeypatch)
    request = _request()
    assert _run(request) == "published"
    receipt = _seal_success(request, logical_calls=SLIDE_COUNT)
    assert not {"headerDesign", "headerRepresentative", "headerSelection"} & set(receipt["stages"])
    from services.authored_generation_timing_receipt import _attempt_receipt_v2_from_dict
    assert _attempt_receipt_v2_from_dict(receipt).validity == "valid"


def test_a_fully_resumed_svg_deck_records_zero_page_work_and_seals_valid(monkeypatch):
    saved: dict[int, dict] = {}
    _install_harness(monkeypatch, saved=saved)
    assert _run(_request()) == "published"

    author_mock, _publish_mock, _saved = _install_harness(monkeypatch, saved=saved)
    resumed = _request()
    assert _run(resumed) == "published"
    author_mock.assert_not_awaited()

    observations = resumed._authored_generation_timing_recorder.stage_observations()
    assert observations["author"]["durationMillis"] == 0
    assert observations["render"]["durationMillis"] == 0
    assert "repair" not in observations
    receipt = _seal_success(resumed, logical_calls=0)
    assert receipt["validity"] == "valid"
    assert receipt["stages"]["provider"] == {"state": "not_started"}
    assert receipt["stages"]["repair"] == {"state": "not_started"}
    for name in _PUBLIC_STAGES:
        assert receipt["stages"][name]["state"] == "observed", name


def test_model_correction_has_its_own_timing_and_completed_replay_does_not_repeat_it(monkeypatch):
    author_mock, _, saved = _install_harness(monkeypatch, repair_page=1)
    request = _request()
    recorder = request._authored_generation_timing_recorder
    stage_windows = []
    original_stage = GenerationTimingRecorder.stage

    def stage(current, name):
        if current is recorder:
            stage_windows.append(name)
        return original_stage(current, name)

    monkeypatch.setattr(GenerationTimingRecorder, "stage", stage)
    assert _run(request) == "published"
    assert author_mock.await_count == SLIDE_COUNT + 1
    page_calls = [call.kwargs for call in author_mock.await_args_list if call.kwargs["index"] == 1]
    assert len(page_calls) == 2
    assert page_calls[0]["correction_hint"] is None
    assert "text_overflow" in page_calls[1]["correction_hint"]
    assert len(saved[1]["attempts"]) == 2
    # Exactly one measured window per provider attempt, never author + repair together.
    assert stage_windows.count("author") == SLIDE_COUNT
    assert stage_windows.count("repair") == 1
    observations = recorder.stage_observations()
    assert observations["repair"]["durationMillis"] >= 1
    receipt = _seal_success(request, logical_calls=SLIDE_COUNT + 1)
    assert receipt["validity"] == "valid"
    assert receipt["stages"]["repair"]["state"] == "observed"
    assert receipt["stages"]["repair"]["durationMillis"] == observations["repair"]["durationMillis"]

    replay_author, _, _ = _install_harness(monkeypatch, saved=saved, repair_page=1)
    replay = _request()
    assert _run(replay) == "published"
    replay_author.assert_not_awaited()
    replay_observations = replay._authored_generation_timing_recorder.stage_observations()
    assert replay_observations["author"]["durationMillis"] == 0
    assert replay_observations["render"]["durationMillis"] == 0
    assert "repair" not in replay_observations
    replay_receipt = _seal_success(replay, logical_calls=0)
    assert replay_receipt["validity"] == "valid"
    assert replay_receipt["stages"]["repair"] == {"state": "not_started"}


def test_a_failed_svg_attempt_is_charged_to_the_stage_that_was_running(monkeypatch):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "false")
    _install_harness(monkeypatch, render_error=RuntimeError("chromium_crashed"))
    request = _request()

    with pytest.raises(RuntimeError):
        _run(request)

    assert request._authored_timing_stage == "author"
    receipt = asyncio.run(_persist_failed_attempt_timing(
        request=request, job_id=uuid.uuid4(), status_error={}, context=_context("private-svg-failed"),
        error=RuntimeError("chromium_crashed"), recorder=request._authored_generation_timing_recorder,
        provider_usage=_usage(SLIDE_COUNT, 15), queue_wait_ms=0))
    assert receipt is not None
    assert receipt["validity"] == "valid"
    assert receipt["stages"]["outline"]["state"] == "observed"
    assert receipt["stages"]["authoring"]["state"] == "observed"
    assert receipt["stages"]["chromiumReview"]["state"] == "observed"
    assert receipt["stages"]["publication"] == {"state": "not_started"}


def test_a_generated_svg_deck_logs_one_outline_span_line(monkeypatch, caplog):
    import logging

    from services import svg_outline_spans

    _install_harness(monkeypatch)
    svg_outline_spans.begin().set("outline_total", 42)
    with caplog.at_level(logging.INFO, logger=svg_outline_spans.LOGGER.name):
        assert _run(_request()) == "published"
    lines = [r.getMessage() for r in caplog.records if "svg_outline_span" in r.getMessage()]
    assert len(lines) == 1
    for key in svg_outline_spans.SPAN_KEYS:
        assert f"{key}_ms=" in lines[0]
    assert "outline_total_ms=42" in lines[0]
    assert "prepare_total_ms=" in lines[0] and "prepare_total_ms=na" not in lines[0]
