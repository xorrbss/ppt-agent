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
        return SimpleNamespace(path=str(_png(tmp_path / f"cutout-{calls['cutout']}.png")))

    async def resolve(labels, *, generate, **_kwargs):
        calls["generate"].append((list(labels), generate))
        result = IconResolution()
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

    context = infographic_assets.InfographicAssetContext(pack_key="p", output_directory=str(tmp_path))
    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", plan)
    monkeypatch.setattr(icon_pack, "generate_scene_cutout", cutout)
    monkeypatch.setattr(icon_pack, "resolve_icons", resolve)
    monkeypatch.setattr(infographic_assets, "build_infographic_asset_context", lambda **_: context)
    return calls


def _build(**overrides):
    kwargs = dict(outline=_outline(), brand=SimpleNamespace(primary="#123456"),
                  style=SimpleNamespace(id="s", illustration_prompt="", reference_mode="style_reference"),
                  roles=["cover", "content", "content"], instructions=None, budget=4,
                  timeout_seconds=5)
    kwargs.update(overrides)
    return asyncio.run(offer_mod.build_asset_offer(**kwargs))


def test_offer_plans_one_illustration_per_scene_and_one_icon_per_node(fakes):
    offer = _build()
    kinds = [(slot.page_index, slot.kind, slot.origin) for slot in offer.slots]
    assert (1, "illustration", "cutout") in kinds
    icons = [slot for slot in offer.slots if slot.kind == "icon"]
    assert [slot.concept for slot in icons] == ["수집", "분석", "대응"]
    assert {slot.page_index for slot in icons} == {2}
    assert icons[0].is_vector and not icons[1].is_vector
    # one scene call + one sheet for the two missing concepts
    assert offer.receipt["providerCalls"] == 2
    assert offer.receipt["illustrations"] == 1 and offer.receipt["icons"] == 3


def test_budget_bounds_provider_calls(fakes):
    offer = _build(budget=1)
    assert offer.receipt["providerCalls"] == 1
    # the sheet was not affordable: only the free library icon remains
    assert [slot.concept for slot in offer.slots if slot.kind == "icon"] == ["수집"]
    assert all(generate is False for _labels, generate in fakes["generate"])


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
    assert modes == ["flattened", "raster", "raster"]
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
    assert sorted(meta["mode"] for meta in projected.slots[2].values()) == ["raster", "raster", "rasterized"]
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
    assert offer.receipt["icons"] == 3
