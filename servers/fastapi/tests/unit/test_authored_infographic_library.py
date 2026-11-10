"""The archetype component library: tokens, skeletons, the contract checker,
library injection, and the authoring egress that ties them together.
"""

import re
from types import SimpleNamespace

import pytest

from models.authored_infographic_plan import ARCHETYPES, coerce_infographic_plan
from utils.authored_infographic import (
    check_infographic_contract,
    library_classes,
    render_skeleton,
    slot_contract,
    tokens_for_brand,
)
from utils.authored_dom_quality import ensure_authored_qids
from utils.authored_infographic.library import LIBRARY_TEMPLATE
from utils.authored_infographic.skeleton import density_class, node_count, split_figure

_CONTENT = (
    "## 예지보전 순환\n- 감지: 센서 이상 패턴 포착\n- 예측: 고장 확률 72% 산출\n"
    "- 조치: 작업지시 자동 발행\n- 학습: 조치 결과 반영\n- 확산: 협력사 공유\n- 보안: 망분리"
)
_NODES = [
    {"label": "감지", "desc": "센서 이상 패턴 포착", "figure": "38시간"},
    {"label": "예측", "desc": "고장 확률 산출", "figure": "72%"},
    {"label": "조치", "desc": "작업지시 자동 발행", "figure": "12건"},
    {"label": "학습", "desc": "조치 결과 반영", "figure": "4.6%"},
    {"label": "확산", "desc": "협력사 공유"},
    {"label": "보안", "desc": "망분리", "figure": "3단계"},
]


def _slide(archetype="cycle", count=4, density="high", emphasis=1):
    plan = coerce_infographic_plan(
        {
            "slides": [
                {
                    "index": 2,
                    "archetype": archetype,
                    "weight": "hero",
                    "takeaway": "감지-예측-조치-학습",
                    "nodes": _NODES[:count],
                    "emphasis": emphasis,
                    "density": density,
                }
            ]
        },
        slide_contents=["표지", "목차", _CONTENT],
        roles=["COVER", "AGENDA", "CONTENT"],
    )
    slide = plan.at(2)
    assert slide.archetype == archetype
    return slide


def _doc(body, head=""):
    return (
        "<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        f"{head}</head><body><div style=\"width:1152px;height:520px\">{body}</div></body></html>"
    )


# --- tokens and CSS -------------------------------------------------------------


def test_a_bad_primary_falls_back_rather_than_failing():
    assert tokens_for_brand(SimpleNamespace(primary="teal", fonts=None)).primary == "#2563EB"


def test_the_library_covers_every_archetype_root_and_density():
    classes = library_classes()
    for name in ("ig", "ig-node", "ig-node-bg", "is-key", "n3", "n4", "n5", "n6"):
        assert name in classes
    for archetype in ARCHETYPES:
        if archetype != "none":
            assert render_skeleton(_slide(archetype, 3)).startswith("<section class=\"ig ig-")


# --- skeletons ------------------------------------------------------------------


def test_the_skeleton_carries_the_planned_text_and_stable_qids():
    slide = _slide("cycle", 4)
    skeleton = render_skeleton(slide)
    assert 'data-qid="ig.s3.root"' in skeleton and 'data-qid="ig.s3.n2.label"' in skeleton
    assert "감지" in skeleton and "센서 이상 패턴 포착" in skeleton
    assert '<span class="ig-num" data-qid="ig.s3.n2.num">72%</span>' in skeleton
    assert 'class="ig-node p2 is-key"' in skeleton
    assert "감지-예측-조치-학습" in skeleton


def test_density_hides_phrases_and_figures():
    low = render_skeleton(_slide("process", 4, density="low"))
    assert "ig-desc" not in low and "ig-figure" not in low
    medium = render_skeleton(_slide("process", 4, density="medium"))
    assert "ig-desc" in medium and "ig-figure" not in medium


def test_split_figure_and_node_counts():
    assert split_figure("72시간 전") == ("72", "시간 전")
    assert split_figure("4.6%") == ("4.6%", "")
    assert split_figure("상위") == ("", "상위")
    assert node_count(_slide("hub_spoke", 5)) == 4
    assert node_count(_slide("comparison", 4)) == 0
    assert density_class(_slide("comparison", 4)) == "n4"
    assert density_class(_slide("hub_spoke", 5)) == "n4"


def test_tables_are_semantic_for_native_promotion():
    before_after = render_skeleton(_slide("before_after", 4))
    assert before_after.count("<tr") == 5 and '<th scope="row"' in before_after
    comparison = render_skeleton(_slide("comparison", 3))
    assert "<thead" in comparison and comparison.count("</th>") == 5


@pytest.mark.parametrize("archetype", ["comparison", "before_after"])
@pytest.mark.parametrize("count", [3, 4, 5, 6])
def test_a_table_figure_is_the_only_declared_element_inside_itself(archetype, count):
    """The exporter admits a declared table only when it owns no declared child."""
    slide = _slide(archetype, count)
    skeleton = render_skeleton(slide)
    table = skeleton.split("<table", 1)[1]
    assert table.startswith(' class="ig-table" data-native="table" data-qid=')
    body = table.split(">", 1)[1]
    assert "data-qid=" not in body, "a row or cell of a native table carries a data-qid"
    for tag in ("tr", "td", "th", "thead", "tbody", "span"):
        for opening in re.findall(rf"<{tag}\b[^>]*>", body):
            assert 'data-qid-ignore=""' in opening, f"{opening} is not excluded from the qids"
    assert check_infographic_contract(_doc(skeleton), slide) == []
    # The canonicalizer mints a qid for every edit target; the cells must not
    # become one, or the exporter would refuse the table it just admitted.
    canonical = ensure_authored_qids(_doc(skeleton))
    assert "data-qid=" not in canonical.split("<table", 1)[1].split(">", 1)[1].split("</table>")[0]


def test_the_checker_names_every_native_table_departure():
    slide = _slide("comparison", 3)
    skeleton = render_skeleton(slide)
    untagged = skeleton.replace(' data-native="table"', "", 1)
    assert any("data-native" in v for v in check_infographic_contract(_doc(untagged), slide))
    declared = skeleton.replace("<td class=", '<td data-qid="ig.s3.x" class=', 1)
    assert any("data-qid inside the table" in v for v in check_infographic_contract(_doc(declared), slide))
    iconised = skeleton.replace("</td>", "<svg></svg></td>", 1)
    assert any("only contain text" in v for v in check_infographic_contract(_doc(iconised), slide))
    merged = skeleton.replace("<td class=", '<td colspan="2" class=', 1)
    assert any("span rows or columns" in v for v in check_infographic_contract(_doc(merged), slide))
    ragged = skeleton.replace("</tr>", '<td data-qid-ignore="">x</td></tr>', 1)
    assert any("same number of cells" in v for v in check_infographic_contract(_doc(ragged), slide))


def test_no_library_class_forces_the_exporter_to_rasterise_its_wrapper():
    """`chart`/`diagram`/`flowchart`/`timeline`/`mindmap` as a whole class token
    makes the hybrid exporter treat the whole wrapper as a raster island; the
    matcher splits a class on whitespace, ``_`` and ``-``."""
    forbidden = re.compile(r"(^|[\s_-])(chart|diagram|flowchart|timeline|mindmap)([\s_-]|$)")
    offenders = sorted(name for name in library_classes() if forbidden.search(name))
    assert offenders == [], offenders
    assert "ig-milestones" in library_classes() and "ig-timeline" not in library_classes()


_PAIR_CONTENT = "## 현행 vs 도입 후\n- 불량률: 4.6% → 2.2%\n- 재고 오차: 9% → 1.5%\n- 납기 준수율: 87% → 96%"
_PAIR_NODES = [
    {"label": "불량률", "before": "4.6%", "after": "2.2%", "figure": "-2.4%p"},
    {"label": "재고 오차", "before": "9%", "after": "1.5%"},
    {"label": "납기 준수율", "before": "87%", "after": "96%"},
]


def _pair_slide(archetype, density="high"):
    plan = coerce_infographic_plan(
        {
            "slides": [
                {
                    "index": 0,
                    "archetype": archetype,
                    "weight": "hero",
                    "takeaway": "손실지표 전면개선",
                    "nodes": _PAIR_NODES,
                    "emphasis": 1,
                    "density": density,
                }
            ]
        },
        slide_contents=[_PAIR_CONTENT],
        roles=["CONTENT"],
    )
    return plan.at(0)


def test_comparison_with_two_stated_values_gets_a_before_and_an_after_row():
    slide = _pair_slide("comparison")
    skeleton = render_skeleton(slide)
    assert '<th scope="row" data-qid-ignore="">BEFORE</th>' in skeleton
    assert '<th scope="row" data-qid-ignore="">AFTER</th>' in skeleton
    assert skeleton.count('class="ig-td-value') == 6
    assert check_infographic_contract(_doc(skeleton), slide) == []
    assert "BEFORE / AFTER row headers" in slot_contract(slide)


@pytest.mark.parametrize("count", [3, 4, 5, 6])
def test_roadmap_bars_always_end_inside_the_track(count):
    from utils.authored_infographic.skeleton_flow import roadmap_bar

    offsets = []
    for i in range(count):
        offset, width = roadmap_bar(i, count)
        assert offset + width <= 100 and offset % 20 == 0 and width % 20 == 0
        offsets.append(offset)
    assert offsets == sorted(offsets) and offsets[0] == 0
    skeleton = render_skeleton(_slide("roadmap", count))
    assert skeleton.count('class="ig-fill') == count
    # The library clips any bar the model lengthens past the track.
    assert ".ig-roadmap .ig-track{" in LIBRARY_TEMPLATE
    assert "overflow:hidden" in LIBRARY_TEMPLATE.split(".ig-roadmap .ig-track{", 1)[1].split("}", 1)[0]


# --- the checker ----------------------------------------------------------------


def test_the_checker_names_each_departure():
    slide = _slide("process", 3)
    skeleton = render_skeleton(slide)
    restyled = skeleton.replace(
        'class="ig-node-bg"', 'class="ig-node-bg" style="background:red"', 1
    )
    assert any("inline style" in v for v in check_infographic_contract(_doc(restyled), slide))
    reworded = skeleton.replace(">감지<", ">감지 단계<", 1)
    assert any("verbatim" in v for v in check_infographic_contract(_doc(reworded), slide))
    extra_class = skeleton.replace('class="ig-node ', 'class="ig-node my-card ', 1)
    assert any("my-card" in v for v in check_infographic_contract(_doc(extra_class), slide))
    dropped = skeleton.replace('<div class="ig-node-bg" data-qid="ig.s3.n1.bg"></div>', "", 1)
    assert any("ig-node-bg" in v for v in check_infographic_contract(_doc(dropped), slide))
    svg_text = skeleton.replace("</svg>", "<text>x</text></svg>", 1)
    assert any("svg" in v for v in check_infographic_contract(_doc(svg_text), slide))
    assert any(
        "figure root" in v for v in check_infographic_contract(_doc("<div>nothing</div>"), slide)
    )
    styled_head = _doc(skeleton, head="<style>.ig-label{color:red}</style>")
    assert any("library classes" in v for v in check_infographic_contract(styled_head, slide))


def test_matrix_nodes_may_move_between_quadrants():
    slide = _slide("matrix", 4)
    skeleton = render_skeleton(slide)
    swapped = skeleton.replace(">감지<", ">@@<").replace(">예측<", ">감지<").replace(">@@<", ">예측<")
    assert check_infographic_contract(_doc(swapped), slide) == []


# --- injection and egress -------------------------------------------------------


