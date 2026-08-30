from __future__ import annotations

import asyncio
from dataclasses import replace
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
    ArtifactByteRange,
    ArtifactNotFound,
    ArtifactStoreError,
    DurableArtifactWorkerObjectPort,
    DurableArtifactService,
    ExportIdentityKey,
    LocalDevelopmentArtifactObjectStore,
    ProvisionalArtifactRef,
    S3ArtifactObjectStore,
    claim_export_identity,
    complete_export_identity,
    fail_export_identity,
)
from services.isolated_worker_contracts import ArtifactRef as WorkerArtifactRef
from services.artifact_lifecycle import (
    ArtifactLifecycleService,
    ArtifactLifecycleRecord,
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
            self.uploads: dict[str, dict[str, object]] = {}

        def head_object(self, *, Bucket: str, Key: str):
            try:
                item = self.objects[(Bucket, Key)]
            except KeyError as exc:
                raise MissingObject from exc
            return {
                "ContentLength": len(item["payload"]),
                "Metadata": item["metadata"],
                "ContentType": item["content_type"],
                "ETag": item["etag"],
                "LastModified": item["last_modified"],
            }

        def put_object(self, **request):
            identity = (request["Bucket"], request["Key"])
            assert request["IfNoneMatch"] == "*"
            assert request["ServerSideEncryption"] == "AES256"
            body = request["Body"]
            payload = body.read() if hasattr(body, "read") else bytes(body)
            self.objects[identity] = {
                "payload": payload,
                "metadata": dict(request["Metadata"]),
                "content_type": request["ContentType"],
                "etag": f'"{sha256(payload).hexdigest()}"',
                "last_modified": datetime.now(timezone.utc),
            }

        def create_multipart_upload(self, **request):
            upload_id = str(uuid.uuid4())
            self.uploads[upload_id] = {"request": request, "parts": {}}
            return {"UploadId": upload_id}

        def upload_part(self, **request):
            upload = self.uploads[request["UploadId"]]
            upload["parts"][request["PartNumber"]] = bytes(request["Body"])
            return {"ETag": f'"part-{request["PartNumber"]}"'}

        def complete_multipart_upload(self, **request):
            upload = self.uploads.pop(request["UploadId"])
            created = upload["request"]
            payload = b"".join(
                upload["parts"][part["PartNumber"]]
                for part in request["MultipartUpload"]["Parts"]
            )
            self.objects[(created["Bucket"], created["Key"])] = {
                "payload": payload,
                "metadata": dict(created["Metadata"]),
                "content_type": created["ContentType"],
                "etag": f'"{sha256(payload).hexdigest()}"',
                "last_modified": datetime.now(timezone.utc),
            }

        def abort_multipart_upload(self, **request):
            self.uploads.pop(request["UploadId"], None)

        def copy_object(self, **request):
            source = self.objects[(request["CopySource"]["Bucket"], request["CopySource"]["Key"])]
            payload = source["payload"]
            self.objects[(request["Bucket"], request["Key"])] = {
                "payload": payload,
                "metadata": dict(request["Metadata"]),
                "content_type": request["ContentType"],
                "etag": f'"{sha256(payload).hexdigest()}"',
                "last_modified": datetime.now(timezone.utc),
            }

        def get_object(self, *, Bucket: str, Key: str, Range: str | None = None):
            try:
                item = self.objects[(Bucket, Key)]
            except KeyError as exc:
                raise MissingObject from exc
            payload = item["payload"]
            response: dict[str, object] = {"Body": io.BytesIO(payload)}
            if Range is not None:
                start_text, end_text = Range.removeprefix("bytes=").split("-", 1)
                start = int(start_text)
                end = len(payload) - 1 if not end_text else int(end_text)
                payload = payload[start : end + 1]
                response = {
                    "Body": io.BytesIO(payload),
                    "ContentLength": len(payload),
                    "ContentRange": f"bytes {start}-{end}/{len(item['payload'])}",
                }
            return response

        def delete_object(self, *, Bucket: str, Key: str):
            self.objects.pop((Bucket, Key), None)

        def list_objects_v2(self, *, Bucket: str, Prefix: str, MaxKeys: int):
            contents = [
                {"Key": key, "LastModified": item["last_modified"]}
                for (bucket, key), item in self.objects.items()
                if bucket == Bucket and key.startswith(Prefix)
            ]
            return {"Contents": contents[:MaxKeys]}

        def generate_presigned_url(self, operation: str, *, Params, ExpiresIn: int):
            assert operation == "get_object"
            assert 1 <= ExpiresIn <= 300
            return f"https://object-store.invalid/{Params['Bucket']}/{Params['Key']}"

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

    class ChunkedReader(io.BytesIO):
        def __init__(self, data: bytes) -> None:
            super().__init__(data)
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            if size < 0:
                raise AssertionError("S3 adapter attempted a whole-file read")
            return super().read(min(size, 3))

    async def scenario() -> None:
        provisional = ProvisionalArtifactRef.create(reference)
        source = ChunkedReader(payload)
        await store.put_provisional_stream(provisional, source)
        assert source.read_sizes and max(source.read_sizes) <= 8 * 1024 * 1024
        await store.commit_provisional(provisional)
        assert await store.exists(reference)
        assert await store.read_bytes(reference) == payload
        ranged = await store.open_read_stream(
            reference,
            byte_range=ArtifactByteRange(start=2, end_inclusive=7),
        )
        assert b"".join(ranged.iter_chunks(chunk_size=2)) == payload[2:8]
        assert ranged.content_range == f"bytes 2-7/{len(payload)}"
        signed = await store.create_signed_download(reference, expires_seconds=120)
        assert signed.range_supported is True
        assert signed.url.startswith("https://object-store.invalid/")

        orphan = ProvisionalArtifactRef.create(reference)
        await store.put_provisional_stream(orphan, io.BytesIO(payload))
        orphan_key = ("artifact-bucket", f"presenton-artifacts/{orphan.storage_key}")
        client.objects[orphan_key]["last_modified"] = datetime(2020, 1, 1, tzinfo=timezone.utc)
        assert await store.sweep_provisional_orphans(
            older_than=datetime(2021, 1, 1, tzinfo=timezone.utc),
            limit=1,
        ) == 1

    _run(scenario())
    stored = client.objects[("artifact-bucket", f"presenton-artifacts/{reference.storage_key}")]
    assert stored["metadata"]["sha256"] == digest
    assert stored["metadata"]["owner-id"] == str(owner_id)


def test_worker_object_port_streams_exact_stage_and_publish(tmp_path: Path) -> None:
    source_payload = b"sandbox-input" * 1024
    owner_id = uuid.uuid4()
    operation_id = uuid.uuid4()

    def reference_for(*, payload: bytes, kind: str, media_type: str) -> ArtifactRef:
        digest = sha256(payload).hexdigest()
        return ArtifactRef(
            artifact_id=uuid.uuid4(),
            owner_id=owner_id,
            tenant_id="tenant-a",
            kind=kind,
            sha256=digest,
            size_bytes=len(payload),
            media_type=media_type,
            storage_key=artifact_storage_key(
                owner_id=owner_id,
                tenant_id="tenant-a",
                digest=digest,
            ),
            created_by_operation_id=operation_id,
            retention_class=("published" if kind.startswith("presentation-") else "ephemeral"),
        )

    async def scenario() -> None:
        store = LocalDevelopmentArtifactObjectStore(tmp_path / "objects")
        service = DurableArtifactService(store)
        port = DurableArtifactWorkerObjectPort(service)
        source_ref = reference_for(
            payload=source_payload,
            kind="export-bundle",
            media_type="application/vnd.presenton.export-bundle+json",
        )
        await store.put_stream(source_ref, io.BytesIO(source_payload))
        staged = (tmp_path / "sandbox" / "input" / source_ref.storage_key).resolve()
        await port.stage(
            reference=WorkerArtifactRef.from_wire(
                source_ref.model_dump(mode="json", by_alias=True)
            ),
            destination=staged,
        )
        assert staged.read_bytes() == source_payload

        result_payload = b"pptx-output" * 1024
        result_ref = reference_for(
            payload=result_payload,
            kind="presentation-pptx",
            media_type=PPTX_MEDIA_TYPE,
        )
        result_path = (tmp_path / "sandbox" / "tmp" / "result.pptx").resolve()
        result_path.parent.mkdir(parents=True)
        result_path.write_bytes(result_payload)
        await port.publish(
            reference=WorkerArtifactRef.from_wire(
                result_ref.model_dump(mode="json", by_alias=True)
            ),
            source=result_path,
        )
        assert await store.read_bytes(result_ref) == result_payload

    _run(scenario())


def test_feature_lifecycle_refcount_expiry_and_gc_fail_closed(tmp_path: Path) -> None:
    payload = b"checkpoint"
    owner_id = uuid.uuid4()
    digest = sha256(payload).hexdigest()
    reference = ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="tenant-a",
        kind="checkpoint",
        sha256=digest,
        size_bytes=len(payload),
        media_type="application/json",
        storage_key=artifact_storage_key(
            owner_id=owner_id,
            tenant_id="tenant-a",
            digest=digest,
        ),
        created_by_operation_id=uuid.uuid4(),
        retention_class="checkpoint",
    )
    started = datetime.now(timezone.utc)
    events: list[str] = []

    class TrackingLocalStore(LocalDevelopmentArtifactObjectStore):
        async def delete(self, artifact: ArtifactRef) -> None:
            events.append("delete")
            await super().delete(artifact)

    class MemoryAuthority:
        def __init__(self) -> None:
            self.record = ArtifactLifecycleRecord(
                artifact=reference,
                state="provisional",
                ref_count=0,
                state_revision=1,
                updated_at=started,
                expires_at=started,
                delete_after=started,
            )

        async def compare_and_set(
            self,
            *,
            artifact_id,
            expected_state,
            expected_revision,
            next_state,
            ref_count_delta=0,
        ):
            current = self.record
            if (
                current.artifact.artifact_id != artifact_id
                or current.state != expected_state
                or current.state_revision != expected_revision
            ):
                return None
            events.append(f"cas:{current.state}->{next_state}")
            self.record = ArtifactLifecycleRecord(
                artifact=current.artifact,
                state=next_state,
                ref_count=current.ref_count + ref_count_delta,
                state_revision=current.state_revision + 1,
                updated_at=datetime.now(timezone.utc),
                expires_at=current.expires_at,
                delete_after=current.delete_after,
                last_gc_error=current.last_gc_error,
            )
            return self.record

        async def list_gc_candidates(self, *, older_than, limit):
            if (
                limit > 0
                and self.record.state == "tombstoned"
                and self.record.ref_count == 0
                and self.record.updated_at <= older_than
            ):
                return [self.record]
            return []

    async def scenario() -> None:
        store = TrackingLocalStore(tmp_path / "objects")
        await store.put_stream(reference, io.BytesIO(payload))
        authority = MemoryAuthority()
        disabled = ArtifactLifecycleService(
            authority=authority,
            object_store=store,
        )
        with pytest.raises(ArtifactStoreError, match="feature_disabled"):
            await disabled.transition(authority.record, "committed")

        lifecycle = ArtifactLifecycleService(
            authority=authority,
            object_store=store,
            enabled=True,
        )
        committed = await lifecycle.transition(authority.record, "committed")
        referenced = await lifecycle.change_ref_count(committed, delta=1)
        with pytest.raises(ArtifactStoreError, match="references_remain"):
            await lifecycle.transition(referenced, "tombstoned")
        released = await lifecycle.change_ref_count(referenced, delta=-1)
        tombstoned = await lifecycle.transition(released, "tombstoned")
        authority.record = replace(
            authority.record,
            delete_after=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        assert await lifecycle.collect_once(
            older_than=datetime.now(timezone.utc) + timedelta(seconds=1),
            limit=1,
        ) == 0
        authority.record = replace(authority.record, delete_after=started)
        events.clear()
        assert await lifecycle.collect_once(
            older_than=datetime.now(timezone.utc) + timedelta(seconds=1),
            limit=1,
        ) == 1
        assert events == [
            "cas:tombstoned->tombstoned",
            "delete",
            "cas:tombstoned->deleted",
        ]
        assert tombstoned.state == "tombstoned"
        assert authority.record.state == "deleted"
        assert await store.exists(reference) is False

    _run(scenario())


def test_durable_artifacts_are_owner_bound_and_export_is_cluster_single_flight(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        class TrackingObjectStore(LocalDevelopmentArtifactObjectStore):
            def __init__(self, root: Path) -> None:
                super().__init__(root)
                self.provisional_writes = 0
                self.provisional_commits = 0

            async def put_provisional_stream(self, provisional, source) -> None:
                self.provisional_writes += 1
                await super().put_provisional_stream(provisional, source)

            async def commit_provisional(self, provisional) -> None:
                self.provisional_commits += 1
                await super().commit_provisional(provisional)

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'artifact-test.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[PublicArtifact.__table__, PublicExportIdentity.__table__],
                )
            )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        object_store = TrackingObjectStore(tmp_path / "objects")
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
            assert object_store.provisional_writes == 2
            assert object_store.provisional_commits == 2
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
                scene_digest=sha256(b"scene").hexdigest(),
                semantic_digest=sha256(b"semantic").hexdigest(),
                quality_digest=sha256(b"quality").hexdigest(),
                exporter_digest=sha256(b"exporter").hexdigest(),
                export_mode="pptx",
                output_mode="fidelity-raster",
                font_manifest_digest=sha256(b"fonts").hexdigest(),
                exporter_version="exporter-1.0",
                render_adapter="chromium-raster-v2",
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

            assert await fail_export_identity(
                session,
                identity_id=first.identity_id,
                worker_id="export-worker-1",
                attempt_token=first.attempt_token,
                fencing_token=first.fencing_token,
                error_code="retryable_export_failure",
                now=started + timedelta(seconds=2),
            )
            await session.commit()
            source_row = await session.get(PublicArtifact, source.artifact_id)
            assert source_row is not None
            await session.refresh(source_row)
            assert source_row.refcount == 0

            recovered = await claim_export_identity(
                session,
                key=identity_key,
                operation_id=operation_two,
                worker_id="export-worker-2",
                lease_seconds=30,
                now=started + timedelta(seconds=3),
            )
            assert recovered.disposition == "claimed"
            assert recovered.fencing_token == first.fencing_token + 1
            assert recovered.attempt_token is not None
            await session.refresh(source_row)
            assert source_row.refcount == 1

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
            result_row = await session.get(PublicArtifact, result.artifact_id)
            assert result_row is not None
            await session.refresh(result_row)
            await session.refresh(source_row)
            assert source_row.refcount == 1
            assert result_row.refcount == 1
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
        "PPT_WORKER_SECCOMP_PROFILE": "public-worker-default.json",
        "PPT_WORKER_MEMORY_LIMIT_BYTES": "1073741824",
        "PPT_WORKER_CPU_MILLIS": "1000",
        "PPT_WORKER_PID_LIMIT": "64",
        "PPT_WORKER_OPEN_FILE_LIMIT": "256",
        "PPT_WORKER_OUTPUT_LIMIT_BYTES": "268435456",
        "PPT_WORKER_WALL_CLOCK_SECONDS": "600",
        "PPT_WORKER_REPLICAS": "3",
        "PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES": "536870912",
        "PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS": "3600",
    }
    validate_public_worker_environment(render, isolated_environment)
    with pytest.raises(RuntimeError, match="canonical_database_credential_forbidden"):
        validate_public_worker_environment(
            render,
            {**isolated_environment, "DATABASE_URL": "postgresql://not-used"},
        )
