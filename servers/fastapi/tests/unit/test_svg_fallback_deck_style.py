"""The server-built fallback page follows the deck's own style and stays readable.

Fictional content only. Covers plain svg decks (style colours and fonts, markdown
tables as a grid), template pages (cover shape, a panel over a picture) and the
contrast rule shared by both.
"""

import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from services.generation_core.drivers.svg.fallback import (
    _contrast, _ink_on, fallback_page_svg, template_fallback_body_svg,
)
from services.generation_core.drivers.svg.terminal import _picture_under_body
from services.svg_contract import SVG_NS

TABLE = ("# 위험 관리\n- 위험은 세 가지이다.\n\n| 위험 | 가능성 | 대응 |\n| --- | --- | --- |\n"
         "| 자료 품질 | 높음 | 차수별 검증 |\n| 일정 충돌 | 중간 | 시험 제외 |\n| 연계 지연 | 낮음 | 담당자 지정 |")
CELLS = ["위험", "가능성", "대응", "자료 품질", "높음", "차수별 검증", "일정 충돌", "중간", "시험 제외",
         "연계 지연", "낮음", "담당자 지정"]
DARK = {"tokens": {"background": "#0B1B3A", "primary": "#5B8DEF", "palette": ["#0B1B3A", "#5B8DEF", "#F5F7FA"],
                   "body_font": "Noto Sans KR", "heading_font": "Noto Serif KR"}}
LIGHT = {"tokens": {"background": "#FAF7F2", "primary": "#B5532A", "palette": ["#FAF7F2", "#B5532A", "#2B2B2B"],
                    "body_font": "Noto Sans KR"}}
# An accent too close to the page colour to carry text on its own.
MUDDY = {"tokens": {"background": "#2A2A2A", "primary": "#333333", "palette": ["#2A2A2A", "#333333"]}}
REGION = {"x": 60.0, "y": 180.0, "width": 1160.0, "height": 500.0}
FACE = {"family": "Pretendard", "weight": 400}
BOLD = {"family": "Pretendard", "weight": 700}


def _nodes(svg, tag):
    return [node for node in ET.fromstring(svg).iter(f"{{{SVG_NS}}}{tag}")]


def _plain(content, snapshot, role="content"):
    return fallback_page_svg(content=content, role=role, index=1, count=5, width=1280, height=720,
                             style_snapshot=snapshot)


def _text_vs_fill(svg, page="#FFFFFF"):
    """(text colour, colour of the last rect drawn before it that holds its point, else ``page``) for every text."""
    rects = []
    pairs = []
    for node in ET.fromstring(svg).iter():
        if node.tag == f"{{{SVG_NS}}}rect":
            rects.append(node)
        elif node.tag == f"{{{SVG_NS}}}text" and not node.get("id", "").startswith("svg-system"):
            x, y = float(node.get("x")), float(node.get("y"))
            held = [r for r in rects if float(r.get("x")) <= x <= float(r.get("x")) + float(r.get("width"))
                    and float(r.get("y")) <= y <= float(r.get("y")) + float(r.get("height"))]
            pairs.append((node.get("fill"), held[-1].get("fill") if held else page))
    return pairs


@pytest.mark.parametrize("snapshot", [DARK, LIGHT, MUDDY])
def test_plain_fallback_uses_the_deck_colours_and_every_text_reads(snapshot):
    svg = _plain(TABLE, snapshot)
    pairs = _text_vs_fill(svg)
    assert pairs
    assert svg.count(snapshot["tokens"]["background"].upper()) >= 1
    for text, fill in pairs:
        assert _contrast(text, fill) >= 4.5, (text, fill)


def test_plain_fallback_uses_the_deck_fonts():
    svg = _plain(TABLE, DARK)
    assert {node.get("font-family") for node in _nodes(svg, "text")} <= {"Noto Sans KR", "Noto Serif KR"}
    assert "Noto Serif KR" in svg


def test_a_markdown_table_becomes_a_grid_with_every_cell_and_no_pipe_syntax():
    svg = _plain(TABLE, DARK)
    shown = [node.text for node in _nodes(svg, "text")]
    for cell in CELLS:
        assert cell in shown
    assert not [text for text in shown if "|" in text or "---" in text]
    assert "위험은 세 가지이다." in " ".join(shown)
    # Header row in the deck's accent, then one band per data row.
    fills = [rect.get("fill") for rect in _nodes(svg, "rect")]
    assert "#5B8DEF" in fills and len([f for f in fills if f not in ("#0B1B3A", "#5B8DEF")]) >= 3


def test_a_table_shrinks_to_hold_every_row_instead_of_dropping_any():
    rows = "\n".join(f"| 항목 {n} | 값 {n} | 설명 문장 {n} |" for n in range(60))
    svg = _plain("# 많은 행\n| 항목 | 값 | 설명 |\n| --- | --- | --- |\n" + rows, LIGHT)
    shown = [node.text for node in _nodes(svg, "text")]
    assert all(f"항목 {n}" in shown for n in range(60))


def test_lines_that_only_look_like_a_table_stay_plain_lines():
    shown = [node.text for node in _nodes(_plain("# 제목\n| 하나뿐인 줄 |", LIGHT), "text")]
    assert any("하나뿐인 줄" in text for text in shown)


def test_ink_prefers_the_given_colour_and_falls_back_to_the_stronger_one():
    assert _ink_on("#FFFFFF", "#111111") == "#111111"
    assert _ink_on("#101820", "#111111") == "#FFFFFF"
    assert _ink_on("#F2F2F2", "#EEEEEE") == "#0F172A"


def _template(content, *, role, photo, background, title_in_frame=False):
    return template_fallback_body_svg(content=content, width=1280, height=720, body_region=REGION,
        title_in_frame=title_in_frame, title_face=BOLD, body_face=FACE, style_snapshot={"tokens": {}},
        body_background=background, role=role, body_photo=photo)


DARK_PHOTO = {"mean_color": "#0A1E46", "tone": "dark", "text_color": "#FFFFFF"}
LIGHT_PHOTO = {"mean_color": "#E8E4DA", "tone": "light", "text_color": "#111111"}
COVER = "# 가상 제조사 현대화 제안서\n- 통합 업무 플랫폼 전환 사업\n- 2027년 3월부터 12개월"


def test_a_template_cover_keeps_the_cover_shape_inside_the_region_with_body_ids():
    svg = _template(COVER, role="cover", photo=False, background=DARK_PHOTO)
    texts = _nodes(svg, "text")
    assert texts[0].text == "가상 제조사 현대화 제안서" and float(texts[0].get("font-size")) >= 36
    assert float(texts[1].get("font-size")) < float(texts[0].get("font-size"))
    assert {node.get("font-family") for node in texts} == {"Pretendard"}
    assert texts[0].get("font-weight") == "700"
    for node in ET.fromstring(svg).iter():
        assert node.get("id", "body-").startswith("body-")
    for node in texts:
        assert REGION["x"] <= float(node.get("x")) <= REGION["x"] + REGION["width"]
        assert REGION["y"] <= float(node.get("y")) <= REGION["y"] + REGION["height"]
    assert not _nodes(svg, "rect")[0].get("fill-opacity")  # no panel without a picture


@pytest.mark.parametrize("background, panel, ink", [(DARK_PHOTO, "#000000", "#FFFFFF"),
                                                    (LIGHT_PHOTO, "#FFFFFF", "#0F172A")])
def test_text_over_a_picture_sits_on_a_panel_that_keeps_it_readable(background, panel, ink):
    for role, content in (("cover", COVER), ("content", TABLE)):
        svg = _template(content, role=role, photo=True, background=background)
        scrim = _nodes(svg, "rect")[0]
        assert scrim.get("id") == "body-fallback-scrim" and scrim.get("fill") == panel
        opacity = float(scrim.get("fill-opacity"))
        assert 0.5 <= opacity < 1
        # Worst case: the brightest (or darkest) picture pixel under the panel.
        worst = "#FFFFFF" if panel == "#000000" else "#000000"
        shown = tuple(round(int(worst[i:i + 2], 16) * (1 - opacity) + int(panel[i:i + 2], 16) * opacity)
                      for i in (1, 3, 5))
        assert _contrast(ink, "#%02X%02X%02X" % shown) >= 4.5
        assert {node.get("fill") for node in _nodes(svg, "text") if node.get("fill") == ink}
        x, y, w, h = (float(scrim.get(k)) for k in ("x", "y", "width", "height"))
        assert REGION["x"] <= x and x + w <= REGION["x"] + REGION["width"] + 1e-6
        assert REGION["y"] <= y and y + h <= REGION["y"] + REGION["height"] + 1e-6
        for node in _nodes(svg, "text"):
            assert x <= float(node.get("x")) <= x + w and y <= float(node.get("y")) <= y + h


def test_a_template_table_is_a_grid_inside_the_region_with_body_ids():
    svg = _template(TABLE, role="content", photo=False, background={"mean_color": "#FFFFFF", "tone": "light",
                                                                   "text_color": "#111111"}, title_in_frame=True)
    shown = [node.text for node in _nodes(svg, "text")]
    assert all(cell in shown for cell in CELLS) and not [t for t in shown if "|" in t]
    ids = [node.get("id") for node in ET.fromstring(svg).iter() if node.get("id")]
    assert len(ids) == len(set(ids)) and all(i.startswith("body-") for i in ids)
    for rect in _nodes(svg, "rect"):
        assert REGION["x"] <= float(rect.get("x"))
        assert float(rect.get("x")) + float(rect.get("width")) <= REGION["x"] + REGION["width"] + 1e-6
    for text, fill in _text_vs_fill(svg):
        assert _contrast(text, fill) >= 4.5


def _frame(*elements, region=(0, 100, 1000, 500)):
    box = SimpleNamespace
    return box(body_region=box(x=region[0], y=region[1], width=region[2], height=region[3]),
               elements=[box(layer=layer, kind=kind, bounds=box(x=x, y=y, width=w, height=h))
                         for layer, kind, x, y, w, h in elements])


def test_only_a_background_picture_over_the_body_counts_as_a_picture_under_it():
    assert _picture_under_body(_frame(("background", "image", 0, 0, 1000, 700)))
    assert not _picture_under_body(_frame(("background", "image", 0, 0, 1000, 90)))  # header art
    assert not _picture_under_body(_frame(("background", "rect", 0, 0, 1000, 700)))
    assert not _picture_under_body(_frame(("foreground", "image", 0, 0, 1000, 700)))


# --- Q2: fallback pages that look like the deck ---------------------------------

D2_TITLE = "2026년 국내 전기차 시장 분석: 판매 추이, 점유율, 가격대, 전망"
NAVY = {"tokens": {"background": "#051C2C", "primary": "#00A3E0", "body_font": "Pretendard",
                   "heading_font": "Pretendard", "palette": ["#051C2C", "#FFFFFF", "#2E3338", "#00A3E0", "#A7B0B7"]}}
DEFAULT_STYLE = {"tokens": {"background": "#FFFFFF", "primary": "#2563EB", "body_font": "Noto Sans KR",
                            "palette": ["#FFFFFF", "#111827", "#2563EB"]}}


def _page_texts(svg):
    return [node for node in ET.fromstring(svg).iter(f"{{{SVG_NS}}}text")
            if node.get("id") != "svg-system-page-number"]


@pytest.mark.parametrize("snapshot", [NAVY, DEFAULT_STYLE])
def test_d2_cover_title_wraps_at_a_space_without_an_ellipsis_and_is_drawn_once(snapshot):
    svg = _plain(f"# {D2_TITLE}\n- 2026년 11월\n- 시장조사팀", snapshot, role="cover")
    texts = _page_texts(svg)
    assert "…" not in "".join(node.text for node in texts)
    title_lines = [node.text for node in texts if float(node.get("font-size")) >= 36]
    assert len(title_lines) == 2 and " ".join(title_lines) == D2_TITLE
    assert sum(node.text.count("판매 추이") for node in texts) == 1
    background = snapshot["tokens"]["background"].upper()
    assert all(_contrast(node.get("fill"), background) >= 4.5 for node in texts)


@pytest.mark.parametrize("snapshot", [NAVY, DEFAULT_STYLE])
def test_a_long_content_title_wraps_and_is_neither_cut_nor_repeated(snapshot):
    title = f"{D2_TITLE} 그리고 두 번째 줄로 넘어가는 아주 긴 부제 문장"
    svg = _plain(f"# {title}\n- 본문 한 줄", snapshot)
    texts = _page_texts(svg)
    assert "…" not in "".join(node.text for node in texts)
    heading = [node.text for node in texts if node.get("font-weight") == "700"]
    assert " ".join(heading) == title
    assert sum(node.text.count("본문 한 줄") for node in texts) == 1


def test_a_page_does_not_draw_the_title_the_applied_header_already_shows(monkeypatch):
    import services.svg_common_header as header

    monkeypatch.setattr(header, "render_common_header", lambda *args, **kwargs: None)
    snapshot = {**DEFAULT_STYLE, "common_header": {"content_top_px": 120}}
    for role in ("content", "cover"):
        svg = fallback_page_svg(content="# 겹치지 않는 제목\n- 본문 한 줄", role=role, index=1, count=5, width=1280,
                                height=720, style_snapshot=snapshot, header_page={"applied": True})
        shown = " ".join(node.text for node in _page_texts(svg))
        assert "겹치지 않는 제목" not in shown and "본문 한 줄" in shown
    plain = _plain("# 겹치지 않는 제목\n- 본문 한 줄", DEFAULT_STYLE)
    assert "겹치지 않는 제목" in " ".join(node.text for node in _page_texts(plain))


def test_the_body_font_never_drops_below_the_readability_floor():
    lines = "\n".join(f"- 항목 {n} " + "내용 " * 25 for n in range(40))
    content = f"# 제목\n{lines}"
    free = _plain(content, NAVY)
    floored = fallback_page_svg(content=content, role="content", index=1, count=5, width=1280, height=720,
                                style_snapshot=NAVY, minimum_font_px=12)
    assert min(float(node.get("font-size")) for node in _page_texts(free)) < 12
    assert min(float(node.get("font-size")) for node in _page_texts(floored)) >= 12
    shown = "".join(node.text for node in _page_texts(floored)).replace(" ", "")
    assert all(f"항목{n}" in shown for n in range(40))


@pytest.mark.parametrize("snapshot", [NAVY, DEFAULT_STYLE])
def test_the_placeholder_takes_the_deck_colours_and_fonts(snapshot):
    from services.generation_core.drivers.svg.fallback import placeholder_page_svg

    svg = placeholder_page_svg(title=D2_TITLE, width=1280, height=720, font_family="Other", index=1, count=5,
                               style_snapshot=snapshot)
    background = snapshot["tokens"]["background"].upper()
    assert _nodes(svg, "rect")[0].get("fill") == background
    texts = _page_texts(svg)
    assert all(_contrast(node.get("fill"), background) >= 4.5 for node in texts)
    assert {node.get("font-family") for node in texts} == {
        snapshot["tokens"].get("heading_font") or snapshot["tokens"]["body_font"]}
    assert "…" not in svg and sum(node.text.count("판매 추이") for node in texts) == 1


def test_a_placeholder_without_a_style_keeps_its_plain_page():
    from services.generation_core.drivers.svg.fallback import placeholder_page_svg

    svg = placeholder_page_svg(title="제목", width=1280, height=720, font_family="Pretendard")
    assert _nodes(svg, "rect")[0].get("fill") == "#FFFFFF"
    assert {node.get("font-family") for node in _nodes(svg, "text")} == {"Pretendard"}


@pytest.mark.parametrize("background", ["#051C2C", "#FFFFFF", "#F4EDE0"])
def test_source_fallback_ink_and_table_cells_follow_the_page_background(background):
    from test_svg_source_layout import manifest_for
    from services.generation_core.drivers.svg.source_fallback import source_fallback_page
    from services.svg_source_layout import verify_source_receipt

    manifest = manifest_for([
        ("text", {"text": "분기 실적 요약"}),
        ("table", {"rows": [[{"text": "구분"}, {"text": "값"}], [{"text": "매출"}, {"text": "12%"}]],
                   "columnWidthsEmu": [300, 100]}),
    ])
    snapshot = {"digest": "sha256:style", "source_authoring_contract": {"version": "svg-source-whole-page/v1"},
                "source_template": {"tokens": {"background": background}}}
    svg, receipt = source_fallback_page(manifest, 0, snapshot, image_slots={}, width=1280, height=720,
                                        minimum_font_px=12)
    assert verify_source_receipt(svg, receipt, manifest, 0)
    pairs = _text_vs_fill(svg, page=background)
    assert pairs and len(pairs) >= 5
    for text, fill in pairs:
        assert _contrast(text, fill) >= 4.5, (text, fill)
