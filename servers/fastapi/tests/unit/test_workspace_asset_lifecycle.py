from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from starlette.datastructures import Headers, UploadFile

from api.v1.ppt.endpoints.workspace import WORKSPACE_ROUTER, post_workspace_asset
from models.sql.presentation import PresentationModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from models.workspace import (
    WorkspaceAssetTransitionRequest,
    WorkspaceBootstrapRequest,
    WorkspaceOperationActionRequest,
)
from services.account_deletion_service import mark_account_deleting
from services.workspace_asset_service import (
    cleanup_expired_workspace_assets,
    create_workspace_asset,
    expire_orphan_workspace_assets,
    get_workspace_asset,
    require_workspace_asset_malware_scan_safety,
    resolve_workspace_asset_download,
    transition_workspace_asset,
    workspace_asset_response,
)
from services.workspace_asset_maintenance import (
    process_pending_workspace_assets,
    reconcile_timed_out_workspace_assets,
)
from services.workspace_operation_handler_registry import (
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import WorkspaceOperationRunner
from services.workspace_service import (
    WorkspaceServiceError,
    apply_workspace_action,
    bootstrap_workspace,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import (
    reset_current_owner_id,
    set_current_owner_id,
)

WORKSPACE_ASSET_TABLES = [
    User.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceMessage.__table__,
    WorkspaceAsset.__table__,
    WorkspaceMessageAttachment.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
    WorkspaceActionReceipt.__table__,
]


@contextmanager
def _owner(owner_id: uuid.UUID):
    token = set_current_owner_id(owner_id)
    try:
        yield
    finally:
        reset_current_owner_id(token)


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=WORKSPACE_ASSET_TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_owner_and_presentation(
    maker,
    *,
    owner_id: uuid.UUID,
    username: str,
) -> PresentationModel:
    async with maker() as session:
        session.add(
            User(
                id=owner_id,
                username=username,
                hashed_password="not-used",
            )
        )
        with _owner(owner_id):
            presentation = PresentationModel(
                content="asset lifecycle test",
                n_slides=1,
                language="en",
                layout={"name": "test"},
            )
            session.add(presentation)
            await session.commit()
            return presentation


def _pdf_upload(filename: str = r"C:\private\source\market.pdf") -> UploadFile:
    return UploadFile(
        file=BytesIO(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"),
        filename=filename,
        headers=Headers({"content-type": "application/pdf"}),
    )


def _bootstrap_request(asset_id: uuid.UUID, *, key: str) -> WorkspaceBootstrapRequest:
    return WorkspaceBootstrapRequest(
        idempotency_key=key,
        prompt="Build a strategy deck from the uploaded source",
        generation_settings={"language": "ko", "n_slides": 8},
        asset_ids=[asset_id],
        expected_revision=0,
    )


async def _drain_once(runner: WorkspaceOperationRunner) -> int:
    dispatched = await runner.dispatch_once()
    tasks = tuple(runner._active.values())
    if tasks:
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
    return dispatched


def test_async_upload_bootstrap_waits_for_durable_asset_runner_then_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    asyncio.run(
        _async_upload_bootstrap_waits_for_durable_asset_runner_then_completes(tmp_path, monkeypatch)
    )


async def _async_upload_bootstrap_waits_for_durable_asset_runner_then_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    engine, maker = await _database(tmp_path / "asset-upload-runner.db")
    owner_id = uuid.uuid4()
    try:
        presentation = await _seed_owner_and_presentation(
            maker,
            owner_id=owner_id,
            username="asset-upload-runner-owner",
        )
        scans: list[Path] = []

        def record_scan(path: Path) -> None:
            scans.append(path)

        with _owner(owner_id):
            monkeypatch.setattr(
                "api.v1.ppt.endpoints.workspace._require_workspace_writes",
                lambda _presentation_id: None,
            )
            monkeypatch.setattr(
                "services.workspace_asset_service.get_writable_path",
                lambda _name: str(tmp_path / "runner-assets"),
            )
            monkeypatch.setattr(
                "services.workspace_asset_service._scan_workspace_asset",
                record_scan,
            )
            async with maker() as session:
                upload_response = await post_workspace_asset(
                    file=_pdf_upload("runner-ready.pdf"),
                    conversation_id=None,
                    presentation_id=presentation.id,
                    session=session,
                )
                assert upload_response.status == "uploaded"
                asset = await session.get(WorkspaceAsset, upload_response.asset_id)
                assert asset is not None
                assert asset.state == "uploaded"
                assert asset.version == 0
                assert scans == []

            async with maker() as session:
                _, _, operation, duplicate = await bootstrap_workspace(
                    session,
                    presentation_id=presentation.id,
                    request=_bootstrap_request(asset.id, key="asset-upload-runner"),
                )
                assert duplicate is False
                assert operation.state == "waiting_for_assets"

        assert (
            await process_pending_workspace_assets(
                session_factory=maker,
                storage_root=tmp_path / "runner-assets",
            )
            == 1
        )
        with _owner(owner_id):
            async with maker() as session:
                processed = await session.get(WorkspaceAsset, asset.id)
                waiting = await session.get(WorkspaceOperation, operation.id)
                assert processed is not None and processed.state == "ready"
                # Bootstrap claims the asset before durable processing advances
                # its three lifecycle checkpoints.
                assert processed.version == 4
                assert len(scans) == 1
                assert waiting is not None and waiting.state == "queued"

        seen_asset_ids: list[str] = []

        async def generation_handler(context):
            seen_asset_ids.extend(context.claim.request_payload["asset_ids"])
            return WorkspaceOperationResult(payload={"status": "completed"})

        registry = WorkspaceOperationHandlerRegistry()
        registry.register("presentation.generate", generation_handler)
        runner = WorkspaceOperationRunner(
            session_factory=maker,
            registry=registry,
            max_concurrency=1,
            heartbeat_seconds=60,
        )
        assert await _drain_once(runner) == 1
        assert seen_asset_ids == [str(asset.id)]
        async with maker() as session:
            completed = await session.get(WorkspaceOperation, operation.id)
            assert completed is not None and completed.state == "succeeded"
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("scan_code", "expected_state"),
    [
        ("workspace_asset_malware_detected", "quarantined"),
        ("workspace_asset_malware_scan_failed", "failed"),
    ],
)
def test_synchronous_upload_persists_sanitized_scan_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scan_code: str,
    expected_state: str,
):
    asyncio.run(
        _synchronous_upload_persists_sanitized_scan_failure(
            tmp_path,
            monkeypatch,
            scan_code,
            expected_state,
        )
    )


async def _synchronous_upload_persists_sanitized_scan_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scan_code: str,
    expected_state: str,
):
    engine, maker = await _database(tmp_path / f"asset-{expected_state}.db")
    owner_id = uuid.uuid4()
    try:
        await _seed_owner_and_presentation(
            maker,
            owner_id=owner_id,
            username=f"asset-{expected_state}-owner",
        )

        def reject_scan(_path: Path) -> None:
            raise WorkspaceServiceError(
                scan_code,
                status_code=400 if expected_state == "quarantined" else 503,
                retryable=expected_state == "failed",
            )

        monkeypatch.setattr(
            "services.workspace_asset_service._scan_workspace_asset",
            reject_scan,
        )
        with _owner(owner_id):
            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as rejected:
                    await create_workspace_asset(
                        session,
                        upload=_pdf_upload(f"{expected_state}.pdf"),
                        storage_root=tmp_path / f"{expected_state}-assets",
                        process_immediately=True,
                    )
                assert rejected.value.code == scan_code

            async with maker() as session:
                asset = (await session.scalars(select(WorkspaceAsset))).one()
                assert asset.state == expected_state
                assert asset.failure_code == scan_code
                assert asset.failure_detail == {
                    "retryable": expected_state == "failed"
                }
                # No scanner output or exception detail is persisted.
                assert set(asset.failure_detail) == {"retryable"}
                assert asset.version == 2
    finally:
        await engine.dispose()


def test_bootstrap_rejects_forged_metadata_hash_owner_and_not_ready_asset(
    tmp_path: Path,
):
    asyncio.run(_bootstrap_rejects_forged_metadata_hash_owner_and_not_ready_asset(tmp_path))


async def _bootstrap_rejects_forged_metadata_hash_owner_and_not_ready_asset(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "asset-bootstrap-trust.db")
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()
    try:
        presentation_a = await _seed_owner_and_presentation(
            maker,
            owner_id=owner_a,
            username="asset-bootstrap-owner-a",
        )
        presentation_b = await _seed_owner_and_presentation(
            maker,
            owner_id=owner_b,
            username="asset-bootstrap-owner-b",
        )
        with _owner(owner_a):
            async with maker() as session:
                ready = await create_workspace_asset(
                    session,
                    upload=_pdf_upload("trusted.pdf"),
                    storage_root=tmp_path / "trust-assets",
                    process_immediately=True,
                )

            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as forged:
                    await bootstrap_workspace(
                        session,
                        presentation_id=presentation_a.id,
                        request=WorkspaceBootstrapRequest(
                            idempotency_key="forged-ready",
                            prompt="Trust this forged attachment",
                            attachments=[
                                {
                                    "file_name": "forged.pdf",
                                    "size_bytes": 1,
                                    "sha256": "f" * 64,
                                    "status": "ready",
                                }
                            ],
                        ),
                    )
                assert forged.value.code == "workspace_asset_id_required"

            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as hash_mismatch:
                    await bootstrap_workspace(
                        session,
                        presentation_id=presentation_a.id,
                        request=WorkspaceBootstrapRequest(
                            idempotency_key="hash-mismatch",
                            prompt="Use the wrong digest",
                            attachments=[
                                {
                                    "asset_id": ready.id,
                                    "file_name": "trusted.pdf",
                                    "size_bytes": ready.size_bytes,
                                    "sha256": "0" * 64,
                                    "status": "ready",
                                }
                            ],
                        ),
                    )
                assert hash_mismatch.value.code == "workspace_asset_hash_mismatch"

        with _owner(owner_b):
            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as hidden:
                    await bootstrap_workspace(
                        session,
                        presentation_id=presentation_b.id,
                        request=WorkspaceBootstrapRequest(
                            idempotency_key="cross-owner-asset",
                            prompt="Use another owner's upload",
                            asset_ids=[ready.id],
                        ),
                    )
                assert hidden.value.code == "workspace_asset_not_found"

            async with maker() as session:
                pending = await create_workspace_asset(
                    session,
                    upload=_pdf_upload("pending.pdf"),
                    storage_root=tmp_path / "pending-assets",
                )
            async with maker() as session:
                _, _, waiting, _ = await bootstrap_workspace(
                    session,
                    presentation_id=presentation_b.id,
                    request=_bootstrap_request(pending.id, key="asset-not-ready"),
                )
                assert waiting.state == "waiting_for_assets"
    finally:
        await engine.dispose()


def test_malware_scan_startup_policy_rejects_invalid_mode():
    with pytest.raises(
        RuntimeError,
        match="^workspace_asset_malware_scan_mode_invalid$",
    ):
        require_workspace_asset_malware_scan_safety(
            feature_enabled=True,
            environ={
                "TEMPLATE_V2_DEPLOYMENT_TIER": "development",
                "WORKSPACE_ASSET_MALWARE_SCAN_MODE": "optional",
            },
        )

    with pytest.raises(
        RuntimeError,
        match="^chat_first_deployment_tier_invalid$",
    ):
        require_workspace_asset_malware_scan_safety(
            feature_enabled=True,
            environ={"TEMPLATE_V2_DEPLOYMENT_TIER": "preview"},
        )


@pytest.mark.parametrize("tier", ["staging", "production"])
@pytest.mark.parametrize("mode", [None, "disabled"])
def test_managed_chat_first_requires_explicit_malware_scan(
    tier: str,
    mode: str | None,
):
    environ = {"TEMPLATE_V2_DEPLOYMENT_TIER": tier}
    if mode is not None:
        environ["WORKSPACE_ASSET_MALWARE_SCAN_MODE"] = mode

    with pytest.raises(
        RuntimeError,
        match="^chat_first_asset_malware_scan_required$",
    ):
        require_workspace_asset_malware_scan_safety(
            feature_enabled=True,
            environ=environ,
        )


def test_required_malware_scanner_must_be_executable():
    requested: list[str] = []

    def missing(executable: str) -> str | None:
        requested.append(executable)
        return None

    with pytest.raises(
        RuntimeError,
        match="^workspace_asset_malware_scanner_unavailable$",
    ):
        require_workspace_asset_malware_scan_safety(
            feature_enabled=True,
            environ={
                "TEMPLATE_V2_DEPLOYMENT_TIER": "production",
                "WORKSPACE_ASSET_MALWARE_SCAN_MODE": "required",
                "WORKSPACE_ASSET_MALWARE_SCANNER": "managed-clamscan",
            },
            which=missing,
        )

    assert requested == ["managed-clamscan"]


def test_required_available_scanner_and_local_disabled_mode_are_safe():
    require_workspace_asset_malware_scan_safety(
        feature_enabled=True,
        environ={
            "TEMPLATE_V2_DEPLOYMENT_TIER": "staging",
            "WORKSPACE_ASSET_MALWARE_SCAN_MODE": "required",
        },
        which=lambda executable: executable,
    )
    require_workspace_asset_malware_scan_safety(
        feature_enabled=True,
        environ={
            "TEMPLATE_V2_DEPLOYMENT_TIER": "development",
            "WORKSPACE_ASSET_MALWARE_SCAN_MODE": "disabled",
        },
        which=lambda _executable: None,
    )


def test_feature_off_bypasses_malware_scan_configuration_for_rollback():
    require_workspace_asset_malware_scan_safety(
        feature_enabled=False,
        environ={
            "TEMPLATE_V2_DEPLOYMENT_TIER": "invalid",
            "WORKSPACE_ASSET_MALWARE_SCAN_MODE": "invalid",
            "WORKSPACE_ASSET_MALWARE_SCANNER": "missing",
        },
        which=lambda _executable: None,
    )


def test_asset_routes_and_response_are_allowlisted():
    routes = {
        (route.path, frozenset(route.methods or set()))
        for route in WORKSPACE_ROUTER.routes
    }
    assert ("/workspace/assets", frozenset({"POST"})) in routes
    assert ("/workspace/assets/{asset_id}", frozenset({"GET"})) in routes
    # Lifecycle transitions are an internal worker/service boundary.  A normal
    # authenticated user must not be able to promote an upload to ``ready`` or
    # clear a quarantine through the public workspace router.
    assert not any(
        path == "/workspace/assets/{asset_id}/transitions" for path, _ in routes
    )


def test_upload_is_hashed_immutable_and_owner_scoped(tmp_path: Path):
    asyncio.run(_upload_is_hashed_immutable_and_owner_scoped(tmp_path))


async def _upload_is_hashed_immutable_and_owner_scoped(tmp_path: Path):
    engine, maker = await _database(tmp_path / "asset-upload.db")
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()
    try:
        await _seed_owner_and_presentation(
            maker,
            owner_id=owner_a,
            username="asset-owner-a",
        )
        async with maker() as session:
            session.add(
                User(
                    id=owner_b,
                    username="asset-owner-b",
                    hashed_password="not-used",
                )
            )
            await session.commit()

        storage_root = tmp_path / "workspace-assets"
        with _owner(owner_a):
            async with maker() as session:
                asset = await create_workspace_asset(
                    session,
                    upload=_pdf_upload(),
                    storage_root=storage_root,
                )
                assert asset.original_name == "market.pdf"
                assert asset.sha256 is not None and len(asset.sha256) == 64
                assert Path(asset.storage_key).name == f"{asset.sha256}.pdf"
                stored_path = storage_root / Path(asset.storage_key)
                assert stored_path.read_bytes().startswith(b"%PDF-")
                assert asset.expires_at is not None

                public = workspace_asset_response(asset).model_dump()
                assert "storage_key" not in public
                assert "owner_id" not in public
                assert public["file_name"] == "market.pdf"

                with pytest.raises(WorkspaceServiceError) as wrong_conversation:
                    await get_workspace_asset(
                        session,
                        asset_id=asset.id,
                        conversation_id=uuid.uuid4(),
                    )
                assert wrong_conversation.value.code == "workspace_asset_not_found"

        with _owner(owner_b):
            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as missing:
                    await get_workspace_asset(session, asset_id=asset.id)
                assert missing.value.code == "workspace_asset_not_found"
                assert missing.value.status_code == 404
    finally:
        await engine.dispose()


def test_upload_compensates_file_when_database_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    asyncio.run(
        _upload_compensates_file_when_database_commit_fails(tmp_path, monkeypatch)
    )


async def _upload_compensates_file_when_database_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    engine, maker = await _database(tmp_path / "asset-compensation.db")
    owner_id = uuid.uuid4()
    storage_root = tmp_path / "compensation-assets"
    try:
        await _seed_owner_and_presentation(
            maker,
            owner_id=owner_id,
            username="asset-compensation-owner",
        )
        with _owner(owner_id):
            async with maker() as session:
                monkeypatch.setattr(
                    session,
                    "commit",
                    AsyncMock(side_effect=RuntimeError("simulated commit failure")),
                )
                with pytest.raises(RuntimeError, match="simulated commit failure"):
                    await create_workspace_asset(
                        session,
                        upload=_pdf_upload(),
                        storage_root=storage_root,
                    )
            assert not any(path.is_file() for path in storage_root.rglob("*"))
            async with maker() as session:
                assert (
                    await session.scalar(
                        select(func.count()).select_from(WorkspaceAsset)
                    )
                    == 0
                )
    finally:
        await engine.dispose()


def test_upload_scan_is_fail_closed_and_office_mime_is_bound_to_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    asyncio.run(
        _upload_scan_is_fail_closed_and_office_mime_is_bound_to_extension(
            tmp_path,
            monkeypatch,
        )
    )


async def _upload_scan_is_fail_closed_and_office_mime_is_bound_to_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    engine, maker = await _database(tmp_path / "asset-upload-security.db")
    owner_id = uuid.uuid4()
    storage_root = tmp_path / "security-assets"
    try:
        await _seed_owner_and_presentation(
            maker,
            owner_id=owner_id,
            username="asset-security-owner",
        )
        with _owner(owner_id):
            monkeypatch.setenv("WORKSPACE_ASSET_MALWARE_SCAN_MODE", "required")
            monkeypatch.setattr(
                "services.workspace_asset_service.shutil.which",
                lambda _executable: None,
            )
            async with maker() as session:
                pending = await create_workspace_asset(
                    session,
                    upload=_pdf_upload(),
                    storage_root=storage_root,
                )
                assert pending.state == "uploaded"

            assert (
                await process_pending_workspace_assets(
                    session_factory=maker,
                    storage_root=storage_root,
                )
                == 1
            )
            async with maker() as session:
                failed = await session.get(WorkspaceAsset, pending.id)
                assert failed is not None and failed.state == "failed"
                assert failed.failure_code == "workspace_asset_malware_scanner_unavailable"
                assert failed.failure_detail == {"retryable": True}

            # The immutable source remains attached to the durable failed card
            # for an explicit reprocess/re-upload decision; it is not exposed
            # by the failed upload request itself.
            assert any(path.is_file() for path in storage_root.rglob("*"))
            monkeypatch.setenv("WORKSPACE_ASSET_MALWARE_SCAN_MODE", "disabled")
            mismatched_office_upload = UploadFile(
                file=BytesIO(b"PK\x03\x04placeholder"),
                filename="proposal.pptx",
                headers=Headers({"content-type": "application/msword"}),
            )
            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as mismatch:
                    await create_workspace_asset(
                        session,
                        upload=mismatched_office_upload,
                        storage_root=storage_root,
                    )
                assert mismatch.value.code == "workspace_asset_media_type_mismatch"
    finally:
        await engine.dispose()


def test_asset_wait_ready_projection_and_optimistic_transition(tmp_path: Path):
    asyncio.run(_asset_wait_ready_projection_and_optimistic_transition(tmp_path))


async def _asset_wait_ready_projection_and_optimistic_transition(tmp_path: Path):
    engine, maker = await _database(tmp_path / "asset-ready.db")
    owner_id = uuid.uuid4()
    try:
        presentation = await _seed_owner_and_presentation(
            maker,
            owner_id=owner_id,
            username="asset-ready-owner",
        )
        with _owner(owner_id):
            async with maker() as session:
                asset = await create_workspace_asset(
                    session,
                    upload=_pdf_upload("authoritative.pdf"),
                    storage_root=tmp_path / "ready-assets",
                )
            async with maker() as session:
                _, message, operation, duplicate = await bootstrap_workspace(
                    session,
                    presentation_id=presentation.id,
                    request=_bootstrap_request(asset.id, key="asset-ready"),
                )
                assert duplicate is False
                assert operation.state == "waiting_for_assets"
                claimed_version = (await session.get(WorkspaceAsset, asset.id)).version
                attachment = (
                    await session.scalars(
                        select(WorkspaceMessageAttachment).where(
                            WorkspaceMessageAttachment.message_id == message.id
                        )
                    )
                ).one()
                assert attachment.display_name == "authoritative.pdf"
                assert attachment.asset_state == "uploaded"

            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as invalid:
                    await transition_workspace_asset(
                        session,
                        asset_id=asset.id,
                        request=WorkspaceAssetTransitionRequest(
                            state="ready",
                            expected_version=claimed_version,
                        ),
                    )
                assert invalid.value.code == "workspace_asset_transition_invalid"

                processing = await transition_workspace_asset(
                    session,
                    asset_id=asset.id,
                    request=WorkspaceAssetTransitionRequest(
                        state="processing",
                        expected_version=claimed_version,
                    ),
                )

            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as stale:
                    await transition_workspace_asset(
                        session,
                        asset_id=asset.id,
                        request=WorkspaceAssetTransitionRequest(
                            state="ready",
                            expected_version=claimed_version,
                        ),
                    )
                assert stale.value.code == "workspace_asset_version_conflict"

                ready = await transition_workspace_asset(
                    session,
                    asset_id=asset.id,
                    request=WorkspaceAssetTransitionRequest(
                        state="ready",
                        expected_version=processing.version,
                    ),
                )
                assert ready.state == "ready"

            async with maker() as session:
                fresh_operation = await session.get(WorkspaceOperation, operation.id)
                assert fresh_operation is not None
                assert fresh_operation.state == "queued"
                attachment_state = await session.scalar(
                    select(WorkspaceMessageAttachment.asset_state).where(
                        WorkspaceMessageAttachment.asset_id == asset.id
                    )
                )
                # Attachments retain the immutable state captured when the
                # message was created; live asset state is projected separately.
                assert attachment_state == "uploaded"
                topics = list(
                    (
                        await session.scalars(
                            select(WorkspaceOutbox.topic).where(
                                WorkspaceOutbox.operation_id == operation.id
                            )
                        )
                    ).all()
                )
                assert "workspace.operation.ready" in topics
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("terminal_state", "expected_code"),
    [
        ("failed", "workspace_asset_reprocess_required"),
        ("quarantined", "workspace_asset_reattach_required"),
        ("expired", "workspace_asset_reattach_required"),
        ("deleted", "workspace_asset_reattach_required"),
    ],
)
def test_fatal_asset_blocks_operation_and_retry(
    tmp_path: Path,
    terminal_state: str,
    expected_code: str,
):
    asyncio.run(
        _fatal_asset_blocks_operation_and_retry(
            tmp_path,
            terminal_state=terminal_state,
            expected_code=expected_code,
        )
    )


async def _fatal_asset_blocks_operation_and_retry(
    tmp_path: Path,
    *,
    terminal_state: str,
    expected_code: str,
):
    engine, maker = await _database(tmp_path / f"asset-{terminal_state}.db")
    owner_id = uuid.uuid4()
    try:
        presentation = await _seed_owner_and_presentation(
            maker,
            owner_id=owner_id,
            username=f"asset-{terminal_state}-owner",
        )
        with _owner(owner_id):
            async with maker() as session:
                asset = await create_workspace_asset(
                    session,
                    upload=_pdf_upload(),
                    storage_root=tmp_path / f"{terminal_state}-assets",
                )
            async with maker() as session:
                _, _, operation, _ = await bootstrap_workspace(
                    session,
                    presentation_id=presentation.id,
                    request=_bootstrap_request(asset.id, key=terminal_state),
                )
                claimed = await session.get(WorkspaceAsset, asset.id)
                assert claimed is not None
                claimed_version = claimed.version

            async with maker() as session:
                transitioned = await transition_workspace_asset(
                    session,
                    asset_id=asset.id,
                    request=WorkspaceAssetTransitionRequest(
                        state=terminal_state,
                        expected_version=claimed_version,
                    ),
                )
                assert transitioned.state == terminal_state

            async with maker() as session:
                blocked = await session.get(WorkspaceOperation, operation.id)
                assert blocked is not None and blocked.state == "blocked"
                outbox_before = await session.scalar(
                    select(func.count()).select_from(WorkspaceOutbox)
                )
                with pytest.raises(WorkspaceServiceError) as retry_error:
                    await apply_workspace_action(
                        session,
                        operation_id=operation.id,
                        request=WorkspaceOperationActionRequest(
                            action="retry",
                            idempotency_key=f"retry-{terminal_state}",
                            expected_operation_version=blocked.version,
                            expected_revision=0,
                        ),
                    )
                assert retry_error.value.code == expected_code
                assert (
                    await session.scalar(
                        select(func.count()).select_from(WorkspaceActionReceipt)
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(WorkspaceOutbox)
                    )
                    == outbox_before
                )
    finally:
        await engine.dispose()


def test_ready_asset_restores_approval_gate_without_waking_runner(tmp_path: Path):
    asyncio.run(_ready_asset_restores_approval_gate_without_waking_runner(tmp_path))


async def _ready_asset_restores_approval_gate_without_waking_runner(tmp_path: Path):
    engine, maker = await _database(tmp_path / "asset-approval.db")
    owner_id = uuid.uuid4()
    try:
        presentation = await _seed_owner_and_presentation(
            maker,
            owner_id=owner_id,
            username="asset-approval-owner",
        )
        with _owner(owner_id):
            async with maker() as session:
                asset = await create_workspace_asset(
                    session,
                    upload=_pdf_upload(),
                    storage_root=tmp_path / "approval-assets",
                )
            async with maker() as session:
                _, _, operation, _ = await bootstrap_workspace(
                    session,
                    presentation_id=presentation.id,
                    request=_bootstrap_request(asset.id, key="approval-assets"),
                    initial_operation_state="awaiting_approval",
                )
                assert operation.state == "waiting_for_assets"
                claimed = await session.get(WorkspaceAsset, asset.id)
                assert claimed is not None
                claimed_version = claimed.version

            async with maker() as session:
                processing = await transition_workspace_asset(
                    session,
                    asset_id=asset.id,
                    request=WorkspaceAssetTransitionRequest(
                        state="processing",
                        expected_version=claimed_version,
                    ),
                )
            async with maker() as session:
                await transition_workspace_asset(
                    session,
                    asset_id=asset.id,
                    request=WorkspaceAssetTransitionRequest(
                        state="ready",
                        expected_version=processing.version,
                    ),
                )
            async with maker() as session:
                approval = await session.get(WorkspaceOperation, operation.id)
                assert approval is not None
                assert approval.state == "awaiting_approval"
                ready_wakeups = await session.scalar(
                    select(func.count())
                    .select_from(WorkspaceOutbox)
                    .where(
                        WorkspaceOutbox.operation_id == operation.id,
                        WorkspaceOutbox.topic == "workspace.operation.ready",
                    )
                )
                assert ready_wakeups == 0
    finally:
        await engine.dispose()


def test_bootstrap_flush_only_and_cross_owner_presentation_is_hidden(tmp_path: Path):
    asyncio.run(_bootstrap_flush_only_and_cross_owner_presentation_is_hidden(tmp_path))


async def _bootstrap_flush_only_and_cross_owner_presentation_is_hidden(tmp_path: Path):
    engine, maker = await _database(tmp_path / "asset-atomic.db")
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()
    try:
        presentation = await _seed_owner_and_presentation(
            maker,
            owner_id=owner_a,
            username="atomic-owner-a",
        )
        async with maker() as session:
            session.add(
                User(
                    id=owner_b,
                    username="atomic-owner-b",
                    hashed_password="not-used",
                )
            )
            await session.commit()

        with _owner(owner_a):
            async with maker() as session:
                result = await bootstrap_workspace(
                    session,
                    presentation_id=presentation.id,
                    request=WorkspaceBootstrapRequest(
                        idempotency_key="flush-only",
                        prompt="Create the draft",
                        generation_settings={},
                        expected_revision=0,
                    ),
                    commit=False,
                    initial_operation_state="awaiting_approval",
                )
                assert result[2].state == "awaiting_approval"
                assert (
                    await session.scalar(
                        select(func.count()).select_from(WorkspaceOperation)
                    )
                    == 1
                )
                await session.rollback()

            async with maker() as session:
                assert (
                    await session.scalar(
                        select(func.count()).select_from(WorkspaceOperation)
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(WorkspaceMessage)
                    )
                    == 0
                )

        with _owner(owner_b):
            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as hidden:
                    await bootstrap_workspace(
                        session,
                        presentation_id=presentation.id,
                        request=WorkspaceBootstrapRequest(
                            idempotency_key="cross-owner",
                            prompt="Try another owner's deck",
                            generation_settings={},
                        ),
                    )
                assert hidden.value.code == "presentation_not_found"
                assert hidden.value.status_code == 404
    finally:
        await engine.dispose()


def test_orphan_expiry_and_compensating_file_cleanup(tmp_path: Path):
    asyncio.run(_orphan_expiry_and_compensating_file_cleanup(tmp_path))


async def _orphan_expiry_and_compensating_file_cleanup(tmp_path: Path):
    engine, maker = await _database(tmp_path / "asset-cleanup.db")
    owner_id = uuid.uuid4()
    storage_root = tmp_path / "cleanup-assets"
    try:
        await _seed_owner_and_presentation(
            maker,
            owner_id=owner_id,
            username="cleanup-owner",
        )
        with _owner(owner_id):
            async with maker() as session:
                asset = await create_workspace_asset(
                    session,
                    upload=_pdf_upload(),
                    storage_root=storage_root,
                )
                stored_path = storage_root / Path(asset.storage_key)
                assert stored_path.exists()
                asset.expires_at = get_current_utc_datetime() - timedelta(seconds=1)
                await session.commit()

            async with maker() as session:
                assert await expire_orphan_workspace_assets(session) == 1
                expired = await session.get(WorkspaceAsset, asset.id)
                assert expired is not None and expired.state == "expired"
                assert stored_path.exists()

            async with maker() as session:
                assert (
                    await cleanup_expired_workspace_assets(
                        session,
                        storage_root=storage_root,
                    )
                    == 1
                )
                deleted = await session.get(WorkspaceAsset, asset.id)
                assert deleted is not None and deleted.state == "deleted"
                assert deleted.deleted_at is not None
                assert not stored_path.exists()
    finally:
        await engine.dispose()


def test_stale_orphan_expiry_cannot_overwrite_bootstrap_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    asyncio.run(
        _stale_orphan_expiry_cannot_overwrite_bootstrap_claim(tmp_path, monkeypatch)
    )


async def _stale_orphan_expiry_cannot_overwrite_bootstrap_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    engine, maker = await _database(tmp_path / "asset-expiry-claim-race.db")
    owner_id = uuid.uuid4()
    storage_root = tmp_path / "expiry-claim-assets"
    try:
        presentation = await _seed_owner_and_presentation(
            maker,
            owner_id=owner_id,
            username="asset-expiry-claim-owner",
        )
        with _owner(owner_id):
            async with maker() as session:
                asset = await create_workspace_asset(
                    session,
                    upload=_pdf_upload(),
                    storage_root=storage_root,
                )
                asset_id = asset.id
                asset.expires_at = get_current_utc_datetime() - timedelta(seconds=1)
                await session.commit()

            async with maker() as expiry_session:
                original_scalars = expiry_session.scalars
                claimed: dict[str, uuid.UUID] = {}

                class _BufferedScalars:
                    def __init__(self, rows):
                        self._rows = rows

                    def all(self):
                        return list(self._rows)

                async def select_then_claim(statement, *args, **kwargs):
                    result = await original_scalars(statement, *args, **kwargs)
                    rows = list(result.all())
                    # End the expiry reader transaction while preserving its stale
                    # candidate snapshot, then commit the competing bootstrap claim.
                    await expiry_session.commit()
                    async with maker() as bootstrap_session:
                        conversation, _, operation, duplicate = await bootstrap_workspace(
                            bootstrap_session,
                            presentation_id=presentation.id,
                            request=_bootstrap_request(asset_id, key="expiry-claim-race"),
                        )
                    assert duplicate is False
                    claimed["conversation_id"] = conversation.id
                    claimed["operation_id"] = operation.id
                    return _BufferedScalars(rows)

                monkeypatch.setattr(expiry_session, "scalars", select_then_claim)
                assert await expire_orphan_workspace_assets(expiry_session) == 0

            async with maker() as session:
                current = await session.get(WorkspaceAsset, asset_id)
                assert current is not None
                assert current.conversation_id == claimed["conversation_id"]
                assert current.state == "uploaded"
                assert current.expires_at is None
                assert current.version == 1

                attachment = (
                    await session.scalars(
                        select(WorkspaceMessageAttachment).where(
                            WorkspaceMessageAttachment.asset_id == asset_id
                        )
                    )
                ).one()
                assert attachment.asset_state == "uploaded"

                operation = await session.get(
                    WorkspaceOperation, claimed["operation_id"]
                )
                assert operation is not None
                assert operation.state == "waiting_for_assets"
    finally:
        await engine.dispose()


def test_attached_asset_timeout_projects_retryable_operation_error(tmp_path: Path):
    asyncio.run(_attached_asset_timeout_projects_retryable_operation_error(tmp_path))


async def _attached_asset_timeout_projects_retryable_operation_error(tmp_path: Path):
    engine, maker = await _database(tmp_path / "asset-timeout.db")
    owner_id = uuid.uuid4()
    now = get_current_utc_datetime()
    try:
        presentation = await _seed_owner_and_presentation(
            maker,
            owner_id=owner_id,
            username="asset-timeout-owner",
        )
        with _owner(owner_id):
            async with maker() as session:
                asset = await create_workspace_asset(
                    session,
                    upload=_pdf_upload("stalled.pdf"),
                    storage_root=tmp_path / "timeout-assets",
                )
            async with maker() as session:
                _, _, operation, _ = await bootstrap_workspace(
                    session,
                    presentation_id=presentation.id,
                    request=_bootstrap_request(asset.id, key="asset-timeout"),
                )
                stalled = await session.get(WorkspaceAsset, asset.id)
                assert stalled is not None
                stalled.updated_at = now - timedelta(minutes=10)
                await session.commit()

        assert (
            await reconcile_timed_out_workspace_assets(
                session_factory=maker,
                now=now,
                timeout_seconds=60,
            )
            == 1
        )

        with _owner(owner_id):
            async with maker() as session:
                failed = await session.get(WorkspaceAsset, asset.id)
                blocked = await session.get(WorkspaceOperation, operation.id)
                attachment_state = await session.scalar(
                    select(WorkspaceMessageAttachment.asset_state).where(
                        WorkspaceMessageAttachment.asset_id == asset.id
                    )
                )
                assert failed is not None
                assert failed.state == "failed"
                assert failed.failure_code == "asset_processing_timeout"
                assert failed.failure_detail == {"retryable": True}
                assert blocked is not None
                assert blocked.state == "blocked"
                assert blocked.error_code == "asset_processing_timeout"
                assert blocked.error_detail == {"retryable": True}
                # A later asset failure must not rewrite message history.
                assert attachment_state == "uploaded"

        # A stale discovery result cannot transition or emit a second card event.
        assert (
            await reconcile_timed_out_workspace_assets(
                session_factory=maker,
                now=now,
                timeout_seconds=60,
            )
            == 0
        )
    finally:
        await engine.dispose()


def test_account_deletion_marker_blocks_upload_and_new_command(
    tmp_path: Path,
):
    asyncio.run(_account_deletion_marker_blocks_upload_and_new_command(tmp_path))


async def _account_deletion_marker_blocks_upload_and_new_command(tmp_path: Path):
    engine, maker = await _database(tmp_path / "account-delete-admission.db")
    owner_id = uuid.uuid4()
    asset_root = tmp_path / "blocked-assets"
    try:
        presentation = await _seed_owner_and_presentation(
            maker,
            owner_id=owner_id,
            username="account-delete-admission-owner",
        )
        async with maker() as session:
            marker = await mark_account_deleting(session, user_id=owner_id)
            assert marker.generation == 1

        with _owner(owner_id):
            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as upload_rejected:
                    await create_workspace_asset(
                        session,
                        upload=_pdf_upload("blocked.pdf"),
                        storage_root=asset_root,
                    )
                assert (
                    upload_rejected.value.code
                    == "account_deletion_in_progress"
                )

            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as command_rejected:
                    await bootstrap_workspace(
                        session,
                        presentation_id=presentation.id,
                        request=WorkspaceBootstrapRequest(
                            idempotency_key="blocked-after-account-delete",
                            prompt="This command must not be admitted",
                            generation_settings={"language": "en", "n_slides": 4},
                            expected_revision=0,
                        ),
                    )
                assert (
                    command_rejected.value.code
                    == "account_deletion_in_progress"
                )

        assert not asset_root.exists()
        async with maker() as session:
            assert await session.scalar(select(func.count(WorkspaceAsset.id))) == 0
            assert await session.scalar(select(func.count(WorkspaceOperation.id))) == 0
    finally:
        await engine.dispose()


def test_asset_download_is_owner_scoped_and_rejects_unsafe_or_terminal_assets(
    tmp_path: Path,
):
    asyncio.run(
        _asset_download_is_owner_scoped_and_rejects_unsafe_or_terminal_assets(
            tmp_path
        )
    )


async def _asset_download_is_owner_scoped_and_rejects_unsafe_or_terminal_assets(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "asset-download.db")
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()
    storage_root = tmp_path / "download-assets"
    try:
        await _seed_owner_and_presentation(
            maker,
            owner_id=owner_a,
            username="asset-download-owner-a",
        )
        await _seed_owner_and_presentation(
            maker,
            owner_id=owner_b,
            username="asset-download-owner-b",
        )
        with _owner(owner_a):
            async with maker() as session:
                ready = await create_workspace_asset(
                    session,
                    upload=_pdf_upload("download.pdf"),
                    storage_root=storage_root,
                    process_immediately=True,
                )
                authorized, path = await resolve_workspace_asset_download(
                    session,
                    asset_id=ready.id,
                    storage_root=storage_root,
                )
                assert authorized.id == ready.id
                assert path.is_file()
                assert storage_root.resolve() in path.parents

        # Authorization is applied before state/path inspection and therefore
        # hides another owner's valid asset behind the same 404 contract.
        with _owner(owner_b):
            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as hidden:
                    await resolve_workspace_asset_download(
                        session,
                        asset_id=ready.id,
                        storage_root=storage_root,
                    )
                assert hidden.value.code == "workspace_asset_not_found"
                assert hidden.value.status_code == 404

        with _owner(owner_a):
            outside = tmp_path / "outside.pdf"
            outside.write_bytes(b"%PDF-1.4\n%%EOF\n")
            async with maker() as session:
                traversal = WorkspaceAsset(
                    state="ready",
                    original_name="outside.pdf",
                    media_type="application/pdf",
                    size_bytes=outside.stat().st_size,
                    storage_key="../outside.pdf",
                    sha256="a" * 64,
                    expires_at=get_current_utc_datetime() + timedelta(hours=1),
                )
                session.add(traversal)
                await session.commit()
                with pytest.raises(WorkspaceServiceError) as rejected:
                    await resolve_workspace_asset_download(
                        session,
                        asset_id=traversal.id,
                        storage_root=storage_root,
                    )
                assert rejected.value.code == "workspace_asset_not_found"
                assert rejected.value.status_code == 404

            expected_codes = {
                "quarantined": "workspace_asset_quarantined",
                "expired": "workspace_asset_expired",
                "deleted": "workspace_asset_deleted",
            }
            for state, expected_code in expected_codes.items():
                async with maker() as session:
                    terminal = WorkspaceAsset(
                        state=state,
                        original_name=f"{state}.pdf",
                        media_type="application/pdf",
                        size_bytes=1,
                        storage_key=f"terminal/{uuid.uuid4()}.pdf",
                        sha256="b" * 64,
                        expires_at=get_current_utc_datetime() + timedelta(hours=1),
                    )
                    session.add(terminal)
                    await session.commit()
                    with pytest.raises(WorkspaceServiceError) as unavailable:
                        await resolve_workspace_asset_download(
                            session,
                            asset_id=terminal.id,
                            storage_root=storage_root,
                        )
                    assert unavailable.value.code == expected_code
                    assert unavailable.value.status_code == 409

            async with maker() as session:
                elapsed = WorkspaceAsset(
                    state="ready",
                    original_name="elapsed.pdf",
                    media_type="application/pdf",
                    size_bytes=1,
                    storage_key=f"elapsed/{uuid.uuid4()}.pdf",
                    sha256="c" * 64,
                    expires_at=get_current_utc_datetime() - timedelta(seconds=1),
                )
                session.add(elapsed)
                await session.commit()
                with pytest.raises(WorkspaceServiceError) as unavailable:
                    await resolve_workspace_asset_download(
                        session,
                        asset_id=elapsed.id,
                        storage_root=storage_root,
                    )
                assert unavailable.value.code == "workspace_asset_expired"
    finally:
        await engine.dispose()
