from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
from pathlib import Path
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.public_artifact import (
    ArtifactRef,
    PublicArtifact,
    PublicExportIdentity,
    artifact_storage_key,
)
from services.artifact_store import (
    ArtifactNotFound,
    DurableArtifactService,
    ExportIdentityKey,
    LocalDevelopmentArtifactObjectStore,
    S3ArtifactObjectStore,
    claim_export_identity,
    complete_export_identity,
)
from services.public_worker_profiles import (
    get_public_worker_profile,
    validate_public_worker_environment,
)


PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _run(coroutine):
    return asyncio.run(coroutine)


def test_artifact_ref_rejects_unbound_or_path_like_storage_keys() -> None:
    owner_id = uuid.uuid4()
    digest = sha256(b"checkpoint").hexdigest()
    base = {
        "artifactId": str(uuid.uuid4()),
        "ownerId": str(owner_id),
        "tenantId": "tenant-a",
        "kind": "checkpoint",
        "sha256": digest,
        "sizeBytes": 10,
        "mediaType": "application/json",
        "createdByOperationId": str(uuid.uuid4()),
        "retentionClass": "checkpoint",
    }
    with pytest.raises(ValidationError, match="storage key"):
        ArtifactRef.model_validate({**base, "storageKey": "C:\\exports\\deck.json"})
    with pytest.raises(ValidationError, match="storage key"):
        ArtifactRef.model_validate(
            {
                **base,
                "storageKey": artifact_storage_key(
                    owner_id=uuid.uuid4(),
                    tenant_id="tenant-a",
                    digest=digest,
                ),
            }
        )


def test_s3_adapter_commits_owner_digest_metadata_and_readback() -> None:
    class MissingObject(Exception):
        response = {"Error": {"Code": "404"}}

    class FakeS3:
        def __init__(self) -> None:
            self.objects: dict[tuple[str, str], dict[str, object]] = {}

        def head_object(self, *, Bucket: str, Key: str):
            try:
                item = self.objects[(Bucket, Key)]
            except KeyError as exc:
                raise MissingObject from exc
            return {
                "ContentLength": len(item["payload"]),
                "Metadata": item["metadata"],
            }

        def put_object(self, **request):
            identity = (request["Bucket"], request["Key"])
            assert request["IfNoneMatch"] == "*"
            assert request["ServerSideEncryption"] == "AES256"
            self.objects[identity] = {
                "payload": bytes(request["Body"]),
                "metadata": dict(request["Metadata"]),
            }

        def get_object(self, *, Bucket: str, Key: str):
            try:
                item = self.objects[(Bucket, Key)]
            except KeyError as exc:
                raise MissingObject from exc
            return {"Body": io.BytesIO(item["payload"])}

    payload = b"rendered-png"
    owner_id = uuid.uuid4()
    digest = sha256(payload).hexdigest()
    reference = ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="tenant-a",
        kind="slide-png",
        sha256=digest,
        size_bytes=len(payload),
        media_type="image/png",
        storage_key=artifact_storage_key(
            owner_id=owner_id,
            tenant_id="tenant-a",
            digest=digest,
        ),
        created_by_operation_id=uuid.uuid4(),
        retention_class="ephemeral",
    )
    client = FakeS3()
    store = S3ArtifactObjectStore(bucket="artifact-bucket", client=client)

    async def scenario() -> None:
        await store.put_bytes(reference, payload)
        assert await store.exists(reference)
        assert await store.read_bytes(reference) == payload

    _run(scenario())
    stored = next(iter(client.objects.values()))
    assert stored["metadata"]["sha256"] == digest
    assert stored["metadata"]["owner-id"] == str(owner_id)


def test_durable_artifacts_are_owner_bound_and_export_is_cluster_single_flight(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'artifact-test.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[PublicArtifact.__table__, PublicExportIdentity.__table__],
                )
            )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        object_store = LocalDevelopmentArtifactObjectStore(tmp_path / "objects")
        artifacts = DurableArtifactService(object_store)
        owner_id = uuid.uuid4()
        other_owner_id = uuid.uuid4()
        operation_one = uuid.uuid4()
        operation_two = uuid.uuid4()
        canonical_payload = b'{"deck":"canonical"}'

        async with sessions() as session:
            source = await artifacts.persist_bytes(
                session,
                owner_id=owner_id,
                tenant_id="tenant-a",
                kind="checkpoint",
                payload=canonical_payload,
                media_type="application/json",
                created_by_operation_id=operation_one,
                retention_class="checkpoint",
            )
            duplicate = await artifacts.persist_bytes(
                session,
                owner_id=owner_id,
                tenant_id="tenant-a",
                kind="checkpoint",
                payload=canonical_payload,
                media_type="application/json",
                created_by_operation_id=operation_one,
                retention_class="checkpoint",
            )
            assert duplicate.artifact_id == source.artifact_id
            await session.commit()

            with pytest.raises(ArtifactNotFound, match="artifact.ref_not_found"):
                await artifacts.read_bytes(
                    session,
                    artifact_id=source.artifact_id,
                    owner_id=other_owner_id,
                    tenant_id="tenant-a",
                    expected_digest=source.sha256,
                )

            identity_key = ExportIdentityKey(
                owner_id=owner_id,
                tenant_id="tenant-a",
                presentation_id=uuid.uuid4(),
                revision=7,
                canonical_digest=source.sha256,
                export_mode="pptx",
                font_manifest_digest=sha256(b"fonts").hexdigest(),
                exporter_version="exporter-1.0",
                source_artifact_id=source.artifact_id,
            )
            started = datetime(2026, 8, 30, tzinfo=timezone.utc)
            first = await claim_export_identity(
                session,
                key=identity_key,
                operation_id=operation_one,
                worker_id="export-worker-1",
                lease_seconds=30,
                now=started,
            )
            assert first.disposition == "claimed"
            assert first.attempt_token is not None
            await session.commit()

            duplicate_claim = await claim_export_identity(
                session,
                key=identity_key,
                operation_id=operation_two,
                worker_id="export-worker-2",
                lease_seconds=30,
                now=started + timedelta(seconds=1),
            )
            assert duplicate_claim.disposition == "in_flight"
            assert duplicate_claim.identity_id == first.identity_id

            recovered = await claim_export_identity(
                session,
                key=identity_key,
                operation_id=operation_two,
                worker_id="export-worker-2",
                lease_seconds=30,
                now=started + timedelta(seconds=31),
            )
            assert recovered.disposition == "claimed"
            assert recovered.fencing_token == first.fencing_token + 1
            assert recovered.attempt_token is not None

            result = await artifacts.persist_bytes(
                session,
                owner_id=owner_id,
                tenant_id="tenant-a",
                kind="presentation-pptx",
                payload=b"pptx-package",
                media_type=PPTX_MEDIA_TYPE,
                created_by_operation_id=operation_two,
                retention_class="published",
            )
            stale_commit = await complete_export_identity(
                session,
                identity_id=first.identity_id,
                worker_id="export-worker-1",
                attempt_token=first.attempt_token,
                fencing_token=first.fencing_token,
                result_artifact_id=result.artifact_id,
            )
            assert stale_commit is False
            assert await complete_export_identity(
                session,
                identity_id=recovered.identity_id,
                worker_id="export-worker-2",
                attempt_token=recovered.attempt_token,
                fencing_token=recovered.fencing_token,
                result_artifact_id=result.artifact_id,
            )
            await session.commit()

            completed = await claim_export_identity(
                session,
                key=identity_key,
                operation_id=uuid.uuid4(),
                worker_id="export-worker-3",
                lease_seconds=30,
                now=started + timedelta(seconds=32),
            )
            assert completed.disposition == "completed"
            assert completed.artifact == result
        await engine.dispose()

    _run(scenario())


def test_isolated_worker_profiles_fail_closed_without_resource_contract() -> None:
    render = get_public_worker_profile("render")
    assert render.independently_scalable is True
    assert render.canonical_database_access is False
    assert render.network_mode == "none"
    with pytest.raises(RuntimeError, match="isolation_requirement_missing"):
        validate_public_worker_environment(render, {})

    isolated_environment = {
        "PPT_WORKER_NETWORK_MODE": "none",
        "PPT_WORKER_ROOT_FILESYSTEM": "read-only",
        "PPT_WORKER_SCRATCH": "tmpfs",
        "PPT_WORKER_RUN_AS_NON_ROOT": "true",
        "PPT_WORKER_MEMORY_LIMIT_BYTES": "1073741824",
        "PPT_WORKER_CPU_MILLIS": "1000",
        "PPT_WORKER_PID_LIMIT": "64",
        "PPT_WORKER_OPEN_FILE_LIMIT": "256",
        "PPT_WORKER_OUTPUT_LIMIT_BYTES": "268435456",
        "PPT_WORKER_WALL_CLOCK_SECONDS": "600",
        "PPT_WORKER_REPLICAS": "3",
    }
    validate_public_worker_environment(render, isolated_environment)
    with pytest.raises(RuntimeError, match="canonical_database_credential_forbidden"):
        validate_public_worker_environment(
            render,
            {**isolated_environment, "DATABASE_URL": "postgresql://not-used"},
        )
