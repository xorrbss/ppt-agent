"""``SVG_TEMPLATE_STEP_PICTURES``: model-drawn step pictures for a template-redesign deck."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services.generation_core.assets import offer as offer_mod
from services.generation_core.drivers.svg import asset_slots
from utils.get_env_concurrency import get_svg_template_step_pictures_env
from tests.unit.test_vector_illustrations import _build, vector_fakes  # noqa: F401 - fixture


def test_the_knob_reads_on_true_and_1_and_defaults_to_off(monkeypatch):
    monkeypatch.delenv("SVG_TEMPLATE_STEP_PICTURES", raising=False)
    assert get_svg_template_step_pictures_env() is False
    for value in ("on", "True", "1"):
        monkeypatch.setenv("SVG_TEMPLATE_STEP_PICTURES", value)
        assert get_svg_template_step_pictures_env() is True
    monkeypatch.setenv("SVG_TEMPLATE_STEP_PICTURES", "spots")
    assert get_svg_template_step_pictures_env() is False


def test_only_a_template_redesign_deck_gets_step_pictures_and_only_with_the_knob(monkeypatch):
    redesign = SimpleNamespace(svg_template_pin={"templateId": "t"}, svg_template_mode="template-redesign")
    free = SimpleNamespace(svg_template_pin={"templateId": "t"}, svg_template_mode="free-design")
    plain = SimpleNamespace(svg_template_pin=None, svg_template_mode=None)
    monkeypatch.delenv("SVG_TEMPLATE_STEP_PICTURES", raising=False)
    assert asset_slots.template_step_pictures(redesign) is False
    monkeypatch.setenv("SVG_TEMPLATE_STEP_PICTURES", "on")
    assert asset_slots.template_step_pictures(redesign) is True
    assert asset_slots.template_step_pictures(free) is False
    assert asset_slots.template_step_pictures(plain) is False


def test_spots_alone_keep_the_off_mode_plan_and_add_step_pictures(vector_fakes):  # noqa: F811
    """Without the scene part the plan is made as in off mode and its scene page keeps its icons."""
    offer = _build(illustration_parts=("spots",))
    # The planner was asked for the provider guide (scene ceiling kept), not the vector one.
    assert vector_fakes["plan"] == [False]
    assert sorted({request.kind for request in vector_fakes["draw"]}) == ["spots"]
    spots = [slot for slot in offer.slots if slot.kind == "spot"]
    assert {slot.page_index for slot in spots} == {2} and len(spots) == 3
    assert all(slot.role == "figure-spot" and slot.concept for slot in spots)
    # The scene page (1) is demoted to its icon figure, the step page keeps no icon for a spotted step.
    icon_pages = {slot.page_index for slot in offer.slots if slot.kind == "icon"}
    assert 1 in icon_pages and 3 in icon_pages and 2 not in icon_pages
    assert not [slot for slot in offer.slots if slot.kind == "illustration"]
    assert offer.receipt["scenesPlanned"] == 0 and offer.receipt["vectorDrawings"]["parts"] == ["spots"]
    assert offer.receipt["spots"] == 3


def test_parts_with_a_scene_keep_the_vector_plan(vector_fakes):  # noqa: F811
    _build(illustration_parts=("scene", "spots"))
    assert vector_fakes["plan"] == [True]
    assert sorted({request.kind for request in vector_fakes["draw"]}) == ["scene", "spots"]


@pytest.mark.parametrize("mode", [None, "vector"])
def test_resolve_offer_turns_on_vector_spots_for_a_template_deck_while_the_mode_is_off(monkeypatch, mode):
    seen = {}

    async def build(request, outline, roles, job, profile, image_service, **kwargs):
        seen.update(kwargs, image_service=image_service)
        return offer_mod.AssetOffer(receipt={"status": "stub"})

    monkeypatch.setattr(asset_slots, "_build", build)
    monkeypatch.setattr(asset_slots, "_native_literal_pages", lambda *_a, **_k: None)
    monkeypatch.setattr("services.image_generation_service.ImageGenerationService",
                        lambda _directory: SimpleNamespace(is_image_generation_disabled=True, image_gen_func=None))
    monkeypatch.setattr("utils.asset_directory_utils.get_images_directory", lambda: "/nonexistent")
    monkeypatch.setenv("SVG_TEMPLATE_STEP_PICTURES", "on")
    monkeypatch.delenv("SVG_ILLUSTRATION_PARTS", raising=False)
    if mode is None:
        monkeypatch.delenv("SVG_ILLUSTRATION_MODE", raising=False)
    else:
        monkeypatch.setenv("SVG_ILLUSTRATION_MODE", mode)
    template = SimpleNamespace(svg_template_pin={"templateId": "t"}, svg_template_mode="template-redesign",
                               authored_style=None, instructions=None)
    profile = SimpleNamespace(illustration_budget=4, illustration_timeout_seconds=5)

    async def checkpoint(_job, _index, *, payload=None):
        return {}

    asyncio.run(asset_slots.resolve_offer(template, SimpleNamespace(slides=[]), [], None, profile=profile,
                                          checkpoint=checkpoint, durable=True))
    assert seen["icons_only"] is True and seen["illustration_mode"] == "vector"
    # An explicit illustration mode keeps its own parts; the knob fills in only while the mode is off.
    assert seen["illustration_parts"] == (("spots",) if mode is None else ("cover", "scene"))
    seen.clear()
    monkeypatch.delenv("SVG_ILLUSTRATION_MODE", raising=False)
    plain = SimpleNamespace(svg_template_pin=None, svg_template_mode=None, authored_style=None, instructions=None)
    asyncio.run(asset_slots.resolve_offer(plain, SimpleNamespace(slides=[]), [], None, profile=profile,
                                          checkpoint=checkpoint, durable=True))
    assert seen["icons_only"] is True and "illustration_mode" not in seen
