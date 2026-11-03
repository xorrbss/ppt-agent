"""AssetOffer builder and its SVG projection (generation-core C3)."""
from __future__ import annotations

import asyncio
import hashlib
import io
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from models.authored_infographic_plan import (
    AuthoredInfographicPlan,
    AuthoredSlideInfographic,
    InfographicNode,
)
from services.generation_core.assets import icon_pack, infographic_assets, infographic_plan
from services.generation_core.assets import offer as offer_mod
from services.generation_core.assets.icon_pack.service import IconResolution, ResolvedIcon
from services.generation_core.assets.icon_pack.store import concept_key
from services.generation_core.drivers.svg import asset_slots
from services.image_generation_service import _metered_image_provider_call
from utils.llm_calls import select_deck_icons as icon_selection

LIBRARY = Path(infographic_assets.__file__).resolve().parents[5] / "assets" / "infographics" / "monochrome-v1"
ICON_SVG = LIBRARY / "originals" / "ai-01-neural-network.svg"


def _png(path: Path, size=(64, 48), shade: int = 90) -> Path:
    buffer = io.BytesIO()
    Image.new("RGBA", size, (30, shade, 200, 255)).save(buffer, format="PNG")
    path.write_bytes(buffer.getvalue())
    return path


def _nodes(*labels):
    return tuple(InfographicNode(label=label) for label in labels)


def _plan():
    return AuthoredInfographicPlan(slides=(
        AuthoredSlideInfographic(index=0),
        AuthoredSlideInfographic(index=1, archetype="scene", weight="hero", takeaway="현장 점검",
                                 nodes=_nodes("센서", "경보", "현장")),
        AuthoredSlideInfographic(index=2, archetype="process", weight="hero",
                                 nodes=_nodes("수집", "분석", "대응")),
    ))


def _outline():
    return SimpleNamespace(slides=[SimpleNamespace(content=f"# 슬라이드 {i}\n- 수집 분석 대응 센서 경보")
                                   for i in range(3)])


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    calls = {"cutout": 0, "generate": []}

    async def plan(*_args, **_kwargs):
        return _plan()

    async def cutout(**kwargs):
        calls["cutout"] += 1
        async with _metered_image_provider_call(provider="fake", model="test", semantic_slot="image.cutout"):
            return SimpleNamespace(path=str(_png(tmp_path / f"cutout-{calls['cutout']}.png")))

    async def resolve(labels, *, generate, result=None, **_kwargs):
        calls["generate"].append((list(labels), generate))
        result = result if result is not None else IconResolution()
        if generate:
            async with _metered_image_provider_call(provider="fake", model="test", semantic_slot="image.icon_sheet"):
                result.sheets_attempted += 1
                result.sheets_generated += 1
        for label in labels:
            if label == "수집":
                # the real resolver serves a tinted copy from the pack directory
                copy = tmp_path / "reusable.svg"
                copy.write_text(ICON_SVG.read_text(encoding="utf-8"), encoding="utf-8")
                result.icons[concept_key(label)] = ResolvedIcon(label, str(copy), 128, 128, "library")
            elif generate:
                path = _png(tmp_path / f"icon-{len(result.icons)}.png", (96, 96), shade=len(result.icons) * 40)
                result.icons[concept_key(label)] = ResolvedIcon(label, str(path), 96, 96, "generated")
            else:
                result.missing.append(label)
        return result

    async def no_picks(labels, names):
        calls.setdefault("selection", []).append(list(labels))
        return {}

    context = infographic_assets.InfographicAssetContext(pack_key="p", output_directory=str(tmp_path))
    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", plan)
    monkeypatch.setattr(icon_pack, "generate_scene_cutout", cutout)
    monkeypatch.setattr(icon_pack, "resolve_icons", resolve)
    monkeypatch.setattr(infographic_assets, "build_infographic_asset_context", lambda **_: context)
    monkeypatch.setattr(icon_selection, "select_deck_icons", no_picks)
    monkeypatch.setattr("utils.asset_directory_utils.get_images_directory", lambda: str(tmp_path / "images"))
    return calls


def _build(**overrides):
    kwargs = dict(outline=_outline(), brand=SimpleNamespace(primary="#123456"),
                  style=SimpleNamespace(id="s", illustration_prompt="", reference_mode="style_reference"),
                  roles=["cover", "content", "content"], instructions=None, budget=4,
                  timeout_seconds=5)
    kwargs.update(overrides)
    return asyncio.run(offer_mod.build_asset_offer(**kwargs))


def test_offer_plans_one_illustration_per_scene_and_library_icons(fakes):
    offer = _build()
    kinds = [(slot.page_index, slot.kind, slot.origin) for slot in offer.slots]
    assert (1, "illustration", "cutout") in kinds
    icons = [slot for slot in offer.slots if slot.kind == "icon"]
    assert [slot.concept for slot in icons] == ["수집"]
    assert {slot.page_index for slot in icons} == {2} and icons[0].is_vector
    # one scene call; icons never reach the image provider
    assert offer.receipt["providerCalls"] == 1
    assert offer.receipt["illustrations"] == 1 and offer.receipt["icons"] == 1


def test_budget_bounds_provider_calls(fakes):
    offer = _build(budget=1)
    assert offer.receipt["providerCalls"] == 1
    # the sheet was not affordable: only the free library icon remains
    assert [slot.concept for slot in offer.slots if slot.kind == "icon"] == ["수집"]
    assert all(generate is False for _labels, generate in fakes["generate"])


def test_no_icon_is_generated_even_with_an_image_provider(fakes):
    offer = _build()
    assert all(generate is False for _labels, generate in fakes["generate"])
    icons = [slot for slot in offer.slots if slot.kind == "icon"]
    # only the bundled library's vector icon; a missing concept gets no icon
    assert [slot.concept for slot in icons] == ["수집"] and icons[0].is_vector
    assert not any(key.startswith("sheets") for key in offer.receipt["icon"])


@pytest.mark.parametrize("cutouts", [True, False])
def test_parallel_scenes_get_distinct_provider_slots_and_stable_replay(fakes, monkeypatch, tmp_path, cutouts):
    from contextlib import asynccontextmanager
    from services import image_generation_service

    slots, active, reached = [], set(), []

    @asynccontextmanager
    async def admission(*, semantic_slot, **_kwargs):
        slots.append(semantic_slot)
        if semantic_slot in active:
            raise RuntimeError("public_provider_inflight_replay_unsupported")
        active.add(semantic_slot)
        try:
            reached.append(semantic_slot)
            await asyncio.sleep(0.01)
            yield
        finally:
            active.remove(semantic_slot)

    async def plan(*_args, **_kwargs):
        return AuthoredInfographicPlan(slides=(AuthoredSlideInfographic(index=0), *tuple(AuthoredSlideInfographic(
            index=index, archetype="scene", weight="hero", takeaway="same object",
            nodes=_nodes("part1", "part2", "part3")) for index in (1, 2))))

    async def image(_prompt):
        async with _metered_image_provider_call(provider="fake", model="test", semantic_slot="image.primary.codex"):
            return SimpleNamespace(path=str(_png(tmp_path / f"scene-{len(slots)}.png")))

    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", plan)
    monkeypatch.setattr(image_generation_service, "provider_call_admission", admission)
    if not cutouts:
        monkeypatch.setattr(infographic_assets, "build_infographic_asset_context", lambda **_: None)
    kwargs = dict(image_service=SimpleNamespace(generate_image=image),
                  style=SimpleNamespace(id="s", illustration_prompt="flat", reference_mode="style_reference"))
    offer = _build(**kwargs)
    assert len(offer.slots) == len(reached) == len(set(slots)) == 2
    assert offer.receipt["providerCalls"] == 2
    original_slots = list(slots)
    replay = _build(**kwargs)
    assert len(replay.slots) == 2 and slots[2:] == original_slots


def test_timeout_drops_the_slot_and_is_recorded(fakes, monkeypatch):
    async def slow(**_kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(icon_pack, "generate_scene_cutout", slow)
    offer = _build(timeout_seconds=0.05)
    assert not [slot for slot in offer.slots if slot.kind == "illustration"]
    assert offer.receipt["timeouts"] == 1
    assert {"page": 1, "reason": "cutout_unavailable"} in offer.receipt["failures"]


def test_instructions_refusing_pictures_skip_the_plan(fakes, monkeypatch):
    async def forbidden(*_a, **_k):
        raise AssertionError("plan must not be bought")

    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", forbidden)
    offer = _build(instructions="이미지 없이 텍스트만 사용해 주세요")
    assert offer.slots == () and offer.receipt["status"] == "prohibited_by_instructions"


def test_offer_round_trips_and_detects_changed_bytes(fakes, tmp_path):
    offer = _build()
    persisted = offer.to_json(root=tmp_path.parent)
    assert not any(Path(slot["path"]).is_absolute() for slot in persisted["slots"])
    again = offer_mod.AssetOffer.from_json(persisted, root=tmp_path.parent)
    assert again == offer
    escaped = {**persisted, "slots": [{**persisted["slots"][0], "path": "../../x.png"}]}
    with pytest.raises(ValueError, match="asset_offer_path_outside_root"):
        offer_mod.AssetOffer.from_json(escaped, root=tmp_path)
    Path(again.slots[0].path).write_bytes(b"changed")
    with pytest.raises(ValueError, match="asset_offer_digest_mismatch"):
        again.slots[0].verify()


class _Session:
    def __init__(self):
        self.rows = {}

    async def get(self, _model, key):
        return self.rows.get(key)

    def add(self, row):
        self.rows[row.id] = row

    async def flush(self):
        return None


def test_projection_registers_rasters_and_flattens_vectors(fakes, monkeypatch, tmp_path):
    monkeypatch.setattr("utils.asset_directory_utils.get_images_directory", lambda: str(tmp_path / "images"))
    offer = _build()
    session = _Session()
    projected = asyncio.run(asset_slots.project_offer(session, offer, presentation_id=uuid.uuid4(), count=3))
    assert projected.raster[0] == {} and projected.vector[0] == {}
    assert len(projected.raster[1]) == 1
    (candidate,) = projected.raster[1].values()
    assert candidate.source_locator["assetOffer"] == "illustration"
    assert hashlib.sha256(candidate.path.read_bytes()).hexdigest() == candidate.digest
    assert str(candidate.path).startswith(str(tmp_path / "images"))
    assert uuid.UUID(candidate.asset_id) in session.rows
    (vector_id, entry), = projected.vector[2].items()
    assert vector_id.startswith("source-slot-") and entry["kind"] == "icon"
    assert entry["data_url"].startswith("data:image/png;base64,")
    modes = sorted(meta["mode"] for meta in projected.slots[2].values())
    assert modes == ["flattened"]
    assert projected.receipt["iconsFlattened"] == 1


def test_over_budget_icon_is_rasterized_and_recorded(fakes, monkeypatch, tmp_path):
    from services.generation_core.drivers.svg import embed_assets

    monkeypatch.setattr("utils.asset_directory_utils.get_images_directory", lambda: str(tmp_path / "images"))
    real = embed_assets.flatten_icon_svg

    def strict(text, *, color, point_budget=embed_assets.ICON_POINT_BUDGET, **kwargs):
        if point_budget is not None:
            raise embed_assets.IconFlattenBudgetExceeded("over")
        return real(text, color=color, point_budget=None, **kwargs)

    monkeypatch.setattr(embed_assets, "flatten_icon_svg", strict)
    projected = asyncio.run(asset_slots.project_offer(_Session(), _build(), presentation_id=uuid.uuid4(),
                                                      count=3))
    assert projected.vector[2] == {}
    assert sorted(meta["mode"] for meta in projected.slots[2].values()) == ["rasterized"]
    assert projected.receipt["iconsRasterized"] == 1


def test_resolve_offer_replays_the_persisted_offer_without_building(fakes, monkeypatch, tmp_path):
    stored = {}
    offer = _build()

    async def checkpoint(_job, _index, *, payload=None):
        if payload is not None:
            stored.update(payload)
        return dict(stored)

    job = SimpleNamespace(id=uuid.uuid4(), attempt_number=1)
    monkeypatch.setattr(asset_slots, "get_app_data_directory_env", lambda: str(tmp_path.parent))
    stored[asset_slots.SEED_KEY] = offer.to_json(root=tmp_path.parent)

    async def never(*_a, **_k):
        raise AssertionError("a resumed attempt must not rebuild its offer")

    monkeypatch.setattr(offer_mod, "build_asset_offer", never)
    profile = SimpleNamespace(illustration_budget=4, illustration_timeout_seconds=5)
    request = SimpleNamespace(authored_style=None, instructions=None)
    replayed = asyncio.run(asset_slots.resolve_offer(request, _outline(), [], job, profile=profile,
                                                     checkpoint=checkpoint, durable=True))
    assert replayed == offer
    stored.clear()
    stored["source_manifest_ref"] = {"ref": "x"}
    sealed = asyncio.run(asset_slots.resolve_offer(request, _outline(), [], job, profile=profile,
                                                   checkpoint=checkpoint, durable=True))
    assert sealed.slots == () and sealed.receipt["status"] == "sealed_without_offer"
    assert asyncio.run(asset_slots.resolve_offer(request, _outline(), [], job, profile=profile,
                                                 checkpoint=checkpoint, durable=False)).receipt == {
        "status": "non_durable_job"}


def test_an_unusable_picture_drops_its_slot_instead_of_failing_the_deck(fakes, monkeypatch, tmp_path):
    async def webp(**_kwargs):
        path = tmp_path / "cutout.webp"
        Image.new("RGBA", (32, 32), (1, 2, 3, 255)).save(path, format="WEBP")
        return SimpleNamespace(path=str(path))

    monkeypatch.setattr(icon_pack, "generate_scene_cutout", webp)
    offer = _build()
    assert not [slot for slot in offer.slots if slot.kind == "illustration"]
    assert {"page": 1, "reason": "ValueError"} in offer.receipt["failures"]
    assert offer.receipt["icons"] == 1


def _literal_request():
    slides = ["# 원문 표\n| 항목 | 수집 |\n| --- | --- |\n| 실적 | 10 |",
              "# 원문 차트\n차트: 수집 10, 분석 20"]
    request = SimpleNamespace(strategy="svg", generation_mode="auto", authored_style="default",
        slides_markdown=slides, instructions="표와 차트를 편집 가능한 네이티브 개체로 보존한다.",
        primary_color=None)
    outline = SimpleNamespace(slides=[SimpleNamespace(content=content) for content in slides])
    snapshot = {"composition_mode": "free-svg", "style_id": "default"}
    return request, outline, snapshot


def test_native_literal_offer_never_dispatches_planner_or_image_provider_and_replays(fakes, monkeypatch, tmp_path):
    request, outline, snapshot = _literal_request()
    stored = {}
    async def checkpoint(_job, _index, *, payload=None):
        if payload is not None:
            stored.update(payload)
        return dict(stored)
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("native literal fastpath must not dispatch a provider")
    monkeypatch.setattr(asset_slots, "_build", forbidden)
    monkeypatch.setattr(asset_slots, "get_app_data_directory_env", lambda: str(tmp_path))
    job = SimpleNamespace(id=uuid.uuid4(), attempt_number=1)
    args = dict(profile=SimpleNamespace(illustration_budget=4, illustration_timeout_seconds=30),
        checkpoint=checkpoint, durable=True, style_snapshot=snapshot, has_source_assets=False)
    result = asyncio.run(asset_slots.resolve_offer(request, outline, ["content", "content"], job, **args))
    assert result.receipt["providerCalls"] == 0
    assert result.receipt["status"] == "native_literal_library_only"
    assert result.slots and all(slot.is_vector and slot.origin == "library" for slot in result.slots)
    assert all(generate is False for _labels, generate in fakes["generate"])
    assert len(result.slots) <= 8 and all(len(labels) <= 8 for labels, _generate in fakes["generate"])
    assert asset_slots.SEED_KEY in stored
    async def no_reselect(*_args, **_kwargs):
        raise AssertionError("checkpoint priority must precede fastpath selection")
    monkeypatch.setattr(asset_slots, "_native_literal_offer", no_reselect)
    replay = asyncio.run(asset_slots.resolve_offer(request, outline, [], job, **args))
    assert replay == result
    stored.clear()
    stored["source_manifest_ref"] = {"already": "sealed"}
    sealed = asyncio.run(asset_slots.resolve_offer(request, outline, [], job, **args))
    assert sealed.receipt["status"] == "sealed_without_offer"


@pytest.mark.parametrize("change", ["prose", "partial_chart", "imagery", "relation", "master", "reference",
    "style", "source", "source_unknown", "outline_changed", "implicit_svg", "registered", "not_free", "frozen_ref"])
def test_native_literal_offer_requires_complete_data_and_explicit_source_absence(change):
    request, outline, snapshot = _literal_request()
    has_source_assets = False
    if change in {"prose", "partial_chart"}:
        request.slides_markdown[0] += "\n독립된 사실은 별도로 보존해야 한다." if change == "prose" else "\n차트: A 10, B 20. 추가 내용"
        outline.slides[0].content = request.slides_markdown[0]
    elif change == "imagery":
        request.instructions += " 사진을 추가한다."
    elif change == "relation":
        request.instructions += " 관계도를 추가한다."
    elif change == "master":
        request.master_pin = {"id": "master"}
    elif change == "reference":
        request.reference_snapshot_ids = [uuid.uuid4()]
    elif change == "style":
        request.authored_style = "iso-blueprint"
    elif change == "source":
        has_source_assets = True
    elif change == "source_unknown":
        has_source_assets = None
    elif change == "outline_changed":
        outline.slides[0].content += "\n새 항목"
    elif change == "implicit_svg":
        request.strategy = None
    elif change == "registered":
        request.generation_mode = "registered-template"
    elif change == "not_free":
        snapshot["composition_mode"] = "template-layout"
    elif change == "frozen_ref":
        snapshot["design_pack_template"] = {"version": "v1"}
    assert asset_slots._native_literal_pages(request, outline, style_snapshot=snapshot,
                                             has_source_assets=has_source_assets) is None


# --- no image provider: the library-icon-only offer ---------------------------------------------

def _icons_only_plan():
    # page 1 is a scene (demoted without a picture source), page 2 a process, pages 0 and 3 carry no figure
    return AuthoredInfographicPlan(slides=(
        AuthoredSlideInfographic(index=0),
        AuthoredSlideInfographic(index=1, archetype="scene", weight="hero", takeaway="현장 점검",
                                 nodes=_nodes("신경망", "센서", "현장")),
        AuthoredSlideInfographic(index=2, archetype="process", weight="hero",
                                 nodes=_nodes("신경망", "로봇비서", "생성형")),
        AuthoredSlideInfographic(index=3),
    ))


_LIBRARY_PLACEMENT = [(1, "신경망"), (1, "센서"), (2, "신경망"), (2, "로봇비서")]


def _four_page_outline():
    return SimpleNamespace(slides=[SimpleNamespace(content=f"# {i}") for i in range(4)])


@pytest.fixture
def library_only(monkeypatch, tmp_path):
    """Real icon resolver and library; every image-provider entry point fails the test."""
    from services.generation_core.assets.icon_pack import service

    seen = {"plan": 0, "context": 0}

    async def plan(*_args, **_kwargs):
        seen["plan"] += 1
        return _icons_only_plan()

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("an image provider was reached")

    def no_context(**_kwargs):
        seen["context"] += 1
        raise AssertionError("no asset context may be built without an image provider")

    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", plan)
    monkeypatch.setattr(infographic_assets, "build_infographic_asset_context", no_context)
    monkeypatch.setattr(icon_pack, "generate_scene_cutout", forbidden)
    monkeypatch.setattr(service, "_generate_sheet", forbidden)
    return seen


def _icons_only(**overrides):
    overrides.setdefault("outline", _four_page_outline())
    overrides.setdefault("roles", ["cover", "content", "content", "content"])
    return _build(icons_only=True, **overrides)


def test_icons_only_offer_places_library_vectors_without_any_image_call(library_only, tmp_path):
    offer = _icons_only(budget=4, image_service=None)
    receipt = offer.receipt
    assert receipt["status"] == "library_icons_only" and receipt["iconsOnly"] is True
    assert receipt["reason"] == "image_generation_unavailable" and receipt["planLlmCalls"] == 1
    assert receipt["providerCalls"] == 0 and receipt["budget"] == 0 and receipt["budgetAllocatedCalls"] == 0
    assert receipt["illustrations"] == 0 and receipt["scenesPlanned"] == 0
    assert receipt["transparentProvider"] is False
    assert not any(key.startswith("sheets") for key in receipt["icon"])
    assert receipt["icon"]["providerCalls"] == 0 and receipt["icon"]["origins"] == ["library"]
    # the demoted scene page and the process page get icons; "현장" and "생성형" have no library icon
    placed = [(slot.page_index, slot.concept) for slot in offer.slots]
    assert placed == _LIBRARY_PLACEMENT
    assert receipt["icons"] == 4 and receipt["icon"]["requested"] == 5 and receipt["icon"]["resolved"] == 3
    assert all(slot.kind == "icon" and slot.is_vector and slot.origin == "library" for slot in offer.slots)
    assert {slot.page_index for slot in offer.slots}.isdisjoint({0, 3})  # the plan said no figure there
    for slot in offer.slots:
        assert slot.verify()
    assert library_only["plan"] == 1 and library_only["context"] == 0
    # the persisted form round-trips like any offer
    again = offer_mod.AssetOffer.from_json(offer.to_json(root=tmp_path), root=tmp_path)
    assert again == offer


def test_icons_only_offer_ignores_cached_raster_icons(library_only, monkeypatch, tmp_path):
    from services.generation_core.assets.icon_pack import service

    cached = _png(tmp_path / "cached.png", (96, 96))
    real = service.resolve_icons

    async def resolve(labels, **kwargs):
        result = await real(labels, **kwargs)
        result.icons[concept_key("센서")] = ResolvedIcon("센서", str(cached), 96, 96, "cached")
        return result

    monkeypatch.setattr(icon_pack, "resolve_icons", resolve)
    offer = _icons_only()
    assert "센서" not in {slot.concept for slot in offer.slots}
    assert all(slot.is_vector for slot in offer.slots)


def test_icons_only_offer_keeps_instruction_refusals(library_only):
    offer = _icons_only(instructions="이미지와 아이콘 없이 텍스트로만 작성")
    assert offer.slots == () and offer.receipt["status"] == "prohibited_by_instructions"
    assert library_only["plan"] == 0


def test_icons_only_offer_reports_plan_failure_without_icons(library_only, monkeypatch):
    async def none(*_args, **_kwargs):
        return None

    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", none)
    offer = _icons_only()
    assert offer.slots == () and offer.receipt["status"] == "plan_unavailable" and offer.receipt["iconsOnly"] is True


def test_image_enabled_offer_is_unchanged_by_the_icons_only_mode(fakes):
    offer = _build()
    assert offer.receipt["status"] == "resolved" and "iconsOnly" not in offer.receipt
    assert "planLlmCalls" not in offer.receipt and offer.receipt["providerCalls"] == 1
    assert offer.receipt["budget"] == 4 and offer.receipt["illustrations"] == 1


class _ImageService:
    def __init__(self, *, disabled, func=None):
        self.is_image_generation_disabled, self.image_gen_func = disabled, func


@pytest.mark.parametrize("service", [_ImageService(disabled=True), _ImageService(disabled=False, func=None)])
def test_resolve_offer_without_image_provider_builds_icons_only_persists_and_replays(
        service, library_only, monkeypatch, tmp_path):
    import services.image_generation_service as image_module
    from utils import asset_directory_utils

    stored, built = {}, []
    outline = _four_page_outline()

    async def checkpoint(_job, _index, *, payload=None):
        if payload is not None:
            stored.update(payload)
        return dict(stored)

    real_build = offer_mod.build_asset_offer

    async def spy(**kwargs):
        built.append(kwargs)
        return await real_build(**kwargs)

    monkeypatch.setattr(image_module, "ImageGenerationService", lambda *_a, **_k: service)
    monkeypatch.setattr(asset_directory_utils, "get_images_directory", lambda: str(tmp_path / "images"))
    monkeypatch.setattr(asset_slots, "get_app_data_directory_env", lambda: str(tmp_path))
    monkeypatch.setattr(offer_mod, "build_asset_offer", spy)
    monkeypatch.setattr("services.generation_core.style.build.resolve_brand",
                        lambda *_a, **_k: SimpleNamespace(primary="#123456"))
    job = SimpleNamespace(id=uuid.uuid4(), attempt_number=1)
    profile = SimpleNamespace(illustration_budget=4, illustration_timeout_seconds=5)
    request = SimpleNamespace(authored_style=None, instructions=None, strategy="svg", language="Korean")
    roles = ["cover", "content", "content", "content"]
    args = dict(profile=profile, checkpoint=checkpoint, durable=True)
    offer = asyncio.run(asset_slots.resolve_offer(request, outline, roles, job, **args))
    assert len(built) == 1 and built[0]["icons_only"] is True and built[0]["image_service"] is None
    assert offer.receipt["status"] == "library_icons_only" and offer.receipt["providerCalls"] == 0
    assert [(slot.page_index, slot.concept) for slot in offer.slots] == _LIBRARY_PLACEMENT
    assert asset_slots.SEED_KEY in stored

    async def never(**_kwargs):
        raise AssertionError("a resumed attempt must replay the persisted offer")

    monkeypatch.setattr(offer_mod, "build_asset_offer", never)
    replay = asyncio.run(asset_slots.resolve_offer(request, outline, roles, job, **args))
    assert replay == offer
    # the sealed-manifest contract holds even if image generation is enabled by the time of the resume
    monkeypatch.setattr(image_module, "ImageGenerationService",
                        lambda *_a, **_k: _ImageService(disabled=False, func=object()))
    assert asyncio.run(asset_slots.resolve_offer(request, outline, roles, job, **args)) == offer
    # and the replayed library vectors project and flatten like any icon slot
    projected = asyncio.run(asset_slots.project_offer(_Session(), replay, presentation_id=uuid.uuid4(), count=4))
    assert [len(page) for page in projected.vector] == [0, 2, 2, 0]
    assert projected.receipt["iconsFlattened"] == 4 and not any(projected.raster)


def test_lucide_picks_serve_their_labels_and_only_the_rest_reach_the_icon_pack(fakes, monkeypatch):
    async def picks(labels, names):
        assert "shield-check" in names and labels == ["수집", "분석", "대응"]
        return {"분석": ("chart-line", None), "대응": ("siren", "shield-check")}

    monkeypatch.setattr(icon_selection, "select_deck_icons", picks)
    offer = _build()
    icons = {slot.concept: slot for slot in offer.slots if slot.kind == "icon"}
    assert icons["분석"].origin == icons["대응"].origin == "lucide"
    assert icons["분석"].media_type == "image/svg+xml" and icons["수집"].origin == "library"
    assert fakes["generate"][0] == (["수집"], False)
    assert '#123456' in Path(icons["대응"].path).read_text(encoding="utf-8")
    selection = offer.receipt["iconSelection"]
    assert (selection["status"], selection["requested"], selection["selected"], selection["badges"]) == \
        ("selected", 3, 2, 1)
    assert offer.receipt["icon"]["family"] == 2 and "lucide" in offer.receipt["icon"]["origins"]
    assert (selection["family"], selection["version"]) == ("lucide", "1.67.0")


def test_a_failed_selection_leaves_every_label_to_the_icon_pack(fakes, monkeypatch):
    async def failed(labels, names):
        return None

    monkeypatch.setattr(icon_selection, "select_deck_icons", failed)
    offer = _build()
    assert offer.receipt["iconSelection"]["status"] == "selection_failed"
    assert fakes["generate"][0] == (["수집", "분석", "대응"], False)
    assert all(slot.origin != "lucide" for slot in offer.slots)


def test_icons_only_mode_still_uses_lucide_picks_without_any_provider(fakes, monkeypatch):
    async def picks(labels, names):
        return {label: ("circle-check", None) for label in labels}

    monkeypatch.setattr(icon_selection, "select_deck_icons", picks)
    offer = _build(icons_only=True)
    icons = [slot for slot in offer.slots if slot.kind == "icon"]
    assert icons and all(slot.origin == "lucide" for slot in icons)
    assert offer.receipt["providerCalls"] == 0 and not fakes["generate"]


def test_the_style_icon_family_supplies_the_vocabulary_and_the_origin(fakes, monkeypatch):
    async def picks(labels, names):
        assert "building-bank" in names and "brand-github" not in names
        return {"분석": ("report-analytics", None)}

    monkeypatch.setattr(icon_selection, "select_deck_icons", picks)
    style = SimpleNamespace(id="s", illustration_prompt="", reference_mode="style_reference",
                            icon_family="tabler-outline")
    offer = _build(style=style)
    icon = next(slot for slot in offer.slots if slot.concept == "분석")
    assert icon.origin == "tabler-outline"
    assert (offer.receipt["iconSelection"]["family"], offer.receipt["iconSelection"]["version"]) == \
        ("tabler-outline", "3.50.0")
