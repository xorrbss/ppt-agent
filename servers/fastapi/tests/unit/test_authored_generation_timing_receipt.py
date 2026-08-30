from __future__ import annotations

import asyncio
from types import SimpleNamespace
import uuid

import pytest

from services.authored_generation_timing_receipt import (
    GENERATION_OBSERVABILITY_METRICS,
    create_generation_timing_receipt,
    generation_timing_receipt_path,
    load_generation_timing_receipt,
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
            else {"state": "unavailable", "reason": "counter_not_implemented"}
            if name in {"blockedRequest", "timeout", "crash"}
            else {"state": "observed", "value": 1}
        )
        for name in GENERATION_OBSERVABILITY_METRICS
    }

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
