"""Icon-pack pictures reaching a planned figure slide.

The library draws every figure with built-in SVG icons and, for a scene, one
illustration slot. These tests pin how the icon pack replaces them: only
after the contract check, only when the deck may spend a transparent PNG,
never failing a slide, and on the first pass, the illustration stage and the
vision-QA re-author path alike.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from models.authored_infographic_plan import coerce_infographic_plan
from services import authored_illustration_stage as stage_mod
from services import authored_infographic_assets as assets_mod
from services.authored_icon_pack.service import IconResolution, ResolvedIcon
from services.authored_icon_pack.sheet import encode_png
from services.authored_icon_pack.store import concept_key
from services.authored_infographic_assets import (
    InfographicAssetContext,
    build_infographic_asset_context,
    fill_scene_cutout,
    substitute_figure_icons,
)
from utils.authored_infographic import (
    check_infographic_contract,
    inject_library_css,
    library_css,
    render_skeleton,
)
from utils.llm_calls import author_vision_qa as vqa_mod
from utils.llm_calls.author_slide import Brand, validate_authored_slide_html

_CONTENT = (
    "## 프레스 3호 라인 센서 배치\n- 주 모터: 진동 센서\n- 크랭크 베어링: 온도 센서\n"
    "- 유압 유닛: 압력 센서\n- 제어반: 전류 센서"
)
_NODES = [
    {"label": "주 모터", "desc": "진동 센서"},
    {"label": "크랭크 베어링", "desc": "온도 센서"},
    {"label": "유압 유닛", "desc": "압력 센서"},
    {"label": "제어반", "desc": "전류 센서"},
]


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_text_ocr(monkeypatch):
    async def fake(_payload, **_kwargs):
        return SimpleNamespace(available=True, status="success", text="")

    import utils.authored_ocr_quality as ocr_mod

    monkeypatch.setattr(ocr_mod, "inspect_authored_ocr", fake)


def _slide(archetype="icon_list", count=4):
    plan = coerce_infographic_plan(
        {
            "slides": [
                {
                    "index": 2,
                    "archetype": archetype,
                    "weight": "hero",
                    "takeaway": "센서를 단다",
                    "nodes": _NODES[:count],
                    "density": "medium",
                }
            ]
        },
        slide_contents=["표지", "목차", _CONTENT],
        roles=["COVER", "AGENDA", "CONTENT"],
    )
    return plan.at(2)


def _brand():
    return Brand(topic="스마트팩토리", primary="#1E5EFF", fonts="Noto Sans KR")


def _style(**overrides):
    base = dict(
        id="editorial",
        illustration_prompt="flat vector",
        reference_mode="style_reference",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _bare_doc(slide):
    body = render_skeleton(slide)
    return (
        '<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"></head>'
        f'<body><div style="width:1152px;height:520px">{body}</div></body></html>'
    )


def _doc(slide):
    return inject_library_css(_bare_doc(slide), library_css(_brand()))


def _sheet_png(size: str, count: int, *, opaque=False) -> bytes:
    width, height = (int(v) for v in size.split("x"))
    image = Image.new(
        "RGBA", (width, height), (255, 255, 255, 255) if opaque else (0, 0, 0, 0)
    )
    draw = ImageDraw.Draw(image)
    columns, rows = 3, {"1536x1024": 2, "1024x1024": 3}[size]
    cell_w, cell_h = width // columns, height // rows
    for index in range(count):
        row, column = divmod(index, columns)
        cx, cy = column * cell_w + cell_w // 2, row * cell_h + cell_h // 2
        draw.ellipse((cx - 90, cy - 90, cx + 90, cy + 90), fill=(30, 94, 255, 255))
    return encode_png(image)


class FakeProvider:
    """Writes a synthetic transparent sheet or cutout; records every call."""

    def __init__(self, *, available=True, error=None):
        self.available = available
        self.model = "fake-image" if available else None
        self.error = error
        self.calls: list[dict] = []

    async def generate(self, prompt, output_directory, *, size="1024x1024", semantic_slot="image.icon"):
        self.calls.append({"prompt": prompt, "size": size, "slot": semantic_slot})
        if self.error is not None:
            raise self.error
        count = sum(1 for _ in prompt.split("'")[1::2])
        path = Path(output_directory) / f"{uuid.uuid4().hex}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_sheet_png(size, count))
        return str(path)


def _context(tmp_path, provider=None):
    return InfographicAssetContext(
        pack_key="editorial",
        output_directory=str(tmp_path / "images"),
        style_prompt="flat vector",
        provider=provider or FakeProvider(),
        app_data=str(tmp_path / "app_data"),
    )


# --- the deck-level decision ----------------------------------------------------


def test_the_context_is_withheld_when_the_deck_may_not_spend_a_transparent_png(monkeypatch):
    monkeypatch.setattr(assets_mod, "transparent_model_for", lambda _settings=None: None)
    assert build_infographic_asset_context(style=_style(), brand=_brand()) is None
    monkeypatch.setattr(assets_mod, "transparent_model_for", lambda _settings=None: "gpt-image-2")
    assert (
        build_infographic_asset_context(
            style=_style(reference_mode="exact_reconstruction"), brand=_brand()
        )
        is None
    )
    assert (
        build_infographic_asset_context(
            style=_style(), brand=_brand(), instructions="이미지는 넣지 말고"
        )
        is None
    )
    context = build_infographic_asset_context(
        style=_style(), brand=_brand(), image_service=SimpleNamespace(output_directory="img")
    )
    assert context.pack_key == "editorial"
    assert context.output_directory == "img"
    assert context.color == "#1E5EFF"
    assert context.style_prompt == "flat vector"
    assert build_infographic_asset_context(style=_style(id=None), brand=_brand()).pack_key == "default"


# --- icons ----------------------------------------------------------------------


def test_pack_icons_replace_the_built_in_svg_and_keep_the_slot_qids(tmp_path):
    slide = _slide("icon_list", 4)
    assert check_infographic_contract(_bare_doc(slide), slide) == []
    html = _doc(slide)
    provider = FakeProvider()
    replaced = _run(substitute_figure_icons(html, slide=slide, context=_context(tmp_path, provider)))
    assert replaced.count('<img src="data:image/png;base64,') == 4
    assert "<svg" not in replaced.split("ig-icon-list", 1)[1]
    for position in range(4):
        assert f'data-qid="ig.s3.n{position + 1}.ic"><img' in replaced
    assert provider.calls and provider.calls[0]["slot"] == "image.icon_sheet"
    validate_authored_slide_html(replaced, finalized=True)
    # A second slide of the same deck reads the pack rather than generating.
    again = _run(substitute_figure_icons(html, slide=slide, context=_context(tmp_path, provider)))
    assert again == replaced and len(provider.calls) == 1


def test_a_missing_icon_keeps_the_library_svg(tmp_path, monkeypatch):
    slide = _slide("process", 4)
    html = _doc(slide)
    icon = tmp_path / "icon.png"
    icon.write_bytes(_sheet_png("1024x1024", 1))

    async def partial(concepts, **_kwargs):
        resolution = IconResolution()
        resolution.icons[concept_key(concepts[1])] = ResolvedIcon(
            concepts[1], str(icon), 8, 8, "cached"
        )
        resolution.missing = [c for i, c in enumerate(concepts) if i != 1]
        return resolution

    monkeypatch.setattr(assets_mod, "resolve_icons", partial)
    replaced = _run(substitute_figure_icons(html, slide=slide, context=_context(tmp_path)))
    assert replaced.count("<img src=") == 1
    assert 'data-qid="ig.s3.n2.ic"><img' in replaced
    assert replaced.count("<svg") == html.count("<svg") - 1


def test_icon_resolution_failure_and_absent_context_leave_the_slide_alone(tmp_path, monkeypatch):
    slide = _slide("process", 3)
    html = _doc(slide)
    assert _run(substitute_figure_icons(html, slide=slide, context=None)) == html
    assert _run(substitute_figure_icons(html, slide=None, context=_context(tmp_path))) == html

    async def boom(*_args, **_kwargs):
        raise RuntimeError("pack unavailable")

    monkeypatch.setattr(assets_mod, "resolve_icons", boom)
    assert _run(substitute_figure_icons(html, slide=slide, context=_context(tmp_path))) == html


# --- the scene cutout -----------------------------------------------------------


def test_the_scene_cutout_fills_the_art_slot_and_drops_the_prompt(tmp_path):
    slide = _slide("scene", 4)
    html = _doc(slide)
    provider = FakeProvider()
    filled = _run(
        fill_scene_cutout(html, slide=slide, context=_context(tmp_path, provider), slide_index=2)
    )
    assert "data-illust-prompt" not in filled
    assert '<img src="data:image/png;base64,' in filled
    assert 'data-qid="ig.s3.art.img"' in filled
    assert 'style="width:720px;height:405px"' in filled
    assert provider.calls[0]["slot"] == "image.cutout"
    assert "센서를 단다" in provider.calls[0]["prompt"]
    assert "'주 모터'" in provider.calls[0]["prompt"]


def test_a_failed_cutout_leaves_the_slot_for_the_ordinary_path(tmp_path):
    slide = _slide("scene", 4)
    html = _doc(slide)
    failing = FakeProvider(error=RuntimeError("provider down"))
    assert _run(fill_scene_cutout(html, slide=slide, context=_context(tmp_path, failing))) == html
    not_a_scene = _slide("process", 4)
    untouched = _run(
        fill_scene_cutout(_doc(not_a_scene), slide=not_a_scene, context=_context(tmp_path))
    )
    assert untouched == _doc(not_a_scene)


# --- the illustration stage -----------------------------------------------------


class _RefusingImageService:
    """The ordinary illustration path; a call here means the cutout did not land."""

    output_directory = "unused"
    is_image_generation_disabled = False

    def __init__(self):
        self.calls = 0

    async def generate_image(self, prompt):
        self.calls += 1
        raise TimeoutError("ordinary illustration provider unavailable")


def _illustrate(html, slide, context, service):
    return _run(
        stage_mod.illustrate_slide(
            html=html,
            checkpoint=None,
            index=2,
            style=_style(),
            image_service=service,
            slide_content=_CONTENT,
            illustration_allowed=True,
            generation_job=None,
            attempt_token=None,
            infographic=slide,
            infographic_assets=context,
        )
    )


def test_the_stage_takes_the_cutout_first_and_then_the_icons(tmp_path):
    slide = _slide("scene", 4)
    service = _RefusingImageService()
    finalized, _checkpoint, _work = _illustrate(_doc(slide), slide, _context(tmp_path), service)
    assert service.calls == 0
    assert "data-illust-prompt" not in finalized
    assert 'data-qid="ig.s3.art.img"' in finalized
    assert "data:image/png;base64," in finalized


def test_the_stage_falls_back_to_the_ordinary_rule_when_the_cutout_fails(tmp_path):
    slide = _slide("scene", 4)
    service = _RefusingImageService()
    failing = _context(tmp_path, FakeProvider(error=RuntimeError("no alpha today")))
    finalized, _checkpoint, _work = _illustrate(_doc(slide), slide, failing, service)
    assert service.calls == 1
    assert "data-illust-prompt" not in finalized
    assert 'data-qid="ig.s3.art.img"' not in finalized
    assert 'class="ig-art"' in finalized


def test_the_stage_places_icons_on_a_figure_without_a_picture(tmp_path):
    slide = _slide("layers", 4)
    provider = FakeProvider()
    finalized, _checkpoint, _work = _illustrate(
        _doc(slide), slide, _context(tmp_path, provider), _RefusingImageService()
    )
    assert finalized.count('<img src="data:image/png;base64,') == 4
    assert provider.calls[0]["slot"] == "image.icon_sheet"
    untouched, _checkpoint, _work = _illustrate(_doc(slide), slide, None, _RefusingImageService())
    assert "<img" not in untouched


# --- the re-author path ---------------------------------------------------------


def test_a_reauthored_figure_gets_its_pack_icons_again(tmp_path, monkeypatch):
    slide = _slide("process", 4)
    reauthored = _doc(slide)

    async def fake_critique(pngs, contexts=None, **_kwargs):
        return [vqa_mod.SlideCritique(needs_fix=True)]

    async def fake_author(*args, **kwargs):
        assert kwargs["infographic"] is slide
        return reauthored

    async def fake_render(html, **_options):
        return b"revised-png"

    monkeypatch.setattr(vqa_mod, "critique_authored", fake_critique)
    monkeypatch.setattr(vqa_mod, "author_slide_html", fake_author)
    monkeypatch.setattr(vqa_mod, "render_html_to_png", fake_render)
    provider = FakeProvider()
    htmls, pngs, fixed = _run(
        vqa_mod.revise_authored_deck(
            [reauthored],
            [b"original-png"],
            [_CONTENT],
            ["CONTENT"],
            _brand(),
            "SELECTED DESIGN SYSTEM",
            style=_style(),
            slide_indices=[2],
            total_slides=3,
            illustration_slots=[False],
            infographic_briefs=[slide],
            infographic_assets=_context(tmp_path, provider),
        )
    )
    assert fixed == [0] and pngs == [b"revised-png"]
    assert htmls[0].count('<img src="data:image/png;base64,') == 4
    assert provider.calls[0]["slot"] == "image.icon_sheet"
