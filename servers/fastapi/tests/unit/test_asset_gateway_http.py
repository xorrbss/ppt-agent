from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from api.v1.ppt.endpoints import asset_gateway
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
