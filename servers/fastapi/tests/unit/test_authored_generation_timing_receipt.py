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
    create_generation_attempt_timing_receipt,
    create_generation_timing_receipt,
    generation_attempt_timing_receipt_path,
    generation_timing_receipt_path,
    load_generation_attempt_timing_receipts,
    load_generation_timing_receipt,
)
from services.authored_presentation_generation import _PERFORMANCE_STAGE_NAMES
from services.public_v2_orchestrator import SqlPublicV2Orchestrator
from services.authored_deck_plan_checkpoint import (
    resolve_with_authored_deck_plan_checkpoint_signal,
)
from services.authored_quality_receipt_v2 import (
    QualityReceiptLedger,
    create_quality_receipt_v2,
    quality_receipt_manifest_path,
)
from services.workspace_operation_runner import (
    _attach_durable_generation_timing,
    sanitize_workspace_event_payload,
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
            state="failed",
            error_code="authored_slide_capacity_exceeded",
            error_detail={"retryable": False},
            result=attached,
            progress={"percent": 80},
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
