"""The pure helpers of the offline fit sweep.

``scripts/authored_infographic_fit_sweep.py`` needs Chrome to render, but the
parts that decide what is measured and what counts as a miss do not. Those are
what is checked here, so a wrong reading is caught before a few hundred
renders are spent on it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import authored_infographic_fit_sweep as sweep  # noqa: E402


def _bbox(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {"x": x, "y": y, "width": width, "height": height}


def test_a_box_is_centred_on_the_canvas_and_ends_on_the_bottom_content_line():
    # Every box is anchored on the line the figure contract now tells the model
    # to reach, so the sweep measures the geometry generation actually asks for.
    box = sweep.Box("wide_short", 1138, 386)
    assert (box.left, box.top) == (71, sweep.BOTTOM_CONTENT_LINE - 386)
    assert box.left + box.width <= sweep.CANVAS_W
    assert box.top + box.height == sweep.BOTTOM_CONTENT_LINE
    assert box.top + box.height <= sweep.CANVAS_H


def test_an_element_inside_its_rectangle_overshoots_nothing():
    assert sweep.overshoot(_bbox(20, 20, 40, 40), (10, 10, 100, 100)).worst == 0


def test_each_side_is_measured_separately():
    out = sweep.overshoot(_bbox(0, 0, 200, 90), (10, 10, 100, 50))
    assert (out.left, out.top, out.right, out.bottom) == (10, 10, 90, 30)
    assert out.worst == 90
    assert out.as_text() == "left+10, top+10, right+90, bottom+30"


def test_a_subpixel_miss_is_not_a_miss():
    assert sweep.overshoot(_bbox(9.7, 10, 100, 50), (10, 10, 100, 50)).worst == 0


def test_only_the_figures_own_elements_are_measured():
    elements = [
        {"qid": "ig.s14.n1", "bbox": _bbox(0, 0, 20, 20)},
        {"qid": "s14.hero.claim", "bbox": _bbox(0, 0, 2000, 2000)},
    ]
    finding = sweep.inspect(elements, "ig.s14.", sweep.Box("b", 100, 100))
    assert finding.box_qid == "ig.s14.n1"
    assert finding.canvas_overflow == 0


def test_the_root_is_held_to_the_canvas_but_not_to_its_own_box():
    box = sweep.Box("b", 100, 100)
    root = {"qid": "ig.s14.root", "bbox": _bbox(box.left, box.top, 400, 400)}
    finding = sweep.inspect([root], "ig.s14.", box)
    assert finding.box_overflow == 0
    assert finding.canvas_overflow > 0


def test_a_clipped_element_fails_even_when_its_box_is_inside():
    elements = [{"qid": "ig.s14.n6.desc", "bbox": _bbox(600, 300, 10, 10), "clipped": True}]
    finding = sweep.inspect(elements, "ig.s14.", sweep.Box("b", 1138, 386))
    assert finding.failed
    assert finding.clipped == ["ig.s14.n6.desc"]


@pytest.mark.parametrize("count", [3, 4, 5, 6])
def test_a_swept_slide_carries_the_planned_node_count_and_a_figure(count):
    slide = sweep.build_slide("roadmap", count, "high", "standard")
    assert len(slide.nodes) == count
    assert slide.has_figure
    assert all(node.label and node.desc and node.figure for node in slide.nodes)


def test_the_matrix_crosses_every_axis():
    archetypes = sweep.sweep_archetypes(skip=())
    assert "none" not in archetypes
    assert len(sweep.combinations(archetypes)) == (
        len(archetypes) * 4 * 3 * 2 * len(sweep.BOXES)
    )
    assert len(sweep.combinations(archetypes, sweep.BOXES[:2])) == (
        len(archetypes) * 4 * 3 * 2 * 2
    )


def test_the_swept_document_carries_the_library_and_the_font_floor():
    box = sweep.Box("wide_short", 1138, 386)
    figure = '<section class="ig ig-roadmap n6"><div class="ig-desc">x</div></section>'
    html = sweep.sweep_document(figure, box, ".ig-desc{font-size:13px}", font_floor=15.0)
    assert f"width:{box.width}px;height:{box.height}px" in html
    assert ".ig-desc{font-size:15px}" in html


def test_an_unresolved_illustration_marker_is_dropped_so_the_render_is_admitted():
    figure = '<div class="ig-art"><img data-illust-prompt="SCENE" alt=""></div>'
    html = sweep.sweep_document(figure, sweep.BOXES[0], "", font_floor=0)
    assert "data-illust-prompt" not in html
    assert '<img alt="">' in html


def test_a_box_can_be_named_on_the_command_line():
    # The sweep has to be runnable at whatever box a defect was measured in,
    # without editing the matrix into the script.
    assert sweep.parse_box("1138x440") == sweep.Box("box_1138x440", 1138, 440)
    assert sweep.parse_box("tall:1152x560") == sweep.Box("tall", 1152, 560)


def test_the_matrix_covers_the_boxes_the_contract_now_asks_for():
    # 430px is the contract's stated floor; a figure told to reach the bottom
    # content line lands between there and about 560 on an ordinary page, and
    # those three are where the fullness reading is judged.
    heights = {box.height for box in sweep.BOXES}
    assert {430, 500, 560} <= heights
    # The short boxes stay as the regression floor for the compaction.
    assert min(heights) <= 400
