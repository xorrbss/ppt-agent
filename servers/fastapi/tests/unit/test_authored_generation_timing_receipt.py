from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from services.authored_generation_timing_receipt import (
    GENERATION_ATTEMPT_TIMING_STAGES,
    GENERATION_OBSERVABILITY_METRICS,
    GenerationTimingRecorder,
    _attempt_receipt_v2_from_dict,
    create_generation_attempt_timing_receipt,
    create_generation_attempt_timing_receipt_v2,
    create_generation_timing_receipt,
    generation_attempt_timing_receipt_path,
    generation_timing_receipt_path,
    load_generation_attempt_timing_receipts,
    load_generation_timing_receipt,
)
from services.authored_presentation_generation import _PERFORMANCE_STAGE_NAMES
from services.public_v2_orchestrator import (
    SqlPublicV2Orchestrator,
    _public_generation_timing,
)
from services.authored_deck_plan_checkpoint import (
    resolve_with_authored_deck_plan_checkpoint_signal,
)
from services.authored_quality_receipt_v2 import (
    QualityReceiptLedger,
    create_quality_receipt_v2,
    public_attempt_id,
    quality_receipt_manifest_path,
)
from services.workspace_operation_runner import (
    _attach_durable_generation_timing,
    sanitize_workspace_event_payload,
)
from services.workspace_generation_timing_payload import (
    generation_timing_result_projection,
)
from services.provider_usage_instrumentation import (
    ProviderOperationUsage,
    provider_call_admission,
    provider_operation_usage_scope,
    provider_usage_scope,
    record_provider_call,
)
from services.presentation_generation_operation_handler import (
    _persist_failed_attempt_timing,
    _persist_terminal_attempt_timing,
    _project_failed_attempt_timing,
)


def _quality_receipt(job_id: uuid.UUID):
    return create_quality_receipt_v2(
        QualityReceiptLedger(storage_path=quality_receipt_manifest_path(job_id)),
        attempt_id="private-attempt-token",
        revision=3,
        ledger_digest="sha256:" + "1" * 64,
        deck_html_digest="sha256:" + "2" * 64,
        evidence={
            "safety": {"status": "passed"},
            "capacity": {
                "browserObserved": True,
                "checks": {
                    name: True
                    for name in ("text", "title", "body", "font", "qid", "image")
                },
            },
            "sourceReadback": {"required": False, "status": "not_applicable"},
            "performance": {"status": "passed"},
        },
        requested_release_profile="release",
        pre_export_evaluation_profile="default",
    )


def test_generation_timing_receipt_is_durable_bounded_and_runner_projected(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    job_id = uuid.uuid4()
    quality_receipt = _quality_receipt(job_id)
    stages = {
        "outline": 11,
        "author": 22,
        "illustration": 23,
        "render": 24,
        "quality": 25,
        "repair": 33,
        "checkpoint": 44,
        "export": 55,
        "publication": 66,
    }
    observability = {
        name: (
            {"state": "observed", "value": "publish_committed"}
            if name == "fencingOutcome"
            else {"state": "observed", "value": "succeeded"}
            if name == "finalOutcome"
            else {"state": "observed", "value": {"author": 1}}
            if name
            in {"stageRetryCalls", "stageEstimatedCost", "stageActualCost"}
            else {"state": "unavailable", "reason": "counter_not_implemented"}
            if name in {"blockedRequest", "timeout", "crash"}
            else {"state": "observed", "value": 1}
        )
        for name in GENERATION_OBSERVABILITY_METRICS
        if not name.endswith("Duration")
    }
    observability.update(
        {
            f"{stage}Duration": {"state": "observed", "value": stages[stage]}
            for stage in _PERFORMANCE_STAGE_NAMES
        }
    )
    assert set(observability) == set(GENERATION_OBSERVABILITY_METRICS)

    first = create_generation_timing_receipt(
        job_id=job_id,
        quality_receipt=quality_receipt,
        attempt_number=2,
        stage_timings_ms=stages,
        total_ms=250,
        observability_metrics=observability,
    )
    second = create_generation_timing_receipt(
        job_id=job_id,
        quality_receipt=quality_receipt,
        attempt_number=2,
        stage_timings_ms=stages,
        total_ms=250,
        observability_metrics=observability,
    )

    assert second == first
    assert load_generation_timing_receipt(job_id) == first
    assert "private-attempt-token" not in generation_timing_receipt_path(job_id).read_text(
        encoding="utf-8"
    )

    claim = SimpleNamespace(
        kind="presentation.generate",
        operation_id=uuid.uuid4(),
        request_payload={"job_id": str(job_id)},
    )
    attached = asyncio.run(
        _attach_durable_generation_timing(
            claim,
            {"status": "completed", "job_id": str(job_id)},
        )
    )
    safe = sanitize_workspace_event_payload(
        attached,
        operation_kind="presentation.generate",
    )
    assert safe["stage_timings_ms"] == stages
    assert safe["total_ms"] == 250
    assert safe["generation_timing_receipt"]["qualityReceiptDigest"] == (
        quality_receipt.receipt_digest
    )
    assert safe["generation_timing_receipt"]["requestedReleasePolicyDigest"] == (
        quality_receipt.requested_release_policy_digest
    )
    assert safe["generation_timing_receipt"]["observability"] == {
        "schemaVersion": "generation-observability/v1",
        "metrics": observability,
    }
    assert safe["generation_timing_receipt"]["observability"]["metrics"][
        "repairDuration"
    ] == {"state": "observed", "value": stages["repair"]}

    now = datetime.now(UTC)
    public_status = SqlPublicV2Orchestrator._status(
        SimpleNamespace(
            id=uuid.uuid4(),
            presentation_id=uuid.uuid4(),
            kind="presentation.generate",
            # The status projection reads the admission payload for the
            # route receipt; this attempt carries none.
            request_payload=None,
            state="succeeded",
            error_code=None,
            error_detail=None,
            result={
                "generation_timing_receipt": first.to_dict(),
            },
            result_revision=3,
            progress={"percent": 100},
            attempt_number=2,
            retry_count=1,
            max_attempts=3,
            started_at=now + timedelta(milliseconds=5),
            completed_at=now + timedelta(milliseconds=255),
            created_at=now,
            updated_at=now + timedelta(milliseconds=255),
        ),
        SimpleNamespace(deadline_at=now + timedelta(minutes=5)),
        [],
    )
    timing = public_status.generation_timing
    assert timing is not None
    assert timing.outcome == "succeeded"
    assert timing.queue_wait_millis == 5
    assert timing.wall_millis == 250
    assert timing.logical_calls == 2
    assert timing.retry_reason == "retry_reason_unavailable"
    assert timing.stages.outline.duration_millis == 11
    assert timing.stages.provider == "not_started"
    assert timing.stages.authoring.duration_millis == 22
    assert timing.stages.repair.duration_millis == 33
    assert timing.stages.chromium_review.duration_millis == 49
    assert timing.stages.publication.duration_millis == 66
    assert timing.usage_availability.tokens == "available"
    assert timing.usage_availability.cost == "available"
    assert timing.worker.status == "observed"
    assert timing.terminal_error is None

    for excluded_names in (
        {"repairDuration"},
        {"repairDuration", "stageRetryCalls", "stageEstimatedCost", "stageActualCost"},
    ):
        legacy_job_id = uuid.uuid4()
        legacy_metrics = {
            name: metric
            for name, metric in observability.items()
            if name not in excluded_names
        }
        legacy = create_generation_timing_receipt(
            job_id=legacy_job_id,
            quality_receipt=_quality_receipt(legacy_job_id),
            attempt_number=2,
            stage_timings_ms=stages,
            total_ms=250,
            observability_metrics=legacy_metrics,
        )
        assert load_generation_timing_receipt(legacy_job_id) == legacy
        assert dict(legacy.observability_metrics) == legacy_metrics

    for invalid_metrics in (
        {**observability, "unknownMetric": {"state": "observed", "value": 1}},
        {name: metric for name, metric in observability.items() if name != "authorDuration"},
    ):
        with pytest.raises(ValueError, match="^generation_observability_metrics_invalid$"):
            create_generation_timing_receipt(
                job_id=job_id,
                quality_receipt=quality_receipt,
                attempt_number=2,
                stage_timings_ms=stages,
                total_ms=250,
                observability_metrics=invalid_metrics,
            )

    with pytest.raises(ValueError, match="binding_changed"):
        create_generation_timing_receipt(
            job_id=job_id,
            quality_receipt=quality_receipt,
            attempt_number=2,
            stage_timings_ms={**stages, "author": 23},
            total_ms=250,
            observability_metrics=observability,
        )


def test_generation_timing_projection_drops_invalid_or_incomplete_values() -> None:
    safe = sanitize_workspace_event_payload(
        {
            "stage_timings_ms": {
                "outline": float("nan"),
                "author": 1,
            },
            "total_ms": -1,
            "generation_timing_receipt": {"schemaVersion": "untrusted"},
        },
        operation_kind="presentation.generate",
    )
    assert safe == {}


def test_terminal_v2_timing_is_durable_for_success_failure_and_rc_invalid(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    usage = {
        "totals": {
            "logicalCalls": 1,
            "physicalCalls": 1,
            "retryCalls": 0,
            "reportedUsageCalls": 1,
            "unavailableUsageCalls": 0,
            "pricedCalls": 0,
            "latencyMs": 13,
            "inputTokens": 3,
            "outputTokens": 4,
            "actualCostMicrounits": 0,
            "estimatedCostMicrounits": 7,
        },
        "stages": {},
    }
    provider_usage = SimpleNamespace(snapshot=lambda: usage)
    context = SimpleNamespace(
        claim=SimpleNamespace(
            operation_id=uuid.uuid4(),
            attempt_token="private-v2-success-attempt",
            attempt_number=1,
            expected_revision=4,
            fencing_token=3,
        )
    )
    success_job_id = uuid.uuid4()
    success_recorder = GenerationTimingRecorder()
    for stage, duration in (
        ("outline", 5),
        ("author", 7),
        ("repair", 0),
        ("render", 11),
        ("quality", 3),
        ("publication", 2),
    ):
        success_recorder.record_stage_ms(stage, duration)
    success = asyncio.run(
        _persist_terminal_attempt_timing(
            job_id=success_job_id,
            context=context,
            recorder=success_recorder,
            provider_usage=provider_usage,
            queue_wait_ms=9,
            outcome="succeeded",
            retry_reason=None,
            terminal_error=None,
        )
    )
    assert success is not None
    assert success["schemaVersion"] == "generation-attempt-timing-receipt/v2"
    assert success["validity"] == "valid"
    assert success["queueWaitMillis"] == 9
    assert success["logicalCalls"] == 1
    assert success["tokenAvailability"] == "available"
    assert success["costAvailability"] == "partial"
    assert success["terminalError"] is None
    assert success["stages"]["provider"]["durationMillis"] == 13
    assert success["stages"]["chromiumReview"]["durationMillis"] == 14
    assert set(success["stages"]) == {
        "outline",
        "provider",
        "authoring",
        "repair",
        "chromiumReview",
        "publication",
    }
    persisted_success = load_generation_attempt_timing_receipts(success_job_id)[-1]
    assert persisted_success.to_dict() == success
    projected_success = _public_generation_timing(
        SimpleNamespace(
            kind="presentation.generate",
            state="succeeded",
            attempt_number=1,
            retry_count=0,
            created_at=datetime.now(UTC) - timedelta(seconds=1),
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            error_detail=None,
            error_code=None,
            progress={},
            result={"generation_timing_receipt": success},
        ),
        success,
    )
    assert projected_success is not None
    assert projected_success.stages.provider.duration_millis == 13
    assert projected_success.stages.chromium_review.duration_millis == 14
    assert projected_success.queue_wait_millis == 9
    assert projected_success.logical_calls == 1
    assert generation_timing_result_projection(success) == {
        "total_ms": success["wallMillis"],
        "generation_timing_receipt": success,
    }

    failure_job_id = uuid.uuid4()
    failure_recorder = GenerationTimingRecorder()
    failure_recorder.record_stage_ms("outline", 4)
    request = SimpleNamespace(
        template="authored",
        _authored_timing_stage="author",
        _authored_timing_stage_started_at=failure_recorder.started_at,
    )
    failure = asyncio.run(
        _persist_failed_attempt_timing(
            request=request,
            job_id=failure_job_id,
            status_error={"detail": {"stage": "author"}},
            context=context,
            error=RuntimeError("authored_slide_capacity_exceeded"),
            recorder=failure_recorder,
            provider_usage=provider_usage,
            queue_wait_ms=6,
        )
    )
    assert failure is not None
    assert failure["outcome"] == "failed"
    assert failure["terminalError"] == "authored_slide_capacity_exceeded"
    assert failure["stages"]["authoring"]["state"] == "observed"
    assert failure["stages"]["repair"] == {"state": "not_started"}
    assert failure["stages"]["publication"] == {"state": "not_started"}
    assert generation_timing_result_projection(failure)[
        "generation_timing_receipt"
    ] == failure

    invalid_job_id = uuid.uuid4()
    invalid = create_generation_attempt_timing_receipt_v2(
        job_id=invalid_job_id,
        attempt_id="private-v2-invalid-attempt",
        attempt_number=1,
        revision=1,
        outcome="succeeded",
        stage_observations={
            "outline": {
                "startMillis": 0,
                "endMillis": 5,
                "durationMillis": 5,
            }
        },
        executed_stages=(
            "outline",
            "provider",
            "authoring",
            "repair",
            "chromiumReview",
            "publication",
        ),
        queue_wait_ms=1,
        wall_ms=5,
        provider_usage=usage,
        retry_reason=None,
        worker="workspace-generation-worker",
        terminal_error=None,
    ).to_dict()
    assert invalid["validity"] == "rc_invalid"
    assert invalid["stages"]["provider"] == {
        "state": "invalid",
        "reason": "executed_duration_missing",
    }
    assert "provider:executed_duration_missing" in invalid["invalidReasons"]


def test_failed_attempt_timing_is_attached_and_exposed_by_public_v2_status(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    job_id = uuid.uuid4()
    recorder = GenerationTimingRecorder()
    for index, stage in enumerate(sorted(GENERATION_ATTEMPT_TIMING_STAGES)):
        recorder.record_stage_ms(stage, index + 1)
    request = SimpleNamespace(
        template="authored",
        _authored_timing_stage="render",
        _authored_timing_stage_started_at=recorder.started_at,
    )
    operation_id = uuid.uuid4()
    context = SimpleNamespace(
        claim=SimpleNamespace(
            operation_id=operation_id,
            attempt_token="private-failed-attempt-token",
            attempt_number=1,
            expected_revision=0,
        )
    )
    persisted = asyncio.run(
        _persist_failed_attempt_timing(
            request=request,
            job_id=job_id,
            status_error={"detail": {"stage": "render"}},
            context=context,
            error=RuntimeError("authored_slide_capacity_exceeded"),
            recorder=recorder,
            provider_usage=ProviderOperationUsage(),
        )
    )
    assert persisted is not None
    receipt = load_generation_attempt_timing_receipts(job_id)[0]
    assert persisted == receipt.to_dict()
    claim = SimpleNamespace(
        kind="presentation.generate",
        operation_id=operation_id,
        attempt_token="private-failed-attempt-token",
        attempt_number=1,
        request_payload={"job_id": str(job_id)},
    )
    attached = asyncio.run(
        _attach_durable_generation_timing(claim, {"status": "failed"})
    )
    assert attached["generation_timing_receipt"] == receipt.to_dict()

    now = datetime.now(UTC)
    status = SqlPublicV2Orchestrator._status(
        SimpleNamespace(
            id=claim.operation_id,
            presentation_id=uuid.uuid4(),
            kind="presentation.generate",
            # The status projection reads the admission payload for the
            # route receipt; this attempt carries none.
            request_payload=None,
            state="failed",
            error_code="authored_slide_capacity_exceeded",
            error_detail={"retryable": False},
            result=attached,
            progress={"percent": 80},
            attempt_number=1,
            retry_count=0,
            max_attempts=3,
            created_at=now,
            updated_at=now,
        ),
        SimpleNamespace(deadline_at=now + timedelta(minutes=5)),
        [],
    )
    assert status.generation_timing_receipt == receipt.to_dict()
    assert status.generation_timing_receipt["providerUsage"]["totals"][
        "logicalCalls"
    ] == 0
    assert status.generation_timing is not None
    assert status.generation_timing.attempt_number == 1
    assert status.generation_timing.outcome == "failed"
    assert status.generation_timing.stages.outline.duration_millis >= 0
    assert status.generation_timing.stages.provider.duration_millis >= 0
    assert status.generation_timing.stages.authoring.duration_millis >= 0
    assert status.generation_timing.stages.repair.duration_millis >= 0
    assert status.generation_timing.stages.chromium_review.duration_millis >= 0
    assert status.generation_timing.stages.publication.duration_millis >= 0
    assert status.generation_timing.logical_calls == 0
    assert status.generation_timing.usage_availability.tokens == "not_started"
    assert status.generation_timing.usage_availability.cost == "not_started"
    # The handler stamps the worker on every attempt receipt, so a failed
    # attempt still carries worker evidence rather than an empty slot.
    assert status.generation_timing.worker.status == "observed"
    assert status.generation_timing.worker.worker == "workspace-generation-worker"
    assert status.generation_timing.terminal_error == "authored_slide_capacity_exceeded"
    assert status.readiness_scope == "artifact:fidelity"
    assert status.fidelity_artifact_ready is False
    assert status.native_editability_ready is False
    assert status.promotion_ready is False
    assert status.deployment_authorized is False

    # PostgreSQL projection is authoritative after a handled failure; the
    # local sidecar may disappear before a later runner/status read.
    generation_attempt_timing_receipt_path(job_id).unlink()
    embedded = asyncio.run(
        _attach_durable_generation_timing(
            claim,
            {"generation_timing_receipt": receipt.to_dict()},
        )
    )
    assert embedded["generation_timing_receipt"] == receipt.to_dict()


def test_failed_attempt_timing_projection_is_fenced_and_committed(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    operation_id = uuid.uuid4()
    operation = SimpleNamespace(
        id=operation_id,
        state="running",
        attempt_token="attempt-token",
        fencing_token=7,
        version=3,
        result={"status": "running"},
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=operation),
        execute=AsyncMock(return_value=SimpleNamespace(rowcount=1)),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    context = SimpleNamespace(
        claim=SimpleNamespace(
            operation_id=operation_id,
            attempt_token="attempt-token",
            fencing_token=7,
        )
    )
    receipt = create_generation_attempt_timing_receipt(
        job_id=uuid.uuid4(),
        attempt_id="attempt-token",
        attempt_number=1,
        revision=0,
        outcome="failed",
        failure_stage="render",
        stage_timings_ms={"render": 5},
        total_ms=5,
    )

    assert asyncio.run(
        _project_failed_attempt_timing(
            session=session,
            context=context,
            receipt_payload=receipt.to_dict(),
        )
    )
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


def test_failed_attempt_receipt_preserves_provider_and_checkpoint_signals(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> tuple[object, dict[str, object], dict[str, object]]:
        monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
        monkeypatch.setattr(
            "services.provider_usage_instrumentation.durable_provider_runtime_required",
            lambda: False,
        )

        async def store_receipt(_receipt):
            return None

        monkeypatch.setattr(
            "services.provider_usage_receipt_service.record_provider_usage_receipt_detached",
            store_receipt,
        )
        job_id = uuid.uuid4()
        recorder = GenerationTimingRecorder()
        with recorder.stage("outline"):
            await asyncio.sleep(0)

        with provider_operation_usage_scope() as provider_usage:
            with provider_usage_scope(
                job_id=job_id,
                operation="authored_generation",
                stage="author",
                attempt_number=2,
            ):
                async with provider_call_admission(
                    estimated_cost_microunits=7,
                    semantic_slot="deck.primary",
                ):
                    await asyncio.sleep(0)
                async with provider_call_admission(
                    is_retry=True,
                    estimated_cost_microunits=7,
                    semantic_slot="deck.primary",
                ):
                    await asyncio.sleep(0)
                await record_provider_call(
                    provider="test",
                    model="model",
                    response=SimpleNamespace(
                        usage=SimpleNamespace(input_tokens=3, output_tokens=4)
                    ),
                    latency_ms=1,
                )
            provider_snapshot = provider_usage.snapshot()

        value, checkpoint_signal = (
            await resolve_with_authored_deck_plan_checkpoint_signal(
                generation_job=None,
                stage="deck_plan",
                input_contract={"schemaVersion": "test/v1"},
                resolve=lambda: asyncio.sleep(0, result={"value": 1}),
                encode=lambda item: item,
                decode=lambda item: dict(item),
                observe=lambda signal: recorder.record_checkpoint_signal(
                    signal.to_dict()
                ),
            )
        )
        assert value == {"value": 1}

        async def fail_checkpoint_resolution():
            raise RuntimeError("expected checkpoint resolution failure")

        with pytest.raises(RuntimeError, match="expected checkpoint"):
            await resolve_with_authored_deck_plan_checkpoint_signal(
                generation_job=None,
                stage="header",
                input_contract={"schemaVersion": "test/v1"},
                resolve=fail_checkpoint_resolution,
                encode=lambda item: item,
                decode=lambda item: dict(item),
                observe=lambda signal: recorder.record_checkpoint_signal(
                    signal.to_dict()
                ),
            )
        recorder.record_stage_ms(
            "provider", provider_snapshot["totals"]["latencyMs"]
        )
        recorder.record_stage_ms("checkpoint", checkpoint_signal.total_ms)
        stages = recorder.snapshot_stage_timings_ms()
        signals = recorder.checkpoint_signals()
        receipt = create_generation_attempt_timing_receipt(
            job_id=job_id,
            attempt_id="private-attempt-token",
            attempt_number=2,
            revision=9,
            outcome="failed",
            failure_stage="author",
            stage_timings_ms=stages,
            total_ms=recorder.elapsed_ms,
            provider_usage=provider_snapshot,
            checkpoint_signals=signals,
        )
        return receipt, provider_snapshot, {
            "job_id": job_id,
            "stages": stages,
            "signals": signals,
        }

    receipt, provider_snapshot, captured = asyncio.run(scenario())
    job_id = captured["job_id"]
    assert receipt.quality_receipt_digest is None
    assert receipt.outcome == "failed"
    assert receipt.failure_stage == "author"
    assert provider_snapshot["totals"]["logicalCalls"] == 1
    assert provider_snapshot["totals"]["physicalCalls"] == 2
    assert provider_snapshot["totals"]["retryCalls"] == 1
    assert provider_snapshot["stages"]["author"]["reportedUsageCalls"] == 1
    assert receipt.checkpoint_signals[0]["enabled"] is False
    assert receipt.checkpoint_signals[0]["reused"] is False
    assert receipt.checkpoint_signals[0]["outcome"] == "succeeded"
    assert receipt.checkpoint_signals[1]["outcome"] == "failed"
    assert load_generation_attempt_timing_receipts(job_id) == (receipt,)
    assert "private-attempt-token" not in generation_attempt_timing_receipt_path(
        job_id
    ).read_text(encoding="utf-8")

    assert create_generation_attempt_timing_receipt(
        job_id=job_id,
        attempt_id="private-attempt-token",
        attempt_number=2,
        revision=9,
        outcome="failed",
        failure_stage="author",
        stage_timings_ms=captured["stages"],
        total_ms=receipt.total_ms,
        provider_usage=provider_snapshot,
        checkpoint_signals=captured["signals"],
    ) == receipt

    with pytest.raises(ValueError, match="binding_changed"):
        create_generation_attempt_timing_receipt(
            job_id=job_id,
            attempt_id="private-attempt-token",
            attempt_number=2,
            revision=9,
            outcome="failed",
            failure_stage="render",
            stage_timings_ms=captured["stages"],
            total_ms=receipt.total_ms,
            provider_usage=provider_snapshot,
            checkpoint_signals=captured["signals"],
        )


# --- E4 (docs/generation-programme-master-design-20260904.md section 8, D13):
# per-stage wallMillis, providerRateProfile, normalizations, qualityLoop.variant
# on the v2 attempt-timing receipt. ------------------------------------------


def _v2_usage() -> dict:
    return {
        "totals": {
            "logicalCalls": 1,
            "physicalCalls": 1,
            "retryCalls": 0,
            "reportedUsageCalls": 1,
            "unavailableUsageCalls": 0,
            "pricedCalls": 1,
            "latencyMs": 13,
            "inputTokens": 3,
            "outputTokens": 4,
            "actualCostMicrounits": 0,
            "estimatedCostMicrounits": 7,
        },
        "stages": {},
    }


def test_v2_stage_wall_millis_reveals_a_gap_the_summed_duration_hides(
    tmp_path, monkeypatch
) -> None:
    """D13: chromiumReview's durationMillis is a work sum; wallMillis is the
    real start-to-end span, so the two only agree when nothing overlapped and
    no gap opened up between the merged source stages."""
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    job_id = uuid.uuid4()
    receipt = create_generation_attempt_timing_receipt_v2(
        job_id=job_id,
        attempt_id="private-wall-gap-attempt",
        attempt_number=1,
        revision=0,
        outcome="succeeded",
        stage_observations={
            "outline": {"startMillis": 0, "endMillis": 5, "durationMillis": 5},
            "render": {"startMillis": 5, "endMillis": 15, "durationMillis": 10},
            # A 5ms idle gap between render ending and quality starting.
            "quality": {"startMillis": 20, "endMillis": 25, "durationMillis": 5},
            "publication": {"startMillis": 25, "endMillis": 30, "durationMillis": 5},
        },
        executed_stages=("outline", "authoring", "chromiumReview", "publication"),
        queue_wait_ms=0,
        wall_ms=30,
        provider_usage=_v2_usage(),
        retry_reason=None,
        worker="workspace-generation-worker",
        terminal_error=None,
    )
    review = receipt.to_dict()["stages"]["chromiumReview"]
    assert review["durationMillis"] == 15
    assert review["wallMillis"] == 20
    assert review["wallMillis"] != review["durationMillis"]
    assert load_generation_attempt_timing_receipts(job_id)[-1].to_dict() == (
        receipt.to_dict()
    )


def _v2_digest(payload: dict) -> str:
    import hashlib as _hashlib
    import json as _json

    clean = dict(payload)
    clean.pop("receiptDigest", None)
    return "sha256:" + _hashlib.sha256(
        _json.dumps(
            clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def _v2_receipt_dict(stages: dict, attempt_id: str) -> dict:
    payload = dict(
        schemaVersion="generation-attempt-timing-receipt/v2",
        attemptId=public_attempt_id(attempt_id),
        attemptNumber=1,
        revision=0,
        outcome="succeeded",
        stages=stages,
        queueWaitMillis=0,
        wallMillis=11,
        logicalCalls=0,
        retryReason=None,
        tokenAvailability="not_started",
        costAvailability="not_started",
        worker="w",
        terminalError=None,
        validity="valid",
        invalidReasons=[],
        providerUsage=None,
        receiptDigest="",
    )
    payload["receiptDigest"] = _v2_digest(payload)
    return payload


def test_v2_stage_observed_dict_accepts_missing_wall_and_rejects_non_numeric() -> None:
    legacy_stages = {
        "outline": {
            "state": "observed", "startMillis": 0, "endMillis": 5, "durationMillis": 5,
        },
        "provider": {"state": "not_started"},
        "authoring": {
            "state": "observed", "startMillis": 0, "endMillis": 5, "durationMillis": 5,
        },
        "repair": {"state": "not_started"},
        "chromiumReview": {
            "state": "observed", "startMillis": 5, "endMillis": 10, "durationMillis": 5,
        },
        "publication": {
            "state": "observed", "startMillis": 10, "endMillis": 11, "durationMillis": 1,
        },
    }

    # A receipt written before D13 landed has no wallMillis at all -- still
    # loads, and its digest is unaffected by the new (absent) field.
    legacy = _v2_receipt_dict(legacy_stages, "private-wall-legacy")
    receipt = _attempt_receipt_v2_from_dict(legacy)
    assert "wallMillis" not in receipt.to_dict()["stages"]["outline"]
    assert receipt.to_dict() == legacy

    # A non-numeric wallMillis on one stage is rejected.
    bad_stages = dict(legacy_stages)
    bad_stages["outline"] = {**legacy_stages["outline"], "wallMillis": "not-a-number"}
    with pytest.raises(ValueError, match="generation_timing_outline_wall_invalid"):
        _attempt_receipt_v2_from_dict(_v2_receipt_dict(bad_stages, "private-wall-bad"))

    # A valid numeric wallMillis on one stage round-trips.
    good_stages = dict(legacy_stages)
    good_stages["outline"] = {**legacy_stages["outline"], "wallMillis": 4}
    good = _attempt_receipt_v2_from_dict(_v2_receipt_dict(good_stages, "private-wall-good"))
    assert good.to_dict()["stages"]["outline"]["wallMillis"] == 4


def test_v2_receipt_carries_pinned_provider_rate_profile_normalizations_and_variant(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "token")
    from services.authored_provider_rate_profile import resolve_authored_rate_profile

    profile = resolve_authored_rate_profile()
    assert profile.name == "token"

    job_id = uuid.uuid4()
    normalization_entries = [
        {
            "rule": "hidden_element_without_bound_text_removed",
            "detail": "div",
            "count": 2,
        }
    ]
    receipt = create_generation_attempt_timing_receipt_v2(
        job_id=job_id,
        attempt_id="private-e4-fields-attempt",
        attempt_number=1,
        revision=0,
        outcome="succeeded",
        stage_observations={
            "outline": {"startMillis": 0, "endMillis": 5, "durationMillis": 5},
        },
        executed_stages=("outline",),
        queue_wait_ms=0,
        wall_ms=5,
        provider_usage=_v2_usage(),
        retry_reason=None,
        worker="workspace-generation-worker",
        terminal_error=None,
        provider_rate_profile=profile.name,
        normalizations=normalization_entries,
        quality_loop_variant="density-tight",
    )
    payload = receipt.to_dict()
    assert payload["providerRateProfile"] == "token"
    assert payload["normalizations"] == normalization_entries
    assert payload["qualityLoop"] == {"variant": "density-tight"}
    assert load_generation_attempt_timing_receipts(job_id)[-1].to_dict() == payload


def test_v2_legacy_receipt_digest_is_unaffected_by_the_new_e4_fields() -> None:
    """A receipt sealed before E4 must still verify: the new fields are only
    serialized when set, so ``to_dict()`` for an old receipt is unchanged."""
    stages = {
        "outline": {
            "state": "observed", "startMillis": 0, "endMillis": 5, "durationMillis": 5,
        },
        "provider": {"state": "not_started"},
        "authoring": {
            "state": "observed", "startMillis": 0, "endMillis": 5, "durationMillis": 5,
        },
        "repair": {"state": "not_started"},
        "chromiumReview": {
            "state": "observed", "startMillis": 5, "endMillis": 10, "durationMillis": 5,
        },
        "publication": {
            "state": "observed", "startMillis": 10, "endMillis": 11, "durationMillis": 1,
        },
    }
    payload = _v2_receipt_dict(stages, "private-legacy-e4")
    receipt = _attempt_receipt_v2_from_dict(payload)
    assert receipt.provider_rate_profile is None
    assert receipt.normalizations == ()
    assert receipt.quality_loop_variant is None
    assert receipt.to_dict() == payload
    assert "providerRateProfile" not in receipt.to_dict()
    assert "normalizations" not in receipt.to_dict()
    assert "qualityLoop" not in receipt.to_dict()


def test_v2_provider_rate_profile_rejects_an_unknown_name() -> None:
    with pytest.raises(
        ValueError, match="generation_attempt_v2_provider_rate_profile_invalid"
    ):
        create_generation_attempt_timing_receipt_v2(
            job_id=uuid.uuid4(),
            attempt_id="private-bad-profile",
            attempt_number=1,
            revision=0,
            outcome="succeeded",
            stage_observations={
                "outline": {"startMillis": 0, "endMillis": 1, "durationMillis": 1},
            },
            executed_stages=("outline",),
            queue_wait_ms=0,
            wall_ms=1,
            provider_usage=_v2_usage(),
            retry_reason=None,
            worker="w",
            terminal_error=None,
            provider_rate_profile="bogus",
        )


def test_v2_normalizations_reject_a_malformed_entry() -> None:
    with pytest.raises(
        ValueError, match="generation_attempt_v2_normalizations_invalid"
    ):
        create_generation_attempt_timing_receipt_v2(
            job_id=uuid.uuid4(),
            attempt_id="private-bad-normalizations",
            attempt_number=1,
            revision=0,
            outcome="succeeded",
            stage_observations={
                "outline": {"startMillis": 0, "endMillis": 1, "durationMillis": 1},
            },
            executed_stages=("outline",),
            queue_wait_ms=0,
            wall_ms=1,
            provider_usage=_v2_usage(),
            retry_reason=None,
            worker="w",
            terminal_error=None,
            normalizations=[{"rule": "x"}],
        )
