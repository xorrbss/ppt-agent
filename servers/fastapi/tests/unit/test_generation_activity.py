"""``GET /workspace/generation-activity`` and the draft preview's progress stamp.

The list is what tells a user a long deck is still generating before they
start another one, so it must be owner-scoped by the central hook, exclude
finished and deleted work, count pages from checkpoint metadata, and hand back
exactly the preconditions the existing cancel action accepts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from api.v1.ppt.endpoints import presentation_draft_preview as draft_preview_endpoint
from api.v1.ppt.endpoints import workspace as workspace_endpoint
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation import PRESENTATION_LIFECYCLE_DELETED, PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
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
    WorkspaceAttachmentInput,
    WorkspaceBootstrapRequest,
    WorkspaceOperationActionRequest,
)
from services import generation_activity as activity_service
from services.database_owner_scope import register_owner_scope_hooks
from services.workspace_operation_principal import operation_principal
from services.workspace_runtime_profile import WorkspaceRuntimeKind, WorkspaceRuntimeProfile
from services.workspace_service import bootstrap_workspace

TABLES = [
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
    AsyncPresentationGenerationTaskModel.__table__,
    PresentationGenerationJob.__table__,
    AuthoredSlideCheckpoint.__table__,
]
TENANT = "default"
T0 = datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _supported_runtime(monkeypatch):
    register_owner_scope_hooks()
    monkeypatch.setattr(
        workspace_endpoint,
        "_runtime_profile",
        lambda: WorkspaceRuntimeProfile(
            dialect="sqlite", kind=WorkspaceRuntimeKind.SQLITE_LOCAL, supported=True,
        ),
    )


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: SQLModel.metadata.create_all(sync, tables=TABLES)
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _owner(maker, name: str) -> uuid.UUID:
    async with maker() as session:
        user = User(username=name, hashed_password="unused")
        session.add(user)
        await session.commit()
        return user.id


async def _generation(
    maker,
    *,
    state: str,
    created_at: datetime,
    title: str | None = "Quarterly plan",
    file_name: str = r"C:\private\source\market.pdf",
    progress: dict | None = None,
    version: int = 3,
    started_at: datetime | None = None,
    lifecycle: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """One deck with a bootstrapped ``presentation.generate`` operation."""

    async with maker() as session:
        presentation = PresentationModel(
            content="generation activity", n_slides=4, language="ko",
            layout={"name": "test"}, mode="authored", title=title,
        )
        session.add(presentation)
        await session.commit()
        presentation_id = presentation.id
    async with maker() as session:
        _, _, operation, _ = await bootstrap_workspace(
            session,
            presentation_id=presentation_id,
            request=WorkspaceBootstrapRequest.from_internal_legacy_metadata(
                idempotency_key=f"generate-{presentation_id}",
                prompt="Build the deck",
                generation_settings={"language": "ko", "n_slides": 4},
                attachments=[
                    WorkspaceAttachmentInput(
                        file_name=file_name, media_type="application/pdf",
                        size_bytes=321, sha256="a" * 64, status="ready",
                    )
                ],
                expected_revision=0,
            ),
        )
        await session.commit()
        operation_id = operation.id
    async with maker() as session:
        operation = await session.get(WorkspaceOperation, operation_id)
        operation.state = state
        operation.version = version
        operation.created_at = created_at
        operation.started_at = started_at
        operation.progress = progress
        if lifecycle is not None:
            presentation = await session.get(PresentationModel, presentation_id)
            presentation.lifecycle_status = lifecycle
        await session.commit()
    return presentation_id, operation_id


async def _job(maker, *, presentation_id, operation_id, total, stage, checkpoints):
    async with maker() as session:
        task = AsyncPresentationGenerationTaskModel(status="pending")
        session.add(task)
        await session.flush()
        job = PresentationGenerationJob(
            task_id=task.id, presentation_id=presentation_id,
            workspace_operation_id=operation_id, request_sha256="0" * 64,
            request_payload={"template": "authored"}, state="running",
            total_slides=total, current_stage=stage,
        )
        session.add(job)
        await session.flush()
        for index, statuses in enumerate(checkpoints):
            author, render, persist, completed_at = statuses
            session.add(
                AuthoredSlideCheckpoint(
                    job_id=job.id, slide_index=index,
                    author_status=author, render_status=render, persist_status=persist,
                    author_completed_at=completed_at if author == "completed" else None,
                    render_completed_at=(
                        completed_at + timedelta(seconds=30) if render == "completed" else None
                    ),
                    persist_completed_at=None,
                    svg_checkpoint={"svg_content": "<svg/>" * 100},
                    author_html="<p>large</p>",
                )
            )
        await session.commit()
        return job.id


def test_lists_running_then_queued_with_checkpoint_progress(tmp_path: Path):
    asyncio.run(_lists_running_then_queued(tmp_path))


async def _lists_running_then_queued(tmp_path: Path):
    engine, maker = await _database(tmp_path / "activity.db")
    try:
        owner = await _owner(maker, "activity-owner")
        other = await _owner(maker, "activity-other")
        with operation_principal(other, TENANT):
            await _generation(maker, state="running", created_at=T0 - timedelta(hours=1))
        with operation_principal(owner, TENANT):
            later_queue = await _generation(
                maker, state="queued", created_at=T0 + timedelta(minutes=5),
                title=None, file_name="second-upload.pptx",
                progress={"stage": "waiting_for_capacity", "capacity_waiting": True,
                          "queue_position": 2},
            )
            earlier_queue = await _generation(
                maker, state="queued", created_at=T0 + timedelta(minutes=2), version=1,
            )
            running = await _generation(
                maker, state="running", created_at=T0 + timedelta(minutes=9),
                started_at=T0 + timedelta(minutes=10), version=7,
            )
            winding_down = await _generation(
                maker, state="cancelling", created_at=T0 + timedelta(minutes=1),
            )
            for state in ("succeeded", "failed", "cancelled", "awaiting_approval"):
                await _generation(maker, state=state, created_at=T0)
            await _generation(
                maker, state="running", created_at=T0,
                lifecycle=PRESENTATION_LIFECYCLE_DELETED,
            )
            await _job(
                maker, presentation_id=running[0], operation_id=running[1],
                total=100, stage="render",
                checkpoints=[
                    ("completed", "completed", "pending", T0 + timedelta(minutes=20)),
                    ("completed", "completed", "pending", T0 + timedelta(minutes=31)),
                    ("completed", "pending", "pending", T0 + timedelta(minutes=33)),
                    ("running", "pending", "pending", None),
                ],
            )
            # A queued deck whose job has not planned pages yet.
            await _job(
                maker, presentation_id=earlier_queue[0], operation_id=earlier_queue[1],
                total=0, stage=None, checkpoints=[],
            )

            async with maker() as session:
                response = await workspace_endpoint.get_workspace_generation_activity(
                    session=session,
                )
    finally:
        await engine.dispose()

    wire = response.model_dump(mode="json")
    assert datetime.fromisoformat(wire["server_time"]).tzinfo is not None
    items = wire["items"]
    # Another owner's deck, finished/unapproved work and deleted decks are absent.
    assert [item["operation_id"] for item in items] == [
        str(running[1]), str(winding_down[1]), str(earlier_queue[1]), str(later_queue[1]),
    ]
    assert items[1]["state"] == "cancelling"
    first, second, third = items[0], items[2], items[3]
    assert first == {
        "operation_id": str(running[1]),
        "presentation_id": str(running[0]),
        "state": "running",
        "presentation_title": "Quarterly plan",
        "source_file_name": "market.pdf",
        "pages": {"authored": 3, "rendered": 2, "persisted": 0, "total": 100},
        "stage": "render",
        "queue_position": None,
        "capacity_waiting": False,
        "created_at": (T0 + timedelta(minutes=9)).isoformat(),
        "started_at": (T0 + timedelta(minutes=10)).isoformat(),
        # The latest of every author/render/persist completion.
        "last_progress_at": (T0 + timedelta(minutes=33)).isoformat(),
        "operation_version": 7,
        "presentation_revision": 0,
    }
    assert second["pages"] == {"authored": 0, "rendered": 0, "persisted": 0, "total": None}
    assert second["last_progress_at"] is None and second["started_at"] is None
    assert (second["queue_position"], second["capacity_waiting"]) == (None, False)
    assert second["operation_version"] == 1
    # No job yet: zero counts, and the capacity wait comes from the operation.
    assert third["pages"] == {"authored": 0, "rendered": 0, "persisted": 0, "total": None}
    assert third["presentation_title"] is None
    assert third["source_file_name"] == "second-upload.pptx"
    assert (third["queue_position"], third["capacity_waiting"]) == (2, True)
    assert third["stage"] is None


def test_list_is_capped_and_uses_a_fixed_number_of_metadata_queries(tmp_path: Path):
    asyncio.run(_capped_fixed_queries(tmp_path))


async def _capped_fixed_queries(tmp_path: Path):
    engine, maker = await _database(tmp_path / "activity-cap.db")
    statements: list[str] = []

    def _record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    try:
        owner = await _owner(maker, "cap-owner")
        with operation_principal(owner, TENANT):
            first = await _generation(maker, state="running", created_at=T0)
            await _job(
                maker, presentation_id=first[0], operation_id=first[1], total=4,
                stage="author",
                checkpoints=[("completed", "pending", "pending", T0)],
            )
            event.listen(engine.sync_engine, "before_cursor_execute", _record)
            async with maker() as session:
                single = await activity_service.generation_activity(session)
            single_count = len(statements)
            event.remove(engine.sync_engine, "before_cursor_execute", _record)
            for minute in range(1, activity_service.GENERATION_ACTIVITY_LIMIT + 2):
                presentation_id, operation_id = await _generation(
                    maker, state="queued", created_at=T0 + timedelta(minutes=minute),
                )
                await _job(
                    maker, presentation_id=presentation_id, operation_id=operation_id,
                    total=4, stage=None,
                    checkpoints=[("completed", "completed", "pending", T0)],
                )
            statements.clear()
            event.listen(engine.sync_engine, "before_cursor_execute", _record)
            async with maker() as session:
                capped = await activity_service.generation_activity(session)
            event.remove(engine.sync_engine, "before_cursor_execute", _record)
    finally:
        await engine.dispose()

    assert len(single.items) == 1
    assert len(capped.items) == activity_service.GENERATION_ACTIVITY_LIMIT
    assert capped.items[0].operation_id == str(first[1])
    assert all(item.state == "queued" for item in capped.items[1:])
    created = [item.created_at for item in capped.items[1:]]
    assert created == sorted(created)
    # Same statement count for 1 item and for a full page: no per-item reads.
    assert single_count == len(statements) == 4
    for statement in statements:
        lowered = statement.lower()
        for heavy in ("svg_checkpoint", "author_html", "illustrated_html", "request_payload"):
            assert heavy not in lowered, (heavy, statement)


def test_returned_preconditions_are_accepted_by_the_cancel_action(tmp_path: Path):
    asyncio.run(_cancel_from_activity(tmp_path))


async def _cancel_from_activity(tmp_path: Path):
    engine, maker = await _database(tmp_path / "activity-cancel.db")
    try:
        owner = await _owner(maker, "cancel-owner")
        with operation_principal(owner, TENANT):
            running = await _generation(maker, state="running", created_at=T0, version=4)
            queued = await _generation(
                maker, state="queued", created_at=T0 + timedelta(minutes=1), version=2,
            )
            async with maker() as session:
                activity = await workspace_endpoint.get_workspace_generation_activity(
                    session=session,
                )
            assert [item.operation_id for item in activity.items] == [
                str(running[1]), str(queued[1]),
            ]
            # A stale version is refused, so the returned one is load-bearing.
            stale = activity.items[0]
            async with maker() as session:
                with pytest.raises(Exception) as conflict:
                    await workspace_endpoint.post_workspace_operation_action(
                        operation_id=uuid.UUID(stale.operation_id),
                        request=WorkspaceOperationActionRequest(
                            action="cancel", idempotency_key=str(uuid.uuid4()),
                            expected_revision=stale.presentation_revision,
                            expected_operation_version=stale.operation_version - 1,
                        ),
                        http_request=None,
                        session=session,
                    )
            assert getattr(conflict.value, "status_code", None) == 409
            for item in activity.items:
                async with maker() as session:
                    receipt = await workspace_endpoint.post_workspace_operation_action(
                        operation_id=uuid.UUID(item.operation_id),
                        request=WorkspaceOperationActionRequest(
                            action="cancel", idempotency_key=str(uuid.uuid4()),
                            expected_revision=item.presentation_revision,
                            expected_operation_version=item.operation_version,
                        ),
                        http_request=None,
                        session=session,
                    )
                assert receipt.duplicate is False
            async with maker() as session:
                states = {
                    operation_id: (await session.get(WorkspaceOperation, operation_id)).state
                    for operation_id in (running[1], queued[1])
                }
                after = await activity_service.generation_activity(session)
    finally:
        await engine.dispose()

    assert states == {running[1]: "cancelling", queued[1]: "cancelled"}
    # A cancelling deck still holds the owner's slot until the worker stops, so
    # it stays visible; the cancelled queued one is gone.
    assert [(item.operation_id, item.state) for item in after.items] == [
        (str(running[1]), "cancelling"),
    ]


def test_draft_preview_reports_the_shared_last_progress_instant(tmp_path: Path, monkeypatch):
    asyncio.run(_draft_preview_last_progress(tmp_path, monkeypatch))


async def _draft_preview_last_progress(tmp_path: Path, monkeypatch):
    engine, maker = await _database(tmp_path / "draft-preview.db")
    try:
        owner = await _owner(maker, "preview-owner")
        with operation_principal(owner, TENANT):
            presentation_id, operation_id = await _generation(
                maker, state="running", created_at=T0,
            )
            job_id = await _job(
                maker, presentation_id=presentation_id, operation_id=operation_id,
                total=3, stage="render",
                checkpoints=[
                    ("completed", "completed", "pending", T0 + timedelta(minutes=4)),
                    ("completed", "pending", "pending", T0 + timedelta(minutes=2)),
                ],
            )
            empty_job_id = uuid.uuid4()

            def context_for(selected_job_id):
                async def _context(_session, _presentation_id):
                    return SimpleNamespace(
                        id=selected_job_id, state="running", total_slides=3,
                        current_stage="render", operation_id=operation_id,
                        operation_state="running", error_code=None,
                    )
                return _context

            monkeypatch.setattr(draft_preview_endpoint, "_context", context_for(job_id))
            async with maker() as session:
                body = await draft_preview_endpoint.draft_preview(
                    presentation_id, SimpleNamespace(headers={}), session=session,
                )
                listed = await activity_service.generation_activity(session)
            monkeypatch.setattr(draft_preview_endpoint, "_context", context_for(empty_job_id))
            async with maker() as session:
                empty = await draft_preview_endpoint.draft_preview(
                    presentation_id, SimpleNamespace(headers={}), session=session,
                )
    finally:
        await engine.dispose()

    # render_completed_at of slide 0 is author + 30 s in the fixture.
    expected = (T0 + timedelta(minutes=4, seconds=30)).isoformat()
    assert body["lastProgressAt"] == expected
    assert listed.items[0].last_progress_at == expected
    assert empty["lastProgressAt"] is None
