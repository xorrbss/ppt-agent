import json
import math
from unittest.mock import Mock
import uuid

import pytest

from services.workspace_operation_observability import (
    build_workspace_operation_observation,
    log_workspace_operation_observation,
)
from services.workspace_metrics import WORKSPACE_METRICS


def test_workspace_operation_observation_emits_closed_metadata_schema():
    operation_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    presentation_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    conversation_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    logger = Mock()

    event = log_workspace_operation_observation(
        name="succeeded",
        operation_id=operation_id,
        presentation_id=presentation_id,
        conversation_id=conversation_id,
        command_type="presentation.generate",
        result_status="succeeded",
        duration_ms=123.6,
        event_lag_ms=4,
        attempt_number=2,
        retry_count=1,
        recovery_count=0,
        expected_revision=7,
        result_revision=8,
        fencing_token=2,
        revision_conflict=False,
        fence_outcome="publish_committed",
        logger=logger,
    )

    assert event == {
        "schema_version": 1,
        "pipeline": "workspace_operation",
        "name": "succeeded",
        "operation_id": str(operation_id),
        "presentation_id": str(presentation_id),
        "conversation_id": str(conversation_id),
        "command_type": "presentation.generate",
        "result_status": "succeeded",
        "fence_outcome": "publish_committed",
        "revision_conflict": False,
        "duration_ms": 124,
        "event_lag_ms": 4,
        "attempt_number": 2,
        "retry_count": 1,
        "recovery_count": 0,
        "expected_revision": 7,
        "result_revision": 8,
        "fencing_token": 2,
    }
    logger.info.assert_called_once_with(
        "workspace_operation %s",
        json.dumps(event, sort_keys=True, separators=(",", ":")),
    )


def test_unrecognized_dimensions_collapse_instead_of_leaking_input():
    secret = "prompt-is-secret /srv/private/file.pptx bearer-token"

    event = build_workspace_operation_observation(
        name="failed",
        command_type=secret,
        result_status="failed",
        error_code=secret,
        fence_outcome="transition_committed",
    )

    assert event["command_type"] == "other"
    assert event["error_code"] == "other"
    assert secret not in json.dumps(event)


@pytest.mark.parametrize(
    "command_type",
    [
        "presentation.generate",
        "presentation.edit",
        "slide.edit",
        "slide.regenerate",
        "slide.delete",
        "version.restore",
        "quality.review",
        "export.pptx",
        "export.pdf",
    ],
)
def test_supported_command_types_remain_diagnostic(command_type):
    event = build_workspace_operation_observation(
        name="accepted",
        command_type=command_type,
        result_status="queued",
        fence_outcome="not_applicable",
    )

    assert event["command_type"] == command_type


@pytest.mark.parametrize(
    "error_code",
    [
        "stale_presentation_revision",
        "presentation_edit_failed",
        "presentation_generation_failed",
        "authored_cluster_capacity_unavailable",
        "operation_lease_lost",
        "workspace_expected_revision_required",
        "presentation_generation_job_missing",
        "presentation_generation_job_not_linked",
        "version_not_found",
        "version_restore_failed",
        "version_restore_target_invalid",
        "version_snapshot_invalid",
    ],
)
def test_stable_workspace_error_codes_remain_diagnostic(error_code):
    event = build_workspace_operation_observation(
        name="failed",
        command_type="presentation.generate",
        result_status="failed",
        error_code=error_code,
        fence_outcome="transition_committed",
    )

    assert event["error_code"] == error_code


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "prompt",
        "message_content",
        "file_path",
        "attachment_file_name",
        "attempt_token",
        "secret",
        "raw_error",
        "owner_id",
        "owner_scope",
    ],
)
def test_content_secret_and_owner_fields_are_not_accepted(forbidden_field):
    with pytest.raises(TypeError):
        build_workspace_operation_observation(
            name="running",
            **{forbidden_field: "must-not-be-accepted"},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duration_ms", math.nan),
        ("event_lag_ms", math.inf),
        ("retry_count", -1),
        ("expected_revision", True),
        ("fencing_token", 1_000_000_001),
    ],
)
def test_unbounded_or_non_finite_metrics_are_rejected(field, value):
    with pytest.raises(ValueError):
        build_workspace_operation_observation(
            name="running",
            **{field: value},
        )


def test_logging_failure_never_changes_operation_control_flow():
    logger = Mock()
    logger.info.side_effect = RuntimeError("collector contains sensitive details")
    WORKSPACE_METRICS.reset()

    event = log_workspace_operation_observation(
        name="runner_fault",
        result_status="running",
        error_code="workspace_operation_internal_error",
        fence_outcome="not_applicable",
        logger=logger,
    )

    assert event is not None
    assert WORKSPACE_METRICS.snapshot()["signal_failures"] == {
        "operation_observation": 1
    }
    assert WORKSPACE_METRICS.snapshot()["operations"]
    WORKSPACE_METRICS.reset()
