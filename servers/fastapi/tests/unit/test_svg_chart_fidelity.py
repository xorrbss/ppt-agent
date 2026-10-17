"""Four drawn SVG charts through publication, the real export worker and PPTX readback."""
from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

from services.generation_core.drivers.svg.native_declarations import (
    bind_native_declarations, extract_native_declarations,
)
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.svg_contract import finalize_svg, validate_svg
from tests.unit.test_svg_native_objects_end_to_end import _export, _frames

COLORS = ["#2457D6", "#68A4FF", "#19A974", "#F2B134"]
VALUES = {"bar": [40, 50, 60, 70], "column": [50, 80, 60, 100],
          "line": [30, 50, 40, 70], "pie": [10, 20, 30, 40]}
CATEGORIES = ["Q1", "Q2", "Q3", "Q4"]
FONT = 'font-family="Arial" font-size="18" fill="#202020"'


def _text(x: float, y: float, value: str, *, centered: bool = False,
          color: str = "#202020") -> str:
    anchor = ' text-anchor="middle"' if centered else ""
    font = FONT.replace("#202020", color)
    return f'<text x="{x}" y="{y}" {font}{anchor}>{value}</text>'


def _group(kind: str, *, wrong_color: bool = False, wrong_slice: bool = False,
           wrong_label: bool = False, contrasting_labels: bool = False) -> str:
    values = VALUES[kind]
    labels = {"bar": (True, "insideEnd"), "column": (True, "outsideEnd"),
              "line": (False, None), "pie": (True, "center")}
    declaration = {"chartType": kind, "categories": CATEGORIES,
                   "series": [{"name": "Revenue", "values": values}],
                   "categoryOrder": "topToBottom" if kind == "bar" else
                   "clockwise" if kind == "pie" else "leftToRight",
                   "valueLabels": {"shown": labels[kind][0], "position": labels[kind][1]},
                   "markers": kind == "line", "title": f"{kind.title()} revenue"}
    if kind != "line":
        declaration["series"][0]["pointColors"] = COLORS
    parts = [_text(90, 85, declaration["title"]).replace('<text ', '<text font-weight="700" ', 1),
             _text(980, 135, "Revenue")]
    if kind in {"bar", "column"}:
        parts.append('<rect x="960" y="118" width="14" height="14" fill="#2457D6"/>')
    if kind == "bar":
        for i, (category, value) in enumerate(zip(CATEGORIES, values, strict=True)):
            y, width = 185 + 100 * i, value * 6
            color = "#AA0000" if wrong_color and i == 1 else COLORS[i]
            parts += [f'<rect x="240" y="{y}" width="{width}" height="40" fill="{color}"/>',
                      _text(165, y + 27, category, centered=True),
                      _text(240 + width + (30 if wrong_label and i == 0 else -36),
                            y + 27, str(value), centered=True,
                            color="#FFFFFF" if contrasting_labels else "#202020")]
    elif kind == "column":
        for i, (category, value) in enumerate(zip(CATEGORIES, values, strict=True)):
            x, height = 230 + 190 * i, value * 3
            parts += [f'<rect x="{x}" y="{600 - height}" width="70" height="{height}" fill="{COLORS[i]}"/>',
                      _text(x + 35, 635, category, centered=True),
                      _text(x + 35, 590 - height, str(value), centered=True)]
    elif kind == "line":
        points = [(260 + 180 * i, 600 - value * 4) for i, value in enumerate(values)]
        parts.append('<polyline points="' + " ".join(f"{x},{y}" for x, y in points) +
                     '" fill="none" stroke="#2457D6" stroke-width="5"/>')
        for i, (x, y) in enumerate(points):
            parts += [f'<circle cx="{x}" cy="{y}" r="7" fill="#2457D6"/>',
                      _text(x, 635, CATEGORIES[i], centered=True)]
    else:
        angle = -math.pi / 2
        for i, value in enumerate(values):
            sweep = 2 * math.pi * (0.25 if wrong_slice else value / sum(values))
            arc = [(550 + 180 * math.cos(angle + sweep * step / 24),
                    400 + 180 * math.sin(angle + sweep * step / 24)) for step in range(25)]
            points = " ".join(f"{x:.2f},{y:.2f}" for x, y in [(550, 400), *arc])
            middle = angle + sweep / 2
            label_xy = (550 + 100 * math.cos(middle), 400 + 100 * math.sin(middle) + 6)
            category_xy = (550 + 235 * math.cos(middle), 400 + 235 * math.sin(middle) + 6)
            parts += [f'<polygon points="{points}" fill="{COLORS[i]}"/>',
                      _text(*label_xy, str(value), centered=True),
                      _text(*category_xy, CATEGORIES[i], centered=True)]
            angle += sweep
    encoded = json.dumps(declaration, separators=(",", ":"))
    return f'<g id="native-chart-{kind}" data-native-chart=\'{encoded}\'>{"".join(parts)}</g>'


def _publish(group: str, index: int) -> tuple[str, dict]:
    from xml.etree import ElementTree as ET

    raw = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
           f'viewBox="0 0 1280 720"><rect width="1280" height="720" fill="#FFFFFF"/>{group}</svg>')
    root = ET.fromstring(raw)
    declarations = extract_native_declarations(root)
    authored = ET.tostring(root, encoding="unicode")
    validate_svg(authored)
    content = {"svg_content": authored}
    bind_native_declarations(content, authored, declarations)
    content["svg_content"] = finalize_svg(authored, index=index, n=4)
    sidecar, receipt = page_native_objects(content, pt_per_px=0.75)
    content["nativeObjects"], content["svgReceipt"] = sidecar, {"nativeObjects": receipt}
    return content["svg_content"], content


def _render_evidence(directory: Path, svgs: list[str], deck) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pptx = directory / "chart-fidelity.pptx"
    deck.save(pptx)
    office = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")
    profile = (directory / "lo-profile").as_uri()
    command = [str(office), f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf",
               "--outdir", str(directory)]
    subprocess.run([*command, str(pptx)], check=True, capture_output=True, timeout=90)
    with pdfium.PdfDocument(directory / "chart-fidelity.pdf") as pdf:
        for index, svg in enumerate(svgs, 1):
            source = directory / f"source-slide-{index}.svg"
            source.write_text(svg, encoding="utf-8")
            subprocess.run([*command, str(source)], check=True, capture_output=True, timeout=90)
            with pdfium.PdfDocument(directory / f"source-slide-{index}.pdf") as source_pdf:
                source_image = source_pdf[0].render(scale=2).to_pil().convert("RGB")
            slide_image = pdf[index - 1].render(scale=2).to_pil().convert("RGB")
            source_image.save(directory / f"source-slide-{index}.png")
            slide_image.save(directory / f"slide-{index}.png")
            source_image = source_image.resize(slide_image.size)
            pair = Image.new("RGB", (slide_image.width * 2, slide_image.height), "white")
            pair.paste(source_image, (0, 0))
            pair.paste(slide_image, (slide_image.width, 0))
            pair.save(directory / f"pair-{index}.png")


def test_chart_appearance_survives_worker_and_pptx_readback(tmp_path):
    kinds = ("bar", "column", "line", "pie")
    published = [_publish(_group(kind, contrasting_labels=True), index)
                 for index, kind in enumerate(kinds)]
    svgs, contents = map(list, zip(*published, strict=True))
    assert all(content["svgReceipt"]["nativeObjects"]["accepted"] == 1 for content in contents)
    # Existing snapshots can freeze a chart-wide colour from white in-bar labels.
    # Re-export must recover semantic colours from their unchanged SVG drawing.
    for content in contents:
        content["nativeObjects"]["objects"][0]["payload"]["textColor"] = "#FFFFFF"
    deck, evidence = _export(tmp_path, svgs, contents)
    assert evidence["structuralReadback"]["nativeObjects"]["native"] == 4
    assert all(content["nativeObjects"]["objects"][0]["payload"]["textColor"] == "#FFFFFF"
               and content["svg_content"] == svgs[index] for index, content in enumerate(contents))
    for index, kind in enumerate(kinds):
        (shape,) = [item for item in _frames(deck.slides[index]) if item.has_chart]
        chart = shape.chart
        assert list(chart.plots[0].categories) == CATEGORIES
        assert list(chart.plots[0].series[0].values) == VALUES[kind]
        xml = chart._element
        if kind == "bar":
            assert not chart.has_title
            (title,) = [item for item in _frames(deck.slides[index])
                        if item.has_text_frame and item.text_frame.text == f"{kind.title()} revenue"]
            assert title.text_frame.paragraphs[0].runs[0].font.bold
            assert abs(title.left / 9525 - 90) < 1
            assert (title.top + title.height) < shape.top
            assert not xml.xpath(".//c:plotArea/c:layout/c:manualLayout")
            assert not xml.xpath(".//c:valAx/c:scaling/c:min|.//c:valAx/c:scaling/c:max")
        else:
            assert chart.has_title and chart.chart_title.text_frame.text == f"{kind.title()} revenue"
            assert len(xml.xpath(".//c:title/c:overlay")) == 1
            assert {node.get("val") for node in xml.xpath(".//c:title//a:srgbClr")} == {"202020"}
            assert xml.xpath(".//c:title//a:rPr[@b='1']")
            assert xml.xpath(".//c:title//a:pPr[@algn='l']")
        if kind == "column":
            assert xml.xpath(".//c:title/c:layout/c:manualLayout")
            assert xml.xpath(".//c:title/c:overlay[@val='1']")
        elif kind != "bar":
            assert not xml.xpath(".//c:title/c:layout/c:manualLayout")
            assert xml.xpath(".//c:title/c:overlay[@val='0']")
        if kind != "pie":
            if kind == "line":
                assert chart.has_legend
                assert xml.xpath(".//c:legend/c:overlay[@val='0']")
                assert not xml.xpath(".//c:legend/c:layout/c:manualLayout")
            else:
                assert not chart.has_legend
                labels = [shape for shape in _frames(deck.slides[index])
                          if shape.has_text_frame and shape.text_frame.text == "Revenue"]
                assert len(labels) == 1
                label = labels[0]
                assert label.text_frame.paragraphs[0].runs[0].font.name == "Arial"
                assert abs(label.left / 9525 - 980) < 1
                if kind == "bar":
                    assert label.top < shape.top
                    # Editable text frames include slack below the source ink.
                    assert abs(shape.top / 9525 - 139.5) < 1
                keys = [shape for shape in _frames(deck.slides[index])
                        if abs(shape.left / 9525 - 960) < 1 and abs(shape.top / 9525 - 118) < 1
                        and abs(shape.width / 9525 - 14) < 1 and abs(shape.height / 9525 - 14) < 1]
                assert len(keys) == 1 and str(keys[0].fill.fore_color.rgb) == "2457D6"
            assert {node.get("val") for node in xml.xpath(".//c:catAx/c:txPr//a:srgbClr")} == {"202020"}
            assert len(xml.xpath(".//c:catAx/c:delete[@val='0']")) == 1
            assert len(xml.xpath(".//c:valAx/c:delete[@val='1']")) == 1
            assert not xml.xpath(".//c:majorGridlines")
        if kind != "line":
            fills = xml.xpath(".//c:ser/c:dPt/c:spPr/a:solidFill/a:srgbClr")
            assert ["#" + point.get("val") for point in fills] == COLORS
            assert len(xml.xpath(".//c:dLbls/c:showVal[@val='1']")) >= 1
        if kind in {"bar", "column"}:
            assert xml.xpath(".//c:barChart/c:varyColors[@val='0']")
            assert xml.xpath(".//c:ser/c:tx/c:strRef/c:strCache/c:pt/c:v")[0].text == "Revenue"
        if kind == "column":
            assert xml.xpath(".//c:plotArea/c:layout/c:manualLayout/c:layoutTarget[@val='inner']")
            assert xml.xpath(".//c:plotArea/c:layout/c:manualLayout/c:xMode[@val='edge']")
            assert xml.xpath(".//c:plotArea/c:layout/c:manualLayout/c:yMode[@val='edge']")
            assert float(xml.xpath(".//c:valAx/c:scaling/c:max")[0].get("val")) == max(VALUES[kind])
            assert float(xml.xpath(".//c:valAx/c:scaling/c:min")[0].get("val")) == 0
        if kind == "bar":
            assert {node.get("val") for node in xml.xpath(".//c:dLbls/c:txPr//a:srgbClr")} == {"FFFFFF"}
            assert len(xml.xpath(".//c:catAx/c:scaling/c:orientation[@val='maxMin']")) == 1
            assert len(xml.xpath(".//c:valAx/c:crosses[@val='max']")) == 1
        if kind == "pie":
            assert len(xml.xpath(".//c:dLbls/c:showCatName[@val='1']")) == 1
        if kind == "line":
            assert len(xml.xpath(".//c:ser/c:marker/c:symbol[@val='circle']")) == 1
            assert int(xml.xpath(".//c:ser/c:marker/c:size")[0].get("val")) >= 10
            assert int(xml.xpath(".//c:ser/c:spPr/a:ln")[0].get("w")) == 47625
    if os.getenv("Y2_CHART_EVIDENCE_DIR"):
        _render_evidence(Path(os.environ["Y2_CHART_EVIDENCE_DIR"]), svgs, deck)


@pytest.mark.parametrize("kind", ["column", "bar"])
@pytest.mark.parametrize("canvas", [(1280, 720), (720, 1280)])
def test_retained_axis_furniture_proves_scale_and_category_frame(kind, canvas, tmp_path):
    from copy import deepcopy
    from types import SimpleNamespace
    from xml.etree import ElementTree as ET
    from services.generation_core.drivers.svg.export_chart_style import _retained_axis_plot
    from services.generation_core.drivers.svg.native_geometry import Box, DrawnText
    from services.generation_core.native_objects import NativeAnchor

    horizontal = kind == "bar"
    values, centers = [12, 20, 16], [160, 260, 360]
    anchor = NativeAnchor(100, 100, 200 if horizontal else 280, 450 if not horizontal else 300)
    item = SimpleNamespace(id="chart-proven", anchor=anchor, payload={"series": [{"values": values}]})
    plots = [SimpleNamespace(box=Box(200, center - 20, 200 + value * 4, center + 20)
                             if horizontal else Box(center - 20, 500 - value * 4, center + 20, 500))
             for center, value in zip(centers, values, strict=True)]
    categories = [DrawnText((f"C{index}",), ((170, center + 6.75) if horizontal else (center, 540),),
                           18, "Arial", "#202020", "normal", "middle", True)
                  for index, center in enumerate(centers)]
    lines = []
    for value in (0, 10, 24):  # Unequal tick intervals still prove one linear scale.
        position = 200 + value * 4 if horizontal else 500 - value * 4
        if horizontal:
            lines += [f'<line x1="{position}" y1="110" x2="{position}" y2="410" stroke="#202020"/>',
                      _text(position, 435, str(value), centered=True)]
        else:
            lines += [f'<line x1="110" y1="{position}" x2="410" y2="{position}" stroke="#202020"/>',
                      _text(95, position + 5, str(value)).replace('<text ', '<text text-anchor="end" ', 1)]
    root = ET.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas[0]}" height="{canvas[1]}">'
                         + ''.join(lines) + '</svg>')
    frozen = deepcopy(item)
    projected, axes = {"type": kind}, {"category": {}, "value": {}}
    source = _retained_axis_plot(projected, item, root, plots, categories, axes)
    assert source is not None and len(source) == len(categories)
    assert item.anchor == frozen.anchor and item.payload == frozen.payload
    assert axes["value"]["minimum"] == 0 and axes["value"]["maximum"] == 24
    assert not axes["value"]["visible"] and not axes["category"]["visible"]
    assert projected["gap_width"] == 150
    layout = projected["plot_layout"]
    bounds = (projected["x"] + layout["x"] * projected["width"],
              projected["y"] + layout["y"] * projected["height"],
              layout["w"] * projected["width"], layout["h"] * projected["height"])
    assert bounds == pytest.approx((200, 110, 96, 300) if horizontal else (110, 404, 300, 96))
    # Wrong source ticks, shifted bars and mixed-sign data retain automatic semantics.
    invalid_tick = ET.fromstring(ET.tostring(root).decode().replace('>10<', '>11<'))
    for invalid_root, invalid_plots, invalid_values in (
            (invalid_tick, plots, values),
            (root, [*plots[:-1], SimpleNamespace(box=Box(0, 0, 10, 10))], values),
            (root, plots, [12, -20, 16])):
        item.payload["series"][0]["values"] = invalid_values
        untouched, auto_axes = {"type": kind}, {"category": {}, "value": {}}
        assert _retained_axis_plot(untouched, item, invalid_root, invalid_plots, categories, auto_axes) is None
        assert untouched == {"type": kind} and auto_axes == {"category": {}, "value": {}}

    if kind == "column" and canvas == (1280, 720):
        # Sparse 0/4/7 ticks are outside the chart while their grid is inside.
        # All six bars share the actual 26px/unit scale and the zero baseline.
        from services.generation_core.native_objects import NativeObjects
        from services.generation_core.drivers.svg.export_projection import converter_payload
        from services.generation_core.drivers.svg.export_chart_style import apply_drawn_chart_style
        values = [4.75, 6.35, 5.35, 5.6, 5.1, 3.75]
        labels = [f"M{i + 1}" for i in range(6)]
        declaration = {"chartType": "column", "categories": labels,
            "series": [{"name": "Monthly work", "values": values}],
            "categoryOrder": "leftToRight", "valueLabels": {"shown": True, "position": "outsideEnd"},
            "markers": False}
        parts = [_text(125, 247, "Monthly work"),
                 *[f'<line id="source-grid-{y}" x1="125" y1="{y}" x2="809" y2="{y}" stroke="#CCCCCC" stroke-width="1"/>'
                   for y in (270, 348, 452)]]
        for i, value in enumerate(values):
            center, height = 182 + i * 114, value * 26
            parts += [f'<rect x="{center - 37}" y="{452 - height}" width="74" height="{height}" fill="#2457D6"/>',
                      _text(center, 442 - height, str(value), centered=True),
                      _text(center, 475, labels[i], centered=True)]
        ticks = ''.join(_text(109, y + 5, label).replace('<text ',
            f'<text id="axis-tick-{i}" text-anchor="end" ', 1)
            for i, (label, y) in enumerate((("7.0", 270), ("4.0", 348), ("0", 452))))
        group = ('<g id="native-chart-sparse" data-native-chart=\'' + json.dumps(declaration)
                 + '\'>' + ''.join(parts) + '</g>' + ticks)
        svg, content = _publish(group, 0)
        assert content["svgReceipt"]["nativeObjects"] == {"declared": 1, "accepted": 1, "dropped": []}
        chart = NativeObjects.from_wire(content["nativeObjects"]).objects[0]
        source_root = ET.fromstring(svg)
        frozen_svg, frozen_content = ET.tostring(source_root), deepcopy(content)

        def project(candidate_root):
            result = converter_payload(chart)
            apply_drawn_chart_style(result, chart, candidate_root)
            return result

        projected = project(source_root)
        assert projected["axes"]["value"]["minimum"] == 0
        assert projected["axes"]["value"]["maximum"] == 7
        assert projected["plot_layout"] and not projected["axes"]["category"]["visible"]
        assert not projected["axes"]["value"]["visible"]
        assert ET.tostring(source_root) == frozen_svg and content == frozen_content
        underlays = []
        apply_drawn_chart_style(converter_payload(chart), chart, source_root,
                               source_underlays=underlays)
        assert len(underlays) == 1
        retained = list(underlays[0].iter())
        assert len([node for node in retained if node.tag.rsplit("}", 1)[-1] == "line"]) == 3
        assert not any(key == "id" or key.startswith(("data-native-", "data-pptx-"))
                       for node in retained for key in node.attrib)
        deck, evidence = _export(tmp_path, [svg], [content])
        assert evidence["structuralReadback"]["nativeObjects"] == {
            "offered": 1, "native": 1, "fellBack": 0, "reasons": []}
        (frame,) = [shape for shape in _frames(deck.slides[0]) if shape.has_chart]
        slide_xml = deck.slides[0]._element
        tree = list(slide_xml.iter())
        line_shapes = [shape._element for shape in _frames(deck.slides[0])
                       if shape.name.startswith("Line ")
                       and shape._element.xpath("./p:spPr/a:custGeom")]
        assert len(line_shapes) == 3
        for node, y in zip(line_shapes, (270, 348, 452), strict=True):
            xfrm = node.xpath("./p:spPr/a:xfrm")[0]
            assert int(xfrm.find("{http://schemas.openxmlformats.org/drawingml/2006/main}off").get("x")) == 125 * 9525
            assert int(xfrm.find("{http://schemas.openxmlformats.org/drawingml/2006/main}off").get("y")) == y * 9525
            assert int(xfrm.find("{http://schemas.openxmlformats.org/drawingml/2006/main}ext").get("cx")) == 684 * 9525
            assert int(xfrm.find("{http://schemas.openxmlformats.org/drawingml/2006/main}ext").get("cy")) == 9525
            assert node.xpath("./p:spPr/a:custGeom/a:pathLst/a:path/a:moveTo/a:pt[@x='0'][@y='0']")
            assert node.xpath("./p:spPr/a:custGeom/a:pathLst/a:path/a:lnTo/a:pt[@x='6515100'][@y='0']")
            assert node.xpath("./p:spPr/a:ln[@w='9525']/a:solidFill/a:srgbClr[@val='CCCCCC']")
            assert tree.index(node) < tree.index(frame._element)
        assert frame.chart._element.xpath("./c:spPr/a:noFill")
        assert frame.chart._element.xpath("./c:chart/c:plotArea/c:spPr/a:noFill")
        assert ET.tostring(source_root) == frozen_svg and content == frozen_content
        from services.generation_core.drivers.svg.native_geometry import NativeRejection
        after_bar = deepcopy(source_root)
        chart_group = next(node for node in after_bar.iter() if node.get("id") == chart.id)
        first_grid = next(node for node in chart_group if node.tag.rsplit("}", 1)[-1] == "line")
        chart_group.remove(first_grid)
        chart_group.append(first_grid)
        with pytest.raises(NativeRejection, match="native_chart_grid_underlay_unproven"):
            project(after_bar)
        from dataclasses import replace
        from services.generation_core.drivers.svg.export_chart_style import _proven_grid_underlay
        from services.generation_core.drivers.svg.native_chart import CHART_ELEMENTS
        from services.generation_core.drivers.svg.native_chart_visual import _bar_matrix, _category_texts
        from services.generation_core.drivers.svg.native_geometry import collect_primitives, find_group
        chart_group, offset, inherited = find_group(source_root, chart.id)
        primitives = collect_primitives(chart_group, offset, inherited, CHART_ELEMENTS)
        bars = [bar for series in _bar_matrix(chart.payload, primitives,
                _category_texts(chart.payload, primitives)) for bar in series]
        grid = [shape for shape in primitives.shapes if shape.tag == "line"]
        wrong_paint = replace(grid[0], stroke="#000000")
        with pytest.raises(NativeRejection, match="native_chart_grid_underlay_unproven"):
            _proven_grid_underlay(source_root, chart, bars, [wrong_paint], [wrong_paint])
        duplicate = deepcopy(source_root)
        duplicate_group = next(node for node in duplicate.iter() if node.get("id") == chart.id)
        duplicate_group.insert(0, deepcopy(next(node for node in duplicate_group
                                              if node.tag.rsplit("}", 1)[-1] == "line")))
        with pytest.raises(NativeRejection, match="native_chart_grid_underlay_unproven"):
            _proven_grid_underlay(duplicate, chart, bars, grid, grid)
        for violation in ("missing_ticks", "ambiguous_tick", "wrong_scale", "bounds"):
            changed = deepcopy(source_root)
            tick = next(node for node in changed if node.get("id") == "axis-tick-1")
            if violation == "missing_ticks":
                for node in list(changed):
                    if node.get("id", "").startswith("axis-tick-"):
                        changed.remove(node)
            elif violation == "ambiguous_tick":
                changed.append(deepcopy(tick))
            elif violation == "wrong_scale":
                tick.text = "4.1"
            else:
                changed.set("width", "800")
            rejected_underlays = []
            result = converter_payload(chart)
            apply_drawn_chart_style(result, chart, changed, source_underlays=rejected_underlays)
            assert "maximum" not in result.get("axes", {}).get("value", {})
            assert not rejected_underlays


def test_shifted_or_inconsistent_bar_scale_keeps_automatic_axis_and_plot():
    from types import SimpleNamespace
    from services.generation_core.drivers.svg.export_chart_style import _proportional_bar_plot
    from services.generation_core.drivers.svg.native_geometry import Box

    categories = [SimpleNamespace(points=((100, 300),)), SimpleNamespace(points=((200, 300),))]
    for values, bounds in (([10, 20], (Box(80, 190, 120, 200), Box(180, 180, 220, 210))),
                           ([10, 20], (Box(80, 195, 120, 200), Box(180, 185, 220, 200))),
                           ([10, 10], (Box(80, 190, 120, 200), Box(180, 190, 220, 200)))):
        item = SimpleNamespace(payload={"series": [{"values": values}]})
        projected, axes = {"type": "column"}, {"value": {}}
        _proportional_bar_plot(projected, item, [SimpleNamespace(box=box) for box in bounds], categories, axes)
        assert projected == {"type": "column"} and axes == {"value": {}}


@pytest.mark.parametrize("grid_y", [(596, 426, 256), (596, 400, 256), (596, 400, 220)])
def test_source_grid_preserves_plot_and_legend_zone_through_worker(tmp_path, grid_y):
    from copy import deepcopy

    values, categories = [240, 360, 520, 740], ["1월", "2월", "3월", "4월"]
    colors = ["#219F88", "#219F88", "#08916E", "#08916E"]
    declaration = {"chartType": "column", "categories": categories,
                   "series": [{"name": "주문 건수", "values": values, "pointColors": colors}],
                   "categoryOrder": "leftToRight", "valueLabels": {"shown": True, "position": "outsideEnd"},
                   "title": "월별 주문 계획 (단위: 건)"}
    parts = ['<rect x="64" y="184" width="1060" height="490" fill="#FFFFFF"/>',
             _text(100, 236, declaration["title"]).replace('font-size="18"', 'font-size="23"'),
             _text(995, 232, "주문 건수").replace('font-size="18"', 'font-size="16"'),
             '<rect x="970" y="218" width="15" height="15" fill="#08916E"/>']
    for y in grid_y:
        parts.append(f'<line x1="137" y1="{y}" x2="1052" y2="{y}" stroke="#EDF3F1"/>')
    for index, (x, y, height, label_y) in enumerate(((183, 494, 102, 478), (416, 443, 153, 427),
                                                  (649, 375, 221, 359), (882, 281, 315, 265))):
        parts.extend((f'<rect x="{x}" y="{y}" width="126" height="{height}" fill="{colors[index]}"/>',
                      _text(x + 63, label_y, str(values[index]), centered=True).replace('font-size="18"', 'font-size="23"'),
                      _text(x + 63, 640, categories[index], centered=True).replace('font-size="18"', 'font-size="20"')))
    group = '<g id="native-chart-grid" data-native-chart=\'' + json.dumps(declaration) + '\'>' + ''.join(parts) + '</g>'
    svg, content = _publish(group, 0)
    frozen = deepcopy(content)
    assert content["svgReceipt"]["nativeObjects"]["accepted"] == 1
    deck, evidence = _export(tmp_path, [svg], [content])
    assert content == frozen and evidence["structuralReadback"]["nativeObjects"]["native"] == 1
    (shape,) = [shape for shape in _frames(deck.slides[0]) if shape.has_chart]
    chart, xml = shape.chart, shape.chart._element
    assert list(chart.plots[0].categories) == categories
    assert list(chart.plots[0].series[0].values) == values
    assert ["#" + node.get("val") for node in xml.xpath(".//c:ser/c:dPt/c:spPr/a:solidFill/a:srgbClr")] == colors
    if grid_y != (596, 426, 256):
        assert not chart.has_legend
        if grid_y == (596, 400, 220):
            # A source line prevents frame reservation; keep only the proven
            # editable series legend and let Office reserve native title room.
            assert chart.has_title and chart.chart_title.text_frame.text == declaration["title"]
            assert not xml.xpath(".//c:plotArea/c:layout/c:manualLayout")
            assert not xml.xpath(".//c:valAx/c:scaling/c:max")
            assert not xml.xpath(".//c:title/c:layout/c:manualLayout")
            assert xml.xpath(".//c:title/c:overlay[@val='0']")
            (legend,) = [item for item in _frames(deck.slides[0])
                        if item.has_text_frame and item.text_frame.text == "주문 건수"]
            assert abs(legend.left / 9525 - 995) < 1
            assert abs(shape.top / 9525 - 184) < 1
            assert abs((shape.top + shape.height) / 9525 - 674) < 1
            return
        assert not chart.has_title
        assert not xml.xpath(".//c:plotArea/c:layout/c:manualLayout")
        assert not xml.xpath(".//c:valAx/c:scaling/c:max")
        assert not xml.xpath(".//c:title/c:layout/c:manualLayout")
        assert not xml.xpath(".//c:legend/c:layout/c:manualLayout")
        (title_shape,) = [item for item in _frames(deck.slides[0])
                         if item.has_text_frame and item.text_frame.text == declaration["title"]]
        (legend,) = [item for item in _frames(deck.slides[0])
                    if item.has_text_frame and item.text_frame.text == "주문 건수"]
        assert title_shape.top < shape.top and legend.top < shape.top
        assert abs(shape.top / 9525 - 241.75) < 1
        assert abs((shape.top + shape.height) / 9525 - 674) < 1
        return
    assert not chart.has_legend
    assert float(xml.xpath(".//c:valAx/c:scaling/c:max")[0].get("val")) == 800
    assert float(xml.xpath(".//c:valAx/c:majorUnit")[0].get("val")) == 400
    layout = xml.xpath(".//c:plotArea/c:layout/c:manualLayout")[0]
    coords = {name: float(layout.xpath(f"./c:{name}")[0].get("val")) for name in ("x", "y", "w", "h")}
    left, top, width, height = (shape.left / 9525, shape.top / 9525, shape.width / 9525, shape.height / 9525)
    assert abs(left + coords["x"] * width - 137) < 1
    plot_top = top + coords["y"] * height
    assert abs(plot_top - 256) < 1
    assert abs(top + (coords["y"] + coords["h"]) * height - 596) < 1
    (legend,) = [shape for shape in _frames(deck.slides[0]) if shape.has_text_frame and shape.text_frame.text == "주문 건수"]
    assert (legend.top + legend.height) / 9525 < plot_top - 10


@pytest.mark.parametrize("source_frame", [False, True, "occupied"])
def test_source_baseline_preserves_plot_at_frozen_anchor_edge_through_worker(tmp_path, source_frame):
    from copy import deepcopy

    values, categories = [240, 360, 520, 740], ["1월", "2월", "3월", "4월"]
    colors = ["#9BDCCE", "#59BAA4", "#219F88", "#08916E"]
    title = "월별 주문 계획 · 단위: 건"
    declaration = {"chartType": "column", "categories": categories,
                   "series": [{"name": "주문 계획", "values": values, "pointColors": colors}],
                   "categoryOrder": "leftToRight", "valueLabels": {"shown": True, "position": "insideEnd"},
                   "title": title}
    parts = [_text(93, 267, title).replace('font-size="18"', 'font-size="22"').replace('<text ', '<text font-weight="700" ', 1),
             '<rect x="94" y="288" width="16" height="16" fill="#08916E"/>',
             _text(120, 303, "주문 계획").replace('font-size="18"', 'font-size="17"'),
             '<line x1="111" y1="695" x2="1077" y2="695" stroke="#B5CCC5"/>']
    for index, (x, y, height) in enumerate(((158, 587, 108), (400, 533, 162), (642, 461, 234), (884, 362, 333))):
        parts.extend((f'<rect x="{x}" y="{y}" width="154" height="{height}" fill="{colors[index]}"/>',
                      _text(x + 77, y + 37, str(values[index]), centered=True).replace('font-size="18"', 'font-size="26"'),
                      _text(x + 77, 729, categories[index], centered=True).replace('font-size="18"', 'font-size="20"')))
    # Translate the real 840px-high source geometry into the existing 720px
    # worker fixture; its relative geometry and frozen horizontal bounds agree.
    group = ('<g id="native-chart-baseline" transform="translate(0,-40)" data-native-chart=\''
             + json.dumps(declaration) + '\'>' + ''.join(parts) + '</g>')
    if source_frame:
        group = '<rect x="54" y="173" width="1080" height="542" fill="#FFFFFF"/>' + group
    if source_frame == "occupied":
        group += '<rect x="200" y="700" width="30" height="8" fill="#000000"/>'
    svg, content = _publish(group, 0)
    assert content["svgReceipt"]["nativeObjects"]["accepted"] == 1
    content["nativeObjects"]["objects"][0]["anchor"] = {"x": 93, "y": 205, "width": 984, "height": 489}
    frozen = deepcopy(content)
    deck, evidence = _export(tmp_path, [svg], [content])
    assert content == frozen and evidence["structuralReadback"]["nativeObjects"]["native"] == 1
    (shape,) = [shape for shape in _frames(deck.slides[0]) if shape.has_chart]
    chart, xml = shape.chart, shape.chart._element
    assert list(chart.plots[0].categories) == categories
    assert list(chart.plots[0].series[0].values) == values
    assert ["#" + node.get("val") for node in xml.xpath(".//c:ser/c:dPt/c:spPr/a:solidFill/a:srgbClr")] == colors
    assert not chart.has_legend
    assert xml.xpath(".//c:title/c:layout/c:manualLayout")
    assert xml.xpath(".//c:title//a:rPr[@b='1']") and xml.xpath(".//c:title//a:pPr[@algn='l']")
    assert float(xml.xpath(".//c:valAx/c:scaling/c:max")[0].get("val")) == 740
    layout = xml.xpath(".//c:plotArea/c:layout/c:manualLayout")[0]
    coords = {name: float(layout.xpath(f"./c:{name}")[0].get("val")) for name in ("x", "y", "w", "h")}
    left, top, width, height = (shape.left / 9525, shape.top / 9525, shape.width / 9525, shape.height / 9525)
    assert abs(height - (510 if source_frame is True else 489)) < 1
    assert {node.get("sz") for node in xml.xpath(".//c:catAx/c:txPr//a:defRPr")} == {"1500"}
    assert xml.xpath(".//c:catAx/c:lblOffset[@val='100']")
    assert abs(left + coords["x"] * width - 111) < 1
    assert abs(left + (coords["x"] + coords["w"]) * width - 1077) < 1
    assert abs(top + coords["y"] * height - 322) < 1
    assert abs(top + (coords["y"] + coords["h"]) * height - 655) < 1
    if source_frame is True:
        assert abs(top + height - 715) < 1
    (legend,) = [shape for shape in _frames(deck.slides[0]) if shape.has_text_frame and shape.text_frame.text == "주문 계획"]
    assert (legend.top + legend.height) / 9525 < top + coords["y"] * height


def test_irregular_source_grid_keeps_automatic_plot():
    from types import SimpleNamespace
    from services.generation_core.drivers.svg.export_chart_style import _proportional_bar_plot
    from services.generation_core.drivers.svg.native_geometry import Box

    item = SimpleNamespace(payload={"series": [{"values": [10, 20]}]})
    plots = [SimpleNamespace(box=Box(80, 190, 120, 200)), SimpleNamespace(box=Box(180, 180, 220, 200))]
    categories = [SimpleNamespace(points=((100, 300),)), SimpleNamespace(points=((200, 300),))]
    lines = [SimpleNamespace(box=Box(60, y, 240, y)) for y in (200, 170, 100)]
    projected, axes = {"type": "column"}, {"value": {"major_gridlines": True}}
    _proportional_bar_plot(projected, item, plots, categories, axes, grid_lines=lines)
    assert projected == {"type": "column"} and axes == {"value": {"major_gridlines": True}}


@pytest.mark.parametrize("kind", ["column", "bar"])
def test_native_clustered_grid_and_category_slots_share_one_extent(kind):
    from services.generation_core.drivers.svg.native_chart import _model_chart, _source_plot_geometry
    from services.generation_core.drivers.svg.native_geometry import (
        Box, DrawnText, GroupPrimitives, NativeRejection, Shape,
    )

    horizontal = kind == "bar"
    centers = (80, 240, 400)
    colors = ("#2457D6", "#19A974")
    bars = []
    for center in centers:
        for offset, color in ((-30, colors[0]), (5, colors[1])):
            box = (Box(150, center + offset, 400, center + offset + 25)
                   if horizontal else Box(center + offset, 150, center + offset + 25, 400))
            bars.append(Shape("rect", box, color, None))
    labels = tuple(
        DrawnText((f"Q{index}",), ((0, center + 6) if horizontal else (center, 450),),
                  16, "Arial", "#202020", "normal", "middle", True)
        for index, center in enumerate(centers, 1)
    )
    payload = {"chartType": kind, "grouping": "clustered", "categories": ["Q1", "Q2", "Q3"],
               "series": [{"name": "A", "values": [1, 2, 3]},
                          {"name": "B", "values": [2, 3, 4]}],
               "categoryOrder": "topToBottom" if horizontal else "leftToRight",
               "valueLabels": {"shown": False}, "markers": False}

    def primitives(grid_extent):
        grid = [Shape("line", Box(value, 0, value, grid_extent) if horizontal
                      else Box(0, value, grid_extent, value), None, "#CCCCCC")
                for value in (100, 200, 300)]
        return GroupPrimitives(tuple([*bars, *grid]), labels, frozenset({"rect", "line", "text"}))

    assert _model_chart({**payload, "geometryContract": "native-chart-coherent-grid/v1"})
    with pytest.raises(NativeRejection, match="native_chart_json_invalid"):
        _model_chart({**payload, "series": payload["series"][:1],
                      "geometryContract": "native-chart-coherent-grid/v1"})
    coherent = _source_plot_geometry(payload, primitives(480), Box(0, 0, 500, 500), coherent_grid=True)
    assert coherent is not None
    layout, overlap, gap = coherent
    assert abs(layout["h" if horizontal else "w"] * 500 - 480) < 1
    assert overlap == -40 and gap == 400
    # Declarations without the new contract retain the existing v1 projection.
    assert _source_plot_geometry(payload, primitives(490), Box(0, 0, 500, 500)) is not None
    with pytest.raises(NativeRejection, match="native_chart_grid_category_geometry_conflict"):
        _source_plot_geometry(payload, primitives(490), Box(0, 0, 500, 500), coherent_grid=True)


def test_manual_chart_layout_rejects_nonfinite_and_outside_bounds(monkeypatch):
    import pytest
    from workers.public_svg_pptx_convert import VENDOR_ROOT

    monkeypatch.syspath_prepend(str(VENDOR_ROOT))
    from svg_to_pptx.native_objects.chart_xml import _chart_manual_layout_xml, _chart_title_xml

    for layout in ({"x": float("nan"), "y": 0, "w": .5, "h": .5},
                   {"x": .6, "y": 0, "w": .5, "h": .5}):
        with pytest.raises(RuntimeError):
            _chart_manual_layout_xml(layout)
    # No new options preserves the existing title XML defaults.
    legacy = _chart_title_xml("Title", font_size=1200)
    assert '<c:layout/>' in legacy and 'manualLayout' not in legacy
    assert ' b=' not in legacy and '<a:pPr' not in legacy


def test_wrong_declared_point_color_uses_the_drawn_color():
    svg, content = _publish(_group("bar", wrong_color=True), 0)
    assert content["svgReceipt"]["nativeObjects"]["accepted"] == 1
    assert content["nativeObjects"]["objects"][0]["payload"]["series"][0]["pointColors"][1] == "#AA0000"
    assert "#AA0000" in svg


def test_wrong_pie_sector_sizes_keep_the_svg_drawing():
    svg, content = _publish(_group("pie", wrong_slice=True), 0)
    assert content["nativeObjects"] is None
    assert content["svgReceipt"]["nativeObjects"]["dropped"][0]["reason"] == "native_chart_slice_value_mismatch"
    assert "<polygon" in svg


def test_wrong_value_label_position_keeps_the_svg_drawing():
    svg, content = _publish(_group("bar", wrong_label=True), 0)
    assert content["nativeObjects"] is None
    assert content["svgReceipt"]["nativeObjects"]["dropped"][0]["reason"] == (
        "native_chart_value_label_position_mismatch")
    assert ">40</" in svg


def test_wrong_declared_value_drops_the_native_annotation():
    for value in (True, None, "50", float("nan"), float("inf"), -float("inf"), 10 ** 400, []):
        group = _group("column").replace(
            '"values":[50,80,60,100]', '"values":' + json.dumps([value, 80, 60, 100])
        )
        svg, content = _publish(group, 0)
        assert content["nativeObjects"] is None
        assert content["svgReceipt"]["nativeObjects"]["dropped"][0]["reason"] == "native_chart_json_invalid"
        assert ">50</" in svg

    # The author uses compact JSON; change only the declaration, not drawn text.
    group = _group("column").replace('"values":[50,80,60,100]', '"values":[51,80,60,100]')
    _, content = _publish(group, 0)
    assert content["nativeObjects"] is None
    assert content["svgReceipt"]["nativeObjects"]["dropped"][0]["reason"] in {
        "native_chart_text_unaccounted", "native_chart_value_geometry_mismatch",
        "native_chart_value_labels_mismatch"}

    # Single-series title candidates keep the final source-unit guard mandatory.
    from copy import deepcopy
    from xml.etree import ElementTree as ET
    from services.generation_core.drivers.svg.native_requirements import apply_requested_native_quality

    base = ET.fromstring(_group("column"))
    declaration = json.loads(base.attrib["data-native-chart"])
    name = declaration["series"][0]["name"]
    source = "차트: " + ", ".join(
        f"{category} {value}"
        for category, value in zip(declaration["categories"], declaration["series"][0]["values"])
    ) + ". 단위: 건"

    def candidate(unit, *, qualifier="", multiple=False, duplicate=False):
        root = deepcopy(base)
        raw = deepcopy(declaration)
        title = f"{name} · 단위: {unit}{qualifier}"
        old_title = raw["title"]
        raw["title"] = title
        if multiple:
            raw["series"].append({"name": "Expense", "values": list(raw["series"][0]["values"])})
        for text in list(root):
            if text.tag == "text" and "".join(text.itertext()) == name:
                root.remove(text)
            elif text.tag == "text" and "".join(text.itertext()) == old_title:
                text.text = title
                if duplicate:
                    root.append(deepcopy(text))
        root.attrib["data-native-chart"] = json.dumps(raw)
        return ET.tostring(root, encoding="unicode")

    for unit, accepted in [("건", True), ("분", False)]:
        svg, content = _publish(candidate(unit), 0)
        assert len(content["nativeObjects"]["objects"]) == 1
        # The correct unit elsewhere does not excuse a conflicting chart title.
        quality = {"passed": True, "verified": True, "failures": []}
        apply_requested_native_quality(quality, content, svg, source + "\n단위: 건", pt_per_px=0.75)
        assert quality["requestedNativeObjects"]["passed"] is accepted
        assert quality["passed"] is accepted
        assert content["nativeObjects"]["objects"][0]["payload"]["series"][0]["name"] == name

    for group in [candidate("건", qualifier=" 참고"), candidate("건", multiple=True),
                  candidate("건", duplicate=True)]:
        _, content = _publish(group, 0)
        assert content["nativeObjects"] is None

    def short_inside_labels(*, outside=False, end_far=False):
        root = ET.fromstring(candidate("건"))
        raw = json.loads(root.attrib["data-native-chart"])
        raw["valueLabels"]["position"] = "insideEnd"
        bars = [x for x in root if x.tag == "rect" and float(x.get("width", "0")) >= 40]
        assert len(bars) == 4
        values = raw["series"][0]["values"]
        for bar, value in zip(sorted(bars, key=lambda x: float(x.get("x", "0"))), values):
            height = value * 0.52
            top = 300 - height
            bar.set("y", str(top))
            bar.set("height", str(height))
            text = next(x for x in root if x.tag == "text" and "".join(x.itertext()) == str(value))
            text.set("font-size", "16")
            baseline = top + 19
            if outside and value == values[0]:
                baseline = top + 2
            if end_far and value == values[1]:
                baseline = top + height / 2 + 6
            text.set("y", str(baseline))
        root.attrib["data-native-chart"] = json.dumps(raw)
        return ET.tostring(root, encoding="unicode")

    _, content = _publish(short_inside_labels(), 0)
    assert len(content["nativeObjects"]["objects"]) == 1
    assert content["nativeObjects"]["objects"][0]["svgAppearance"]["valueLabels"]["position"] == "insideEnd"
    for group in [short_inside_labels(outside=True), short_inside_labels(end_far=True)]:
        _, content = _publish(group, 0)
        assert content["nativeObjects"] is None


def test_wide_source_category_labels_at_frozen_edge_remain_editable(tmp_path):
    from copy import deepcopy

    # Exact portable chart primitives from the wide source's clipped-axis case.
    group = '<ns0:g xmlns:ns0="http://www.w3.org/2000/svg" id="native-chart-monthly-orders" data-native-chart="{&quot;chartType&quot;:&quot;column&quot;,&quot;categories&quot;:[&quot;1월&quot;,&quot;2월&quot;,&quot;3월&quot;,&quot;4월&quot;],&quot;series&quot;:[{&quot;name&quot;:&quot;주문 수(건)&quot;,&quot;values&quot;:[240,360,520,740],&quot;pointColors&quot;:[&quot;#6AC2AE&quot;,&quot;#46AF97&quot;,&quot;#219F88&quot;,&quot;#08916E&quot;]}],&quot;title&quot;:&quot;월별 주문 수 (건)&quot;,&quot;categoryOrder&quot;:&quot;leftToRight&quot;,&quot;valueLabels&quot;:{&quot;shown&quot;:true,&quot;position&quot;:&quot;outsideEnd&quot;}}">\n    <ns0:text font-family="Noto Sans KR" font-size="21" font-weight="bold" fill="#002060" x="687" y="209">월별 주문 수 (건)</ns0:text>\n    <ns0:rect x="692" y="244" width="12" height="12" fill="#08916E" />\n    <ns0:text font-family="Noto Sans KR" font-size="15" fill="#344B46" x="713" y="255">주문 수(건)</ns0:text>\n    <ns0:line x1="700" y1="475" x2="1208" y2="475" stroke="#AFC5BE" />\n    <ns0:rect x="743" y="413" width="72" height="62" fill="#6AC2AE" />\n    <ns0:rect x="865" y="382" width="72" height="93" fill="#46AF97" />\n    <ns0:rect x="987" y="341" width="72" height="134" fill="#219F88" />\n    <ns0:rect x="1109" y="284" width="72" height="191" fill="#08916E" />\n    <ns0:text font-family="Noto Sans KR" font-size="18" font-weight="bold" fill="#00140E" x="779" y="404" text-anchor="middle">240</ns0:text>\n    <ns0:text font-family="Noto Sans KR" font-size="18" font-weight="bold" fill="#00140E" x="901" y="373" text-anchor="middle">360</ns0:text>\n    <ns0:text font-family="Noto Sans KR" font-size="18" font-weight="bold" fill="#00140E" x="1023" y="332" text-anchor="middle">520</ns0:text>\n    <ns0:text font-family="Noto Sans KR" font-size="18" font-weight="bold" fill="#00140E" x="1145" y="275" text-anchor="middle">740</ns0:text>\n    <ns0:text font-family="Noto Sans KR" font-size="17" fill="#344B46" x="779" y="500" text-anchor="middle">1월</ns0:text>\n    <ns0:text font-family="Noto Sans KR" font-size="17" fill="#344B46" x="901" y="500" text-anchor="middle">2월</ns0:text>\n    <ns0:text font-family="Noto Sans KR" font-size="17" fill="#344B46" x="1023" y="500" text-anchor="middle">3월</ns0:text>\n    <ns0:text font-family="Noto Sans KR" font-size="17" fill="#344B46" x="1145" y="500" text-anchor="middle">4월</ns0:text>\n  </ns0:g>'
    panel = '<rect x="665" y="226" width="567" height="288" fill="#FFFFFF" stroke="#CFDEDB"/>'
    svg, content = _publish(panel + group, 0)
    frozen = deepcopy(content)
    assert content["svgReceipt"]["nativeObjects"]["accepted"] == 1
    deck, evidence = _export(tmp_path, [svg], [content])
    assert content == frozen and evidence["structuralReadback"]["nativeObjects"]["native"] == 1
    (shape,) = [shape for shape in _frames(deck.slides[0]) if shape.has_chart]
    chart, xml = shape.chart, shape.chart._element
    assert list(chart.plots[0].categories) == ["1월", "2월", "3월", "4월"]
    assert list(chart.plots[0].series[0].values) == [240, 360, 520, 740]
    assert not chart.has_legend and chart.has_title
    assert chart.chart_title.text_frame.text == "월별 주문 수 (건)"
    assert xml.xpath(".//c:catAx/c:tickLblPos[@val='none']")
    assert xml.xpath(".//c:catAx/c:delete[@val='0']")
    labels = [item for item in _frames(deck.slides[0])
              if item.has_text_frame and item.text_frame.text in ["1월", "2월", "3월", "4월"]]
    assert len(labels) == 4
    assert sorted(round((item.left + item.width / 2) / 9525) for item in labels) == [779, 901, 1023, 1145]
    assert all(item.text_frame.paragraphs[0].runs[0].font.name == "Noto Sans KR" for item in labels)
    assert all(abs(item.text_frame.paragraphs[0].runs[0].font.size.pt - 12.75) < .1 for item in labels)
    assert abs(shape.top / 9525 - 188) < 1
    assert abs(shape.height / 9525 - 316.25) < 1
    layout = xml.xpath(".//c:plotArea/c:layout/c:manualLayout")[0]
    y, height = (float(layout.xpath(f"./c:{key}")[0].get("val")) for key in ("y", "h"))
    assert abs((shape.top + (y + height) * shape.height) / 9525 - 475) < 1
    (legend,) = [item for item in _frames(deck.slides[0])
                if item.has_text_frame and item.text_frame.text == "주문 수(건)"]
    assert legend.text_frame.paragraphs[0].runs[0].font.name == "Noto Sans KR"
