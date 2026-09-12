"""Public export readback must retain its durable admission authority."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.public_admission import PublicOperationAdmission
from models.sql.public_artifact import PublicArtifact
from services import artifact_store
from services import workspace_artifact_publication_service as publication
from utils.owner_context import reset_current_owner_id, set_current_owner_id


@pytest.mark.parametrize("boundary", [
    "valid", "tenant", "owner", "operation", "missing-admission",
    "provisional", "tombstoned", "deleted", "retention", "kind",
    "digest", "length", "corrupt-bytes",
])
def test_export_readback_checks_durable_authority(tmp_path, monkeypatch, boundary):
    monkeypatch.setenv("ENVIRONMENT", "test")

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'export.db'}")
        owner, operation = uuid.uuid4(), uuid.uuid4()
        owner_token = set_current_owner_id(owner)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all, tables=[
                    PublicArtifact.__table__, PublicOperationAdmission.__table__,
                ])
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            root = tmp_path / "objects"
            service = artifact_store.DurableArtifactService(
                artifact_store.LocalDevelopmentArtifactObjectStore(root)
            )
            monkeypatch.setattr(artifact_store, "configured_public_artifact_service", lambda: service)
            payload = b"immutable-export"
            async with sessions() as session:
                reference = await service.persist_bytes(
                    session, owner_id=owner, tenant_id="tenant-a",
                    kind="presentation-pdf", payload=payload, media_type="application/pdf",
                    created_by_operation_id=operation, retention_class="published",
                )
                if boundary != "missing-admission":
                    session.add(PublicOperationAdmission(
                        operation_id=uuid.uuid4() if boundary == "operation" else operation,
                        owner_id=owner,
                        tenant_id="tenant-b" if boundary == "tenant" else "tenant-a",
                        policy_version="test-policy", trace_id_hash="a" * 64,
                        operation_kind="export", resource_units=1, policy_digest="b" * 64,
                        deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
                        planned_request_limit=1, retry_request_limit=1,
                        estimated_cost_limit_microunits=1, actual_cost_limit_microunits=1,
                    ))
                await session.commit()
                if boundary == "owner":
                    # Owner-scoped inserts normalize to the current principal.
                    # Corrupt durable authority explicitly after insertion.
                    await session.execute(
                        update(PublicOperationAdmission)
                        .where(PublicOperationAdmission.operation_id == operation)
                        .values(owner_id=uuid.uuid4())
                    )
                    await session.commit()
                resolved = publication.ResolvedWorkspaceExportArtifact(
                    artifact_id=uuid.uuid4(), operation_id=operation,
                    presentation_id=uuid.uuid4(), kind="export.pdf", source_revision=1,
                    sha256=reference.sha256, size_bytes=reference.size_bytes,
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    absolute_path=None, public_artifact_id=reference.artifact_id,
                )
                if boundary in {"provisional", "tombstoned", "deleted", "retention", "kind", "digest", "length"}:
                    # Mutation after a successful resolution must also be rejected at readback.
                    assert await publication._resolve_public_workspace_export(session, resolved)
                    row = await session.get(PublicArtifact, reference.artifact_id)
                    if boundary in {"provisional", "tombstoned", "deleted"}:
                        row.lifecycle_state = boundary
                    elif boundary == "retention":
                        row.retention_class = "checkpoint"
                    elif boundary == "kind":
                        row.kind = "presentation-pptx"
                    elif boundary == "digest":
                        row.sha256 = "0" * 64
                    else:
                        row.size_bytes += 1
                    await session.commit()
                if boundary == "valid":
                    assert await publication._resolve_public_workspace_export(session, resolved)
                    assert await publication.read_workspace_export_artifact_bytes(session, resolved) == payload
                elif boundary == "corrupt-bytes":
                    (root / reference.storage_key).write_bytes(b"corrupt")
                    with pytest.raises(artifact_store.ArtifactStoreError):
                        await publication.read_workspace_export_artifact_bytes(session, resolved)
                else:
                    with pytest.raises(publication.WorkspaceArtifactPublicationError, match="workspace_export_missing"):
                        await publication._resolve_public_workspace_export(session, resolved)
                    with pytest.raises(publication.WorkspaceArtifactPublicationError, match="workspace_export_missing"):
                        await publication.read_workspace_export_artifact_bytes(session, resolved)
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())
