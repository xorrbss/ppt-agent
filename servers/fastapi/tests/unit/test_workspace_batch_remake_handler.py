import asyncio
import uuid
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services import workspace_batch_remake_operation_handler as handler_module
from services.authored_page_remake_service import AuthoredPageRemakeError
from services.workspace_batch_remake_operation_handler import (
    WorkspaceBatchRemakeOperationHandler,
    register_workspace_batch_remake_operation_handler,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationClaim,
    WorkspaceOperationHandlerRegistry,
)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


async def _engine(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


class _SessionFactory:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> "_SessionFactory":
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_exc_info) -> bool:
        return False


def _claim(presentation_id: uuid.UUID, previews: list[dict]) -> WorkspaceOperationClaim:
    return WorkspaceOperationClaim(
        operation_id=uuid.uuid4(),
        owner_id=None,
        owner_scope="anonymous",
        conversation_id=uuid.uuid4(),
        presentation_id=presentation_id,
        kind="slide.regenerate.batch",
        request_payload={"context": {"batch_task_id": "task-parent", "previews": previews}},
        expected_revision=3,
        attempt_number=1,
        attempt_token="attempt",
        fencing_token=1,
    )


def _context(claim: WorkspaceOperationClaim, *, progress_result: bool = True):
    stages: list[str] = []

    async def progress(*, stage, current=None, total=None, percent=None, message=None):
        stages.append(stage)
        return progress_result

    async def raise_if_cancelled():
        return None

    leases: list[int] = []

    async def extend_lease(*, seconds):
        leases.append(seconds)
        return True

    return SimpleNamespace(
        claim=claim,
        progress=progress,
        raise_if_cancelled=raise_if_cancelled,
        extend_lease=extend_lease,
        stages=stages,
        leases=leases,
    )


async def _seed_deck(session: AsyncSession, slide_count: int, *, theme=None):
    outline = PresentationOutlineModel(
        slides=[SlideOutlineModel(content="Keep all facts")] * slide_count
    )
    presentation = PresentationModel(
        content="Deck",
        n_slides=slide_count,
        language="Korean",
        title="Deck",
        mode="authored",
        outlines=outline.model_dump(mode="json"),
        theme=theme if theme is not None else {"style": "default"},
        revision=3,
    )
    session.add(presentation)
    await session.flush()
    session.add_all(
        [
            SlideModel(
                presentation=presentation.id,
                layout_group="authored",
                layout="authored:content",
                index=index,
                content={"__authored__": True, "role": "content"},
                html_content=f"old-{index}",
                properties={},
            )
            for index in range(slide_count)
        ]
    )
    await session.commit()
    return presentation


def _install_publishers(monkeypatch, session, *, failing_indexes: frozenset[int]):
    """Stub the per-slide publish and the terminal revision bump.

    The per-slide stub writes before it fails so the savepoint's rollback is
    observable in the committed deck rather than assumed.
    """

    revision_calls: list[dict] = []

    async def publish_page(
        *, session, presentation_id, slide_index, preview_task_id, snapshot=True
    ):
        # The batch takes one snapshot of its own, so it must never ask the
        # per-page publisher for another.
        assert snapshot is False
        await session.execute(
            update(SlideModel)
            .where(
                SlideModel.presentation == presentation_id,
                SlideModel.index == slide_index,
            )
            .values(html_content=f"new-{slide_index}")
            .execution_options(synchronize_session=False)
        )
        if slide_index in failing_indexes:
            raise AuthoredPageRemakeError(
                "authored_page_remake_source_changed", stage="publish_validation"
            )
        return {"slide_index": slide_index, "preview_task_id": preview_task_id}

    async def publish_revision(_session, **kwargs):
        revision_calls.append(kwargs)
        return int(kwargs["expected_revision"]) + 1

    monkeypatch.setattr(handler_module, "publish_authored_page_remake", publish_page)
    monkeypatch.setattr(handler_module, "publish_presentation_revision", publish_revision)
    return revision_calls


def test_batch_handler_registers_with_automatic_recovery():
    registry = WorkspaceOperationHandlerRegistry()
    handler = register_workspace_batch_remake_operation_handler(registry=registry)

    assert isinstance(handler, WorkspaceBatchRemakeOperationHandler)
    assert registry.resolve("slide.regenerate.batch") is handler
    assert registry.recovery_policy("slide.regenerate.batch") == "automatic"


@async_test
async def test_batch_handler_rejects_a_malformed_preview_list():
    for previews in ([], [{"slide_index": 0}], [{"slide_index": -1, "preview_task_id": "t"}]):
        claim = _claim(uuid.uuid4(), previews)
        with pytest.raises(
            NonRetryableWorkspaceOperationError,
            match="authored_batch_remake_request_invalid",
        ):
            await WorkspaceBatchRemakeOperationHandler().execute(_context(claim))


@async_test
async def test_batch_handler_publishes_every_slide_with_one_revision_bump(
    tmp_path, monkeypatch
):
    engine = await _engine(tmp_path / "batch-happy.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = await _seed_deck(session, 3)
        revision_calls = _install_publishers(
            monkeypatch, session, failing_indexes=frozenset()
        )
        monkeypatch.setattr(
            handler_module, "async_session_maker", _SessionFactory(session)
        )
        claim = _claim(
            presentation.id,
            [
                {"slide_index": 2, "preview_task_id": "task-2"},
                {"slide_index": 0, "preview_task_id": "task-0"},
            ],
        )

        context = _context(claim)
        result = await WorkspaceBatchRemakeOperationHandler().execute(context)

        # The publish is one write transaction, so no heartbeat can renew the
        # lease while it runs: the whole batch has to be paid for up front.
        assert context.leases == [
            handler_module._LEASE_BASE_SECONDS
            + handler_module._LEASE_PER_PAGE_SECONDS * 2
        ]
        assert len(revision_calls) == 1
        assert revision_calls[0]["expected_revision"] == 3
        assert revision_calls[0]["operation_kind"] == "slide.regenerate.batch"
        assert result.result_revision == 4
        assert result.payload["status"] == "published"
        assert result.payload["kind"] == "authored_batch_remake"
        assert result.payload["requested_count"] == 2
        assert result.payload["published_count"] == 2
        assert result.payload["failed_count"] == 0
        assert result.payload["published_slide_indices"] == [0, 2]
        assert result.payload["fixed_slide_indices"] == [0, 2]
        assert result.payload["failed_slide_indices"] == []
        assert result.payload["canvas_refresh_required"] is True
        assert result.payload["rollback_available"] is True
        assert result.payload["result_revision"] == 4
        assert result.payload["slide_results"] == [
            {"slide_index": 0, "status": "published", "preview_task_id": "task-0"},
            {"slide_index": 2, "status": "published", "preview_task_id": "task-2"},
        ]

        html = dict(
            (
                await session.execute(
                    select(SlideModel.index, SlideModel.html_content)
                )
            ).all()
        )
        assert html == {0: "new-0", 1: "old-1", 2: "new-2"}
    await engine.dispose()


@async_test
async def test_one_rejected_slide_is_reported_while_the_rest_commit(
    tmp_path, monkeypatch
):
    engine = await _engine(tmp_path / "batch-partial.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = await _seed_deck(session, 3)
        revision_calls = _install_publishers(
            monkeypatch, session, failing_indexes=frozenset({1})
        )
        monkeypatch.setattr(
            handler_module, "async_session_maker", _SessionFactory(session)
        )
        claim = _claim(
            presentation.id,
            [
                {"slide_index": 0, "preview_task_id": "task-0"},
                {"slide_index": 1, "preview_task_id": "task-1"},
                {"slide_index": 2, "preview_task_id": "task-2"},
            ],
        )

        result = await WorkspaceBatchRemakeOperationHandler().execute(_context(claim))

        assert len(revision_calls) == 1
        assert result.payload["status"] == "partial"
        assert result.payload["published_count"] == 2
        assert result.payload["failed_count"] == 1
        assert result.payload["failed_slide_indices"] == [1]
        assert result.payload["slide_results"][1] == {
            "slide_index": 1,
            "status": "failed",
            "preview_task_id": "task-1",
            "reason_code": "authored_page_remake_source_changed",
        }

        html = dict(
            (
                await session.execute(
                    select(SlideModel.index, SlideModel.html_content)
                )
            ).all()
        )
        # The savepoint rolled back only the rejected slide's write.
        assert html == {0: "new-0", 1: "old-1", 2: "new-2"}
    await engine.dispose()


@async_test
async def test_all_failed_slides_publish_nothing(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "batch-all-failed.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = await _seed_deck(session, 2)
        revision_calls = _install_publishers(
            monkeypatch, session, failing_indexes=frozenset({0, 1})
        )
        monkeypatch.setattr(
            handler_module, "async_session_maker", _SessionFactory(session)
        )
        claim = _claim(
            presentation.id,
            [
                {"slide_index": 0, "preview_task_id": "task-0"},
                {"slide_index": 1, "preview_task_id": "task-1"},
            ],
        )

        with pytest.raises(
            NonRetryableWorkspaceOperationError,
            match="authored_batch_remake_all_slides_failed",
        ):
            await WorkspaceBatchRemakeOperationHandler().execute(_context(claim))

        assert revision_calls == []
        html = dict(
            (
                await session.execute(
                    select(SlideModel.index, SlideModel.html_content)
                )
            ).all()
        )
        assert html == {0: "old-0", 1: "old-1"}
    await engine.dispose()


@async_test
async def test_pinned_master_deck_is_rejected_before_any_publish(
    tmp_path, monkeypatch
):
    engine = await _engine(tmp_path / "batch-pinned.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = await _seed_deck(
            session,
            2,
            theme={
                "style": "default",
                "authored_vnext": {
                    "schemaVersion": "legacy-authored-vnext-projection/v1",
                    "masterPin": {"masterId": "master:company"},
                },
            },
        )
        revision_calls = _install_publishers(
            monkeypatch, session, failing_indexes=frozenset()
        )
        monkeypatch.setattr(
            handler_module, "async_session_maker", _SessionFactory(session)
        )
        claim = _claim(presentation.id, [{"slide_index": 0, "preview_task_id": "t0"}])

        with pytest.raises(
            NonRetryableWorkspaceOperationError,
            match="authored_batch_remake_master_pinned_unsupported",
        ):
            await WorkspaceBatchRemakeOperationHandler().execute(_context(claim))

        assert revision_calls == []
    await engine.dispose()


@async_test
async def test_stale_expected_revision_is_reported_without_publishing(
    tmp_path, monkeypatch
):
    engine = await _engine(tmp_path / "batch-stale.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = await _seed_deck(session, 1)
        presentation.revision = 9
        session.add(presentation)
        await session.commit()
        revision_calls = _install_publishers(
            monkeypatch, session, failing_indexes=frozenset()
        )
        monkeypatch.setattr(
            handler_module, "async_session_maker", _SessionFactory(session)
        )
        claim = _claim(presentation.id, [{"slide_index": 0, "preview_task_id": "t0"}])

        with pytest.raises(
            NonRetryableWorkspaceOperationError, match="stale_presentation_revision"
        ):
            await WorkspaceBatchRemakeOperationHandler().execute(_context(claim))

        assert revision_calls == []
    await engine.dispose()


@async_test
async def test_the_batch_takes_exactly_one_undo_point(tmp_path, monkeypatch):
    """Twenty per-page snapshots would evict the deck's whole real history.

    The version store keeps MAX_VERSIONS_PER_PRESENTATION entries and a batch may
    publish that many pages, so the batch snapshots once and tells the per-page
    publisher not to snapshot at all.
    """
    engine = await _engine(tmp_path / "batch-undo.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = await _seed_deck(session, 3)
        _install_publishers(monkeypatch, session, failing_indexes=frozenset())
        monkeypatch.setattr(
            handler_module, "async_session_maker", _SessionFactory(session)
        )

        snapshots: list[uuid.UUID] = []

        async def fake_snapshot(session, presentation_id):
            snapshots.append(presentation_id)

        monkeypatch.setattr(handler_module, "snapshot_slides_for_batch", fake_snapshot)

        claim = _claim(
            presentation.id,
            [
                {"slide_index": 0, "preview_task_id": "task-0"},
                {"slide_index": 1, "preview_task_id": "task-1"},
                {"slide_index": 2, "preview_task_id": "task-2"},
            ],
        )

        await WorkspaceBatchRemakeOperationHandler().execute(_context(claim))

        assert snapshots == [presentation.id]
    await engine.dispose()
