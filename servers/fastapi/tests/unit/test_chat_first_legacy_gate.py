import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1.ppt.endpoints.chat import _require_legacy_chat_write_path
from services import workspace_legacy_write_gate


def test_legacy_chat_mutation_is_blocked_when_workspace_runner_is_enabled(
    monkeypatch,
):
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE", "sqlite-local")

    with pytest.raises(HTTPException) as caught:
        _require_legacy_chat_write_path()

    assert caught.value.status_code == 409
    assert caught.value.detail == "chat_first_command_required"


def test_legacy_chat_mutation_remains_available_for_rollback(monkeypatch):
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "false")

    assert _require_legacy_chat_write_path() is None


def test_rollback_ignores_stale_profile_and_unsupported_legacy_database(
    monkeypatch,
):
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "false")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE", "typo")
    monkeypatch.setattr(
        workspace_legacy_write_gate,
        "sql_engine",
        SimpleNamespace(url="mysql+aiomysql://db/app"),
    )

    assert (
        workspace_legacy_write_gate.require_legacy_presentation_write_path() is None
    )


class _OutlineBootstrapSession:
    def __init__(self, operation_id):
        self.operation_id = operation_id

    async def scalar(self, _statement):
        return self.operation_id


def test_outline_bootstrap_write_is_allowed_for_staged_pending_generation(
    monkeypatch,
):
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE", "sqlite-local")
    presentation = SimpleNamespace(
        id=uuid.uuid4(),
        lifecycle_status="staging",
        revision=0,
    )

    assert (
        asyncio.run(
            workspace_legacy_write_gate.require_outline_bootstrap_write_path(
                _OutlineBootstrapSession(uuid.uuid4()),
                presentation,
            )
        )
        is None
    )


@pytest.mark.parametrize(
    ("lifecycle_status", "operation_id"),
    (("published", uuid.uuid4()), ("staging", None)),
)
def test_outline_bootstrap_write_remains_blocked_outside_pending_staging(
    monkeypatch,
    lifecycle_status,
    operation_id,
):
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE", "sqlite-local")
    presentation = SimpleNamespace(
        id=uuid.uuid4(),
        lifecycle_status=lifecycle_status,
        revision=0,
    )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            workspace_legacy_write_gate.require_outline_bootstrap_write_path(
                _OutlineBootstrapSession(operation_id),
                presentation,
            )
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == "chat_first_command_required"
