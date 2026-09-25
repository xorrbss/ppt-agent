"""Redesign rendering of raster pages into editable screenshot-blue A4 SVG."""

from __future__ import annotations

import hashlib
import itertools
import re
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

import test_raster_redesign_layout_fixtures as fx
from services.raster_source_assets import _approved_crop_regions, raster_slot_id
from services.source_content import raster_svg_layout
from services.source_content.raster_redesign_layout import render_redesign_page_svg
from services.source_content.raster_redesign_plan import build_items, plan_content, redesign_crop_regions
from services.source_content.raster_redesign_text import visual_width
from services.source_content.raster_svg_layout import MIN_FONT_UNITS, render_raster_page_svg
from services.svg_contract import PAGE_NUMBER_ID, SVG_NS, validate_svg

NS = f"{{{SVG_NS}}}"
DECK = fx.deck()
PAGES = range(1, len(DECK.pages) + 1)


def _svg(number, analysis=DECK, **kwargs):
    return render_redesign_page_svg(analysis, number, **kwargs)


def _texts(svg):
    root = ET.fromstring(svg)
    return [(node.get("id") or "", "".join(node.itertext())) for node in root.iter(f"{NS}text")]


def _dense(value):
    return re.sub(r"\s+", "", value or "")


def _tag(region_id):
    return hashlib.sha256(region_id.encode("utf-8")).hexdigest()[:10]


def _source_text(region):
    if region.role == "table" and region.table_cells:
        return "".join("".join(row) for row in region.table_cells)
    return region.text or ""


def _editable_regions(page):
    return [r for r in page.regions if r.role != "decoration" and r not in redesign_crop_regions(page)]


@pytest.mark.parametrize("number", PAGES)
def test_page_is_valid_a4_landscape_svg_with_exact_crop_slots(number):
    page = DECK.pages[number - 1]
    svg = _svg(number)
    slots = {raster_slot_id(number, r.region_id) for r in redesign_crop_regions(page)}
    validate_svg(svg, width=1188, height=840,
                 source_manifest={slot: {"required": True, "allowOmit": False} for slot in slots})
    root = ET.fromstring(svg)
    assert (root.get("width"), root.get("height"), root.get("viewBox")) == ("1188", "840", "0 0 1188 840")
    hrefs = [node.get("href") for node in root.iter(f"{NS}image")]
    assert sorted(hrefs) == sorted(slots)  # each crop exactly once, nothing else
    assert raster_slot_id(number, page.reference_region.region_id) not in svg
    for region in page.regions:
        if region.role == "decoration" or (region.role == "table" and region.table_cells):
            assert raster_slot_id(number, region.region_id) not in svg


@pytest.mark.parametrize("number", PAGES)
def test_every_text_and_font_size_respects_the_floor(number):
    root = ET.fromstring(_svg(number))
    sizes = [float(node.get("font-size")) for node in root.iter()
             if node.tag in {f"{NS}text", f"{NS}tspan"} and node.get("font-size")]
    assert sizes and min(sizes) >= MIN_FONT_UNITS


@pytest.mark.parametrize("number", PAGES)
@pytest.mark.parametrize("instruction", [None, "간결하고 촘촘하게 compact"])
def test_every_source_character_is_emitted_exactly_once_in_order(number, instruction):
    page = DECK.pages[number - 1]
    texts = _texts(_svg(number, instruction=instruction))
    title, _, _ = build_items(page)
    expected_total = ""
    for region in _editable_regions(page):
        prefix = "rd-title" if region is title else f"rd-{_tag(region.region_id)}"
        emitted = "".join(value for key, value in texts if key == prefix or key.startswith(prefix + "-"))
        assert _dense(emitted) == _dense(_source_text(region)), region.region_id
        expected_total += _dense(_source_text(region))
    everything = "".join(value for key, value in texts if key != PAGE_NUMBER_ID)
    assert len(_dense(everything)) == len(expected_total)  # no duplicates anywhere else


@pytest.mark.parametrize("number", PAGES)
def test_source_tokens_are_never_split_when_they_fit(number):
    page = DECK.pages[number - 1]
    root = ET.fromstring(_svg(number))
    lines = [node.text or "" for node in root.iter(f"{NS}tspan")]
    tokens = set(itertools.chain.from_iterable(line.split() for line in lines))
    for region in _editable_regions(page):
        for token in _source_text(region).split() if region.role != "table" else ():
            assert token in tokens, token


@pytest.mark.parametrize("number", PAGES)
def test_plan_has_no_overlap_and_text_fits_its_box(number):
    page = DECK.pages[number - 1]
    _, _, items = build_items(page)
    box = (48.0, 130.0, 1092.0, 640.0)
    plan = plan_content(items, box, source_size=(fx.SOURCE_W, fx.SOURCE_H))
    content = [p for p in plan.placements if p.kind in {"text", "table", "image"}]
    for placed in content:
        assert placed.x >= box[0] - 0.5 and placed.x + placed.w <= box[0] + box[2] + 0.5
        assert placed.y >= box[1] - 0.5 and placed.y + placed.h <= box[1] + box[3] + 0.5
        for line in placed.lines:
            assert line.indent + visual_width(line.text) * line.size <= placed.w + 0.5
    for a, b in itertools.combinations(content, 2):
        overlap_x = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
        overlap_y = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
        assert overlap_x <= 0.5 or overlap_y <= 0.5, (a.kind, b.kind)


@pytest.mark.parametrize("number", PAGES)
def test_page_number_follows_source_order(number):
    texts = dict(_texts(_svg(number)))
    assert texts[PAGE_NUMBER_ID] == str(number).zfill(2)


def test_unfittable_content_fails_loudly_instead_of_dropping():
    sentence = "번째 항목은 반드시 원문 그대로 유지되어야 하는 긴 설명 문장입니다."
    long_text = "\n".join(f"{index}{sentence}" for index in range(160))
    page = fx._page(1, [
        fx.region(1, "title", "title", (40, 40, 800, 70), "넘치는 페이지"),
        fx.region(1, "body", "body", (40, 150, 1500, 700), long_text),
    ])
    with pytest.raises(ValueError, match="raster_redesign_content_overflow"):
        _svg(1, fx.analysis(page))


def test_structured_table_is_an_editable_grid():
    svg = _svg(4)
    region = next(r for r in DECK.pages[3].regions if r.region_id == "p4-table")
    texts = dict(_texts(svg))
    prefix = f"rd-{_tag(region.region_id)}"
    for row_index, row in enumerate(region.table_cells):
        for column_index, value in enumerate(row):
            assert texts[f"{prefix}-r{row_index}c{column_index}"] == value


def _dispatch_analysis(page):
    return fx.analysis(page)


def test_render_raster_page_svg_dispatches_redesign_and_ignores_reconstruct_blockers(monkeypatch):
    from services.source_content import raster_reconstruction_store

    monkeypatch.setattr(raster_svg_layout, "require_analysis_digest", lambda analysis: None)
    monkeypatch.setattr(raster_reconstruction_store, "raster_review_blockers",
                        lambda analysis: ("page_1:ocr_review_required",))
    analysis = _dispatch_analysis(fx.page_hero(1))
    svg = render_raster_page_svg(analysis, 1, instruction="compact")
    assert svg == render_redesign_page_svg(analysis, 1, instruction="compact")
    monkeypatch.setattr(raster_reconstruction_store, "raster_review_blockers",
                        lambda analysis: ("local_ocr_unavailable",))
    with pytest.raises(ValueError, match="raster_analysis_review_unresolved"):
        render_raster_page_svg(analysis, 1)


def test_review_required_region_blocks_but_decoration_does_not(monkeypatch):
    from services.source_content import raster_reconstruction_store

    monkeypatch.setattr(raster_svg_layout, "require_analysis_digest", lambda analysis: None)
    monkeypatch.setattr(raster_reconstruction_store, "raster_review_blockers", lambda analysis: ())
    page = fx.page_manual(1)
    decoration = next(r for r in page.regions if r.role == "decoration")
    decoration.review_required = True
    render_raster_page_svg(fx.analysis(page), 1)
    page.regions[1].review_required = True
    with pytest.raises(ValueError, match="raster_analysis_review_unresolved"):
        render_raster_page_svg(fx.analysis(page), 1)


def test_missing_crop_blocks_redesign(monkeypatch):
    from services.source_content import raster_reconstruction_store

    monkeypatch.setattr(raster_svg_layout, "require_analysis_digest", lambda analysis: None)
    monkeypatch.setattr(raster_reconstruction_store, "raster_review_blockers", lambda analysis: ())
    page = fx.page_hero(1)
    next(r for r in page.regions if r.role == "screenshot").crop_artifact_ref = None
    with pytest.raises(ValueError, match="raster_image_crop_missing"):
        render_raster_page_svg(fx.analysis(page), 1)


def test_source_assets_offer_slots_only_for_layout_crops():
    analysis = fx.deck()
    for page in analysis.pages:
        approved = tuple(r.region_id for p in analysis.pages for r in p.regions)
        pin = SimpleNamespace(approved_region_ids=approved, full_page_preserve_pages=(), automatic_preserve_pages=(),
                             automatic_redesign_pages=tuple(p.page_number for p in analysis.pages))
        regions = _approved_crop_regions(analysis, pin, page.page_number)
        assert [r.region_id for r in regions] == [r.region_id for r in redesign_crop_regions(page)]
        assert page.reference_region.region_id not in {r.region_id for r in regions}
    page = analysis.pages[3]
    missing = tuple(r.region_id for p in analysis.pages for r in p.regions if r.region_id != "p4-raw")
    pin = SimpleNamespace(approved_region_ids=missing, full_page_preserve_pages=(), automatic_preserve_pages=(),
                          automatic_redesign_pages=tuple(p.page_number for p in analysis.pages))
    with pytest.raises(ValueError, match="raster_redesign_crop_region_not_approved"):
        _approved_crop_regions(analysis, pin, page.page_number)


def test_icons_stay_at_natural_size_and_only_the_brand_joins_the_header():
    page = fx._page(1, [
        fx.region(1, "title", "title", (40, 40, 700, 70), "아이콘 배치 확인"),
        fx.region(1, "brand", "logo", (1300, 30, 200, 50)),
        *[fx.region(1, f"icon{i}", "logo", (60 + i * 400, 300, 24, 24)) for i in range(4)],
        *[fx.region(1, f"label{i}", "body", (90 + i * 400, 300, 300, 40), f"항목 {i} 설명 문장입니다.")
          for i in range(4)],
    ])
    analysis = fx.analysis(page)
    svg = _svg(1, analysis)
    root = ET.fromstring(svg)
    images = {node.get("href"): node for node in root.iter(f"{NS}image")}
    brand = images[raster_slot_id(1, "p1-brand")]
    assert float(brand.get("y")) < 80  # header band
    for i in range(4):
        icon = images[raster_slot_id(1, f"p1-icon{i}")]
        assert int(icon.get("width")) <= 24 * 1.25 + 1 and int(icon.get("height")) <= 24 * 1.25 + 1
        assert float(icon.get("y")) > 100  # content, not header


def test_dense_page_retries_with_compact_header_before_failing():
    lines = "\n".join(f"{index}. 조밀한 페이지의 항목 설명 문장" for index in range(24))
    page = fx._page(1, [
        fx.region(1, "kicker", "body", (40, 5, 300, 20), "SAMSUNG DISPLAY Security Portal"),
        fx.region(1, "title", "title", (40, 40, 800, 70), "헤더를 줄여야 들어가는 조밀한 페이지 제목입니다"),
        fx.region(1, "subtitle", "body", (40, 115, 800, 30), "부제목 한 줄"),
        fx.region(1, "a", "body", (40, 200, 780, 700), lines),
        fx.region(1, "b", "body", (850, 200, 780, 700), lines),
    ])
    texts = dict(_texts(_svg(1, fx.analysis(page))))
    assert _dense(texts["rd-title"]) == _dense("헤더를 줄여야 들어가는 조밀한 페이지 제목입니다")


SEMANTIC = fx.semantic_deck()


@pytest.mark.parametrize("number", range(1, len(SEMANTIC.pages) + 1))
def test_semantic_pages_are_valid_complete_and_carded(number):
    page = SEMANTIC.pages[number - 1]
    svg = _svg(number, SEMANTIC)
    slots = {raster_slot_id(number, r.region_id) for r in redesign_crop_regions(page)}
    validate_svg(svg, width=1188, height=840,
                 source_manifest={slot: {"required": True, "allowOmit": False} for slot in slots})
    texts = _texts(svg)
    title, _, _ = build_items(page)
    for region in _editable_regions(page):
        prefix = "rd-title" if region is title else f"rd-{_tag(region.region_id)}"
        emitted = "".join(value for key, value in texts if key == prefix or key.startswith(prefix + "-"))
        assert _dense(emitted) == _dense(_source_text(region)), region.region_id
    root = ET.fromstring(svg)
    sizes = [float(n.get("font-size")) for n in root.iter()
             if n.tag in {f"{NS}text", f"{NS}tspan"} and n.get("font-size")]
    assert min(sizes) >= MIN_FONT_UNITS
    headings = [n for n in root.iter(f"{NS}tspan") if n.get("font-weight") == "700"]
    assert headings  # card headings are bold


def test_semantic_cards_follow_group_structure():
    page = SEMANTIC.pages[0]
    _, _, items = build_items(page)
    from services.source_content.raster_redesign_header import split_header_items
    _, _, _, _, items = split_header_items(page.regions[1], items, (fx.SOURCE_W, fx.SOURCE_H))
    plan = plan_content(items, (48.0, 150.0, 1092.0, 630.0), source_size=(fx.SOURCE_W, fx.SOURCE_H))
    cards = [p for p in plan.placements if p.kind == "card"]
    assert len(cards) == 7  # 4 step cards + 3 pain-point cards
    step_tops = {round(card.y) for card in cards[:4]}
    assert len(step_tops) == 1  # four steps side by side
    assert len({round(card.y) for card in cards[4:]}) == 1  # pain points in their own row of three
    for placed in plan.placements:
        if placed.kind == "image" and not placed.item.is_icon:
            floor = min(placed.item.native_height * 0.5, 0.3 * placed.w * placed.item.aspect)
            assert placed.h >= floor - 0.5


def _dense_grouped_page(page=1):
    long = "\n".join(f"{index}. 조밀한 카드 본문 문장이 계속 이어집니다" for index in range(9))
    regions = [fx.region(page, "title", "title", (40, 40, 700, 70), "조밀한 그룹 페이지")]
    for index in range(8):
        x, y = 30 + (index % 4) * 405, 150 + (index // 4) * 380
        regions.append(fx.region(page, f"card{index}", "body", (x, y, 380, 360), f"카드 {index}\n{long}",
                                 group=f"g{index}", heading=1))
    regions.append(fx.region(page, "foot", "body", (40, 905, 600, 25), "DevAX 기반 제안서 01", group="footer"))
    return fx._page(page, fx._ordered(regions))


def test_dense_page_uses_minimal_group_pixel_fallback_with_advisories():
    from services.source_content.raster_redesign_layout import render_redesign_page
    from services.source_content.raster_svg_layout import raster_page_render_advisories

    analysis = fx.analysis(_dense_grouped_page())
    result = render_redesign_page(analysis, 1)
    assert result.advisories and all(a["code"] == "dense_group_pixel_fallback" for a in result.advisories)
    # The fewest groups that give a fitting reflow which keeps the source order
    # (crops in their groups' places, never a side gallery of cards).
    assert result.composition == "reflow" and result.incoherence is None
    assert raster_page_render_advisories(analysis, 1, result.svg) == result.advisories
    root = ET.fromstring(result.svg)
    images = {node.get("href"): node for node in root.iter(f"{NS}image")}
    for advisory in result.advisories:
        crop = images[raster_slot_id(1, advisory["regionId"])]
        region = analysis.pages[0].reference_region  # source pixels: 380x360 per card
        assert region is not None
        assert int(crop.get("height")) >= 0.6 * 360 - 1  # at least 0.6x native pixels
    texts = dict(_texts(result.svg))
    replaced = {rid for advisory in result.advisories for rid in advisory["replacedRegionIds"]}
    for index in range(8):
        key = f"rd-{_tag(f'p1-card{index}')}"
        assert any(k.startswith(key) for k in texts) != (f"p1-card{index}" in replaced)


def test_footer_group_renders_on_the_footer_line_and_assets_follow_the_svg():
    analysis = fx.analysis(_dense_grouped_page())
    svg = _svg(1, analysis)
    footer = next(n for n in ET.fromstring(svg).iter(f"{NS}text") if "".join(n.itertext()) == "DevAX 기반 제안서 01")
    assert float(footer.get("y")) > 840 - 44
    page = analysis.pages[0]
    approved = tuple(r.region_id for r in page.regions)
    pin = SimpleNamespace(approved_region_ids=approved, full_page_preserve_pages=(), automatic_preserve_pages=(),
                          automatic_redesign_pages=(1,))
    regions = _approved_crop_regions(analysis, pin, 1, svg)
    hrefs = {n.get("href") for n in ET.fromstring(svg).iter(f"{NS}image")}
    assert {raster_slot_id(1, r.region_id) for r in regions} == hrefs
    from services.source_content.raster_contracts import RasterRegion

    assert regions and all(isinstance(r, RasterRegion) and r.region_id.endswith("-pixels") for r in regions)


def test_group_crop_is_a_lossless_cut_of_the_reference_image():
    import io

    from PIL import Image

    from services.raster_source_assets import crop_png

    source = Image.new("RGBA", (100, 80), (10, 20, 30, 255))
    source.putpixel((12, 7), (200, 100, 50, 255))
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")
    cut = Image.open(io.BytesIO(crop_png(buffer.getvalue(), (10, 5, 20, 10))))
    assert cut.size == (20, 10) and cut.getpixel((2, 2)) == (200, 100, 50, 255)
    with pytest.raises(ValueError, match="outside_source"):
        crop_png(buffer.getvalue(), (90, 70, 20, 20))


def test_dense_page_keeps_source_arrangement_group_by_group():
    from services.source_content.raster_redesign_layout import render_redesign_page
    from services.source_content.raster_svg_layout import raster_page_render_advisories

    long = "\n".join(f"{index}. 매우 조밀한 원본 문장이 페이지 전체에 이어집니다" for index in range(14))
    regions = [fx.region(1, "title", "title", (40, 40, 700, 70), "원본 배치를 유지하는 조밀한 페이지")]
    for index in range(6):  # six tall groups: per-group crops need two rows, the source needs one
        x = 30 + index * 270
        regions.append(fx.region(1, f"text{index}", "body", (x, 150, 260, 380), f"항목 {index}\n{long}",
                                 group=f"g{index}", heading=1))
        regions.append(fx.region(1, f"shot{index}", "screenshot", (x, 540, 260, 340), group=f"g{index}"))
    analysis = fx.analysis(fx._page(1, fx._ordered(regions)))
    result = render_redesign_page(analysis, 1)
    # Not one page-content picture: each group is its own crop, in the source arrangement.
    assert [a["groupId"] for a in result.advisories] == [*(f"g{index}" for index in range(6)), "source-geometry"]
    assert all(a["code"] == "dense_group_pixel_fallback" for a in result.advisories[:-1])
    assert result.advisories[-1]["reason"] == "content_overflow"
    assert raster_page_render_advisories(analysis, 1, result.svg) == result.advisories
    texts = dict(_texts(result.svg))
    assert _dense(texts["rd-title"]) == _dense("원본 배치를 유지하는 조밀한 페이지")  # title stays editable
    images = [n for n in ET.fromstring(result.svg).iter(f"{NS}image")]
    assert len(images) == 6
    xs, ys = [float(n.get("x")) for n in images], {round(float(n.get("y")), 1) for n in images}
    assert xs == sorted(xs) and len(ys) == 1  # one row, left to right, as in the source
    assert all(int(n.get("height")) >= 0.6 * 730 - 1 for n in images)  # each group box is 260x730 px


def test_uncovered_ink_crops_are_never_below_their_legibility_scale():
    page = fx.semantic_steps(1)
    ink = fx.region(1, "ink", "screenshot", (200, 176, 220, 52), group="step0")
    ink.review_reason = "uncovered_ink"
    page.regions = (*page.regions, ink)
    svg = _svg(1, fx.analysis(page))
    node = next(n for n in ET.fromstring(svg).iter(f"{NS}image") if n.get("href") == raster_slot_id(1, "p1-ink"))
    assert int(node.get("height")) >= 0.6 * 52 - 1


def test_layout_invariants_reject_image_over_text_and_text_outside_card():
    from services.source_content.raster_redesign_plan import layout_violation
    from services.source_content.raster_redesign_text import TextLine
    from services.source_content.raster_redesign_tree import Item, Placed

    text_item = Item("text", fx.region(1, "t", "body", (0, 0, 10, 10), "x"))
    image_item = Item("image", fx.region(1, "i", "screenshot", (0, 0, 10, 10)))
    line = TextLine("가나다라마바사", 20.0, False, "#000000", 24.0)
    text = Placed("text", 100.0, 100.0, 300.0, 40.0, text_item, lines=[line])
    assert layout_violation([text, Placed("image", 150.0, 110.0, 50.0, 50.0, image_item)]) == "image_over_text"
    assert layout_violation([text, Placed("image", 150.0, 200.0, 50.0, 50.0, image_item)]) is None
    card = Placed("card", 90.0, 90.0, 80.0, 60.0)
    assert layout_violation([card, text]) == "text_outside_card"


@pytest.mark.parametrize("deck", ["deck", "semantic"])
def test_every_fixture_plan_satisfies_the_layout_invariants(deck):
    from services.source_content.raster_redesign_plan import layout_violation

    analysis = fx.deck() if deck == "deck" else fx.semantic_deck()
    for page in analysis.pages:
        _, _, items = build_items(page)
        plan = plan_content(items, (48.0, 150.0, 1092.0, 630.0), source_size=(fx.SOURCE_W, fx.SOURCE_H))
        assert layout_violation(plan.placements) is None


def test_pixel_line_sits_inline_between_its_text_lines_left_aligned():
    page = fx.semantic_steps(1)
    line = fx.region(1, "pxline", "screenshot", (90, 240, 300, 20), group="step0", order=None)
    line.review_reason, line.editable_fallback = "low_confidence_text_pixels", True
    regions = list(page.regions)
    index = next(i for i, r in enumerate(regions) if r.region_id == "p1-text0")
    regions.insert(index + 1, line)
    after = fx.region(1, "after0", "body", (70, 262, 300, 20), "뒤따르는 문장입니다.", group="step0")
    regions.insert(index + 2, after)
    page.regions = tuple(fx._ordered(regions))
    svg = _svg(1, fx.analysis(page))
    root = ET.fromstring(svg)
    image = next(n for n in root.iter(f"{NS}image") if n.get("href") == raster_slot_id(1, "p1-pxline"))
    texts = {n.get("id"): n for n in root.iter(f"{NS}text")}
    before = next(n for k, n in texts.items() if k and k.startswith(f"rd-{_tag('p1-text0')}"))
    follow = next(n for k, n in texts.items() if k and k.startswith(f"rd-{_tag('p1-after0')}"))
    last_before = max(float(s.get("y")) for s in before.iter(f"{NS}tspan") if s.get("y")) if any(
        s.get("y") for s in before.iter(f"{NS}tspan")) else float(before.get("y"))
    assert last_before < float(image.get("y")) < float(follow.get("y"))
    assert abs(float(image.get("x")) - float(follow.get("x"))) < 0.5  # left-aligned with the flow
    assert 15 <= int(image.get("height")) <= 40  # text-sized, not a gallery capture


def test_regions_inside_a_drawn_crop_are_drawn_once_with_advisory():
    from services.source_content.raster_svg_layout import raster_page_render_advisories

    page = fx.semantic_hero(1)
    inner = fx.region(1, "inner-shot", "screenshot", (700, 300, 200, 100))  # inside p1-shot's source box
    page.regions = (*page.regions, inner)
    analysis = fx.analysis(page)
    svg = _svg(1, analysis)
    assert raster_slot_id(1, "p1-inner-shot") not in svg
    assert svg.count(raster_slot_id(1, "p1-shot")) == 1
    assert {"code": "region_carried_by_crop", "regionId": "p1-shot", "replacedRegionIds": ["p1-inner-shot"]} in (
        raster_page_render_advisories(analysis, 1, svg))
    assert "p1-inner-shot" not in {r.region_id for r in redesign_crop_regions(page)}


def test_pixel_word_pieces_on_one_source_line_form_one_inline_row():
    page = fx.semantic_hero(1)
    pieces = []
    for index, x in enumerate((100, 260, 420)):
        piece = fx.region(1, f"word{index}", "screenshot", (x, 900, 150, 30))
        piece.review_reason, piece.editable_fallback = "low_confidence_text_pixels", True
        pieces.append(piece)
    page.regions = tuple(fx._ordered([*page.regions, *pieces]))
    root = ET.fromstring(_svg(1, fx.analysis(page)))
    nodes = [next(n for n in root.iter(f"{NS}image") if n.get("href") == raster_slot_id(1, f"p1-word{i}"))
             for i in range(3)]
    ys = {round(float(n.get("y")), 1) for n in nodes}
    xs = [float(n.get("x")) for n in nodes]
    assert len(ys) == 1 and xs == sorted(xs)  # one line, reading order, no tiles/cards


def test_title_pixel_pieces_without_title_region_render():
    page = fx.semantic_hero(1)
    regions = [r for r in page.regions if r.role != "title"]
    for index, x in enumerate((100, 220)):
        piece = fx.region(1, f"tword{index}", "screenshot", (x, 60, 100, 40), group="title")
        piece.review_reason, piece.editable_fallback = "uncovered_ink", True
        regions.append(piece)
    page.regions = tuple(fx._ordered(regions))
    svg = _svg(1, fx.analysis(page))
    assert raster_slot_id(1, "p1-tword0") in svg and raster_slot_id(1, "p1-tword1") in svg


def test_title_carried_as_pixels_renders_in_the_title_band():
    page = fx.semantic_hero(1)
    regions = [r for r in page.regions if r.role != "title"]
    crop = fx.region(1, "title-px", "screenshot", (40, 90, 900, 70), group="title")
    crop.review_reason, crop.editable_fallback = "low_confidence_text_pixels", True
    page.regions = tuple(fx._ordered([crop, *regions]))
    root = ET.fromstring(_svg(1, fx.analysis(page)))
    node = next(n for n in root.iter(f"{NS}image") if n.get("href") == raster_slot_id(1, "p1-title-px"))
    assert float(node.get("y")) < 110  # header band, above the header rule


def test_pixel_paragraph_flows_inline_at_text_scale():
    page = fx.semantic_steps(1)
    para = fx.region(1, "para", "screenshot", (90, 240, 300, 60), group="step0")
    para.review_reason, para.editable_fallback = "paragraph_pixels", True
    para.ocr_token_indices = (0,)
    page.regions = tuple(fx._ordered([*page.regions, para]))
    root = ET.fromstring(_svg(1, fx.analysis(page)))
    node = next(n for n in root.iter(f"{NS}image") if n.get("href") == raster_slot_id(1, "p1-para"))
    assert int(node.get("height")) <= 90  # text-matched block, not a gallery capture
