"""Model-drawn vector pictures: validation, planning, the offer stage and projection."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.authored_infographic_plan import (
    AuthoredInfographicPlan,
    AuthoredSlideInfographic,
    InfographicNode,
    coerce_infographic_plan,
)
from services.generation_core.assets import infographic_plan, vector_offer
from services.generation_core.assets import offer as offer_mod
from services.generation_core.assets.vector_illustration import palette_of, validate_drawing
from utils.llm_calls import draw_vector_illustrations as drawing_calls
from utils.llm_calls import select_deck_icons as icon_selection

PALETTE = ["#051C2C", "#00A3E0", "#E6EEF2", "#A7B0B7"]


def _svg(width: int, height: int, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}">{body}</svg>')


GOOD_BODY = ('<!-- a monitor -->'
             '<circle cx="256" cy="230" r="180" fill="#E6EEF2"/>'
             '<rect x="150" y="120" width="210" height="140" rx="8" fill="#FFFFFF" stroke="#051C2C" '
             'stroke-width="3" stroke-linecap="round"/>'
             '<path d="M180 200 C 220 160 260 240 330 170" fill="none" stroke="#00a3e1" stroke-width="3"/>')


def test_a_good_drawing_is_normalised_into_contract_shapes_in_the_palette():
    drawing = validate_drawing(_svg(512, 460, GOOD_BODY), width=512, height=460, palette=PALETTE, ink="#051C2C")
    assert drawing.snapped_paints == 2  # #FFFFFF and #00a3e1 moved onto the palette
    assert set(palette_of(drawing.svg)) <= set(PALETTE)
    assert "<!--" not in drawing.svg and "C " not in drawing.svg  # curves sampled into line segments
    assert 'stroke-linecap="round"' in drawing.svg
    assert 0.3 < drawing.fill_ratio <= 1.0


@pytest.mark.parametrize(("body", "reason"), [
    ('<text x="10" y="10">AI</text>', "vector_illustration_element_forbidden:text"),
    ('<image href="x.png" x="0" y="0" width="10" height="10"/>', "vector_illustration_element_forbidden:image"),
    ('<rect x="0" y="0" width="512" height="460" fill="#E6EEF2"/>', "vector_illustration_background_rectangle"),
    ('<circle cx="500" cy="230" r="80" fill="#00A3E0"/>', "vector_illustration_overflow"),
    ('<rect x="10" y="10" width="20" height="20" style="fill:red"/>', "vector_illustration_attribute_forbidden:style"),
    ("", "vector_illustration_empty"),
])
def test_unacceptable_drawings_are_rejected_with_a_reason(body, reason):
    with pytest.raises(ValueError, match=reason):
        validate_drawing(_svg(512, 460, body), width=512, height=460, palette=PALETTE, ink="#051C2C")


def test_a_drawing_of_the_wrong_size_or_with_declarations_is_rejected():
    with pytest.raises(ValueError, match="canvas_mismatch"):
        validate_drawing(_svg(300, 300, GOOD_BODY), width=512, height=460, palette=PALETTE, ink="#051C2C")
    with pytest.raises(ValueError, match="declaration_forbidden"):
        validate_drawing('<!DOCTYPE svg>' + _svg(512, 460, GOOD_BODY), width=512, height=460,
                         palette=PALETTE, ink="#051C2C")


def test_vector_mode_plans_lift_the_scene_ceiling_and_keep_depiction():
    contents = [f"# 슬라이드 {i}\n- 자동화 센서 경보 현장" for i in range(9)]
    roles = ["cover"] + ["content"] * 8
    payload = {"slides": [{"index": i, "archetype": "scene", "weight": "standard", "takeaway": "현장",
                           "nodes": [{"label": "자동화"}, {"label": "센서"}, {"label": "경보"}],
                           "depiction": "photo" if i == 2 else "concept"} for i in range(1, 9)]}
    capped = coerce_infographic_plan(payload, slide_contents=contents, roles=roles)
    open_ = coerce_infographic_plan(payload, slide_contents=contents, roles=roles, scene_ceiling=False)
    assert len(capped.scene_indices()) == 2 and len(open_.scene_indices()) == 8
    assert open_.slides[2].depiction == "photo" and open_.slides[3].depiction == "concept"


def test_the_vector_plan_prompt_allows_abstract_scenes():
    from utils.llm_calls.author_infographic_plan import build_infographic_plan_messages

    common = dict(topic="t", language="Korean", slide_contents=["a"], roles=["content"])
    provider = build_infographic_plan_messages(**common)[-1].content
    vector = build_infographic_plan_messages(**common, vector_illustrations=True)[-1].content
    assert "never for\n  an abstract concept" in provider and "never for\n  an abstract concept" not in vector
    assert "visual metaphor" in vector and "depiction" in vector


def _nodes(*labels):
    return tuple(InfographicNode(label=label) for label in labels)


def _plan():
    return AuthoredInfographicPlan(slides=(
        AuthoredSlideInfographic(index=0),
        AuthoredSlideInfographic(index=1, archetype="scene", weight="hero", takeaway="현장 점검",
                                 nodes=_nodes("센서", "경보", "현장"), depiction="concept"),
        AuthoredSlideInfographic(index=2, archetype="process", weight="standard",
                                 nodes=_nodes("수집", "분석", "대응")),
        AuthoredSlideInfographic(index=3, archetype="icon_list", weight="standard",
                                 nodes=_nodes("보안", "비용", "속도")),
    ))


def _outline():
    return SimpleNamespace(slides=[SimpleNamespace(content=f"# 슬라이드 {i}\n- 수집 분석 대응 센서 경보 현장 보안 비용 속도")
                                   for i in range(4)])


@pytest.fixture
def vector_fakes(monkeypatch, tmp_path):
    calls = {"draw": [], "plan": []}

    async def plan(*_args, vector_illustrations=False, **_kwargs):
        calls["plan"].append(vector_illustrations)
        return _plan()

    async def draw(request):
        calls["draw"].append(request)
        return {spec.id: _svg(spec.width, spec.height,
                              f'<circle cx="{spec.width / 2}" cy="{spec.height / 2}" r="{spec.height / 3}" '
                              'fill="#00A3E0" stroke="#051C2C" stroke-width="3"/>')
                for spec in request.drawings}

    async def picks(labels, names):
        return {label: ("circle-check", None) for label in labels}

    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", plan)
    monkeypatch.setattr(drawing_calls, "draw_vector_illustrations", draw)
    monkeypatch.setattr(icon_selection, "select_deck_icons", picks)
    monkeypatch.setattr("utils.asset_directory_utils.get_images_directory", lambda: str(tmp_path / "images"))
    return calls


def _style():
    return SimpleNamespace(id="exec-report", illustration_prompt="", reference_mode="style_reference",
                           icon_family="lucide", preview_palette=PALETTE, preview_bg="#FFFFFF",
                           preview_accent="#00A3E0", background_color=None, primary_color=None)


def _build(**overrides):
    kwargs = dict(outline=_outline(), brand=SimpleNamespace(primary="#2563EB", topic="현장 자동화"),
                  style=_style(), roles=["cover", "content", "content", "content"], instructions=None,
                  budget=4, timeout_seconds=30, icons_only=True, illustration_mode="vector",
                  illustration_parts=("cover", "scene", "spots", "pairs"))
    kwargs.update(overrides)
    return asyncio.run(offer_mod.build_asset_offer(**kwargs))


def test_vector_mode_draws_cover_scene_and_step_pictures_without_a_provider(vector_fakes):
    offer = _build()
    assert vector_fakes["plan"] == [True]
    kinds = sorted({request.kind for request in vector_fakes["draw"]})
    assert kinds == ["cover", "scene", "spots"]
    by_role = {}
    for slot in offer.slots:
        by_role.setdefault(slot.role, []).append(slot)
    assert [slot.page_index for slot in by_role["cover"]] == [0]
    assert [slot.page_index for slot in by_role["scene"]] == [1]
    assert sorted(slot.concept for slot in by_role["figure-spot"]) == ["대응", "분석", "수집"]
    assert all(slot.kind == "spot" and slot.media_type == "image/svg+xml" for slot in by_role["figure-spot"])
    # step pictures replace those nodes' icons; the icon_list page keeps its icons
    icons = [slot for slot in offer.slots if slot.kind == "icon"]
    assert {slot.page_index for slot in icons} == {3}
    receipt = offer.receipt["vectorDrawings"]
    assert receipt["accepted"] == 5 and receipt["calls"] == 3 and receipt["rejected"] == {}
    assert offer.receipt["providerCalls"] == 0 and offer.receipt["status"] == "resolved"
    for slot in offer.slots:
        slot.verify()


def test_a_rejected_single_picture_is_asked_for_once_more(vector_fakes, monkeypatch):
    attempts = []

    async def draw(request):
        attempts.append(request.semantic_slot)
        if request.kind == "scene" and len([a for a in attempts if "scene" in a]) == 1:
            return {request.drawings[0].id: _svg(10, 10, "")}  # wrong size: rejected
        return {spec.id: _svg(spec.width, spec.height, f'<circle cx="{spec.width / 2}" cy="{spec.height / 2}" '
                                                      'r="20" fill="#00A3E0"/>') for spec in request.drawings}

    monkeypatch.setattr(drawing_calls, "draw_vector_illustrations", draw)
    offer = _build()
    assert "vector_drawing_p1_scene" in attempts and "vector_drawing_p1_scene_retry" in attempts
    receipt = offer.receipt["vectorDrawings"]
    assert receipt["retries"] == 1 and receipt["rejected"] == {"vector_illustration_canvas_mismatch": 1}
    assert any(slot.role == "scene" for slot in offer.slots)


def test_off_mode_draws_nothing(vector_fakes):
    offer = _build(illustration_mode="off")
    assert vector_fakes["draw"] == [] and vector_fakes["plan"] == [False]
    assert "vectorDrawings" not in offer.receipt


def test_hybrid_mode_sends_a_photograph_scene_to_the_provider(vector_fakes, monkeypatch):
    def jobs(plan, contents, roles, **kwargs):
        jobs.pages = kwargs["vector_scene_pages"]
        return []

    monkeypatch.setattr(vector_offer, "plan_jobs", jobs)
    photo = _plan().model_copy(update={"slides": tuple(
        slide.model_copy(update={"depiction": "photo"}) if slide.index == 1 else slide for slide in _plan().slides)})

    async def plan(*_args, **_kwargs):
        return photo

    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", plan)
    _build(illustration_mode="vector")
    assert jobs.pages == {1}
    # hybrid without a provider context still draws it
    _build(illustration_mode="hybrid")
    assert jobs.pages == {1}


def test_projection_places_drawings_as_multicolour_vector_slots(vector_fakes, monkeypatch, tmp_path):
    import uuid

    from services.generation_core.drivers.svg import asset_slots

    offer = _build()

    class Session:
        async def get(self, *_args):
            return None

        def add(self, _row):
            pass

        async def flush(self):
            pass

    projected = asyncio.run(asset_slots.project_offer(Session(), offer, presentation_id=uuid.uuid4(), count=4))
    drawings = [entry for page in projected.vector for entry in page.values() if entry["kind"] in {"illustration", "spot"}]
    assert len(drawings) == 5 and all(entry["vector"].multicolor for entry in drawings)
    assert projected.receipt["drawingsPlaced"] == 5
    assert Path(offer.slots[0].path).is_file()


def test_an_omitted_drawing_is_recorded_but_an_omitted_icon_still_fails():
    from services.generation_core.drivers.svg.embed_assets import apply_vector_icon_quality

    quality = {"passed": True}
    apply_vector_icon_quality(quality, {"assetOffer": {"vectorIcons": {
        "offered": ["drawing", "icon"], "used": ["icon"], "optional": ["drawing"]}}})
    assert quality["passed"] is True
    assert quality["assetOffer"]["omitted"] == [] and quality["assetOffer"]["omittedOptional"] == ["drawing"]
    apply_vector_icon_quality(quality, {"assetOffer": {"vectorIcons": {
        "offered": ["drawing", "icon"], "used": ["drawing"], "optional": ["drawing"]}}})
    assert quality["passed"] is False and quality["assetOffer"]["omitted"] == ["icon"]


def test_the_default_parts_draw_only_cover_and_scene_pictures(vector_fakes):
    offer = _build(illustration_parts=None)
    assert sorted({request.kind for request in vector_fakes["draw"]}) == ["cover", "scene"]
    assert not [slot for slot in offer.slots if slot.kind == "spot"]
    # the process page keeps its icons when it gets no step pictures
    assert {slot.page_index for slot in offer.slots if slot.kind == "icon"} == {2, 3}
    assert offer.receipt["vectorDrawings"]["parts"] == ["cover", "scene"]


@pytest.mark.parametrize(("raw", "parts"), [
    (None, ("cover", "scene")), ("", ("cover", "scene")), ("bogus", ("cover", "scene")),
    ("spots, pairs", ("spots", "pairs")), ("cover,scene,spots,pairs", ("cover", "scene", "spots", "pairs")),
])
def test_the_parts_knob(monkeypatch, raw, parts):
    from utils.get_env_concurrency import get_svg_illustration_parts_env

    if raw is None:
        monkeypatch.delenv("SVG_ILLUSTRATION_PARTS", raising=False)
    else:
        monkeypatch.setenv("SVG_ILLUSTRATION_PARTS", raw)
    assert get_svg_illustration_parts_env() == parts


def test_a_kpi_percentage_becomes_an_exact_pictograph_in_the_deck_family(vector_fakes, monkeypatch):
    kpi = _plan().model_copy(update={"slides": _plan().slides[:3] + (AuthoredSlideInfographic(
        index=3, archetype="kpi", weight="standard", emphasis=1,
        nodes=(InfographicNode(label="보안", figure="12건"), InfographicNode(label="비용", figure="70%"),
               InfographicNode(label="속도", figure="2배"))),)})

    async def plan(*_args, **_kwargs):
        return kpi

    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", plan)
    offer = _build(illustration_parts=("pictograph",))
    assert vector_fakes["draw"] == []  # composed, not drawn
    (slot,) = [slot for slot in offer.slots if slot.role == "pictograph"]
    assert (slot.page_index, slot.kind, slot.concept, slot.origin) == (3, "spot", "비용", "pictograph")
    assert "7 of 10" in slot.alt
    svg = Path(slot.path).read_text(encoding="utf-8")
    # lucide user-round is two shapes per person: 3 muted people -> 6 muted shapes
    assert svg.count('opacity="0.25"') == 6
    assert offer.receipt["vectorDrawings"]["pictographs"] == 1
    # the node with the pictograph gets no icon; its neighbours keep theirs
    assert {slot.concept for slot in offer.slots if slot.kind == "icon" and slot.page_index == 3} == {"보안", "속도"}


@pytest.mark.parametrize("family_id", ["lucide", "tabler-outline", "phosphor-regular", "phosphor-fill",
                                       "phosphor-duotone"])
def test_every_family_can_compose_a_pictograph(family_id):
    from services.generation_core.assets.icon_pack.families import compose_pictograph_svg

    svg = compose_pictograph_svg(total=10, highlighted=4, color="#0E7C7B", family_id=family_id)
    drawing = validate_drawing(svg, width=640, height=256, palette=["#0E7C7B"], ink="#0E7C7B")
    assert drawing.elements >= 10


_PROJECTION_BODY = ('<g fill-opacity="0.5"><circle cx="256" cy="230" r="150" '
                    'fill="#00A3E0" fill-opacity="0.5"/></g>')


def test_asset_projection_legacy_drawing_normalization_matches_7c_bytes():
    from hashlib import sha256
    from services.generation_core.drivers.svg.icon_flatten import ASSET_PROJECTION_LEGACY
    args = dict(width=512, height=460, palette=PALETTE, ink="#051C2C")
    svg = _svg(512, 460, _PROJECTION_BODY)
    legacy = validate_drawing(svg, **args, projection_revision=ASSET_PROJECTION_LEGACY)
    assert sha256(legacy.svg.encode()).hexdigest() == "417492234e4fed5d01522e55e42a94036ba43714d191394b1782ccb48dc188fd"
    current = validate_drawing(svg, **args)
    assert legacy.svg != current.svg
    assert 'fill-opacity="0.25"' in legacy.svg
    assert 'fill-opacity="0.5"' in current.svg
    with pytest.raises(ValueError, match="svg_asset_projection_revision_invalid"):
        validate_drawing(svg, **args, projection_revision="forged")


@pytest.mark.parametrize("current", [False, True])
def test_asset_projection_seedless_resume_pins_drawing_generation(vector_fakes, monkeypatch, tmp_path, current):
    import uuid
    from services.generation_core.drivers.svg import asset_slots
    from services.generation_core.drivers.svg.icon_flatten import (
        ASSET_PROJECTION_KEY, ASSET_PROJECTION_CURRENT, ASSET_PROJECTION_LEGACY,
    )
    from utils.hashing import canonical_json_digest
    stored = {}
    responses = {}

    async def checkpoint(_job, _index, *, payload=None):
        if payload is not None:
            stored.update(payload)
        return dict(stored)

    async def draw(request):
        vector_fakes["draw"].append(request)
        result = {spec.id: _svg(spec.width, spec.height,
            f'<g fill-opacity="0.5"><circle cx="{spec.width / 2}" cy="{spec.height / 2}" '
            f'r="{spec.height / 3}" fill="#00A3E0" fill-opacity="0.5"/></g>')
            for spec in request.drawings}
        responses.update(result)
        return result

    monkeypatch.setattr(drawing_calls, "draw_vector_illustrations", draw)
    monkeypatch.setattr("services.image_generation_service.ImageGenerationService",
        lambda *_a: SimpleNamespace(is_image_generation_disabled=True, image_gen_func=None))
    monkeypatch.setattr("utils.authored_styles.resolve_authored_style", lambda *_a: _style())
    monkeypatch.setattr("services.generation_core.style.build.resolve_brand",
        lambda *_a: SimpleNamespace(primary="#2563EB", topic="현장 자동화"))
    monkeypatch.setattr("utils.get_env.get_svg_illustration_mode_env", lambda: "vector")
    monkeypatch.setattr("utils.get_env.get_svg_illustration_parts_env", lambda: ("cover", "scene", "spots"))
    monkeypatch.setattr(asset_slots, "get_app_data_directory_env", lambda: str(tmp_path))
    snapshot = {ASSET_PROJECTION_KEY: ASSET_PROJECTION_CURRENT} if current else {}
    snapshot["digest"] = canonical_json_digest(snapshot, allow_nan=True)
    request = SimpleNamespace(authored_style=None, instructions=None, strategy="svg", language="Korean",
        asset_projection_revision=ASSET_PROJECTION_LEGACY if current else ASSET_PROJECTION_CURRENT)
    job = SimpleNamespace(id=uuid.uuid4(), attempt_number=1)
    args = dict(profile=SimpleNamespace(illustration_budget=4, illustration_timeout_seconds=5),
        checkpoint=checkpoint, durable=True, style_snapshot=snapshot, has_source_assets=False)
    roles = ["cover", "content", "content", "content"]
    offer = asyncio.run(asset_slots.resolve_offer(request, _outline(), roles, job, **args))
    assert vector_fakes["draw"]
    drawings = [slot for slot in offer.slots if slot.origin == "model-vector"]
    assert drawings
    revision = ASSET_PROJECTION_CURRENT if current else ASSET_PROJECTION_LEGACY
    palette = list(dict.fromkeys(offer.receipt["vectorDrawings"]["palette"].values()))
    for slot in drawings:
        actual = slot.verify().decode()
        expected = [validate_drawing(svg, width=slot.width, height=slot.height, palette=palette,
            ink=offer.receipt["vectorDrawings"]["palette"]["DARK"], projection_revision=revision).svg
            for svg in responses.values() if f'width="{slot.width}" height="{slot.height}"' in svg]
        assert actual in expected
        assert ('fill-opacity="0.5"' if current else 'fill-opacity="0.25"') in actual
    call_count = len(vector_fakes["draw"])
    replay = asyncio.run(asset_slots.resolve_offer(request, _outline(), roles, job, **args))
    assert replay.to_json(root=tmp_path) == offer.to_json(root=tmp_path)
    assert len(vector_fakes["draw"]) == call_count
    with pytest.raises(ValueError, match="svg_asset_projection_revision_invalid"):
        asyncio.run(asset_slots.resolve_offer(request, _outline(), roles, job,
            **{**args, "style_snapshot": {ASSET_PROJECTION_KEY: "forged"}}))
    assert len(vector_fakes["draw"]) == call_count


def test_asset_projection_unknown_revision_rejected_before_drawing_builder(vector_fakes):
    with pytest.raises(ValueError, match="svg_asset_projection_revision_invalid"):
        _build(projection_revision="forged")
    assert vector_fakes["draw"] == [] and vector_fakes["plan"] == []
