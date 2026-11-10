"""The deck-scoped infographic plan: what it accepts, how it repairs, what it derives.

The plan decides once, per deck, which figure every page is drawn as and which
pages are the deck's hero moments, and the illustration plan is now derived
from it rather than budgeted. These tests pin the repair rules that keep one
bad answer from costing the deck its plan, the derivation, and the fail-open
boundaries of the model call and the service.
"""

import asyncio
from types import SimpleNamespace

import pytest

from models.authored_illustration_plan import (
    AuthoredIllustrationPlan,
    illustrations_are_prohibited,
)
from models.authored_infographic_plan import (
    ARCHETYPES,
    AuthoredInfographicPlan,
    AuthoredSlideInfographic,
    InfographicNode,
    coerce_infographic_plan,
    scene_ceiling,
    without_scenes,
)
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from services.generation_core.assets import infographic_plan as plan_service
from services.generation_core.assets import infographic_assets as assets_mod
from services.generation_core.assets.infographic_plan import (
    derive_illustration_plan,
    load_infographic_plan,
    resolve_infographic_plan,
)
from utils.llm_calls import author_infographic_plan as plan_call
from utils.llm_calls.author_infographic_marker import infographic_marker
from utils.llm_calls.author_infographic_plan import (
    build_infographic_plan_messages,
    generate_infographic_plan,
)
from services.generation_core.authoring.author_slide import Brand

_CYCLE = (
    "## 예지보전 순환\n- 감지: 센서 이상 패턴 포착\n- 예측: 고장 확률 72시간 전 산출\n"
    "- 조치: 작업지시 자동 발행\n- 학습: 조치 결과를 모델에 반영"
)
_CYCLE_NODES = [
    {"label": "감지", "desc": "센서 이상 패턴 포착"},
    {"label": "예측", "desc": "고장 확률 산출", "figure": "72시간 전"},
    {"label": "조치", "desc": "작업지시 자동 발행"},
    {"label": "학습", "desc": "조치 결과 모델 반영"},
]


def _entry(index, archetype="cycle", nodes=None, **extra):
    return {
        "index": index,
        "archetype": archetype,
        "weight": "standard",
        "takeaway": "감지-예측-조치-학습",
        "nodes": _CYCLE_NODES if nodes is None else nodes,
        "density": "medium",
        **extra,
    }


def _outline(count: int) -> PresentationOutlineModel:
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=_CYCLE) for _ in range(count)]
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


def test_the_vocabulary_has_twenty_figures_and_none():
    assert len(ARCHETYPES) == 21
    assert "none" in ARCHETYPES and "scene" in ARCHETYPES


def test_a_quiet_page_carries_no_nodes_and_no_emphasis():
    with pytest.raises(ValueError):
        AuthoredSlideInfographic(index=0, nodes=(InfographicNode(label="a"),) * 3)
    with pytest.raises(ValueError):
        AuthoredSlideInfographic(index=0, weight="hero")


def test_a_figure_needs_three_to_six_nodes_and_an_emphasis_inside_them():
    nodes = tuple(InfographicNode(label=f"n{i}") for i in range(3))
    AuthoredSlideInfographic(index=0, archetype="process", weight="hero", nodes=nodes)
    with pytest.raises(ValueError):
        AuthoredSlideInfographic(index=0, archetype="process", nodes=nodes[:2])
    with pytest.raises(ValueError):
        AuthoredSlideInfographic(
            index=0, archetype="process", nodes=nodes, emphasis=3
        )


def test_plan_entries_must_be_contiguous():
    with pytest.raises(ValueError):
        AuthoredInfographicPlan(slides=(AuthoredSlideInfographic(index=1),))


# --- repair ---------------------------------------------------------------------


def test_coercion_yields_one_entry_per_slide_with_missing_ones_quiet():
    plan = coerce_infographic_plan(
        {"slides": [_entry(1)]},
        slide_contents=["표지", _CYCLE, "마무리"],
        roles=["COVER", "CONTENT", "CLOSING"],
    )
    assert [s.archetype for s in plan.slides] == ["none", "cycle", "none"]
    assert plan.slides[1].nodes[1].figure == "72시간 전"
    assert plan.slides[1].weight == "standard"


def test_cover_agenda_and_closing_are_quiet_whatever_the_model_said():
    plan = coerce_infographic_plan(
        {"slides": [_entry(0), _entry(1), _entry(2)]},
        slide_contents=[_CYCLE] * 3,
        roles=["COVER", "AGENDA", "CLOSING"],
    )
    assert all(not s.has_figure for s in plan.slides)


def test_a_label_the_content_does_not_contain_is_dropped():
    nodes = _CYCLE_NODES + [{"label": "발명된 단계"}]
    plan = coerce_infographic_plan(
        {"slides": [_entry(0, nodes=nodes)]},
        slide_contents=[_CYCLE],
        roles=["CONTENT"],
    )
    assert [n.label for n in plan.slides[0].nodes] == ["감지", "예측", "조치", "학습"]


def test_a_figure_left_with_too_few_verbatim_labels_becomes_a_quiet_page():
    nodes = [{"label": "감지"}, {"label": "없는말"}, {"label": "다른말"}]
    plan = coerce_infographic_plan(
        {"slides": [_entry(0, nodes=nodes)]},
        slide_contents=[_CYCLE],
        roles=["CONTENT"],
    )
    assert plan.slides[0].archetype == "none"


def test_an_unknown_archetype_or_weight_is_repaired_not_fatal():
    plan = coerce_infographic_plan(
        {"slides": [_entry(0, archetype="hologram"), _entry(1, weight="loud")]},
        slide_contents=[_CYCLE, _CYCLE],
        roles=["CONTENT", "CONTENT"],
    )
    assert plan.slides[0].archetype == "none"
    assert plan.slides[1].weight == "standard"


def test_a_duplicated_index_keeps_the_first_mention():
    plan = coerce_infographic_plan(
        {"slides": [_entry(0, archetype="cycle"), _entry(0, archetype="funnel")]},
        slide_contents=[_CYCLE],
        roles=["CONTENT"],
    )
    assert plan.slides[0].archetype == "cycle"


def test_an_emphasis_outside_the_surviving_nodes_is_dropped():
    plan = coerce_infographic_plan(
        {"slides": [_entry(0, emphasis=9)]},
        slide_contents=[_CYCLE],
        roles=["CONTENT"],
    )
    assert plan.slides[0].emphasis is None


def test_a_takeaway_far_over_budget_is_dropped_rather_than_clipped():
    plan = coerce_infographic_plan(
        {"slides": [_entry(0, takeaway="가" * 40)]},
        slide_contents=[_CYCLE],
        roles=["CONTENT"],
    )
    assert plan.slides[0].takeaway is None


def test_a_before_after_pair_is_kept_and_an_arrow_figure_is_unpacked_into_one():
    content = "## 현행 vs 도입 후\n- 불량률: 4.6% → 2.2%\n- 재고 오차: 9% → 1.5%\n- 납기 준수율: 87% → 96%"
    nodes = [
        {"label": "불량률", "before": "4.6%", "after": "2.2%", "figure": "-2.4%p"},
        {"label": "재고 오차", "figure": "9% → 1.5%"},
        {"label": "납기 준수율", "before": "   ", "after": " 96% "},
    ]
    plan = coerce_infographic_plan(
        {"slides": [_entry(0, archetype="before_after", nodes=nodes)]},
        slide_contents=[content],
        roles=["CONTENT"],
    )
    kept = plan.slides[0].nodes
    assert (kept[0].before, kept[0].after, kept[0].figure) == ("4.6%", "2.2%", "-2.4%p")
    assert (kept[1].before, kept[1].after, kept[1].figure) == ("9%", "1.5%", None)
    assert kept[2].before is None and kept[2].after == "96%"
    marker = infographic_marker(plan.slides[0])
    assert 'before="4.6%" after="2.2%"' in marker
    assert 'before="" after="96%"' in marker


def test_scenes_past_the_ceiling_are_demoted_keeping_the_weighted_ones():
    entries = [
        _entry(i, archetype="scene", weight="hero" if i == 3 else "standard")
        for i in range(4)
    ]
    plan = coerce_infographic_plan(
        {"slides": entries}, slide_contents=[_CYCLE] * 4, roles=["CONTENT"] * 4
    )
    assert scene_ceiling(4) == 1
    assert plan.scene_indices() == (3,)
    assert plan.slides[0].archetype == "icon_list"


def test_a_payload_that_is_not_an_object_is_refused():
    with pytest.raises(ValueError):
        coerce_infographic_plan([1, 2], slide_contents=["a"], roles=["CONTENT"])


# --- derivation -----------------------------------------------------------------


def test_the_illustration_plan_is_the_scenes_in_outline_order():
    plan = coerce_infographic_plan(
        {"slides": [_entry(0), _entry(1, archetype="scene"), _entry(2)]},
        slide_contents=[_CYCLE] * 3,
        roles=["CONTENT"] * 3,
    )
    assert derive_illustration_plan(plan) == AuthoredIllustrationPlan(
        slide_indices=(1,)
    )
    assert derive_illustration_plan(None) is None


def test_a_plan_without_scenes_derives_an_empty_plan_not_a_missing_one():
    plan = coerce_infographic_plan(
        {"slides": [_entry(0)]}, slide_contents=[_CYCLE], roles=["CONTENT"]
    )
    derived = derive_illustration_plan(plan)
    assert derived is not None and derived.count == 0


def test_without_scenes_demotes_every_scene():
    plan = coerce_infographic_plan(
        {"slides": [_entry(0, archetype="scene", weight="hero")]},
        slide_contents=[_CYCLE],
        roles=["CONTENT"],
    )
    demoted = without_scenes(plan)
    assert demoted.slides[0].archetype == "icon_list"
    assert demoted.slides[0].weight == "standard"
    assert derive_illustration_plan(demoted).count == 0


# --- the prompt -----------------------------------------------------------------


def test_the_prompt_carries_full_content_roles_and_the_quoted_request():
    messages = build_infographic_plan_messages(
        topic="스마트팩토리",
        language="Korean",
        slide_contents=["표지", _CYCLE],
        roles=["COVER", "CONTENT"],
        instructions="그림은 꼭 필요한 곳에만",
    )
    text = messages[1].content
    assert "slide 1 (role: CONTENT)" in text
    assert "고장 확률 72시간 전 산출" in text
    assert "그림은 꼭 필요한 곳에만" in text
    assert "COPIED VERBATIM" in text


def test_the_slide_marker_lists_nodes_verbatim_and_names_the_archetype():
    plan = coerce_infographic_plan(
        {"slides": [_entry(0, emphasis=1, density="high")]},
        slide_contents=[_CYCLE],
        roles=["CONTENT"],
    )
    marker = infographic_marker(plan.at(0))
    assert "archetype: cycle" in marker
    assert 'label="예측"' in marker and "(EMPHASIS)" in marker
    assert 'figure="72시간 전"' in marker
    assert "QUIET" in infographic_marker(AuthoredSlideInfographic(index=3))
    assert infographic_marker(None) == ""


# --- the model call -------------------------------------------------------------


def test_a_broken_payload_leaves_the_deck_unplanned(monkeypatch):
    _stub_provider(monkeypatch, response=["not", "an", "object"])
    assert (
        asyncio.run(
            generate_infographic_plan(
                topic="주제",
                language="Korean",
                slide_contents=[_CYCLE],
                roles=["CONTENT"],
            )
        )
        is None
    )


def test_a_provider_failure_leaves_the_deck_unplanned(monkeypatch):
    _stub_provider(monkeypatch, error=RuntimeError("provider is down"))
    assert (
        asyncio.run(
            generate_infographic_plan(
                topic="주제",
                language="Korean",
                slide_contents=[_CYCLE],
                roles=["CONTENT"],
            )
        )
        is None
    )


def test_a_usable_payload_survives_the_call(monkeypatch):
    _stub_provider(monkeypatch, response={"slides": [_entry(0)]})
    plan = asyncio.run(
        generate_infographic_plan(
            topic="주제", language="Korean", slide_contents=[_CYCLE], roles=["CONTENT"]
        )
    )
    assert plan is not None and plan.slides[0].archetype == "cycle"


# --- the service ----------------------------------------------------------------


def _brand():
    return Brand(topic="주제", language="Korean", primary="#2563EB", fonts="Noto")


def _scene_plan(count):
    return coerce_infographic_plan(
        {"slides": [_entry(i, archetype="scene") for i in range(count)]},
        slide_contents=[_CYCLE] * count,
        roles=["CONTENT"] * count,
    )


def test_the_service_plans_every_style_and_any_length(monkeypatch):
    received = {}

    async def fake_plan(**options):
        received.update(options)
        return _scene_plan(1)

    monkeypatch.setattr(plan_service, "generate_infographic_plan", fake_plan)
    plan = asyncio.run(
        resolve_infographic_plan(
            _outline(1),
            _brand(),
            SimpleNamespace(brief=" 톤 ", illustration_prompt="art"),
            roles=["CONTENT"],
            instructions="요청",
        )
    )
    assert received["roles"] == ["CONTENT"]
    assert received["style_brief"] == "톤"
    assert received["instructions"] == "요청"
    assert plan.scene_indices() == (0,)


def test_a_deck_with_no_picture_source_keeps_the_figure_but_loses_the_scene(
    monkeypatch,
):
    async def fake_plan(**_options):
        return _scene_plan(1)

    monkeypatch.setattr(plan_service, "generate_infographic_plan", fake_plan)
    monkeypatch.setattr(assets_mod, "transparent_model_for", lambda _settings=None: None)
    plan = asyncio.run(
        resolve_infographic_plan(
            _outline(1), _brand(), SimpleNamespace(illustration_prompt=None),
            roles=["CONTENT"],
        )
    )
    assert plan.slides[0].archetype == "icon_list"


def test_a_transparent_capable_provider_keeps_the_scene_without_a_style_prompt(
    monkeypatch,
):
    """The cutout comes from the transparent provider; no style prompt is needed."""

    async def fake_plan(**_options):
        return _scene_plan(1)

    monkeypatch.setattr(plan_service, "generate_infographic_plan", fake_plan)
    monkeypatch.setattr(
        assets_mod, "transparent_model_for", lambda _settings=None: "gpt-image-2"
    )
    plan = asyncio.run(
        resolve_infographic_plan(
            _outline(1), _brand(), SimpleNamespace(illustration_prompt=None),
            roles=["CONTENT"],
        )
    )
    assert plan.scene_indices() == (0,)
    refused = asyncio.run(
        resolve_infographic_plan(
            _outline(1), _brand(), SimpleNamespace(illustration_prompt=None),
            roles=["CONTENT"], instructions="이미지는 넣지 말고",
        )
    )
    assert refused.scene_indices() == ()


def test_a_refusal_of_pictures_loses_the_scene_too(monkeypatch):
    async def fake_plan(**_options):
        return _scene_plan(1)

    monkeypatch.setattr(plan_service, "generate_infographic_plan", fake_plan)
    plan = asyncio.run(
        resolve_infographic_plan(
            _outline(1), _brand(), SimpleNamespace(illustration_prompt="art"),
            roles=["CONTENT"], instructions="이미지는 넣지 말고",
        )
    )
    assert plan.scene_indices() == ()
    assert illustrations_are_prohibited("이미지는 넣지 말고")
    assert not illustrations_are_prohibited("표는 넣지 말고")


def test_a_failed_plan_call_leaves_the_deck_with_no_plan(monkeypatch):
    async def fake_plan(**_options):
        return None

    monkeypatch.setattr(plan_service, "generate_infographic_plan", fake_plan)
    assert (
        asyncio.run(
            resolve_infographic_plan(
                _outline(2), _brand(), SimpleNamespace(), roles=["CONTENT"] * 2
            )
        )
        is None
    )


def test_a_persisted_plan_round_trips_and_an_unreadable_one_is_absent():
    plan = _scene_plan(2)
    theme = {"infographicPlan": plan.model_dump(mode="json")}
    assert load_infographic_plan(theme) == plan
    assert load_infographic_plan({"infographicPlan": {"slides": [{"index": 5}]}}) is None
    assert load_infographic_plan(None) is None
