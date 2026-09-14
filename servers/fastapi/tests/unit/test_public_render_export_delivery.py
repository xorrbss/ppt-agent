from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import inspect
import textwrap
import uuid

import pytest

from api.v2.contracts import PublicArtifactV2
from models.sql.public_admission import PublicOperationAdmission
from models.sql.workspace import WorkspaceOperation
from services.public_export_operation import (
    HYBRID_EDITABILITY_POLICY_VERSION,
    PUBLIC_RENDER_WORKER_PROTOCOL_VERSION,
    PublicExportHybridFallback,
    _bounded_editability_evidence,
    public_render_runtime_binding_digest,
    render_and_export_authored_htmls,
)
from services.public_export_join import _cached_hybrid_worker_evidence
from services.public_v2_orchestrator import SqlPublicV2Orchestrator


def test_editable_export_delivery_keeps_partial_unknown_and_deterministic_failure_contract() -> None:
    from services.workspace_quality_export_operation_handlers import (
        _public_export_failure_is_non_retryable,
    )

    partial = {
        "schemaVersion": "public-hybrid-editability/v1",
        "verdict": "partially-editable",
        "requiredTargetCount": 4,
        "nativeCorrespondenceCount": 3,
    }
    assert _bounded_editability_evidence(partial) == partial
    assert _bounded_editability_evidence(None)["verdict"] == "unknown"
    assert HYBRID_EDITABILITY_POLICY_VERSION.startswith("public-hybrid-editability-policy/")
    assert HYBRID_EDITABILITY_POLICY_VERSION != "public-hybrid-editability-policy/v1"
    assert PublicExportHybridFallback("required-target-missing").code == (
        "public_export_hybrid_not_editable"
    )
    assert _public_export_failure_is_non_retryable(
        "public_export_hybrid_not_editable"
    )
    assert _public_export_failure_is_non_retryable("hybrid_job_wire_limit")
    assert _public_export_failure_is_non_retryable(
        "public_worker_payload_too_large"
    )
    assert _public_export_failure_is_non_retryable(
        "public_worker_cache_budget_exceeded"
    )
    assert _public_export_failure_is_non_retryable(
        "trusted_worker_staging_capacity_exceeded"
    )
    assert not _public_export_failure_is_non_retryable(
        "public_export_editability_evidence_pending"
    )

    operation = WorkspaceOperation(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        owner_scope="owner",
        conversation_id=uuid.uuid4(),
        presentation_id=uuid.uuid4(),
        anchor_position=1,
        kind="export.pptx",
        state="succeeded",
        idempotency_key_hash="1" * 64,
        request_hash="2" * 64,
        result={
            "public_export_worker_evidence": {
                "editability": partial,
                "quality": {
                    "schemaVersion": "public-hybrid-quality-summary/v1",
                    "status": "partially-editable",
                    "editableSlides": 3,
                    "imageFallbackSlides": 1,
                    "slideFallbacks": [{"n": 4, "code": "selection-obstructed"}],
                },
                "timing": {
                    "schemaVersion": "public-hybrid-timing/v1",
                    "wallMillis": 321,
                    "slides": [{"n": 4, "conversionMillis": 123, "recovery": {
                        "attempts": 2, "firstFailureCode": "measurement-timeout",
                        "cleanupFailure": True, "retryDisposition": "recovered",
                        "rawError": "private source must not survive",
                    }}],
                },
                "localFitFallbackPages": [{"n": 4, "code": "raster_fragment_placement_mismatch", "diagnostics": {
                    "schemaVersion": "raster-fragment-diagnostic/v1", "bandIndex": 0,
                    "expectedPlacementPt": {"x": 2.0, "y": float("nan"), "source": "private"},
                    "candidates": [{"imageIndex": 1, "digestSha256": "a" * 64,
                                    "placementPt": {"x": 3.0}, "rawError": "private"}] * 8,
                    "rawError": "private",
                }}],
            }
        },
    )
    cached = _cached_hybrid_worker_evidence(operation)
    assert cached is not None
    assert cached["editability"]["verdict"] == "partially-editable"
    assert cached["editability"]["requiredTargetCount"] == 4
    assert cached["editability"]["nativeCorrespondenceCount"] == 3
    assert cached["quality"]["slideFallbacks"] == [
        {"n": 4, "code": "selection-obstructed"}
    ]
    assert "timing" not in cached
    assert cached["producerTiming"]["wallMillis"] == 321
    assert cached["producerTiming"]["slides"] == [{
        "n": 4, "conversionMillis": 123, "recovery": {
            "attempts": 2, "firstFailureCode": "measurement-timeout",
            "cleanupFailure": True, "retryDisposition": "recovered",
        },
    }]
    diagnostic = cached["localFitFallbackPages"][0]["diagnostics"]
    assert diagnostic["expectedPlacementPt"] == {"x": 2.0}
    assert len(diagnostic["candidates"]) == 4
    assert "rawError" not in diagnostic
    assert "rawError" not in diagnostic["candidates"][0]


def test_public_status_projects_bounded_export_execution_and_publication_receipt() -> None:
    from services import workspace_quality_export_operation_handlers as handlers

    # These two producer fields must follow the actual canonical exporter,
    # rather than independently pinning a protocol version in public evidence.
    execute = ast.parse(textwrap.dedent(inspect.getsource(handlers.WorkspaceExportOperationHandler.execute)))
    runtime_versions = [
        value
        for node in ast.walk(execute)
        if isinstance(node, ast.Dict) and any(
            isinstance(key, ast.Constant) and key.value == "compiler"
            and isinstance(value, ast.Constant) and value.value == "public-isolated-export"
            for key, value in zip(node.keys, node.values, strict=True)
        )
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == "runtime_version"
    ]
    receipt_versions = [
        keyword.value
        for node in ast.walk(execute)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "project_persisted_authored_quality_receipt_v3"
        for keyword in node.keywords if keyword.arg == "exporter_version"
    ]
    assert len(runtime_versions) == len(receipt_versions) == 1
    for version in [*runtime_versions, *receipt_versions]:
        assert isinstance(version, ast.Name)
        assert version.id == "PUBLIC_RENDER_WORKER_PROTOCOL_VERSION"
    assert handlers.PUBLIC_RENDER_WORKER_PROTOCOL_VERSION == PUBLIC_RENDER_WORKER_PROTOCOL_VERSION

    assert callable(render_and_export_authored_htmls)
    now = datetime(2026, 8, 31, tzinfo=timezone.utc)
    owner_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    runtime_binding = {
        "schemaVersion": "public-render-runtime-binding/v2",
        "mode": "owned-local-controller-observed",
        "checkpointReuseEligible": True,
        "releaseEligible": False,
        "releaseEligibilityReason": "runtime-binding-is-not-release-authorization",
        "workerImageDigest": None,
        "chromeRuntimeDigest": "sha256:" + "6" * 64,
        "fontInventoryDigest": "sha256:" + "7" * 64,
        "limitations": [
            "controller-worker-colocation-required",
        ],
    }
    runtime_identity = {
        **runtime_binding,
        "bindingDigest": public_render_runtime_binding_digest(runtime_binding),
    }
    timing = {
        "schemaVersion": "public-export-execution-receipt/v1",
        "slideCount": 5,
        "renderConcurrencyLimit": 4,
        "renderConcurrencyPeak": 2,
        "renderGateWaitMillis": 18,
        "renderSchedulerQueueWaitMillis": 9,
        "renderWorkerCumulativeMillis": 420,
        "renderWallMillis": 240,
        "artifactStageMillis": 32,
        "htmlPersistMillis": 7,
        "exportWorkerMillis": 80,
        "exportSchedulerQueueWaitMillis": 3,
        "operationWallMillis": 370,
        "renderExecutionCount": 5,
        "renderBatchExecutionCount": 1,
        "chromiumProcessLaunchCount": 1,
        "browserReuseCount": 4,
        "renderCheckpointReuseCount": 0,
        "renderSingleflightJoinCount": 0,
        "renderRuntimeIdentity": runtime_identity,
        "deckRenderIdentityDigest": "sha256:" + "8" * 64,
    }
    operation = WorkspaceOperation(
        id=operation_id,
        owner_id=owner_id,
        owner_scope=str(owner_id),
        conversation_id=uuid.uuid4(),
        presentation_id=presentation_id,
        anchor_position=1,
        kind="export.pptx",
        state="succeeded",
        idempotency_key_hash="1" * 64,
        request_hash="2" * 64,
        attempt_number=2,
        retry_count=1,
        max_attempts=3,
        progress={"stage": "export_completed", "queue_position": 3},
        result={"public_export_execution_receipt": timing},
        result_revision=9,
        created_at=now,
        updated_at=now + timedelta(seconds=1),
    )
    admission = PublicOperationAdmission(
        operation_id=operation_id,
        policy_version="public-test/v1",
        owner_id=owner_id,
        tenant_id="tenant-test",
        trace_id_hash="3" * 64,
        operation_kind="export",
        resource_units=5,
        policy_digest="4" * 64,
        deadline_at=now + timedelta(minutes=5),
        planned_request_limit=0,
        retry_request_limit=0,
        estimated_cost_limit_microunits=0,
        actual_cost_limit_microunits=0,
    )
    artifact = PublicArtifactV2(
        artifact_id="artifact_1",
        operation_id=str(operation_id),
        presentation_id=str(presentation_id),
        kind="presentation-pptx",
        digest="sha256:" + "5" * 64,
        size_bytes=4096,
        media_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        download="/api/v2/artifacts/artifact_1/download",
    )

    projected = SqlPublicV2Orchestrator._status(operation, admission, [artifact])

    assert projected.status == "succeeded"
    assert projected.phase == "packaging"
    assert projected.execution_receipt is not None
    assert projected.execution_receipt.queue_position == 3
    assert projected.execution_receipt.retry_count == 1
    assert projected.execution_receipt.publication == "published"
    assert projected.execution_receipt.export == "completed"
    assert projected.execution_receipt.timing is not None
    assert projected.execution_receipt.timing.render_concurrency_peak == 2
    assert projected.execution_receipt.timing.render_scheduler_queue_wait_millis == 9
    assert projected.execution_receipt.timing.render_wall_millis == 240
    public_status = projected.model_dump(mode="json", by_alias=True)
    assert (
        public_status["executionReceipt"]["timing"]["renderRuntimeIdentity"]
        == runtime_identity
    )

    operation.result = {
        "public_export_execution_receipt": {
            **timing,
            "renderRuntimeIdentity": {
                **runtime_identity,
                "bindingDigest": "sha256:" + ("0" * 64),
            },
        }
    }
    rejected = SqlPublicV2Orchestrator._status(operation, admission, [artifact])
    assert rejected.execution_receipt is not None
    assert rejected.execution_receipt.timing is None


def test_export_quality_projection_uses_validated_actual_worker_render_identity() -> None:
    from services.workspace_quality_export_operation_handlers import (
        NonRetryableWorkspaceOperationError,
        _validated_public_export_render_identity,
    )

    runtime_binding = {
        "schemaVersion": "public-render-runtime-binding/v2",
        "mode": "owned-local-controller-observed",
        "checkpointReuseEligible": True,
        "releaseEligible": False,
        "releaseEligibilityReason": "runtime-binding-is-not-release-authorization",
        "workerImageDigest": None,
        "chromeRuntimeDigest": "sha256:" + "6" * 64,
        "fontInventoryDigest": "sha256:" + "7" * 64,
        "limitations": ["controller-worker-colocation-required"],
    }
    runtime_digest = public_render_runtime_binding_digest(runtime_binding)
    deck_digest = "sha256:" + "8" * 64
    metrics = {
        "public_export_execution_receipt": {
            "schemaVersion": "public-export-execution-receipt/v1",
            "renderRuntimeIdentity": {
                **runtime_binding,
                "bindingDigest": runtime_digest,
            },
            "deckRenderIdentityDigest": deck_digest,
        }
    }

    assert _validated_public_export_render_identity(metrics) == (
        runtime_digest,
        deck_digest,
    )

    metrics["public_export_execution_receipt"]["renderRuntimeIdentity"][
        "bindingDigest"
    ] = "sha256:" + "0" * 64
    with pytest.raises(
        NonRetryableWorkspaceOperationError,
        match="public_export_render_identity_invalid",
    ):
        _validated_public_export_render_identity(metrics)
