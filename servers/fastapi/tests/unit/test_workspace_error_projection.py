from __future__ import annotations

import uuid

from services.workspace_api_contract import (
    project_workspace_event_page,
    project_workspace_timeline,
)


def _event(*, error: object, retryable: bool | None = None) -> dict[str, object]:
    operation_id = uuid.uuid4()
    return {
        "id": f"{operation_id}:1",
        "operation_id": str(operation_id),
        "operation_version": 1,
        "sequence": 1,
        "cursor": "1",
        "type": "failed",
        "error": error,
        "retryable": retryable,
    }


def test_event_projection_extracts_registered_mapping_code_and_retryability() -> None:
    projected = project_workspace_event_page(
        {
            "events": [
                _event(
                    error={
                        "code": "revision_conflict",
                        "retryable": False,
                        "private": "must-not-cross-wire",
                    },
                    retryable=True,
                )
            ],
            "event_high_watermark": "1",
        }
    ).model_dump(mode="json")

    assert projected["events"][0]["error"] == "revision_conflict"
    assert projected["events"][0]["safe_message"] == (
        "The presentation changed in another session. Reload and try again."
    )
    assert projected["events"][0]["retryable"] is False
    assert "must-not-cross-wire" not in str(projected)


def test_event_projection_fails_closed_for_unknown_mapping_code() -> None:
    projected = project_workspace_event_page(
        {
            "events": [
                _event(
                    error={
                        "detail": "provider_secret_timeout_987",
                        "retryable": True,
                    }
                )
            ]
        }
    ).model_dump(mode="json")

    assert projected["events"][0]["error"] == "workspace_request_failed"
    assert projected["events"][0]["safe_message"] == (
        "The workspace request could not be completed."
    )
    assert projected["events"][0]["retryable"] is True
    assert "provider_secret_timeout_987" not in str(projected)


def test_event_without_error_clears_untrusted_safe_message() -> None:
    raw_event = _event(error=None)
    raw_event["safe_message"] = "private provider detail"

    projected = project_workspace_event_page(
        {"events": [raw_event]}
    ).model_dump(mode="json")

    event = projected["events"][0]
    assert event["error"] is None
    assert event["safe_message"] is None
    assert "private provider detail" not in str(projected)


def test_operation_without_error_clears_untrusted_message_and_retryability() -> None:
    operation_id = uuid.uuid4()
    projected = project_workspace_timeline(
        {
            "conversation_id": uuid.uuid4(),
            "items": [
                {
                    "anchor_type": "operation",
                    "id": operation_id,
                    "operation_id": operation_id,
                    "position": 1,
                    "operation_type": "slide.edit",
                    "title": "Edit",
                    "status": "succeeded",
                    "error": None,
                    "error_message": "private provider detail",
                    "retryable": True,
                    "version": 1,
                    "events": [],
                }
            ],
            "revision": 1,
        },
        presentation_id=uuid.uuid4(),
    ).model_dump(mode="json")

    operation = projected["items"][0]
    assert operation["error"] is None
    assert operation["error_message"] is None
    assert operation["retryable"] is None
    assert "private provider detail" not in str(projected)


def test_legacy_unknown_operation_projects_as_inert_unsupported_card() -> None:
    operation_id = uuid.uuid4()
    projected = project_workspace_timeline(
        {
            "conversation_id": uuid.uuid4(),
            "items": [
                {
                    "anchor_type": "operation",
                    "id": operation_id,
                    "operation_id": operation_id,
                    "position": 1,
                    "operation_type": "legacy.private.operation",
                    "title": "Private legacy title",
                    "status": "failed",
                    "error": "provider_secret_error",
                    "error_message": "private provider detail",
                    "retryable": True,
                    "version": 1,
                    "events": [],
                }
            ],
            "revision": 1,
        },
        presentation_id=uuid.uuid4(),
    ).model_dump(mode="json")

    operation = projected["items"][0]
    assert operation["target"]["summary"] == "Unsupported operation"
    assert operation["activity_summary"] == "Unsupported workspace operation failed."
    assert operation["allowed_actions"] == []
    assert operation["error"] == "unsupported_operation_type"
    assert operation["error_message"] == "This workspace operation is not supported."
    assert operation["retryable"] is False
    assert "provider_secret" not in str(projected)


def test_generation_operation_projects_durable_retry_and_recovery_aggregate() -> None:
    operation_id = uuid.uuid4()
    projected = project_workspace_timeline(
        {
            "conversation_id": uuid.uuid4(),
            "items": [
                {
                    "anchor_type": "operation",
                    "id": operation_id,
                    "operation_id": operation_id,
                    "position": 1,
                    "operation_type": "presentation.generate",
                    "title": "Generate",
                    "status": "succeeded",
                    "error": None,
                    "retry_count": 1,
                    "result": {
                        "generation_timing_receipt": {
                            "providerAttemptCount": 7,
                        }
                    },
                    "version": 3,
                    "events": [
                        {
                            "id": f"{operation_id}:2",
                            "operation_id": operation_id,
                            "operation_version": 2,
                            "sequence": 2,
                            "type": "operation.error",
                            "status": "queued",
                            "retryCount": 1,
                            "recoveryReason": "operation_recovery_required",
                            "recoveryStatus": "scheduled",
                        }
                    ],
                }
            ],
            "revision": 1,
        },
        presentation_id=uuid.uuid4(),
    ).model_dump(mode="json")

    operation = projected["items"][0]
    assert operation["providerAttemptCount"] == 7
    assert operation["retryCount"] == 1
    assert operation["recoveryReason"] == "operation_recovery_required"
    assert operation["recoveryStatus"] == "recovered"
    assert operation["events"][0]["retryCount"] == 1
    assert operation["events"][0]["recoveryStatus"] == "scheduled"


def test_generation_operation_keeps_unavailable_provider_count_null() -> None:
    operation_id = uuid.uuid4()
    projected = project_workspace_timeline(
        {
            "items": [
                {
                    "anchor_type": "operation",
                    "id": operation_id,
                    "operation_id": operation_id,
                    "position": 1,
                    "operation_type": "presentation.generate",
                    "title": "Generate",
                    "status": "failed",
                    "error": "presentation_generation_failed",
                    "retry_count": 0,
                    "version": 1,
                    "events": [],
                }
            ]
        }
    ).model_dump(mode="json")

    operation = projected["items"][0]
    assert operation["providerAttemptCount"] is None
    assert operation["retryCount"] == 0
    assert operation["recoveryReason"] is None
    assert operation["recoveryStatus"] == "not_required"


def test_retry_count_alone_proves_recovery_required_state() -> None:
    operation_id = uuid.uuid4()
    projected = project_workspace_timeline(
        {
            "items": [
                {
                    "anchor_type": "operation",
                    "id": operation_id,
                    "operation_id": operation_id,
                    "position": 1,
                    "operation_type": "presentation.generate",
                    "title": "Generate",
                    "status": "succeeded",
                    "error": None,
                    "retry_count": 2,
                    "version": 4,
                    "events": [],
                }
            ]
        }
    ).model_dump(mode="json")

    operation = projected["items"][0]
    assert operation["retryCount"] == 2
    assert operation["recoveryReason"] is None
    assert operation["recoveryStatus"] == "recovered"


def test_missing_retry_count_keeps_recovery_status_unavailable() -> None:
    operation_id = uuid.uuid4()
    timeline = project_workspace_timeline(
        {
            "items": [
                {
                    "anchor_type": "operation",
                    "id": operation_id,
                    "operation_id": operation_id,
                    "position": 1,
                    "operation_type": "presentation.generate",
                    "title": "Generate",
                    "status": "succeeded",
                    "error": None,
                    "version": 1,
                    "events": [],
                }
            ]
        }
    ).model_dump(mode="json")
    event = _event(error=None)
    event.update(
        {
            "status": "succeeded",
            "retryCount": None,
            "recoveryStatus": "not_required",
        }
    )
    event_page = project_workspace_event_page(
        {"events": [event]}
    ).model_dump(mode="json")

    assert timeline["items"][0]["retryCount"] is None
    assert timeline["items"][0]["recoveryStatus"] == "unavailable"
    assert event_page["events"][0]["recoveryStatus"] == "unavailable"


def test_event_retry_count_overrides_false_not_required_status() -> None:
    event = _event(error=None)
    event.update(
        {
            "status": "failed",
            "retryCount": 1,
            "recoveryStatus": "not_required",
        }
    )

    projected = project_workspace_event_page(
        {"events": [event]}
    ).model_dump(mode="json")

    assert projected["events"][0]["retryCount"] == 1
    assert projected["events"][0]["recoveryStatus"] == "failed"
