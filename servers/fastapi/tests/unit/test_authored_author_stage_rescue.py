"""D7 at the author stage: a third simplified attempt, then a flagged placeholder.

The pipeline-level cases drive the real ``_author_illustrate_render_pipeline``
with a stubbed provider and Chromium, because the decision under test
is a slide outcome inside that pipeline: published (unflagged or flagged)
versus ``authored_slide_author_failed``. The deck-level case reuses the D7 harness to prove the
author-stage flag reaches the same published ``flaggedSlides`` receipt the
gate-stage flags do.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from models.authored_header_contract import default_header_contract
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.presentation import PresentationModel
from services import authored_author_stage_rescue as rescue_mod
from services import authored_presentation_pipeline as pipeline_mod
from services import authored_presentation_service as authored_service
from services import authored_terminal_slide_fallback as terminal_mod
from services.authored_admission_capture import canonical_capture_html
from services.authored_composition_policy import CompositionProfile
from services.authored_generation_observability import (
    log_authored_slide_failure_observation,
)
from services.authored_generation_timing_receipt_e4 import flagged_slide_entries
from services.authored_local_fit_policy import POLICY_REVISION, html_digest
from services.authored_presentation_contracts import (
    AuthoredGenerationPipelineResult,
)
from services.source_content.browser_readback import (
    BrowserSourceReadbackResult,
)
from utils.authored_header_validation import validate_header_contract
from utils.authored_semantic_role_repair import canonicalize_authored_roles
from utils.llm_calls.author_deck import (
    AuthoredDeckResult,
    AuthoredProviderBudgetExhausted,
)
from utils.llm_calls.author_slide import (
    Brand,
    is_fallback_slide_html,
    validate_authored_slide_html,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id
from utils.provider_idempotency import ProviderOutcomeError
from tests.unit.test_authored_terminal_slide_fallback import _pop_slide_ids

_ORDINARY = CompositionProfile("ordinary", "authored", True, True, 1)
_STYLE = SimpleNamespace(id="default", illustration_prompt=None)
_RESCUED_HTML = (
    "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
    "<body><h1>RESCUED</h1></body></html>"
)
_OUTLINE_TEXT = "분기 실적 요약\n매출 120억 원\n영업이익 18억 원"


def _fit_receipts(htmls):
    """One local-fit receipt per slide, as the real pipeline always returns.

    A pipeline that renders a deck also fits it, so generation requires one
    receipt per slide (``local_fit_applies`` in
    ``services/authored_presentation_generation.py``); a stub that returns none
    fails the deck with ``authored_local_fit_evidence_incomplete`` before the
    behaviour under test is reached.
    """

    return tuple(
        {
            "policy_revision": POLICY_REVISION,
            "finalHtmlDigest": html_digest(canonicalize_authored_roles(html)),
            "qualityStatus": "passed",
            "unresolvedReasons": [],
            "changed": False,
        }
        for html in htmls
    )


def _run(coro):
    return asyncio.run(coro)


def _brand():
    return Brand(topic="주제", language="Korean", primary="#2563EB", fonts="Noto Sans KR")


def _outline(count, content=_OUTLINE_TEXT):
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=content) for _ in range(count)]
    )


def _budget_exhausted():
    return AuthoredProviderBudgetExhausted(
        "authored_header_contract_budget_exhausted",
        "header contract violated: header_band_missing, header_title_missing",
    )


def _stub_plan(count=1, *, contract=None):
    """The stand-in deck plan the pipeline's own tests use, plus what D7 reads."""

    return SimpleNamespace(
        contents=tuple(_OUTLINE_TEXT for _ in range(count)),
        roles=tuple("CLOSING" for _ in range(count)),
        design_system="PIPELINED DESIGN SYSTEM",
        brand=_brand(),
        page_number_mode="none",
        header_contract_at=lambda _index: contract,
    )


def _install_pipeline_stubs(monkeypatch):
    """Chromium and the deterministic gate; the provider is per-test."""

    async def fake_render(_html):
        return b"png"

    monkeypatch.setattr(pipeline_mod, "render_html_to_png", fake_render)
    monkeypatch.setattr(
        pipeline_mod, "get_cached_browser_metrics", lambda _html: {"telemetry": "ok"}
    )
    monkeypatch.setattr(
        pipeline_mod,
        "inspect_authored_dom",
        lambda *args, **kwargs: SimpleNamespace(
            telemetry_status="available", issues=[], fit_summary=None
        ),
    )
    monkeypatch.setattr(pipeline_mod, "blocking_safety_issues", lambda _report: ())


def _capture_cooldown_waits(monkeypatch):
    waits: list[float] = []
    now = [100.0]
    schedule_type = pipeline_mod.AuthoredWaveSchedule

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(
        pipeline_mod,
        "AuthoredWaveSchedule",
        lambda policy: schedule_type(
            policy, clock=lambda: now[0], sleep=sleep
        ),
    )
    return waits


def _run_pipeline(monkeypatch, *, plan, first_pass, rescue, slides=1, **kwargs):
    """One pipeline run whose two author budgets and rescue call are stubbed."""

    _install_pipeline_stubs(monkeypatch)
    monkeypatch.setattr(pipeline_mod, "prepare_authored_deck", lambda *a, **k: plan)
    monkeypatch.setattr(pipeline_mod, "author_planned_slide", first_pass)
    monkeypatch.setattr(rescue_mod, "author_planned_slide", rescue)
    return _run(
        pipeline_mod._author_illustrate_render_pipeline(
            _outline(slides),
            _brand(),
            _STYLE,
            composition_profile=_ORDINARY,
            **kwargs,
        )
    )


def _first_pass_that_spends_its_budget(calls):
    async def fake_author(
        selected_plan,
        index,
        *,
        attempt_observer=None,
        provider_attempt_offset=0,
        **_kwargs,
    ):
        calls.append(("first-pass", index))
        if attempt_observer is not None:
            attempt_observer(1)
            attempt_observer(2)
        raise _budget_exhausted()

    return fake_author


# --- the simplified brief ----------------------------------------------------


def test_simplified_brief_leads_with_the_header_contract_and_the_frame():
    contract = default_header_contract(primary="#2563EB")

    brief = rescue_mod.simplified_author_brief(role="CLOSING", contract=contract)

    header_rule, layout_rule, frame_rule = brief.split("\n")[1:4]
    # The contract is stated first, as the contract's own DOM markers.
    assert header_rule.startswith("1. NON-NEGOTIABLE")
    assert 'data-header-part="band"' in header_rule
    assert 'data-header-part="title"' in header_rule
    assert 'data-header-part="body"' in header_rule
    assert "data-header-band" in header_rule
    # The role's plain layout, with the deck's silhouette explicitly withdrawn.
    assert "PLAIN layout" in layout_rule and "CLOSING" in layout_rule
    assert "Ignore every silhouette" in layout_rule
    # The frame, in pixels.
    assert "1280x720" in frame_rule
    assert f"y={rescue_mod.SAFE_BOTTOM_Y_PX}" in frame_rule


def test_simplified_brief_without_a_contract_forbids_a_header_instead():
    brief = rescue_mod.simplified_author_brief(role="COVER", contract=None)

    assert "must carry no " in brief
    assert "data-header-part" not in brief


# --- the deterministic placeholder -------------------------------------------


def test_placeholder_reproduces_the_deck_header_contract_and_the_outline():
    contract = default_header_contract(primary="#2563EB")

    html = rescue_mod._placeholder(_stub_plan(contract=contract), 0)

    # Every part the contract requires, exactly once, with a legal band.
    assert validate_header_contract(html, contract, role="CLOSING") == ()
    # The slide's own outline text, in full.
    assert "분기 실적 요약" in html
    assert "매출 120억 원" in html
    assert "영업이익 18억 원" in html
    # It is a real authored page, not the emergency branded fallback the
    # pipeline rejects outright.
    assert not is_fallback_slide_html(html)
    assert validate_authored_slide_html(html)


def test_placeholder_without_a_contract_still_carries_the_outline():
    html = rescue_mod._placeholder(_stub_plan(), 0)

    assert "data-header-part" not in html
    assert "매출 120억 원" in html
    assert validate_authored_slide_html(html)


def test_placeholder_keeps_a_long_title_line_out_of_the_body_only_once():
    long_line = "가" * 90
    plan = SimpleNamespace(
        contents=(long_line,),
        roles=("PROBLEM",),
        brand=_brand(),
        header_contract_at=lambda _index: default_header_contract(),
    )

    html = rescue_mod._placeholder(plan, 0)

    # Truncated in the title, preserved in the body: no outline text is lost.
    assert "…</h1>" in html
    assert long_line in html


# --- the third attempt -------------------------------------------------------


def test_third_attempt_uses_the_simplified_brief_and_publishes_unflagged(monkeypatch):
    contract = default_header_contract(primary="#2563EB")
    plan = _stub_plan(contract=contract)
    calls: list[tuple[str, int]] = []
    seen: dict[str, object] = {}

    async def fake_rescue(rescue_plan, index, *, attempt_observer=None, **kwargs):
        calls.append(("rescue", index))
        seen["plan"] = rescue_plan
        seen["kwargs"] = kwargs
        if attempt_observer is not None:
            attempt_observer(3)
        return _RESCUED_HTML

    result = _run_pipeline(
        monkeypatch,
        plan=plan,
        first_pass=_first_pass_that_spends_its_budget(calls),
        rescue=fake_rescue,
    )

    assert "RESCUED" in result.deck.htmls[0]
    # Exactly one extra call, and the receipt is billed for all three.
    assert calls == [("first-pass", 0), ("rescue", 0)]
    assert result.author_provider_attempt_counts == (3,)
    # A recovered slide is not a flagged slide.
    assert result.author_flagged_slides == ()
    # One more call, not a second two-call budget.
    assert seen["kwargs"]["provider_attempt_offset"] == 2
    assert seen["kwargs"]["provider_attempt_limit"] == 3
    hint = seen["kwargs"]["prior_failure_hint"]
    assert hint == rescue_mod.simplified_author_brief(role="CLOSING", contract=contract)
    # The deck's visual ambitions are withheld; everything else is the deck's.
    simplified = seen["plan"]
    assert simplified.visual_plan is None
    assert simplified.infographic_briefs == ()
    assert simplified.illustration_slots == ()
    assert simplified.contents is plan.contents
    assert simplified.header_contract_at(0) is contract


def test_rescue_overload_replay_is_owned_by_the_bounded_provider_layer(
    monkeypatch,
):
    waits = _capture_cooldown_waits(monkeypatch)
    calls: list[tuple[int, int]] = []
    observed: list[int] = []

    async def overloaded_once(
        _plan,
        _index,
        *,
        attempt_observer=None,
        provider_attempt_offset=0,
        provider_attempt_limit=2,
        **_kwargs,
    ):
        calls.append((provider_attempt_offset, provider_attempt_limit))
        if attempt_observer is not None:
            attempt_observer(provider_attempt_offset + 1)
            observed.append(provider_attempt_offset + 1)
        raise ProviderOutcomeError(
            "public_provider_overload", recovery_allowed=True, overload=True
        )

    with pytest.raises(pipeline_mod.AuthoredStageError) as caught:
        _run_pipeline(
            monkeypatch,
            plan=_stub_plan(contract=default_header_contract()),
            first_pass=_first_pass_that_spends_its_budget([]),
            rescue=overloaded_once,
            source_images_locked_to_source_slide=True,
        )

    assert calls == [(2, 3)]
    assert observed == [3]
    assert waits == []
    assert caught.value.code == "public_provider_overload"
    assert caught.value.provider_attempt_count == 3


@pytest.mark.parametrize(
    "code", ("public_provider_overload", "public_provider_call_timeout")
)
def test_rescue_overload_past_bound_keeps_coded_terminal_failure(monkeypatch, code):
    waits = _capture_cooldown_waits(monkeypatch)
    calls = 0

    async def always_overloaded(
        _plan, _index, *, attempt_observer=None, provider_attempt_offset=0, **_kwargs
    ):
        nonlocal calls
        calls += 1
        if attempt_observer is not None:
            attempt_observer(provider_attempt_offset + 1)
        raise ProviderOutcomeError(code, recovery_allowed=True, overload=True)

    with pytest.raises(pipeline_mod.AuthoredStageError) as caught:
        _run_pipeline(
            monkeypatch,
            plan=_stub_plan(contract=default_header_contract()),
            first_pass=_first_pass_that_spends_its_budget([]),
            rescue=always_overloaded,
            source_images_locked_to_source_slide=True,
        )

    assert calls == 1
    assert waits == []
    assert caught.value.code == code
    assert caught.value.provider_attempt_count == 3
    event = log_authored_slide_failure_observation(
        stage="author",
        code=caught.value.code,
        slide_number=1,
        retryable=False,
        provider_attempt_count=3,
    )
    assert event["code"] == code


def test_third_attempt_failure_publishes_a_flagged_placeholder(monkeypatch):
    contract = default_header_contract(primary="#2563EB")
    calls: list[tuple[str, int]] = []

    async def fake_rescue(_plan, index, **_kwargs):
        calls.append(("rescue", index))
        raise _budget_exhausted()

    result = _run_pipeline(
        monkeypatch,
        plan=_stub_plan(contract=contract),
        first_pass=_first_pass_that_spends_its_budget(calls),
        rescue=fake_rescue,
    )

    assert calls == [("first-pass", 0), ("rescue", 0)]
    assert result.author_flagged_slides == (
        {
            "index": 0,
            "reason": "author:attempts_exhausted",
            "remakeAttempted": True,
            "remakeOutcome": "author_rescue_budget_exhausted",
        },
    )
    published = result.deck.htmls[0]
    assert validate_header_contract(published, contract, role="CLOSING") == ()
    assert "매출 120억 원" in published
    assert result.author_provider_attempt_counts == (3,)


def test_a_provider_crash_in_the_third_attempt_still_publishes_flagged(monkeypatch):
    waits = _capture_cooldown_waits(monkeypatch)
    calls = 0

    async def fake_rescue(_plan, _index, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider transport died")

    result = _run_pipeline(
        monkeypatch,
        plan=_stub_plan(contract=default_header_contract()),
        first_pass=_first_pass_that_spends_its_budget([]),
        rescue=fake_rescue,
    )

    assert [entry["remakeOutcome"] for entry in result.author_flagged_slides] == [
        "author_rescue_failed"
    ]
    assert calls == 1
    assert waits == []


def test_only_the_failed_slide_spends_a_rescue_call(monkeypatch):
    calls: list[tuple[str, int]] = []

    async def fake_author(selected_plan, index, *, attempt_observer=None, **_kwargs):
        calls.append(("first-pass", index))
        if attempt_observer is not None:
            attempt_observer(1)
        if index != 1:
            return _RESCUED_HTML
        if attempt_observer is not None:
            attempt_observer(2)
        raise _budget_exhausted()

    async def fake_rescue(_plan, index, **_kwargs):
        calls.append(("rescue", index))
        raise _budget_exhausted()

    result = _run_pipeline(
        monkeypatch,
        plan=_stub_plan(3, contract=default_header_contract()),
        first_pass=fake_author,
        rescue=fake_rescue,
        slides=3,
    )

    assert [call for call in calls if call[0] == "rescue"] == [("rescue", 1)]
    assert [entry["index"] for entry in result.author_flagged_slides] == [1]
    assert result.author_provider_attempt_counts == (1, 3, 1)


# --- the fail-closed decks ---------------------------------------------------


def test_knob_off_keeps_todays_failed_deck(monkeypatch):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "false")

    async def forbidden_rescue(*_args, **_kwargs):
        raise AssertionError("no rescue call may be made with the knob off")

    with pytest.raises(pipeline_mod.AuthoredStageError) as caught:
        _run_pipeline(
            monkeypatch,
            plan=_stub_plan(contract=default_header_contract()),
            first_pass=_first_pass_that_spends_its_budget([]),
            rescue=forbidden_rescue,
        )

    assert caught.value.code == "authored_slide_author_failed"
    assert caught.value.stage == "author"


def test_a_source_reflow_deck_gets_one_strict_third_call_without_placeholder(
    monkeypatch,
):
    seen = {}

    async def strict_rescue(_plan, _index, **kwargs):
        seen.update(kwargs)
        return _RESCUED_HTML

    result = _run_pipeline(
        monkeypatch,
        plan=_stub_plan(contract=default_header_contract()),
        first_pass=_first_pass_that_spends_its_budget([]),
        rescue=strict_rescue,
        source_images_locked_to_source_slide=True,
    )

    assert len(result.pngs) == 1
    assert result.author_flagged_slides == ()
    assert result.author_provider_attempt_counts == (3,)
    assert seen["provider_attempt_offset"] == 2
    assert seen["provider_attempt_limit"] == 3
    hint = seen["prior_failure_hint"]
    assert "source-locked correction" in hint
    assert "do not summarize, shorten, merge" in hint


def _readback_result(**overrides):
    values = {
        "checked_slide_count": 1,
        "checked_block_count": 1,
        "failed_slide_indices": (0,),
    }
    values.update(overrides)
    return BrowserSourceReadbackResult(**values)


def test_source_reflow_rescue_visibility_is_predeclared_verbatim_flag_only(
    monkeypatch,
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")
    monkeypatch.setattr(
        rescue_mod,
        "inspect_browser_source_readback",
        lambda **_kwargs: _readback_result(visibility_failures=("b1",)),
    )

    entry = rescue_mod.source_reflow_rescue_readback(
        index=0,
        html=_RESCUED_HTML,
        metrics={},
        bindings=(("b1", "RESCUED"),),
        profile=_ORDINARY,
        reference_mode="style_reference",
        prior_failure={"code": "authored_header_contract_rejected"},
    )

    assert entry == {
        "index": 0,
        "reason": "source_readback:visibility",
        "remakeAttempted": False,
        "remakeOutcome": "verbatim_flag_only",
        "reasons": [
            "source_readback:visibility",
            "author_prior:authored_header_contract_rejected",
        ],
    }
    assert flagged_slide_entries([entry]) == (entry,)


def test_recovered_source_slide_routes_visibility_to_the_pipeline_flag(monkeypatch):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")

    async def strict_rescue(_plan, _index, **_kwargs):
        return _RESCUED_HTML

    monkeypatch.setattr(
        rescue_mod,
        "inspect_browser_source_readback",
        lambda **_kwargs: _readback_result(visibility_failures=("b1",)),
    )
    result = _run_pipeline(
        monkeypatch,
        plan=_stub_plan(contract=default_header_contract()),
        first_pass=_first_pass_that_spends_its_budget([]),
        rescue=strict_rescue,
        source_block_bindings=((('b1', "RESCUED"),),),
    )

    assert result.author_flagged_slides[0]["remakeOutcome"] == "verbatim_flag_only"


@pytest.mark.parametrize(
    "result",
    (
        _readback_result(missing_block_ids=("b1",)),
        _readback_result(integrity_failures=("b1",)),
    ),
)
def test_source_reflow_rescue_hard_readback_uses_a_coded_terminal_failure(
    monkeypatch, result,
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")
    monkeypatch.setattr(
        rescue_mod, "inspect_browser_source_readback", lambda **_kwargs: result
    )

    with pytest.raises(pipeline_mod.AuthoredStageError) as caught:
        rescue_mod.source_reflow_rescue_readback(
            index=0,
            html=_RESCUED_HTML,
            metrics={},
            bindings=(("b1", "RESCUED"),),
            profile=_ORDINARY,
            reference_mode="style_reference",
        )

    assert caught.value.code == "source_content_browser_readback_failed"
    assert caught.value.cause.block_ids == ("b1",)
    event = log_authored_slide_failure_observation(
        stage="author",
        code=caught.value.code,
        slide_number=1,
        retryable=False,
        provider_attempt_count=3,
    )
    assert event["code"] == "source_content_browser_readback_failed"


def test_recovered_source_slide_hard_readback_is_never_unexpected(monkeypatch):
    async def strict_rescue(_plan, _index, **_kwargs):
        return _RESCUED_HTML

    monkeypatch.setattr(
        rescue_mod,
        "inspect_browser_source_readback",
        lambda **_kwargs: _readback_result(integrity_failures=("b1",)),
    )
    with pytest.raises(pipeline_mod.AuthoredStageError) as caught:
        _run_pipeline(
            monkeypatch,
            plan=_stub_plan(contract=default_header_contract()),
            first_pass=_first_pass_that_spends_its_budget([]),
            rescue=strict_rescue,
            source_block_bindings=((('b1', "RESCUED"),),),
        )

    assert caught.value.code == "source_content_browser_readback_failed"
    assert "integrity=[b1]" in caught.value.safe_message
    assert caught.value.provider_attempt_count == 3


def test_a_retryable_transport_failure_is_not_rescued(monkeypatch):
    async def fake_author(_plan, _index, **_kwargs):
        raise TimeoutError("provider timed out")

    async def forbidden_rescue(*_args, **_kwargs):
        raise AssertionError("a retryable failure belongs to the operation retry")

    with pytest.raises(pipeline_mod.AuthoredStageError) as caught:
        _run_pipeline(
            monkeypatch,
            plan=_stub_plan(contract=default_header_contract()),
            first_pass=fake_author,
            rescue=forbidden_rescue,
        )

    assert caught.value.code == "authored_slide_author_failed"
    assert caught.value.retryable is True


# --- the published receipt ---------------------------------------------------


def test_the_flag_is_a_valid_receipt_entry():
    entry = rescue_mod._flagged_entry(4, outcome="author_rescue_budget_exhausted")

    assert flagged_slide_entries([entry]) == (entry,)


async def _sqlite_engine(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'author-rescue.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


def test_the_author_flag_reaches_the_published_quality_gate(monkeypatch, tmp_path):
    """The one place the two flag sources merge into the deck's one list."""

    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app_data"))
    flag = {
        "index": 0,
        "reason": "author:attempts_exhausted",
        "remakeAttempted": True,
        "remakeOutcome": "author_rescue_budget_exhausted",
    }

    async def fake_pipeline(
        outline,
        brand,
        style,
        header_contract=None,
        page_number_mode="none",
        chapter_plan=None,
        illustration_plan=None,
        instructions=None,
    ):
        prepared_html = canonical_capture_html(_RESCUED_HTML, index=0)
        return AuthoredGenerationPipelineResult(
            deck=AuthoredDeckResult([prepared_html], "DESIGN SYSTEM"),
            pngs=[b"png"],
            elapsed=0.1,
            author_work=0.1,
            illustration_work=0.0,
            render_work=0.1,
            author_flagged_slides=(flag,),
            local_fit_receipts=_fit_receipts([prepared_html]),
        )

    async def fake_header_contract(
        outline, brand, style=None, instructions=None, chapter_breadcrumb=False
    ):
        return default_header_contract(primary=brand.primary)

    async def fake_metrics(_html):
        return {"telemetry": "available"}

    from services import authored_presentation_generation as generation_mod
    from utils import llm_provider

    monkeypatch.setattr(
        authored_service,
        "resolve_authored_style",
        lambda style_id: SimpleNamespace(
            id="editorial", brief="- Editorial", illustration_prompt=None
        ),
    )
    monkeypatch.setattr(
        authored_service, "_author_illustrate_render_pipeline", fake_pipeline
    )
    monkeypatch.setattr(
        authored_service, "resolve_header_contract", fake_header_contract
    )
    monkeypatch.setattr(authored_service, "find_chrome", lambda: True)
    monkeypatch.setattr(
        authored_service,
        "_save_slide_pngs",
        lambda presentation_id, pngs, *args, **kwargs: ["slide-0.png"],
    )
    monkeypatch.setattr(
        authored_service,
        "_build_authored_export",
        lambda request, presentation_id, pngs, title, **kwargs: "deck.pptx",
    )
    monkeypatch.setattr(
        llm_provider, "get_llm_provider", lambda: SimpleNamespace(value="stub")
    )
    monkeypatch.setattr(llm_provider, "get_model", lambda: "stub-model")
    for module in (generation_mod, terminal_mod):
        monkeypatch.setattr(
            module,
            "inspect_authored_dom",
            lambda *args, **kwargs: SimpleNamespace(
                telemetry_status="available", issues=[], fit_summary=None
            ),
        )
        monkeypatch.setattr(module, "blocking_safety_issues", lambda _report: ())
        monkeypatch.setattr(module, "capacity_violations", lambda *a, **k: ())
    monkeypatch.setattr(generation_mod, "ensure_browser_metrics", fake_metrics)
    monkeypatch.setattr(
        terminal_mod, "get_cached_browser_metrics", lambda _html: {"telemetry": "ok"}
    )

    request = GeneratePresentationRequest(
        content="topic", template="authored", authored_style="editorial"
    )
    presentation_id = uuid.uuid4()
    owner_token = set_current_owner_id(uuid.uuid4())

    async def scenario():
        engine = await _sqlite_engine(tmp_path)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                await authored_service.generate_authored_presentation(
                    request, presentation_id, _outline(1), "Korean", session
                )
                return await session.get(PresentationModel, presentation_id)
        finally:
            await engine.dispose()

    try:
        presentation = _run(scenario())
    finally:
        reset_current_owner_id(owner_token)

    assert _pop_slide_ids(
        presentation.theme["authoredQualityGate"]["flaggedSlides"]
    ) == [flag]
