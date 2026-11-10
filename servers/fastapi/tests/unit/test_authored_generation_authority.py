import uuid


from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_generation_timing_receipt import (
    GENERATION_TIMING_STAGES,
    GenerationTimingRecorder,
    load_generation_attempt_timing_receipts,
)
from tests.support.authored_generation_attempt_timing_v1 import (
    create_generation_attempt_timing_receipt,
)


def test_attempt_receipt_keeps_non_null_stages_and_operational_causes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    recorder = GenerationTimingRecorder()
    recorder.record_stage_ms("author", 9)
    # ``snapshot_stage_timings_ms`` only ever reports stages actually recorded
    # (it has never zero-filled; see services/authored_generation_timing_receipt.py
    # GenerationTimingRecorder, unchanged since its introduction in 4e7221520).
    # The one production caller (services/authored_presentation_generation.py,
    # ``stage_timings_ms = {stage: 0 for stage in _PERFORMANCE_STAGE_NAMES}``)
    # owns zero-filling itself before merging in recorded values; this test
    # does the same to build a receipt-ready, fully-keyed stage map.
    stages = {stage: 0 for stage in GENERATION_TIMING_STAGES}
    stages.update(recorder.snapshot_stage_timings_ms())

    assert all(stage in stages for stage in GENERATION_TIMING_STAGES)
    assert stages["outline"] == 0
    assert stages["repair"] == 0
    assert stages["checkpoint"] == 0

    job_id = uuid.uuid4()
    receipt = create_generation_attempt_timing_receipt(
        job_id=job_id,
        attempt_id="private-attempt-token",
        attempt_number=3,
        revision=0,
        outcome="failed",
        failure_stage="author",
        stage_timings_ms=stages,
        total_ms=10,
        causes={
            "queueReason": "workspace_retry",
            "stageReason": "public_provider_retry_limit_exceeded",
            "retryReason": "terminal_failure",
        },
    )

    assert dict(receipt.causes) == {
        "queueReason": "workspace_retry",
        "stageReason": "public_provider_retry_limit_exceeded",
        "retryReason": "terminal_failure",
    }
    assert load_generation_attempt_timing_receipts(job_id) == (receipt,)
    assert "private-attempt-token" not in receipt.to_dict()["attemptId"]


def test_provider_attempt_count_rejects_unbounded_checkpoint_value() -> None:
    error = AuthoredStageError(
        slide_index=0,
        stage="author",
        code="authored_slide_author_failed",
        provider_attempt_count=99,
    )
    assert "provider_attempt_count" not in error.as_safe_dict()


def test_active_scope_slide_index_tells_a_deck_stage_from_a_slide_stage():
    """The wiring in author_slide_html asks this to decide the slot shape."""

    from services.provider_usage_instrumentation import (
        active_usage_scope_slide_index,
        provider_usage_scope,
    )

    job_id = uuid.uuid4()
    assert active_usage_scope_slide_index() is None
    with provider_usage_scope(
        job_id=job_id, operation="authored_generation", stage="author", slide_index=4
    ):
        assert active_usage_scope_slide_index() == 4
    with provider_usage_scope(
        job_id=job_id, operation="authored_generation", stage="repair"
    ):
        assert active_usage_scope_slide_index() is None
