"""The archetype component library: tokens, skeletons, the contract checker,
library injection, and the authoring egress that ties them together.
"""

import re
from types import SimpleNamespace

import pytest

from models.authored_infographic_plan import ARCHETYPES, coerce_infographic_plan
from utils.authored_infographic import (
    InfographicContractError,
    check_infographic_contract,
    finalize_infographic_slide_html,
    has_library,
    inject_library_css,
    library_classes,
    library_css,
    render_skeleton,
    slot_contract,
    tokens_for_brand,
)
from utils.authored_dom_quality import ensure_authored_qids
from utils.authored_infographic.library import LIBRARY_TEMPLATE
from utils.authored_infographic.skeleton import density_class, node_count, split_figure
from utils.llm_calls import author_deck as author_deck_mod
from utils.llm_calls.author_infographic_marker import infographic_marker
from utils.llm_calls.author_slide import Brand, validate_authored_slide_html
from utils.llm_calls.author_slide_prompt import author_prompt

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


def _brand(primary="#1E5EFF"):
    return Brand(topic="스마트팩토리", primary=primary, fonts="Noto Sans KR")


def _doc(body, head=""):
    return (
        "<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        f"{head}</head><body><div style=\"width:1152px;height:520px\">{body}</div></body></html>"
    )


# --- tokens and CSS -------------------------------------------------------------


def test_tokens_come_from_the_brand_and_stay_literal():
    tokens = tokens_for_brand(_brand("#1E5EFF"))
    assert tokens.primary == "#1E5EFF"
    assert tokens.primary_tint != tokens.primary and tokens.primary_deep != tokens.primary
    assert "Malgun Gothic" in tokens.font
    css = library_css(_brand("#1E5EFF"))
    assert "#1E5EFF" in css and "$" not in css and "--" not in css
    assert "::before" not in LIBRARY_TEMPLATE and "::after" not in LIBRARY_TEMPLATE
    assert not re.search(r"[;{]\s*content:", LIBRARY_TEMPLATE)


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


@pytest.mark.parametrize("archetype", [a for a in ARCHETYPES if a != "none"])
@pytest.mark.parametrize("count", [3, 4, 5, 6])
def test_every_skeleton_satisfies_its_own_contract_and_renderer_admission(archetype, count):
    slide = _slide(archetype, count)
    skeleton = render_skeleton(slide)
    assert check_infographic_contract(_doc(skeleton), slide) == []
    admitted = validate_authored_slide_html(
        inject_library_css(_doc(skeleton), library_css(_brand())),
        allow_illustration_slots=True,
    )
    assert has_library(admitted)


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


def test_before_after_fills_both_value_cells_and_a_delta_column_from_the_plan():
    slide = _pair_slide("before_after")
    skeleton = render_skeleton(slide)
    assert '<td class="ig-td-before" data-qid-ignore="">4.6%</td>' in skeleton
    assert '<td class="ig-td-after" data-qid-ignore="">2.2%</td>' in skeleton
    assert '<td class="ig-td-delta" data-qid-ignore="">-2.4%p</td>' in skeleton
    assert skeleton.count("ig-th-delta") == 1
    assert check_infographic_contract(_doc(skeleton), slide) == []
    validate_authored_slide_html(_doc(skeleton, f"<style>{library_css(_brand())}</style>"))
    # Without a stated delta the table keeps its four columns.
    lean = render_skeleton(_pair_slide("before_after", density="medium"))
    assert "ig-td-delta" not in lean and '>9%</td>' in lean and '>1.5%</td>' in lean


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


@pytest.mark.parametrize("count", [3, 4, 5, 6])
def test_cycle_draws_one_arrowhead_per_segment_and_a_filled_key_plate(count):
    slide = _slide("cycle", count, density="medium", emphasis=1)
    skeleton = render_skeleton(slide)
    assert skeleton.count('class="ig-ring-head h') == count
    assert all(f'ig-ring-head h{i + 1}"' in skeleton for i in range(count))
    assert '<div class="ig-node p2 is-key"' in skeleton
    css = library_css(_brand())
    # Every arrowhead is placed by coordinates and turned in place: a pure
    # rotation is what the exporter keeps as a native shape.
    for i in range(count):
        rule = f".ig-cycle.n{count} .ig-ring-head.h{i + 1}{{"
        assert rule in css
        declarations = css.split(rule, 1)[1].split("}", 1)[0]
        assert "left:" in declarations and "top:" in declarations
        assert re.fullmatch(r"rotate\(\d+deg\)", declarations.split("transform:")[1])
    assert ".ig-node.is-key .ig-node-bg{background:#1E5EFF" in css
    assert ".ig-cycle .ig-node.is-key{width:220px" in css
    assert check_infographic_contract(_doc(skeleton), slide) == []


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


def test_a_quiet_page_has_no_contract():
    quiet = coerce_infographic_plan(
        {"slides": [{"index": 0}]}, slide_contents=["표지"], roles=["COVER"]
    ).at(0)
    assert check_infographic_contract("<html><body>x</body></html>", quiet) == []
    untouched = "<html><body>x</body></html>"
    assert finalize_infographic_slide_html(untouched, brand=_brand(), slide=quiet) == untouched


# --- injection and egress -------------------------------------------------------


def test_injection_is_idempotent_and_lands_after_the_models_styles():
    css = library_css(_brand())
    html = _doc("<p>x</p>", head="<style>p{color:red}</style>")
    once = inject_library_css(html, css)
    assert once.index("p{color:red}") < once.index(".ig-lib-v1")
    assert inject_library_css(once, css) == once


def test_finalize_rejects_a_broken_figure_with_a_hint_and_the_rejected_html():
    slide = _slide("process", 3)
    broken = _doc(render_skeleton(slide).replace(">감지<", ">감지하기<", 1))
    with pytest.raises(InfographicContractError) as raised:
        finalize_infographic_slide_html(broken, brand=_brand(), slide=slide)
    assert raised.value.rejected_html == broken
    correction = author_deck_mod._classify_author_validation_error(raised.value)
    assert correction is not None and correction.category == "infographic_contract"
    assert "감지" in correction.hint and "FIGURE MARKUP" in correction.hint


def test_the_prompt_carries_the_contract_and_the_skeleton():
    slide = _slide("cycle", 4)
    marker = infographic_marker(slide)
    assert "FIGURE CONTRACT" in marker and render_skeleton(slide) in marker
    assert "ICON MENU" in marker and "ARCHETYPE NOTE (cycle)" in marker
    prompt = author_prompt(
        _CONTENT, "DESIGN", _brand(), "CONTENT", 2, 10, infographic=slide
    )
    assert slot_contract(slide) in prompt


def test_the_batch_egress_checks_and_injects_like_the_single_path():
    """The batch path post-processes through the same contract and injection."""
    from utils.llm_calls import author_batch as batch_mod

    slide = _slide("process", 3)
    request = batch_mod.AuthoredBatchSlideRequest(
        index=2, content=_CONTENT, role="CONTENT", infographic=slide
    )
    prepared = batch_mod._prepare_slide(
        request, design_system="DESIGN", brand=_brand(), slide_count=10
    )
    assert "FIGURE CONTRACT" in prepared.prompt
    good = _doc(render_skeleton(slide))
    finished = batch_mod._postprocess_slide(prepared, good, brand=_brand())
    assert has_library(finished) and "#1E5EFF" in finished
    with pytest.raises(InfographicContractError):
        batch_mod._postprocess_slide(
            prepared, good.replace(">감지<", ">감지하기<", 1), brand=_brand()
        )
