from __future__ import annotations

import asyncio
from types import SimpleNamespace
import uuid

import pytest

from models.presentation_and_path import PresentationPathAndEditPath
from services import presentation_generation_export_chain as chain
from services.workspace_service import WorkspaceServiceError


class _Session:
    """Minimal stand-in for the generation's AsyncSession."""

    def __init__(self, presentation) -> None:
        self._presentation = presentation
        self.commits = 0
        self.rollbacks = 0
        self.added: list[object] = []

    async def get(self, _model, _identity):
        return self._presentation

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def add(self, instance) -> None:
        self.added.append(instance)


def _response(presentation_id: uuid.UUID) -> PresentationPathAndEditPath:
    return PresentationPathAndEditPath(
        presentation_id=presentation_id,
        path="/app_data/exports/deck.pptx",
        edit_path=f"/presentation?id={presentation_id}",
    )


def test_idempotency_key_is_stable_per_revision_and_format() -> None:
    presentation_id = uuid.uuid4()
    first = chain.chained_export_idempotency_key(
        presentation_id=presentation_id, revision=4, command_type="export.pptx"
    )
    same = chain.chained_export_idempotency_key(
        presentation_id=presentation_id, revision=4, command_type="export.pptx"
    )
    later_revision = chain.chained_export_idempotency_key(
        presentation_id=presentation_id, revision=5, command_type="export.pptx"
    )
    other_format = chain.chained_export_idempotency_key(
        presentation_id=presentation_id, revision=4, command_type="export.pdf"
    )

    assert first == same
    assert first != later_revision
    assert first != other_format
    assert len(first) <= 255


@pytest.mark.parametrize(
    ("export_as", "command_type"),
    [("pptx", "export.pptx"), ("pdf", "export.pdf")],
)
def test_completed_generation_submits_one_export_command(
    monkeypatch: pytest.MonkeyPatch,
    export_as: str,
    command_type: str,
) -> None:
    presentation_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    session = _Session(SimpleNamespace(id=presentation_id, revision=7))
    submitted: list[tuple[uuid.UUID, object]] = []
    woken: list[bool] = []

    async def create(_session, *, presentation_id, request):
        submitted.append((presentation_id, request))
        return SimpleNamespace(id=operation_id), False

    monkeypatch.setattr(chain, "create_workspace_command", create)
    monkeypatch.setattr(
        chain, "wake_workspace_operation_runner", lambda: woken.append(True)
    )

    enriched = asyncio.run(
        chain.finalize_generation_export_chain(
            session,
            response=_response(presentation_id),
            export_as=export_as,
        )
    )

    assert len(submitted) == 1
    target, request = submitted[0]
    assert target == presentation_id
    assert request.command_type == command_type
    assert request.expected_revision == 7
    # The chain must pin no export option, so the server default governs.
    assert request.context == {}
    assert enriched.export_operation_id == operation_id
    assert enriched.export_error is None
    assert session.commits == 1
    assert woken == [True]


def test_duplicate_submission_adopts_the_existing_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    presentation_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    session = _Session(SimpleNamespace(id=presentation_id, revision=2))

    async def create(_session, *, presentation_id, request):
        return SimpleNamespace(id=operation_id), True

    monkeypatch.setattr(chain, "create_workspace_command", create)
    monkeypatch.setattr(chain, "wake_workspace_operation_runner", lambda: None)

    outcome = asyncio.run(
        chain.chain_generation_export(
            session, presentation_id=presentation_id, export_as="pptx"
        )
    )

    assert outcome.duplicate is True
    assert outcome.operation_id == operation_id
    assert outcome.error_code is None


def test_refused_submission_keeps_the_published_deck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    presentation_id = uuid.uuid4()
    session = _Session(SimpleNamespace(id=presentation_id, revision=1))

    async def create(_session, *, presentation_id, request):
        raise WorkspaceServiceError(
            "public_admission_export_quota", status_code=429, retryable=True
        )

    monkeypatch.setattr(chain, "create_workspace_command", create)
    monkeypatch.setattr(chain, "wake_workspace_operation_runner", lambda: None)

    enriched = asyncio.run(
        chain.finalize_generation_export_chain(
            session, response=_response(presentation_id), export_as="pptx"
        )
    )

    assert enriched.presentation_id == presentation_id
    assert enriched.export_operation_id is None
    assert enriched.export_error == "public_admission_export_quota"
    assert session.rollbacks == 1


def test_async_task_projection_carries_the_export_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    presentation_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    session = _Session(SimpleNamespace(id=presentation_id, revision=3))
    async_status = SimpleNamespace(
        data={"presentation_id": str(presentation_id), "path": "/app_data/x.pptx"},
        updated_at=None,
    )

    async def create(_session, *, presentation_id, request):
        return SimpleNamespace(id=operation_id), False

    monkeypatch.setattr(chain, "create_workspace_command", create)
    monkeypatch.setattr(chain, "wake_workspace_operation_runner", lambda: None)

    asyncio.run(
        chain.finalize_generation_export_chain(
            session,
            response=_response(presentation_id),
            export_as="pptx",
            async_status=async_status,
        )
    )

    assert async_status.data["export_operation_id"] == str(operation_id)
    assert async_status.data["export_error"] is None
    assert async_status.data["path"] == "/app_data/x.pptx"
    assert async_status in session.added
