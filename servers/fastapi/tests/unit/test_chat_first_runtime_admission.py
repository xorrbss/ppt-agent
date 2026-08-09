from __future__ import annotations

import asyncio
from types import SimpleNamespace
import uuid

from fastapi import BackgroundTasks, HTTPException
import pytest

from api.v1.ppt.endpoints import presentation_crud, presentation_generate
from api.v1.ppt.endpoints import workspace as workspace_endpoint
from enums.tone import Tone
from enums.verbosity import Verbosity
from models.generate_presentation_request import GeneratePresentationRequest
from models.workspace import WorkspaceBootstrapRequest


class _NoMutationSession:
    """Any session access means runtime admission happened too late."""

    def __getattr__(self, name):
        raise AssertionError(f"database access before runtime admission: {name}")


# The operation store only runs on sqlite or postgresql; any other dialect is
# admitted nowhere, and there is no legacy path left to fall back to.
_INVALID_RUNTIME_CASES = (
    ("mysql+aiomysql://db/app", "chat_first_runtime_unsupported", 501),
    ("oracle://db/app", "chat_first_runtime_unsupported", 501),
)


def _configure_invalid_runtime(monkeypatch, module, database_url):
    monkeypatch.setattr(module, "sql_engine", SimpleNamespace(url=database_url))


@pytest.mark.parametrize(
    ("database_url", "reason", "status_code"),
    _INVALID_RUNTIME_CASES,
)
def test_create_fails_before_database_or_file_admission(
    monkeypatch,
    database_url,
    reason,
    status_code,
):
    _configure_invalid_runtime(monkeypatch, presentation_crud, database_url)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            presentation_crud.create_presentation(
                content="runtime admission",
                presentation_id=uuid.uuid4(),
                n_slides=None,
                language=None,
                file_paths=None,
                tone=Tone.DEFAULT,
                verbosity=Verbosity.STANDARD,
                instructions=None,
                include_table_of_contents=False,
                include_title_slide=True,
                web_search=False,
                theme=None,
                idempotency_key="runtime-admission",
                sql_session=_NoMutationSession(),
            )
        )

    assert caught.value.status_code == status_code
    assert caught.value.detail == reason


@pytest.mark.parametrize(
    ("database_url", "reason", "status_code"),
    _INVALID_RUNTIME_CASES,
)
def test_generation_queue_fails_before_validation_or_job_mutation(
    monkeypatch,
    database_url,
    reason,
    status_code,
):
    _configure_invalid_runtime(monkeypatch, presentation_generate, database_url)
    validation_calls: list[object] = []
    monkeypatch.setattr(
        presentation_generate,
        "_validate_authored_exact_admission",
        lambda request: validation_calls.append(request),
    )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            presentation_generate.queue_presentation_generation(
                GeneratePresentationRequest(
                    presentation_id=uuid.uuid4(),
                    content="runtime admission",
                ),
                BackgroundTasks(),
                _NoMutationSession(),
                idempotency_key="runtime-admission",
            )
        )

    assert caught.value.status_code == status_code
    assert caught.value.detail == reason
    assert validation_calls == []


@pytest.mark.parametrize(
    ("database_url", "reason", "status_code"),
    _INVALID_RUNTIME_CASES,
)
def test_workspace_bootstrap_fails_before_service_mutation(
    monkeypatch,
    database_url,
    reason,
    status_code,
):
    _configure_invalid_runtime(monkeypatch, workspace_endpoint, database_url)
    service_calls: list[object] = []

    async def _bootstrap(*args, **kwargs):
        service_calls.append((args, kwargs))
        raise AssertionError("bootstrap service reached before runtime admission")

    monkeypatch.setattr(workspace_endpoint, "bootstrap_workspace", _bootstrap)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            workspace_endpoint.bootstrap_presentation_workspace(
                presentation_id=uuid.uuid4(),
                request=WorkspaceBootstrapRequest(
                    idempotency_key="runtime-admission",
                    prompt="runtime admission",
                ),
                session=_NoMutationSession(),
            )
        )

    assert caught.value.status_code == status_code
    assert caught.value.detail == reason
    assert service_calls == []


def test_supported_runtime_admits_create_and_generation(monkeypatch):
    sqlite = SimpleNamespace(url="sqlite+aiosqlite:///local.db")
    monkeypatch.setattr(presentation_crud, "sql_engine", sqlite)
    monkeypatch.setattr(presentation_generate, "sql_engine", sqlite)

    assert presentation_crud._chat_first_create_enabled() is True
    assert presentation_generate._chat_first_generation_enabled() is True
