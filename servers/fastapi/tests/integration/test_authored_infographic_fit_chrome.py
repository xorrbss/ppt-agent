"""Real-Chrome regression: a six-node figure stays inside the box it was given.

The reproduction is the slide that terminated a deck: a six-node ``roadmap``
in the 1138x386 shell its own document reserved. Before the fit work the rows
kept their intrinsic height, needed 548px, and row six landed at y=667..744
with its phrase clipped by the bottom of the canvas.

The unit suite pins the CSS rules; this pins the rendered outcome, which is
the only thing the gate actually reads.
"""

import asyncio
from types import SimpleNamespace

import pytest

from models.authored_infographic_plan import AuthoredSlideInfographic, InfographicNode
from utils.authored_font_policy import enforce_authored_font_floor
from utils.authored_infographic import library_css, render_skeleton
from utils.slide_capture import find_chrome, render_html_to_png_with_metrics

#: The figure shell the failing slide gave its figure, on the 1280x720 canvas.
BOX_LEFT, BOX_TOP, BOX_WIDTH, BOX_HEIGHT = 71, 203, 1138, 386
CANVAS_WIDTH, CANVAS_HEIGHT = 1280, 720
#: Labels and phrases at the plan's own per-field budget.
NODES = (
    ("치명·중대 결함관리", "운영 진입 기준을 객관화한 절차", "8 단계"),
    ("전량 모의이관 검증", "최종 리허설로 전환을 검증한다", "99.9%"),
    ("사용자 인수시험", "이관 수행 결과를 전량 대조한다", "72시간 전"),
    ("사후대사 정합성", "시험 유형별 합격 기준을 고정", "4.6%"),
    ("운영 전환 리허설", "전환 판정 회의체가 승인한다", "2 회 이상"),
    ("단위·통합 시험", "잔여 결함은 회귀 시험으로 확인", "0 건"),
)


def _document(archetype: str, count: int) -> str:
    slide = AuthoredSlideInfographic(
        index=13,
        archetype=archetype,
        weight="standard",
        takeaway="운영 전환을 객관화",
        nodes=tuple(
            InfographicNode(label=label, desc=desc, figure=figure)
            for label, desc, figure in NODES[:count]
        ),
        emphasis=count - 2,
        density="high",
    )
    brand = SimpleNamespace(primary="#08916E", fonts="Noto Sans KR")
    html = (
        '<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><style>'
        "*{box-sizing:border-box}html,body{width:1280px;height:720px;margin:0;overflow:hidden}"
        'body{position:relative;background:#F9F9F9;font-family:"Noto Sans KR",sans-serif}'
        f".shell{{position:absolute;left:{BOX_LEFT}px;top:{BOX_TOP}px;"
        f"width:{BOX_WIDTH}px;height:{BOX_HEIGHT}px;background:#FFFFFF;border-radius:14px}}"
        f"</style><style>{library_css(brand)}</style></head><body>"
        f'<div class="shell">{render_skeleton(slide)}</div></body></html>'
    )
    # The generation pipeline clamps the whole document, this stylesheet
    # included, to the frozen slide capacity's 15px floor before the first
    # render. Rendering without it would measure a stylesheet that never ships.
    return enforce_authored_font_floor(html, minimum_px=15)


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
@pytest.mark.parametrize("archetype", ["roadmap", "layers", "checklist"])
def test_a_six_node_figure_stays_inside_a_1138x386_box(archetype: str) -> None:
    capture = asyncio.run(render_html_to_png_with_metrics(_document(archetype, 6), timeout=60))
    elements = [
        element
        for element in (capture.browser_metrics or {})["elements"]
        if str(element.get("qid") or "").startswith("ig.s14.")
    ]
    assert len(elements) > 6, "the figure did not render its nodes"

    for element in elements:
        qid, box = element["qid"], element["bbox"]
        if box["width"] <= 0 and box["height"] <= 0:
            continue
        assert not element.get("clipped"), f"{qid} is clipped"
        assert box["y"] >= 0 and box["y"] + box["height"] <= CANVAS_HEIGHT, (
            f"{qid} leaves the canvas: y={box['y']:.0f}..{box['y'] + box['height']:.0f}"
        )
        assert box["x"] >= 0 and box["x"] + box["width"] <= CANVAS_WIDTH, f"{qid} leaves the canvas"
        if qid.endswith(".root"):
            continue
        assert box["y"] >= BOX_TOP - 0.5, f"{qid} leaves its box at the top"
        assert box["y"] + box["height"] <= BOX_TOP + BOX_HEIGHT + 0.5, (
            f"{qid} leaves its box at the bottom by "
            f"{box['y'] + box['height'] - BOX_TOP - BOX_HEIGHT:.0f}px"
        )
        assert box["x"] >= BOX_LEFT - 0.5, f"{qid} leaves its box on the left"
        assert box["x"] + box["width"] <= BOX_LEFT + BOX_WIDTH + 0.5, (
            f"{qid} leaves its box on the right"
        )
