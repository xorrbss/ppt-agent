"""The background ``svg.chart.verify`` continuation of the v5 chart paint proof.

Runs the real ``WorkspaceOperationRunner`` and handler against SQLite; only
the capture (renderer) and the proof's validator are replaced, exactly as in
``test_svg_chart_paint_verification_v5``.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import services.database  # noqa: F401 - registers the full model graph
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.user import User
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
from services import svg_chart_paint_followup as followup
from services import svg_chart_paint_verification as verification_service
from services.generation_core.drivers.svg import chart_content_paint
from services.generation_core.drivers.svg.chart_paint_verification import (
    EVIDENCE_KEY, UNVERIFIED_REASON, current_paint_evidence,
)
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION_V4 as V4, REQUIREMENTS_VERSION_V5 as V5, SNAPSHOT_REVISION_KEY,
)
from services.workspace_operation_handler_registry import (
    WorkspaceOperationCancelled, WorkspaceOperationHandlerRegistry,
)
from services.workspace_operation_runner import WorkspaceOperationRunner
from tests.unit.test_svg_chart_paint_verification_v5 import FAILED, PASSED, _published
from utils.owner_context import reset_current_owner_id, set_current_owner_id

OWNER_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
PER_PAGE = 145  # renderer timeout (120 s) plus the client grace (25 s)
EXPORT_CONTEXT = {"font_embedding": True}


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
async def harness(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'followup.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        # The production profile: WAL, a 30 s busy timeout and foreign keys.
        services.database._configure_sqlite_connection(connection)

    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    owner_token = set_current_owner_id(OWNER_ID)
    async with maker() as session, session.begin():
        session.add(User(id=OWNER_ID, username="chart-owner", hashed_password="test-only"))

    state = SimpleNamespace(maker=maker, clock=_Clock(), renders=[], paint=dict(PASSED), refreshes=[],
                            refresh_rounds=[], capture_error=None)

    async def inputs(session, presentation, slide):
        return {"width": 1280, "height": 720}

    def validate(manifest, quality, *, capture):
        return dict(state.paint)

    async def render(source, **kwargs):
        state.renders.append(kwargs["timeout"])
        state.clock.now += PER_PAGE
        if state.capture_error is not None:
            raise state.capture_error
        return SimpleNamespace(quality={"fixture": True}, png=b"png", wrapper_html="<html/>", source_digest="d")

    async def refresh(session_factory, *, presentation_id, revision, round_number, export_context):
        state.refreshes.append((presentation_id, revision, dict(export_context)))
        state.refresh_rounds.append(round_number)
        return uuid.uuid4()

    monkeypatch.setattr(verification_service, "_render_inputs", inputs)
    monkeypatch.setattr(chart_content_paint, "validate_chart_content_paint", validate)
    monkeypatch.setattr(followup, "submit_refresh_export", refresh)
    state.render = render

    def runner(round_seconds=10_000):
        registry = WorkspaceOperationHandlerRegistry()
        registry.register(followup.OPERATION_KIND, followup.SvgChartPaintVerificationOperationHandler(
            session_factory=maker, render=render, round_seconds=round_seconds, clock=state.clock))
        return WorkspaceOperationRunner(session_factory=maker, registry=registry, max_concurrency=1,
                                        heartbeat_seconds=60, poll_seconds=0.01)
    state.runner = runner
    try:
        yield state
    finally:
        reset_current_owner_id(owner_token)
        await engine.dispose()


async def _seed(maker, *, pages, revision=V5):
    page, content = _published()
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            content="source", n_slides=pages, language="en", title="charts", mode="svg", version="v1-standard",
            lifecycle_status="published", revision=1,
            theme={"svg": {"styleSnapshot": {SNAPSHOT_REVISION_KEY: revision}}})
        session.add(presentation)
        await session.flush()
        for index in range(pages):
            session.add(SlideModel(presentation=presentation.id, layout_group="svg", layout="content", index=index,
                                   content=content, svg_content=page["svg_content"], source_format="svg",
                                   properties={"image": f"{index}.png"}))
        conversation = WorkspaceConversation(presentation_id=presentation.id)
        session.add(conversation)
        await session.flush()
        return presentation.id, conversation.id


async def _enqueue(maker, presentation_id, conversation_id, *, round_number=1, revision=1, passed=0):
    return await followup.enqueue_chart_paint_followup(
        maker, owner_id=OWNER_ID, owner_scope=str(OWNER_ID), conversation_id=conversation_id,
        presentation_id=presentation_id, revision=revision, round_number=round_number, passed_so_far=passed,
        export_context=EXPORT_CONTEXT, source_export_operation_id=uuid.uuid4())


async def _drain(runner):
    dispatched = await runner.dispatch_once()
    tasks = tuple(runner._active.values())
    if tasks:
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
    return dispatched


async def _operations(maker, presentation_id):
    async with maker() as session:
        return (await session.scalars(select(WorkspaceOperation).where(
            WorkspaceOperation.presentation_id == presentation_id,
            WorkspaceOperation.kind == followup.OPERATION_KIND,
        ).order_by(WorkspaceOperation.anchor_position).execution_options(populate_existing=True))).all()


async def _slides(maker, presentation_id):
    async with maker() as session:
        return (await session.scalars(select(SlideModel).where(SlideModel.presentation == presentation_id)
                                      .order_by(SlideModel.index).execution_options(populate_existing=True))).all()


def _verified(slide) -> bool:
    return current_paint_evidence(slide.properties, slide.content, slide.svg_content) is not None


@pytest.mark.anyio
async def test_round_verifies_pages_in_order_and_chains_one_refreshed_export(harness):
    presentation_id, conversation_id = await _seed(harness.maker, pages=3)
    assert await _enqueue(harness.maker, presentation_id, conversation_id) is not None
    assert await _drain(harness.runner()) == 1

    (operation,) = await _operations(harness.maker, presentation_id)
    assert operation.state == "succeeded"
    assert operation.result["outcome"] == "completed"
    assert (operation.result["charts_passed"], operation.result["charts_passed_total"]) == (3, 3)
    assert operation.result["pages_remaining"] == 0 and "refresh_export_operation_id" in operation.result
    slides = await _slides(harness.maker, presentation_id)
    assert all(_verified(slide) and slide.properties["image"] == f"{slide.index}.png" for slide in slides)
    assert harness.renders == [120, 120, 120]
    assert harness.refreshes == [(presentation_id, 1, EXPORT_CONTEXT)]


@pytest.mark.anyio
async def test_budget_cut_continues_in_bounded_rounds_and_refreshes_once(harness):
    presentation_id, conversation_id = await _seed(harness.maker, pages=5)
    await _enqueue(harness.maker, presentation_id, conversation_id)
    runner = harness.runner(round_seconds=2 * PER_PAGE + 10)
    for _ in range(3):
        assert await _drain(runner) == 1

    first, second, third = await _operations(harness.maker, presentation_id)
    assert [op.result["outcome"] for op in (first, second, third)] == ["continued", "continued", "completed"]
    assert [op.request_payload["round"] for op in (first, second, third)] == [1, 2, 3]
    assert [op.request_payload["passed_so_far"] for op in (first, second, third)] == [0, 2, 4]
    assert first.result["next_operation_id"] == str(second.id)
    assert third.result["charts_passed_total"] == 5
    assert all(_verified(slide) for slide in await _slides(harness.maker, presentation_id))
    assert len(harness.renders) == 5 and len(harness.refreshes) == 1
    assert await _drain(runner) == 0


@pytest.mark.anyio
async def test_rounds_stop_at_the_bound_and_still_refresh_what_passed(harness):
    pages = 2 * followup.MAX_ROUNDS + 2
    presentation_id, conversation_id = await _seed(harness.maker, pages=pages)
    await _enqueue(harness.maker, presentation_id, conversation_id)
    runner = harness.runner(round_seconds=2 * PER_PAGE + 10)
    for _ in range(followup.MAX_ROUNDS):
        assert await _drain(runner) == 1
    assert await _drain(runner) == 0

    operations = await _operations(harness.maker, presentation_id)
    assert len(operations) == followup.MAX_ROUNDS
    last = operations[-1].result
    assert last["outcome"] == "completed" and last["pages_remaining"] == 2
    assert last["charts_passed_total"] == 2 * followup.MAX_ROUNDS
    assert len(harness.refreshes) == 1
    assert await _enqueue(harness.maker, presentation_id, conversation_id,
                          round_number=followup.MAX_ROUNDS + 1) is None


@pytest.mark.anyio
async def test_failed_proofs_are_stored_and_chain_no_export(harness):
    presentation_id, conversation_id = await _seed(harness.maker, pages=2)
    harness.paint = dict(FAILED)
    await _enqueue(harness.maker, presentation_id, conversation_id)
    assert await _drain(harness.runner()) == 1
    (operation,) = await _operations(harness.maker, presentation_id)
    assert operation.result["outcome"] == "completed" and operation.result["charts_failed"] == 2
    # Failed evidence is stored (the chart stays drawn for its real reason).
    assert all(slide.properties[EVIDENCE_KEY]["paint"] == FAILED for slide in await _slides(
        harness.maker, presentation_id))
    assert harness.refreshes == []


@pytest.mark.anyio
async def test_capture_errors_store_nothing_chain_no_export_and_do_not_loop(harness):
    other_id, other_conversation = await _seed(harness.maker, pages=2)
    harness.capture_error = TimeoutError("capture exceeded")
    await _enqueue(harness.maker, other_id, other_conversation)
    runner = harness.runner()
    assert await _drain(runner) == 1
    (operation,) = await _operations(harness.maker, other_id)
    assert operation.result["outcome"] == "completed" and operation.result["pages_unverified"] == 2
    assert all(EVIDENCE_KEY not in slide.properties for slide in await _slides(harness.maker, other_id))
    assert await _drain(runner) == 0 and harness.refreshes == []


@pytest.mark.anyio
async def test_a_changed_revision_stops_the_chain_without_applying(harness):
    presentation_id, conversation_id = await _seed(harness.maker, pages=2)
    await _enqueue(harness.maker, presentation_id, conversation_id)
    async with harness.maker() as session, session.begin():
        (await session.get(PresentationModel, presentation_id)).revision = 2
    assert await _drain(harness.runner()) == 1
    (operation,) = await _operations(harness.maker, presentation_id)
    assert operation.state == "succeeded" and operation.result["outcome"] == "stale"
    assert harness.renders == [] and harness.refreshes == []
    assert not any(_verified(slide) for slide in await _slides(harness.maker, presentation_id))
    # A follow-up is never enqueued against a revision the deck has left.
    assert await _enqueue(harness.maker, presentation_id, conversation_id, round_number=2, revision=1) is None


@pytest.mark.anyio
async def test_a_revision_change_during_the_round_stops_before_the_next_page(harness):
    presentation_id, conversation_id = await _seed(harness.maker, pages=3)
    await _enqueue(harness.maker, presentation_id, conversation_id)
    render = harness.render

    async def editing_render(source, **kwargs):
        async with harness.maker() as session, session.begin():
            (await session.get(PresentationModel, presentation_id)).revision += 1
        return await render(source, **kwargs)

    registry = WorkspaceOperationHandlerRegistry()
    registry.register(followup.OPERATION_KIND, followup.SvgChartPaintVerificationOperationHandler(
        session_factory=harness.maker, render=editing_render, clock=harness.clock))
    runner = WorkspaceOperationRunner(session_factory=harness.maker, registry=registry, max_concurrency=1,
                                      heartbeat_seconds=60, poll_seconds=0.01)
    assert await _drain(runner) == 1
    (operation,) = await _operations(harness.maker, presentation_id)
    assert operation.result["outcome"] == "stale"
    assert len(harness.renders) == 1 and harness.refreshes == []


@pytest.mark.anyio
async def test_one_follow_up_in_flight_per_presentation_and_one_per_round(harness):
    presentation_id, conversation_id = await _seed(harness.maker, pages=1)
    first = await _enqueue(harness.maker, presentation_id, conversation_id)
    assert first is not None
    assert await _enqueue(harness.maker, presentation_id, conversation_id) is None
    assert await _enqueue(harness.maker, presentation_id, conversation_id, round_number=2) is None
    assert await _drain(harness.runner()) == 1
    # The finished round 1 of this revision is never enqueued again (a later
    # export of the same revision included).
    assert await _enqueue(harness.maker, presentation_id, conversation_id) is None
    assert len(await _operations(harness.maker, presentation_id)) == 1


@pytest.mark.anyio
async def test_queued_user_work_makes_the_round_yield_to_the_next_round(harness):
    presentation_id, conversation_id = await _seed(harness.maker, pages=2)
    await _enqueue(harness.maker, presentation_id, conversation_id)
    async with harness.maker() as session, session.begin():
        conversation = await session.get(WorkspaceConversation, conversation_id)
        position = conversation.next_position
        conversation.next_position += 1
        session.add(WorkspaceOperation(
            conversation_id=conversation_id, presentation_id=presentation_id, anchor_position=position,
            kind="slide.edit", state="queued", idempotency_key_hash="u" * 64, request_hash="r" * 64,
            request_payload={}, next_attempt_at=None))
    assert await _drain(harness.runner()) == 1
    first, second = await _operations(harness.maker, presentation_id)
    assert first.result["outcome"] == "continued" and first.result["pages_remaining"] == 2
    assert second.state == "queued" and second.request_payload["round"] == 2
    assert harness.renders == []


@pytest.mark.anyio
async def test_cancellation_stops_the_round_without_continuing_or_exporting(harness):
    presentation_id, conversation_id = await _seed(harness.maker, pages=3)
    operation_id = await _enqueue(harness.maker, presentation_id, conversation_id)
    (operation,) = await _operations(harness.maker, presentation_id)
    checks = []

    class Context:
        claim = SimpleNamespace(operation_id=operation_id, owner_id=OWNER_ID, owner_scope=str(OWNER_ID),
                                conversation_id=conversation_id, presentation_id=presentation_id,
                                request_payload=operation.request_payload)

        async def raise_if_cancelled(self):
            checks.append(len(harness.renders))
            if harness.renders:
                raise WorkspaceOperationCancelled()

        async def extend_lease(self, *, seconds):
            return True

        async def progress(self, **kwargs):
            return True

    handler = followup.SvgChartPaintVerificationOperationHandler(
        session_factory=harness.maker, render=harness.render, clock=harness.clock)
    with pytest.raises(WorkspaceOperationCancelled):
        await handler.execute(Context())
    assert len(harness.renders) == 1 and harness.refreshes == []
    assert len(await _operations(harness.maker, presentation_id)) == 1
    # Evidence of the page verified before the cancellation is valid and kept.
    assert [_verified(slide) for slide in await _slides(harness.maker, presentation_id)] == [True, False, False]


@pytest.mark.anyio
async def test_earlier_revisions_never_enqueue_and_are_not_verified(harness):
    presentation_id, conversation_id = await _seed(harness.maker, pages=1, revision=V4)
    claim = SimpleNamespace(owner_id=OWNER_ID, owner_scope=str(OWNER_ID), conversation_id=conversation_id,
                            presentation_id=presentation_id, expected_revision=1, operation_id=uuid.uuid4(),
                            request_payload={"context": {}})
    # A non-v5 export computes no chart paint fallbacks at all.
    assert await followup.enqueue_after_export(harness.maker, claim=claim, chart_paint_fallbacks=None) is None
    # A v5 export whose drawn charts all have evidence (failed) has nothing left.
    failed = [[{"id": "native-chart-sales", "reason": "native_chart_paint_failed:x"}]]
    assert await followup.enqueue_after_export(harness.maker, claim=claim, chart_paint_fallbacks=failed) is None
    assert await followup.enqueue_after_export(harness.maker, claim=claim, chart_paint_fallbacks=[[]]) is None
    assert await _operations(harness.maker, presentation_id) == []
    # Even a stray round on such a deck verifies nothing.
    await _enqueue(harness.maker, presentation_id, conversation_id)
    assert await _drain(harness.runner()) == 1
    (operation,) = await _operations(harness.maker, presentation_id)
    assert operation.result["outcome"] == "not_applicable" and harness.renders == []


@pytest.mark.anyio
async def test_export_with_unverified_charts_enqueues_round_one_with_its_options(harness):
    presentation_id, conversation_id = await _seed(harness.maker, pages=2)
    export_id = uuid.uuid4()
    claim = SimpleNamespace(owner_id=OWNER_ID, owner_scope=str(OWNER_ID), conversation_id=conversation_id,
                            presentation_id=presentation_id, expected_revision=1, operation_id=export_id,
                            request_payload={"context": {"font_embedding": False}, "quality_policy": "interactive-v1",
                                             "account_deletion_generation": 0})
    fallbacks = [[], [{"id": "native-chart-sales", "reason": UNVERIFIED_REASON}]]
    operation_id = await followup.enqueue_after_export(harness.maker, claim=claim, chart_paint_fallbacks=fallbacks)
    (operation,) = await _operations(harness.maker, presentation_id)
    assert operation.id == operation_id and operation.state == "queued" and operation.max_attempts == 1
    assert operation.expected_revision is None
    payload = operation.request_payload
    assert (payload["source_revision"], payload["round"], payload["passed_so_far"]) == (1, 1, 0)
    assert payload["export_context"] == {"font_embedding": False, "quality_policy": "interactive-v1"}
    assert payload["source_export_operation_id"] == str(export_id)
    # Another export while round 1 is still queued starts nothing.
    assert await followup.enqueue_after_export(harness.maker, claim=claim, chart_paint_fallbacks=fallbacks) is None


@pytest.mark.anyio
async def test_refresh_export_is_one_export_command_at_the_verified_revision(monkeypatch):
    from services import workspace_service

    submitted = []

    async def create(session, *, presentation_id, request):
        submitted.append((presentation_id, request))
        return SimpleNamespace(id=uuid.uuid4()), False

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def commit(self):
            pass

    monkeypatch.setattr(workspace_service, "create_workspace_command", create)
    presentation_id = uuid.uuid4()
    operation_id = await followup.submit_refresh_export(
        lambda: Session(), presentation_id=presentation_id, revision=7, round_number=2,
        export_context={"font_embedding": True})
    assert operation_id is not None
    ((target, request),) = submitted
    assert target == presentation_id and request.command_type == "export.pptx"
    assert request.expected_revision == 7 and request.context == {"font_embedding": True}
    assert request.idempotency_key == followup.refresh_export_idempotency_key(
        presentation_id=presentation_id, revision=7, round_number=2)

    async def refused(session, *, presentation_id, request):
        raise workspace_service.WorkspaceServiceError("public_admission_capacity", status_code=429)

    monkeypatch.setattr(workspace_service, "create_workspace_command", refused)
    assert await followup.submit_refresh_export(
        lambda: Session(), presentation_id=presentation_id, revision=7, round_number=1, export_context={}) is None


def test_export_enqueues_only_after_publishing_a_v5_pptx_of_stored_pages():
    import inspect

    from services import svg_export_operation

    source = inspect.getsource(svg_export_operation.execute_svg_workspace_export)
    # Fallbacks exist only for a v5 PPTX; every other export never reaches the hook.
    assert ('if fmt == "pptx" and deck_requirements_revision(presentation) == REQUIREMENTS_VERSION_V5:'
            in source)
    hook = source.index("enqueue_after_export(session_factory")
    assert source.index("revision = await publish_workspace_export_artifact(") < hook
    guard = source.rindex("if (chart_paint_fallbacks is not None and header_authority is None", 0, hook)
    assert "measured_source_preservation is None" in source[guard:hook]
    assert hook < source.index("return WorkspaceOperationResult(", hook)


def _export_claim(presentation_id, conversation_id, operation_id=None):
    return SimpleNamespace(owner_id=OWNER_ID, owner_scope=str(OWNER_ID), conversation_id=conversation_id,
                           presentation_id=presentation_id, expected_revision=1,
                           operation_id=operation_id or uuid.uuid4(),
                           request_payload={"context": {}, "account_deletion_generation": 0})


FALLBACKS = [[{"id": "native-chart-sales", "reason": UNVERIFIED_REASON}]]


@pytest.mark.anyio
async def test_a_later_manual_export_at_the_same_revision_continues_with_the_next_round(harness):
    """Seen live: a second manual export of the same revision never resumed the
    background verification, because round 1 of that revision was already used."""
    presentation_id, conversation_id = await _seed(harness.maker, pages=2)
    harness.capture_error = TimeoutError("capture cut off")
    first = await followup.enqueue_after_export(
        harness.maker, claim=_export_claim(presentation_id, conversation_id), chart_paint_fallbacks=FALLBACKS)
    assert await _drain(harness.runner()) == 1
    second = await followup.enqueue_after_export(
        harness.maker, claim=_export_claim(presentation_id, conversation_id), chart_paint_fallbacks=FALLBACKS)
    assert first is not None and second is not None and second != first
    assert [op.request_payload["round"] for op in await _operations(harness.maker, presentation_id)] == [1, 2]


@pytest.mark.anyio
async def test_rounds_after_manual_exports_stay_within_the_bound(harness):
    presentation_id, conversation_id = await _seed(harness.maker, pages=1)
    harness.capture_error = TimeoutError("capture cut off")
    runner = harness.runner()
    for _ in range(followup.MAX_ROUNDS):
        assert await followup.enqueue_after_export(
            harness.maker, claim=_export_claim(presentation_id, conversation_id), chart_paint_fallbacks=FALLBACKS)
        assert await _drain(runner) == 1
    assert await followup.enqueue_after_export(
        harness.maker, claim=_export_claim(presentation_id, conversation_id), chart_paint_fallbacks=FALLBACKS) is None
    assert len(await _operations(harness.maker, presentation_id)) == followup.MAX_ROUNDS


@pytest.mark.anyio
async def test_the_chains_own_refreshed_export_starts_no_new_chain(harness):
    from services.workspace_service import workspace_idempotency_hash
    presentation_id, conversation_id = await _seed(harness.maker, pages=2)
    refresh_id = uuid.uuid4()
    async with harness.maker() as session, session.begin():
        session.add(WorkspaceOperation(
            id=refresh_id, owner_id=OWNER_ID, owner_scope=str(OWNER_ID), conversation_id=conversation_id,
            presentation_id=presentation_id, anchor_position=900, kind="export.pptx", state="succeeded",
            idempotency_key_hash=workspace_idempotency_hash(followup.refresh_export_idempotency_key(
                presentation_id=presentation_id, revision=1, round_number=3)),
            request_hash="0" * 64, request_payload={}))
    assert await followup.enqueue_after_export(
        harness.maker, claim=_export_claim(presentation_id, conversation_id, refresh_id),
        chart_paint_fallbacks=FALLBACKS) is None
    assert await _operations(harness.maker, presentation_id) == []


@pytest.mark.anyio
async def test_export_verifies_the_cheapest_capture_first_and_keeps_deck_order(harness, monkeypatch):
    """Seen live: a 29-target chart first in the deck always ran out of time and
    used the export's whole verification budget."""
    presentation_id, _conversation_id = await _seed(harness.maker, pages=3)
    slides = await _slides(harness.maker, presentation_id)
    async with harness.maker() as session:
        presentation = await session.get(PresentationModel, presentation_id)
    costs = {slides[0].id: 29, slides[1].id: 14, slides[2].id: 14}
    monkeypatch.setattr(verification_service, "verification_cost", lambda slide: costs[slide.id])
    order = []

    async def verify(session, presentation, slide, **kwargs):
        order.append(slide.index)
        return None

    monkeypatch.setattr(verification_service, "verify_slide_chart_paint", verify)
    result = await verification_service.deck_chart_paint_fallbacks(
        harness.maker, presentation=presentation, slides=slides, remaining_seconds=lambda: 10_000)
    assert order == [1, 2, 0]
    assert len(result) == 3 and all(page for page in result)


def test_verification_cost_counts_the_proofs_capture_targets():
    page, content = _published()
    manifest = verification_service.verification_manifest(content, page["svg_content"])
    expected = sum(len(chart["compositionGroup"]["primitiveQids"])
                   + sum(len(glyph["ranges"]) for glyph in chart["glyphs"]) for chart in manifest["charts"])
    slide = SimpleNamespace(content=content, svg_content=page["svg_content"])
    assert verification_service.verification_cost(slide) == expected > 0
    assert verification_service.verification_cost(SimpleNamespace(content={}, svg_content="<svg/>")) == 0
