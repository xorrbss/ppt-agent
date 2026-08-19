"""Deck-scoped authored illustration plan: how many pictures, and whose.

The count is the thing under test. Every attempt to steer it from inside the
per-slide prompt oscillated, so what matters here is that the budget is decided
by arithmetic the model cannot reach: a reported count is advice, the returned
list is truncated, and out-of-range indices cost their own slot and nothing more.
"""

import asyncio

import pytest

from models.authored_illustration_plan import (
    MAX_ILLUSTRATION_BUDGET,
    MIN_ILLUSTRATION_BUDGET,
    MIN_SLIDES_FOR_ILLUSTRATION_PLAN,
    AuthoredIllustrationPlan,
    AuthoredIllustrationPlanDraft,
    default_illustration_budget,
    read_illustration_appetite,
    resolve_illustration_budget,
)
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from services import authored_deck_illustration_plan as plan_service
from services.authored_deck_illustration_plan import (
    load_illustration_plan,
    resolve_illustration_plan,
)
from utils.llm_calls import author_illustration_plan as plan_call
from utils.llm_calls.author_illustration_plan import (
    build_illustration_plan_messages,
    coerce_illustration_plan_draft,
    generate_illustration_plan,
)
from utils.llm_calls.author_slide import Brand


class _Style:
    def __init__(self, illustration_prompt="watercolour architectural scenes"):
        self.illustration_prompt = illustration_prompt
        self.brief = "차분한 기업 제안 톤"


def _outline(count: int) -> PresentationOutlineModel:
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"## 슬라이드 {i}") for i in range(count)]
    )


def _stub_provider(monkeypatch, *, response=None, error=None):
    async def fake_generate(_client, _model, **_kwargs):
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(plan_call, "get_llm_config", lambda: "config")
    monkeypatch.setattr(plan_call, "get_client", lambda *, config: object())
    monkeypatch.setattr(plan_call, "get_model", lambda: "plan-model")
    monkeypatch.setattr(
        plan_call, "generate_structured_with_schema_retries", fake_generate
    )


# --- the frozen plan ------------------------------------------------------------


def test_plan_indices_must_strictly_ascend():
    assert AuthoredIllustrationPlan(slide_indices=(0, 3, 9)).slide_indices == (0, 3, 9)
    with pytest.raises(ValueError):
        AuthoredIllustrationPlan(slide_indices=(9, 3))


def test_a_repeated_index_is_rejected_rather_than_spending_two_slots():
    with pytest.raises(ValueError):
        AuthoredIllustrationPlan(slide_indices=(3, 3, 7))


def test_a_negative_index_is_rejected():
    with pytest.raises(ValueError):
        AuthoredIllustrationPlan(slide_indices=(-1, 4))


def test_allows_answers_only_for_the_slides_the_plan_named():
    plan = AuthoredIllustrationPlan(slide_indices=(2, 5, 11))
    assert [plan.allows(index) for index in (0, 2, 3, 5, 11, 12)] == [
        False,
        True,
        False,
        True,
        True,
        False,
    ]
    assert plan.count == 3


def test_an_empty_plan_is_a_plan_and_forbids_every_slide():
    empty = AuthoredIllustrationPlan()
    assert empty.slide_indices == ()
    assert empty.allows(0) is False
    assert empty.count == 0


def test_a_draft_becomes_a_plan_in_outline_order():
    # The draft carries the model's priority order; the plan carries the deck's.
    draft = AuthoredIllustrationPlanDraft(slide_indices=(11, 2, 5))
    assert draft.to_plan().slide_indices == (2, 5, 11)


# --- the budget -----------------------------------------------------------------


def test_the_default_budget_is_a_sixth_of_the_deck_between_its_bounds():
    assert default_illustration_budget(77) == 12  # round(0.15 * 77)
    assert default_illustration_budget(40) == 6
    # Short decks are held up to the floor and long decks held down to the cap,
    # so neither arithmetic edge decides a deck on its own.
    assert default_illustration_budget(10) == MIN_ILLUSTRATION_BUDGET
    assert default_illustration_budget(200) == MAX_ILLUSTRATION_BUDGET
    assert default_illustration_budget(2) == 2
    assert default_illustration_budget(0) == 0


def test_words_about_pictures_everywhere_raise_the_ceiling_to_the_share_cap():
    assert read_illustration_appetite("모든 슬라이드에 이미지를 넣어줘") == 1
    # 0.6 * 77 = 46.2, and the model's own ask of 70 is clamped to it rather
    # than believed.
    assert (
        resolve_illustration_budget(
            77, instructions="모든 슬라이드에 이미지를 넣어줘", requested=70
        )
        == 46
    )
    # A raised ceiling never lowers the ordinary target underneath it.
    assert (
        resolve_illustration_budget(
            77, instructions="every slide should have an illustration", requested=2
        )
        == 12
    )


def test_words_asking_for_fewer_pictures_lower_the_budget_toward_zero():
    assert read_illustration_appetite("이미지 없이 텍스트만") == -1
    assert (
        resolve_illustration_budget(77, instructions="이미지 없이 텍스트만", requested=0)
        == 0
    )
    # "few" caps the deck at the floor of the ordinary range, whatever count the
    # model reported.
    assert (
        resolve_illustration_budget(
            77, instructions="사진은 최소한으로", requested=9
        )
        == MIN_ILLUSTRATION_BUDGET
    )


def test_a_quantity_word_about_something_else_does_not_move_the_budget():
    # The request asks for tables everywhere, not pictures everywhere.
    assert read_illustration_appetite("모든 슬라이드에 표를 넣어줘") == 0
    assert (
        resolve_illustration_budget(77, instructions="모든 슬라이드에 표를 넣어줘")
        == 12
    )
    assert resolve_illustration_budget(77, instructions=None) == 12


def test_a_reported_count_can_only_lower_a_deck_nobody_asked_anything_about():
    assert resolve_illustration_budget(77, requested=4) == 4
    assert resolve_illustration_budget(77, requested=40) == 12


# --- coercion -------------------------------------------------------------------


def test_the_budget_truncates_from_the_tail_of_the_models_priority_order():
    payload = {"requested_count": 12, "slide_indices": list(range(1, 30))}
    draft = coerce_illustration_plan_draft(payload, slide_count=77)
    # 15% of 77 is 12, and what survives is the head of the model's ranking.
    assert draft.slide_indices == tuple(range(1, 13))


def test_indices_outside_the_outline_and_repeats_are_dropped():
    payload = {
        "requested_count": 5,
        "slide_indices": [3, 77, -2, 3, 9, 120, 40],
    }
    draft = coerce_illustration_plan_draft(payload, slide_count=20)
    assert draft.slide_indices == (3, 9)


def test_a_missing_reported_count_is_read_as_the_list_the_model_returned():
    payload = {"slide_indices": [2, 4]}
    draft = coerce_illustration_plan_draft(payload, slide_count=77)
    assert draft.slide_indices == (2, 4)


def test_the_caller_instructions_reach_the_truncation():
    payload = {"requested_count": 60, "slide_indices": list(range(0, 60))}
    draft = coerce_illustration_plan_draft(
        payload, slide_count=77, instructions="이미지를 최대한 많이 넣어줘"
    )
    assert len(draft.slide_indices) == 46
    fewer = coerce_illustration_plan_draft(
        payload, slide_count=77, instructions="이미지는 최소한으로"
    )
    assert len(fewer.slide_indices) == MIN_ILLUSTRATION_BUDGET


def test_a_payload_that_is_not_an_object_is_refused():
    with pytest.raises(ValueError):
        coerce_illustration_plan_draft([1, 2, 3], slide_count=20)


def test_the_prompt_names_the_target_and_quotes_the_user_request():
    messages = build_illustration_plan_messages(
        topic="스마트공장 구축",
        language="Korean",
        slide_contents=["## 표지", "## 설비 배치도"],
        instructions="사진 많이",
        target_count=7,
    )
    body = messages[-1].content
    assert "Illustration budget for this deck: 7 slides" in body
    # The budget is a target to fill, not a ceiling to stay under: naming it
    # as a limit is what returned an empty plan for a real 9-slide deck.
    assert "Rank at least this many, best first." in body
    assert "사진 많이" in body
    assert "best candidate FIRST" in body


# --- failing open ---------------------------------------------------------------


def test_a_broken_payload_leaves_the_deck_unplanned(monkeypatch):
    _stub_provider(monkeypatch, response=["not", "an", "object"])
    assert (
        asyncio.run(
            generate_illustration_plan(
                topic="주제", language="Korean", slide_contents=["## 1"] * 20
            )
        )
        is None
    )


def test_a_provider_failure_leaves_the_deck_unplanned(monkeypatch):
    _stub_provider(monkeypatch, error=RuntimeError("provider is down"))
    assert (
        asyncio.run(
            generate_illustration_plan(
                topic="주제", language="Korean", slide_contents=["## 1"] * 20
            )
        )
        is None
    )


def test_a_usable_payload_survives_the_call(monkeypatch):
    _stub_provider(
        monkeypatch, response={"requested_count": 3, "slide_indices": [4, 2, 8]}
    )
    draft = asyncio.run(
        generate_illustration_plan(
            topic="주제", language="Korean", slide_contents=["## 1"] * 20
        )
    )
    assert draft is not None
    assert draft.slide_indices == (4, 2, 8)


# --- the service ----------------------------------------------------------------


def _never(**_options):
    raise AssertionError("this deck must not reach the planning model")


def test_a_style_that_does_not_illustrate_costs_no_model_call(monkeypatch):
    monkeypatch.setattr(plan_service, "generate_illustration_plan", _never)
    assert (
        asyncio.run(
            resolve_illustration_plan(
                _outline(40), Brand(topic="주제", language="Korean"), _Style(None)
            )
        )
        is None
    )


def test_a_short_deck_costs_no_model_call(monkeypatch):
    monkeypatch.setattr(plan_service, "generate_illustration_plan", _never)
    assert (
        asyncio.run(
            resolve_illustration_plan(
                _outline(MIN_SLIDES_FOR_ILLUSTRATION_PLAN - 1),
                Brand(topic="주제", language="Korean"),
                _Style(),
            )
        )
        is None
    )


def test_the_service_hands_the_deck_and_the_request_to_the_planner(monkeypatch):
    seen = {}

    async def fake_plan(**options):
        seen.update(options)
        return AuthoredIllustrationPlanDraft(requested_count=3, slide_indices=(9, 3))

    monkeypatch.setattr(plan_service, "generate_illustration_plan", fake_plan)
    plan = asyncio.run(
        resolve_illustration_plan(
            _outline(40),
            Brand(topic="스마트공장", language="Korean"),
            _Style(),
            instructions="설비 사진을 넣어줘",
        )
    )
    assert plan is not None
    assert plan.slide_indices == (3, 9)
    assert seen["topic"] == "스마트공장"
    assert seen["instructions"] == "설비 사진을 넣어줘"
    assert len(seen["slide_contents"]) == 40
    assert seen["style_brief"] == "차분한 기업 제안 톤"


def test_a_failed_plan_call_leaves_the_deck_with_no_plan(monkeypatch):
    async def fake_plan(**_options):
        return None

    monkeypatch.setattr(plan_service, "generate_illustration_plan", fake_plan)
    assert (
        asyncio.run(
            resolve_illustration_plan(
                _outline(40), Brand(topic="주제", language="Korean"), _Style()
            )
        )
        is None
    )


def test_a_deck_that_earned_nothing_gets_an_empty_plan_not_a_missing_one(monkeypatch):
    # No plan means "author as before"; an empty plan means "no pictures". The
    # service must not collapse the second into the first.
    async def fake_plan(**_options):
        return AuthoredIllustrationPlanDraft(requested_count=0, slide_indices=())

    monkeypatch.setattr(plan_service, "generate_illustration_plan", fake_plan)
    plan = asyncio.run(
        resolve_illustration_plan(
            _outline(40), Brand(topic="주제", language="Korean"), _Style()
        )
    )
    assert plan == AuthoredIllustrationPlan(slide_indices=())


def test_an_unreadable_persisted_plan_is_read_as_no_plan():
    assert load_illustration_plan(None) is None
    assert load_illustration_plan({}) is None
    assert load_illustration_plan({"illustrationPlan": [1, 2]}) is None
    assert load_illustration_plan({"illustrationPlan": {"slide_indices": [4, 4]}}) is None


def test_a_persisted_plan_round_trips():
    plan = AuthoredIllustrationPlan(slide_indices=(2, 6, 14))
    stored = {"illustrationPlan": plan.model_dump(mode="json")}
    assert load_illustration_plan(stored) == plan


def test_a_korean_refusal_zeroes_the_budget_rather_than_merely_lowering_it():
    """A real request said 이미지 추가하지 말고 and still got three pictures.

    Korean states a prohibition by negating the verb rather than by naming a
    quantity, so none of the quantity words matched and the text read as
    neutral. Even once it read as "fewer", reaching zero still depended on the
    planning model reporting zero. A refusal is decided in code instead.
    """
    from models.authored_illustration_plan import (
        illustrations_are_prohibited,
        resolve_illustration_budget,
    )

    for refusal in (
        "첨부한 문서 그대로 만들어줘 이미지 추가하지 말고 디자인만 바꿔줘",
        "이미지는 만들지 말고 컨텐츠는 그대로 사용해줘",
        "이미지 넣지 마세요",
        "이미지 없이 텍스트만",
        "no images please",
    ):
        assert illustrations_are_prohibited(refusal), refusal
        # requested is deliberately non-zero: the model's advice must not be
        # able to reinstate pictures the caller refused.
        assert resolve_illustration_budget(9, instructions=refusal, requested=5) == 0


def test_a_refusal_about_something_else_leaves_the_budget_alone():
    """Only text about pictures may zero the budget."""
    from models.authored_illustration_plan import (
        illustrations_are_prohibited,
        resolve_illustration_budget,
    )

    other = "표를 넣지 말고 그래프로 보여줘"

    assert not illustrations_are_prohibited(other)
    assert resolve_illustration_budget(9, instructions=other, requested=5) == 3
