import math
from unittest.mock import Mock

import pytest

from services.authored_generation_observability import (
    build_authored_generation_observation,
    log_authored_generation_observation,
    log_authored_slide_failure_observation,
)


def test_slide_failure_observation_keeps_only_safe_diagnostics():
    event = build_authored_generation_observation(
        name="slide_failure",
        stage="render",
        code="authored_slide_render_failed",
        slide_number=37,
        retryable=True,
    )

    assert event == {
        "schema_version": 1,
        "pipeline": "authored",
        "name": "slide_failure",
        "stage": "render",
        "code": "authored_slide_render_failed",
        "slide_number": 37,
        "retryable": True,
    }
    assert "job" not in str(event)
    assert "exception" not in str(event)


def test_resume_and_quota_wait_observations_expose_bounded_counts():
    resume = build_authored_generation_observation(
        name="resume",
        attempt_number=4,
        completed_slides=36,
        total_slides=90,
    )
    quota = build_authored_generation_observation(
        name="quota_wait",
        duration_ms=1250.5,
        polls=2,
    )

    assert resume["resume_count"] == 3
    assert resume["completed_slides"] == 36
    assert quota == {
        "schema_version": 1,
        "pipeline": "authored",
        "name": "quota_wait",
        "duration_ms": 1250.5,
        "polls": 2,
    }


def test_sweep_and_stalled_recovery_observations_are_aggregation_safe():
    sweep = build_authored_generation_observation(
        name="artifact_sweep",
        outcome="success",
        scanned_terminal=5,
        deleted_terminal=2,
        scanned_orphan=3,
        deleted_orphan=1,
        deleted_bytes=4096,
        deleted_files=3,
        skipped_budget=1,
        errors=1,
    )
    stalled = build_authored_generation_observation(
        name="stalled_recovery",
        prior_state="running",
        recovered_jobs=2,
    )

    assert sweep["deleted_bytes"] == 4096
    assert sweep["skipped_budget"] == 1
    assert stalled["recovered_jobs"] == 2
    assert stalled["prior_state"] == "running"


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "name": "slide_failure",
            "stage": "render",
            "code": "customer-secret-token",
            "slide_number": 1,
            "retryable": True,
        },
        {
            "name": "slide_failure",
            "stage": "private-stage",
            "code": "authored_slide_render_failed",
            "slide_number": 1,
            "retryable": True,
        },
        {
            "name": "quota_wait",
            "duration_ms": math.inf,
            "polls": 1,
        },
        {
            "name": "resume",
            "attempt_number": 2,
            "completed_slides": 91,
            "total_slides": 90,
        },
        {
            "name": "stalled_recovery",
            "prior_state": "tenant@example.com",
            "recovered_jobs": 1,
        },
    ],
)
def test_observation_rejects_unbounded_or_sensitive_dimensions(kwargs):
    with pytest.raises(ValueError):
        build_authored_generation_observation(**kwargs)


def test_observation_has_no_arbitrary_metadata_entry_point():
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        build_authored_generation_observation(
            name="quota_wait",
            duration_ms=10,
            polls=1,
            prompt="private customer prompt",
        )


def test_logger_emits_validated_compact_json():
    logger = Mock()

    event = log_authored_generation_observation(
        name="artifact_sweep",
        outcome="failure",
        logger=logger,
    )

    assert event["outcome"] == "failure"
    logger.info.assert_called_once()
    assert logger.info.call_args.args[0] == "authored_generation %s"
    assert logger.info.call_args.args[1] == (
        '{"name":"artifact_sweep","outcome":"failure",'
        '"pipeline":"authored","schema_version":1}'
    )


def test_slide_failure_logger_collapses_unknown_code_without_leaking_it():
    logger = Mock()

    event = log_authored_slide_failure_observation(
        logger=logger,
        stage="render",
        code="provider-secret-customer-123",
        slide_number=8,
        retryable=True,
    )

    assert event["code"] == "authored_unexpected_failure"
    assert "provider-secret-customer-123" not in logger.info.call_args.args[1]
