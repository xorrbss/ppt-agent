"""Phase 3: the slide tail runs while later slides are still being authored.

Two levels. The coordinator/runner cases drive
``services.authored_tail_streaming`` directly; the pipeline cases drive the
real ``_author_illustrate_render_pipeline`` against a scratch checkpoint
database, with the provider batch call and Chromium replaced, so the ordering
claim is made about the shipped code path and not about a model of it.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission
from models.sql.workspace import WorkspaceOperation
from services import authored_checkpoint_service as checkpoints
from services import authored_presentation_pipeline as pipeline
from services.authored_tail_streaming import (
    AuthoredTailStreamCoordinator,
    run_streamed_slide_tail,
)
from utils.llm_calls.author_deck import (
    AuthoredBatchFailure,
    AuthoredPlannedBatchResult,
)
from utils.llm_calls.author_slide import Brand
from utils.slide_capture_deck_scope import current_render_deck_scope


def _run(coro):
    return asyncio.run(coro)


# --- coordinator and runner ---------------------------------------------------


def test_coordinator_holds_slides_until_the_anchor_stage_is_over():
    coordinator = AuthoredTailStreamCoordinator(4)
    assert coordinator.publish(2) is True
    assert coordinator.publish(2) is False  # exactly once
    assert coordinator.publish(9) is False  # outside the deck

    async def scenario():
        # Nothing is claimable while the anchor may still be re-authored.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(coordinator.next_index(), timeout=0.05)
        coordinator.resume()
        coordinator.publish(0)
        first = await coordinator.next_index()
        second = await coordinator.next_index()
        return first, second

    assert _run(scenario()) == (2, 0)


def test_coordinator_close_flushes_every_unpublished_slide_in_order():
    coordinator = AuthoredTailStreamCoordinator(4)
    coordinator.resume()
    coordinator.publish(3)

    async def scenario():
        assert await coordinator.next_index() == 3
        coordinator.close()
        return [await coordinator.next_index() for _ in range(4)]

    assert _run(scenario()) == [0, 1, 2, None]
    assert coordinator.published == (0, 1, 2, 3)
    # A publish after the flush would hand the pool a second copy of an index.
    assert coordinator.publish(1) is False


def test_streamed_tail_processes_a_slide_before_the_pass_ends():
    processed: list[int] = []
    first_processed = asyncio.Event()

    async def process_slide(index):
        processed.append(index)
        first_processed.set()
        return index

    coordinator = AuthoredTailStreamCoordinator(3)
    coordinator.resume()

    async def author_pass():
        coordinator.publish(1)
        # The pass only returns once the tail has actually run a slide, which
        # is impossible under the phase barrier.
        await asyncio.wait_for(first_processed.wait(), timeout=1)
        coordinator.publish(0)
        coordinator.publish(2)

    results = _run(
        asyncio.wait_for(
            run_streamed_slide_tail(
                process_slide,
                author_pass=author_pass,
                coordinator=coordinator,
                slide_count=3,
                worker_count=2,
            ),
            timeout=2,
        )
    )

    assert processed[0] == 1
    assert sorted(processed) == [0, 1, 2]
    # Results stay in slide order however the indexes arrived.
    assert results == [0, 1, 2]


def test_streamed_tail_cancels_the_pool_when_the_pass_fails():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def process_slide(index):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return index

    coordinator = AuthoredTailStreamCoordinator(2)
    coordinator.resume()

    async def author_pass():
        coordinator.publish(0)
        await asyncio.wait_for(started.wait(), timeout=1)
        raise RuntimeError("slide 1 failed both calls")

    async def scenario():
        with pytest.raises(RuntimeError, match="failed both calls"):
            await asyncio.wait_for(
                run_streamed_slide_tail(
                    process_slide,
                    author_pass=author_pass,
                    coordinator=coordinator,
                    slide_count=2,
                    worker_count=2,
                ),
                timeout=2,
            )

    _run(scenario())
    assert cancelled.is_set()


def test_streamed_tail_stops_authoring_when_a_slide_fails():
    pass_cancelled = asyncio.Event()

    async def process_slide(index):
        raise ValueError("render failed")

    coordinator = AuthoredTailStreamCoordinator(2)
    coordinator.resume()

    async def author_pass():
        coordinator.publish(0)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass_cancelled.set()
            raise

    async def scenario():
        with pytest.raises(ValueError, match="render failed"):
            await asyncio.wait_for(
                run_streamed_slide_tail(
                    process_slide,
                    author_pass=author_pass,
                    coordinator=coordinator,
                    slide_count=2,
                    worker_count=2,
                ),
                timeout=2,
            )

    _run(scenario())
    assert pass_cancelled.is_set()


# --- the real pipeline --------------------------------------------------------


_VALID_SLIDE = (
    "<!DOCTYPE html><html><head></head><body>"
    "<h1 data-qid='slide-{index}.title'>slide-{index}</h1>"
    "</body></html>"
)


async def _checkpoint_database(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'authored-tail-stream.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            SQLModel.metadata.create_all,
            tables=[
                AsyncPresentationGenerationTaskModel.__table__,
                PresentationGenerationJob.__table__,
                AuthoredSlideCheckpoint.__table__,
                WorkspaceOperation.__table__,
                PublicOperationAdmission.__table__,
            ],
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoints, "async_session_maker", maker)
    monkeypatch.setenv(
        "AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "render-artifacts")
    )
    return engine, maker


async def _running_authored_job(maker, *, total_slides):
    status = AsyncPresentationGenerationTaskModel(
        status="running", message="Generating authored presentation"
    )
    job = PresentationGenerationJob(
        task_id=status.id,
        presentation_id=uuid.uuid4(),
        request_sha256="b" * 64,
        request_payload={"template": "authored"},
        state="running",
        attempt_number=1,
        attempt_token="attempt-stream",
        total_slides=total_slides,
    )
    async with maker() as session:
        session.add_all((status, job))
        await session.commit()
    return job


def _install_pipeline_fakes(monkeypatch, *, slide_count, batch, render):
    plan = SimpleNamespace(
        contents=tuple(f"slide {index}" for index in range(slide_count)),
        design_system="STREAMED DESIGN SYSTEM",
        reference_mode="style_reference",
    )

    async def unexpected_single(*_args, **_kwargs):
        raise AssertionError("the pool must not author a streamed slide itself")

    async def no_recording(**_kwargs):
        return None

    monkeypatch.setattr(pipeline, "prepare_authored_deck", lambda *a, **k: plan)
    monkeypatch.setattr(pipeline, "author_planned_slide", unexpected_single)
    monkeypatch.setattr(pipeline, "_DEFAULT_AUTHOR_PLANNED_SLIDE", unexpected_single)
    monkeypatch.setattr(pipeline, "author_planned_batch", batch)
    monkeypatch.setattr(pipeline, "render_html_to_png", render)
    monkeypatch.setattr(pipeline, "record_local_stage", no_recording)
    return plan


def _outline(count):
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"slide {index}") for index in range(count)]
    )


def _brand():
    return Brand(
        topic="streaming", language="English", primary="#2563EB", fonts="Inter"
    )


async def _drive_pipeline(job, *, seconds=10):
    return await asyncio.wait_for(
        pipeline._author_illustrate_render_pipeline(
            _outline(job.total_slides),
            _brand(),
            SimpleNamespace(reference_mode="style_reference", illustration_prompt=None),
            generation_job=job,
        ),
        timeout=seconds,
    )


def test_streaming_renders_a_slide_before_the_last_slide_is_authored(
    tmp_path, monkeypatch
):
    """The deck can only finish if a render happens mid-authoring."""

    monkeypatch.setenv("AUTHORED_TAIL_STREAMING_ENABLED", "true")
    slide_count = 4
    last_slide_released = asyncio.Event()
    order: list[str] = []
    renders = 0
    scopes: set[str | None] = set()

    async def batch(_plan, indexes, **_kwargs):
        selected = tuple(indexes)
        if selected == (slide_count - 1,):
            # The last slide's authoring returns only once the tail has
            # rendered an earlier slide. Under the phase barrier this deadlocks
            # until the test's own timeout.
            await asyncio.wait_for(last_slide_released.wait(), timeout=5)
        order.extend(f"author-{index}" for index in selected)
        return AuthoredPlannedBatchResult(
            htmls={index: _VALID_SLIDE.format(index=index) for index in selected},
            failures={},
        )

    async def render(_html):
        # The anchor stage renders its own slide once to gate it, before any
        # slide is offered to the tail; only later renders are tail renders.
        nonlocal renders
        renders += 1
        # Every render of the deck, including one started inside a pool worker
        # task, carries the deck's renderer-lease scope.
        scopes.add(current_render_deck_scope())
        if renders == 1:
            order.append("anchor-gate-render")
            return b"png"
        order.append("render")
        last_slide_released.set()
        return b"png"

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        _install_pipeline_fakes(
            monkeypatch, slide_count=slide_count, batch=batch, render=render
        )
        try:
            job = await _running_authored_job(maker, total_slides=slide_count)
            return await _drive_pipeline(job)
        finally:
            await engine.dispose()

    result = _run(scenario())

    assert len(result.pngs) == slide_count
    assert result.deck.htmls[0].count("slide-0") > 0
    assert order.index("render") < order.index(f"author-{slide_count - 1}")
    assert scopes == {str(result.evidence_attempt_id)} or len(scopes) == 1
    assert None not in scopes


def test_barrier_authors_every_slide_before_the_first_render(tmp_path, monkeypatch):
    """Knob off: today's order -- the whole deck is authored, then rendered."""

    monkeypatch.setenv("AUTHORED_TAIL_STREAMING_ENABLED", "false")
    slide_count = 3
    order: list[str] = []

    async def batch(_plan, indexes, **_kwargs):
        selected = tuple(indexes)
        order.extend(f"author-{index}" for index in selected)
        return AuthoredPlannedBatchResult(
            htmls={index: _VALID_SLIDE.format(index=index) for index in selected},
            failures={},
        )

    renders = 0

    async def render(_html):
        nonlocal renders
        renders += 1
        order.append("anchor-gate-render" if renders == 1 else "render")
        return b"png"

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        _install_pipeline_fakes(
            monkeypatch, slide_count=slide_count, batch=batch, render=render
        )
        try:
            job = await _running_authored_job(maker, total_slides=slide_count)
            return await _drive_pipeline(job)
        finally:
            await engine.dispose()

    result = _run(scenario())

    assert len(result.pngs) == slide_count
    authored = [step for step in order if step.startswith("author-")]
    assert len(authored) == slide_count
    # Every authoring call precedes the first tail render; the anchor stage's
    # own gate render is the one render that runs during authoring.
    assert order.index("render") > order.index(authored[-1])


def test_streaming_publishes_a_recovered_slide_exactly_once(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORED_TAIL_STREAMING_ENABLED", "true")
    slide_count = 3
    failed_once: set[int] = set()
    rendered: list[str] = []

    async def batch(_plan, indexes, *, recovery=False, **_kwargs):
        selected = tuple(indexes)
        if not recovery and selected == (2,):
            failed_once.update(selected)
            return AuthoredPlannedBatchResult(
                htmls={},
                failures={
                    2: AuthoredBatchFailure(ValueError("slide 2 rejected"), "retry")
                },
            )
        return AuthoredPlannedBatchResult(
            htmls={index: _VALID_SLIDE.format(index=index) for index in selected},
            failures={},
        )

    async def render(html):
        rendered.append(html)
        return b"png"

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        _install_pipeline_fakes(
            monkeypatch, slide_count=slide_count, batch=batch, render=render
        )
        try:
            job = await _running_authored_job(maker, total_slides=slide_count)
            return await _drive_pipeline(job)
        finally:
            await engine.dispose()

    result = _run(scenario())

    assert failed_once == {2}
    assert len(result.pngs) == slide_count
    assert sum("slide-2" in html for html in result.deck.htmls) == 1


def test_streaming_never_hands_the_pool_a_twice_failed_slide(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORED_TAIL_STREAMING_ENABLED", "true")
    slide_count = 3
    processed: list[int] = []

    async def batch(_plan, indexes, **_kwargs):
        selected = tuple(indexes)
        if selected == (2,):
            return AuthoredPlannedBatchResult(
                htmls={},
                failures={
                    2: AuthoredBatchFailure(ValueError("slide 2 rejected"), "retry")
                },
            )
        return AuthoredPlannedBatchResult(
            htmls={index: _VALID_SLIDE.format(index=index) for index in selected},
            failures={},
        )

    async def render(html):
        processed.append(html)
        return b"png"

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        _install_pipeline_fakes(
            monkeypatch, slide_count=slide_count, batch=batch, render=render
        )
        try:
            job = await _running_authored_job(maker, total_slides=slide_count)
            # The deck fails on the slide that lost both calls, and the pool is
            # cancelled rather than left waiting on a queue nothing will fill.
            with pytest.raises(checkpoints.AuthoredStageError) as raised:
                await _drive_pipeline(job)
            return raised.value
        finally:
            await engine.dispose()

    error = _run(scenario())

    assert error.stage == "author"
    assert error.slide_index == 2
    assert all("slide-2" not in html for html in processed)
