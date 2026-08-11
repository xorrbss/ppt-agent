from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import uuid

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from models.sql.reference_snapshot import ReferenceSnapshot
from models.sql.user import User
from models.sql.workspace import WorkspaceAsset, WorkspaceConversation
from services.references.contracts import NormalizedReference, ReferenceProviderError
from services.references import service
from services.workspace_service import WorkspaceServiceError
from utils.owner_context import reset_current_owner_id, set_current_owner_id


def test_partial_legacy_import_failure_removes_promoted_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_source = tmp_path / "first.txt"
    second_source = tmp_path / "second.txt"
    first_source.write_text("first", encoding="utf-8")
    second_source.write_text("second", encoding="utf-8")
    promoted_dir = tmp_path / "canonical" / str(uuid.uuid4())
    promoted_dir.mkdir(parents=True)
    promoted_copy = promoted_dir / ("a" * 64 + ".txt")
    promoted_copy.write_text("first", encoding="utf-8")

    monkeypatch.setattr(
        service,
        "validate_document_paths",
        lambda _paths, **_kwargs: [str(first_source), str(second_source)],
    )
    calls = 0

    async def import_asset(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(id=uuid.uuid4()), promoted_copy
        raise WorkspaceServiceError("workspace_asset_scan_failed", status_code=422)

    monkeypatch.setattr(service, "import_workspace_document_asset", import_asset)
    session = SimpleNamespace(rollback=AsyncMock())

    with pytest.raises(service.ReferenceProviderError) as raised:
        asyncio.run(
            service.canonicalize_legacy_document_paths(
                session,
                file_paths=[str(first_source), str(second_source)],
            )
        )

    assert raised.value.code == "workspace_asset_scan_failed"
    session.rollback.assert_awaited_once()
    assert not promoted_copy.exists()
    assert not promoted_dir.exists()


def test_multi_file_import_admission_failure_rolls_back_rows_and_private_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'admission.db'}"
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: SQLModel.metadata.create_all(
                    sync,
                    tables=[
                        User.__table__,
                        WorkspaceConversation.__table__,
                        WorkspaceAsset.__table__,
                        ReferenceSnapshot.__table__,
                    ],
                )
            )
        owner_id = uuid.uuid4()
        source_a = tmp_path / "a.txt"
        source_b = tmp_path / "b.txt"
        source_a.write_text("alpha", encoding="utf-8")
        source_b.write_text("beta", encoding="utf-8")
        storage_root = tmp_path / "canonical"
        monkeypatch.setattr(
            service,
            "validate_document_paths",
            lambda _paths, **_kwargs: [str(source_a), str(source_b)],
        )
        monkeypatch.setattr(
            service,
            "admit_reference_snapshot",
            AsyncMock(
                side_effect=ReferenceProviderError(
                    "reference_asset_binding_stale",
                    status_code=409,
                )
            ),
        )
        try:
            async with maker() as session:
                session.add(
                    User(id=owner_id, username="reference-owner", hashed_password="x")
                )
                await session.commit()
            owner_token = set_current_owner_id(owner_id)
            try:
                async with maker() as session:
                    with pytest.raises(ReferenceProviderError) as raised:
                        await service.canonicalize_legacy_document_paths(
                            session,
                            file_paths=[str(source_a), str(source_b)],
                            storage_root=storage_root,
                        )
                    assert raised.value.code == "reference_asset_binding_stale"
                    assert list(await session.scalars(select(WorkspaceAsset))) == []
                    assert list(await session.scalars(select(ReferenceSnapshot))) == []
            finally:
                reset_current_owner_id(owner_token)
            assert not storage_root.exists() or not any(
                path.is_file() for path in storage_root.rglob("*")
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_snapshot_post_flush_admission_failure_is_not_committed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Provider:
        provider_id = "uploaded_document"

        async def normalize(self, *_args, **_kwargs):
            return NormalizedReference(
                provider_id=self.provider_id,
                source_revision="test:1",
                normalized_payload={"documents": []},
                asset_bindings=(),
            )

    class Registry:
        def require(self, provider_id: str):
            assert provider_id == "uploaded_document"
            return Provider()

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'flush.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: SQLModel.metadata.create_all(
                    sync,
                    tables=[ReferenceSnapshot.__table__],
                )
            )
        monkeypatch.setattr(
            service,
            "admit_reference_snapshot",
            AsyncMock(
                side_effect=ReferenceProviderError(
                    "reference_snapshot_integrity_failed",
                    status_code=409,
                )
            ),
        )
        try:
            async with maker() as session:
                with pytest.raises(ReferenceProviderError):
                    await service.create_reference_snapshot(
                        session,
                        provider_id="uploaded_document",
                        request={},
                        registry=Registry(),  # type: ignore[arg-type]
                    )
                assert list(await session.scalars(select(ReferenceSnapshot))) == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_snapshot_recovers_ambiguous_successful_commit(tmp_path: Path) -> None:
    class Provider:
        provider_id = "uploaded_document"

        async def normalize(self, *_args, **_kwargs):
            return NormalizedReference(
                provider_id=self.provider_id,
                source_revision="test:ambiguous",
                normalized_payload={"documents": []},
                asset_bindings=(),
            )

    class Registry:
        def require(self, provider_id: str):
            assert provider_id == "uploaded_document"
            return Provider()

    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'ambiguous.db'}"
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: SQLModel.metadata.create_all(
                    sync,
                    tables=[ReferenceSnapshot.__table__],
                )
            )
        try:
            async with maker() as session:
                original_commit = session.commit
                raised = False

                async def commit_then_lose_ack():
                    nonlocal raised
                    await original_commit()
                    if not raised:
                        raised = True
                        raise RuntimeError("lost commit acknowledgement")

                session.commit = commit_then_lose_ack  # type: ignore[method-assign]
                view = await service.create_reference_snapshot(
                    session,
                    provider_id="uploaded_document",
                    request={},
                    registry=Registry(),  # type: ignore[arg-type]
                )
                row = await session.get(ReferenceSnapshot, view.snapshot_id)
                assert row is not None
                assert row.content_sha256 == view.content_sha256
        finally:
            await engine.dispose()

    asyncio.run(scenario())
