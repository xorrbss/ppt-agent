"""Server chart renderer revision five and the writing policy around server charts."""
from __future__ import annotations


import pytest

from services.generation_core.drivers.svg import server_chart
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION_V4 as V4, REQUIREMENTS_VERSION_V5 as V5, apply_requested_native_quality,
)
from tests.unit.test_svg_server_chart_v4 import CASES, NS, _group, _page, _trusted

R4, R5 = server_chart.SERVER_CHART_REVISION_V4, server_chart.SERVER_CHART_REVISION_V5
BOX = (64.0, 140.0, 700.0, 460.0)


def _lines(page):
    return [(float(node.get("x1")), float(node.get("y1")), float(node.get("x2")), float(node.get("y2")),
             float(node.get("stroke-width", "1"))) for node in _group(page).iter(NS + "line")]


def _strip_lines(page):
    group = _group(page)
    return [(node.tag, {key: value for key, value in node.attrib.items()}, node.text)
            for node in group.iter() if node.tag != NS + "line" and node is not group]


@pytest.mark.parametrize("name", list(CASES))
def test_revision_five_keeps_every_line_stroke_inside_the_box(name):
    page, record = _page(CASES[name][0], render=R5)
    assert record["dropped"] == []
    x0, y0, width, height = BOX
    for x1, y1, x2, y2, stroke in _lines(page):
        half = stroke / 2
        assert min(x1, x2) - half >= x0 - 1e-6 and max(x1, x2) + half <= x0 + width + 1e-6
        assert min(y1, y2) - half >= y0 - 1e-6 and max(y1, y2) + half <= y0 + height + 1e-6


def test_revision_four_did_draw_a_baseline_from_the_box_edge():
    """The measured intrusion: a single-series column's baseline started at the box's left edge."""
    page, _ = _page(CASES["column"][0], render=R4)
    assert any(min(x1, x2) - stroke / 2 < BOX[0] for x1, _y1, x2, _y2, stroke in _lines(page))


@pytest.mark.parametrize("name", list(CASES))
def test_revision_five_draws_revision_four_apart_from_its_lines(name):
    four, four_record = _page(CASES[name][0], render=R4)
    five, five_record = _page(CASES[name][0], render=R5)
    assert _strip_lines(four) == _strip_lines(five)
    assert four_record["objects"][0]["raw"] == five_record["objects"][0]["raw"]
    assert four_record["objects"][0]["style"] == five_record["objects"][0]["style"]


@pytest.mark.parametrize("name", list(CASES))
@pytest.mark.parametrize("revision", [V4, V5])
def test_revision_five_is_trusted_passes_the_checks_and_exports_like_four(name, revision):
    five, _ = _page(CASES[name][0], render=R5)
    assert _trusted(five) == {"native-chart-x"}
    quality = {"passed": True, "failures": []}
    apply_requested_native_quality(quality, five, five["svg_content"], CASES[name][1], pt_per_px=.75,
                                   revision=revision, paint_deferred=True)
    assert quality["failures"] == []
    four, _ = _page(CASES[name][0], render=R4)
    (native_five,) = page_native_objects(five, pt_per_px=.75, revision=revision)[0]["objects"]
    (native_four,) = page_native_objects(four, pt_per_px=.75, revision=revision)[0]["objects"]
    assert native_five == native_four


def test_revision_five_is_current_and_has_its_own_rule():
    assert R5 in server_chart.TRUSTED_RENDERERS
    rule = server_chart.server_chart_rule({"server_chart_render": R5})
    assert rule == server_chart.SERVER_CHART_RULE_V4.replace(f'"{R4}"', f'"{R5}"')


#: Published output of revision five. A change here means a stored deck would replay
#: differently: publish a new revision instead of editing v5.
V5_DIGESTS = {
    "column": "sha256:b0fd7213827e28596cc5887f3fa60efcc2523070944cea0492cd4aa716619f8f",
    "bar_many": "sha256:452e1808b2fb3f313c96e369a58bf1ecbefe4155abd5009b1df257e9bf9f740e",
    "clustered": "sha256:ee3e04b92d2aa8ca8aa9208fecea6bd5ad76a770f6cc273aa5fc40c454e8e758",
    "stacked": "sha256:4d6baec9d0f9afacba9e5dbccbfe5b191ad4747572bf9afbb641ae97d759360a",
    "percent": "sha256:c315cb09405f722fb98e7b0fea30a21c61e29ce7d2c8c2f5505ca1c96737feb1",
    "line": "sha256:e7cb5150f3b2fccfe907d5370fb157672d4728be4239cb898ef6d1789fc1f485",
    "lines_few": "sha256:7d305079bd5bfdcbc3ac930afc6a17cf62e3923725b15b1a89f2c880ea64ef15",
    "lines_many": "sha256:5dcc1198f4e1ee250aa278e04dffcc706a631dc2e6d16aaa27e6f60745319856",
    "area": "sha256:6b91f5e6c2d1f7667e28bb09cc4d57ee5c17fc0e32bf66c6795a806a67cdd679",
    "areas": "sha256:b09894f76e232a10f3d2cd2f02401bd4c7afd43d7d209cc175bbd782146387e9",
    "pie": "sha256:44b934fb2f4d088e518a5ad773cfd694daf1b957c82fe87485581635af3629d8",
    "pie_small": "sha256:f738066f8a77f584499e9fd57a8cad87db0c3c17fd536d5e396ae9a3d1171da2",
    "donut": "sha256:f4cc4d0dc85038ac4dcb3429b90beef28d07aaa9a3de817099b146b2784643e6",
    "donut_legend": "sha256:545059b31718be5001b5f36e08815ca8c3ce63941b41850d3b78d1f8fc338cb7",
    "zero": "sha256:ac6c97b64d3e86da1f1697c943b764cab2c8b1cfb11d88397b368d6241f203a9",
}


@pytest.mark.parametrize("name", list(CASES))
def test_revision_five_output_is_frozen(name):
    from services.svg_contract import source_digest
    page, _ = _page(CASES[name][0], render=R5)
    assert source_digest(page["svg_content"]) == V5_DIGESTS[name]


# --- The writing policy leaves server-drawn charts alone ------------------------------------

MIDDLE_DOT = {"chartType": "bar", "categories": ["강원·제주권", "호남권", "충청권"],
              "series": [{"name": "이용자", "values": [189, 175, 168]}], "title": "이용자 (만명)"}


def test_server_chart_group_ids_name_the_bound_server_charts():
    page, _ = _page(MIDDLE_DOT, render=R5)
    assert server_chart.server_chart_group_ids(page, page["svg_content"]) == ("native-chart-x",)
    assert server_chart.server_chart_group_ids(page, page["svg_content"].replace(">Title<", ">Other<")) == ()
    assert server_chart.server_chart_group_ids({}, page["svg_content"]) == ()


def test_symbol_substitution_and_the_policy_scan_skip_server_chart_text():
    from services.ppt_writing_visible import substitute_svg_visible_symbols, svg_text_nodes

    page, _ = _page(MIDDLE_DOT, render=R5)
    svg = page["svg_content"]
    groups = server_chart.server_chart_group_ids(page, svg)
    rewritten, substitutions = substitute_svg_visible_symbols(svg)
    protected, none = substitute_svg_visible_symbols(svg, protected_element_ids=groups)
    assert any("강원, 제주권" in text for text in svg_text_nodes(rewritten)) and substitutions
    assert protected == svg and none == []
    assert "강원·제주권" in svg_text_nodes(svg)
    assert not any("강원" in text for text in svg_text_nodes(svg, excluded_ids=groups))


def test_render_protects_server_charts_from_the_writing_policy():
    import inspect
    from services.generation_core.drivers.svg import render
    source = inspect.getsource(render)
    assert "*chart_groups))" in source
    assert "svg_text_nodes(final, excluded_ids=chart_groups)" in source
