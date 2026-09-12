"""Checkpoint reads require durable ownership even when object bytes exist."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission
from models.sql.public_artifact import PublicArtifact
from services import authored_checkpoint_artifacts as checkpoints
from services.artifact_store import (
    ArtifactNotFound,
    ArtifactStoreError,
    DurableArtifactService,
    LocalDevelopmentArtifactObjectStore,
)
from services.authored_slide_checkpoint_bundle import SlideCheckpointCorrupt


@pytest.mark.parametrize("boundary", [
    "restart", "owner", "tenant", "operation", "missing-admission",
    "provisional", "tombstoned", "deleted", "corrupt-bytes",
])
def test_checkpoint_read_uses_durable_authority(tmp_path, monkeypatch, boundary):
    monkeypatch.setenv("ENVIRONMENT", "test")
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'authority.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    SQLModel.metadata.create_all,
                    tables=[PublicArtifact.__table__, PublicOperationAdmission.__table__,
                            PresentationGenerationJob.__table__],
                )
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            monkeypatch.setattr(checkpoints, "async_session_maker", sessions)
            root = tmp_path / "objects"
            service = DurableArtifactService(LocalDevelopmentArtifactObjectStore(root))
            owner, operation, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            payload = b"durable-render"
            async with sessions() as session:
                reference = await service.persist_bytes(
                    session,
                    owner_id=uuid.uuid4() if boundary == "owner" else owner,
                    tenant_id="other-tenant" if boundary == "tenant" else "tenant-a",
                    kind="slide-png", payload=payload, media_type="image/png",
                    created_by_operation_id=(
                        uuid.uuid4() if boundary == "operation" else operation
                    ),
                    retention_class="checkpoint",
                )
                session.add(PresentationGenerationJob(
                    id=job_id, task_id="test-task", presentation_id=uuid.uuid4(),
                    workspace_operation_id=operation, request_sha256="a" * 64,
                    request_payload={},
                ))
                if boundary != "missing-admission":
                    session.add(PublicOperationAdmission(
                        operation_id=operation, policy_version="test-policy",
                        owner_id=owner, tenant_id="tenant-a", trace_id_hash="a" * 64,
                        operation_kind="generation", resource_units=1,
                        policy_digest="b" * 64,
                        deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
                        planned_request_limit=1, retry_request_limit=1,
                        estimated_cost_limit_microunits=1, actual_cost_limit_microunits=1,
                    ))
                if boundary in {"provisional", "tombstoned", "deleted"}:
                    row = await session.get(PublicArtifact, reference.artifact_id)
                    row.lifecycle_state = boundary
                await session.commit()
            # A newly constructed adapter must work without any process-local state.
            reopened = DurableArtifactService(LocalDevelopmentArtifactObjectStore(root))
            monkeypatch.setattr(checkpoints, "configured_public_artifact_service", lambda: reopened)
            checkpoint = AuthoredSlideCheckpoint(
                job_id=job_id, slide_index=0, render_sha256=sha256(payload).hexdigest(),
                artifact_refs={"render": reference.model_dump(mode="json", by_alias=True)},
            )
            if boundary == "restart":
                assert await checkpoints.read_valid_render_artifact(checkpoint) == payload
            elif boundary == "corrupt-bytes":
                (root / reference.storage_key).write_bytes(b"corrupt")
                with pytest.raises(ArtifactStoreError):
                    await checkpoints.read_valid_render_artifact(checkpoint)
            elif boundary in {"provisional", "tombstoned", "deleted"}:
                with pytest.raises(ArtifactNotFound, match="artifact.ref_not_found"):
                    await checkpoints.read_valid_render_artifact(checkpoint)
            else:
                with pytest.raises(SlideCheckpointCorrupt, match="owner_binding_invalid"):
                    await checkpoints.read_valid_render_artifact(checkpoint)
        finally:
            await engine.dispose()
    asyncio.run(scenario())


@pytest.mark.parametrize("refs", [[], {"render": None}, {"render": "invalid"}, {}])
def test_present_invalid_refs_never_fall_back_to_local_cache(tmp_path, monkeypatch, refs):
    monkeypatch.setenv("AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path))
    (tmp_path / "render.png").write_bytes(b"cached")
    checkpoint = AuthoredSlideCheckpoint(
        job_id=uuid.uuid4(), slide_index=0, render_path="render.png",
        render_sha256=sha256(b"cached").hexdigest(), artifact_refs=refs,
    )
    with pytest.raises(SlideCheckpointCorrupt):
        asyncio.run(checkpoints.read_valid_render_artifact(checkpoint))


def test_missing_optional_public_evidence_does_not_read_legacy_sidecar(monkeypatch):
    checkpoint = AuthoredSlideCheckpoint(job_id=uuid.uuid4(), slide_index=0, artifact_refs={})
    def unexpected_legacy_read(*args, **kwargs):
        pytest.fail("public checkpoint attempted local evidence fallback")
    monkeypatch.setattr(checkpoints, "read_valid_evidence", unexpected_legacy_read)
    assert asyncio.run(checkpoints.read_valid_evidence_artifact(checkpoint)) is None


def test_legacy_local_checkpoint_render_remains_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path))
    (tmp_path / "render.png").write_bytes(b"cached")
    checkpoint = AuthoredSlideCheckpoint(
        job_id=uuid.uuid4(), slide_index=0, render_path="render.png",
        render_sha256=sha256(b"cached").hexdigest(), artifact_refs=None,
    )
    assert asyncio.run(checkpoints.read_valid_render_artifact(checkpoint)) == b"cached"
