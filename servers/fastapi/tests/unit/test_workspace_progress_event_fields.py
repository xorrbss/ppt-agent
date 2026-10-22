import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from models.workspace import WorkspaceOperationEventResponse
from services.workspace_operation_runner import sanitize_workspace_event_payload
from services.workspace_timeline_projection import _event_to_wire


def _event(payload):
    return SimpleNamespace(
        operation_id=uuid.uuid4(),
        operation_version=2,
        sequence=2,
        cursor=2,
        event_type="progress",
        payload=payload,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_sanitizer_keeps_registered_key_and_valid_params_and_drops_raw_message():
    safe = sanitize_workspace_event_payload(
        {
            "stage": "author",
            "current": 3,
            "total": 8,
            "message": "raw text",
            "message_key": "generation.stage.author.counted",
            "message_params": {"completed": 3, "total": 8, "Bad": 1, "flag": True, "long": "x" * 65, "ok": "s"},
        },
        operation_kind="presentation.generate",
    )
    assert "message" not in safe
    assert safe["stage"] == "author"
    assert safe["message_key"] == "generation.stage.author.counted"
    assert safe["message_params"] == {"completed": 3, "total": 8, "ok": "s"}


def test_sanitizer_drops_unknown_key_and_params_without_key():
    safe = sanitize_workspace_event_payload(
        {"message_key": "not.registered", "message_params": {"a": 1}},
        operation_kind="presentation.generate",
    )
    assert "message_key" not in safe
    assert "message_params" not in safe
    safe = sanitize_workspace_event_payload(
        {"message_key": "generation.stage.outline", "message_params": {"BAD": 1}},
        operation_kind="presentation.generate",
    )
    assert safe == {"message_key": "generation.stage.outline"}


def test_projection_emits_progress_fields_and_validates_in_response_model():
    wire = _event_to_wire(
        _event(
            {
                "state": "running",
                "stage": "author",
                "current": 3,
                "total": 8,
                "message_key": "generation.stage.author.counted",
                "message_params": {"completed": 3, "total": 8},
            }
        )
    )
    assert wire["stage"] == "author"
    assert (wire["current"], wire["total"]) == (3, 8)
    assert wire["message_key"] == "generation.stage.author.counted"
    assert wire["message_params"] == {"completed": 3, "total": 8}
    model = WorkspaceOperationEventResponse.model_validate(wire)
    assert model.stage == "author"
    assert model.message_params == {"completed": 3, "total": 8}


def test_projection_nulls_invalid_values():
    wire = _event_to_wire(
        _event(
            {
                "stage": "Bad Stage!",
                "current": True,
                "total": 0,
                "message_key": "nope",
                "message_params": {"a": 1},
            }
        )
    )
    for field in ("stage", "current", "total", "message_key", "message_params"):
        assert wire[field] is None
    WorkspaceOperationEventResponse.model_validate(wire)
