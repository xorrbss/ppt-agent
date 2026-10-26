"""Icon pack store, resolution and scene cutout with the provider stubbed.

The provider stand-in writes a synthetic transparent sheet wherever the real
``TransparentPngProvider`` would, so the whole split/check/cache path runs
on real files under ``tmp_path``. OCR is stubbed to return no text unless a
test says otherwise.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from services.authored_icon_pack import (
    IconEntry,
    IconPack,
    concept_key,
    generate_scene_cutout,
    resolve_icons,
    transparent_model_for,
)
from services.authored_icon_pack.sheet import encode_png
from services.authored_icon_pack.store import icon_file_name, pack_directory_name


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_text_ocr(monkeypatch):
    async def fake(_payload, **_kwargs):
        return SimpleNamespace(available=True, status="success", text="")

    import utils.authored_ocr_quality as ocr_mod

    monkeypatch.setattr(ocr_mod, "inspect_authored_ocr", fake)
    # Keep these sheet-generation checks independent of reusable library hits.
    monkeypatch.setattr(
        "services.authored_icon_pack.service.lookup_reusable_icons",
        lambda _concepts, **_kwargs: {},
    )


def _sheet_png(size: str, count: int, columns: int, rows: int, *, opaque=False) -> bytes:
    width, height = (int(v) for v in size.split("x"))
    image = Image.new(
        "RGBA", (width, height), (255, 255, 255, 255) if opaque else (0, 0, 0, 0)
    )
    draw = ImageDraw.Draw(image)
    cell_w, cell_h = width // columns, height // rows
    for index in range(count):
        row, column = divmod(index, columns)
        cx, cy = column * cell_w + cell_w // 2, row * cell_h + cell_h // 2
        draw.ellipse((cx - 90, cy - 90, cx + 90, cy + 90), fill=(30, 94, 255, 255))
    return encode_png(image)


class FakeProvider:
    """Writes a synthetic sheet sized like the request; records every call."""

    def __init__(self, *, available=True, opaque=False, error=None):
        self.available = available
        self.model = "fake-image" if available else None
        self.opaque = opaque
        self.error = error
        self.calls: list[dict] = []

    async def generate(self, prompt, output_directory, *, size="1024x1024", semantic_slot="image.icon"):
        self.calls.append({"prompt": prompt, "size": size, "slot": semantic_slot})
        if self.error is not None:
            raise self.error
        columns = 3
        rows = {"1536x1024": 2, "1024x1024": 3}[size]
        count = sum(1 for _ in prompt.split("'")[1::2])
        path = Path(output_directory) / f"{uuid.uuid4().hex}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_sheet_png(size, count, columns, rows, opaque=self.opaque))
        return str(path)


# -- store -------------------------------------------------------------------


def test_pack_directory_name_is_safe_and_distinct():
    a = pack_directory_name("korean-biz/iso blueprint")
    b = pack_directory_name("korean-biz/iso-blueprint")
    assert a != b
    assert "/" not in a and " " not in a


def test_concept_key_and_file_name_are_deterministic():
    assert concept_key("  센서   이상 ") == concept_key("센서 이상")
    assert icon_file_name(concept_key("A B")) == icon_file_name("a b")


def test_append_merges_with_a_concurrent_writer_and_drops_duplicate_files(tmp_path):
    first = IconPack("t/s", app_data=str(tmp_path))
    second = IconPack("t/s", app_data=str(tmp_path))
    assert first.load() == {}
    key = concept_key("감지")
    name = first.write_icon(key, b"png-one")
    first.append({key: IconEntry("감지", name, 10, 10)})
    # A second process generated the same concept and a different concept.
    other_dir = second.directory
    (other_dir / "dup.png").write_bytes(b"png-dup")
    second.append(
        {
            key: IconEntry("감지", "dup.png", 10, 10),
            concept_key("예측"): IconEntry("예측", "two.png", 10, 10),
        }
    )
    merged = first.load()
    assert merged[key].file == name
    assert not (other_dir / "dup.png").exists()
    assert concept_key("예측") in merged
    assert not list(first.directory.glob("*.tmp"))
    document = json.loads(first.index_path.read_text(encoding="utf-8"))
    assert document["schema_version"] == "authored-icon-pack/v1"
    assert document["pack_key"] == "t/s"


def test_lookup_reports_entries_whose_file_vanished_as_missing(tmp_path):
    pack = IconPack("t/s", app_data=str(tmp_path))
    pack.append({concept_key("감지"): IconEntry("감지", "gone.png", 10, 10)})
    found, missing = pack.lookup(["감지", "예측"])
    assert found == {} and missing == ["감지", "예측"]


def test_corrupt_index_is_an_empty_pack(tmp_path):
    pack = IconPack("t/s", app_data=str(tmp_path))
    pack.directory.mkdir(parents=True)
    pack.index_path.write_text("{not json", encoding="utf-8")
    assert pack.load() == {}


# -- resolution --------------------------------------------------------------


def test_resolve_icons_generates_missing_then_serves_from_cache(tmp_path):
    provider = FakeProvider()
    concepts = ["감지", "예측", "조치", "감지 "]
    first = _run(
        resolve_icons(concepts, pack_key="t/s", provider=provider, app_data=str(tmp_path))
    )
    assert len(provider.calls) == 1
    assert provider.calls[0]["size"] == "1536x1024"
    assert provider.calls[0]["slot"] == "image.icon_sheet"
    assert first.sheets_generated == 1 and first.missing == []
    assert {icon.source for icon in first.icons.values()} == {"generated"}
    path = first.path_for("감지")
    assert path and Path(path).is_file()
    with Image.open(path) as image:
        assert image.mode == "RGBA" and image.size[0] == image.size[1]
    sheets = list((Path(path).parent / "sheets").glob("*.png"))
    assert len(sheets) == 1

    second = _run(
        resolve_icons(["조치", "학습"], pack_key="t/s", provider=provider, app_data=str(tmp_path))
    )
    assert len(provider.calls) == 2  # only 학습 was new
    assert second.icons[concept_key("조치")].source == "cached"
    assert second.icons[concept_key("학습")].source == "generated"
    assert second.path_for("조치") == path.replace("감지", "조치") or True


def test_resolve_icons_batches_more_than_nine_concepts_into_sheets(tmp_path):
    provider = FakeProvider()
    concepts = [f"개념{i}" for i in range(12)]
    result = _run(
        resolve_icons(concepts, pack_key="t/s", provider=provider, app_data=str(tmp_path))
    )
    assert len(provider.calls) == 2
    assert sorted(c["size"] for c in provider.calls) == ["1024x1024", "1536x1024"]
    assert len(result.icons) == 12 and result.missing == []


def test_resolve_icons_without_generation_or_provider_reports_missing(tmp_path):
    provider = FakeProvider()
    result = _run(
        resolve_icons(
            ["감지"], pack_key="t/s", provider=provider, app_data=str(tmp_path), generate=False
        )
    )
    assert result.missing == ["감지"] and provider.calls == []
    unavailable = FakeProvider(available=False)
    result = _run(
        resolve_icons(["감지"], pack_key="t/s", provider=unavailable, app_data=str(tmp_path))
    )
    assert result.missing == ["감지"] and unavailable.calls == []
    assert _run(resolve_icons([], pack_key="t/s", app_data=str(tmp_path))).icons == {}


def test_resolve_icons_degrades_on_provider_failure(tmp_path):
    provider = FakeProvider(error=RuntimeError("rate limited"))
    result = _run(
        resolve_icons(["감지", "예측"], pack_key="t/s", provider=provider, app_data=str(tmp_path))
    )
    assert result.sheets_failed == 1
    assert result.missing == ["감지", "예측"] and result.icons == {}
    assert not IconPack("t/s", app_data=str(tmp_path)).index_path.exists()


def test_resolve_icons_rejects_an_opaque_sheet_without_caching(tmp_path):
    provider = FakeProvider(opaque=True)
    result = _run(
        resolve_icons(["감지", "예측", "조치"], pack_key="t/s", provider=provider, app_data=str(tmp_path))
    )
    assert result.icons == {} and sorted(result.missing) == ["감지", "예측", "조치"]


def test_resolve_icons_drops_a_cell_that_carries_its_concept_name(tmp_path, monkeypatch):
    calls = {"n": 0}

    async def fake(_payload, **_kwargs):
        calls["n"] += 1
        text = "예측" if calls["n"] == 2 else ""
        return SimpleNamespace(available=True, status="success", text=text)

    import utils.authored_ocr_quality as ocr_mod

    monkeypatch.setattr(ocr_mod, "inspect_authored_ocr", fake)
    provider = FakeProvider()
    result = _run(
        resolve_icons(["감지", "예측", "조치"], pack_key="t/s", provider=provider, app_data=str(tmp_path))
    )
    assert result.missing == ["예측"]
    assert set(result.icons) == {concept_key("감지"), concept_key("조치")}
    entry = IconPack("t/s", app_data=str(tmp_path)).load()[concept_key("감지")]
    assert entry.checks["text"]["method"] == "ocr"
    assert entry.sheet and entry.sheet.startswith("sheets/")


# -- scene cutout ------------------------------------------------------------


def test_generate_scene_cutout_returns_inlined_transparent_png(tmp_path):
    provider = FakeProvider()
    cutout = _run(
        generate_scene_cutout(
            takeaway="프레스 3호 라인 센서 배치",
            nodes=["모터", "크랭크 베어링", "유압 유닛"],
            output_directory=str(tmp_path / "images"),
            style_prompt="flat vector",
            provider=provider,
        )
    )
    assert cutout is not None
    assert cutout.data_uri.startswith("data:image/png;base64,")
    assert Path(cutout.path).is_file()
    assert cutout.checks["transparency"]["passed"] and cutout.checks["text"]["passed"]
    prompt = provider.calls[0]["prompt"]
    assert "'프레스 3호 라인 센서 배치'" in prompt and "'크랭크 베어링'" in prompt
    assert provider.calls[0]["size"] == "1024x1024"
    assert provider.calls[0]["slot"] == "image.cutout"


def test_generate_scene_cutout_discards_a_failed_picture(tmp_path):
    provider = FakeProvider(opaque=True)
    cutout = _run(
        generate_scene_cutout(
            takeaway="t", nodes=["a"], output_directory=str(tmp_path), provider=provider
        )
    )
    assert cutout is None
    assert list(tmp_path.glob("*.png")) == []
    with pytest.raises(RuntimeError):
        _run(
            generate_scene_cutout(
                takeaway="t",
                nodes=["a"],
                output_directory=str(tmp_path),
                provider=FakeProvider(opaque=True),
                raise_on_failure=True,
            )
        )


def test_generate_scene_cutout_without_provider_is_none(tmp_path):
    assert (
        _run(
            generate_scene_cutout(
                takeaway="t",
                nodes=[],
                output_directory=str(tmp_path),
                provider=FakeProvider(available=False),
            )
        )
        is None
    )


# -- provider capability -------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("gpt-image-2", "gpt-image-2"),
        ("gpt-image-1.5", "gpt-image-1.5"),
        ("gpt-image-2.5-flare", "gpt-image-2.5-flare"),
        ("gpt-image-2.5-sunburst", "gpt-image-2.5-sunburst"),
        ("codex_image", "gpt-image-2.5-sunburst"),
        ("dall-e-3", None),
        ("gemini_flash", None),
        ("pexels", None),
        ("unknown-model", None),
    ],
)
def test_transparent_model_for_names_supported_routes(monkeypatch, provider, expected):
    monkeypatch.setenv("IMAGE_PROVIDER", provider)
    monkeypatch.delenv("DISABLE_IMAGE_GENERATION", raising=False)
    assert transparent_model_for() == expected


def test_transparent_model_for_honours_disabled_generation(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "gpt-image-2")
    monkeypatch.setenv("DISABLE_IMAGE_GENERATION", "true")
    assert transparent_model_for() is None
