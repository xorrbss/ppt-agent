"""The template fallback body keeps every outline line inside the frame's body region."""

import xml.etree.ElementTree as ET

from services.generation_core.drivers.svg.fallback import template_fallback_body_svg
from services.svg_contract import SVG_NS

REGION = {"x": 48.0, "y": 80.0, "width": 1180.0, "height": 540.0}
FACE = {"family": "Pretendard", "weight": 400}
BOLD = {"family": "Pretendard", "weight": 700}
CONTENT = "# 실행 일정\n- 1분기: 계획 확정과 예산 배정\n- 2분기: 핵심 과제 실행\n- 3분기: 중간 점검과 보완"


def _texts(svg):
    return [node for node in ET.fromstring(svg).iter(f"{{{SVG_NS}}}text")]


def _body(**kwargs):
    return template_fallback_body_svg(content=CONTENT, width=1280, height=720, body_region=REGION,
                                      title_face=BOLD, body_face=FACE, style_snapshot={"tokens": {}}, **kwargs)


def test_every_line_is_kept_inside_the_region_with_body_ids():
    svg = _body(title_in_frame=False)
    texts = _texts(svg)
    shown = " ".join(node.text for node in texts)
    for line in ("실행 일정", "1분기: 계획 확정과 예산 배정", "2분기: 핵심 과제 실행", "3분기: 중간 점검과 보완"):
        assert line in shown
    root = ET.fromstring(svg)
    assert not [node for node in root if node.tag.endswith("}rect")]  # no full-canvas background
    for node in root.iter():
        if node is root:
            continue
        assert node.get("id", "body-").startswith("body-")
    for node in texts:
        x, y = float(node.get("x")), float(node.get("y"))
        assert REGION["x"] <= x <= REGION["x"] + REGION["width"]
        assert REGION["y"] <= y <= REGION["y"] + REGION["height"]
        assert node.get("font-family") == "Pretendard"
    assert texts[0].get("font-weight") == "700"


def test_a_title_the_frame_shows_is_not_repeated_in_the_body():
    shown = " ".join(node.text for node in _texts(_body(title_in_frame=True)))
    assert "실행 일정" not in shown and "1분기: 계획 확정과 예산 배정" in shown
