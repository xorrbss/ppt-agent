from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
from pathlib import Path
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.public_artifact import (
    ArtifactRef,
    PublicArtifact,
    PublicArtifactPublication,
    artifact_storage_key,
)
from models.sql.presentation import PresentationModel
from models.sql.public_admission import PublicOperationAdmission, PublicPolicyProfile
from models.sql.user import User
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
from services.artifact_lifecycle import (
    ArtifactLifecycleRecord,
    ShadowArtifactLifecycleService,
)
from services.artifact_store import (
    ArtifactByteRange,
    ArtifactStoreError,
    LocalDevelopmentArtifactObjectStore,
    ProvisionalArtifactRef,
    DurableArtifactService,
    promote_artifact_publication_if_current,
)


def _artifact(payload: bytes, *, kind: str = "checkpoint") -> ArtifactRef:
    owner_id = uuid.uuid4()
    tenant_id = "fault-tenant"
    digest = sha256(payload).hexdigest()
    return ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id=tenant_id,
        kind=kind,
        sha256=digest,
        size_bytes=len(payload),
        media_type="application/json",
        storage_key=artifact_storage_key(
            owner_id=owner_id, tenant_id=tenant_id, digest=digest
        ),
        created_by_operation_id=uuid.uuid4(),
        retention_class="checkpoint",
    )


class _ChunkedReader(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            raise AssertionError("artifact writer attempted whole-file read")
        return super().read(min(size, 3))


class _CasAuthority:
    def __init__(self, record: ArtifactLifecycleRecord) -> None:
        self.record = record
        self.force_stale = False

    async def compare_and_set(self, **kwargs):
        if self.force_stale:
            return None
        if (
            self.record.state != kwargs["expected_state"]
            or self.record.state_revision != kwargs["expected_revision"]
        ):
            return None
        self.record = ArtifactLifecycleRecord(
            artifact=self.record.artifact,
            state=kwargs["next_state"],
            ref_count=self.record.ref_count + kwargs.get("ref_count_delta", 0),
            state_revision=self.record.state_revision + 1,
            updated_at=datetime.now(timezone.utc),
        )
        return self.record

    async def list_gc_candidates(self, *, older_than, limit):
        if (
            self.record.state == "tombstoned"
            and self.record.ref_count == 0
            and self.record.updated_at <= older_than
        ):
            return [self.record]
        return []


def test_artifact_fault_boundaries_stream_digest_orphan_gc_and_stale_cas(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        payload = b"0123456789abcdef"
        reference = _artifact(payload)
        store = LocalDevelopmentArtifactObjectStore(tmp_path / "objects")
        source = _ChunkedReader(payload)
        await store.put_stream(reference, source)
        assert source.read_sizes and max(source.read_sizes) <= 1024 * 1024

        ranged = await store.open_read_stream(
            reference, byte_range=ArtifactByteRange(start=2, end_inclusive=7)
        )
        try:
            assert ranged.body.read() == payload[2:8]
            assert ranged.content_range == "bytes 2-7/16"
        finally:
            ranged.close()

        object_path = tmp_path / "objects" / Path(*reference.storage_key.split("/"))
        object_path.write_bytes(b"tampered")
        with pytest.raises(ArtifactStoreError, match="immutable_object_conflict"):
            await store.head(reference)

        # A provisional object is independently sweepable, while a committed
        # object with a live reference is not a GC candidate.
        orphan_reference = _artifact(b"orphan")
        orphan = ProvisionalArtifactRef.create(orphan_reference)
        await store.put_provisional_stream(orphan, io.BytesIO(b"orphan"))
        orphan_path = tmp_path / "objects" / Path(*orphan.storage_key.split("/"))
        old = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        import os

        os.utime(orphan_path, (old, old))
        assert await store.sweep_provisional_orphans(
            older_than=datetime(2021, 1, 1, tzinfo=timezone.utc), limit=1
        ) == 1

        lifecycle_payload = b"referenced"
        lifecycle_reference = _artifact(lifecycle_payload)
        await store.put_stream(lifecycle_reference, io.BytesIO(lifecycle_payload))
        lifecycle_record = ArtifactLifecycleRecord(
            artifact=lifecycle_reference,
            state="provisional",
            ref_count=0,
            state_revision=1,
            updated_at=datetime.now(timezone.utc),
        )
        authority = _CasAuthority(lifecycle_record)
        lifecycle = ShadowArtifactLifecycleService(
            authority=authority, object_store=store, enabled=True
        )
        committed = await lifecycle.transition(authority.record, "committed")
        referenced = await lifecycle.change_ref_count(committed, delta=1)
        assert await lifecycle.collect_once(
            older_than=datetime.now(timezone.utc) + timedelta(seconds=1), limit=1
        ) == 0
        released = await lifecycle.change_ref_count(referenced, delta=-1)
        authority.force_stale = True
        with pytest.raises(ArtifactStoreError, match="lifecycle_fence_stale"):
            await lifecycle.transition(released, "tombstoned")

    asyncio.run(scenario())


def test_publication_pointer_rejects_stale_fence_and_moves_refcount_atomically(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pointer.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[
                        User.__table__,
                        PresentationModel.__table__,
                        WorkspaceConversation.__table__,
                        WorkspaceOperation.__table__,
                        PublicPolicyProfile.__table__,
                        PublicOperationAdmission.__table__,
                        PublicArtifact.__table__,
                        PublicArtifactPublication.__table__,
                    ],
                )
            )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner_id = uuid.uuid4()
        operation_id = uuid.uuid4()
        artifacts = DurableArtifactService(
            LocalDevelopmentArtifactObjectStore(tmp_path / "pointer-objects")
        )
        async with sessions() as session:
            session.add(
                WorkspaceOperation(
                    id=operation_id,
                    owner_id=owner_id,
                    owner_scope="owner",
                    conversation_id=uuid.uuid4(),
                    presentation_id=uuid.uuid4(),
                    anchor_position=1,
                    kind="export.pptx",
                    state="running",
                    idempotency_key_hash=sha256(b"idempotency").hexdigest(),
                    request_hash=sha256(b"request").hexdigest(),
                    request_payload={},
                    attempt_number=1,
                    attempt_token="attempt-one",
                    fencing_token=11,
                )
            )
            session.add(
                PublicOperationAdmission(
                    operation_id=operation_id,
                    policy_version="test-policy-v1",
                    owner_id=owner_id,
                    tenant_id="tenant-a",
                    trace_id_hash=sha256(b"trace").hexdigest(),
                    operation_kind="export",
                    resource_units=1,
                    policy_digest=sha256(b"policy").hexdigest(),
                    deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                    fence=11,
                    state="active",
                    planned_request_limit=0,
                    retry_request_limit=0,
                    estimated_cost_limit_microunits=0,
                    actual_cost_limit_microunits=0,
                )
            )
            first = await artifacts.persist_bytes(
                session,
                owner_id=owner_id,
                tenant_id="tenant-a",
                kind="presentation-pptx",
                payload=b"first",
                media_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "presentationml.presentation"
                ),
                created_by_operation_id=operation_id,
                retention_class="published",
            )
            second = await artifacts.persist_bytes(
                session,
                owner_id=owner_id,
                tenant_id="tenant-a",
                kind="presentation-pptx",
                payload=b"second",
                media_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "presentationml.presentation"
                ),
                created_by_operation_id=operation_id,
                retention_class="published",
            )
            pointer = await promote_artifact_publication_if_current(
                session,
                operation_id=operation_id,
                owner_id=owner_id,
                tenant_id="tenant-a",
                attempt_number=1,
                attempt_token="attempt-one",
                fencing_token=11,
                publication_name="presentation.pptx",
                revision=7,
                quality_digest=sha256(b"quality-7").hexdigest(),
                artifact_id=first.artifact_id,
            )
            assert pointer is not None
            await session.commit()

            operation = await session.get(WorkspaceOperation, operation_id)
            assert operation is not None
            operation.attempt_number = 2
            operation.attempt_token = "attempt-two"
            operation.fencing_token = 12
            session.add(operation)
            admission = await session.scalar(
                select(PublicOperationAdmission).where(
                    PublicOperationAdmission.operation_id == operation_id
                )
            )
            assert admission is not None
            admission.fence = 12
            session.add(admission)
            await session.commit()

            stale = await promote_artifact_publication_if_current(
                session,
                operation_id=operation_id,
                owner_id=owner_id,
                tenant_id="tenant-a",
                attempt_number=1,
                attempt_token="attempt-one",
                fencing_token=11,
                publication_name="presentation.pptx",
                revision=8,
                quality_digest=sha256(b"quality-8").hexdigest(),
                artifact_id=second.artifact_id,
            )
            assert stale is None
            current = await promote_artifact_publication_if_current(
                session,
                operation_id=operation_id,
                owner_id=owner_id,
                tenant_id="tenant-a",
                attempt_number=2,
                attempt_token="attempt-two",
                fencing_token=12,
                publication_name="presentation.pptx",
                revision=8,
                quality_digest=sha256(b"quality-8").hexdigest(),
                artifact_id=second.artifact_id,
            )
            assert current is not None
            await session.commit()
            first_row = await session.get(PublicArtifact, first.artifact_id)
            second_row = await session.get(PublicArtifact, second.artifact_id)
            assert first_row is not None and first_row.refcount == 0
            assert second_row is not None and second_row.refcount == 1
            assert current.artifact_id == second.artifact_id
            assert current.quality_digest == sha256(b"quality-8").hexdigest()
        await engine.dispose()

    asyncio.run(scenario())
