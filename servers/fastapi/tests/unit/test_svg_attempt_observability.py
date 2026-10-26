"""Real SVG dispatch and terminal storage preserve diagnostic/identity boundaries."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import uuid

import pytest

from services import authored_generation_timing_receipt as receipts
from services import svg_partial_recovery, svg_presentation_checkpoint as checkpoints
from services.generation_core.drivers.svg import render, timing
from services.generation_core.drivers.svg.runtime_provenance import runtime_provenance_field
from services.presentation_generation_attempt_timing import _persist_terminal_attempt_timing
from services.authored_checkpoint_errors import AuthoredStageError
from tests.unit.test_authored_checkpoint_service import _checkpoint_database, _running_authored_job
from tests.unit.test_svg_checkpoint_recovery_contract import _pages, _seed_pages
from tests.unit.test_svg_generation_timing_receipt import (
    _context, _ctx, _install_harness, _request, _run, _usage,
)
from utils.hashing import canonical_json_digest


@pytest.fixture(autouse=True)
def receipt_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path / "receipts"))


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "cancelled", "waiting_cancelled"])
def test_real_renderer_records_permit_wait_and_work_from_one_dispatch(monkeypatch, outcome):
    _install_harness(monkeypatch)
    now = [100.0]
    dispatched_at = datetime(2026, 10, 26, tzinfo=timezone.utc)
    monkeypatch.setattr(timing, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(timing, "get_current_utc_datetime", lambda: dispatched_at)
    dispatches = []

    async def scenario():
        ctx = _ctx()
        queued = asyncio.Event()
        permit = asyncio.Semaphore(0)

        class RenderPermit:
            async def __aenter__(self):
                queued.set()
                await permit.acquire()

            async def __aexit__(self, *_args):
                permit.release()

        ctx.render_semaphore = RenderPermit()
        request = _request()
        recorder = receipts.GenerationTimingRecorder(started_at=100.0)
        request._authored_generation_timing_recorder = recorder

        async def checkpoint(_job, _index, *, payload=None, render_started_at=None):
            if render_started_at is not None:
                assert permit.locked()
                dispatches.append(render_started_at)

        async def capture(*_args, **_kwargs):
            assert dispatches == [dispatched_at]
            now[0] += 2
            await asyncio.sleep(0)
            if outcome == "failed":
                raise RuntimeError("render failed")
            if outcome == "cancelled":
                raise asyncio.CancelledError
            return SimpleNamespace(png=b"png", quality={"passed": True, "verified": True, "failures": []})

        monkeypatch.setattr("services.svg_renderer.render_svg_with_metrics", capture)
        task = asyncio.create_task(render.attempt_render(
            ctx=ctx, request=request, presentation_id=uuid.UUID(int=1), index=0,
            content="page", payload={"attempts": [{"kind": "local", "receipt": {}}]},
            svg="<svg/>", ordinal=0, job=object(), checkpoint=checkpoint,
            async_session_maker=None, write_png=lambda _: {"png_sha256": "png", "png_path": "path"},
        ))
        await queued.wait()
        assert dispatches == []
        assert "render.work" not in recorder.observations()
        now[0] += 3
        if outcome == "waiting_cancelled":
            task.cancel()
        else:
            permit.release()
        if outcome in ("cancelled", "waiting_cancelled"):
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome == "failed":
            with pytest.raises(RuntimeError, match="render failed"):
                await task
        else:
            await task
        return recorder

    recorder = asyncio.run(scenario())
    observed = recorder.observations()
    assert observed["render.wait"]["durationMillis"] == 3000
    if outcome == "waiting_cancelled":
        assert dispatches == []
        assert "render.work" not in observed
    else:
        assert dispatches == [dispatched_at]
        assert observed["render.work"]["durationMillis"] == 2000
    detail, = recorder.render_attempts()
    waits = [stage for stage in detail["stages"] if stage["name"] == "wait"]
    assert waits[0]["startMillis"] == 0
    assert waits[0]["endMillis"] == 3000


def test_dispatch_updates_latest_row_timestamp_without_touching_payload_or_status(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            monkeypatch.setattr(checkpoints, "async_session_maker", maker)
            _, job = await _running_authored_job(maker, total_slides=1)
            await _seed_pages(maker, job, 1)
            payload = {"svg_content": "<svg/>", "attempts": [{"kind": "provider"}]}
            await checkpoints._checkpoint(job, 0, payload=payload)
            before, = await _pages(maker, job)
            digest = canonical_json_digest(before.svg_checkpoint)
            assert before.render_started_at is None
            request = _request()
            detail = timing.RenderAttemptObservations(request, page_index=0, attempt_ordinal=1)
            first = datetime(2026, 10, 26, tzinfo=timezone.utc)
            for dispatched_at in (first, first + timedelta(seconds=7)):
                monkeypatch.setattr(timing, "get_current_utc_datetime", lambda stamp=dispatched_at: stamp)
                await detail.dispatched(checkpoints._checkpoint, job, 0)
                row, = await _pages(maker, job)
                assert row.render_started_at == dispatched_at.replace(tzinfo=None)
                assert row.svg_checkpoint == before.svg_checkpoint
                assert canonical_json_digest(row.svg_checkpoint) == digest
                assert row.render_status == before.render_status == "pending"
                assert row.attempt_token == before.attempt_token
                assert row.author_started_at == before.author_started_at
                assert row.author_completed_at == before.author_completed_at
                assert row.render_completed_at is None
            # Later same-attempt payload persistence retains the latest dispatch.
            await checkpoints._checkpoint(job, 0, payload=payload)
            row, = await _pages(maker, job)
            assert row.render_started_at == (first + timedelta(seconds=7)).replace(tzinfo=None)
            assert canonical_json_digest(row.svg_checkpoint) == digest
            job.attempt_token = "stale-attempt"
            with pytest.raises(AuthoredStageError) as lost:
                await detail.dispatched(checkpoints._checkpoint, job, 0)
            assert lost.value.code == "authored_attempt_lost"
            unchanged, = await _pages(maker, job)
            assert unchanged.render_started_at == row.render_started_at
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["succeeded", "failed"])
def test_default_svg_path_persists_one_sanitized_provenance_record(monkeypatch, outcome):
    _, publish, _ = _install_harness(monkeypatch)
    fingerprint = "a" * 64
    monkeypatch.setattr(svg_partial_recovery, "_code_digest", lambda: fingerprint)
    ctx = _ctx()
    ctx.runtime_profile_provenance = {"author_concurrency": {
        "source": "env", "requested": 12, "executor_capacity": 4,
        "resolved": 4, "effective": ctx.runtime_profile.author_concurrency,
        "clamped_to_executor": True, "api_key": "private", "url": "https://private.invalid",
    }, "credentials": {"token": "private"}}

    async def prepare(*_args, **_kwargs):
        return ctx

    monkeypatch.setattr("services.generation_core.drivers.svg.collect._prepare_generation", prepare)
    request = _request()
    assert _run(request) == "published"
    for payload, _ in publish.await_args.args[-1]:
        assert "runtimeProvenance" not in payload
        assert "runtime_profile_provenance" not in payload
    recorder = request._authored_generation_timing_recorder
    pinned = recorder.runtime_provenance()
    assert pinned["codeFingerprint"] == fingerprint
    assert set(pinned) == {"schemaVersion", "codeFingerprint", "effectiveKnobs", "authorConcurrency"}
    assert set(pinned["effectiveKnobs"]) == {
        "author_concurrency", "render_concurrency", "illustration_budget",
        "illustration_timeout_seconds", "terminal_policy", "vision_advisory",
    }
    assert pinned["effectiveKnobs"]["render_concurrency"] == ctx.runtime_profile.render_concurrency
    assert pinned["authorConcurrency"]["requested"] == 12
    assert pinned["authorConcurrency"]["clamped_to_executor"] is True
    assert not any(word in json.dumps(pinned) for word in ("private", "api_key", "url", "credentials", "token"))
    # A second binding cannot relabel this attempt after its execution.
    monkeypatch.setattr(svg_partial_recovery, "_code_digest", lambda: "b" * 64)
    timing.bind_runtime_provenance(request, ctx)
    assert recorder.runtime_provenance() == pinned
    job_id = uuid.uuid4()
    sealed = asyncio.run(_persist_terminal_attempt_timing(
        job_id=job_id, context=_context("runtime-provenance"), recorder=recorder,
        provider_usage=_usage(3, 15), queue_wait_ms=0, outcome=outcome,
        retry_reason=None, terminal_error=None if outcome == "succeeded" else "render_failed", request=request,
    ))
    assert sealed["runtimeProvenance"] == pinned
    loaded, = receipts.load_generation_attempt_timing_receipts(job_id)
    assert loaded.runtime_provenance == pinned
    assert receipts._attempt_receipt_v2_from_dict(sealed).to_dict() == sealed
    assert receipts._attempt_receipt_v2_digest(replace(loaded, runtime_provenance=None)) == sealed["receiptDigest"]
    manifest = json.loads(receipts.generation_attempt_timing_receipt_path(job_id).read_text(encoding="utf-8"))
    stored, = manifest["receipts"].values()
    assert stored["runtimeProvenance"] == pinned


@pytest.mark.parametrize("field", ["codeFingerprint", "terminal_policy", "vision_advisory", "author_concurrency"])
def test_provenance_rejects_secret_values_even_under_known_keys(field, monkeypatch):
    monkeypatch.setattr(svg_partial_recovery, "_code_digest", lambda: "a" * 64)
    request = _request()
    timing.bind_runtime_provenance(request, _ctx())
    value = request._authored_generation_timing_recorder.runtime_provenance()
    malicious = deepcopy(value)
    if field == "codeFingerprint":
        malicious[field] = "https://private.invalid/key"
    else:
        malicious["effectiveKnobs"][field] = "secret-value"
    assert runtime_provenance_field(malicious) is None


def test_authored_receipt_without_svg_binding_keeps_provenance_absent():
    request = _request()
    request.strategy = "authored"
    recorder = request._authored_generation_timing_recorder
    for name in ("outline", "author", "render", "quality", "publication"):
        recorder.record_stage_ms(name, 0)
    sealed = asyncio.run(_persist_terminal_attempt_timing(
        job_id=uuid.uuid4(), context=_context("authored-provenance-absent"), recorder=recorder,
        provider_usage=_usage(0, 0), queue_wait_ms=0, outcome="succeeded",
        retry_reason=None, terminal_error=None, request=request,
    ))
    assert "runtimeProvenance" not in sealed
    assert receipts._attempt_receipt_v2_from_dict(sealed).runtime_provenance is None
