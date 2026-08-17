from __future__ import annotations

import asyncio
import uuid

import pytest

from models.workspace import WorkspaceBootstrapRequest, WorkspaceCommandRequest
from models.workspace_operation_kinds import (
    WORKSPACE_BOOTSTRAP_KINDS,
    WORKSPACE_COMMAND_KINDS,
    WORKSPACE_DESTRUCTIVE_OPERATION_KINDS,
    WORKSPACE_OPERATION_KIND_REGISTRY,
    WORKSPACE_OPERATION_KINDS,
)
from services.workspace_service import (
    WorkspaceServiceError,
    bootstrap_workspace,
    create_workspace_command,
)


class _PersistenceForbidden:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"unsupported operation touched persistence: {name}")


def test_workspace_operation_kind_catalog_is_closed_and_partitioned() -> None:
    assert set(WORKSPACE_OPERATION_KIND_REGISTRY) == set(WORKSPACE_OPERATION_KINDS)
    assert set(WORKSPACE_BOOTSTRAP_KINDS) == set(WORKSPACE_OPERATION_KINDS)
    assert set(WORKSPACE_COMMAND_KINDS) == {
        "presentation.edit",
        "presentation.delete",
        "slide.edit",
        "slide.regenerate",
        "slide.delete",
        "version.restore",
        "template.apply",
        "design.apply",
        "quality.review",
        "export.pptx",
        "export.pdf",
    }
    assert WORKSPACE_DESTRUCTIVE_OPERATION_KINDS == {
        "presentation.delete",
        "slide.delete",
        "version.restore",
    }
    assert set(WORKSPACE_COMMAND_KINDS) < set(WORKSPACE_BOOTSTRAP_KINDS)


@pytest.mark.parametrize("commit", [True, False])
def test_unknown_bootstrap_kind_is_rejected_before_persistence(commit: bool) -> None:
    request = WorkspaceBootstrapRequest.model_construct(
        idempotency_key="unknown-bootstrap",
        prompt="Create a presentation",
        generation_settings={},
        asset_ids=[],
        attachments=[],
        operation_type="unknown.operation",
        expected_revision=None,
    )

    with pytest.raises(WorkspaceServiceError) as captured:
        asyncio.run(
            bootstrap_workspace(
                _PersistenceForbidden(),
                presentation_id=uuid.uuid4(),
                request=request,
                commit=commit,
            )
        )

    assert captured.value.code == "unsupported_operation_type"
    assert captured.value.status_code == 422
    assert captured.value.retryable is False


def test_unknown_command_kind_is_rejected_before_persistence() -> None:
    request = WorkspaceCommandRequest.model_construct(
        command="Do something unknown",
        command_type="unknown.operation",
        idempotency_key="unknown-command",
        expected_revision=None,
        asset_ids=[],
        context={},
    )

    with pytest.raises(WorkspaceServiceError) as captured:
        asyncio.run(
            create_workspace_command(
                _PersistenceForbidden(),
                presentation_id=uuid.uuid4(),
                request=request,
            )
        )

    assert captured.value.code == "unsupported_operation_type"
    assert captured.value.status_code == 422
    assert captured.value.retryable is False
