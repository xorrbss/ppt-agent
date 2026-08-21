from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.presentation_and_path import PresentationAndPath
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.slide import SlideModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceOperation,
)
from services.presentation_export_attempt_service import (
    begin_export_attempt,
    build_export_source_snapshot_sha256,
    terminalize_export_attempt_failed,
    terminalize_export_attempt_succeeded,
)
from services.presentation_export_adapters import PresentationExportAdapterResult
from services.presentation_export_capabilities import (
    PresentationExportContractError,
    PresentationExportFreshnessError,
    analyze_presentation_export_capabilities,
)
from services.presentation_export_router import (
    PresentationExportExecution,
    PresentationExportRouteResult,
    terminalize_standalone_presentation_export,
)


TABLES = [
    User.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceOperation.__table__,
    WorkspaceAsset.__table__,
    PresentationExportAttempt.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _presentation(*, presentation_id: uuid.UUID | None = None) -> PresentationModel:
    return PresentationModel(
        id=presentation_id or uuid.uuid4(),
        content="semantic deck",
        n_slides=1,
        language="en",
        revision=4,
        title="Stable",
    )


def _slide(presentation_id: uuid.UUID, *, content: dict | None = None) -> SlideModel:
    return SlideModel(
        id=uuid.uuid4(),
        presentation=presentation_id,
        layout_group="general",
        layout="title",
        index=0,
        content=content or {"title": "Same content"},
        speaker_note="Same note",
    )


def _provenance(
    *,
    source_revision: int = 4,
    source_snapshot_sha256: str = "c" * 64,
    asset_digests: dict[str, str] | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "adapter_chain": ["general"],
        "capability_evidence": {"selected_adapter": "general"},
        "fallback_reason": None,
        "asset_digests": asset_digests or {},
        "compiler_runtime": {
            "compiler": "presentation-export",
            "compiler_version": "0.4.3",
            "runtime": "chromium-dom-export",
            "runtime_version": "presenton.render-artifact/v1",
        },
        "freshness": {
            "source_revision": source_revision,
            "source_snapshot_sha256": source_snapshot_sha256,
            "package_sha256": None,
            "asset_digests_match": True,
            "package_digest_match": True,
        },
    }


def test_source_snapshot_ignores_staging_and_slide_uuids_but_tracks_content() -> None:
    durable_id = uuid.uuid4()
    first_presentation = _presentation()
    second_presentation = _presentation()
    first = build_export_source_snapshot_sha256(
        first_presentation,
        [_slide(first_presentation.id)],
        durable_presentation_id=durable_id,
    )
    retry = build_export_source_snapshot_sha256(
        second_presentation,
        [_slide(second_presentation.id)],
        durable_presentation_id=durable_id,
    )
    changed = build_export_source_snapshot_sha256(
        second_presentation,
        [_slide(second_presentation.id, content={"title": "Changed"})],
        durable_presentation_id=durable_id,
    )

    assert first == retry
    assert changed != first


def test_general_analysis_collects_persisted_assets_and_rejects_missing_digest() -> None:
    asset_id = str(uuid.uuid4())
    analysis = analyze_presentation_export_capabilities(
        export_format="pptx",
        persisted_slide_payloads=(
            {"content": {"image": {"assetId": asset_id, "sha256": "a" * 64}}},
        ),
    )

    assert analysis.candidate_chain == ("general",)
    assert analysis.asset_digests == {asset_id: "a" * 64}
    with pytest.raises(
        PresentationExportFreshnessError,
        match="presentation_export_asset_digest_required",
    ):
        analyze_presentation_export_capabilities(
            export_format="pptx",
            persisted_slide_payloads=(
                {"content": {"image": {"asset_id": asset_id}}},
            ),
        )


async def _seed_attempt_presentation(maker) -> uuid.UUID:
    async with maker() as session, session.begin():
        presentation = _presentation()
        session.add(presentation)
        await session.flush()
        return presentation.id


async def _begin(
    maker,
    presentation_id: uuid.UUID,
    *,
    request_hash: str = "d" * 64,
) -> PresentationExportAttempt:
    async with maker() as session:
        return await begin_export_attempt(
            session,
            presentation_id=presentation_id,
            owner_id=None,
            owner_scope="anonymous",
            workspace_operation_id=None,
            export_format="pptx",
            source_revision=4,
            source_snapshot_sha256="c" * 64,
            request_hash=request_hash,
            idempotency_key_hash="e" * 64,
            requested_adapter="auto",
            provenance=_provenance(),
        )


def test_concurrent_begin_reuses_one_identity_and_rejects_conflicting_payload(
    tmp_path: Path,
) -> None:
    async def scenario():
        engine, maker = await _database(tmp_path / "attempt-race.db")
        try:
            presentation_id = await _seed_attempt_presentation(maker)
            first, second = await asyncio.gather(
                _begin(maker, presentation_id),
                _begin(maker, presentation_id),
            )
            assert first.id == second.id
            async with maker() as session:
                assert await session.scalar(
                    select(func.count()).select_from(PresentationExportAttempt)
                ) == 1

            conflict = await asyncio.gather(
                _begin(maker, presentation_id),
                _begin(maker, presentation_id, request_hash="f" * 64),
                return_exceptions=True,
            )
            assert any(
                isinstance(result, PresentationExportContractError)
                and result.code == "presentation_export_idempotency_conflict"
                for result in conflict
            )
            async with maker() as session:
                assert await session.scalar(
                    select(func.count()).select_from(PresentationExportAttempt)
                ) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_terminalization_is_owner_operation_scoped_and_asset_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario():
        engine, maker = await _database(tmp_path / "attempt-freshness.db")
        app_data = tmp_path / "app-data"
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id = await _seed_attempt_presentation(maker)
            original = b"immutable-asset"
            digest = sha256(original).hexdigest()
            asset_id = uuid.uuid4()
            storage_key = f"anonymous/{asset_id}/{digest}.bin"
            asset_path = app_data / "workspace_assets" / storage_key
            asset_path.parent.mkdir(parents=True)
            asset_path.write_bytes(original)
            async with maker() as session, session.begin():
                session.add(
                    WorkspaceAsset(
                        id=asset_id,
                        state="ready",
                        original_name="asset.bin",
                        media_type="application/octet-stream",
                        size_bytes=len(original),
                        storage_key=storage_key,
                        sha256=digest,
                    )
                )
            attempt = await _begin(maker, presentation_id)
            provenance = _provenance(asset_digests={str(asset_id): digest})

            async with maker() as session:
                with pytest.raises(
                    PresentationExportContractError,
                    match="presentation_export_attempt_terminal_conflict",
                ):
                    await terminalize_export_attempt_succeeded(
                        session,
                        attempt_id=attempt.id,
                        presentation_id=presentation_id,
                        expected_owner_id=uuid.uuid4(),
                        expected_owner_scope="anonymous",
                        expected_workspace_operation_id=None,
                        source_revision=4,
                        source_snapshot_sha256="c" * 64,
                        selected_adapter="general",
                        artifact_sha256="a" * 64,
                        artifact_size_bytes=5,
                        provenance=provenance,
                    )
                await session.rollback()

            async with maker() as session:
                with pytest.raises(
                    PresentationExportContractError,
                    match="presentation_export_attempt_terminal_conflict",
                ):
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=attempt.id,
                        expected_owner_id=None,
                        expected_owner_scope="anonymous",
                        expected_workspace_operation_id=uuid.uuid4(),
                        failure_code="wrong_operation",
                        failure_path=None,
                    )

            asset_path.write_bytes(b"modified-asset!")
            async with maker() as session:
                with pytest.raises(
                    PresentationExportFreshnessError,
                    match="presentation_export_asset_digest_mismatch",
                ):
                    await terminalize_export_attempt_succeeded(
                        session,
                        attempt_id=attempt.id,
                        presentation_id=presentation_id,
                        expected_owner_id=None,
                        expected_owner_scope="anonymous",
                        expected_workspace_operation_id=None,
                        source_revision=4,
                        source_snapshot_sha256="c" * 64,
                        selected_adapter="general",
                        artifact_sha256="a" * 64,
                        artifact_size_bytes=5,
                        provenance=provenance,
                    )
                await session.rollback()
            async with maker() as session:
                stored = await session.get(PresentationExportAttempt, attempt.id)
                assert stored is not None and stored.state == "running"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_standalone_terminalization_rejects_concurrently_overwritten_artifact(
    tmp_path: Path,
) -> None:
    async def scenario():
        engine, maker = await _database(tmp_path / "standalone-toctou.db")
        try:
            presentation_id = await _seed_attempt_presentation(maker)
            attempt = await _begin(maker, presentation_id)
            artifact_path = tmp_path / "standalone.pptx"
            original = b"verified standalone bytes"
            artifact_path.write_bytes(original)
            expected_digest = sha256(original).hexdigest()
            execution = PresentationExportExecution(
                attempt_id=attempt.id,
                attempt_presentation_id=presentation_id,
                owner_id=None,
                owner_scope="anonymous",
                workspace_operation_id=None,
                source_revision=4,
                source_snapshot_sha256="c" * 64,
                html_source_sha256="d" * 64,
                composition_sha256="e" * 64,
                asset_manifest_sha256="f" * 64,
                route=PresentationExportRouteResult(
                    result=PresentationExportAdapterResult(
                        presentation=PresentationAndPath(
                            presentation_id=presentation_id,
                            path=str(artifact_path),
                        ),
                        selected_adapter="general",
                        artifact_sha256=expected_digest,
                        artifact_size_bytes=len(original),
                        compiler="presentation-export",
                        compiler_version="0.4.3",
                        runtime="chromium-dom-export",
                        runtime_version="presenton.render-artifact/v1",
                        package_sha256=None,
                    ),
                    adapter_chain=("general",),
                    fallback_reason=None,
                    capability_evidence={"selected_adapter": "general"},
                    asset_digests={},
                    asset_digests_verified=True,
                ),
            )

            artifact_path.write_bytes(b"concurrently overwritten")
            async with maker() as session:
                with pytest.raises(
                    PresentationExportFreshnessError,
                    match="presentation_export_artifact_digest_mismatch",
                ):
                    await terminalize_standalone_presentation_export(
                        session,
                        execution,
                    )

            async with maker() as session:
                stored = await session.get(PresentationExportAttempt, attempt.id)
                assert stored is not None
                assert stored.state == "failed"
                assert (
                    stored.failure_code
                    == "presentation_export_artifact_digest_mismatch"
                )
                assert stored.artifact_sha256 is None
                assert stored.artifact_size_bytes is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
