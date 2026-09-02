"""The event wire carries the runner's outcome as ``status``.

Runner events record an operation's outcome under ``state``; the public
contract exposes it as ``status``, and that is the only field a client applies
to its operation card. Serializing ``status`` alone shipped every terminal
transition as a bare version bump, so a dead-lettered generation stayed
"in progress" at its last percent, with a cancel button, until a reload.
"""

from __future__ import annotations

import uuid

from models.sql.workspace import WorkspaceOperationEvent
from services.workspace_service import _event_to_wire


def _event(payload: dict, event_type: str = "operation.error") -> WorkspaceOperationEvent:
    return WorkspaceOperationEvent(
        operation_id=uuid.uuid4(),
        sequence=7,
        operation_version=7,
        event_type=event_type,
        payload=payload,
    )


def test_runner_state_reaches_the_wire_as_status() -> None:
    wire = _event_to_wire(
        _event({"state": "dead_letter", "error_code": "http_error_500", "retryable": True})
    )

    assert wire["status"] == "dead_letter"
    assert wire["type"] == "operation.error"
    assert wire["retryable"] is True


def test_every_terminal_runner_state_is_carried() -> None:
    for state in ("failed", "deadline_exceeded", "cancelled", "succeeded"):
        assert _event(
            {"state": state, "public_state": state}
        ) is not None
        assert _event_to_wire(_event({"state": state}))["status"] == state


def test_an_explicit_status_still_wins_over_state() -> None:
    wire = _event_to_wire(_event({"status": "running", "state": "queued"}))

    assert wire["status"] == "running"


def test_no_outcome_stays_absent() -> None:
    assert _event_to_wire(_event({"message": "progress only"}))["status"] is None
    assert _event_to_wire(_event({"state": ""}))["status"] is None
    assert _event_to_wire(_event({"state": 5}))["status"] is None
