"""Bench-only brief arms preserve ordinary request and style identities."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from models.generate_presentation_request import GeneratePresentationRequest
from services import presentation_generation_pipeline as pipeline, svg_outline_spans
from services.authored_generation_timing_receipt import (
    GenerationTimingRecorder,
    load_generation_attempt_timing_receipts,
)
from services.canvas_bench_speed import (
    INTERNAL_BENCH_SPEED_ARM_KEY,
    resolve_speed_arm,
    speed_provenance,
)
from services.presentation_generation_attempt_timing import _persist_terminal_attempt_timing
from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload


def _client():
    app = FastAPI()

    @app.post("/generate")
    def generate(request: GeneratePresentationRequest):
        return {"arm": resolve_speed_arm(request)}

    return TestClient(app)


@pytest.mark.parametrize("gate", [None, "false", "1", "TRUE"])
@pytest.mark.parametrize("arm", ["baseline", "brief_off"])
def test_bench_field_requires_explicit_gate(monkeypatch, gate, arm):
    monkeypatch.delenv("CANVAS_BENCH_ENABLED", raising=False)
    if gate is not None:
        monkeypatch.setenv("CANVAS_BENCH_ENABLED", gate)
    response = _client().post("/generate", json={"content": "deck", "bench_speed_arm": arm})
    assert response.status_code == 422
    assert "bench_field_disabled" in response.text


@pytest.mark.parametrize("arm", ["baseline", "brief_off"])
def test_gate_accepts_both_wire_spellings_and_excludes_field(monkeypatch, arm):
    monkeypatch.setenv("CANVAS_BENCH_ENABLED", "true")
    for field in ("bench_speed_arm", "benchSpeedArm"):
        response = _client().post("/generate", json={"content": "deck", field: arm})
        assert response.status_code == 200
        assert response.json() == {"arm": arm}
    request = GeneratePresentationRequest(content="deck", bench_speed_arm=arm)
    assert request.model_dump(mode="json") == GeneratePresentationRequest(content="deck").model_dump(mode="json")


@pytest.mark.parametrize("arm", ["brief_low", "plan_low", "brief_low_plan_low", "A", "B"])
def test_out_of_scope_arms_are_rejected(monkeypatch, arm):
    monkeypatch.setenv("CANVAS_BENCH_ENABLED", "true")
    assert _client().post("/generate", json={"content": "deck", "bench_speed_arm": arm}).status_code == 422


def test_no_field_or_null_needs_no_gate(monkeypatch):
    monkeypatch.delenv("CANVAS_BENCH_ENABLED", raising=False)
    for extra in ({}, {"bench_speed_arm": None}):
        assert _client().post("/generate", json={"content": "deck", **extra}).status_code == 200


@pytest.mark.parametrize("strategy", ["svg", "authored"])
@pytest.mark.parametrize("arm", ["baseline", "brief_off"])
def test_arm_round_trip_survives_gate_change_without_style_digest_change(monkeypatch, strategy, arm):
    monkeypatch.setenv("CANVAS_BENCH_ENABLED", "true")
    # The header design plan draws a random seed per admitted request; pin it
    # so the two requests compared here share one style identity.
    from services import svg_common_header_design_plan
    monkeypatch.setattr(svg_common_header_design_plan, "secrets", SimpleNamespace(token_hex=lambda n: "a" * (2 * n)))
    base = GeneratePresentationRequest(content="deck", strategy=strategy)
    request = GeneratePresentationRequest(content="deck", strategy=strategy, bench_speed_arm=arm)
    baseline_payload = _durable_request_payload(base)
    payload = _durable_request_payload(request)
    assert payload == {**baseline_payload, INTERNAL_BENCH_SPEED_ARM_KEY: arm}
    monkeypatch.delenv("CANVAS_BENCH_ENABLED")
    restored = _request_from_durable_payload(payload)
    assert restored.bench_speed_arm is None
    assert restored._bench_speed_arm == arm
    assert resolve_speed_arm(restored) == arm
    assert _durable_request_payload(restored) == payload
    assert resolve_speed_arm(_request_from_durable_payload(baseline_payload)) is None
    assert payload == {**baseline_payload, INTERNAL_BENCH_SPEED_ARM_KEY: arm}


def test_invalid_durable_arm_fails_closed(monkeypatch):
    payload = _durable_request_payload(GeneratePresentationRequest(content="deck"))
    payload[INTERNAL_BENCH_SPEED_ARM_KEY] = "brief_low"
    with pytest.raises(ValueError, match="bench_speed_arm_invalid"):
        _request_from_durable_payload(payload)


@pytest.mark.parametrize("arm,enabled,calls", [
    (None, True, 1), ("baseline", True, 1), ("brief_off", True, 0),
    (None, False, 0), ("baseline", False, 0), ("brief_off", False, 0),
])
@pytest.mark.parametrize("restored", [False, True])
def test_real_pipeline_brief_call_obeys_arm_and_existing_knob(monkeypatch, arm, enabled, calls, restored):
    monkeypatch.setenv("CANVAS_BENCH_ENABLED", "true")
    request = GeneratePresentationRequest(content="deck", bench_speed_arm=arm)
    if restored:
        request = _request_from_durable_payload(_durable_request_payload(request))
        monkeypatch.delenv("CANVAS_BENCH_ENABLED")
    monkeypatch.setattr(pipeline, "_revalidate_generation_admission_for_retry", AsyncMock())
    monkeypatch.setattr(pipeline, "is_authored_content_brief_enabled", lambda: enabled)
    monkeypatch.setattr(pipeline.MEM0_PRESENTATION_MEMORY_SERVICE, "enqueue", lambda *_args, **_kwargs: None)
    llm = AsyncMock(return_value=SimpleNamespace(brief=None))
    monkeypatch.setattr(pipeline, "generate_content_brief", llm)

    async def checkpoint(generate, **kwargs):
        return await generate(**kwargs)

    monkeypatch.setattr(pipeline, "run_content_brief_with_checkpoint", checkpoint)
    outline_contexts = []
    recorded_spans = []

    async def outline(*args, **kwargs):
        outline_contexts.append(args[3])
        recorded_spans.append(svg_outline_spans.current())
        raise HTTPException(status_code=418, detail="outline_test_boundary")
        yield  # pragma: no cover - preserve the outline async iterator contract

    monkeypatch.setattr(pipeline, "generate_ppt_outline", outline)
    with pytest.raises(HTTPException) as error:
        # Provider admission is outside this test's stubbed LLM boundary.
        asyncio.run(pipeline.generate_presentation_handler.__wrapped__(
            request, uuid.uuid4(), None, sql_session=AsyncMock(),
        ))
    assert error.value.status_code == 418
    assert llm.await_count == calls
    assert outline_contexts == [""]
    assert recorded_spans[0].bench_speed_arm == arm
    assert ("content_brief" in recorded_spans[0].ms) == bool(calls)


@pytest.mark.parametrize("arm", [None, "baseline", "brief_off"])
def test_span_line_changes_only_for_explicit_arm(caplog, arm):
    spans = svg_outline_spans.begin(bench_speed_arm=arm)
    with caplog.at_level(logging.INFO, logger=svg_outline_spans.LOGGER.name):
        spans.emit(uuid.UUID(int=1))
    line = caplog.records[-1].getMessage()
    expected = "svg_outline_span presentation=00000000-0000-0000-0000-000000000001 " + " ".join(
        f"{key}_ms=na" for key in svg_outline_spans.SPAN_KEYS
    )
    assert line == expected + (f" bench_speed_arm={arm}" if arm else "")


@pytest.mark.parametrize("arm", [None, "baseline", "brief_off"])
def test_attempt_receipt_provenance_is_durable_and_default_digest_unchanged(monkeypatch, tmp_path, arm):
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    job_id = uuid.uuid4()
    totals = dict.fromkeys((
        "logicalCalls", "physicalCalls", "retryCalls", "reportedUsageCalls",
        "unavailableUsageCalls", "pricedCalls", "latencyMs", "inputTokens", "outputTokens",
        "cachedTokens", "actualCostMicrounits", "estimatedCostMicrounits",
    ), 0)
    context = SimpleNamespace(claim=SimpleNamespace(
        attempt_token="bench-attempt", attempt_number=1, expected_revision=0,
    ))
    request = SimpleNamespace(_bench_speed_arm=arm)
    recorder = GenerationTimingRecorder()
    monkeypatch.setattr(GenerationTimingRecorder, "elapsed_ms", property(lambda _self: 0))
    timing_kwargs = dict(
        context=context, recorder=recorder,
        provider_usage=SimpleNamespace(snapshot=lambda: {"totals": totals, "stages": {}}),
        queue_wait_ms=0, outcome="failed", retry_reason="terminal_failure",
        terminal_error="test_boundary",
    )
    payload = asyncio.run(_persist_terminal_attempt_timing(job_id=job_id, request=request, **timing_kwargs))
    assert payload is not None
    receipt = load_generation_attempt_timing_receipts(job_id)[0]
    assert receipt.to_dict() == payload
    if arm:
        assert payload["provenance"] == {"benchSpeedArm": arm}
    else:
        assert "provenance" not in payload
        ordinary_payload = asyncio.run(_persist_terminal_attempt_timing(
            job_id=uuid.uuid4(), **timing_kwargs,
        ))
        assert ordinary_payload == payload


@pytest.mark.parametrize("value", [{}, {"benchSpeedArm": None}, {"benchSpeedArm": "brief_low"}, "brief_off"])
def test_receipt_provenance_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        speed_provenance(value)
