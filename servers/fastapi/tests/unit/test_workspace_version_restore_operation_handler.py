from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.presentation import PresentationModel
from models.sql.presentation_version import PresentationVersionModel
from models.sql.slide import SlideModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceConversation,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from services.workspace_chat_operation_handler import (
    PresentationChatOperationHandler,
    VersionRestoreOperationHandler,
    register_presentation_chat_operation_handlers,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationClaim,
    WorkspaceOperationHandlerRegistry,
)


TABLES = [
    User.__table__,
    PresentationModel.__table__,
    PresentationVersionModel.__table__,
    SlideModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _slide_dump(text: str) -> dict:
    return {
        "layout_group": "authored",
        "layout": "custom",
        "index": 0,
        "content": {"text": text},
        "html_content": f"<section>{text}</section>",
        "ui": None,
        "speaker_note": f"note:{text}",
        "properties": {"boundary": "authored"},
    }


async def _seed(
    maker,
    *,
    presentation_revision: int,
    expected_revision: int,
    explicit_target: bool = True,
):
    operation_id = uuid.uuid4()
    attempt_token = "restore-attempt"
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            content="authored deck",
            n_slides=1,
            language="ko",
            layout=None,
            mode="authored",
            theme={"mode": "authored", "brand": "preserve-me"},
            revision=presentation_revision,
        )
        session.add(presentation)
        await session.flush()
        session.add(
            SlideModel(
                presentation=presentation.id,
                **_slide_dump("current"),
            )
        )
        target = PresentationVersionModel(
            presentation_id=presentation.id,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            label="target",
            slides=[_slide_dump("target")],
        )
        session.add(target)
        conversation = WorkspaceConversation(
            presentation_id=presentation.id,
            next_position=2,
        )
        session.add(conversation)
        await session.flush()
        request_payload = {
            "command": "restore version",
            "context": (
                {"version_id": str(target.id), "source": "version_history"}
                if explicit_target
                else {"strategy": "previous", "source": "undo"}
            ),
        }
        session.add(
            WorkspaceOperation(
                id=operation_id,
                conversation_id=conversation.id,
                presentation_id=presentation.id,
                anchor_position=1,
                kind="version.restore",
                state="running",
                idempotency_key_hash="1" * 64,
                request_hash="2" * 64,
                request_payload=request_payload,
                expected_revision=expected_revision,
                attempt_number=1,
                attempt_token=attempt_token,
                fencing_token=1,
            )
        )

    claim = WorkspaceOperationClaim(
        operation_id=operation_id,
        owner_id=None,
        owner_scope="anonymous",
        conversation_id=conversation.id,
        presentation_id=presentation.id,
        kind="version.restore",
        request_payload=request_payload,
        expected_revision=expected_revision,
        attempt_number=1,
        attempt_token=attempt_token,
        fencing_token=1,
    )
    return presentation.id, operation_id, target.id, claim


class _Context:
    def __init__(self, claim: WorkspaceOperationClaim):
        self.claim = claim
        self.stages: list[str] = []

    async def progress(self, *, stage, current=None, total=None, percent=None):
        self.stages.append(stage)
        return True

    async def cancellation_requested(self):
        return False

    async def raise_if_cancelled(self):
        return None


def test_restore_publishes_once_and_preserves_authored_boundary(tmp_path: Path):
    asyncio.run(_restore_publishes_once_and_preserves_authored_boundary(tmp_path))


async def _restore_publishes_once_and_preserves_authored_boundary(tmp_path: Path):
    engine, maker = await _database(tmp_path / "restore-success.db")
    try:
        presentation_id, operation_id, target_id, claim = await _seed(
            maker,
            presentation_revision=3,
            expected_revision=3,
        )
        handler = VersionRestoreOperationHandler(session_factory=maker)
        result = await handler.execute(_Context(claim))

        assert result.result_revision == 4
        assert result.payload == {
            "kind": "version.restore",
            "summary": "선택한 버전으로 복원했습니다.",
            "restored_version_id": str(target_id),
            "slide_count": 1,
            "source_revision": 3,
            "revision": 4,
        }

        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            operation = await session.get(WorkspaceOperation, operation_id)
            slides = list(
                (
                    await session.execute(
                        select(SlideModel).where(
                            SlideModel.presentation == presentation_id
                        )
                    )
                ).scalars()
            )
            versions = list(
                (
                    await session.execute(
                        select(PresentationVersionModel)
                        .where(
                            PresentationVersionModel.presentation_id
                            == presentation_id
                        )
                        .order_by(PresentationVersionModel.created_at)
                    )
                ).scalars()
            )
            assert presentation.revision == 4
            assert presentation.mode == "authored"
            assert presentation.layout is None
            assert presentation.theme == {
                "mode": "authored",
                "brand": "preserve-me",
            }
            assert operation.state == "succeeded"
            assert operation.result_revision == 4
            assert operation.result == result.payload
            assert len(slides) == 1
            assert slides[0].html_content == "<section>target</section>"
            assert slides[0].ui is None
            assert len(versions) == 2
            assert versions[-1].label == "복원 전 자동 저장"
            assert versions[-1].slides[0]["html_content"] == (
                "<section>current</section>"
            )

        # A replay with the same attempt cannot publish or checkpoint twice.
        with pytest.raises(NonRetryableWorkspaceOperationError) as replayed:
            await handler.execute(_Context(claim))
        assert replayed.value.code == "stale_presentation_revision"
        async with maker() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PresentationVersionModel)
                    .where(
                        PresentationVersionModel.presentation_id
                        == presentation_id
                    )
                )
            ) == 2
            assert (await session.get(PresentationModel, presentation_id)).revision == 4
    finally:
        await engine.dispose()


def test_stale_restore_rolls_back_checkpoint_and_slide_replacement(tmp_path: Path):
    asyncio.run(_stale_restore_rolls_back_checkpoint_and_slide_replacement(tmp_path))


async def _stale_restore_rolls_back_checkpoint_and_slide_replacement(tmp_path: Path):
    engine, maker = await _database(tmp_path / "restore-stale.db")
    try:
        presentation_id, operation_id, _target_id, claim = await _seed(
            maker,
            presentation_revision=8,
            expected_revision=7,
        )
        handler = VersionRestoreOperationHandler(session_factory=maker)
        with pytest.raises(NonRetryableWorkspaceOperationError) as caught:
            await handler.execute(_Context(claim))
        assert caught.value.code == "stale_presentation_revision"

        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            operation = await session.get(WorkspaceOperation, operation_id)
            slide = (
                await session.execute(
                    select(SlideModel).where(
                        SlideModel.presentation == presentation_id
                    )
                )
            ).scalar_one()
            assert presentation.revision == 8
            assert operation.state == "running"
            assert operation.result_revision is None
            assert slide.html_content == "<section>current</section>"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PresentationVersionModel)
                    .where(
                        PresentationVersionModel.presentation_id
                        == presentation_id
                    )
                )
            ) == 1
    finally:
        await engine.dispose()


def test_previous_strategy_uses_latest_durable_version(tmp_path: Path):
    asyncio.run(_previous_strategy_uses_latest_durable_version(tmp_path))


async def _previous_strategy_uses_latest_durable_version(tmp_path: Path):
    engine, maker = await _database(tmp_path / "restore-previous.db")
    try:
        presentation_id, _operation_id, target_id, claim = await _seed(
            maker,
            presentation_revision=1,
            expected_revision=1,
            explicit_target=False,
        )
        result = await VersionRestoreOperationHandler(
            session_factory=maker
        ).execute(_Context(claim))
        assert result.payload["restored_version_id"] == str(target_id)
        async with maker() as session:
            slide = (
                await session.execute(
                    select(SlideModel).where(
                        SlideModel.presentation == presentation_id
                    )
                )
            ).scalar_one()
            assert slide.html_content == "<section>target</section>"
    finally:
        await engine.dispose()


def test_restore_registration_reuses_runner_with_automatic_recovery():
    registry = WorkspaceOperationHandlerRegistry()
    chat_handler = PresentationChatOperationHandler()
    restore_handler = VersionRestoreOperationHandler()

    register_presentation_chat_operation_handlers(
        registry=registry,
        handler=chat_handler,
        version_restore_handler=restore_handler,
    )

    assert registry.resolve("presentation.edit") is chat_handler
    assert registry.resolve("version.restore") is restore_handler
    assert registry.recovery_policy("version.restore") == "automatic"
