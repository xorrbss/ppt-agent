"""Generation failure classes survive live SSE and database exception boundaries."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    OperationalError,
    ProgrammingError,
    SQLAlchemyError,
)

from api.v1.ppt.endpoints import conversations as endpoint
from models.api_error_model import APIErrorModel
from models.sql.workspace import WorkspaceOperation, WorkspaceOperationEvent
from services.conversations import events
from services.generation_failure_class import generation_operation_failure_class
from services.presentation_generation_attempt_timing import _generation_exception_failure
from services.presentation_generation_pipeline import _generic_generation_failure_payload
from services.public_generation_worker_lifecycle import durable_generation_failure


@pytest.mark.parametrize("after", [None, "6"])
@pytest.mark.parametrize("kind, state, code, expected", [
    ("presentation.generate", "failed", "internal_defect", "internal_defect"),
    ("presentation.generate", "dead_letter", "svg_checkpoint_missing", "internal_defect"),
    ("presentation.generate", "failed", "public_provider_overload", "provider_unstable"),
    ("presentation.generate", "failed", None, "unknown"),
    ("presentation.generate", "deadline_exceeded", "public_deadline_exceeded", "limit_reached"),
    ("presentation.generate", "running", "internal_defect", None),
    ("export.pptx", "failed", "internal_defect", None),
    ("presentation.edit", "failed", "internal_defect", None),
])
def test_real_sse_projection_matches_timeline_failure_class(monkeypatch, after, kind, state, code, expected):
    conversation_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    operation = WorkspaceOperation(
        id=uuid.uuid4(), kind=kind, state=state, error_code=code, request_payload={},
    )
    event = WorkspaceOperationEvent(
        operation_id=operation.id, cursor=7, sequence=1, operation_version=2,
        event_type="operation.error", payload={"state": state, "failure_class": "provider_unstable"},
    )
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.execute.return_value = SimpleNamespace(all=lambda: [(event, operation)])
    monkeypatch.setattr(endpoint, "async_session_maker", lambda: session)
    monkeypatch.setattr(events, "_conversation_and_presentation", AsyncMock(return_value=(
        SimpleNamespace(id=conversation_id), SimpleNamespace(id=presentation_id),
    )))
    storage_events = AsyncMock(return_value={
        "events": [{"cursor": "7"}], "event_high_watermark": "7",
    })
    monkeypatch.setattr(events, "workspace_events", storage_events)

    async def first_frame():
        stream = endpoint._conversation_event_stream(
            request=SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
            conversation_id=conversation_id, initial_cursor=after,
        )
        try:
            return await anext(stream)
        finally:
            await stream.aclose()

    frame = asyncio.run(first_frame())
    envelope = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))
    assert envelope["type"] == "run.failed"
    assert envelope["data"]["failure_class"] == expected
    assert expected == generation_operation_failure_class(kind=kind, state=state, error_code=code)
    assert envelope["cursor"] == "7"
    assert storage_events.await_args.kwargs["after"] == after


@pytest.mark.parametrize("error", [
    IntegrityError("private SQL", {}, RuntimeError("private constraint")),
    ProgrammingError("private SQL", {}, RuntimeError("private syntax")),
    DBAPIError("private SQL", {}, RuntimeError("private driver defect")),
    SQLAlchemyError("database_contract_invalid"),
])
@pytest.mark.parametrize("explicit_retryable", [None, True])
def test_database_documentation_codes_cannot_become_domain_contracts(error, explicit_retryable):
    if explicit_retryable is not None:
        error.retryable = explicit_retryable
    failure = _generation_exception_failure(error)
    assert failure["code"] == "internal_defect"
    assert failure["retryable"] is False
    assert durable_generation_failure(error) == ("internal_defect", False)
    safe_exception = HTTPException(500, "Presentation generation failed")
    payload = _generic_generation_failure_payload(error, safe_exception, request_id=None)
    assert payload["code"] == "internal_defect"
    assert payload["retryable"] is False
    assert APIErrorModel.from_exception(safe_exception).code == "internal_defect"
    assert "private" not in json.dumps(payload)


def test_database_documentation_code_does_not_override_transient_type():
    error = OperationalError("private SQL", {}, ConnectionError("private connection"))
    error.retryable = False
    assert durable_generation_failure(error) == ("generation_service_interrupted", True)
