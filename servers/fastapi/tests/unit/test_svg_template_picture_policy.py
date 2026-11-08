"""Admitted template picture routing, frozen design identity and replay."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services.generation_core.assets.offer import AssetOffer
from services.generation_core.drivers.svg import asset_slots
from services.svg_templates.picture_policy import (
    TEMPLATE_PICTURES_REVISION, template_picture_guidance, template_picture_policy,
    template_picture_style,
)
from utils.authored_styles import resolve_authored_style


def _request(**changes):
    return SimpleNamespace(**{"strategy": "svg", "svg_template_pin": {"templateId": "synthetic"},
                              "svg_template_mode": "template-redesign", "authored_style": None,
                              "instructions": None, **changes})


def _snapshot(**changes):
    return {"template_pictures": True, "template_pictures_revision": TEMPLATE_PICTURES_REVISION,
            "svg_template": {"mode": "template-redesign", "style_projection": {
                "tokens": {"background": "#FAFAFA", "foreground": "#142638",
                           "accent": "#338855", "palette": ["#338855", "#142638", "#FAFAFA"]},
                "fonts": {"heading": {"family": "Synthetic Sans"}, "body": {"family": "Synthetic Sans"}},
                "references": {"content": [{"text": "UNTRUSTED TEMPLATE WORDING"}]},
                "composition_guidance": ["UNTRUSTED TEMPLATE WORDING"]}}, **changes}


@pytest.mark.parametrize("flag", [None, False, 0, 1, "true", "on"])
def test_only_literal_true_activates_admitted_policy(flag):
    snapshot = _snapshot(template_pictures=flag)
    assert template_picture_policy(_request(), snapshot) is None
    assert template_picture_guidance(snapshot) == ""


@pytest.mark.parametrize("revision", [None, "svg-template-pictures/v0", "future"])
def test_missing_or_unknown_revision_preserves_the_old_path(revision):
    snapshot = _snapshot(template_pictures_revision=revision)
    assert template_picture_policy(_request(), snapshot) is None
    assert template_picture_guidance(snapshot) == ""


def test_policy_is_confined_to_svg_pinned_template_redesign():
    snapshot = _snapshot()
    assert template_picture_policy(_request(), snapshot)["parts"] == ("scene", "spots", "pairs", "pictograph")
    for request in (_request(strategy="authored"), _request(svg_template_pin=None),
                    _request(svg_template_mode="free-design")):
        assert template_picture_policy(request, snapshot) is None
    assert template_picture_policy(_request(), {}) is None
    assert template_picture_policy(_request(), None) is None
    assert template_picture_guidance(_snapshot(svg_template={"mode": "free-design"})) == ""


def test_guidance_uses_authoritative_body_and_optional_omission():
    guidance = template_picture_guidance(_snapshot())
    assert "template_page.body_region" in guidance
    assert "Omit an optional picture" in guidance and "never omit required facts" in guidance
    assert "do not add a second cover picture" in guidance
    assert "x=10, y=20, width=800, height=400" in template_picture_guidance(
        _snapshot(), body_bbox={"x": 10, "y": 20, "width": 800, "height": 400})
    with pytest.raises(ValueError, match="body_bbox_invalid"):
        template_picture_guidance(_snapshot(), body_bbox=(0, 0, -1, 10))


def test_style_uses_frozen_design_tokens_without_template_wording():
    old_style = resolve_authored_style(None)
    style = template_picture_style(_snapshot(), old_style)
    assert style.primary_color == "#338855" and style.background_color == "#FAFAFA"
    assert style.heading_font == style.body_font == "Synthetic Sans"
    assert set(style.preview_palette) == {"#338855", "#142638", "#FAFAFA"}
    assert "UNTRUSTED" not in style.brief
    assert style.illustration_prompt is None and style.reference_images == {}
    assert resolve_authored_style(None) == old_style


def test_enabled_route_never_reads_live_image_settings(monkeypatch):
    seen = {}

    async def build(*args, **kwargs):
        seen.update(kwargs, image_service=args[5])
        return AssetOffer(receipt={"status": "synthetic"})

    def forbidden(*args, **kwargs):
        raise AssertionError("enabled template pictures must not read live image settings")

    monkeypatch.setattr(asset_slots, "_build", build)
    monkeypatch.setattr("services.image_generation_service.ImageGenerationService", forbidden)
    monkeypatch.setattr("utils.get_env.get_svg_illustration_mode_env", forbidden)
    monkeypatch.setattr("utils.get_env.get_svg_illustration_parts_env", forbidden)

    async def checkpoint(*args, **kwargs):
        return {}

    offer = asyncio.run(asset_slots.resolve_offer(
        _request(), SimpleNamespace(slides=[]), [], None, profile=SimpleNamespace(),
        checkpoint=checkpoint, durable=True, style_snapshot=_snapshot()))
    assert offer.receipt["status"] == "synthetic"
    assert seen["image_service"] is None and seen["icons_only"] is True
    assert seen["illustration_mode"] == "vector"
    assert seen["template_picture_policy"]["revision"] == TEMPLATE_PICTURES_REVISION


def test_enabled_driver_passes_frozen_style_and_policy_into_the_real_offer(monkeypatch):
    from services.generation_core.assets import infographic_plan
    from services.generation_core.authoring.author_slide import Brand

    seen = {}

    async def plan(outline, brand, style, **kwargs):
        seen.update(kwargs, style=style, brand=brand)
        return None  # Optional planning failure leaves the authored page usable.

    def forbidden(*args, **kwargs):
        raise AssertionError("template vector pictures cannot construct an image provider")

    async def checkpoint(*args, **kwargs):
        return {}

    monkeypatch.setattr("services.generation_core.style.build.resolve_brand", lambda *a: Brand(
        topic="Synthetic factory", primary="#FF0000", primary_is_explicit=True))
    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", plan)
    monkeypatch.setattr("services.image_generation_service.ImageGenerationService", forbidden)
    profile = SimpleNamespace(illustration_budget=2, illustration_timeout_seconds=1)
    offer = asyncio.run(asset_slots.resolve_offer(
        _request(), SimpleNamespace(slides=[]), [], None, profile=profile,
        checkpoint=checkpoint, durable=True, style_snapshot=_snapshot()))
    assert offer.receipt["status"] == "plan_unavailable"
    assert offer.receipt["templatePicturePolicy"]["revision"] == TEMPLATE_PICTURES_REVISION
    assert seen["template_picture_policy"]["parts"] == ("scene", "spots", "pairs", "pictograph")
    assert seen["vector_illustrations"] is True
    assert seen["style"].primary_color == seen["brand"].primary == "#338855"
    assert seen["style"].heading_font == "Synthetic Sans"
    assert seen["brand"].primary_is_explicit is False
    assert "UNTRUSTED" not in seen["style"].brief


@pytest.mark.parametrize("snapshot", [{}, _snapshot(template_pictures=False),
                                       _snapshot(template_pictures_revision="future")])
def test_disabled_route_preserves_existing_build_arguments(monkeypatch, snapshot):
    calls = []

    async def build(*args, **kwargs):
        calls.append(kwargs)
        return AssetOffer(receipt={"status": "legacy"})

    monkeypatch.setattr(asset_slots, "_build", build)
    monkeypatch.setattr(asset_slots, "_native_literal_pages", lambda *a, **k: None)
    monkeypatch.setattr("services.image_generation_service.ImageGenerationService", lambda *a: SimpleNamespace(
        is_image_generation_disabled=True, image_gen_func=None))
    monkeypatch.setattr("utils.asset_directory_utils.get_images_directory", lambda: "/synthetic")
    monkeypatch.setattr("utils.get_env.get_svg_illustration_mode_env", lambda: "off")
    monkeypatch.setattr("utils.get_env.get_svg_illustration_parts_env", lambda: ("cover", "scene"))
    monkeypatch.setattr(asset_slots, "template_step_pictures", lambda request: False)

    async def checkpoint(*args, **kwargs):
        return {}

    offer = asyncio.run(asset_slots.resolve_offer(
        _request(), SimpleNamespace(slides=[]), [], None, profile=SimpleNamespace(),
        checkpoint=checkpoint, durable=True, style_snapshot=snapshot))
    assert calls == [{"icons_only": True, "projection_revision": "svg-asset-projection/v1"}]
    assert offer == AssetOffer(receipt={"status": "legacy"})


def test_checkpoint_replay_precedes_new_policy_and_all_settings(monkeypatch, tmp_path):
    original = AssetOffer(receipt={"status": "legacy_icons_only", "icons": 2})
    seed = {asset_slots.SEED_KEY: original.to_json(root=tmp_path)}

    async def checkpoint(*args, **kwargs):
        assert "payload" not in kwargs
        return seed

    async def forbidden(*args, **kwargs):
        raise AssertionError("checkpoint replay must not rebuild or repurchase")

    monkeypatch.setattr(asset_slots, "get_app_data_directory_env", lambda: str(tmp_path))
    monkeypatch.setattr(asset_slots, "_build", forbidden)
    replayed = asyncio.run(asset_slots.resolve_offer(
        _request(), SimpleNamespace(slides=[]), [], SimpleNamespace(id="synthetic"),
        profile=SimpleNamespace(), checkpoint=checkpoint, durable=True, style_snapshot=_snapshot()))
    assert replayed == original
