import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services import workspace_legacy_write_gate


def test_direct_presentation_write_always_requires_a_workspace_command():
    with pytest.raises(HTTPException) as caught:
        workspace_legacy_write_gate.require_legacy_presentation_write_path()

    assert caught.value.status_code == 409
    assert caught.value.detail == "chat_first_command_required"


def test_unusable_sql_runtime_fails_closed_instead_of_reopening_direct_writes(
    monkeypatch,
):
    monkeypatch.setattr(
        workspace_legacy_write_gate,
        "sql_engine",
        SimpleNamespace(url="mysql+aiomysql://db/app"),
    )

    with pytest.raises(HTTPException) as caught:
        workspace_legacy_write_gate.require_legacy_presentation_write_path()

    assert caught.value.status_code == 501
    assert caught.value.detail == "chat_first_runtime_unsupported"


class _OutlineBootstrapSession:
    def __init__(self, operation_id):
        self.operation_id = operation_id

    async def scalar(self, _statement):
        return self.operation_id


def test_outline_bootstrap_write_is_allowed_for_staged_pending_generation():
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
    lifecycle_status,
    operation_id,
):
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
