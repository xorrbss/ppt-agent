"""The native PPTX chart of a trusted ``svg-server-chart/v4`` chart follows the renderer's decisions.

One spec, two outputs: ``server_chart_v4_native.native_style`` replays the stored declaration
and style and records what the drawing decided (colours, fonts, axis bounds, gridlines, labels,
legend, plot area); publication stores it as ``svgAppearance["serverChartStyle"]`` and the
export projection builds the converter payload from it alone.

Every other chart (author-drawn, v1/v2/v3 server charts, a v4 chart whose trust was lost)
keeps its exact earlier sidecar and converter input: ``PRE_CHANGE_DIGESTS`` were computed
before this capability existed.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg import server_chart
from services.generation_core.drivers.svg.export_native_wire import bundle_entry, parse_bundle_native_objects
from services.generation_core.drivers.svg.native_declarations import (
    bind_native_declarations,
    extract_native_declarations,
)
from services.generation_core.drivers.svg.publish import svg_slide_content
from services.svg_contract import finalize_svg
from tests.unit.test_svg_server_chart_v4 import CASES, NS, _page

R1, R2, R3 = (server_chart.SERVER_CHART_REVISION, server_chart.SERVER_CHART_REVISION_V2,
              server_chart.SERVER_CHART_REVISION_V3)


def _published(page: dict) -> tuple[str, dict]:
    payload = {"svg_content": finalize_svg(page["svg_content"], index=0, n=1), "quality": {"passed": True},
               "nativeDeclarations": page["nativeDeclarations"]}
    bind_native_declarations(payload, payload["svg_content"], page["nativeDeclarations"])
    return payload["svg_content"], svg_slide_content(payload, role="content", source="", pt_per_px=0.75)


def _scene(tmp_path: Path, svg: str, content: dict) -> tuple[str, list]:
    """The annotated page and offers ``prepare_svg_pptx_inputs`` produces for one slide."""
    from services.generation_core.drivers.svg.export_scene import prepare_svg_pptx_inputs
    from workers.public_svg_pptx_convert import VENDOR_ROOT

    if str(VENDOR_ROOT) not in sys.path:
        sys.path.insert(0, str(VENDOR_ROOT))
    path = tmp_path / "slide.svg"
    path.write_text(svg, encoding="utf-8")
    pages = parse_bundle_native_objects([bundle_entry(content)], 1)
    inputs = prepare_svg_pptx_inputs([path], pages, width=1280, height=720)
    offers = [(offer.id, offer.kind, offer.native, offer.reason) for offer in inputs.offers]
    return path.read_text(encoding="utf-8"), offers


def _digest(tmp_path: Path, page: dict) -> str:
    svg, content = _published(page)
    annotated, offers = _scene(tmp_path, svg, content)
    record = {"sidecar": content.get("nativeObjects"), "receipt": content["svgReceipt"].get("nativeObjects"),
              "annotated": annotated, "offers": offers}
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _author_drawn(name: str) -> dict:
    """The revision-three drawing of a case, declared by an author (no renderer marker)."""
    raw = CASES[name][0]
    drawn, _ = _page(raw, render=R3)
    root = ET.fromstring(drawn["svg_content"])
    group = next(node for node in root.iter(NS + "g") if node.get("id") == "native-chart-x")
    group.set("data-native-chart", json.dumps(raw, ensure_ascii=False))
    record = extract_native_declarations(root)
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, record)
    return page


def _untrusted_v4(name: str) -> dict:
    """A v4 drawing whose trust was lost (one gridline nudged): it takes the drawing proof."""
    page, _ = _page(CASES[name][0])
    root = ET.fromstring(page["svg_content"])
    group = next(node for node in root.iter(NS + "g") if node.get("id") == "native-chart-x")
    line = next(node for node in group.iter(NS + "line"))
    line.set("x2", str(float(line.get("x2")) - 1))
    svg = ET.tostring(root, encoding="unicode")
    edited = {"svg_content": svg}
    bind_native_declarations(edited, svg, json.loads(json.dumps(page["nativeDeclarations"])))
    assert server_chart.trusted_server_chart_ids(edited["nativeDeclarations"], svg) == frozenset()
    return edited


def _fixture(key: str) -> dict:
    kind, name = key.split(":", 1)
    if kind in ("v1", "v2", "v3"):
        page, _ = _page(CASES[name][0], render={"v1": R1, "v2": R2, "v3": R3}[kind])
        return page
    if kind == "author":
        return _author_drawn(name)
    return _untrusted_v4(name)


_BAR_CASES = ("column", "bar_many", "clustered", "stacked", "percent", "zero")
_FIXTURES = ([f"v1:{name}" for name in _BAR_CASES] + [f"{rev}:{name}" for rev in ("v2", "v3") for name in CASES]
             + [f"author:{name}" for name in CASES] + ["untrusted:clustered", "untrusted:line", "untrusted:column"])

#: Computed before the v4 native style existed (2026-11-09). These outputs must never move.
PRE_CHANGE_DIGESTS: dict[str, str] = {
    "v1:column": "3b09356bfbd20154f18c7b3aef9f3cd9edb117c73e78e2219fe1784f4a60a7d4",
    "v1:bar_many": "107f15490acb70a0943c9e6f62cab9070341829f210d6b1d58f0b71de72ea020",
    "v1:clustered": "2ebb01419df1e53f6efcaaa6f03533261fdffa13a6824d966c0bbfada17df177",
    "v1:stacked": "062b4e28ba9992ee1a80c0ae87c2a6b84e6e41d81135b410227edb7b392f2309",
    "v1:percent": "39fb707fd644261bee8d47508f8996b669a64359788b182dc56a7a4e28c150e5",
    "v1:zero": "f0db23e157235851b4c89528a50f32828f172f22e67f9d3681c27ec4a1a18ebc",
    "v2:column": "3b09356bfbd20154f18c7b3aef9f3cd9edb117c73e78e2219fe1784f4a60a7d4",
    "v2:bar_many": "107f15490acb70a0943c9e6f62cab9070341829f210d6b1d58f0b71de72ea020",
    "v2:clustered": "2ebb01419df1e53f6efcaaa6f03533261fdffa13a6824d966c0bbfada17df177",
    "v2:stacked": "062b4e28ba9992ee1a80c0ae87c2a6b84e6e41d81135b410227edb7b392f2309",
    "v2:percent": "39fb707fd644261bee8d47508f8996b669a64359788b182dc56a7a4e28c150e5",
    "v2:line": "82d0af951348d05421b67c0ae8e3bb3a68ae631dac8359f2e92b8a5a6fc5ec65",
    "v2:lines_few": "02a67bf82808634dc8e11a5a05cbffc9daaa0df875664f7a4ec8685f4a060dc2",
    "v2:lines_many": "c83890c6b39c3abfdf2d11abc212dbdccc18d46a613ec3c2686dc6987e0a57ee",
    "v2:area": "505e99aea4208737cf3254a8b72846443a66dbd9c8f55591f35e125b60570bab",
    "v2:areas": "38f464b743336d00caa590e8cbd059737e059cbdebb27401e53d9f01df6df67f",
    "v2:pie": "1e2e6a84d9ca2c816f29e50b34131e85716b1bc58cbe422a7368e60dc9ecdd4f",
    "v2:pie_small": "fbaaa81e45db80a1b6f153d52f8e431c7ef1d79b78763288227b786db831d88a",
    "v2:donut": "edb0f02944727ea1d14b695a4c5a6ca889258c363edfa68526aa40118b0ae7c4",
    "v2:donut_legend": "15e28e883d144dcb0287f812c1fc775b4e7c9176e473fb4bbc666ad0ff2eda7d",
    "v2:zero": "f0db23e157235851b4c89528a50f32828f172f22e67f9d3681c27ec4a1a18ebc",
    "v3:column": "e82c36a71289398f728ca7ae1a2e5aab400902cb1c592addb31534645f707d24",
    "v3:bar_many": "6ed84a0537b673efcc725cffcd14e1efd71266cdf2d8c754e5bd24a1306bf420",
    "v3:clustered": "2ebb01419df1e53f6efcaaa6f03533261fdffa13a6824d966c0bbfada17df177",
    "v3:stacked": "062b4e28ba9992ee1a80c0ae87c2a6b84e6e41d81135b410227edb7b392f2309",
    "v3:percent": "39fb707fd644261bee8d47508f8996b669a64359788b182dc56a7a4e28c150e5",
    "v3:line": "ea590a40c8abca7a45b7ed29ed9ce151e65985f20e34f8c9e888519e2bf89e4a",
    "v3:lines_few": "02a67bf82808634dc8e11a5a05cbffc9daaa0df875664f7a4ec8685f4a060dc2",
    "v3:lines_many": "c83890c6b39c3abfdf2d11abc212dbdccc18d46a613ec3c2686dc6987e0a57ee",
    "v3:area": "94fa45a180adce8a924264d9b16533a750790e2cf7caeff58d7e053895e1c385",
    "v3:areas": "38f464b743336d00caa590e8cbd059737e059cbdebb27401e53d9f01df6df67f",
    "v3:pie": "9dbae7dd44413fc8bbcd605f47eb650819acbef4178f919ed97d1a19f1f90047",
    "v3:pie_small": "d91b97646398d2271e7dc4fdec6a91a9521f5c4619b94a2534626515e300c4a3",
    "v3:donut": "7c23199d0bc1a3b8efa5650ecdaddb00e631e697950e36685490216ecccc921d",
    "v3:donut_legend": "d0d812dbffde8ca4a7aa7730e6e87c2da57e4bdf0b59db1911f6c936850e9a83",
    "v3:zero": "f0db23e157235851b4c89528a50f32828f172f22e67f9d3681c27ec4a1a18ebc",
    "author:column": "3b09356bfbd20154f18c7b3aef9f3cd9edb117c73e78e2219fe1784f4a60a7d4",
    "author:bar_many": "107f15490acb70a0943c9e6f62cab9070341829f210d6b1d58f0b71de72ea020",
    "author:clustered": "9d64010053736974142a97df103b481fa5faa5dbfb9d626e602767afcbdf4493",
    "author:stacked": "0d1afdca18c832c46fb28d0e632d55bff669d26090186e8d6fe63dde1b706b66",
    "author:percent": "c006d267ca63e4e6961fc61dae4752e1220530850458c3b3f6441845297a03c0",
    "author:line": "82d0af951348d05421b67c0ae8e3bb3a68ae631dac8359f2e92b8a5a6fc5ec65",
    "author:lines_few": "f99b39746e9615f69fe7fe6f70913effd7d964ef90ac2eb5dbcf7cc65cdb7be0",
    "author:lines_many": "81d73bf5b96c6da8e64559fb2a0e238e1d2ee157b71f7d3e153b35394a6dddf6",
    "author:area": "505e99aea4208737cf3254a8b72846443a66dbd9c8f55591f35e125b60570bab",
    "author:areas": "7d24e532a8438bc9ef83cd13116968c5f7dc3c359a058f8abd7a9fbc8cf6e70c",
    "author:pie": "1e2e6a84d9ca2c816f29e50b34131e85716b1bc58cbe422a7368e60dc9ecdd4f",
    "author:pie_small": "fbaaa81e45db80a1b6f153d52f8e431c7ef1d79b78763288227b786db831d88a",
    "author:donut": "edb0f02944727ea1d14b695a4c5a6ca889258c363edfa68526aa40118b0ae7c4",
    "author:donut_legend": "15e28e883d144dcb0287f812c1fc775b4e7c9176e473fb4bbc666ad0ff2eda7d",
    "author:zero": "e150ae3267a5fd6e6f1375e7d05365bc08366d79af6b6ffb31646502df488887",
    "untrusted:clustered": "0d9e4b5dfe256520d289dbbb2f7328c9285096e3e046ac372b81d9648dee940e",
    "untrusted:line": "5bed693a96882b84c4b059f91f04af208573e23f13c871b13754095a0c029841",
    "untrusted:column": "a0ead1300752182bedd83ca4817605b43c4392dca64e921e5ed090c4e519cc59",
}


@pytest.mark.parametrize("key", _FIXTURES)
def test_other_charts_keep_their_exact_sidecar_and_converter_input(key, tmp_path):
    assert _digest(tmp_path, _fixture(key)) == PRE_CHANGE_DIGESTS[key]


def test_every_fixture_is_pinned():
    assert set(PRE_CHANGE_DIGESTS) == set(_FIXTURES)


# --- Trusted v4: the sidecar carries the renderer's spec --------------------------------------

def _v4_chart(name: str) -> tuple[dict, dict]:
    """``(record object, published sidecar chart)`` of a v4 case."""
    page, record = _page(CASES[name][0])
    _svg, content = _published(page)
    (chart,) = content["nativeObjects"]["objects"]
    return record["objects"][0], chart


@pytest.mark.parametrize("name", list(CASES))
def test_a_trusted_v4_sidecar_carries_the_spec_its_drawing_replays(name):
    from services.generation_core.drivers.svg.server_chart_v4_native import native_style

    item, chart = _v4_chart(name)
    spec = chart["svgAppearance"]["serverChartStyle"]
    colors, replayed = native_style(json.loads(json.dumps(item["raw"])), server_chart._replay_style(item), item["box"])
    assert spec == replayed
    assert chart["anchor"] == dict(zip(("x", "y", "width", "height"), item["box"], strict=True))
    if CASES[name][0]["chartType"] not in ("pie", "donut"):
        assert [series["color"] for series in chart["payload"]["series"]] == [colors[i] for i in range(len(colors))]
    # The spec is the page's own style: font, ink and the palette that leads the series.
    assert spec["fontFamily"] == item["style"]["font"] and spec["textColor"] == item["style"]["ink"].upper()
    assert spec["textPx"] <= item["style"]["size"]


def test_the_spec_follows_the_drawings_axis_label_and_legend_decisions():
    specs = {name: _v4_chart(name)[1]["svgAppearance"]["serverChartStyle"] for name in CASES}
    # A single series of few categories: clean bars with labels, no axis; the longest bar fills the plot.
    assert specs["column"]["valueAxis"] == {"visible": False, "gridlines": False, "minimum": 0, "maximum": 210,
                                            "numberFormat": "#,##0"}
    assert specs["column"]["dataLabels"]["numberFormat"] == '#,##0"억원"'
    # Several series: ticks, gridlines and the nice axis the drawing used.
    assert specs["clustered"]["valueAxis"] == {"visible": True, "gridlines": True, "minimum": 0, "maximum": 80,
                                               "majorUnit": 20, "numberFormat": "#,##0"}
    assert specs["clustered"]["legend"]["visible"] and specs["clustered"]["legend"]["position"] == "top"
    assert specs["clustered"]["dataLabels"] == {"shown": False}
    # A percent stack: the native axis runs 0..1 with percent ticks; labels keep the raw value and unit.
    assert specs["percent"]["valueAxis"]["maximum"] == 1 and specs["percent"]["valueAxis"]["numberFormat"] == "0%"
    assert specs["percent"]["dataLabels"]["numberFormat"] == '#,##0"%"'
    # Stacks put labels in the segment, in the contrast colour.
    assert specs["stacked"]["dataLabels"]["position"] == "center"
    assert specs["stacked"]["bars"]["overlap"] == 100
    # Lines are 3 px with 9 px markers; areas 2 px, translucent, unlabelled position.
    assert specs["line"]["line"] == {"widthPx": 3.0, "markerPx": 9.0}
    assert specs["area"]["line"] == {"widthPx": 2.0, "markerPx": None} and specs["area"]["fillOpacity"] == 0.3
    assert specs["area"]["dataLabels"]["position"] is None
    assert specs["lines_many"]["dataLabels"] == {"shown": False}
    # Pie and donut: a legend layout when the drawing used one; the donut keeps its hole.
    assert specs["donut"]["holeRatio"] == 0.58
    assert "layout" in specs["donut_legend"]["legend"]
    assert specs["pie_small"]["dataLabels"]["showCategory"] is True


@pytest.mark.parametrize("mutate", [
    lambda spec: spec.update(version="svg-server-chart-native-style/v0"),
    lambda spec: spec.update(textColor="red"),
    lambda spec: spec["valueAxis"].update(minimum=5),
    lambda spec: spec["valueAxis"].update(numberFormat="0.0E+00"),
    lambda spec: spec["dataLabels"].update(position="bestFit"),
    lambda spec: spec.update(extra=True),
    lambda spec: spec["bars"].update(gapWidth=900),
])
def test_a_malformed_spec_is_not_a_valid_sidecar(mutate):
    from services.generation_core.native_objects import NativeObjects, NativeObjectsError

    _item, chart = _v4_chart("column")
    wire = {"schemaVersion": "native-objects/v1", "objects": [json.loads(json.dumps(chart))]}
    NativeObjects.from_wire(wire)
    mutate(wire["objects"][0]["svgAppearance"]["serverChartStyle"])
    with pytest.raises(NativeObjectsError, match="native_chart_appearance_invalid"):
        NativeObjects.from_wire(wire)


# --- Trusted v4: the PPTX chart XML carries the spec -----------------------------------------

_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _chart_xml(name: str, tmp_path: Path):
    from tests.unit.test_svg_native_objects_end_to_end import _export, _frames

    page, _record = _page(CASES[name][0])
    svg, content = _published(page)
    deck, evidence = _export(tmp_path, [svg], [content])
    charts = [shape for shape in _frames(deck.slides[0]) if shape.has_chart]
    assert len(charts) == 1
    assert evidence["structuralReadback"]["nativeObjects"] == {"offered": 1, "native": 1, "fellBack": 0, "reasons": []}
    return content["nativeObjects"]["objects"][0], ET.fromstring(charts[0].chart._chartSpace.xml)


def _val(node, path):
    found = node.find(path)
    return None if found is None else found.get("val")


@pytest.mark.parametrize("name", list(CASES))
def test_every_v4_case_exports_one_native_chart_styled_by_its_spec(name, tmp_path):
    chart, space = _chart_xml(name, tmp_path)
    spec, payload = chart["svgAppearance"]["serverChartStyle"], chart["payload"]
    plot = space.find(f"{_C}chart/{_C}plotArea")
    series = plot.findall(f".//{_C}ser")
    assert len(series) == len(payload["series"])
    # Colours: every series (area included) in the drawing's colour; area fills keep their opacity.
    if payload["chartType"] in ("pie", "donut"):
        fills = [_val(point, f"{_C}spPr/{_A}solidFill/{_A}srgbClr") for point in series[0].findall(f"{_C}dPt")]
        assert fills == [color[1:] for color in payload["series"][0]["pointColors"]]
    else:
        fills = [_val(item, f"{_C}spPr/{_A}solidFill/{_A}srgbClr") for item in series]
        assert fills == [item["color"][1:] for item in payload["series"]]
    if payload["chartType"] == "area":
        assert all(_val(item, f"{_C}spPr/{_A}solidFill/{_A}srgbClr/{_A}alpha") == "30000" for item in series)
    # The page font on every text run.
    faces = {node.get("typeface") for node in space.iter(f"{_A}latin")}
    assert faces == {spec["fontFamily"]}
    # Value axis: bounds, ticks, gridlines and visibility as drawn.
    if "valueAxis" in spec:
        axis, value_axis = spec["valueAxis"], plot.find(f"{_C}valAx")
        assert float(_val(value_axis, f"{_C}scaling/{_C}max")) == axis["maximum"]
        assert float(_val(value_axis, f"{_C}scaling/{_C}min")) == 0
        assert (value_axis.find(f"{_C}majorGridlines") is not None) is axis["gridlines"]
        assert _val(value_axis, f"{_C}delete") == ("0" if axis["visible"] else "1")
        assert value_axis.find(f"{_C}numFmt").get("formatCode") == axis["numberFormat"]
        if "majorUnit" in axis:
            assert float(_val(value_axis, f"{_C}majorUnit")) == axis["majorUnit"]
    # Labels: shown or not, in the drawing's size, weight and number format.
    labels = spec["dataLabels"]
    shown = [_val(node, f"{_C}showVal") for node in plot.iter(f"{_C}dLbls")]
    if labels["shown"]:
        assert shown and set(shown) == {"1"}
        dlbls = next(plot.iter(f"{_C}dLbls"))
        size = dlbls.find(f".//{_A}defRPr")
        assert size.get("sz") == str(round(labels["px"] * 0.75 * 100))
        assert (size.get("b") == "1") is labels["bold"]
        assert dlbls.find(f"{_C}numFmt").get("formatCode") == labels["numberFormat"]
    else:
        assert not shown
    # Legend: present exactly when drawn.
    legend = space.find(f"{_C}chart/{_C}legend")
    assert (legend is not None) is spec["legend"]["visible"]
    if legend is not None:
        assert _val(legend, f"{_C}legendPos") == {"top": "t", "right": "r"}[spec["legend"]["position"]]
    if "holeRatio" in spec:
        assert _val(plot, f"{_C}doughnutChart/{_C}holeSize") == "58"
