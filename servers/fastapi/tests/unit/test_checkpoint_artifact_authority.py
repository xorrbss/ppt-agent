"""Checkpoint reads require durable ownership even when object bytes exist."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import uuid

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission
from models.sql.public_artifact import PublicArtifact
from models.sql.workspace import WorkspaceActionReceipt, WorkspaceOperation
from services import authored_checkpoint_artifacts as checkpoints
from services.artifact_store import (
    ArtifactNotFound,
    ArtifactStoreError,
    DurableArtifactService,
    LocalDevelopmentArtifactObjectStore,
)
from services.authored_slide_checkpoint_bundle import SlideCheckpointCorrupt
from services.workspace_service import canonical_payload_hash


@pytest.mark.parametrize("boundary", [
    "restart", "owner", "tenant", "operation", "missing-admission",
    "provisional", "tombstoned", "deleted", "corrupt-bytes",
    "retry-ancestor", "retry-two-hop", "retry-request-mismatch", "retry-missing-job-binding",
    "retry-receipt-missing",
    "canvas-match", "canvas-mismatch",
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
                            PresentationGenerationJob.__table__, WorkspaceOperation.__table__,
                            WorkspaceActionReceipt.__table__],
                )
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            monkeypatch.setattr(checkpoints, "async_session_maker", sessions)
            root = tmp_path / "objects"
            service = DurableArtifactService(LocalDevelopmentArtifactObjectStore(root))
            owner, operation, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            ancestor = uuid.uuid4()
            intermediate = uuid.uuid4()
            presentation_id = uuid.uuid4()
            retry_case = boundary in {
                "retry-ancestor", "retry-two-hop", "retry-request-mismatch", "retry-missing-job-binding",
                "retry-receipt-missing",
            }
            generation_request = {"content": "same", "aspect_ratio": "9:16"}
            if boundary in {"canvas-match", "canvas-mismatch"}:
                output = io.BytesIO()
                Image.new("RGB", (720, 1280)).save(output, format="PNG")
                payload = output.getvalue()
            else:
                payload = b"durable-render"
            async with sessions() as session:
                reference = await service.persist_bytes(
                    session,
                    owner_id=uuid.uuid4() if boundary == "owner" else owner,
                    tenant_id="other-tenant" if boundary == "tenant" else "tenant-a",
                    kind="slide-png", payload=payload, media_type="image/png",
                    created_by_operation_id=(
                        uuid.uuid4() if boundary == "operation" else (
                            ancestor if retry_case else operation
                        )
                    ),
                    retention_class="checkpoint",
                )
                session.add(PresentationGenerationJob(
                    id=job_id, task_id="test-task", presentation_id=presentation_id,
                    workspace_operation_id=operation, request_sha256="a" * 64,
                    request_payload=generation_request if retry_case else {},
                ))
                if retry_case:
                    conversation_id = uuid.uuid4()
                    identity = {
                        "owner_id": owner,
                        "owner_scope": str(owner),
                        "conversation_id": conversation_id,
                        "presentation_id": presentation_id,
                        "kind": "presentation.create",
                    }
                    root_payload = {
                        "generation_request": generation_request,
                        "task_id": "test-task",
                        "job_id": str(job_id),
                        "presentation_id": str(presentation_id),
                    }
                    retry_payload = {
                        **root_payload,
                        "retry_of_operation_id": str(ancestor),
                        "account_deletion_generation": 1,
                    }
                    if boundary == "retry-missing-job-binding":
                        root_payload.pop("job_id")
                        retry_payload.pop("job_id")
                    session.add(WorkspaceOperation(
                        id=ancestor,
                        anchor_position=1,
                        idempotency_key_hash="c" * 64,
                        request_hash=(
                            "d" * 64
                            if boundary == "retry-request-mismatch"
                            else "a" * 64
                        ),
                        request_payload=root_payload,
                        expected_revision=0,
                        **identity,
                    ))
                    current_parent = ancestor
                    current_payload = retry_payload
                    current_anchor = 2
                    if boundary == "retry-two-hop":
                        session.add(WorkspaceOperation(
                            id=intermediate,
                            retry_of_operation_id=ancestor,
                            anchor_position=2,
                            idempotency_key_hash="4" * 64,
                            request_hash=canonical_payload_hash({
                                "retry_of_operation_id": str(ancestor),
                                "presentation_revision": 0,
                                "request_payload": retry_payload,
                            }),
                            request_payload=retry_payload,
                            expected_revision=0,
                            **identity,
                        ))
                        session.add(WorkspaceActionReceipt(
                            owner_id=owner,
                            owner_scope=str(owner),
                            operation_id=ancestor,
                            result_operation_id=intermediate,
                            action_id="retry-1",
                            action_type="retry",
                            status="applied",
                            idempotency_key_hash="5" * 64,
                            request_hash="6" * 64,
                        ))
                        current_parent = intermediate
                        current_payload = {
                            **retry_payload,
                            "retry_of_operation_id": str(intermediate),
                        }
                        current_anchor = 3
                    session.add(WorkspaceOperation(
                        id=operation,
                        retry_of_operation_id=current_parent,
                        anchor_position=current_anchor,
                        idempotency_key_hash="e" * 64,
                        request_hash=canonical_payload_hash({
                            "retry_of_operation_id": str(current_parent),
                            "presentation_revision": 0,
                            "request_payload": current_payload,
                        }),
                        request_payload=current_payload,
                        expected_revision=0,
                        **identity,
                    ))
                    if boundary != "retry-receipt-missing":
                        session.add(WorkspaceActionReceipt(
                            owner_id=owner,
                            owner_scope=str(owner),
                            operation_id=current_parent,
                            result_operation_id=operation,
                            action_id="retry-current",
                            action_type="retry",
                            status="applied",
                            idempotency_key_hash="f" * 64,
                            request_hash="1" * 64,
                        ))
                if boundary != "missing-admission" and not retry_case:
                    session.add(PublicOperationAdmission(
                        operation_id=operation, policy_version="test-policy",
                        owner_id=owner, tenant_id="tenant-a", trace_id_hash="a" * 64,
                        operation_kind="generation", resource_units=1,
                        policy_digest="b" * 64,
                        deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
                        planned_request_limit=1, retry_request_limit=1,
                        estimated_cost_limit_microunits=1, actual_cost_limit_microunits=1,
                    ))
                if retry_case:
                    session.add(PublicOperationAdmission(
                        operation_id=ancestor, policy_version="test-policy",
                        owner_id=owner, tenant_id="tenant-a", trace_id_hash="c" * 64,
                        operation_kind="generation", resource_units=1,
                        policy_digest="d" * 64,
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
            if boundary in {"restart", "retry-ancestor", "retry-two-hop", "canvas-match"}:
                assert await checkpoints.read_valid_render_artifact(
                    checkpoint,
                    expected_width_px=720 if boundary == "canvas-match" else None,
                    expected_height_px=1280 if boundary == "canvas-match" else None,
                ) == payload
            elif boundary == "canvas-mismatch":
                with pytest.raises(SlideCheckpointCorrupt, match="canvas_mismatch"):
                    await checkpoints.read_valid_render_artifact(
                        checkpoint,
                        expected_width_px=1280,
                        expected_height_px=720,
                    )
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
