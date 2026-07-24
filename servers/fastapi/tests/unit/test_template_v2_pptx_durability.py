from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services import template_v2_pptx_ingestion_service as ingestion
from templates.v2.pptx.assembler import AssembledTemplateV2Draft
from templates.v2.models.layouts import RawSlideLayouts, SlideLayouts


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[
                    PresentationModel.__table__,
                    SlideModel.__table__,
                    TemplateV2.__table__,
                    TemplateV2LocalState.__table__,
                    AsyncPresentationGenerationTaskModel.__table__,
                    TemplateV2PptxImport.__table__,
                ],
            )
        )
    return engine, maker


async def _insert_import(
    maker,
    *,
    state: str = "queued",
    task_status: str = "pending",
    attempt_number: int = 0,
    attempt_token: str | None = None,
    lease_expires_at: datetime | None = None,
    requested_template_id: str | None = None,
) -> tuple[uuid.UUID, str]:
    import_id = uuid.uuid4()
    task_id = f"task-{uuid.uuid4().hex}"
    async with maker() as session:
        session.add(
            AsyncPresentationGenerationTaskModel(
                id=task_id,
                status=task_status,
                data={
                    "kind": ingestion.IMPORT_TASK_KIND,
                    "import_id": str(import_id),
                    "state": state,
                    "attempt_number": attempt_number,
                },
            )
        )
        session.add(
            TemplateV2PptxImport(
                id=import_id,
                task_id=task_id,
                requested_template_id=(
                    requested_template_id
                    or f"template-{uuid.uuid4().hex}"
                ),
                state=state,
                attempt_number=attempt_number,
                attempt_token=attempt_token,
                lease_expires_at=lease_expires_at,
                source_filename="source.pptx",
                source_media_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "presentationml.presentation"
                ),
                source_size_bytes=100,
                source_sha256="a" * 64,
                source_storage_key=f"private/{import_id}.pptx",
                manifest={"schema_version": 1},
            )
        )
        await session.commit()
    return import_id, task_id


def _assembled_draft() -> AssembledTemplateV2Draft:
    text = {
        "type": "text",
        "position": {"x": 1, "y": 1},
        "size": {"width": 8, "height": 1},
        "runs": [{"text": "Imported title"}],
        "decorative": False,
        "name": "title",
        "min_length": 1,
        "max_length": 80,
    }
    raw_layouts = RawSlideLayouts.model_validate(
        {
            "layouts": [
                {
                    "id": "import-layout",
                    "description": "Imported source slide layout",
                    "elements": [text],
                }
            ]
        }
    )
    layouts = SlideLayouts.model_validate(
        {
            "layouts": [
                {
                    "id": "import-layout",
                    "description": "Imported editable slide layout",
                    "components": [
                        {
                            "id": "hero",
                            "description": "Imported editable hero component",
                            "position": {"x": 1, "y": 1},
                            "elements": [
                                {
                                    **text,
                                    "position": {"x": 0, "y": 0},
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )
    return AssembledTemplateV2Draft(
        raw_layouts=raw_layouts,
        layouts=layouts,
        contents=[{"hero": {"title": "Imported title"}}],
        manifest={
            "schema_version": 1,
            "review": {"required": True},
        },
    )


def test_concurrent_workers_only_one_claims_the_queued_attempt(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "claim.sqlite")
        try:
            import_id, task_id = await _insert_import(maker)
            claimed_at = datetime.now(timezone.utc)

            async def claim(token: str):
                async with maker() as session:
                    return await ingestion.claim_template_v2_pptx_import(
                        session,
                        import_id,
                        task_id,
                        token=token,
                        now=claimed_at,
                    )

            claims = await asyncio.gather(claim("owner-a"), claim("owner-b"))
            assert sorted(claim for claim in claims if claim is not None) in (
                ["owner-a"],
                ["owner-b"],
            )
            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                task = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    task_id,
                )
                assert job is not None
                assert task is not None
                assert job.state == "processing"
                assert job.attempt_number == 1
                assert job.attempt_token in {"owner-a", "owner-b"}
                assert task.status == "running"
                assert "attempt_token" not in task.data
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_finalize_writes_template_and_sidecar_once_across_retries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "finalize-idempotent.sqlite")
        monkeypatch.setattr(ingestion, "async_session_maker", maker)
        try:
            import_id, task_id = await _insert_import(
                maker,
                state="processing",
                task_status="running",
                attempt_number=1,
                attempt_token="current-owner",
                lease_expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=10),
            )
            assembled = _assembled_draft()

            assert not await ingestion._persist_success(
                import_id,
                task_id,
                "stale-owner",
                assembled,
            )
            outcomes = await asyncio.gather(
                ingestion._persist_success(
                    import_id,
                    task_id,
                    "current-owner",
                    assembled,
                ),
                ingestion._persist_success(
                    import_id,
                    task_id,
                    "current-owner",
                    assembled,
                ),
            )
            assert sorted(outcomes) == [False, True]
            assert not await ingestion._persist_success(
                import_id,
                task_id,
                "current-owner",
                assembled,
            )

            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                task = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    task_id,
                )
                assert job is not None
                assert task is not None
                assert job.state == "review_required"
                assert job.draft_template_id is not None
                assert job.attempt_token is None
                assert task.status == "completed"

                template = await session.get(
                    TemplateV2,
                    job.draft_template_id,
                )
                local_state = await session.get(
                    TemplateV2LocalState,
                    job.draft_template_id,
                )
                assert template is not None
                assert local_state is not None
                assert local_state.presentation_id == template.presentation_id
                assert local_state.revision == 1
                assert (
                    await session.scalar(
                        select(func.count()).select_from(TemplateV2)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(
                            TemplateV2LocalState
                        )
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(PresentationModel)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(SlideModel)
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_finalize_constraint_failure_rolls_back_entire_transaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "finalize-rollback.sqlite")
        monkeypatch.setattr(ingestion, "async_session_maker", maker)
        requested_template_id = "duplicate-template"
        try:
            import_id, task_id = await _insert_import(
                maker,
                state="processing",
                task_status="running",
                attempt_number=1,
                attempt_token="current-owner",
                lease_expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=10),
                requested_template_id=requested_template_id,
            )
            existing_presentation = PresentationModel(
                content="Existing Template V2 source",
                n_slides=0,
                language="en",
                title="Existing",
                layout=None,
                structure=None,
                theme={"mode": "template"},
                mode="template",
                version="v2-standard",
            )
            async with maker() as session:
                session.add(existing_presentation)
                session.add(
                    TemplateV2(
                        id=requested_template_id,
                        presentation_id=existing_presentation.id,
                        name="Existing template",
                    )
                )
                session.add(
                    TemplateV2LocalState(
                        template_id=requested_template_id,
                        presentation_id=existing_presentation.id,
                        revision=1,
                    )
                )
                await session.commit()

            with pytest.raises(IntegrityError):
                await ingestion._persist_success(
                    import_id,
                    task_id,
                    "current-owner",
                    _assembled_draft(),
                )

            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                task = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    task_id,
                )
                assert job is not None
                assert task is not None
                assert job.state == "processing"
                assert job.draft_template_id is None
                assert job.attempt_token == "current-owner"
                assert task.status == "running"
                assert (
                    await session.scalar(
                        select(func.count()).select_from(PresentationModel)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(TemplateV2)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(
                            TemplateV2LocalState
                        )
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(SlideModel)
                    )
                    == 0
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_expired_lease_is_recovered_and_stale_owner_cannot_finish(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "recovery.sqlite")
        monkeypatch.setattr(ingestion, "async_session_maker", maker)
        try:
            import_id, task_id = await _insert_import(maker)
            started_at = datetime.now(timezone.utc)
            async with maker() as session:
                assert (
                    await ingestion.claim_template_v2_pptx_import(
                        session,
                        import_id,
                        task_id,
                        token="old-owner",
                        now=started_at,
                    )
                    == "old-owner"
                )

            assert await ingestion.heartbeat_template_v2_pptx_import(
                import_id,
                task_id,
                "old-owner",
                now=started_at + timedelta(minutes=1),
            )
            async with maker() as session:
                assert (
                    await ingestion.recover_stalled_template_v2_pptx_imports(
                        session,
                        now=started_at + timedelta(minutes=5),
                    )
                    == 0
                )
            async with maker() as session:
                assert (
                    await ingestion.recover_stalled_template_v2_pptx_imports(
                        session,
                        now=started_at + timedelta(minutes=7),
                    )
                    == 1
                )

            assert not await ingestion.fail_template_v2_pptx_import(
                import_id,
                task_id,
                "old-owner",
                RuntimeError("stale result"),
            )
            async with maker() as session:
                assert (
                    await ingestion.claim_template_v2_pptx_import(
                        session,
                        import_id,
                        task_id,
                        token="new-owner",
                        now=started_at + timedelta(minutes=7),
                    )
                    == "new-owner"
                )
            assert not await ingestion.fail_template_v2_pptx_import(
                import_id,
                task_id,
                "old-owner",
                RuntimeError("late stale result"),
            )
            assert await ingestion.fail_template_v2_pptx_import(
                import_id,
                task_id,
                "new-owner",
                RuntimeError("owned failure"),
            )
            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                task = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    task_id,
                )
                assert job is not None
                assert task is not None
                assert job.state == "failed"
                assert job.attempt_number == 2
                assert job.attempt_token is None
                assert task.status == "error"
                assert task.data["attempt_number"] == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_retry_is_a_single_failed_to_queued_transition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "retry.sqlite")
        monkeypatch.setattr(ingestion, "async_session_maker", maker)
        try:
            import_id, task_id = await _insert_import(
                maker,
                state="failed",
                task_status="error",
                attempt_number=1,
            )

            async def retry(marker: str):
                async with maker() as session:
                    return await ingestion.requeue_failed_template_v2_pptx_import(
                        session,
                        import_id,
                        task_id,
                        {
                            "schema_version": 1,
                            "retry_marker": marker,
                            "failure": None,
                        },
                    )

            outcomes = await asyncio.gather(retry("a"), retry("b"))
            assert sorted(outcomes) == [False, True]
            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                task = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    task_id,
                )
                assert job is not None
                assert task is not None
                assert job.state == "queued"
                assert job.manifest["retry_marker"] in {"a", "b"}
                assert task.status == "pending"
                assert task.data["attempt_number"] == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_dispatch_iteration_recovers_stalled_work_after_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "restart.sqlite")
        monkeypatch.setattr(ingestion, "async_session_maker", maker)
        dispatched: list[tuple[uuid.UUID, str]] = []

        async def record_dispatch(import_id: uuid.UUID, task_id: str) -> None:
            dispatched.append((import_id, task_id))

        monkeypatch.setattr(
            ingestion,
            "run_template_v2_pptx_import",
            record_dispatch,
        )
        try:
            import_id, task_id = await _insert_import(
                maker,
                state="processing",
                task_status="running",
                attempt_number=1,
                attempt_token="dead-process",
                lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
            assert await ingestion.dispatch_template_v2_pptx_imports_once() == 1
            await asyncio.sleep(0)
            assert dispatched == [(import_id, task_id)]
            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                task = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    task_id,
                )
                assert job is not None
                assert task is not None
                assert job.state == "queued"
                assert job.attempt_token is None
                assert task.status == "pending"
        finally:
            if ingestion._inflight_tasks:
                await asyncio.gather(
                    *list(ingestion._inflight_tasks),
                    return_exceptions=True,
                )
                ingestion._inflight_tasks.clear()
            await engine.dispose()

    asyncio.run(scenario())
