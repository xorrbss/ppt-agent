import asyncio
import uuid
from functools import wraps
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from models.authored_batch_remake import AuthoredBatchRemakePreviewRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services import authored_batch_remake_service as batch

SOURCE_HTML = "<html><body><main>Source</main></body></html>"


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


def _patch_durable_admission(monkeypatch):
    async def admit(target_session, *, task_id_prefix, pending_message, **_values):
        task = AsyncPresentationGenerationTaskModel(
            id=f"{task_id_prefix}-{uuid.uuid4()}",
            status="pending",
            message=pending_message,
        )
        target_session.add(task)
        await target_session.commit()
        return task

    monkeypatch.setattr(batch, "admit_workspace_authored_task", admit)


async def _engine(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


class _SessionFactory:
    """Give the batch background runner the test-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> "_SessionFactory":
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_exc_info) -> bool:
        return False


async def _seed_deck(session: AsyncSession, slide_count: int) -> PresentationModel:
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
        theme={"style": "default"},
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
                html_content=SOURCE_HTML,
                properties={},
            )
            for index in range(slide_count)
        ]
    )
    await session.commit()
    return presentation


def test_batch_request_rejects_duplicates_negatives_and_oversize():
    assert AuthoredBatchRemakePreviewRequest(
        slide_indexes=[4, 7]
    ).instruction is None
    with pytest.raises(ValidationError):
        AuthoredBatchRemakePreviewRequest(slide_indexes=[])
    with pytest.raises(ValidationError):
        AuthoredBatchRemakePreviewRequest(slide_indexes=[1, 1])
    with pytest.raises(ValidationError):
        AuthoredBatchRemakePreviewRequest(slide_indexes=[-1])
    with pytest.raises(ValidationError):
        AuthoredBatchRemakePreviewRequest(slide_indexes=list(range(21)))


@async_test
async def test_queue_creates_durable_parent_and_children_without_background_task(
    tmp_path, monkeypatch
):
    engine = await _engine(tmp_path / "queue.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = await _seed_deck(session, 5)
        background_tasks = BackgroundTasks()

        async def admit(
            target_session,
            *,
            presentation_id,
            operation_kind,
            expected_revision,
            command,
            context,
            task_id_prefix,
            pending_message,
        ):
            assert presentation_id == presentation.id
            assert operation_kind == "slide.regenerate.batch.preview"
            assert expected_revision == presentation.revision
            assert context == {"slide_indexes": [1, 4], "instruction": None}
            task = AsyncPresentationGenerationTaskModel(
                id=f"{task_id_prefix}-{uuid.uuid4()}",
                status="pending",
                message=pending_message,
            )
            target_session.add(task)
            await target_session.commit()
            return task

        monkeypatch.setattr(batch, "admit_workspace_authored_task", admit)

        parent = await batch.queue_authored_batch_remake_preview(
            presentation.id,
            AuthoredBatchRemakePreviewRequest(slide_indexes=[4, 1]),
            background_tasks,
            session,
        )

        assert background_tasks.tasks == []
        assert parent.status == "processing"
        assert parent.data == {
            "kind": "authored_batch_remake_preview",
            "phase": "previewing",
            "presentation_id": str(presentation.id),
            "total": 2,
            "completed": 0,
            "slides": [
                {
                    "slide_index": 1,
                    "status": "pending",
                    "preview_task_id": parent.data["slides"][0]["preview_task_id"],
                },
                {
                    "slide_index": 4,
                    "status": "pending",
                    "preview_task_id": parent.data["slides"][1]["preview_task_id"],
                },
            ],
        }
        children = (
            await session.execute(
                select(AsyncPresentationGenerationTaskModel).where(
                    AsyncPresentationGenerationTaskModel.id
                    != parent.id
                )
            )
        ).scalars().all()
        assert len(children) == 2
        assert all(child.status == "pending" for child in children)
    await engine.dispose()


@async_test
async def test_queue_rejects_out_of_range_and_unauthored_targets(tmp_path):
    engine = await _engine(tmp_path / "reject.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = await _seed_deck(session, 2)
        blank = (
            await session.execute(
                select(SlideModel).where(SlideModel.index == 1)
            )
        ).scalars().one()
        blank.html_content = None
        session.add(blank)
        await session.commit()

        with pytest.raises(HTTPException) as out_of_range:
            await batch.queue_authored_batch_remake_preview(
                presentation.id,
                AuthoredBatchRemakePreviewRequest(slide_indexes=[0, 9]),
                BackgroundTasks(),
                session,
            )
        assert out_of_range.value.status_code == 400

        with pytest.raises(HTTPException) as no_html:
            await batch.queue_authored_batch_remake_preview(
                presentation.id,
                AuthoredBatchRemakePreviewRequest(slide_indexes=[1]),
                BackgroundTasks(),
                session,
            )
        assert no_html.value.status_code == 400
    await engine.dispose()


@async_test
async def test_fan_out_peak_concurrency_is_capped_at_four(monkeypatch):
    active = 0
    peak = 0
    started = asyncio.Event()

    async def fake_preview(_presentation_id, _slide_index, _request, _task_id):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.set()
        await asyncio.sleep(0.02)
        active -= 1

    recorded: list[int] = []

    async def fake_record(_parent_task_id, slide_index, _child_task_id):
        recorded.append(slide_index)

    monkeypatch.setattr(batch, "_run_preview_background", fake_preview)
    monkeypatch.setattr(batch, "_record_child_outcome", fake_record)

    pairs = tuple((index, f"task-{index}") for index in range(12))
    await batch._run_batch_preview_background(
        uuid.uuid4(),
        AuthoredBatchRemakePreviewRequest(slide_indexes=list(range(12))),
        "task-parent",
        pairs,
    )

    assert started.is_set()
    assert peak == batch.BATCH_PREVIEW_CONCURRENCY == 4
    assert sorted(recorded) == list(range(12))


@async_test
async def test_one_failed_slide_is_skipped_and_progress_is_reported(
    tmp_path, monkeypatch
):
    engine = await _engine(tmp_path / "fanout.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = await _seed_deck(session, 3)
        _patch_durable_admission(monkeypatch)
        parent = await batch.queue_authored_batch_remake_preview(
            presentation.id,
            AuthoredBatchRemakePreviewRequest(slide_indexes=[0, 1, 2]),
            BackgroundTasks(),
            session,
        )
        pairs = tuple(
            (entry["slide_index"], entry["preview_task_id"])
            for entry in parent.data["slides"]
        )

        # The fan-out is genuinely concurrent, so the shared test session needs
        # its own guard; concurrency itself is asserted separately.
        db_lock = asyncio.Lock()

        async def fake_preview(_presentation_id, slide_index, _request, task_id):
            async with db_lock:
                await _write_child_outcome(slide_index, task_id)

        async def _write_child_outcome(slide_index, task_id):
            task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
            if slide_index == 1:
                # The real per-slide path records its own error task; a raise on
                # top of that must still not cancel the siblings.
                task.status = "error"
                task.error = {
                    "detail": "authored_page_remake_source_render_failed",
                    "stage": "source_render",
                    "retryable": True,
                }
                task.data = {"kind": "authored_page_remake_preview", "phase": "failed"}
                await session.commit()
                raise RuntimeError("worker crashed after recording the failure")
            task.status = "completed"
            task.data = {
                "kind": "authored_page_remake_preview",
                "phase": "preview_ready",
                "candidate_image": f"authored/slide_{slide_index}.png",
                "candidate_digest": f"sha256:candidate-{slide_index}",
                "source_digest": f"sha256:source-{slide_index}",
                "validation": {
                    "candidate_dom": "warning" if slide_index == 2 else "passed"
                },
            }
            await session.commit()

        monkeypatch.setattr(batch, "_run_preview_background", fake_preview)
        monkeypatch.setattr(batch, "async_session_maker", _SessionFactory(session))

        await batch._run_batch_preview_background(
            presentation.id,
            AuthoredBatchRemakePreviewRequest(slide_indexes=[0, 1, 2]),
            parent.id,
            pairs,
        )
        await session.refresh(parent)

        assert parent.status == "completed"
        assert parent.data["phase"] == "preview_ready"
        assert parent.data["completed"] == 3
        assert parent.data["total"] == 3
        statuses = [entry["status"] for entry in parent.data["slides"]]
        assert statuses == ["previewed", "failed", "previewed"]
        assert parent.data["slides"][0]["candidate_image"] == "authored/slide_0.png"
        assert parent.data["slides"][0]["warning"] is False
        assert parent.data["slides"][2]["warning"] is True
        assert parent.data["slides"][1] == {
            "slide_index": 1,
            "status": "failed",
            "preview_task_id": pairs[1][1],
            "reason_code": "authored_page_remake_source_render_failed",
            "stage": "source_render",
            "retryable": True,
        }
    await engine.dispose()


@async_test
async def test_partial_progress_keeps_the_parent_processing(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "progress.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = await _seed_deck(session, 2)
        _patch_durable_admission(monkeypatch)
        parent = await batch.queue_authored_batch_remake_preview(
            presentation.id,
            AuthoredBatchRemakePreviewRequest(slide_indexes=[0, 1]),
            BackgroundTasks(),
            session,
        )
        first = parent.data["slides"][0]
        child = await session.get(
            AsyncPresentationGenerationTaskModel, first["preview_task_id"]
        )
        child.status = "completed"
        child.data = {
            "kind": "authored_page_remake_preview",
            "phase": "preview_ready",
            "candidate_image": "authored/slide_0.png",
            "candidate_digest": "sha256:candidate-0",
            "source_digest": "sha256:source-0",
            "validation": {"candidate_dom": "passed"},
        }
        session.add(child)
        await session.commit()

        monkeypatch.setattr(batch, "async_session_maker", _SessionFactory(session))
        await batch._record_child_outcome(parent.id, 0, first["preview_task_id"])
        await session.refresh(parent)

        assert parent.status == "processing"
        assert parent.data["phase"] == "previewing"
        assert parent.data["completed"] == 1
        assert parent.data["slides"][0]["status"] == "previewed"
        assert parent.data["slides"][1]["status"] == "pending"
    await engine.dispose()
