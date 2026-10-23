"""Optional diagnostics cannot satisfy stage contracts or change evidence identity."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
from types import SimpleNamespace
import uuid

import pytest
from llmai.shared import ImageContentPart

from services import authored_generation_timing_receipt as receipts, svg_outline_spans
from services.generation_core.authoring import author_slide_svg as author
from services.generation_core.drivers.svg import render, timing
from services.svg_partial_recovery import _page_render_result_digest
from tests.unit.test_author_slide_svg_asset_offer import _art_direction_style
from tests.unit.test_svg_generation_timing_receipt import (
    _context, _ctx, _install_harness, _request, _run, _seal_success, _usage,
)
from utils.hashing import canonical_json_digest
from utils.llm_calls.author_prompt_split import author_messages


@pytest.fixture(autouse=True)
def receipt_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))


def test_optional_observations_do_not_execute_required_stages():
    recorder = receipts.GenerationTimingRecorder()
    for name in (*sorted(receipts.OPTIONAL_GENERATION_OBSERVATIONS), "publication.handoff"):
        with recorder.observe(name):
            pass
    assert recorder.stage_observations() == {}
    assert recorder.snapshot_stage_timings_ms() == {}
    assert len(recorder.observations()) == len(receipts.OPTIONAL_GENERATION_OBSERVATIONS) + 1
    request = SimpleNamespace(_authored_generation_timing_recorder=recorder)
    from services.presentation_generation_attempt_timing import _persist_terminal_attempt_timing

    payload = asyncio.run(_persist_terminal_attempt_timing(job_id=uuid.uuid4(),
        context=_context("optional-only"), recorder=recorder, provider_usage=_usage(0, 0),
        queue_wait_ms=0, outcome="succeeded", retry_reason=None, terminal_error=None, request=request))
    assert payload["validity"] == "rc_invalid"
    assert set(payload["invalidReasons"]) == {
        f"{name}:executed_duration_missing" for name in ("outline", "authoring", "chromiumReview", "publication")}


@pytest.mark.parametrize("name", ["outline", "setup.unknown", "publication.", "publication.payload secret"])
def test_optional_vocabulary_is_bounded(name):
    with pytest.raises(ValueError, match="generation_optional_observations_invalid"):
        with receipts.GenerationTimingRecorder().observe(name):
            pass


def test_fake_deck_exposes_observations_and_render_receipts_without_changing_evidence(monkeypatch):
    _, publish, _ = _install_harness(monkeypatch)

    async def prepare(*_args, **_kwargs):
        ctx = _ctx()
        ctx.runtime_profile = replace(ctx.runtime_profile, vision_advisory=True)
        for key in ("plan_llm", "scenes", "icon_sheets"):
            with svg_outline_spans.span(key):
                await asyncio.sleep(0)
        return ctx

    monkeypatch.setattr("services.generation_core.drivers.svg.collect._prepare_generation", prepare)
    svg_outline_spans.begin()
    request = _request()
    assert _run(request) == "published"
    observations = request._authored_generation_timing_recorder.observations()
    assert {"setup.plan_llm", "setup.scenes", "setup.icon_sheets", "setup.prepare_local",
            "render.wait", "render.work", "render.post", "render.checkpoint", "quality.vqa",
            "publication.handoff"} <= set(observations)
    for payload, _ in publish.await_args.args[-1]:
        assert "renderAttempts" not in payload
    for render_receipt in request._authored_generation_timing_recorder.render_attempts():
        assert render_receipt["schema"] == "svg-render-attempt-timing/v1"
        assert render_receipt["pageIndex"] in range(3)
        assert render_receipt["attemptOrdinal"] == 1
        assert render_receipt["eventLoopLagMillis"] >= 0
        windows = render_receipt["stages"]
        assert {window["name"] for window in windows} == {"wait", "work", "post", "checkpoint"}
        for window in windows:
            assert window["startMonotonic"] <= window["endMonotonic"]
            assert window["durationMillis"] >= 0
        for left, right in zip(windows[:-1], windows[1:], strict=True):
            assert left["endMonotonic"] <= right["startMonotonic"]
    sealed = _seal_success(request, logical_calls=3)
    assert sealed["validity"] == "valid"
    assert sealed["observations"] == observations
    assert len(sealed["renderAttempts"]) == 3
    roundtrip = receipts._attempt_receipt_v2_from_dict(sealed)
    assert roundtrip.to_dict() == sealed
    baseline = replace(roundtrip, observations={}, render_attempts=(), render_attempts_dropped=0)
    assert "observations" not in baseline.to_dict()
    assert "renderAttempts" not in baseline.to_dict()
    assert receipts._attempt_receipt_v2_digest(baseline) == sealed["receiptDigest"]
    assert receipts._attempt_receipt_v2_from_dict(baseline.to_dict()) == baseline


def test_render_observations_leave_every_checkpoint_and_page_digest_identical(monkeypatch):
    _install_harness(monkeypatch)

    async def run(observed):
        request = _request()
        if not observed:
            del request._authored_generation_timing_recorder
        payload = {"attempts": [{"kind": "local", "receipt": {}}]}
        checkpoints = []

        async def checkpoint(_job, _index, *, payload):
            checkpoints.append(canonical_json_digest(payload))

        await render.attempt_render(ctx=_ctx(), request=request, presentation_id=uuid.UUID(int=1),
            index=1, content="same page", payload=payload, svg="<svg/>", ordinal=0, job=None,
            checkpoint=checkpoint, async_session_maker=None,
            write_png=lambda _png: {"png_sha256": "same-png", "png_path": "same-path"})
        return payload, checkpoints

    measured, measured_checkpoints = asyncio.run(run(True))
    baseline, baseline_checkpoints = asyncio.run(run(False))
    assert measured_checkpoints and measured_checkpoints == baseline_checkpoints
    assert measured == baseline
    assert canonical_json_digest(measured) == canonical_json_digest(baseline)
    assert _page_render_result_digest(measured) == _page_render_result_digest(baseline)


def test_render_detail_is_bounded_for_200_page_decks_and_overflow_is_explicit():
    request = _request()
    recorder = request._authored_generation_timing_recorder
    for page_index in range(201):
        for attempt_ordinal in range(1, 5):
            detail = timing.RenderAttemptObservations(request,
                page_index=page_index, attempt_ordinal=attempt_ordinal)
            detail.receipt["eventLoopLagMillis"] = 0
            detail.move(None)
    assert len(recorder.render_attempts()) == receipts.MAX_RENDER_ATTEMPT_OBSERVATIONS
    assert recorder.render_attempts_dropped == 4
    assert recorder.render_attempts()[-1]["pageIndex"] == 200
    detail = timing.RenderAttemptObservations(request, page_index=200, attempt_ordinal=5)
    detail.receipt["eventLoopLagMillis"] = 0
    for _ in range(receipts.MAX_RENDER_STAGE_WINDOWS + 5):
        detail.move("wait")
    detail.move(None)
    assert len(detail.receipt["stages"]) == receipts.MAX_RENDER_STAGE_WINDOWS
    assert detail.receipt["stageWindowsDropped"] == 6
    assert "render.wait" in recorder.observations()
    sealed = _seal_success(request, logical_calls=0)
    assert sealed["renderAttemptsDropped"] == 5
    parsed = receipts._attempt_receipt_v2_from_dict(sealed)
    assert receipts._attempt_receipt_v2_digest(replace(parsed,
        render_attempts=(), render_attempts_dropped=0)) == sealed["receiptDigest"]


def test_header_spans_share_the_recorder_and_are_separate_from_prepare_local(monkeypatch, caplog):
    now = [100.0]
    monkeypatch.setattr(svg_outline_spans, "time", SimpleNamespace(monotonic=lambda: now[0]))
    request = SimpleNamespace(_authored_generation_timing_recorder=receipts.GenerationTimingRecorder(started_at=100))
    spans = svg_outline_spans.begin()
    timing.bind_outline_spans(request, spans)
    with spans.span("prepare_total"):
        now[0] += 1
        with spans.span("plan_llm"):
            now[0] += 2
    for key in ("title", "header_design", "header_representative", "header_selection"):
        with timing.observe_setup(request, key):
            now[0] += 3
    with caplog.at_level(logging.INFO, logger=svg_outline_spans.LOGGER.name):
        spans.emit("fake-deck")
    assert spans.ms["prepare_local"] == 1000
    observations = request._authored_generation_timing_recorder.observations()
    assert observations["setup.prepare_local"]["durationMillis"] == 1000
    for key in ("title", "header_design", "header_representative", "header_selection"):
        assert observations[f"setup.{key}"]["durationMillis"] == 3000
        assert f"{key}_ms=3000" in caplog.text


@pytest.mark.parametrize("hoisted", [True, False])
def test_prefix_digest_is_page_invariant_and_full_request_digest_is_unchanged(hoisted):
    style = _art_direction_style()
    design = author.svg_design_text(style)
    rules = author.svg_author_rules(style, 18)

    def receipt(page, *, system=rules, invariant=design):
        messages = author_messages(system, invariant + "\n\n" + page, invariant,
            enabled=hoisted, image_parts=[ImageContentPart(data=page.encode(), mime_type="image/png")])
        result = author.svg_author_input_receipt(style, messages, provider="p", model="m", budget=1000, kwargs={})
        assembled = [{"role": type(message).__name__, "content": [
            {"text": part if isinstance(part, str) else part.text} if not isinstance(part, ImageContentPart)
            else {"image_digest": author.sha256_prefixed(part.data), "mime_type": part.mime_type}
            for part in ([message.content] if isinstance(message.content, str) else message.content)]}
            for message in messages]
        assert result["author_prompt_digest"] == canonical_json_digest({"version": "svg-author-input/v1",
            "messages": assembled, "settings": {"provider": "p", "model": "m", "max_tokens": 1000}})
        return result

    first, second = receipt("page one"), receipt("page two")
    assert first["prefix_digest"] == second["prefix_digest"]
    assert first["author_prompt_digest"] != second["author_prompt_digest"]
    assert receipt("page one", system=rules + " changed")["prefix_digest"] != first["prefix_digest"]
    assert receipt("page one", invariant=design + " changed")["prefix_digest"] != first["prefix_digest"]
