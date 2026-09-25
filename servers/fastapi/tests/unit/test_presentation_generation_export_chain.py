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

    def __init__(self, presentation, *, fail_commit_on: frozenset[int] = frozenset()) -> None:
        self._presentation = presentation
        self._fail_commit_on = fail_commit_on
        self.commits = 0
        self.rollbacks = 0
        self.added: list[object] = []

    async def get(self, _model, _identity):
        return self._presentation

    async def commit(self) -> None:
        self.commits += 1
        if self.commits in self._fail_commit_on:
            raise RuntimeError("simulated commit failure")

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


def test_chained_export_failure_does_not_revert_an_already_completed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a submission failure used to roll back the whole session,
    discarding "completed" if the caller had set it earlier on the same,
    still-uncommitted session. finalize_generation_export_chain itself never
    writes .status -- callers must commit "completed" before invoking the
    chain (api/v1/ppt/endpoints/presentation_generate.py does this now). This
    locks the contract: the chain only ever annotates .data, never .status.
    """
    presentation_id = uuid.uuid4()
    session = _Session(SimpleNamespace(id=presentation_id, revision=1))
    async_status = SimpleNamespace(status="completed", data={}, updated_at=None)

    async def create(_session, *, presentation_id, request):
        raise WorkspaceServiceError("workspace_mutation_admission_conflict", status_code=409, retryable=True)

    monkeypatch.setattr(chain, "create_workspace_command", create)
    monkeypatch.setattr(chain, "wake_workspace_operation_runner", lambda: None)

    enriched = asyncio.run(
        chain.finalize_generation_export_chain(
            session, response=_response(presentation_id), export_as="pptx", async_status=async_status,
        )
    )

    assert async_status.status == "completed"
    assert async_status.data["export_error"] == "workspace_mutation_admission_conflict"
    assert async_status.data["export_operation_id"] is None
    assert enriched.export_error == "workspace_mutation_admission_conflict"
    assert enriched.export_operation_id is None


def test_failing_to_record_the_export_outcome_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The export-outcome annotation commit must never turn a successful,
    already-published generation into a failed request (same philosophy as
    a refused submission itself)."""
    presentation_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    # Commit #1 is the successful chain submission inside _submit_chained_export;
    # commit #2 is finalize_generation_export_chain's own data-annotation commit.
    session = _Session(SimpleNamespace(id=presentation_id, revision=1), fail_commit_on=frozenset({2}))
    async_status = SimpleNamespace(status="completed", data={}, updated_at=None)

    async def create(_session, *, presentation_id, request):
        return SimpleNamespace(id=operation_id), False

    monkeypatch.setattr(chain, "create_workspace_command", create)
    monkeypatch.setattr(chain, "wake_workspace_operation_runner", lambda: None)

    enriched = asyncio.run(
        chain.finalize_generation_export_chain(
            session, response=_response(presentation_id), export_as="pptx", async_status=async_status,
        )
    )

    # No exception propagated; the caller-set "completed" status is untouched;
    # the enriched response still carries the operation id even though the
    # durable annotation of it failed.
    assert async_status.status == "completed"
    assert enriched.export_operation_id == operation_id
    assert session.rollbacks == 1
