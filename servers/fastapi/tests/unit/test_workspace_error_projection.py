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
