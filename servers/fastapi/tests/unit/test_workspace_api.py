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
from services import workspace_rollout_policy
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


def test_capabilities_keep_read_only_timeline_during_feature_rollback(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        workspace_endpoint,
        "_runtime_profile",
        lambda: _profile(supported=True),
    )
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "false")

    capabilities = asyncio.run(workspace_endpoint.workspace_capabilities())

    assert capabilities.supported is True
    assert capabilities.enabled is False
    assert capabilities.timeline is True
    assert capabilities.events is True
    assert capabilities.commands is False
    assert capabilities.reason == "chat_first_feature_disabled"
    workspace_endpoint._require_supported_runtime()
    with pytest.raises(HTTPException) as disabled:
        workspace_endpoint._require_workspace_writes()
    assert disabled.value.status_code == 503
    assert disabled.value.detail == "chat_first_feature_disabled"


@pytest.mark.parametrize(
    ("feature_enabled", "capability_reason"),
    [
        (False, "chat_first_feature_disabled"),
        (True, "chat_first_runtime_unsupported"),
    ],
)
def test_capabilities_fail_closed_for_unsupported_runtime(
    monkeypatch: pytest.MonkeyPatch,
    feature_enabled: bool,
    capability_reason: str,
):
    monkeypatch.setattr(
        workspace_endpoint,
        "_runtime_profile",
        lambda: _profile(supported=False),
    )
    monkeypatch.setenv(
        "CHAT_FIRST_WORKSPACE_ENABLED",
        "true" if feature_enabled else "false",
    )

    capabilities = asyncio.run(workspace_endpoint.workspace_capabilities())

    assert capabilities.supported is False
    assert capabilities.enabled is False
    assert capabilities.timeline is False
    assert capabilities.events is False
    assert capabilities.commands is False
    assert capabilities.runtime_profile == "unsupported"
    assert capabilities.reason == capability_reason
    with pytest.raises(HTTPException) as unsupported_read:
        workspace_endpoint._require_supported_runtime()
    assert unsupported_read.value.status_code == 501
    assert unsupported_read.value.detail == "chat_first_runtime_unsupported"
    with pytest.raises(HTTPException) as unsupported_write:
        workspace_endpoint._require_workspace_writes()
    assert unsupported_write.value.status_code == (501 if feature_enabled else 503)
    assert unsupported_write.value.detail == capability_reason


def test_capabilities_keep_reads_available_for_missing_write_declaration(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        workspace_endpoint,
        "_runtime_profile",
        lambda: _profile(supported=True),
    )
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "true")
    monkeypatch.delenv("CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE", raising=False)

    capabilities = asyncio.run(workspace_endpoint.workspace_capabilities())

    assert capabilities.supported is True
    assert capabilities.timeline is True
    assert capabilities.events is True
    assert capabilities.enabled is False
    assert capabilities.reason == "chat_first_runtime_profile_required"
    workspace_endpoint._require_supported_runtime()
    with pytest.raises(HTTPException) as error:
        workspace_endpoint._require_workspace_writes()
    assert error.value.status_code == 503
    assert error.value.detail == "chat_first_runtime_profile_required"


def test_capabilities_accept_matching_runtime_declaration(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        workspace_endpoint,
        "_runtime_profile",
        lambda: _profile(supported=True),
    )
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE", "sqlite-local")

    capabilities = asyncio.run(workspace_endpoint.workspace_capabilities())

    assert capabilities.supported is True
    assert capabilities.enabled is True
    assert capabilities.reason is None
    workspace_endpoint._require_supported_runtime()


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


def test_partial_rollout_capability_matches_scoped_write_gate_truth_table(
    monkeypatch: pytest.MonkeyPatch,
):
    owner_id = uuid.UUID("00000000-0000-0000-0000-000000000321")
    salt = "capability-write-gate-contract"
    monkeypatch.setattr(
        workspace_endpoint,
        "_runtime_profile",
        lambda: _profile(supported=True),
    )
    monkeypatch.setattr(
        workspace_rollout_policy,
        "get_current_owner_id",
        lambda: owner_id,
    )
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE", "sqlite-local")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ROLLOUT_PERCENT", "50")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ROLLOUT_SALT", salt)
    monkeypatch.delenv("CHAT_FIRST_WORKSPACE_KILL_SWITCH", raising=False)

    candidates = (uuid.UUID(int=value) for value in range(1, 100))
    buckets = {
        presentation_id: workspace_rollout_policy.workspace_cohort_bucket(
            presentation_id,
            owner_id=owner_id,
            salt=salt,
        )
        for presentation_id in candidates
    }
    included_id = next(
        presentation_id
        for presentation_id, bucket in buckets.items()
        if bucket < 5_000
    )
    excluded_id = next(
        presentation_id
        for presentation_id, bucket in buckets.items()
        if bucket >= 5_000
    )

    unscoped = asyncio.run(workspace_endpoint.workspace_capabilities())
    assert unscoped.reads_supported is True
    assert unscoped.timeline is True
    assert unscoped.events is True
    assert unscoped.writes_supported is False
    assert unscoped.commands_enabled is False
    assert unscoped.cohort_bucket is None
    assert unscoped.cohort_eligible is False
    assert unscoped.reason == "chat_first_presentation_scope_required"
    with pytest.raises(HTTPException) as unscoped_write:
        workspace_endpoint._require_workspace_writes()
    assert unscoped_write.value.status_code == 503
    assert unscoped_write.value.detail == unscoped.reason
    assert unscoped_write.value.retryable is False

    for presentation_id, expected_enabled, expected_reason in (
        (included_id, True, None),
        (excluded_id, False, "chat_first_cohort_excluded"),
    ):
        capabilities = asyncio.run(
            workspace_endpoint.workspace_capabilities(presentation_id)
        )
        assert capabilities.reads_supported is True
        assert capabilities.writes_supported is expected_enabled
        assert capabilities.commands_enabled is expected_enabled
        assert capabilities.cohort_eligible is expected_enabled
        assert capabilities.cohort_bucket == buckets[presentation_id]
        assert capabilities.reason == expected_reason

        if expected_enabled:
            decision = workspace_endpoint._require_workspace_writes(
                presentation_id
            )
            assert decision.enabled is capabilities.writes_supported
            assert decision.cohort_eligible is capabilities.cohort_eligible
            assert decision.cohort_bucket == capabilities.cohort_bucket
            assert decision.reason == capabilities.reason
        else:
            with pytest.raises(HTTPException) as excluded_write:
                workspace_endpoint._require_workspace_writes(presentation_id)
            assert excluded_write.value.status_code == 503
            assert excluded_write.value.detail == capabilities.reason
            assert excluded_write.value.retryable is False


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
