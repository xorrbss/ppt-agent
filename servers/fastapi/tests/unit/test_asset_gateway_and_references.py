from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import migrations

from models.sql.reference_snapshot import ReferenceSnapshot
from models.sql.presentation import PresentationModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_revision import TemplateV2Revision
from models.sql.user import User
from models.sql.workspace import WorkspaceAsset, WorkspaceConversation
from services.assets import (
    AssetGateway,
    AssetGatewayError,
    AssetKind,
    AssetPolicyRegistry,
    DEFAULT_ASSET_POLICY_REGISTRY,
)
from services.references import (
    CommunityReferenceProvider,
    ReferenceProviderError,
    ReferenceSourceProviderRegistry,
    TemplateReferenceProvider,
    UploadedDocumentProvider,
    admit_reference_snapshot,
    claim_reference_snapshots,
    cleanup_expired_reference_snapshots,
    create_reference_snapshot,
)
from services.references.contracts import NormalizedReference
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import reset_current_owner_id, set_current_owner_id


@contextmanager
def _owner(owner_id: uuid.UUID):
    token = set_current_owner_id(owner_id)
    try:
        yield
    finally:
        reset_current_owner_id(token)


async def _database(path: Path, *, template_tables: bool = False):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _configure(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    tables = [
        User.__table__,
        PresentationModel.__table__,
        WorkspaceConversation.__table__,
        WorkspaceAsset.__table__,
        ReferenceSnapshot.__table__,
    ]
    if template_tables:
        tables.extend([TemplateV2.__table__, TemplateV2Revision.__table__])
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=tables,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_owner(maker, owner_id: uuid.UUID) -> None:
    async with maker() as session:
        session.add(
            User(
                id=owner_id,
                username=f"asset-{owner_id.hex}",
                hashed_password="not-used",
            )
        )
        await session.commit()


async def _seed_asset(
    maker,
    *,
    root: Path,
    owner_id: uuid.UUID,
    content: bytes = b"%PDF-1.4\n%%EOF\n",
    media_type: str = "application/pdf",
    extension: str = ".pdf",
    state: str = "ready",
    expires_delta: timedelta | None = None,
    storage_key: str | None = None,
    recorded_size: int | None = None,
    conversation_id: uuid.UUID | None = None,
    created_delta: timedelta | None = None,
) -> WorkspaceAsset:
    asset_id = uuid.uuid4()
    digest = sha256(content).hexdigest()
    path = root / str(owner_id) / str(asset_id) / f"{digest}{extension}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    key = storage_key or path.relative_to(root).as_posix()
    with _owner(owner_id):
        async with maker() as session:
            asset = WorkspaceAsset(
                id=asset_id,
                conversation_id=conversation_id,
                state=state,
                original_name=f"reference{extension}",
                media_type=media_type,
                size_bytes=len(content) if recorded_size is None else recorded_size,
                storage_key=key,
                sha256=digest,
                version=3 if state == "ready" else 0,
                expires_at=(
                    get_current_utc_datetime() + expires_delta
                    if expires_delta is not None
                    else None
                ),
                created_at=(
                    get_current_utc_datetime() + created_delta
                    if created_delta is not None
                    else get_current_utc_datetime()
                ),
            )
            session.add(asset)
            await session.commit()
            return asset


async def _seed_conversation(maker, owner_id: uuid.UUID) -> WorkspaceConversation:
    with _owner(owner_id):
        async with maker() as session:
            presentation = PresentationModel(
                content="asset gateway conversation",
                n_slides=1,
                language="en",
                layout={"name": "test"},
            )
            session.add(presentation)
            await session.flush()
            conversation = WorkspaceConversation(presentation_id=presentation.id)
            session.add(conversation)
            await session.commit()
            return conversation


def _run(coroutine):
    return asyncio.run(coroutine)


def test_reference_snapshot_migration_upgrade_downgrade_reupgrade(tmp_path: Path):
    database_url = f"sqlite:///{(tmp_path / 'reference-migration.db').as_posix()}"
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[2] / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE "user" (id CHAR(32) PRIMARY KEY)')
            connection.exec_driver_sql(
                "CREATE TABLE workspace_conversations (id CHAR(32) PRIMARY KEY)"
            )
            # Later compatibility revisions require their real predecessor
            # tables even though this fixture is focused on reference snapshots.
            connection.exec_driver_sql(
                "CREATE TABLE workspace_assets (id CHAR(32) PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE presentations (id CHAR(32) PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE imageasset ("
                "id CHAR(32) PRIMARY KEY, path VARCHAR NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
            )
            connection.execute(
                sa.text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": "e6b8d0f2a4c6"},
            )

        command.upgrade(config, migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY)
        assert "reference_snapshots" in sa.inspect(engine).get_table_names()
        columns = {
            column["name"]
            for column in sa.inspect(engine).get_columns("reference_snapshots")
        }
        assert {"normalized_payload", "asset_bindings", "content_sha256"} <= columns
        presentation_columns = {
            column["name"]
            for column in sa.inspect(engine).get_columns("presentations")
        }
        assert "reference_snapshot_ids" in presentation_columns

        command.downgrade(config, "e6b8d0f2a4c6")
        assert "reference_snapshots" not in sa.inspect(engine).get_table_names()
        assert "reference_snapshot_ids" not in {
            column["name"]
            for column in sa.inspect(engine).get_columns("presentations")
        }
        command.upgrade(config, migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY)
        assert "reference_snapshots" in sa.inspect(engine).get_table_names()
        assert "reference_snapshot_ids" in {
            column["name"]
            for column in sa.inspect(engine).get_columns("presentations")
        }
    finally:
        engine.dispose()


def test_asset_gateway_accepts_ready_scanned_asset_without_exposing_path(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "valid.db")
        owner_id = uuid.uuid4()
        root = tmp_path / "assets"
        try:
            await _seed_owner(maker, owner_id)
            asset = await _seed_asset(maker, root=root, owner_id=owner_id)
            with _owner(owner_id):
                async with maker() as session:
                    binding = await AssetGateway(storage_root=root).bind(
                        session,
                        asset_id=asset.id,
                        kind=AssetKind.CONVERSATION_ATTACHMENT,
                    )
            assert binding.scan_state == "passed"
            assert binding.lifecycle_state == "ready"
            assert "path" not in binding.as_json()
            assert "storage" not in " ".join(binding.as_json()).lower()
        finally:
            await engine.dispose()

    _run(scenario())


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("traversal", "workspace_asset_not_found"),
        ("mime", "asset_media_type_disallowed"),
        ("magic", "asset_signature_mismatch"),
        ("oversize", "asset_too_large"),
        ("unscanned", "workspace_asset_not_ready"),
        ("expired", "workspace_asset_expired"),
        ("retention", "asset_retention_expired"),
    ],
)
def test_asset_gateway_security_matrix(tmp_path: Path, case: str, expected: str):
    async def scenario():
        engine, maker = await _database(tmp_path / f"{case}.db")
        owner_id = uuid.uuid4()
        root = tmp_path / f"{case}-assets"
        try:
            await _seed_owner(maker, owner_id)
            kwargs: dict[str, Any] = {}
            kind = AssetKind.CONVERSATION_ATTACHMENT
            if case == "traversal":
                outside = tmp_path / "outside.pdf"
                outside.write_bytes(b"%PDF-1.4\n%%EOF\n")
                kwargs["storage_key"] = "../outside.pdf"
            elif case == "mime":
                kwargs.update(
                    content=b"\x89PNG\r\n\x1a\nminimal",
                    media_type="application/pdf",
                    extension=".png",
                )
                kind = AssetKind.IMAGE
            elif case == "magic":
                kwargs.update(
                    content=b"not a png",
                    media_type="image/png",
                    extension=".png",
                )
                kind = AssetKind.IMAGE
            elif case == "unscanned":
                kwargs["state"] = "uploaded"
            elif case == "expired":
                kwargs["expires_delta"] = timedelta(seconds=-1)
            elif case == "retention":
                kwargs["created_delta"] = timedelta(days=-31)
            asset = await _seed_asset(
                maker,
                root=root,
                owner_id=owner_id,
                **kwargs,
            )

            gateway = AssetGateway(storage_root=root)
            if case == "oversize":
                policies = []
                for registered_kind in DEFAULT_ASSET_POLICY_REGISTRY.kinds:
                    policy = DEFAULT_ASSET_POLICY_REGISTRY.require(registered_kind)
                    policies.append(
                        replace(policy, max_bytes=1)
                        if registered_kind == AssetKind.CONVERSATION_ATTACHMENT
                        else policy
                    )
                gateway = AssetGateway(
                    AssetPolicyRegistry(policies),
                    storage_root=root,
                )
            with _owner(owner_id):
                async with maker() as session:
                    with pytest.raises(AssetGatewayError) as raised:
                        await gateway.bind(session, asset_id=asset.id, kind=kind)
            assert raised.value.code == expected
        finally:
            await engine.dispose()

    _run(scenario())


def test_asset_gateway_hides_owner_mismatch(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "owner.db")
        owner_a = uuid.uuid4()
        owner_b = uuid.uuid4()
        root = tmp_path / "owner-assets"
        try:
            await _seed_owner(maker, owner_a)
            await _seed_owner(maker, owner_b)
            asset = await _seed_asset(maker, root=root, owner_id=owner_a)
            with _owner(owner_b):
                async with maker() as session:
                    with pytest.raises(AssetGatewayError) as raised:
                        await AssetGateway(storage_root=root).bind(
                            session,
                            asset_id=asset.id,
                            kind=AssetKind.FILE,
                        )
            assert raised.value.code == "workspace_asset_not_found"
        finally:
            await engine.dispose()

    _run(scenario())


def test_asset_gateway_enforces_conversation_binding(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "conversation.db")
        owner_id = uuid.uuid4()
        root = tmp_path / "conversation-assets"
        try:
            await _seed_owner(maker, owner_id)
            conversation = await _seed_conversation(maker, owner_id)
            asset = await _seed_asset(
                maker,
                root=root,
                owner_id=owner_id,
                conversation_id=conversation.id,
            )
            with _owner(owner_id):
                async with maker() as session:
                    with pytest.raises(AssetGatewayError) as raised:
                        await AssetGateway(storage_root=root).bind(
                            session,
                            asset_id=asset.id,
                            kind=AssetKind.CONVERSATION_ATTACHMENT,
                            conversation_id=uuid.uuid4(),
                        )
            assert raised.value.code == "asset_conversation_mismatch"
        finally:
            await engine.dispose()

    _run(scenario())


def test_uploaded_provider_persists_and_admits_immutable_asset_snapshot(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "uploaded.db")
        owner_id = uuid.uuid4()
        root = tmp_path / "uploaded-assets"
        try:
            await _seed_owner(maker, owner_id)
            asset = await _seed_asset(
                maker,
                root=root,
                owner_id=owner_id,
                content=b"Immutable uploaded reference text",
                media_type="text/plain",
                extension=".txt",
            )
            registry = ReferenceSourceProviderRegistry((UploadedDocumentProvider(),))
            gateway = AssetGateway(storage_root=root)
            with _owner(owner_id):
                async with maker() as session:
                    view = await create_reference_snapshot(
                        session,
                        provider_id="uploaded_document",
                        request={"asset_ids": [str(asset.id)]},
                        registry=registry,
                        asset_gateway=gateway,
                    )
                    first_payload = view.payload()
                    assert first_payload["documents"][0]["text"] == (
                        "Immutable uploaded reference text"
                    )
                    first_payload["assetCount"] = 999
                    assert view.payload()["assetCount"] == 1
                    admitted = await admit_reference_snapshot(
                        session,
                        snapshot_id=view.snapshot_id,
                        asset_gateway=gateway,
                    )
                    assert admitted.asset_bindings == view.asset_bindings
                    row = await session.get(ReferenceSnapshot, view.snapshot_id)
                    serialized = str(row.normalized_payload) + str(row.asset_bindings)
                    assert str(root) not in serialized
                    assert "storage_key" not in serialized
        finally:
            await engine.dispose()

    _run(scenario())


def test_uploaded_provider_compacts_oversized_multi_document_snapshot(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "uploaded-compacted.db")
        owner_id = uuid.uuid4()
        root = tmp_path / "uploaded-compacted-assets"
        try:
            await _seed_owner(maker, owner_id)
            assets = [
                await _seed_asset(
                    maker,
                    root=root,
                    owner_id=owner_id,
                    content=(marker + ("x" * 199_998) + marker).encode(),
                    media_type="text/plain",
                    extension=".txt",
                )
                for marker in ("A", "B")
            ]
            with _owner(owner_id):
                async with maker() as session:
                    normalized = await UploadedDocumentProvider().normalize(
                        session,
                        request={"asset_ids": [str(asset.id) for asset in assets]},
                        asset_gateway=AssetGateway(storage_root=root),
                        conversation_id=None,
                    )
            documents = normalized.normalized_payload["documents"]
            assert sum(len(item["text"]) for item in documents) == 300_000
            assert [item["text"][0] for item in documents] == ["A", "B"]
            assert [item["text"][-1] for item in documents] == ["A", "B"]
            assert all(len(item["contentSha256"]) == 64 for item in documents)
        finally:
            await engine.dispose()

    _run(scenario())


def test_uploaded_provider_compacts_multibyte_text_to_generation_byte_budget(
    tmp_path: Path,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "uploaded-multibyte.db")
        owner_id = uuid.uuid4()
        root = tmp_path / "uploaded-multibyte-assets"
        try:
            await _seed_owner(maker, owner_id)
            assets = [
                await _seed_asset(
                    maker,
                    root=root,
                    owner_id=owner_id,
                    content=(marker + ("한" * 149_998) + marker).encode(),
                    media_type="text/plain",
                    extension=".txt",
                )
                for marker in ("A", "B")
            ]
            with _owner(owner_id):
                async with maker() as session:
                    normalized = await UploadedDocumentProvider().normalize(
                        session,
                        request={"asset_ids": [str(asset.id) for asset in assets]},
                        asset_gateway=AssetGateway(storage_root=root),
                        conversation_id=None,
                    )
            payload = normalized.normalized_payload
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            documents = payload["documents"]
            assert len(serialized) <= 480 * 1024
            assert [item["text"][0] for item in documents] == ["A", "B"]
            assert [item["text"][-1] for item in documents] == ["A", "B"]
        finally:
            await engine.dispose()

    _run(scenario())


def test_uploaded_provider_retries_transient_document_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    attempts = 0

    class FlakyLoader:
        def __init__(self, *_args, **_kwargs):
            self.documents: list[str] = []

        async def load_documents(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("transient converter failure")
            self.documents = ["Recovered uploaded reference"]

    monkeypatch.setattr("services.references.providers.DocumentsLoader", FlakyLoader)

    async def scenario():
        engine, maker = await _database(tmp_path / "uploaded-retry.db")
        owner_id = uuid.uuid4()
        root = tmp_path / "uploaded-retry-assets"
        try:
            await _seed_owner(maker, owner_id)
            asset = await _seed_asset(maker, root=root, owner_id=owner_id)
            with _owner(owner_id):
                async with maker() as session:
                    normalized = await UploadedDocumentProvider().normalize(
                        session,
                        request={"asset_ids": [str(asset.id)]},
                        asset_gateway=AssetGateway(storage_root=root),
                        conversation_id=None,
                    )
            assert attempts == 2
            assert normalized.normalized_payload["documents"][0]["text"] == (
                "Recovered uploaded reference"
            )
        finally:
            await engine.dispose()

    _run(scenario())


def test_uploaded_provider_reports_filename_after_extraction_retry_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    attempts = 0

    class FailingLoader:
        def __init__(self, *_args, **_kwargs):
            self.documents: list[str] = []

        async def load_documents(self):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("persistent converter failure")

    monkeypatch.setattr("services.references.providers.DocumentsLoader", FailingLoader)

    async def scenario():
        engine, maker = await _database(tmp_path / "uploaded-failure.db")
        owner_id = uuid.uuid4()
        root = tmp_path / "uploaded-failure-assets"
        try:
            await _seed_owner(maker, owner_id)
            asset = await _seed_asset(maker, root=root, owner_id=owner_id)
            with _owner(owner_id):
                async with maker() as session:
                    with pytest.raises(ReferenceProviderError) as raised:
                        await UploadedDocumentProvider().normalize(
                            session,
                            request={"asset_ids": [str(asset.id)]},
                            asset_gateway=AssetGateway(storage_root=root),
                            conversation_id=None,
                        )
            assert attempts == 2
            assert raised.value.code == "uploaded_reference_extraction_failed"
            assert raised.value.filename == "reference.pdf"
            assert raised.value.retryable is True
        finally:
            await engine.dispose()

    _run(scenario())


def test_snapshot_accepts_exact_workspace_claim_but_rejects_later_version_drift(
    tmp_path: Path,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "claim-transition.db")
        owner_id = uuid.uuid4()
        root = tmp_path / "claim-transition-assets"
        try:
            await _seed_owner(maker, owner_id)
            asset = await _seed_asset(
                maker,
                root=root,
                owner_id=owner_id,
                content=b"canonical source text",
                media_type="text/plain",
                extension=".txt",
            )
            conversation = await _seed_conversation(maker, owner_id)
            with _owner(owner_id):
                async with maker() as session:
                    snapshot = await create_reference_snapshot(
                        session,
                        provider_id="uploaded_document",
                        request={"asset_ids": [str(asset.id)]},
                        asset_gateway=AssetGateway(storage_root=root),
                    )

                async with maker() as session:
                    claimed = await session.get(WorkspaceAsset, asset.id)
                    claimed.conversation_id = conversation.id
                    claimed.version += 1
                    session.add(claimed)
                    await session.commit()
                    admitted = await admit_reference_snapshot(
                        session,
                        snapshot_id=snapshot.snapshot_id,
                        asset_gateway=AssetGateway(storage_root=root),
                    )
                    assert admitted.asset_bindings[0].conversation_id == conversation.id

                    claimed.version += 1
                    session.add(claimed)
                    await session.commit()
                    with pytest.raises(ReferenceProviderError) as stale:
                        await admit_reference_snapshot(
                            session,
                            snapshot_id=snapshot.snapshot_id,
                            asset_gateway=AssetGateway(storage_root=root),
                        )
                    assert stale.value.code == "reference_asset_binding_stale"
        finally:
            await engine.dispose()

    _run(scenario())


def test_snapshot_claim_links_conversation_cleanup_boundary(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "snapshot-cleanup.db")
        owner_id = uuid.uuid4()
        root = tmp_path / "snapshot-cleanup-assets"
        try:
            await _seed_owner(maker, owner_id)
            asset = await _seed_asset(
                maker,
                root=root,
                owner_id=owner_id,
                content=b"cleanup source",
                media_type="text/plain",
                extension=".txt",
            )
            conversation = await _seed_conversation(maker, owner_id)
            with _owner(owner_id):
                async with maker() as session:
                    snapshot = await create_reference_snapshot(
                        session,
                        provider_id="uploaded_document",
                        request={"asset_ids": [str(asset.id)]},
                        asset_gateway=AssetGateway(storage_root=root),
                    )
                    await claim_reference_snapshots(
                        session,
                        snapshot_ids=[snapshot.snapshot_id],
                        conversation_id=conversation.id,
                    )
                    await session.commit()

                    claimed = await session.get(ReferenceSnapshot, snapshot.snapshot_id)
                    assert claimed.conversation_id == conversation.id
                    assert claimed.expires_at is None
                    await session.delete(
                        await session.get(WorkspaceConversation, conversation.id)
                    )
                    await session.commit()

                async with maker() as session:
                    assert (
                        await session.get(ReferenceSnapshot, snapshot.snapshot_id)
                        is None
                    )
        finally:
            await engine.dispose()

    _run(scenario())


def test_expired_unclaimed_snapshot_payload_is_purged(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "snapshot-expiry.db")
        owner_id = uuid.uuid4()
        root = tmp_path / "snapshot-expiry-assets"
        try:
            await _seed_owner(maker, owner_id)
            asset = await _seed_asset(
                maker,
                root=root,
                owner_id=owner_id,
                content=b"orphaned normalized source text",
                media_type="text/plain",
                extension=".txt",
                expires_delta=timedelta(minutes=5),
            )
            with _owner(owner_id):
                async with maker() as session:
                    snapshot = await create_reference_snapshot(
                        session,
                        provider_id="uploaded_document",
                        request={"asset_ids": [str(asset.id)]},
                        asset_gateway=AssetGateway(storage_root=root),
                    )
                    row = await session.get(ReferenceSnapshot, snapshot.snapshot_id)
                    row.expires_at = get_current_utc_datetime() - timedelta(seconds=1)
                    session.add(row)
                    await session.commit()

                # Maintenance runs without a request owner context and must
                # still purge the owner-scoped orphan payload globally.
                async with maker() as session:
                    assert await cleanup_expired_reference_snapshots(session) == 1
                    assert (
                        await session.get(ReferenceSnapshot, snapshot.snapshot_id)
                        is None
                    )
        finally:
            await engine.dispose()

    _run(scenario())


def test_uploaded_provider_rejects_raw_client_paths_before_asset_admission():
    async def scenario():
        provider = UploadedDocumentProvider()
        with pytest.raises(ReferenceProviderError) as raised:
            await provider.normalize(
                None,  # type: ignore[arg-type]
                request={"asset_ids": [], "file_path": "C:/secret.pdf"},
                asset_gateway=AssetGateway(),
                conversation_id=None,
            )
        assert raised.value.code == "reference_request_shape_invalid"

    _run(scenario())


def test_community_provider_is_bounded_sanitized_and_drops_remote_font_urls():
    async def fetcher(community_id: int) -> dict[str, Any]:
        assert community_id == 7
        return {
            "title": "Reference\x00 deck",
            "slides": [
                '<script>alert(1)</script><div onclick="x" style="color:red;'
                'background:url(https://evil.test/x)"><img src="https://evil.test/x">'
                "Safe content</div>"
            ],
            "fonts": {"Inter": "https://evil.test/font.woff2"},
        }

    async def scenario():
        normalized = await CommunityReferenceProvider(fetcher=fetcher).normalize(
            None,  # type: ignore[arg-type]
            request={"community_ids": [7]},
            asset_gateway=AssetGateway(),
            conversation_id=None,
        )
        serialized = str(normalized.normalized_payload).lower()
        assert "script" not in serialized
        assert "onclick" not in serialized
        assert "evil.test" not in serialized
        assert "safe content" in serialized
        assert normalized.normalized_payload["references"][0]["fontFamilies"] == ["Inter"]

    _run(scenario())


def test_template_provider_requires_exact_owned_immutable_revision(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "template.db", template_tables=True)
        owner_a = uuid.uuid4()
        owner_b = uuid.uuid4()
        try:
            await _seed_owner(maker, owner_a)
            await _seed_owner(maker, owner_b)
            with _owner(owner_a):
                async with maker() as session:
                    session.add(
                        TemplateV2(
                            id="owned-template",
                            name="Owned",
                            revision=2,
                            layouts={"title": {"type": "group"}},
                        )
                    )
                    session.add(
                        TemplateV2Revision(
                            template_id="owned-template",
                            revision=2,
                            reason="update",
                            name="Owned",
                            layouts={"title": {"type": "group"}},
                            is_default=False,
                        )
                    )
                    await session.commit()

            provider = TemplateReferenceProvider()
            with _owner(owner_a):
                async with maker() as session:
                    normalized = await provider.normalize(
                        session,
                        request={"template_id": "owned-template", "revision": 2},
                        asset_gateway=AssetGateway(),
                        conversation_id=None,
                    )
                    assert normalized.source_revision == "owned-template:2"
                    with pytest.raises(ReferenceProviderError) as missing_revision:
                        await provider.normalize(
                            session,
                            request={"template_id": "owned-template", "revision": 1},
                            asset_gateway=AssetGateway(),
                            conversation_id=None,
                        )
                    assert missing_revision.value.code == "template_reference_revision_not_found"

            with _owner(owner_b):
                async with maker() as session:
                    with pytest.raises(ReferenceProviderError) as wrong_owner:
                        await provider.normalize(
                            session,
                            request={"template_id": "owned-template", "revision": 2},
                            asset_gateway=AssetGateway(),
                            conversation_id=None,
                        )
                    assert wrong_owner.value.code == "template_reference_revision_not_found"
        finally:
            await engine.dispose()

    _run(scenario())


def test_snapshot_service_rejects_provider_payload_with_storage_location(tmp_path: Path):
    class UnsafeProvider:
        provider_id = "uploaded_document"

        async def normalize(self, session, *, request, asset_gateway, conversation_id):
            return NormalizedReference(
                provider_id=self.provider_id,
                source_revision="unsafe",
                normalized_payload={"storage_path": "C:/secret.pdf"},
            )

    async def scenario():
        engine, maker = await _database(tmp_path / "unsafe.db")
        owner_id = uuid.uuid4()
        try:
            await _seed_owner(maker, owner_id)
            with _owner(owner_id):
                async with maker() as session:
                    with pytest.raises(ReferenceProviderError) as raised:
                        await create_reference_snapshot(
                            session,
                            provider_id="uploaded_document",
                            request={},
                            registry=ReferenceSourceProviderRegistry((UnsafeProvider(),)),
                        )
                    assert raised.value.code == "reference_storage_location_forbidden"
        finally:
            await engine.dispose()

    _run(scenario())
