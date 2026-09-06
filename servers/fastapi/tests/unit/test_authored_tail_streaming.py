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
from sqlmodel import SQLModel, select

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
from services.authored_checkpoint_html import checkpoint_stage_html
from services import authored_presentation_pipeline as pipeline
from services.authored_composition_policy import ensure_authored_semantic_roles
from services.authored_batch_policy import new_authored_batch_policy_state
from services.authored_preauthor_scan import scan_preauthor_checkpoints
from services.authored_slide_ledger import assert_authored_ledger_disjoint
from services.authored_tail_streaming import (
    AuthoredTailStreamCoordinator,
    run_authored_recovery_pass,
    run_streamed_slide_tail,
)
from utils.authored_dom_quality import ensure_authored_qids
from utils.authored_semantic_role_repair import canonicalize_authored_roles
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


_MANY_HEADING_SLIDE = (
    "<!DOCTYPE html><html><head></head><body>"
    "<h1 data-qid='slide-0.title' style='font-size:34px'>목차</h1>"
    + "".join(
        f"<h2 data-qid='slide-0.chapter{index}' style='font-size:24px'>"
        f"제 {index} 장</h2>"
        f"<p data-qid='slide-0.summary{index}'>이 장에서 다루는 내용을 설명합니다.</p>"
        for index in range(6)
    )
    + "</body></html>"
)


def test_one_title_owner_reaches_the_checkpoint_the_ledger_and_the_gate(
    tmp_path, monkeypatch
):
    """Bench runs 30/31: the demotion reached the gate's copy only.

    ``ensure_authored_semantic_roles`` promoted every ``h1``/``h2`` to ``title``
    and only the pipeline's pre-gate normaliser demoted the extras, so the
    document written to ``illustrated_html`` and handed to the evidence ledger
    still carried one title per heading.  The stamper now selects the single
    title owner itself, so the three documents are byte-identical.
    """

    monkeypatch.setenv("AUTHORED_TAIL_STREAMING_ENABLED", "false")

    async def batch(_plan, indexes, **_kwargs):
        return AuthoredPlannedBatchResult(
            htmls={index: _MANY_HEADING_SLIDE for index in indexes}, failures={}
        )

    async def render(_html):
        return b"png"

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        _install_pipeline_fakes(
            monkeypatch, slide_count=1, batch=batch, render=render
        )
        try:
            job = await _running_authored_job(maker, total_slides=1)
            result = await _drive_pipeline(job)
            async with maker() as session:
                rows = await session.execute(
                    select(AuthoredSlideCheckpoint).where(
                        AuthoredSlideCheckpoint.job_id == job.id,
                        AuthoredSlideCheckpoint.slide_index == 0,
                    )
                )
                stored = checkpoint_stage_html(rows.scalars().one())
            return result, stored
        finally:
            await engine.dispose()

    result, checkpointed = _run(scenario())

    gated = result.deck.htmls[0]
    ledger = result.evidence_ledger.latest(result.evidence_attempt_id, 0).html
    assert gated.count('data-semantic-role="title"') == 1
    assert 'data-qid=\'slide-0.title\' style=\'font-size:34px\' data-semantic-role="title"' in gated
    assert gated.count('data-semantic-role="supporting"') == 6
    # The exact bytes, not just the same title count.
    assert checkpointed == gated
    assert ledger == gated
    # The repair is a safety net now: it changes nothing on this slide. Nor
    # does the bare stamper the bounded-repair evidence binding runs -- that is
    # the call that used to promote all six headings back to ``title`` after the
    # pre-gate demotion, so the published deck disagreed with the gated one.
    assert canonicalize_authored_roles(gated) == gated
    assert ensure_authored_semantic_roles(ensure_authored_qids(gated)) == gated


# --- the ledger the recovery pass plans from ----------------------------------


class _Schedule:
    """The two attributes ``run_authored_recovery_pass`` reads off a schedule."""

    def __init__(self):
        self.policy = new_authored_batch_policy_state()
        self.advanced: list[bool] = []

    async def wait_out_cooldown(self, _label):
        return 0.0

    def advance(self, _outcome, *, succeeded):
        self.advanced.append(succeeded)


def test_a_resumed_slide_recovered_by_its_one_call_leaves_the_ledger_disjoint():
    """The resumed-job lane: ``scan.resumed_recovery`` is failed, never both."""

    successful = {0}
    failures = {
        1: AuthoredBatchFailure(ValueError("prior attempt rejected slide 1"), "redo"),
        2: AuthoredBatchFailure(ValueError("prior attempt rejected slide 2"), "redo"),
    }

    async def run_batch(indexes):
        if indexes == (2,):
            raise RuntimeError("provider unavailable")
        return AuthoredPlannedBatchResult(
            htmls={index: _VALID_SLIDE.format(index=index) for index in indexes},
            failures={},
        )

    _run(
        run_authored_recovery_pass(
            [1, 2],
            schedule=_Schedule(),
            run_batch=run_batch,
            successful=successful,
            failures=failures,
        )
    )

    assert successful == {0, 1}
    assert set(failures) == {2}
    assert_authored_ledger_disjoint(successful, failures, stage="test")


def test_a_recovery_batch_never_re_fails_a_slide_the_pass_accepted():
    """A late rejection of an accepted slide costs the deck nothing."""

    successful = {0}
    failures = {2: AuthoredBatchFailure(ValueError("slide 2 rejected"), "redo")}

    async def run_batch(indexes):
        return AuthoredPlannedBatchResult(
            htmls={index: _VALID_SLIDE.format(index=index) for index in indexes},
            # The batch reports slide 0 as failed although its HTML is already
            # persisted -- the shape the anchor's failed remake produces.
            failures={0: AuthoredBatchFailure(ValueError("late"), "redo")},
        )

    _run(
        run_authored_recovery_pass(
            [2],
            schedule=_Schedule(),
            run_batch=run_batch,
            successful=successful,
            failures=failures,
        )
    )

    assert successful == {0, 2}
    assert failures == {}


def test_the_recovery_pass_refuses_a_ledger_that_already_drifted():
    """The drift is named where it happened, not inside the batch planner."""

    successful = {0, 1}
    failures = {1: AuthoredBatchFailure(ValueError("stale"), "redo")}

    async def run_batch(_indexes):
        raise AssertionError("no call may be planned from a drifted ledger")

    with pytest.raises(ValueError, match="authored slide ledger drifted"):
        _run(
            run_authored_recovery_pass(
                [1],
                schedule=_Schedule(),
                run_batch=run_batch,
                successful=successful,
                failures=failures,
            )
        )


def test_the_resumed_scan_never_classifies_a_slide_as_both():
    """A resumed job's own classification is the recovery pass's first input."""

    rows = {
        0: SimpleNamespace(author_status="completed", author_html="<html></html>", error=None),
        1: SimpleNamespace(
            author_status="failed",
            author_html=None,
            error={"provider_attempt_count": 1, "code": "authored_slide_author_failed"},
        ),
        2: SimpleNamespace(
            author_status="failed",
            author_html=None,
            error={"provider_attempt_count": 2},
        ),
        3: None,
    }

    async def read_checkpoint(index):
        return rows[index]

    scan = _run(
        scan_preauthor_checkpoints(
            4,
            read_checkpoint=read_checkpoint,
            attempt_count=lambda checkpoint: int(
                (getattr(checkpoint, "error", None) or {}).get(
                    "provider_attempt_count", 0
                )
            ),
        )
    )

    assert scan.successful == {0}
    assert scan.initial == [3]
    assert scan.resumed_recovery == [1]
    assert set(scan.failures) == {1, 2}
    assert_authored_ledger_disjoint(scan.successful, scan.failures, stage="scan")
