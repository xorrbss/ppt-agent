from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import os
from pathlib import Path
import time
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
    ArtifactBindingError,
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
    configured_worker_artifact_cache,
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


def test_template_review_resolves_raw_artifact_digest_and_rejects_mismatch(
    tmp_path: Path, monkeypatch,
) -> None:
    from copy import deepcopy

    from PIL import Image
    from services.svg_templates import catalog
    from services.svg_templates.source_review import verify_source_review_assets

    monkeypatch.setenv("ENVIRONMENT", "test")
    owner = uuid.uuid4()
    monkeypatch.setattr(catalog, "principal", lambda: (owner, "tenant-review"))

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(PublicArtifact.__table__.create)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            service = DurableArtifactService(LocalDevelopmentArtifactObjectStore(tmp_path / "objects"))
            monkeypatch.setattr("services.artifact_store.configured_public_artifact_service", lambda: service)
            png = io.BytesIO()
            Image.new("RGB", (2, 2), "white").save(png, format="PNG")
            async with sessions() as session:
                ref = await service.persist_bytes(
                    session, owner_id=owner, tenant_id="tenant-review", kind="image-derivative",
                    payload=png.getvalue(), media_type="image/png", created_by_operation_id=uuid.uuid4(),
                    retention_class="published",
                )
                await session.commit()
                image_ref = {"artifact_id": str(ref.artifact_id), "sha256": ref.sha256}
                evidence = {"frames": [{
                    "source_image_ref": image_ref, "frame_image_ref": image_ref,
                    "source_image_digest": "sha256:" + ref.sha256,
                    "frame_image_digest": "sha256:" + ref.sha256,
                }]}
                await verify_source_review_assets(session, evidence)

                mismatched = deepcopy(evidence)
                mismatched["frames"][0]["source_image_digest"] = "sha256:" + "0" * 64
                with pytest.raises(ValueError, match="svg_template_review_image_evidence_missing"):
                    await verify_source_review_assets(session, mismatched)

                wrong_digest = deepcopy(mismatched)
                wrong_digest["frames"][0]["source_image_ref"]["sha256"] = "0" * 64
                with pytest.raises(ArtifactNotFound, match="artifact.ref_not_found"):
                    await verify_source_review_assets(session, wrong_digest)
        finally:
            await engine.dispose()

    _run(scenario())


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


def test_path_for_key_tolerates_extended_length_prefix_on_candidate_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolved candidate can gain the Windows ``\\\\?\\`` extended-length
    prefix that its already-resolved root lacks (e.g. once the OS opts into
    it for a deep/long path). That prefix difference alone must not be
    treated as an escape."""

    store = LocalDevelopmentArtifactObjectStore(tmp_path / "objects")
    owner_id = uuid.uuid4()
    digest = sha256(b"extended-length-candidate").hexdigest()
    key = artifact_storage_key(owner_id=owner_id, tenant_id="tenant-a", digest=digest)
    original_resolve = Path.resolve

    def resolve_with_extended_length_prefix(self: Path, *args, **kwargs) -> Path:
        resolved = original_resolve(self, *args, **kwargs)
        text = str(resolved)
        return resolved if text.startswith("\\\\?\\") else Path("\\\\?\\" + text)

    monkeypatch.setattr(Path, "resolve", resolve_with_extended_length_prefix)

    resolved_path = store._path_for_key(key)

    assert resolved_path == Path("\\\\?\\" + str(store._root / Path(*key.split("/"))))


def test_path_for_key_still_rejects_a_genuine_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extended-length prefix tolerance must not weaken the containment
    check: a storage key that genuinely climbs out of the root is still
    rejected, prefix mismatch or not."""

    store = LocalDevelopmentArtifactObjectStore(tmp_path / "objects")
    with pytest.raises(ArtifactBindingError, match="storage_key_escape"):
        store._path_for_key("../outside/secret.txt")

    original_resolve = Path.resolve

    def resolve_with_extended_length_prefix(self: Path, *args, **kwargs) -> Path:
        resolved = original_resolve(self, *args, **kwargs)
        text = str(resolved)
        return resolved if text.startswith("\\\\?\\") else Path("\\\\?\\" + text)

    monkeypatch.setattr(Path, "resolve", resolve_with_extended_length_prefix)
    with pytest.raises(ArtifactBindingError, match="storage_key_escape"):
        store._path_for_key("../outside/secret.txt")


def test_worker_cache_defaults_to_a_root_separate_from_the_authority_and_cannot_evict_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the original defect: with the local-development
    backend and no explicit ``PUBLIC_WORKER_ARTIFACT_CACHE_ROOT``, the worker
    transport cache used to default to the very same directory as the
    durable artifact authority (``PUBLIC_WORKER_OBJECT_STORE_ROOT``), so the
    cache's TTL/byte-budget eviction could delete a durable object. The
    cache must now get its own root, and running its eviction sweep must
    never remove an object the authority still owns."""

    authority_root = tmp_path / "local-artifact-authority"
    monkeypatch.setenv("PUBLIC_ARTIFACT_STORE_BACKEND", "local-development")
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("PUBLIC_WORKER_OBJECT_STORE_ROOT", str(authority_root))
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app_data"))
    monkeypatch.delenv("PUBLIC_WORKER_ARTIFACT_CACHE_ROOT", raising=False)
    # A tiny TTL so a maintenance sweep evicts anything not just written.
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", "1")
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES", str(1024 * 1024))

    authority_store = LocalDevelopmentArtifactObjectStore(authority_root)
    payload = b"durable-authority-object"
    owner_id = uuid.uuid4()
    digest = sha256(payload).hexdigest()
    reference = ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="tenant-a",
        kind="export-bundle",
        sha256=digest,
        size_bytes=len(payload),
        media_type="application/vnd.presenton.export-bundle+json",
        storage_key=artifact_storage_key(
            owner_id=owner_id, tenant_id="tenant-a", digest=digest
        ),
        created_by_operation_id=uuid.uuid4(),
        retention_class="ephemeral",
    )
    _run(authority_store.put_bytes(reference, payload))
    authority_object_path = authority_store._path(reference)

    worker_cache = configured_worker_artifact_cache()

    assert worker_cache._root.resolve() != authority_root.resolve()

    # Age the durable object well past the cache TTL and run the cache's own
    # eviction sweep: it must only ever look at its own root.
    old_time = time.time() - 3600
    os.utime(authority_object_path, (old_time, old_time))
    _run(worker_cache.maintain())

    assert authority_object_path.is_file()
    assert authority_object_path.read_bytes() == payload


def test_configured_worker_artifact_cache_rejects_a_root_shared_with_the_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even an explicit misconfiguration that points the cache at the
    authority root must fail closed instead of silently sharing the
    directory."""

    shared_root = tmp_path / "shared-root"
    monkeypatch.setenv("PUBLIC_ARTIFACT_STORE_BACKEND", "local-development")
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("PUBLIC_WORKER_OBJECT_STORE_ROOT", str(shared_root))
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_ROOT", str(shared_root))
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", "900")
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES", str(1024 * 1024))

    with pytest.raises(RuntimeError, match="worker_cache_root_must_differ_from_authority"):
        configured_worker_artifact_cache()


def test_feature_lifecycle_refcount_expiry_and_gc_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import nullcontext
    from types import SimpleNamespace

    from sqlalchemy.dialects import postgresql

    from services.artifact_lifecycle import SqlArtifactLifecycleAuthority
    from services.artifact_store import (
        ArtifactKeyBusy, require_artifact_key_guard, try_artifact_key_guard,
    )

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
            self.shared_key_protected = False
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

        async def claim_gc_candidate(self, record, *, older_than):
            if self.shared_key_protected:
                return None
            return await self.compare_and_set(
                artifact_id=record.artifact.artifact_id,
                expected_state="tombstoned",
                expected_revision=record.state_revision,
                next_state="tombstoned",
            )

    class GuardSession:
        """SQL-boundary double, not a PostgreSQL concurrency proof."""

        def __init__(self, *, dialect="postgresql", isolation="read committed", acquired=True):
            self.dialect = dialect
            self.isolation = isolation
            self.acquired = acquired
            self.no_autoflush = nullcontext()
            self.calls = []
            self.rows = []

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

        async def scalar(self, statement, params=None):
            sql = str(statement.compile(dialect=postgresql.dialect()))
            self.calls.append((sql, params))
            if "transaction_isolation" in sql:
                return self.isolation
            if "pg_try_advisory_xact_lock" in sql:
                return self.acquired
            return self.rows.pop(0)

        async def scalars(self, statement):
            self.calls.append((str(statement.compile(dialect=postgresql.dialect())), None))
            return SimpleNamespace(all=lambda: [])

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
        tombstoned = await lifecycle.transition(committed, "tombstoned")
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
        # A live alias or a competing key transaction prevents physical delete.
        authority.shared_key_protected = True
        assert await lifecycle.collect_once(
            older_than=datetime.now(timezone.utc) + timedelta(seconds=1), limit=1,
        ) == 0
        assert events == [] and await store.exists(reference)
        authority.shared_key_protected = False
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

        guard = GuardSession()
        await require_artifact_key_guard(guard, reference)
        alias = reference.model_copy(update={
            "artifact_id": uuid.uuid4(), "created_by_operation_id": uuid.uuid4(),
        })
        await require_artifact_key_guard(guard, alias)
        assert guard.calls[1][1] == guard.calls[3][1]
        assert all("pg_advisory_xact_lock(" not in sql for sql, _ in guard.calls)

        busy = GuardSession(acquired=False)
        with pytest.raises(ArtifactKeyBusy, match="artifact.cas_key_busy") as caught:
            # No metadata or object read may happen after a failed key claim.
            await DurableArtifactService(store).register_ref(busy, reference)
        assert caught.value.retryable and len(busy.calls) == 2

        stale_snapshot = GuardSession(isolation="repeatable read")
        assert not await try_artifact_key_guard(stale_snapshot, reference, for_physical_delete=True)
        assert len(stale_snapshot.calls) == 1
        monkeypatch.setenv("ENVIRONMENT", "test")
        sqlite = GuardSession(dialect="sqlite")
        await require_artifact_key_guard(sqlite, reference)
        assert not await try_artifact_key_guard(sqlite, reference, for_physical_delete=True)
        assert sqlite.calls == []
        monkeypatch.setenv("ENVIRONMENT", "production")
        with pytest.raises(ArtifactStoreError, match="sqlite_not_development"):
            await require_artifact_key_guard(sqlite, reference)

        # The SQL authority's post-guard snapshot includes all same-key aliases
        # and every pointer family, independent of alias operation/kind/ID.
        fresh = PublicArtifact(
            id=reference.artifact_id,
            **reference.model_dump(exclude={"artifact_id"}),
            lifecycle_state="tombstoned", lifecycle_revision=4, refcount=0,
            updated_at=started, expires_at=started,
        )
        record = ArtifactLifecycleRecord(
            artifact=reference, state="tombstoned", ref_count=0,
            state_revision=4, updated_at=started, expires_at=started,
        )
        sql_session = GuardSession()
        sql_session.rows = [fresh, alias.artifact_id]
        sql_authority = SqlArtifactLifecycleAuthority(sql_session)
        assert await sql_authority.claim_gc_candidate(record, older_than=started) is None
        assert "pg_try_advisory_xact_lock" in sql_session.calls[1][0]
        assert "FOR UPDATE SKIP LOCKED" in sql_session.calls[2][0]
        sibling_sql = sql_session.calls[3][0]
        for column in (
            "public_artifacts.storage_key", "public_artifacts.refcount",
            "public_artifacts.lifecycle_state", "public_artifacts.updated_at",
            "public_artifacts.expires_at", "public_artifact_publications.artifact_id",
            "public_slide_checkpoints.artifact_id", "public_export_identities.result_artifact_id",
            "public_export_identities.source_artifact_id", "workspace_artifacts.public_artifact_id",
            "workspace_artifacts.status",
        ):
            assert column in sibling_sql
        assert "created_by_operation_id =" not in sibling_sql and "kind =" not in sibling_sql

        sql_session.calls.clear()
        await sql_authority.list_gc_candidates(older_than=started, limit=1)
        await sql_authority.list_expired_candidates(expires_at_or_before=started, limit=1)
        assert len(sql_session.calls) == 2
        assert all("FOR UPDATE" not in sql for sql, _ in sql_session.calls)

        no_guard = SimpleNamespace(list_gc_candidates=authority.list_gc_candidates)
        authority.record = record
        with pytest.raises(ArtifactStoreError, match="gc_shared_key_authority_required"):
            await ArtifactLifecycleService(
                authority=no_guard, object_store=store, enabled=True,
            ).collect_once(older_than=started, limit=1)

        async def physical_delete_ordering(*, backend, delete_fails, cancel):
            from threading import Event

            # Exercise the real adapters' executor boundary, without filesystem
            # or S3 deletion. The guard is an explicit transaction-boundary double.
            loop = asyncio.get_running_loop()
            physical_started = asyncio.Event()
            physical_release = Event()
            physical_exists = True
            guard_held = True
            ordered = []
            delete_task = None

            def physical_delete(**_kwargs):
                nonlocal physical_exists
                ordered.append("physical_started")
                loop.call_soon_threadsafe(physical_started.set)
                if not physical_release.wait(timeout=5):
                    raise AssertionError("controlled delete was not released")
                if delete_fails:
                    ordered.append("physical_failed")
                    raise ArtifactStoreError("controlled_physical_delete_failed")
                physical_exists = False
                ordered.append("physical_finished")

            class ControlledLocal(LocalDevelopmentArtifactObjectStore):
                def _path(self, _reference):
                    return SimpleNamespace(unlink=physical_delete)

                async def delete(self, artifact):
                    nonlocal delete_task
                    delete_task = asyncio.current_task()
                    await super().delete(artifact)

            class ControlledS3(S3ArtifactObjectStore):
                async def delete(self, artifact):
                    nonlocal delete_task
                    delete_task = asyncio.current_task()
                    await super().delete(artifact)

            controlled = (
                ControlledLocal(tmp_path / "never-deleted")
                if backend == "local"
                else ControlledS3(
                    bucket="unit-test-only",
                    client=SimpleNamespace(delete_object=physical_delete),
                )
            )
            cancel_authority = MemoryAuthority()
            cancel_authority.record = record
            first_event = len(events)

            async def guarded_transaction():
                nonlocal guard_held
                try:
                    return await ArtifactLifecycleService(
                        authority=cancel_authority, object_store=controlled, enabled=True,
                    ).collect_once(older_than=started, limit=1)
                finally:
                    guard_held = False
                    ordered.append("guard_released")

            gc_task = asyncio.create_task(guarded_transaction())
            try:
                await asyncio.wait_for(physical_started.wait(), timeout=2)
                if cancel:
                    gc_task.cancel("original-gc-cancel")
                    await asyncio.sleep(0)
                    gc_task.cancel("repeated-gc-cancel")
                    # Loop shutdown cancels inner Tasks independently. The raw
                    # executor Future must still drain before either Task exits.
                    delete_task.cancel("inner-delete-cancel")
                    await asyncio.sleep(0)
                    delete_task.cancel("repeated-inner-delete-cancel")
                    await asyncio.sleep(0)
                    assert not gc_task.done() and not delete_task.done()
                assert guard_held and physical_exists
                with pytest.raises(ArtifactKeyBusy):
                    await require_artifact_key_guard(
                        GuardSession(acquired=not guard_held), alias,
                    )
                physical_release.set()
                if cancel:
                    with pytest.raises(asyncio.CancelledError) as cancelled:
                        await gc_task
                    assert cancelled.value.args == ("original-gc-cancel",)
                else:
                    with pytest.raises(ArtifactStoreError):
                        await gc_task
                assert cancel_authority.record.state == "tombstoned"
                assert "cas:tombstoned->deleted" not in events[first_event:]
                assert ordered == [
                    "physical_started",
                    "physical_failed" if delete_fails else "physical_finished",
                    "guard_released",
                ]
                await require_artifact_key_guard(
                    GuardSession(acquired=not guard_held), alias,
                )
                physical_exists = True
                ordered.append("new_alias_write")
                assert physical_exists and ordered[-2:] == ["guard_released", "new_alias_write"]
            finally:
                physical_release.set()
                if not gc_task.done():
                    try:
                        await gc_task
                    except BaseException:
                        pass

        for backend in ("local", "s3"):
            for delete_fails, cancel in ((False, True), (True, True), (True, False)):
                await physical_delete_ordering(
                    backend=backend, delete_fails=delete_fails, cancel=cancel,
                )

    _run(scenario())

    # Explicitly opt in only to the owned, empty local PostgreSQL race database.
    # The default unit case above is not evidence of cluster coordination.
    configured_url = os.getenv("PPT_AGENT_ARTIFACT_RACE_TEST_URL")
    if not configured_url:
        return

    async def postgresql_race() -> None:
        from threading import Event

        from sqlalchemy import select, text
        from sqlalchemy.engine import make_url
        from sqlalchemy.ext.asyncio import AsyncSession

        from models.sql.presentation import PresentationModel
        from models.sql.presentation_export_attempt import PresentationExportAttempt
        from models.sql.public_artifact import PublicArtifactPublication, PublicSlideCheckpoint
        from models.sql.user import User
        from models.sql.workspace import WorkspaceArtifact, WorkspaceConversation, WorkspaceOperation
        from services import workspace_artifact_publication_service as publication
        from utils.owner_context import reset_current_owner_id, set_current_owner_id

        parsed = make_url(configured_url)
        assert parsed.get_backend_name() == "postgresql"
        assert parsed.host == "127.0.0.1" and parsed.port == 21001
        assert parsed.database == "cas_guard_race_test"
        engine = create_async_engine(
            parsed.set(drivername="postgresql+asyncpg"),
            isolation_level="READ COMMITTED",
            connect_args={"server_settings": {
                "application_name": "cas-key-race-test",
                "statement_timeout": "5000", "lock_timeout": "1000",
            }},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        loop = asyncio.get_running_loop()
        physical_started = asyncio.Event()
        physical_release = Event()
        ordered = []
        deletion_task = None
        gc_task = None
        stage_task = None
        stage_release = asyncio.Event()
        backend_pids = set()
        store_root = tmp_path / "postgres-race-objects"
        plain_store = LocalDevelopmentArtifactObjectStore(store_root)

        class GatedLocalStore(LocalDevelopmentArtifactObjectStore):
            deleting = False

            def _path(self, artifact):
                path = super()._path(artifact)
                if not self.deleting:
                    return path

                def unlink(**kwargs):
                    ordered.append("physical_started")
                    loop.call_soon_threadsafe(physical_started.set)
                    if not physical_release.wait(timeout=10):
                        raise AssertionError("native delete gate was not released")
                    path.unlink(**kwargs)
                    ordered.append("physical_finished")

                return SimpleNamespace(unlink=unlink)

            async def delete(self, artifact):
                nonlocal deletion_task
                deletion_task = asyncio.current_task()
                self.deleting = True
                try:
                    await super().delete(artifact)
                finally:
                    self.deleting = False

        object_store = GatedLocalStore(store_root)
        service = DurableArtifactService(object_store)
        race_ref = reference.model_copy(update={
            "kind": "presentation-pptx", "retention_class": "published",
            "media_type": PPTX_MEDIA_TYPE,
        })
        contender_ref = race_ref.model_copy(update={
            "artifact_id": uuid.uuid4(), "created_by_operation_id": uuid.uuid4(),
        })
        owner_token = set_current_owner_id(owner_id)
        try:
            tables = [model.__table__ for model in (
                User, PresentationModel, WorkspaceConversation, WorkspaceOperation,
                PresentationExportAttempt, PublicArtifact, WorkspaceArtifact,
                PublicArtifactPublication, PublicSlideCheckpoint, PublicExportIdentity,
            )]
            async with engine.begin() as connection:
                assert await connection.scalar(text("SELECT current_setting('port')")) == "21001"
                assert await connection.scalar(text(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
                )) == 0, "race database must be empty; never reset a populated schema"
                await connection.run_sync(
                    lambda sync: SQLModel.metadata.create_all(sync, tables=tables)
                )
            await plain_store.put_stream(race_ref, io.BytesIO(payload))
            async with maker() as session, session.begin():
                await service.register_ref(session, race_ref)
                authority = SqlArtifactLifecycleAuthority(session)
                assert await authority.compare_and_set(
                    artifact_id=race_ref.artifact_id, expected_state="committed",
                    expected_revision=1, next_state="tombstoned",
                ) is not None
            cutoff = datetime.now(timezone.utc) + timedelta(seconds=30)
            async with maker() as session:
                stale_record, = await SqlArtifactLifecycleAuthority(session).list_gc_candidates(
                    older_than=cutoff, limit=1,
                )

            async def collecting_transaction():
                try:
                    async with maker() as session, session.begin():
                        backend_pids.add(await session.scalar(text("SELECT pg_backend_pid()")))
                        return await ArtifactLifecycleService(
                            authority=SqlArtifactLifecycleAuthority(session),
                            object_store=object_store, enabled=True,
                        ).collect_once(older_than=cutoff, limit=1)
                finally:
                    ordered.append("transaction_ended")

            gc_task = asyncio.create_task(collecting_transaction())
            await asyncio.wait_for(physical_started.wait(), timeout=5)
            gc_task.cancel("original-pg-gc-cancel")
            await asyncio.sleep(0)
            gc_task.cancel("repeated-pg-gc-cancel")
            assert deletion_task is not None
            deletion_task.cancel("inner-pg-delete-cancel")
            await asyncio.sleep(0)
            deletion_task.cancel("repeated-inner-pg-delete-cancel")
            await asyncio.sleep(0)
            assert not gc_task.done() and not deletion_task.done()
            async with maker() as contender:
                backend_pids.add(await contender.scalar(text("SELECT pg_backend_pid()")))
                with pytest.raises(ArtifactKeyBusy):
                    await service.register_ref(contender, contender_ref)
                await contender.rollback()
                with pytest.raises(ArtifactKeyBusy):
                    await service.persist_stream(
                        contender, owner_id=owner_id, tenant_id=race_ref.tenant_id,
                        kind=race_ref.kind, source=io.BytesIO(payload),
                        expected_digest=digest, size_bytes=len(payload),
                        media_type=PPTX_MEDIA_TYPE, retention_class="published",
                        created_by_operation_id=uuid.uuid4(),
                    )
                await contender.rollback()
            assert len(backend_pids) == 2
            assert await plain_store.read_bytes(race_ref) == payload
            assert ordered == ["physical_started"]
            physical_release.set()
            with pytest.raises(asyncio.CancelledError) as cancelled:
                await asyncio.wait_for(gc_task, timeout=5)
            assert cancelled.value.args == ("original-pg-gc-cancel",)
            assert ordered == ["physical_started", "physical_finished", "transaction_ended"]
            assert not await plain_store.exists(race_ref)
            async with maker() as session:
                with pytest.raises(ArtifactNotFound):
                    await service.register_ref(session, contender_ref)
                await session.rollback()
                fresh_ref = await service.persist_stream(
                    session, owner_id=owner_id, tenant_id=race_ref.tenant_id,
                    kind=race_ref.kind, source=io.BytesIO(payload),
                    expected_digest=digest, size_bytes=len(payload),
                    media_type=PPTX_MEDIA_TYPE, retention_class="published",
                    created_by_operation_id=uuid.uuid4(),
                )
                await session.commit()
            async with maker() as writer, writer.begin():
                consumer_ref = await service.register_consumer_ref(
                    writer, fresh_ref, created_by_operation_id=uuid.uuid4(),
                )
                async with maker() as contender:
                    assert await SqlArtifactLifecycleAuthority(contender).claim_gc_candidate(
                        stale_record, older_than=cutoff,
                    ) is None
            async with maker() as session, session.begin():
                session.add(User(id=owner_id, username=f"cas-race-{owner_id.hex}", hashed_password="test-only"))
                await session.flush()
                presentation = PresentationModel(content="CAS race", n_slides=1, language="en", revision=3)
                session.add(presentation)
                await session.flush()
                conversation = WorkspaceConversation(presentation_id=presentation.id)
                session.add(conversation)
                await session.flush()
                operation = WorkspaceOperation(
                    id=consumer_ref.created_by_operation_id,
                    conversation_id=conversation.id, presentation_id=presentation.id,
                    anchor_position=1, kind="presentation.export", state="running",
                    idempotency_key_hash="c" * 64, request_hash="d" * 64,
                    request_payload={}, expected_revision=3,
                )
                session.add(operation)
                await session.flush()
                attempt = PresentationExportAttempt(
                    presentation_id=presentation.id, workspace_operation_id=operation.id,
                    format="pptx", state="running", source_revision=3,
                    source_snapshot_sha256="e" * 64, request_hash="f" * 64,
                    idempotency_key_hash="a" * 64, requested_adapter="general",
                    provenance={}, started_at=datetime.now(timezone.utc),
                )
                session.add(attempt)
                await session.flush()
                presentation_id, attempt_id = presentation.id, attempt.id

            stage_started = asyncio.Event()

            class GatedIntentSession(AsyncSession):
                async def commit(self):
                    stage_started.set()
                    await asyncio.wait_for(stage_release.wait(), timeout=10)
                    await super().commit()

            with monkeypatch.context() as scoped_patch:
                scoped_patch.setattr(publication, "_new_independent_session", lambda session: GatedIntentSession(
                    bind=session.bind, expire_on_commit=False, autoflush=False,
                ))
                async with maker() as caller:
                    stage_task = asyncio.create_task(publication.prepare_workspace_export_artifact_ref(
                        caller, reference=consumer_ref,
                        operation_id=consumer_ref.created_by_operation_id,
                        presentation_id=presentation_id, source_revision=3,
                        kind="export.pptx", export_attempt_id=attempt_id,
                    ))
                    await asyncio.wait_for(stage_started.wait(), timeout=5)
                    async with maker() as contender:
                        # Staging holds the key shared: another same-byte holder
                        # proceeds, while physical delete stays excluded.
                        assert await service.register_ref(contender, contender_ref) == contender_ref
                        assert not await try_artifact_key_guard(
                            contender, contender_ref, for_physical_delete=True,
                        )
                        await contender.rollback()
                    stage_release.set()
                    prepared = await asyncio.wait_for(stage_task, timeout=5)
            async with maker() as session, session.begin():
                staged = await session.get(WorkspaceArtifact, prepared.artifact_id)
                assert staged.status == "staging" and staged.public_artifact_id == consumer_ref.artifact_id
                authority = SqlArtifactLifecycleAuthority(session)
                # Staging prevents tombstoning even when the refcount is zero.
                assert await authority.compare_and_set(
                    artifact_id=consumer_ref.artifact_id, expected_state="committed",
                    expected_revision=1, next_state="tombstoned",
                ) is None
                assert await authority.claim_gc_candidate(stale_record, older_than=cutoff) is None
                assert await ArtifactLifecycleService(
                    authority=authority, object_store=object_store, enabled=True,
                ).collect_once(older_than=cutoff, limit=10) == 0
                assert await session.scalar(select(PresentationModel.revision).where(
                    PresentationModel.id == presentation_id,
                )) == 3
            assert await plain_store.read_bytes(consumer_ref) == payload
            assert ordered.count("physical_started") == 1
            print("local PostgreSQL CAS race: two connections; repeated-cancel drain; busy register/persist; "
                  "fresh alias; staging guard/pointer; stale GC denied; bytes exact; external S3 not exercised")
        finally:
            physical_release.set()
            stage_release.set()
            for pending in (gc_task, stage_task):
                if pending is not None:
                    await asyncio.gather(pending, return_exceptions=True)
            reset_current_owner_id(owner_token)
            await engine.dispose()

    _run(postgresql_race())


def test_durable_artifacts_are_owner_bound_and_export_is_cluster_single_flight(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
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
                source_artifact_digest=source.sha256,
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
