"""Frozen SVG template pictures use the existing vector offer without image calls."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from models.authored_infographic_plan import AuthoredInfographicPlan, AuthoredSlideInfographic, InfographicNode
from services.generation_core.assets import infographic_assets, infographic_plan, vector_offer
from services.generation_core.assets import offer as offer_mod
from utils.llm_calls import draw_vector_illustrations as drawing_calls
from utils.structured_call_budget import StructuredProviderCallBudget


POLICY = {"revision": "svg-template-pictures/v1", "parts": ("scene", "spots", "pairs", "pictograph"),
          "max_calls": 12, "exclude_cover": True}
PALETTE = ["#051C2C", "#00A3E0", "#E6EEF2"]


def _plan(kinds):
    return AuthoredInfographicPlan(slides=tuple(
        AuthoredSlideInfographic(index=index, archetype=kind, weight="standard", takeaway="Factory flow",
                                 nodes=tuple(InfographicNode(label=label, figure="70%" if node == 0 else None,
                                                            before="2", after="3")
                                             for node, label in enumerate(("Sensor", "Factory", "Flow"))))
        for index, kind in enumerate(kinds)))


def _outline(count):
    return SimpleNamespace(slides=[SimpleNamespace(content="Sensor Factory Flow: current 2, target 3, share 70%")
                                   for _ in range(count)])


def _style():
    return SimpleNamespace(id="synthetic-template", illustration_prompt="", reference_mode="style_reference",
                           icon_family="lucide", preview_palette=PALETTE, preview_bg="#FFFFFF",
                           preview_accent="#00A3E0", primary_color=None, background_color=None,
                           brief="Frozen geometric template")


def _brand():
    return SimpleNamespace(primary="#00A3E0", topic="Factory workflow", language="English")


def _svg(spec):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {spec.width} {spec.height}" '
            f'width="{spec.width}" height="{spec.height}">'
            f'<circle cx="{spec.width / 2}" cy="{spec.height / 2}" r="{spec.height / 3}" '
            'fill="#00A3E0" stroke="#051C2C" stroke-width="3"/></svg>')


@pytest.fixture
def offer_fakes(monkeypatch, tmp_path):
    calls = {"plan": [], "drawing": []}

    async def resolve(*_args, **kwargs):
        calls["plan"].append(kwargs)
        return _plan(("scene", "process", "before_after", "kpi"))

    async def draw(request, **kwargs):
        calls["drawing"].append((request, kwargs))
        if kwargs.get("call_budget") is not None:
            kwargs["call_budget"].consume()
        return {spec.id: _svg(spec) for spec in request.drawings}

    async def no_icons(*_args, **_kwargs):
        return {}

    async def empty_slots(*_args, **_kwargs):
        return []

    def no_provider(**_kwargs):
        pytest.fail("Template pictures must never build an image-provider context")

    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", resolve)
    monkeypatch.setattr(drawing_calls, "draw_vector_illustrations", draw)
    monkeypatch.setattr(offer_mod, "_family_icons", no_icons)
    monkeypatch.setattr(offer_mod, "_icon_slots", empty_slots)
    monkeypatch.setattr(infographic_assets, "build_infographic_asset_context", no_provider)
    monkeypatch.setattr("utils.asset_directory_utils.get_images_directory", lambda: str(tmp_path))
    return calls


def _offer(**overrides):
    values = dict(outline=_outline(4), brand=_brand(), style=_style(), roles=["content"] * 4,
                  instructions=None, budget=4, timeout_seconds=1, icons_only=True,
                  illustration_mode="vector", template_picture_policy=POLICY)
    values.update(overrides)
    return asyncio.run(offer_mod.build_asset_offer(**values))


def test_template_policy_uses_all_content_parts_without_an_image_provider(offer_fakes):
    offer = _offer(icons_only=False, illustration_mode="hybrid", image_service=object())
    assert sorted(request.kind for request, _ in offer_fakes["drawing"]) == ["pair", "scene", "spots"]
    assert {slot.role for slot in offer.slots} == {
        "scene", "figure-spot", "comparison-before", "comparison-after", "pictograph"}
    assert offer.receipt["providerCalls"] == 0 and offer.receipt["transparentProvider"] is False
    assert offer.receipt["vectorDrawings"]["physicalCalls"] == 3
    assert offer_fakes["plan"][0]["template_picture_policy"] == POLICY
    for slot in offer.slots:
        slot.verify()


def test_cover_section_and_closing_exclude_every_picture_part(offer_fakes):
    offer = _offer(roles=[" cover ", "section", "CLOSING", "content"])
    assert {slot.page_index for slot in offer.slots} == {3}
    assert {slot.role for slot in offer.slots} == {"pictograph"}
    assert offer_fakes["drawing"] == []


@pytest.mark.parametrize("budget", [0, 1, 2, 20])
def test_profile_budget_caps_enabled_drawing_calls_and_keeps_free_pictographs(offer_fakes, budget):
    offer = _offer(budget=budget)
    receipt = offer.receipt["vectorDrawings"]
    assert receipt["callLimit"] == min(12, budget)
    assert receipt["physicalCalls"] <= min(12, budget)
    assert len(offer_fakes["drawing"]) <= min(12, budget)
    assert any(slot.role == "pictograph" for slot in offer.slots)


def test_prohibition_short_circuits_every_picture_call(offer_fakes):
    offer = _offer(instructions="No pictures or illustrations")
    assert offer.slots == () and offer.receipt["status"] == "prohibited_by_instructions"
    assert offer_fakes == {"plan": [], "drawing": []}


def test_failed_and_retried_drawings_share_one_budget(offer_fakes, monkeypatch):
    async def no_answer(request, **kwargs):
        offer_fakes["drawing"].append((request, kwargs))
        kwargs["call_budget"].consume()
        return None

    monkeypatch.setattr(drawing_calls, "draw_vector_illustrations", no_answer)
    offer = _offer(budget=2)
    receipt = offer.receipt["vectorDrawings"]
    assert receipt["physicalCalls"] == 2 and receipt["calls"] == 2
    assert receipt["rejected"]["call_failed"] > 0
    assert receipt["rejected"]["budget_exhausted"] > 0
    assert {slot.role for slot in offer.slots} == {"pictograph"}


def test_drawing_exceptions_are_optional_and_do_not_drop_other_parts(offer_fakes, monkeypatch):
    async def raises(request, **kwargs):
        kwargs["call_budget"].consume()
        raise RuntimeError("synthetic optional failure")

    monkeypatch.setattr(drawing_calls, "draw_vector_illustrations", raises)
    offer = _offer()
    assert offer.receipt["status"] == "resolved"
    assert offer.receipt["vectorDrawings"]["rejected"]["error"] > 0
    assert {slot.role for slot in offer.slots} == {"pictograph"}


def test_timed_out_drawings_remain_optional_and_retry_within_budget(offer_fakes, monkeypatch):
    async def resolve(*_args, **_kwargs):
        return _plan(("scene",))

    async def blocked(_request, **kwargs):
        kwargs["call_budget"].consume()
        await asyncio.Future()

    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", resolve)
    monkeypatch.setattr(drawing_calls, "draw_vector_illustrations", blocked)
    offer = _offer(outline=_outline(1), roles=["content"], budget=2, timeout_seconds=0.001)
    assert offer.slots == () and offer.receipt["status"] == "resolved"
    receipt = offer.receipt["vectorDrawings"]
    assert receipt["physicalCalls"] == 2 and receipt["timeouts"] == 2 and receipt["retries"] == 1


@pytest.mark.parametrize("update", [
    {"revision": "svg-template-pictures/unknown"}, {"max_calls": True}, {"max_calls": 13},
    {"parts": ("cover",)}, {"parts": ({},)}, {"exclude_cover": False},
])
def test_explicit_invalid_policy_fails_closed(update):
    with pytest.raises(ValueError, match="svg_template_picture_policy_invalid"):
        vector_offer.validate_template_picture_policy({**POLICY, **update})


def test_legacy_vector_offer_keeps_parts_kwargs_budget_and_receipt(offer_fakes):
    implicit = _offer(template_picture_policy=None, roles=["cover", "content", "content", "content"], budget=0)
    explicit = _offer(template_picture_policy=None, roles=["cover", "content", "content", "content"], budget=0)
    assert implicit == explicit
    assert "templatePicturePolicy" not in implicit.receipt
    assert implicit.receipt["vectorDrawings"]["parts"] == ["cover", "scene"]
    assert implicit.receipt["vectorDrawings"]["calls"] == 2
    assert "physicalCalls" not in implicit.receipt["vectorDrawings"]
    assert all(kwargs == {} for _request, kwargs in offer_fakes["drawing"])
    assert all("template_picture_policy" not in kwargs for kwargs in offer_fakes["plan"])


def test_policy_excludes_malformed_cover_scene_jobs_and_preserves_default_jobs():
    plan = _plan(("scene", "scene", "scene", "scene"))
    common = dict(topic="workflow", palette={"DARK": PALETTE[0], "ACCENT": PALETTE[1]},
                  background="#FFFFFF", vector_scene_pages={0, 1, 2, 3})
    roles = ["COVER", "SECTION", "CLOSING", "CONTENT"]
    contents = [slide.content for slide in _outline(4).slides]
    enabled = vector_offer.plan_jobs(plan, contents, roles, template_picture_policy=POLICY, **common)
    old = vector_offer.plan_jobs(plan, contents, roles, **common)
    assert [(job.page, job.kind) for job in enabled] == [(3, "scene")]
    assert [job.kind for job in old].count("cover") == 3
    assert [job.kind for job in old].count("scene") == 4


def test_model_retries_never_exceed_profile_intersection_with_twelve(offer_fakes, monkeypatch):
    async def resolve(*_args, **_kwargs):
        return _plan(["scene"] * 14)

    async def missing(request, **kwargs):
        offer_fakes["drawing"].append((request, kwargs))
        kwargs["call_budget"].consume()
        return None

    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", resolve)
    monkeypatch.setattr(drawing_calls, "draw_vector_illustrations", missing)
    offer = _offer(outline=_outline(14), roles=["content"] * 14, budget=30)
    assert len(offer_fakes["drawing"]) == 12
    assert offer.receipt["vectorDrawings"]["physicalCalls"] == 12


def test_policy_guidance_uses_existing_planner_without_changing_default_kwargs(monkeypatch):
    captured = []

    async def generate(**kwargs):
        captured.append(kwargs)
        return None

    monkeypatch.setattr(infographic_plan, "generate_infographic_plan", generate)
    common = dict(outline=_outline(1), brand=_brand(), style=_style(), roles=["content"],
                  vector_illustrations=True)
    asyncio.run(infographic_plan.resolve_infographic_plan(**common))
    asyncio.run(infographic_plan.resolve_infographic_plan(**common, template_picture_policy=POLICY))
    assert captured[0]["style_brief"] == "Frozen geometric template"
    assert "small matched step pictures" in captured[1]["style_brief"]
    assert "cover, section or closing" in captured[1]["style_brief"]
    assert {key: value for key, value in captured[0].items() if key != "style_brief"} == {
        key: value for key, value in captured[1].items() if key != "style_brief"}


def _request():
    return drawing_calls.DrawingRequest(kind="scene", drawings=(drawing_calls.DrawingSpec("scene", 512, 460, "Factory"),),
                                        topic="Factory", palette={"DARK": PALETTE[0], "ACCENT": PALETTE[1]},
                                        background="#FFFFFF", semantic_slot="synthetic-drawing")


def test_drawing_transport_omits_optional_kwargs_by_default(monkeypatch):
    captured = []

    async def generate(*_args, **kwargs):
        captured.append(kwargs)
        return {"drawings": []}

    monkeypatch.setattr(drawing_calls, "generate_structured_with_schema_retries", generate)
    monkeypatch.setattr(drawing_calls, "get_client", lambda **_: object())
    monkeypatch.setattr(drawing_calls, "get_llm_config", lambda: None)
    monkeypatch.setattr(drawing_calls, "get_model", lambda: "synthetic")
    asyncio.run(drawing_calls.draw_vector_illustrations(_request()))
    budget = StructuredProviderCallBudget(limit=1)
    asyncio.run(drawing_calls.draw_vector_illustrations(_request(), call_budget=budget, call_timeout_seconds=0.25))
    assert "call_budget" not in captured[0] and "call_timeout_seconds" not in captured[0]
    enabled = dict(captured[1])
    assert enabled.pop("call_budget") is budget
    assert enabled.pop("call_timeout_seconds") == 0.25
    assert enabled == captured[0]


def test_actual_structured_parse_retries_share_physical_budget_and_transport_timeout(monkeypatch):
    from utils import llm_utils

    physical_timeouts = []

    async def generate(_client, **kwargs):
        physical_timeouts.append(kwargs["call_timeout_seconds"])
        return SimpleNamespace(content=None, response=None)

    @asynccontextmanager
    async def admission(**_kwargs):
        yield

    async def ignore(*_args, **_kwargs):
        return None

    monkeypatch.setattr(llm_utils, "_generate_structured_content", generate)
    monkeypatch.setattr(llm_utils, "provider_call_admission", admission)
    monkeypatch.setattr(llm_utils, "record_provider_call", ignore)
    monkeypatch.setattr(llm_utils, "supports_provider_idempotency", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(llm_utils, "_provider_receipt_identity", lambda: "synthetic")
    monkeypatch.setattr(llm_utils, "get_extra_body", lambda: None)
    monkeypatch.setattr(llm_utils.asyncio, "sleep", ignore)
    monkeypatch.setattr(drawing_calls, "get_client", lambda **_: object())
    monkeypatch.setattr(drawing_calls, "get_llm_config", lambda: None)
    monkeypatch.setattr(drawing_calls, "get_model", lambda: "synthetic")
    budget = StructuredProviderCallBudget(limit=2)

    async def draw_twice():
        first = await drawing_calls.draw_vector_illustrations(_request(), call_budget=budget, call_timeout_seconds=0.3)
        second = await drawing_calls.draw_vector_illustrations(_request(), call_budget=budget, call_timeout_seconds=0.3)
        return first, second

    assert asyncio.run(draw_twice()) == (None, None)
    assert budget.used == 2 and physical_timeouts == [0.3, 0.3]
