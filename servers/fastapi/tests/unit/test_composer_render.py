"""Composer renderer: admitted HTML whose source bindings hold by construction."""

from __future__ import annotations

import re

import pytest
from lxml import html as lxml_html

from services.composer.contracts import source_px_scale
from services.composer.plan import (
    CANVAS_HEIGHT,
    PX_PER_PT,
    TEMPLATE_TYPE_SCALE_PX,
    ComposerComponent,
    ComposerDeckPlan,
    ComposerSlidePlan,
    ComposerTokens,
    FitParameters,
    Paragraph,
    SourceRect,
    SourceRunRef,
    TextRun,
)
from services.composer.render import (
    ComposerRenderError,
    admission_projection,
    balanced_partition,
    render_slide,
)
from services.composer.render_css import MIN_FONT_PX, chrome_geometry, px
from services.composer.render_geometry import page_box, rect_box
from services.composer.render_title import (
    subtitle_px_from_title,
    title_band_height_px,
    title_band_inner_width,
    title_line_count,
)
from services.source_content.source_stamp_dom import deterministic_source_qid, parse_stamp_dom
from services.source_content.text_stamping import stamp_source_block_ids
from utils.authored_dom_quality import validate_declared_authored_qids
from utils.renderer_isolation import admit_authored_html

TOKENS = ComposerTokens(
    background="#FFFFFF",
    surface="#F3F6FA",
    text_primary="#0F172A",
    text_secondary="#475569",
    accent="#1473E6",
    accent_text="#1473E6",
    accent_soft="#E8F0FE",
    rule="#D9E2EF",
)
PAGE_EMU = (9144000, 6858000)  # 4:3, 10 x 7.5 in


def para(block: str, text: str, run: int | None = None, **kwargs) -> Paragraph:
    binding = block if run is None else f"{block}:run:{run}"
    return Paragraph(
        text=text,
        source=SourceRunRef(block_id=block, run_start=run, run_end=run, binding_id=binding),
        **kwargs,
    )


def rect(x: float, y: float, w: float, h: float) -> SourceRect:
    return SourceRect(x=x, y=y, width=w, height=h)


def _title() -> ComposerComponent:
    return ComposerComponent(
        component_id="title", kind="title", semantic_role="title",
        paragraphs=[para("b1", "분기 실적 요약")], source_rect=rect(0.05, 0.05, 0.9, 0.12), source_size_pt=32,
    )


def _furniture() -> list[ComposerComponent]:
    return [
        ComposerComponent(
            component_id="footer", kind="furniture", semantic_role="furniture", furniture_kind="footer",
            paragraphs=[para("b5", "Confidential · ACME")], source_rect=rect(0.05, 0.92, 0.5, 0.05), source_size_pt=9,
        ),
        ComposerComponent(
            component_id="page", kind="furniture", semantic_role="furniture", furniture_kind="page_number",
            paragraphs=[para("b6", "3 / 12")], source_rect=rect(0.85, 0.92, 0.1, 0.05), source_size_pt=9,
        ),
    ]


TEXT_BLOCKS = [
    ("b1", "분기 실적 요약"),
    ("b2", "첫 번째 문단입니다. 매출은 전년 대비 12% 성장했습니다."),
    ("b3", "두 번째 문단입니다.\n두 줄로 이어집니다."),
    ("b4", "• 항목 하나\n• 항목 둘\n• 항목 셋\n• 항목 넷"),
    ("b5", "Confidential · ACME"),
    ("b6", "3 / 12"),
]


def text_plan() -> ComposerSlidePlan:
    body = ComposerComponent(
        component_id="body", kind="paragraph", semantic_role="body",
        paragraphs=[
            para(
                "b2", "첫 번째 문단입니다. 매출은 전년 대비 12% 성장했습니다.",
                runs=[TextRun(text="첫 번째 문단입니다. ", bold=True, size_pt=14),
                      TextRun(text="매출은 전년 대비 12% 성장했습니다.", size_pt=14)],
            ),
            para("b3", "두 번째 문단입니다.\n두 줄로 이어집니다."),
        ],
        source_rect=rect(0.05, 0.2, 0.5, 0.4), source_size_pt=14,
    )
    bullets = ComposerComponent(
        component_id="list", kind="bullets", semantic_role="body",
        paragraphs=[
            para("b4", f"• 항목 {word}", index, bullet=True)
            for index, word in enumerate(("하나", "둘", "셋", "넷"))
        ],
        source_rect=rect(0.55, 0.2, 0.4, 0.4), source_size_pt=12,
    )
    return ComposerSlidePlan(
        slide_index=2, source_unit_id="u1", archetype="text",
        title=_title(), components=[body, bullets], furniture=_furniture(), text_chars=80,
    )


def comparison_plan() -> ComposerSlidePlan:
    component = ComposerComponent(
        component_id="cmp", kind="comparison", semantic_role="body",
        group_headers=[para("h1", "기존 방식"), para("h2", "개선 방식")],
        groups=[[para("c1", "수작업 검수", 0, bullet=True), para("c1", "주 1회 보고", 1, bullet=True)],
                [para("c2", "자동 검수"), para("c3", "실시간 대시보드")]],
        source_rect=rect(0.05, 0.2, 0.9, 0.6),
    )
    return ComposerSlidePlan(
        slide_index=0, source_unit_id="u2", archetype="comparison", title=_title(), components=[component]
    )


COMPARISON_BLOCKS = [("b1", "분기 실적 요약"), ("h1", "기존 방식"), ("h2", "개선 방식"),
                     ("c1", "수작업 검수\n주 1회 보고"), ("c2", "자동 검수"), ("c3", "실시간 대시보드")]


def process_plan(steps: int = 3) -> ComposerSlidePlan:
    component = ComposerComponent(
        component_id="flow", kind="process", semantic_role="body",
        group_headers=[para(f"s{i}h", f"단계 {i + 1}") for i in range(steps)],
        groups=[[para(f"s{i}", f"작업 내용 {i + 1}")] for i in range(steps)],
        source_rect=rect(0.05, 0.3, 0.9, 0.3),
    )
    return ComposerSlidePlan(
        slide_index=0, source_unit_id="u3", archetype="process", title=_title(), components=[component]
    )


def process_blocks(steps: int = 3) -> list[tuple[str, str]]:
    blocks = [("b1", "분기 실적 요약")]
    for i in range(steps):
        blocks.extend(((f"s{i}h", f"단계 {i + 1}"), (f"s{i}", f"작업 내용 {i + 1}")))
    return blocks


def table_plan() -> ComposerSlidePlan:
    rows = [["지표", "Before", "After"], ["처리 시간", "48h", "6h"], ["오류율", "4.2%", "0.8%"]]
    component = ComposerComponent(
        component_id="tbl", kind="table", semantic_role="body",
        table_rows=[[para("t1", text, r * 3 + c) for c, text in enumerate(row)] for r, row in enumerate(rows)],
        source_rect=rect(0.05, 0.2, 0.9, 0.5), source_size_pt=12,
    )
    return ComposerSlidePlan(
        slide_index=0, source_unit_id="u4", archetype="table", title=_title(), components=[component]
    )


def image_plan(count: int = 1, with_text: bool = False) -> ComposerSlidePlan:
    images = [
        ComposerComponent(component_id=f"img{i}", kind="image", semantic_role="image", asset_id=f"asset-{i}",
                          occurrence_id=f"occ-{i}", aspect_ratio=1.5, source_rect=rect(0.1, 0.2, 0.8, 0.6))
        for i in range(count)
    ]
    components = []
    if with_text:
        components.append(ComposerComponent(component_id="cap", kind="caption", semantic_role="caption",
                                            paragraphs=[para("k1", "그림 1. 매출 추이")]))
    return ComposerSlidePlan(slide_index=0, source_unit_id="u5", archetype="image_grid" if count > 1 else "image_only",
                             title=_title(), components=components, images=images)


def deck_for(plan: ComposerSlidePlan) -> ComposerDeckPlan:
    return ComposerDeckPlan(
        slides=[plan], tokens=TOKENS, source_page_emu=PAGE_EMU, source_px_scale=source_px_scale(PAGE_EMU)
    )


def elements(html: str) -> list[tuple[str, dict[str, str], str]]:
    """(tag, attributes, rendered text) for every element, in document order."""

    parser = parse_stamp_dom(html)
    found = []
    for node in parser.nodes:
        raw = parser.tokens[node.start]
        tag = re.match(r"<\s*([a-zA-Z0-9]+)", raw).group(1).lower()
        attrs = dict(re.findall(r'([:\w-]+)="([^"]*)"', raw))
        found.append((tag, attrs, parser.node_text(node)))
    return found


def bound(html: str) -> dict[str, tuple[str, dict[str, str], str]]:
    result = {}
    for tag, attrs, text in elements(html):
        binding = attrs.get("data-source-block-id")
        if binding is not None:
            assert binding not in result, f"binding {binding} rendered twice"
            result[binding] = (tag, attrs, text)
    return result


def assert_gates(html: str, blocks: list[tuple[str, str]]) -> None:
    admit_authored_html(admission_projection(html))
    assert validate_declared_authored_qids(html)
    assert stamp_source_block_ids(html, blocks) == html
    assert "<script" not in html.lower()
    assert "transform" not in html
    assert "data-native-candidate" not in html
    assert not re.search(r"<img[^>]*\ssrc=", html)
    assert "http:" not in html and "https:" not in html
    # No layout whitespace between elements: the only text between tags is source
    # text inside a bound element (the space between two formatting spans).
    assert not re.search(r">\s+<(?!span)", html[html.index("<body>"):]), "layout whitespace between elements"


def font_size(attrs: dict[str, str]) -> str:
    return re.search(r"font-size:([^;\"]+)", attrs["style"]).group(1)


ALL_PARAMS = [
    FitParameters(),
    FitParameters(rung=1, density=1, columns=2),
    FitParameters(rung=3, chrome_level=3, density=1, type_scale=0.75, columns=3),
    FitParameters(rung=4, chrome_level=3, density=1, source_relative=True, source_factor=0.95),
    FitParameters(
        rung=5, chrome_level=3, density=1, source_geometry=True, page_scale=0.95, box_text_scale={"body": 0.9}
    ),
]


@pytest.mark.parametrize("params", ALL_PARAMS, ids=lambda p: f"rung{p.rung}")
def test_text_page_passes_gates_and_binds_every_run_once(params: FitParameters) -> None:
    plan = text_plan()
    html = render_slide(plan, TOKENS, params, deck_for(plan))

    assert html.startswith('<!doctype html><html><head><meta charset="utf-8"><style>')
    assert "html,body{width:1280px;height:720px;margin:0;padding:0;overflow:hidden;}" in html
    assert f"body{{background:{TOKENS.background};" in html
    assert_gates(html, TEXT_BLOCKS)

    by_binding = bound(html)
    expected = ["b1", "b2", "b3", "b4:run:0", "b4:run:1", "b4:run:2", "b4:run:3", "b5", "b6"]
    assert sorted(by_binding) == sorted(expected)
    for binding, (_tag, attrs, _text) in by_binding.items():
        assert attrs["data-qid"] == deterministic_source_qid(binding)
        assert attrs["data-semantic-role"]
    assert by_binding["b1"][0] == "h1"
    assert by_binding["b1"][2] == "분기 실적 요약"
    assert by_binding["b2"][2] == "첫 번째 문단입니다. 매출은 전년 대비 12% 성장했습니다."
    assert by_binding["b3"][2] == "두 번째 문단입니다.\n두 줄로 이어집니다."
    # Bullets are drawn by a sibling decoration, never typed into the bound element.
    for index, word in enumerate(("하나", "둘", "셋", "넷")):
        assert by_binding[f"b4:run:{index}"][2] == f"항목 {word}"
    markers = [text for tag, attrs, text in elements(html) if "cx-marker" in attrs.get("class", "")]
    assert markers == ["•"] * 4
    assert all(
        attrs.get("aria-hidden") == "true" and attrs["data-semantic-role"] == "decoration"
        for _tag, attrs, _text in elements(html) if "cx-marker" in attrs.get("class", "")
    )
    # Source order within the block.
    positions = [html.index(f'data-source-block-id="b4:run:{i}"') for i in range(4)]
    assert positions == sorted(positions)
    # Run formatting survives as strict-subset inline spans.
    assert re.search(
        r'<span data-qid="[^"]+\.r0" data-semantic-role="body" style="font-weight:700">첫 번째 문단입니다\.</span>',
        html,
    )


def test_every_visible_element_carries_a_unique_qid() -> None:
    plan = text_plan()
    html = render_slide(plan, TOKENS, FitParameters(), deck_for(plan))
    qids = []
    for tag, attrs, _text in elements(html):
        if tag in {"html", "head", "meta", "style", "body", "br"}:
            continue
        assert "data-qid" in attrs, f"<{tag}> without data-qid"
        qids.append(attrs["data-qid"])
    assert len(qids) == len(set(qids))


def test_chrome_levels_change_bands_but_not_title_typography() -> None:
    plan = text_plan()
    deck = deck_for(plan)
    bands = {}
    levels = {0: (120, 56, 64), 1: (96, 40, 48), 2: (64, 28, 32), 3: (48, 24, 24)}
    title_padding_y = {0: 20, 1: 16, 2: 12, 3: 10}
    for level, (title_band, footer_band, margin) in levels.items():
        html = render_slide(plan, TOKENS, FitParameters(chrome_level=level), deck)
        style = html[html.index("<style>") : html.index("</style>")]
        # Bands are flex children with a *minimum* height, never a fixed one: a
        # wrapped title grows its band instead of clipping or going off-canvas.
        pad = title_padding_y[level]
        assert (
            f".cx-title-band{{flex:0 0 auto;width:1280px;min-height:{title_band}px;"
            f"padding:{pad}px {margin}px {pad}px 64px;"
        ) in style
        assert (
            f".cx-footer-band{{flex:0 0 auto;width:1280px;min-height:{footer_band}px;padding:8px {margin}px;"
        ) in style
        assert f".cx-content{{flex:1 1 auto;min-height:0;width:1280px;padding:0 {margin}px;" in style
        assert ".cx-title-band{position:absolute" not in style
        assert ".cx-footer-band{position:absolute" not in style
        assert ".cx-content{position:absolute" not in style
        title = bound(html)["b1"][1]
        bands[level] = font_size(title)
    assert set(bands.values()) == {px(TEMPLATE_TYPE_SCALE_PX["title"])}


def test_title_and_subtitle_line_heights_never_touch_the_canvas_edge() -> None:
    """The Korean glyph-clipping defect: 1.15 line-height plus zero padding
    let a title's ascenders sit above y=0, where body{overflow:hidden} clips
    them (see artifacts/composer-offline-eval/diag-slide6-rung0.html)."""

    plan = text_plan()
    plan.subtitle = ComposerComponent(component_id="sub", kind="subtitle", semantic_role="subtitle",
                                      paragraphs=[para("b7", "2026년 2분기")])
    deck = deck_for(plan)
    html = render_slide(plan, TOKENS, FitParameters(chrome_level=0), deck)
    style = html[html.index("<style>") : html.index("</style>")]
    assert ".cx-title{" in style
    assert "line-height:1.3;" in style[style.index(".cx-title{") : style.index(".cx-title{") + 200]
    assert ".cx-subtitle{" in style
    assert "line-height:1.35;" in style[style.index(".cx-subtitle{") : style.index(".cx-subtitle{") + 100]
    # Every chrome level pads the title band; the footer band gets a flat floor.
    for level, pad in {0: 20, 1: 16, 2: 12, 3: 10}.items():
        geometry = chrome_geometry(level)
        assert geometry.title_padding_y == pad
        level_html = render_slide(plan, TOKENS, FitParameters(chrome_level=level), deck)
        level_style = level_html[level_html.index("<style>") : level_html.index("</style>")]
        assert f"padding:{pad}px" in level_style
        assert "padding:8px" in level_style  # footer band, every level


def test_accent_text_paints_marker_accent_and_kpi_while_accent_still_fills() -> None:
    """``accent_text`` (readable_accent_color) drives text-bearing rules only.

    ``accent`` keeps painting every fill/stroke/rule (band border, cover rule,
    step border, arrow, quote rail) unchanged: only the classes that render
    accent-coloured *text* switch to the legible variant.
    """

    tokens = ComposerTokens(
        background="#FFFFFF", surface="#F3F6FA", text_primary="#0F172A", text_secondary="#475569",
        accent="#FFD400", accent_text="#7A6600", accent_soft="#FFF6CC", rule="#D9E2EF",
    )
    plan = text_plan()
    deck = deck_for(plan)
    html = render_slide(plan, tokens, FitParameters(chrome_level=0), deck)
    style = html[html.index("<style>") : html.index("</style>")]

    for selector in (".cx-marker{", ".cx-accent{", ".cx-kpi{"):
        assert selector in style, selector
        body = style[style.index(selector) : style.index("}", style.index(selector)) + 1]
        assert f"color:{tokens.accent_text}" in body, body
        assert tokens.accent not in body, body

    # The stylesheet is one static block for the whole slide, so every
    # selector is emitted regardless of which components the plan uses.
    for selector in (".cx-cover-rule{", ".cx-step{", ".cx-arrow{", ".cx-quote{"):
        assert selector in style, selector
        body = style[style.index(selector) : style.index("}", style.index(selector)) + 1]
        assert tokens.accent in body, body
        assert tokens.accent_text not in body, body

    band = style[style.index(".cx-title-band{") : style.index(".cx-title-stack{")]
    assert f"border-bottom:3px solid {tokens.accent};" in band


def test_title_stack_band_height_estimate_fits_the_canvas() -> None:
    """diag-slide6-rung0.html: a two-run 36px title plus subtitle at chrome
    level 0. The band height ``render_title`` and ``render_css`` imply from
    padding + lines x line-height must stay under the canvas minus the footer
    band, and must be at least the band's own min-height (the stack is taller
    than the floor, so the band is expected to grow, not clip)."""

    geometry = chrome_geometry(0)
    title_px = 36.0
    subtitle_px = subtitle_px_from_title(title_px)
    texts = ["1주차", "숏폼 대행사업 시작하기"]
    width = title_band_inner_width(geometry, has_header=False)
    title_lines = title_line_count(texts, title_px, width)
    subtitle_lines = title_line_count(["120분"], subtitle_px, width)
    estimated = title_band_height_px(
        title_lines,
        title_px,
        padding_y=geometry.title_padding_y,
        subtitle_lines=subtitle_lines,
        subtitle_px=subtitle_px,
    )
    assert estimated >= geometry.title_band
    assert estimated < CANVAS_HEIGHT - geometry.footer_band


def test_subtitle_moves_from_band_to_content_at_compact_chrome() -> None:
    plan = text_plan()
    plan.subtitle = ComposerComponent(component_id="sub", kind="subtitle", semantic_role="subtitle",
                                      paragraphs=[para("b7", "2026년 2분기")])
    blocks = [*TEXT_BLOCKS, ("b7", "2026년 2분기")]
    deck = deck_for(plan)
    in_band = render_slide(plan, TOKENS, FitParameters(chrome_level=0), deck)
    compact = render_slide(plan, TOKENS, FitParameters(chrome_level=3), deck)
    assert_gates(in_band, blocks)
    assert_gates(compact, blocks)
    assert in_band.index('data-source-block-id="b7"') < in_band.index("</header>")
    assert compact.index('data-source-block-id="b7"') > compact.index("</header>")


def test_type_scale_and_density_follow_parameters() -> None:
    plan = text_plan()
    deck = deck_for(plan)
    loose = render_slide(plan, TOKENS, FitParameters(), deck)
    dense = render_slide(plan, TOKENS, FitParameters(density=1, type_scale=0.75, columns=2), deck)
    assert ".cx-body{line-height:1.6;}" in loose and ".cx-col{display:flex;flex-direction:column;gap:24px;" in loose
    assert ".cx-body{line-height:1.4;}" in dense and ".cx-col{display:flex;flex-direction:column;gap:12px;" in dense
    assert 'class="cx-component cx-list cx-stack cx-panel"' in loose
    assert "cx-panel" not in dense[dense.index("<body>"):]
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in dense
    assert font_size(bound(loose)["b2"][1]) == "20px"
    assert font_size(bound(dense)["b2"][1]) == "15px"
    # Supporting text keeps its own floor instead of following the body scale.
    assert font_size(bound(dense)["b5"][1]) == "12px"
    # Title fit is independent of the body scale below rung 3: type_scale=0.75
    # here never reaches the title, which stays at the template size.
    assert font_size(bound(dense)["b1"][1]) == "40px"


def test_source_relative_sizes_use_each_blocks_source_size() -> None:
    plan = text_plan()
    deck = deck_for(plan)
    plan.furniture[1].source_size_pt = 7
    params = FitParameters(rung=4, chrome_level=3, source_relative=True, source_factor=0.95)
    html = render_slide(plan, TOKENS, params, deck)
    by_binding = bound(html)
    scale = deck.source_px_scale * params.source_factor
    assert font_size(by_binding["b1"][1]) == px(32 * PX_PER_PT * scale)
    assert font_size(by_binding["b2"][1]) == px(14 * PX_PER_PT * scale)   # run sizes, 14 pt
    assert font_size(by_binding["b3"][1]) == px(14 * PX_PER_PT * scale)   # component size fallback
    assert font_size(by_binding["b4:run:0"][1]) == px(12 * PX_PER_PT * scale)
    assert font_size(by_binding["b5"][1]) == px(9 * PX_PER_PT * scale)
    # 7 pt furniture would fall below the floor; it is clamped, never smaller.
    assert 7 * PX_PER_PT * scale < MIN_FONT_PX
    assert font_size(by_binding["b6"][1]) == px(MIN_FONT_PX)
    # An element without any source size keeps the template scale.
    plan.components[0].source_size_pt = None
    plan.components[0].paragraphs[1].runs = []
    html = render_slide(plan, TOKENS, params, deck)
    assert font_size(bound(html)["b3"][1]) == px(TEMPLATE_TYPE_SCALE_PX["body"])


def test_source_geometry_places_components_in_their_scaled_source_rects() -> None:
    plan = text_plan()
    deck = deck_for(plan)
    assert deck.source_px_scale == 1.0  # 4:3 page fits by height: 960 x 720 px
    params = FitParameters(rung=5, chrome_level=3, source_geometry=True, page_scale=0.95, box_text_scale={"body": 0.9})
    html = render_slide(plan, TOKENS, params, deck)
    assert_gates(html, TEXT_BLOCKS)
    assert "cx-title-band" not in html[html.index("<body>"):]
    page_w, page_h = 960 * 0.95, 720 * 0.95
    x0, y0 = (1280 - page_w) / 2, (720 - page_h) / 2
    title_box = (
        f"left:{px(x0 + 0.05 * page_w)};top:{px(y0 + 0.05 * page_h)};"
        f"width:{px(0.9 * page_w)};height:{px(0.12 * page_h)}"
    )
    # The title's own geo wrapper carries an explicit z-index so it is always the
    # topmost element wherever it sits, protecting it from any component whose
    # real rendered text overflows its own (tighter) source rectangle.
    assert re.search(
        rf'<div data-qid="cx-title-geo"[^>]*class="cx-geo" style="{re.escape(title_box)};z-index:2"', html
    )
    # Side bands fill the canvas outside the page and are painted with the soft accent.
    assert f'class="cx-side-band" style="left:0px;top:0px;width:{px(x0)};height:720px"' in html
    right_band = f'style="left:{px(x0 + page_w)};top:0px;width:{px(1280 - x0 - page_w)};height:720px"'
    assert f'class="cx-side-band" {right_band}' in html
    assert f".cx-side-band{{position:absolute;background:{TOKENS.accent_soft};}}" in html
    by_binding = bound(html)
    assert font_size(by_binding["b1"][1]) == px(32 * PX_PER_PT * 0.95)
    assert font_size(by_binding["b2"][1]) == px(14 * PX_PER_PT * 0.95 * 0.9)  # per-box text scale
    assert font_size(by_binding["b4:run:0"][1]) == px(12 * PX_PER_PT * 0.95)


def _decoration_box(attrs: dict[str, str]) -> tuple[float, float, float, float]:
    """``(x0, y0, x1, y1)`` computed from a rendered element's own inline style."""

    style = attrs["style"]
    left = float(re.search(r"left:([\d.]+)px", style).group(1))
    top = float(re.search(r"top:([\d.]+)px", style).group(1))
    width = float(re.search(r"width:([\d.]+)px", style).group(1))
    height = float(re.search(r"height:([\d.]+)px", style).group(1))
    return (left, top, left + width, top + height)


def _boxes_intersect(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def test_source_geometry_side_bands_never_intersect_a_title_flush_with_the_page_edge() -> None:
    """A title's source rectangle can start exactly at the page's own leading
    (left) edge. The side band painted in the margin outside the scaled page
    must stop exactly there too: the band's right edge and the title's left
    edge are the same computed coordinate, never overlapping."""

    plan = text_plan()
    plan.title.source_rect = rect(0.0, 0.05, 0.5, 0.12)
    deck = deck_for(plan)
    params = FitParameters(rung=5, chrome_level=3, source_geometry=True, page_scale=0.9)
    html = render_slide(plan, TOKENS, params, deck)

    title_box = next(
        _decoration_box(attrs) for _tag, attrs, _text in elements(html) if attrs.get("data-qid") == "cx-title-geo"
    )
    for _tag, attrs, _text in elements(html):
        if attrs.get("data-qid") == "cx-title-geo" or attrs.get("class") not in {"cx-side-band", "cx-geo"}:
            continue
        assert not _boxes_intersect(title_box, _decoration_box(attrs)), attrs


def test_source_geometry_title_outranks_an_overlapping_neighbor_in_paint_order() -> None:
    """Reproduces the measured defect (base43 slides 37/42): a body block's own
    source rectangle can sit close enough to the title's that its real
    (browser-measured) rendered text spills past its declared box into the
    title's. The title's geo wrapper must carry an explicit z-index so it is
    always the topmost element at any point it covers -- the only thing the
    browser's ``elementsFromPoint``-based occlusion probe (``utils.
    slide_capture``) checks when it decides whether something occludes the
    title. A plain neighbour carries no such z-index."""

    plan = text_plan()
    plan.components[0].source_rect = plan.title.source_rect  # deliberately overlapping
    deck = deck_for(plan)
    html = render_slide(plan, TOKENS, FitParameters(rung=5, chrome_level=3, source_geometry=True), deck)
    assert_gates(html, TEXT_BLOCKS)

    by_qid = {attrs["data-qid"]: attrs for _tag, attrs, _text in elements(html) if "data-qid" in attrs}
    assert "z-index:2" in by_qid["cx-title-geo"]["style"]
    neighbor_qid = f"cx-{plan.components[0].component_id}-geo"
    assert "z-index" not in by_qid[neighbor_qid]["style"]


def test_source_geometry_grows_a_too_short_kicker_box_instead_of_overlapping() -> None:
    """Reproduces the measured defect (``composer-offline-eval`` final2-43
    slides 12-15/32): a kicker paragraph's captured source box is shorter
    than the text it holds at the source's own font size, so the real
    (unclipped) rendered text spills into the next component's box below it
    -- an ``overlap`` blocking issue that survived every rung 5 step because
    neither ``box_text_scale`` nor ``page_scale`` changes a box's declared
    position. The renderer must grow the box straight down, capped at the
    next box's own top, before ever shrinking the text."""

    long_text = "절감 동인 다각화를 통한 원가 구조 개선과 품질 안정화를 함께 달성하는 전략입니다. " * 3
    kicker = ComposerComponent(
        component_id="kicker", kind="paragraph", semantic_role="body",
        paragraphs=[para("k1", long_text)],
        source_rect=rect(0.05, 0.10, 0.5, 0.05), source_size_pt=14,
    )
    below = ComposerComponent(
        component_id="below", kind="paragraph", semantic_role="body",
        paragraphs=[para("n1", "다음 문단입니다.")],
        source_rect=rect(0.05, 0.40, 0.5, 0.1),
    )
    plan = ComposerSlidePlan(
        slide_index=0, source_unit_id="u9", archetype="text", title=_title(), components=[kicker, below],
    )
    deck = deck_for(plan)
    params = FitParameters(rung=5, chrome_level=3, source_geometry=True)
    html = render_slide(plan, TOKENS, params, deck)
    assert_gates(html, [("b1", "분기 실적 요약"), ("k1", long_text), ("n1", "다음 문단입니다.")])

    page = page_box(deck, params)
    original_height = rect_box(kicker.source_rect, page)[3]
    boxes = {
        attrs["data-qid"]: _decoration_box(attrs)
        for _tag, attrs, _text in elements(html)
        if attrs.get("data-qid") in {"cx-kicker-geo", "cx-below-geo"}
    }
    kicker_box, below_box = boxes["cx-kicker-geo"], boxes["cx-below-geo"]
    kicker_top, kicker_bottom = kicker_box[1], kicker_box[3]
    below_top = below_box[1]
    # Grown well past its own (too short) declared source rectangle...
    assert kicker_bottom - kicker_top > original_height
    # ...but never past the next box's own top: no overlap is left behind.
    assert kicker_bottom <= below_top + 1e-6


def test_source_geometry_requires_a_rectangle_for_every_component() -> None:
    plan = text_plan()
    plan.components[0].source_rect = None
    with pytest.raises(ComposerRenderError) as error:
        render_slide(plan, TOKENS, FitParameters(rung=5, source_geometry=True), deck_for(plan))
    assert error.value.code == "composer_render_plan_invalid"


def test_duplicate_binding_is_a_renderer_error() -> None:
    plan = text_plan()
    plan.components[0].paragraphs.append(para("b2", "첫 번째 문단입니다. 매출은 전년 대비 12% 성장했습니다."))
    with pytest.raises(ComposerRenderError):
        render_slide(plan, TOKENS, FitParameters(), deck_for(plan))


@pytest.mark.parametrize("params", ALL_PARAMS[:4], ids=lambda p: f"rung{p.rung}")
def test_comparison_plan(params: FitParameters) -> None:
    plan = comparison_plan()
    html = render_slide(plan, TOKENS, params, deck_for(plan))
    assert_gates(html, COMPARISON_BLOCKS)
    assert 'class="cx-component cx-comparison" style="grid-template-columns:repeat(2,minmax(0,1fr))"' in html
    by_binding = bound(html)
    assert sorted(by_binding) == ["b1", "c1:run:0", "c1:run:1", "c2", "c3", "h1", "h2"]
    assert "cx-compare-head" in by_binding["h1"][1]["class"]
    assert by_binding["c1:run:0"][2] == "수작업 검수"
    order = [html.index(f'data-source-block-id="{binding}"') for binding in ("h1", "c1:run:0", "h2")]
    assert order == sorted(order)


@pytest.mark.parametrize("steps,per_row,arrows", [(3, 3, 2), (5, 3, 3), (6, 3, 4)])
def test_process_plan(steps: int, per_row: int, arrows: int) -> None:
    plan = process_plan(steps)
    html = render_slide(plan, TOKENS, FitParameters(), deck_for(plan))
    assert_gates(html, process_blocks(steps))
    assert f'class="cx-component cx-process" style="grid-template-columns:repeat({per_row},minmax(0,1fr))"' in html
    assert html.count('class="cx-arrow"') == arrows
    arrows_found = [attrs for _t, attrs, _x in elements(html) if attrs.get("class") == "cx-arrow"]
    assert all(attrs.get("aria-hidden") == "true" for attrs in arrows_found)
    assert ".cx-arrow{position:absolute;top:50%;margin-top:-8px;" in html
    by_binding = bound(html)
    assert len(by_binding) == 1 + 2 * steps
    assert "cx-step-head" in by_binding["s0h"][1]["class"]


def test_table_plan_binds_the_table_element_only() -> None:
    plan = table_plan()
    html = render_slide(plan, TOKENS, FitParameters(), deck_for(plan))
    admit_authored_html(admission_projection(html))
    validate_declared_authored_qids(html)
    assert stamp_source_block_ids(html, [("b1", "분기 실적 요약")]) == html
    table = re.search(r"<table[^>]*>", html).group(0)
    assert 'data-native="table"' in table
    assert f'data-qid="{deterministic_source_qid("t1")}"' in table
    assert 'data-source-block-id="t1"' in table
    inside = html[html.index("<table"):html.index("</table>")]
    assert inside.count("data-qid=") == 1
    assert inside.count('data-qid-ignore=""') == 1 + 1 + 3 + 9  # thead, tbody, rows, cells
    header_row = "".join(f'<th data-qid-ignore="">{text}</th>' for text in ("지표", "Before", "After"))
    assert header_row in html
    assert "<td data-qid-ignore=\"\">6h</td>" in html
    assert "<img" not in inside and "<svg" not in inside


def test_table_from_several_blocks_binds_each_cell() -> None:
    plan = table_plan()
    rows = [["항목", "값"], ["매출", "120"]]
    cells = [(f"cell{r}{c}", text) for r, row in enumerate(rows) for c, text in enumerate(row)]
    plan.components[0].table_rows = [
        [para(f"cell{r}{c}", text) for c, text in enumerate(row)] for r, row in enumerate(rows)
    ]
    html = render_slide(plan, TOKENS, FitParameters(), deck_for(plan))
    blocks = [("b1", "분기 실적 요약"), *cells]
    assert_gates(html, blocks)
    by_binding = bound(html)
    assert by_binding["cell11"][0] == "td" and by_binding["cell11"][2] == "120"
    assert by_binding["cell00"][0] == "th"


@pytest.mark.parametrize("count,with_text,columns", [(1, False, 1), (3, False, 2), (5, False, 3), (2, True, 1)])
def test_image_plans_place_asset_slots_without_src(count: int, with_text: bool, columns: int) -> None:
    plan = image_plan(count, with_text)
    html = render_slide(plan, TOKENS, FitParameters(), deck_for(plan))
    blocks = [("b1", "분기 실적 요약")] + ([("k1", "그림 1. 매출 추이")] if with_text else [])
    assert_gates(html, blocks)
    images = re.findall(r"<img[^>]*>", html)
    assert len(images) == count
    for index, tag in enumerate(images):
        assert f'data-asset-id="asset-{index}"' in tag
        assert 'data-semantic-role="image"' in tag
        assert "data-qid=" in tag
        assert "src=" not in tag
    assert ".cx-image{width:100%;height:100%;object-fit:contain;display:block;}" in html
    assert f'class="cx-images" style="grid-template-columns:repeat({columns},minmax(0,1fr))"' in html
    if with_text:
        assert 'class="cx-content" style="grid-template-columns:minmax(0,7fr) minmax(0,5fr)"' in html
    # The admission projection is exactly the marker-free document the pipeline admits.
    assert "data-asset-id" not in admission_projection(html)
    assert admission_projection(html).count("<img") == count


def test_image_without_asset_id_is_rejected() -> None:
    plan = image_plan(1)
    plan.images[0].asset_id = None
    with pytest.raises(ComposerRenderError):
        render_slide(plan, TOKENS, FitParameters(), deck_for(plan))


def test_source_marker_is_peeled_and_redrawn_while_numbered_lists_count() -> None:
    plan = text_plan()
    plan.components[1] = ComposerComponent(
        component_id="steps", kind="numbered", semantic_role="body",
        paragraphs=[para("n1", "① 준비", 0), para("n1", "② 실행", 1), para("n1", "마무리", 2)],
    )
    blocks = [block for block in TEXT_BLOCKS if block[0] != "b4"] + [("n1", "① 준비\n② 실행\n마무리")]
    html = render_slide(plan, TOKENS, FitParameters(), deck_for(plan))
    assert_gates(html, blocks)
    markers = [text for _t, attrs, text in elements(html) if "cx-marker" in attrs.get("class", "")]
    assert markers == ["①", "②", "3."]
    by_binding = bound(html)
    assert [by_binding[f"n1:run:{i}"][2] for i in range(3)] == ["준비", "실행", "마무리"]


def test_balanced_partition_is_contiguous_and_balanced() -> None:
    assert balanced_partition([10, 10, 10, 10], 2) == [(0, 2), (2, 4)]
    assert balanced_partition([30, 5, 5, 5], 2) == [(0, 1), (1, 4)]
    assert balanced_partition([1, 1], 3) == [(0, 1), (1, 2)]
    assert balanced_partition([], 2) == [(0, 0)]


# --- Wide components (comparison/process/table) never share a balanced column ---


def wide_flow_plan() -> ComposerSlidePlan:
    """``[paragraph, comparison(3 cols x 9 rows), paragraph]``: the defect shape
    measured in ``artifacts/composer-offline-eval/r7-43`` slides 6 and 9 -- a
    large comparison landing alone in one balanced column while a short
    paragraph sits in the other squeezes it to half the content width, wraps
    it far taller than it needs, and overflows the canvas. It must instead
    render as its own full-width row, in source order between the flow text."""

    lead = ComposerComponent(
        component_id="lead", kind="paragraph", semantic_role="body",
        paragraphs=[para("lead0", "도입부 문단 하나."), para("lead1", "도입부 문단 둘.")],
    )
    comparison = ComposerComponent(
        component_id="cmp", kind="comparison", semantic_role="body",
        group_headers=[para(f"h{i}", f"제목 {i}") for i in range(3)],
        groups=[[para(f"g{i}-{j}", f"항목 {i}-{j}") for j in range(9)] for i in range(3)],
    )
    tail = ComposerComponent(
        component_id="tail", kind="paragraph", semantic_role="body",
        paragraphs=[para("tail0", "마무리 문단.")],
    )
    return ComposerSlidePlan(
        slide_index=0, source_unit_id="u8", archetype="comparison", title=_title(),
        components=[lead, comparison, tail],
    )


def test_wide_component_renders_as_a_full_width_row_not_a_balanced_column() -> None:
    plan = wide_flow_plan()
    html = render_slide(plan, TOKENS, FitParameters(columns=2), deck_for(plan))
    assert_gates(html, [
        ("lead0", "도입부 문단 하나."), ("lead1", "도입부 문단 둘."),
        *[(f"h{i}", f"제목 {i}") for i in range(3)],
        *[(f"g{i}-{j}", f"항목 {i}-{j}") for i in range(3) for j in range(9)],
        ("tail0", "마무리 문단."), ("b1", "분기 실적 요약"),
    ])
    style = html[html.index("<style>") : html.index("</style>")]
    content_rule = style[style.index(".cx-content{") : style.index(".cx-content{") + 200]
    assert "align-items:start" in content_rule
    body = html[html.index("<body>") :]
    # The comparison is a direct child of a full-width ``cx-row``, never nested
    # inside a ``cx-col`` (a balanced column) the way a flow unit would be.
    assert (
        'class="cx-row" style="grid-column:1 / -1"><div data-qid="cx-cmp" '
        'data-semantic-role="decoration" class="cx-component cx-comparison"'
    ) in body
    # The two lead paragraphs, on the other hand, share one balanced 2-column
    # flow grid (``params.columns``), each in its own ``cx-col``.
    assert 'class="cx-region" style="grid-template-columns:repeat(2,minmax(0,1fr))"' in body
    assert body.count('class="cx-col"') == 3  # 2 for the lead run, 1 for the tail run


def test_wide_component_and_flow_runs_preserve_source_order() -> None:
    plan = wide_flow_plan()
    html = render_slide(plan, TOKENS, FitParameters(columns=2), deck_for(plan))
    positions = [
        html.index(f'data-source-block-id="{binding}"')
        for binding in ("lead0", "lead1", "h0", "g0-0", "g2-8", "tail0")
    ]
    assert positions == sorted(positions)


def wide_flow_with_images_plan() -> ComposerSlidePlan:
    """``wide_flow_plan`` (two paragraphs, a wide comparison, one paragraph)
    plus three images: the shape measured on ``composer-offline-eval``
    final3-43 slides 6/17/34, where the image row's height came from a
    Python estimate of the text above it and pushed images off-canvas or
    under the text at every template rung. The image row must instead be a
    flex-grow child sized by the browser, never a fixed pixel height."""

    plan = wide_flow_plan()
    plan.images = [
        ComposerComponent(
            component_id=f"img{i}", kind="image", semantic_role="image", asset_id=f"asset-{i}",
            occurrence_id=f"occ-{i}", aspect_ratio=1.5, source_rect=rect(0.1, 0.2, 0.8, 0.6),
        )
        for i in range(3)
    ]
    return plan


@pytest.mark.parametrize(
    "params,floor",
    [(FitParameters(chrome_level=0), 90), (FitParameters(chrome_level=3, density=1), 72)],
    ids=["chrome0-density0-floor90", "chrome3-density1-floor72"],
)
def test_wide_component_with_images_sizes_the_row_from_layout_not_an_estimate(
    params: FitParameters, floor: int
) -> None:
    plan = wide_flow_with_images_plan()
    html = render_slide(plan, TOKENS, params, deck_for(plan))
    style = html[html.index("<style>") : html.index("</style>")]
    body = html[html.index("<body>") : html.index("</body>")]
    # The content area is a flex column, so the browser -- not a Python
    # estimate of the text's height -- decides how much room the image row
    # actually gets.
    assert 'class="cx-content" style="display:flex;flex-direction:column;min-height:0"' in body
    # Every text segment (the balanced-column flow region, and the wide
    # comparison's own full-width row) keeps its natural height instead of
    # sharing the flex-grow the image row gets.
    assert ".cx-region{display:grid;" in style and "min-width:0;flex:0 0 auto;}" in style
    assert ".cx-row{min-width:0;flex:0 0 auto;}" in style
    # The image row is the sole flexible child: a floor it never shrinks
    # below, `overflow:hidden` as the backstop, and no fixed pixel height.
    images_tag = body[body.index('class="cx-images"') : body.index(">", body.index('class="cx-images"'))]
    assert f"flex:1 1 {floor}px;min-height:{floor}px;overflow:hidden;" in images_tag
    assert "height:" not in images_tag.split("flex:1 1")[0]
    # Every image still renders visibly, addressed for substitution, and
    # capped -- never forced -- to its cell so it can shrink but never leave
    # the canvas or paint over the text above it.
    assert ".cx-image-flex{max-width:100%;max-height:100%;object-fit:contain;display:block;}" in style
    figures = re.findall(r'<figure[^>]*>', body)
    assert len(figures) == 3
    assert all("style=" not in figure for figure in figures)  # no Python-computed px height on the box
    images = re.findall(r"<img[^>]*>", body)
    assert len(images) == 3
    for index, tag in enumerate(images):
        assert 'class="cx-image-flex"' in tag
        assert f'data-asset-id="asset-{index}"' in tag
        assert 'data-semantic-role="image"' in tag


# --- Title fit: independent of the body type scale, wraps rather than clips -----


def long_title_plan() -> ComposerSlidePlan:
    """The measured slide-1 defect: a three-run title that overflows a 40px band."""

    title = ComposerComponent(
        component_id="title", kind="title", semantic_role="title",
        paragraphs=[
            para("t1", "숏폼대행"),
            para("t2", "1인사업자 홀로서기"),
            para("t3", "4주 · 총 8시간 · 온라인 실전 창업 과정"),
        ],
    )
    return ComposerSlidePlan(
        slide_index=0, source_unit_id="u6", archetype="text", title=title, furniture=_furniture(), text_chars=40
    )


LONG_TITLE_BLOCKS = [
    ("t1", "숏폼대행"),
    ("t2", "1인사업자 홀로서기"),
    ("t3", "4주 · 총 8시간 · 온라인 실전 창업 과정"),
    ("b5", "Confidential · ACME"),
    ("b6", "3 / 12"),
]


def test_long_multi_run_title_grows_the_band_instead_of_clipping() -> None:
    plan = long_title_plan()
    deck = deck_for(plan)
    html = render_slide(plan, TOKENS, FitParameters(chrome_level=0), deck)
    assert_gates(html, LONG_TITLE_BLOCKS)
    style = html[html.index("<style>") : html.index("</style>")]
    # The band has a floor, not a fixed height, and the content area is a flex child.
    assert ".cx-title-band{flex:0 0 auto;width:1280px;min-height:120px;" in style
    assert ".cx-title-band{position:absolute" not in style
    assert ".cx-content{flex:1 1 auto;min-height:0;" in style
    sizes = {font_size(bound(html)[binding][1]) for binding in ("t1", "t2", "t3")}
    assert len(sizes) == 1, "every run of one title stacks at the same fitted size"
    chosen = float(next(iter(sizes)).replace("px", ""))
    assert 28.0 <= chosen < 40.0


def test_deck_title_px_is_honored_else_template_size_is_used() -> None:
    plan = text_plan()
    custom = ComposerDeckPlan(slides=[plan], tokens=TOKENS, title_px=32.0)
    html = render_slide(plan, TOKENS, FitParameters(), custom)
    assert font_size(bound(html)["b1"][1]) == "32px"

    default = ComposerDeckPlan(slides=[plan], tokens=TOKENS)
    html = render_slide(plan, TOKENS, FitParameters(), default)
    assert font_size(bound(html)["b1"][1]) == px(TEMPLATE_TYPE_SCALE_PX["title"])


def test_rung2_type_scale_does_not_change_title_size_at_chrome0() -> None:
    plan = text_plan()
    deck = deck_for(plan)
    baseline = render_slide(plan, TOKENS, FitParameters(chrome_level=0), deck)
    rung2 = render_slide(plan, TOKENS, FitParameters(rung=2, chrome_level=0, type_scale=0.8), deck)
    template_title = px(TEMPLATE_TYPE_SCALE_PX["title"])
    assert font_size(bound(baseline)["b1"][1]) == template_title
    assert font_size(bound(rung2)["b1"][1]) == template_title
    # The body IS subject to type_scale, so it shrinks as expected.
    assert font_size(bound(rung2)["b2"][1]) == px(TEMPLATE_TYPE_SCALE_PX["body"] * 0.8)


# --- The cover archetype ---------------------------------------------------------


def cover_plan() -> ComposerSlidePlan:
    title = ComposerComponent(
        component_id="title", kind="title", semantic_role="title",
        paragraphs=[para("ct1", "새로운 시작"), para("ct2", "2026년 사업계획")],
    )
    subtitle = ComposerComponent(
        component_id="subtitle", kind="subtitle", semantic_role="subtitle",
        paragraphs=[para("cs1", "함께 성장하는 파트너")],
    )
    return ComposerSlidePlan(
        slide_index=0, source_unit_id="u7", archetype="cover",
        title=title, subtitle=subtitle, furniture=_furniture(), text_chars=20,
    )


COVER_BLOCKS = [
    ("ct1", "새로운 시작"),
    ("ct2", "2026년 사업계획"),
    ("cs1", "함께 성장하는 파트너"),
    ("b5", "Confidential · ACME"),
    ("b6", "3 / 12"),
]


def test_cover_archetype_renders_a_centered_stack_with_no_title_band() -> None:
    plan = cover_plan()
    html = render_slide(plan, TOKENS, FitParameters(), deck_for(plan))
    assert_gates(html, COVER_BLOCKS)
    body = html[html.index("<body>") :]
    assert "cx-title-band" not in body
    assert 'class="cx-cover"' in body
    assert 'class="cx-cover-stack"' in body
    assert 'class="cx-cover-rule"' in body
    # Furniture (footer, page number) still lives in the footer band.
    assert "cx-footer-band" in body
    by_binding = bound(html)
    assert sorted(by_binding) == sorted(binding for binding, _text in COVER_BLOCKS)
    assert by_binding["ct1"][0] == "h1"
    assert by_binding["cs1"][0] == "p"
    assert by_binding["ct1"][2] == "새로운 시작"


# --- Publication validator parity: every visible element declares itself --------


def _assert_every_element_is_declared(html: str) -> None:
    """Mirrors ``utils.authored_dom_quality.validate_declared_authored_qids``:
    every element needs ``data-qid``, ``data-qid-ignore``, or ``aria-hidden``."""

    document = lxml_html.document_fromstring(html)
    skip = {"html", "head", "meta", "style", "body", "br"}
    for element in document.iter():
        tag = element.tag
        if not isinstance(tag, str) or tag in skip:
            continue
        attrs = element.attrib
        assert "data-qid" in attrs or "data-qid-ignore" in attrs or attrs.get("aria-hidden") == "true", (
            f"<{tag}> {dict(attrs)} declares neither data-qid nor an ignore/aria-hidden exemption"
        )


@pytest.mark.parametrize(
    "plan",
    [text_plan(), comparison_plan(), process_plan(), table_plan(), image_plan(3), cover_plan(), long_title_plan()],
    ids=["text", "comparison", "process", "table", "image", "cover", "long_title"],
)
def test_every_visible_element_is_qid_declared_or_explicitly_exempt(plan: ComposerSlidePlan) -> None:
    html = render_slide(plan, TOKENS, FitParameters(), deck_for(plan))
    validate_declared_authored_qids(html)
    _assert_every_element_is_declared(html)


# --- grow step: larger body type, centred content ------------------------------


def _body_font_px(html: str, qid: str) -> float:
    for _tag, attrs, _text in elements(html):
        if attrs.get("data-qid") == qid:
            match = re.search(r"font-size:([\d.]+)px", attrs.get("style", ""))
            assert match, f"{qid} carries no font-size"
            return float(match.group(1))
    raise AssertionError(f"{qid} not rendered")


def test_grown_render_enlarges_body_but_not_title_or_furniture():
    plan = text_plan()
    deck = deck_for(plan)
    plain = render_slide(plan, TOKENS, FitParameters(rung=0, columns=1), deck)
    grown = render_slide(plan, TOKENS, FitParameters(rung=0, columns=1, type_grow=1.25, content_centered=True), deck)

    body_qid = deterministic_source_qid("b2")
    assert _body_font_px(grown, body_qid) == pytest.approx(_body_font_px(plain, body_qid) * 1.25)
    for chrome in ("b1", "b5", "b6"):
        qid = deterministic_source_qid(chrome)
        assert _body_font_px(grown, qid) == pytest.approx(_body_font_px(plain, qid))


def test_grown_render_centers_the_content_section():
    plan = text_plan()
    deck = deck_for(plan)
    plain = render_slide(plan, TOKENS, FitParameters(rung=0, columns=1), deck)
    grown = render_slide(plan, TOKENS, FitParameters(rung=0, columns=1, type_grow=1.1, content_centered=True), deck)
    assert 'class="cx-content" style="align-content:center"' in grown
    assert "align-content:center" not in plain

    # a wide row beside images keeps the flex column: its image row already
    # absorbs whatever the text leaves, so there is no unused area to centre in.
    mixed = image_plan(count=2)
    mixed = mixed.model_copy(update={"components": [comparison_plan().components[0]]})
    flex = render_slide(mixed, TOKENS, FitParameters(rung=0, columns=1, type_grow=1.1, content_centered=True),
                        deck_for(mixed))
    assert 'class="cx-content" style="display:flex;flex-direction:column;min-height:0"' in flex


def test_grow_never_reaches_the_source_relative_rungs():
    plan = text_plan()
    deck = deck_for(plan)
    params = FitParameters(rung=4, source_relative=True, source_factor=1.0, type_scale=0.75)
    assert render_slide(plan, TOKENS, params, deck) == render_slide(
        plan, TOKENS, params.model_copy(update={"type_grow": 1.3}), deck
    )
