from __future__ import annotations

import asyncio
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

from api.v1.ppt.endpoints import workspace as workspace_endpoint
from services.workspace_runtime_profile import (
    WorkspaceRuntimeKind,
    WorkspaceRuntimeProfile,
)
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactPublicationError,
)


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


def test_capabilities_fail_closed_for_missing_runtime_declaration(
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

    assert capabilities.supported is False
    assert capabilities.enabled is False
    assert capabilities.reason == "chat_first_runtime_profile_required"
    with pytest.raises(HTTPException) as error:
        workspace_endpoint._require_supported_runtime()
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
