from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import HTTPException, UploadFile
import pytest
from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from starlette.datastructures import Headers

from api.v1.ppt.endpoints import asset_gateway
from models.sql.asset_gateway_idempotency_receipt import (
    AssetGatewayIdempotencyReceipt,
)
from services.assets import AssetBinding, AssetKind
from services.references import ReferenceSnapshotView


def test_canonical_document_receipt_never_exposes_storage_path(monkeypatch) -> None:
    asset_id = uuid.uuid4()

    async def create(_session, **_kwargs):
        return SimpleNamespace(
            id=asset_id,
            state="ready",
            media_type="text/plain",
            size_bytes=4,
            sha256="a" * 64,
            version=3,
            conversation_id=None,
            expires_at=None,
            storage_key="private/asset.txt",
        )

    monkeypatch.setattr(asset_gateway, "create_workspace_asset", create)
    receipt = asyncio.run(
        asset_gateway.post_canonical_document_asset(
            file=object(),  # type: ignore[arg-type]
            session=object(),  # type: ignore[arg-type]
        )
    )
    dumped = receipt.model_dump(mode="json")
    assert dumped["asset_id"] == str(asset_id)
    assert "path" not in dumped
    assert "storage_key" not in dumped


def test_reference_snapshot_receipt_contains_only_immutable_ids(monkeypatch) -> None:
    snapshot_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    created = datetime.now(timezone.utc)
    binding = AssetBinding(
        asset_id=asset_id,
        kind=AssetKind.CONVERSATION_ATTACHMENT,
        media_type="text/plain",
        size_bytes=4,
        sha256="a" * 64,
        version=3,
        conversation_id=None,
    )

    async def capture(_session, **_kwargs):
        return ReferenceSnapshotView(
            snapshot_id=snapshot_id,
            provider_id="uploaded_document",
            source_revision="r1",
            source_fingerprint="b" * 64,
            normalized_payload_json='{"documents":[{"text":"secret body"}]}',
            asset_bindings=(binding,),
            content_sha256="c" * 64,
            conversation_id=None,
            created_at=created,
            expires_at=None,
        )

    monkeypatch.setattr(asset_gateway, "create_reference_snapshot", capture)
    receipt = asyncio.run(
        asset_gateway.post_reference_snapshot(
            asset_gateway.ReferenceSnapshotCreateRequest(
                provider_id="uploaded_document",
                source={"asset_ids": [str(asset_id)]},
            ),
            session=object(),  # type: ignore[arg-type]
        )
    )
    serialized = str(receipt.model_dump(mode="json"))
    assert str(snapshot_id) in serialized
    assert str(asset_id) in serialized
    assert "secret body" not in serialized
    assert "path" not in serialized


def _upload(content: bytes = b"body") -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename="source.txt",
        headers=Headers({"content-type": "text/plain"}),
    )


async def _receipt_database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: AssetGatewayIdempotencyReceipt.__table__.create(
                sync_connection,
                checkfirst=True,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_document_upload_idempotency_replays_across_sessions_and_rejects_conflict(
    monkeypatch, tmp_path: Path
) -> None:
    owner_id = uuid.uuid4()
    created: list[object] = []
    asset_id = uuid.uuid4()
    asset = SimpleNamespace(
        id=asset_id,
        state="ready",
        media_type="text/plain",
        size_bytes=4,
        sha256="a" * 64,
        version=3,
        conversation_id=None,
        expires_at=None,
    )

    async def create(_session, **_kwargs):
        created.append(object())
        return asset

    monkeypatch.setattr(asset_gateway, "get_current_owner_id", lambda: owner_id)
    monkeypatch.setattr(asset_gateway, "create_workspace_asset", create)

    async def scenario() -> None:
        engine, sessions = await _receipt_database(tmp_path / "upload-receipts.db")
        try:
            async with sessions() as session:
                first = await asset_gateway.post_canonical_document_asset(
                    file=_upload(),
                    idempotency_key="upload-1",
                    session=session,
                )
            async with sessions() as session:
                replay = await asset_gateway.post_canonical_document_asset(
                    file=_upload(),
                    idempotency_key="upload-1",
                    session=session,
                )
            assert replay == first
            assert len(created) == 1

            async with sessions() as session:
                with pytest.raises(HTTPException) as raised:
                    await asset_gateway.post_canonical_document_asset(
                        file=_upload(b"changed"),
                        idempotency_key="upload-1",
                        session=session,
                    )
            assert raised.value.status_code == 409
            assert raised.value.detail == "idempotency_key_payload_conflict"

            async with sessions() as session:
                row = await session.scalar(select(AssetGatewayIdempotencyReceipt))
                assert row is not None
                assert row.state == "complete"
                assert row.asset_id == asset_id
                assert "path" not in str(row.response_payload).lower()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_pending_receipt_fails_closed_without_restarting_work(
    monkeypatch, tmp_path: Path
) -> None:
    owner_id = uuid.uuid4()
    created = False

    async def create(_session, **_kwargs):
        nonlocal created
        created = True
        raise AssertionError("pending receipt must not restart the upload")

    monkeypatch.setattr(asset_gateway, "get_current_owner_id", lambda: owner_id)
    monkeypatch.setattr(asset_gateway, "create_workspace_asset", create)

    async def scenario() -> None:
        engine, sessions = await _receipt_database(tmp_path / "pending-receipt.db")
        try:
            upload = _upload()
            request_hash = await asset_gateway._upload_payload_sha256(
                upload,
                conversation_id=None,
            )
            scope = asset_gateway._idempotency_scope("upload-pending")
            assert scope is not None
            async with sessions() as session:
                session.add(
                    AssetGatewayIdempotencyReceipt(
                        owner_id=owner_id,
                        owner_scope=scope[0],
                        operation=asset_gateway._DOCUMENT_UPLOAD_OPERATION,
                        key_hash=scope[1],
                        request_sha256=request_hash,
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    )
                )
                await session.commit()
            async with sessions() as session:
                with pytest.raises(HTTPException) as raised:
                    await asset_gateway.post_canonical_document_asset(
                        file=_upload(),
                        idempotency_key="upload-pending",
                        session=session,
                    )
            assert raised.value.status_code == 409
            assert raised.value.detail == "idempotency_record_incomplete"
            assert created is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_receipt_insert_race_has_one_durable_winner(
    monkeypatch, tmp_path: Path
) -> None:
    owner_id = uuid.uuid4()
    owner_scope = str(owner_id)
    original_find = asset_gateway._find_idempotency_receipt
    initial_reads = 0
    both_initial_reads = asyncio.Event()
    monkeypatch.setattr(asset_gateway, "get_current_owner_id", lambda: owner_id)

    async def synchronized_find(session, **kwargs):
        nonlocal initial_reads
        row = await original_find(session, **kwargs)
        if row is None and not both_initial_reads.is_set():
            initial_reads += 1
            if initial_reads == 2:
                both_initial_reads.set()
            await both_initial_reads.wait()
        return row

    monkeypatch.setattr(
        asset_gateway,
        "_find_idempotency_receipt",
        synchronized_find,
    )

    async def scenario() -> None:
        engine, sessions = await _receipt_database(tmp_path / "receipt-race.db")
        try:
            async def reserve():
                async with sessions() as session:
                    return await asset_gateway._reserve_idempotency_receipt(
                        session,
                        owner_scope=owner_scope,
                        operation=asset_gateway._DOCUMENT_UPLOAD_OPERATION,
                        key_hash="d" * 64,
                        request_sha256="a" * 64,
                    )

            outcomes = await asyncio.gather(
                reserve(),
                reserve(),
                return_exceptions=True,
            )
            winners = [
                outcome
                for outcome in outcomes
                if isinstance(outcome, tuple) and outcome[0] is not None
            ]
            failures = [
                outcome for outcome in outcomes if isinstance(outcome, HTTPException)
            ]
            assert len(winners) == 1
            assert len(failures) == 1
            assert failures[0].detail == "idempotency_record_incomplete"

            async with sessions() as session:
                count = await session.scalar(
                    select(func.count()).select_from(
                        AssetGatewayIdempotencyReceipt
                    )
                )
                assert count == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_snapshot_idempotency_is_owner_scoped(monkeypatch, tmp_path: Path) -> None:
    owners = [uuid.uuid4(), uuid.uuid4()]
    active_owner = owners[0]
    created: list[ReferenceSnapshotView] = []
    asset_id = uuid.uuid4()

    async def capture(_session, **_kwargs):
        view = ReferenceSnapshotView(
            snapshot_id=uuid.uuid4(),
            provider_id="uploaded_document",
            source_revision="r1",
            source_fingerprint="b" * 64,
            normalized_payload_json="{}",
            asset_bindings=(
                AssetBinding(
                    asset_id=asset_id,
                    kind=AssetKind.CONVERSATION_ATTACHMENT,
                    media_type="text/plain",
                    size_bytes=4,
                    sha256="a" * 64,
                    version=3,
                    conversation_id=None,
                ),
            ),
            content_sha256="c" * 64,
            conversation_id=None,
            created_at=datetime.now(timezone.utc),
            expires_at=None,
        )
        created.append(view)
        return view

    monkeypatch.setattr(asset_gateway, "get_current_owner_id", lambda: active_owner)
    monkeypatch.setattr(asset_gateway, "create_reference_snapshot", capture)
    request = asset_gateway.ReferenceSnapshotCreateRequest(
        provider_id="uploaded_document",
        source={"asset_ids": [str(asset_id)]},
    )

    async def scenario() -> None:
        nonlocal active_owner
        engine, sessions = await _receipt_database(tmp_path / "snapshot-receipts.db")
        try:
            async with sessions() as session:
                first = await asset_gateway.post_reference_snapshot(
                    request,
                    idempotency_key="snapshot-1",
                    session=session,
                )
            async with sessions() as session:
                replay = await asset_gateway.post_reference_snapshot(
                    request,
                    idempotency_key="snapshot-1",
                    session=session,
                )
            assert replay == first
            assert len(created) == 1

            async with sessions() as session:
                with pytest.raises(HTTPException) as raised:
                    await asset_gateway.post_reference_snapshot(
                        request.model_copy(
                            update={
                                "source": {"asset_ids": [str(uuid.uuid4())]}
                            }
                        ),
                        idempotency_key="snapshot-1",
                        session=session,
                    )
            assert raised.value.status_code == 409
            assert raised.value.detail == "idempotency_key_payload_conflict"

            active_owner = owners[1]
            async with sessions() as session:
                second_owner = await asset_gateway.post_reference_snapshot(
                    request,
                    idempotency_key="snapshot-1",
                    session=session,
                )
            assert second_owner.snapshot_id != first.snapshot_id
            assert len(created) == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cleanup_expires_only_unclaimed_asset(monkeypatch) -> None:
    asset_id = uuid.uuid4()
    asset = SimpleNamespace(
        id=asset_id,
        state="ready",
        conversation_id=None,
        failure_code=None,
        failure_detail=None,
        expires_at=None,
        version=3,
        updated_at=None,
    )

    async def get(_session, **_kwargs):
        return asset

    async def delete_receipts(_session, **_kwargs):
        return None

    class Session:
        def add(self, _value):
            return None

        async def commit(self):
            return None

    monkeypatch.setattr(asset_gateway, "get_workspace_asset", get)
    monkeypatch.setattr(
        asset_gateway,
        "_delete_resource_idempotency_receipts",
        delete_receipts,
    )
    receipt = asyncio.run(
        asset_gateway.delete_unclaimed_document_asset(
            asset_id,
            session=Session(),  # type: ignore[arg-type]
        )
    )
    assert receipt.asset_ids == [asset_id]
    assert asset.state == "expired"
    assert asset.failure_code == "asset_admission_compensated"

    asset.state = "ready"
    asset.conversation_id = uuid.uuid4()
    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            asset_gateway.delete_unclaimed_document_asset(
                asset_id,
                session=Session(),  # type: ignore[arg-type]
            )
        )
    assert raised.value.detail == "asset_already_claimed"


def test_snapshot_cleanup_expires_assets_without_exposing_paths(monkeypatch) -> None:
    owner_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    asset = SimpleNamespace(
        id=asset_id,
        state="ready",
        conversation_id=None,
        failure_code=None,
        failure_detail=None,
        expires_at=None,
        version=3,
        updated_at=None,
    )
    row = SimpleNamespace(
        id=snapshot_id,
        conversation_id=None,
        asset_bindings=[{"assetId": str(asset_id), "path": "private/source.txt"}],
    )

    async def get(_session, **_kwargs):
        return asset

    async def delete_receipts(_session, **_kwargs):
        return None

    class Session:
        deleted = None

        async def scalar(self, _query):
            return row

        def add(self, _value):
            return None

        async def delete(self, value):
            self.deleted = value

        async def commit(self):
            return None

    session = Session()
    monkeypatch.setattr(asset_gateway, "get_current_owner_id", lambda: owner_id)
    monkeypatch.setattr(asset_gateway, "get_workspace_asset", get)
    monkeypatch.setattr(
        asset_gateway,
        "_delete_resource_idempotency_receipts",
        delete_receipts,
    )
    receipt = asyncio.run(
        asset_gateway.delete_unclaimed_reference_snapshot(
            snapshot_id,
            session=session,  # type: ignore[arg-type]
        )
    )
    dumped = receipt.model_dump(mode="json")
    assert dumped["snapshot_id"] == str(snapshot_id)
    assert dumped["asset_ids"] == [str(asset_id)]
    assert "path" not in str(dumped).lower()
    assert asset.state == "expired"
    assert session.deleted is row


def test_idempotency_receipt_cleanup_is_bounded_and_resource_scoped(
    monkeypatch, tmp_path: Path
) -> None:
    owner_id = uuid.uuid4()
    target_asset_id = uuid.uuid4()
    other_asset_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(asset_gateway, "get_current_owner_id", lambda: owner_id)

    async def scenario() -> None:
        engine, sessions = await _receipt_database(tmp_path / "cleanup-receipts.db")
        try:
            async with sessions() as session:
                for index in range(130):
                    session.add(
                        AssetGatewayIdempotencyReceipt(
                            owner_id=owner_id,
                            owner_scope=str(owner_id),
                            operation=asset_gateway._DOCUMENT_UPLOAD_OPERATION,
                            key_hash=f"{index:064x}",
                            request_sha256="a" * 64,
                            expires_at=now - timedelta(minutes=1),
                        )
                    )
                session.add(
                    AssetGatewayIdempotencyReceipt(
                        owner_id=owner_id,
                        owner_scope=str(owner_id),
                        operation=asset_gateway._DOCUMENT_UPLOAD_OPERATION,
                        key_hash="f" * 64,
                        request_sha256="b" * 64,
                        state="complete",
                        response_payload={"asset_id": str(target_asset_id)},
                        asset_id=target_asset_id,
                        expires_at=now + timedelta(hours=1),
                    )
                )
                session.add(
                    AssetGatewayIdempotencyReceipt(
                        owner_id=owner_id,
                        owner_scope=str(owner_id),
                        operation=asset_gateway._DOCUMENT_UPLOAD_OPERATION,
                        key_hash="e" * 64,
                        request_sha256="c" * 64,
                        state="complete",
                        response_payload={"asset_id": str(other_asset_id)},
                        asset_id=other_asset_id,
                        expires_at=now + timedelta(hours=1),
                    )
                )
                await session.commit()

            async with sessions() as session:
                removed = await asset_gateway._cleanup_expired_idempotency_receipts(
                    session,
                    owner_scope=str(owner_id),
                    now=now,
                    limit=10_000,
                )
                assert removed == asset_gateway._IDEMPOTENCY_CLEANUP_BATCH_SIZE

            async with sessions() as session:
                remaining = await session.scalar(
                    select(func.count())
                    .select_from(AssetGatewayIdempotencyReceipt)
                    .where(
                        AssetGatewayIdempotencyReceipt.owner_scope == str(owner_id)
                    )
                )
                assert remaining == 4
                await asset_gateway._delete_resource_idempotency_receipts(
                    session,
                    asset_ids=[target_asset_id],
                )
                await session.commit()

            async with sessions() as session:
                target = await session.scalar(
                    select(AssetGatewayIdempotencyReceipt).where(
                        AssetGatewayIdempotencyReceipt.asset_id == target_asset_id
                    )
                )
                other = await session.scalar(
                    select(AssetGatewayIdempotencyReceipt).where(
                        AssetGatewayIdempotencyReceipt.asset_id == other_asset_id
                    )
                )
                assert target is None
                assert other is not None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
