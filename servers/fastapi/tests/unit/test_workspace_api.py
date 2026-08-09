from __future__ import annotations

import asyncio
from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI, HTTPException

from api.v1.ppt.endpoints import workspace as workspace_endpoint
from models.workspace import WorkspaceBootstrapRequest, WorkspaceCommandRequest
from services.workspace_runtime_profile import (
    WorkspaceRuntimeKind,
    WorkspaceRuntimeProfile,
)
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactPublicationError,
)
from services.workspace_service import WorkspaceServiceError


def _profile(*, supported: bool) -> WorkspaceRuntimeProfile:
    return WorkspaceRuntimeProfile(
        dialect="sqlite" if supported else "mysql",
        kind=(
            WorkspaceRuntimeKind.SQLITE_LOCAL
            if supported
            else WorkspaceRuntimeKind.UNSUPPORTED
        ),
        supported=supported,
    )


@pytest.mark.parametrize("supported", [True, False])
def test_capabilities_track_the_sql_runtime_alone(
    monkeypatch: pytest.MonkeyPatch,
    supported: bool,
):
    monkeypatch.setattr(
        workspace_endpoint,
        "_runtime_profile",
        lambda: _profile(supported=supported),
    )

    capabilities = asyncio.run(workspace_endpoint.workspace_capabilities())

    # Reads and writes stand or fall together: the runner is the only writer,
    # so there is no state where the timeline is readable but commands are not.
    assert capabilities.supported is supported
    assert capabilities.enabled is supported
    assert capabilities.timeline is supported
    assert capabilities.events is supported
    assert capabilities.commands is supported
    assert capabilities.kill_switch_active is False
    assert capabilities.shadow_mode is False
    assert capabilities.cohort_bucket is None

    if supported:
        assert capabilities.reason is None
        workspace_endpoint._require_supported_runtime()
        workspace_endpoint._require_workspace_writes()
        return

    assert capabilities.runtime_profile == "unsupported"
    assert capabilities.reason == "chat_first_runtime_unsupported"
    for guard in (
        workspace_endpoint._require_supported_runtime,
        workspace_endpoint._require_workspace_writes,
    ):
        with pytest.raises(HTTPException) as unsupported:
            guard()
        assert unsupported.value.status_code == 501
        assert unsupported.value.detail == "chat_first_runtime_unsupported"


def test_command_asset_ids_are_a_bounded_unique_uuid_openapi_contract():
    app = FastAPI()
    app.include_router(workspace_endpoint.WORKSPACE_ROUTER)

    openapi = app.openapi()
    request_schema = openapi["components"]["schemas"]["WorkspaceCommandRequest"]
    asset_ids = request_schema["properties"]["asset_ids"]

    assert asset_ids["type"] == "array"
    assert asset_ids["maxItems"] == 100
    assert asset_ids["uniqueItems"] is True
    assert asset_ids["items"] == {"type": "string", "format": "uuid"}
    command_operation = openapi["paths"][
        "/workspace/presentations/{presentation_id}/commands"
    ]["post"]
    assert command_operation["requestBody"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/WorkspaceCommandRequest"}

    assert WorkspaceCommandRequest(
        command="Tighten the opening slide",
        idempotency_key="frontend-empty-assets",
        asset_ids=[],
    ).asset_ids == []
    duplicate_id = uuid.uuid4()
    with pytest.raises(ValueError, match="workspace asset references must be unique"):
        WorkspaceCommandRequest(
            command="Tighten the opening slide",
            idempotency_key="duplicate-assets",
            asset_ids=[duplicate_id, duplicate_id],
        )
    with pytest.raises(ValueError):
        WorkspaceCommandRequest(
            command="Tighten the opening slide",
            idempotency_key="too-many-assets",
            asset_ids=[uuid.uuid4() for _ in range(101)],
        )


def test_event_route_rejects_conflicting_query_and_last_event_id(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(workspace_endpoint, "_require_supported_runtime", lambda: None)

    with pytest.raises(HTTPException) as conflict:
        asyncio.run(
            workspace_endpoint.get_presentation_workspace_events(
                request=SimpleNamespace(),
                presentation_id=uuid.uuid4(),
                after="8",
                last_event_id="7",
                limit=200,
                stream=False,
                session=None,
            )
        )

    assert conflict.value.status_code == 400
    assert conflict.value.detail == "invalid_event_cursor"
    assert conflict.value.retryable is False


@pytest.mark.parametrize(
    ("kind", "suffix", "media_type"),
    [
        (
            "export.pptx",
            "pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        ("export.pdf", "pdf", "application/pdf"),
    ],
)
def test_export_download_streams_per_request_without_exposing_storage_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    kind: str,
    suffix: str,
    media_type: str,
):
    artifact_id = uuid.uuid4()
    calls = []
    private_file = tmp_path / f"{artifact_id}.{suffix}"
    private_file.write_bytes(b"pptx")

    async def resolve(_session, *, artifact_id):
        calls.append(artifact_id)
        return SimpleNamespace(
            absolute_path=private_file,
            kind=kind,
            presentation_id=uuid.uuid4(),
            source_revision=4,
        )

    monkeypatch.setattr(workspace_endpoint, "_require_supported_runtime", lambda: None)
    monkeypatch.setattr(
        workspace_endpoint,
        "resolve_workspace_export_artifact",
        resolve,
    )

    response = asyncio.run(
        workspace_endpoint.download_workspace_export_artifact(
            artifact_id=artifact_id,
            session=None,
        )
    )

    assert calls == [artifact_id]
    assert response.status_code == 200
    assert "location" not in response.headers
    assert response.media_type == media_type
    assert response.headers["content-disposition"].endswith(f'.{suffix}"')
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_export_download_maps_expiry_to_gone(monkeypatch: pytest.MonkeyPatch):
    async def expired(_session, *, artifact_id):
        raise WorkspaceArtifactPublicationError("workspace_export_expired")

    monkeypatch.setattr(workspace_endpoint, "_require_supported_runtime", lambda: None)
    monkeypatch.setattr(
        workspace_endpoint,
        "resolve_workspace_export_artifact",
        expired,
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            workspace_endpoint.download_workspace_export_artifact(
                artifact_id=uuid.uuid4(),
                session=None,
            )
        )
    assert error.value.status_code == 410
    assert error.value.detail == "workspace_export_expired"


def test_workspace_admission_and_revision_conflict_metrics_are_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
):
    class MetricsProbe:
        def __init__(self):
            self.admissions: list[str] = []
            self.revision_conflicts = 0

        def record_operation_admission(self, operation_type: str) -> None:
            self.admissions.append(operation_type)

        def record_revision_conflict(self) -> None:
            self.revision_conflicts += 1

    metrics = MetricsProbe()
    monkeypatch.setattr(workspace_endpoint, "WORKSPACE_METRICS", metrics)

    workspace_endpoint._record_operation_admission(
        "presentation.edit",
        duplicate=False,
    )
    workspace_endpoint._record_operation_admission(
        "presentation.edit",
        duplicate=True,
    )
    with pytest.raises(HTTPException):
        workspace_endpoint._raise_service_error(
            WorkspaceServiceError(
                "revision_conflict",
                status_code=409,
                retryable=True,
            )
        )
    with pytest.raises(HTTPException):
        workspace_endpoint._raise_service_error(
            WorkspaceServiceError(
                "stale_presentation_revision",
                status_code=409,
                retryable=True,
            )
        )
    with pytest.raises(HTTPException):
        workspace_endpoint._raise_service_error(
            WorkspaceServiceError("operation_state_conflict", status_code=409)
        )

    assert metrics.admissions == ["presentation.edit"]
    assert metrics.revision_conflicts == 2


def test_unknown_command_endpoint_returns_typed_safe_422_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(workspace_endpoint, "_require_workspace_writes", lambda _id: None)
    request = WorkspaceCommandRequest(
        command="Run an unsupported operation",
        command_type="unknown.operation",
        idempotency_key="unknown-endpoint",
    )

    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            workspace_endpoint.post_presentation_workspace_command(
                presentation_id=uuid.uuid4(),
                request=request,
                session=None,
            )
        )

    assert captured.value.status_code == 422
    assert captured.value.detail == "unsupported_operation_type"


def test_event_stream_releases_the_request_session_before_streaming(
    monkeypatch: pytest.MonkeyPatch,
):
    """The pre-flight read must not pin a snapshot for the life of the stream.

    A request-scoped session is torn down only once the response ends, and this
    response ends only when the client disconnects.  On SQLite the read
    transaction opened by cursor validation would hold a WAL snapshot for as
    long as the tab stays open, so every checkpoint could copy frames but never
    restart the log - the WAL then grows without bound.
    """

    monkeypatch.setattr(workspace_endpoint, "_require_supported_runtime", lambda: None)

    async def _events(session, **_kwargs):
        session.reads += 1
        return SimpleNamespace(events=[], next_cursor=None)

    monkeypatch.setattr(workspace_endpoint, "workspace_events", _events)

    class _Session:
        def __init__(self) -> None:
            self.reads = 0
            self.rolled_back_after_reads: int | None = None

        async def rollback(self) -> None:
            self.rolled_back_after_reads = self.reads

    session = _Session()
    response = asyncio.run(
        workspace_endpoint.get_presentation_workspace_events(
            request=SimpleNamespace(),
            presentation_id=uuid.uuid4(),
            after=None,
            last_event_id=None,
            limit=200,
            stream=True,
            session=session,
        )
    )

    assert response.media_type == "text/event-stream"
    # Released after the validation read, not before - the check must still run.
    assert session.rolled_back_after_reads == 1


def test_unknown_bootstrap_endpoint_returns_typed_safe_422_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(workspace_endpoint, "_require_workspace_writes", lambda _id: None)
    request = WorkspaceBootstrapRequest.model_construct(
        idempotency_key="unknown-bootstrap-endpoint",
        prompt="Create a presentation",
        generation_settings={},
        asset_ids=[],
        attachments=[],
        operation_type="unknown.operation",
        expected_revision=None,
    )

    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            workspace_endpoint.bootstrap_presentation_workspace(
                presentation_id=uuid.uuid4(),
                request=request,
                session=None,
            )
        )

    assert captured.value.status_code == 422
    assert captured.value.detail == "unsupported_operation_type"


def test_operation_actions_do_not_dilute_operation_admission_slo(
    monkeypatch: pytest.MonkeyPatch,
):
    admissions: list[str] = []
    monkeypatch.setattr(
        workspace_endpoint,
        "_record_operation_admission",
        lambda operation_type, *, duplicate: admissions.append(operation_type)
        if not duplicate
        else None,
    )
    source_id = uuid.uuid4()
    existing = SimpleNamespace(id=source_id, kind="presentation.edit")

    for _ in range(1_000):
        workspace_endpoint._record_operation_action_admission(
            source_id,
            existing,
            duplicate=False,
        )

    replacement = SimpleNamespace(
        id=uuid.uuid4(),
        kind="presentation.edit",
    )
    workspace_endpoint._record_operation_action_admission(
        source_id,
        replacement,
        duplicate=False,
    )
    workspace_endpoint._record_operation_action_admission(
        source_id,
        replacement,
        duplicate=True,
    )

    assert admissions == ["presentation.edit"]
