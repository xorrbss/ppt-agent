"""The job lane hands a twice-rejected slide to the rescue instead of dying.

``rescue_author_stage`` was reachable only from ``process_slide``'s authoring
branch, which the production job lane never reaches for a slide the
pre-authoring pass could not author: that pass failed the stage for every
unresolved slide and raised the first one. So on the shipped batch path a
persistently failing slide still killed the deck with
``AUTHORED_PUBLISH_FLAGGED_SLIDES`` on.

These cases drive the real ``_author_illustrate_render_pipeline`` against a
scratch checkpoint database with the provider batch call, the third rescue call
and Chromium replaced, on both the streaming path and the phase barrier, so the
claim is made about the shipped code path rather than a model of it.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.authored_header_contract import default_header_contract
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
from services import authored_author_stage_rescue as rescue_mod
from services import authored_checkpoint_service as checkpoints
from services import authored_presentation_pipeline as pipeline
from services.authored_author_stage_deferral import (
    author_failure_is_rescuable,
    defer_rescuable_author_failures,
    resumed_author_budget_is_rescuable,
)
from services.authored_composition_policy import CompositionProfile
from utils.llm_calls.author_deck import (
    AuthoredBatchFailure,
    AuthoredPlannedBatchResult,
    AuthoredProviderBudgetExhausted,
)
from utils.llm_calls.author_slide import Brand
from utils.renderer_isolation import RendererPolicyError

_ORDINARY = CompositionProfile("ordinary", "authored", True, True, 1)
_FAILING_SLIDE = 2
_SLIDE_COUNT = 3
_CONTENT = "분기 실적 요약\n매출 120억 원\n영업이익 18억 원"
_VALID_SLIDE = (
    "<!DOCTYPE html><html><head></head><body>"
    "<h1 data-qid='slide-{index}.title'>slide-{index}</h1>"
    "</body></html>"
)
_RESCUED_HTML = (
    "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
    "<body><h1>RESCUED</h1></body></html>"
)


def _run(coro):
    return asyncio.run(coro)


def _header_contract_rejection():
    """What the batch lane records when both candidates broke the header."""

    error = ValueError("header contract violated: header_band_missing")
    error.rejected_html = "<html><body>a rejected candidate</body></html>"
    return AuthoredBatchFailure(
        error, "Restore the deck's header band.", error.rejected_html
    )


def _renderer_policy_rejection():
    error = RendererPolicyError("CSS property is not allowed: content: 'x'")
    return AuthoredBatchFailure(error, "Remove the forbidden declaration.", "<html/>")


# --- the classification -------------------------------------------------------


def test_a_rejected_candidate_is_the_dead_end_the_rescue_answers():
    assert author_failure_is_rescuable(_header_contract_rejection()) is True
    assert (
        author_failure_is_rescuable(
            AuthoredBatchFailure(
                AuthoredProviderBudgetExhausted("authored_x_budget_exhausted", "x"), "h"
            )
        )
        is True
    )


def test_a_renderer_policy_refusal_and_a_transport_failure_are_not_rescuable():
    assert author_failure_is_rescuable(_renderer_policy_rejection()) is False
    # A refusal wrapped by the pipeline's own stage envelope stays refused.
    wrapped = checkpoints.AuthoredStageError(
        slide_index=0,
        stage="author",
        code="authored_slide_author_failed",
        cause=RendererPolicyError("CSS property is not allowed: content: 'x'"),
        retryable=False,
    )
    assert author_failure_is_rescuable(AuthoredBatchFailure(wrapped, "h")) is False
    # Nothing came back from the provider: the operation's own retry owns it.
    assert (
        author_failure_is_rescuable(AuthoredBatchFailure(TimeoutError("timed out"), ""))
        is False
    )
    # A terminal public-provider admission is the provider's answer, not the
    # slide's.
    public = checkpoints.AuthoredStageError(
        slide_index=0,
        stage="author",
        code="public_provider_quota_exhausted",
        retryable=False,
    )
    assert author_failure_is_rescuable(AuthoredBatchFailure(public, "h", "<html/>")) is False


def test_an_inactive_predicate_defers_nothing():
    failures = {_FAILING_SLIDE: _header_contract_rejection()}
    assert (
        defer_rescuable_author_failures(
            _SLIDE_COUNT,
            successful=set(),
            failures=failures,
            attempt_counts={_FAILING_SLIDE: 2},
            profile=None,
            reference_mode="style_reference",
            source_reflow=False,
        )
        == {}
    )
    deferred = defer_rescuable_author_failures(
        _SLIDE_COUNT,
        successful=set(),
        failures=failures,
        attempt_counts={_FAILING_SLIDE: 2},
        profile=_ORDINARY,
        reference_mode="style_reference",
        source_reflow=False,
    )
    assert set(deferred) == {_FAILING_SLIDE}
    assert deferred[_FAILING_SLIDE].provider_attempt_count == 2


def test_a_resumed_slide_keeps_a_recorded_refusal_fail_closed():
    # A deferred slide is never failed, so its checkpoint carries the ordinals
    # and nothing else.
    assert resumed_author_budget_is_rescuable({"provider_attempt_count": 2}) is True
    assert resumed_author_budget_is_rescuable(None) is True
    assert (
        resumed_author_budget_is_rescuable(
            {"code": "authored_slide_author_failed", "exception_type": "RendererPolicyError"}
        )
        is False
    )
    assert (
        resumed_author_budget_is_rescuable({"code": "public_provider_quota_exhausted"})
        is False
    )
    assert resumed_author_budget_is_rescuable({"retryable": True}) is False


# --- the real pipeline --------------------------------------------------------


async def _checkpoint_database(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'authored-author-deferral.db'}"
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
        request_sha256="c" * 64,
        request_payload={"template": "authored"},
        state="running",
        attempt_number=1,
        attempt_token="attempt-deferral",
        total_slides=total_slides,
    )
    async with maker() as session:
        session.add_all((status, job))
        await session.commit()
    return job


def _plan(contract):
    """The stand-in deck plan, carrying what the batch lane and D7 both read."""

    return SimpleNamespace(
        contents=tuple(_CONTENT for _ in range(_SLIDE_COUNT)),
        roles=tuple("CONTENT" for _ in range(_SLIDE_COUNT)),
        design_system="DEFERRED DESIGN SYSTEM",
        brand=_brand(),
        page_number_mode="none",
        reference_mode="style_reference",
        header_contract_at=lambda _index: contract,
    )


def _brand():
    return Brand(topic="주제", language="Korean", primary="#2563EB", fonts="Noto Sans KR")


def _outline(count):
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=_CONTENT) for _ in range(count)]
    )


def _batch_that_never_authors_one_slide(*, failing=_FAILING_SLIDE, failure=None):
    """Both of the failing slide's calls are spent and both are rejected."""

    calls: list[tuple[int, ...]] = []

    async def batch(
        _plan,
        indexes,
        *,
        recovery=False,
        attempt_observers=None,
        attempt_reservers=None,
        **_kwargs,
    ):
        selected = tuple(indexes)
        calls.append(selected)
        ordinal = 2 if recovery else 1
        for index in selected:
            reserver = (attempt_reservers or {}).get(index)
            if reserver is not None:
                await reserver(ordinal)
            observer = (attempt_observers or {}).get(index)
            if observer is not None:
                observer(ordinal)
        return AuthoredPlannedBatchResult(
            htmls={
                index: _VALID_SLIDE.format(index=index)
                for index in selected
                if index != failing
            },
            failures=(
                {failing: (failure or _header_contract_rejection())}
                if failing in selected
                else {}
            ),
        )

    return batch, calls


def _install_pipeline_fakes(monkeypatch, *, plan, batch, rescue):
    async def render(_html):
        return b"png"

    async def no_recording(**_kwargs):
        return None

    async def unexpected_single(*_args, **_kwargs):
        raise AssertionError("the pool must not author a pre-authored slide itself")

    monkeypatch.setattr(pipeline, "prepare_authored_deck", lambda *a, **k: plan)
    monkeypatch.setattr(pipeline, "author_planned_slide", unexpected_single)
    monkeypatch.setattr(pipeline, "_DEFAULT_AUTHOR_PLANNED_SLIDE", unexpected_single)
    monkeypatch.setattr(pipeline, "author_planned_batch", batch)
    monkeypatch.setattr(pipeline, "render_html_to_png", render)
    monkeypatch.setattr(pipeline, "record_local_stage", no_recording)
    monkeypatch.setattr(rescue_mod, "author_planned_slide", rescue)
    monkeypatch.setattr(
        pipeline, "get_cached_browser_metrics", lambda _html: {"telemetry": "ok"}
    )
    monkeypatch.setattr(
        pipeline,
        "inspect_authored_dom",
        lambda *a, **k: SimpleNamespace(
            telemetry_status="available", issues=[], fit_summary=None
        ),
    )
    monkeypatch.setattr(pipeline, "blocking_safety_issues", lambda _report: ())


def _drive(tmp_path, monkeypatch, *, batch, rescue, contract=None, profile=_ORDINARY):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        _install_pipeline_fakes(
            monkeypatch, plan=_plan(contract), batch=batch, rescue=rescue
        )
        try:
            job = await _running_authored_job(maker, total_slides=_SLIDE_COUNT)
            return await asyncio.wait_for(
                pipeline._author_illustrate_render_pipeline(
                    _outline(_SLIDE_COUNT),
                    _brand(),
                    SimpleNamespace(
                        reference_mode="style_reference", illustration_prompt=None
                    ),
                    generation_job=job,
                    composition_profile=profile,
                ),
                timeout=20,
            )
        finally:
            await engine.dispose()

    return _run(scenario())


def _streaming(monkeypatch):
    monkeypatch.setenv("AUTHORED_TAIL_STREAMING_ENABLED", "true")


def _barrier(monkeypatch):
    monkeypatch.setenv("AUTHORED_TAIL_STREAMING_ENABLED", "false")
    monkeypatch.setenv("AUTHORED_WAVE_BARRIER_DISABLED", "false")


@pytest.mark.parametrize("lane", (_streaming, _barrier))
def test_a_twice_rejected_slide_is_rescued_and_the_deck_publishes(
    tmp_path, monkeypatch, lane
):
    lane(monkeypatch)
    contract = default_header_contract(primary="#2563EB")
    batch, calls = _batch_that_never_authors_one_slide()
    rescued: list[int] = []

    async def rescue(_plan, index, **_kwargs):
        rescued.append(index)
        return _RESCUED_HTML

    result = _drive(tmp_path, monkeypatch, batch=batch, rescue=rescue, contract=contract)

    # The failing slide reached the rescue exactly once, and no other slide did.
    assert rescued == [_FAILING_SLIDE]
    assert len(result.pngs) == _SLIDE_COUNT
    assert "RESCUED" in result.deck.htmls[_FAILING_SLIDE]
    for index in range(_SLIDE_COUNT):
        if index != _FAILING_SLIDE:
            assert f"slide-{index}" in result.deck.htmls[index]
    # A recovered slide is not a flagged slide, and it is billed for all three.
    assert result.author_flagged_slides == ()
    assert result.author_provider_attempt_counts[_FAILING_SLIDE] == 3
    # Two batch calls were spent on the failing slide before the rescue.
    assert sum(_FAILING_SLIDE in selected for selected in calls) == 2


@pytest.mark.parametrize("lane", (_streaming, _barrier))
def test_a_twice_rejected_slide_publishes_flagged_when_the_rescue_fails(
    tmp_path, monkeypatch, lane
):
    lane(monkeypatch)
    contract = default_header_contract(primary="#2563EB")
    batch, _calls = _batch_that_never_authors_one_slide()

    async def rescue(_plan, _index, **_kwargs):
        raise AuthoredProviderBudgetExhausted(
            "authored_header_contract_budget_exhausted", "still wrong"
        )

    result = _drive(tmp_path, monkeypatch, batch=batch, rescue=rescue, contract=contract)

    assert len(result.pngs) == _SLIDE_COUNT
    assert result.author_flagged_slides == (
        {
            "index": _FAILING_SLIDE,
            "reason": "author:attempts_exhausted",
            "remakeAttempted": True,
            "remakeOutcome": "author_rescue_budget_exhausted",
        },
    )
    # The deterministic placeholder carries the slide's own outline.
    assert "매출 120억 원" in result.deck.htmls[_FAILING_SLIDE]


@pytest.mark.parametrize("lane", (_streaming, _barrier))
def test_the_knob_off_keeps_todays_failed_deck(tmp_path, monkeypatch, lane):
    lane(monkeypatch)
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "false")
    batch, _calls = _batch_that_never_authors_one_slide()

    async def forbidden_rescue(*_args, **_kwargs):
        raise AssertionError("no rescue call may be made with the knob off")

    with pytest.raises(checkpoints.AuthoredStageError) as caught:
        _drive(tmp_path, monkeypatch, batch=batch, rescue=forbidden_rescue)

    assert caught.value.stage == "author"
    assert caught.value.slide_index == _FAILING_SLIDE
    assert caught.value.code == "authored_slide_author_failed"


@pytest.mark.parametrize("lane", (_streaming, _barrier))
def test_a_renderer_policy_refusal_still_fails_closed(tmp_path, monkeypatch, lane):
    lane(monkeypatch)
    batch, _calls = _batch_that_never_authors_one_slide(
        failure=_renderer_policy_rejection()
    )

    async def forbidden_rescue(*_args, **_kwargs):
        raise AssertionError("a renderer policy refusal is a security boundary")

    with pytest.raises(checkpoints.AuthoredStageError) as caught:
        _drive(
            tmp_path,
            monkeypatch,
            batch=batch,
            rescue=forbidden_rescue,
            contract=default_header_contract(primary="#2563EB"),
        )

    assert caught.value.stage == "author"
    assert caught.value.slide_index == _FAILING_SLIDE
