"""Four drawn SVG charts through publication, the real export worker and PPTX readback."""
from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path

import pypdfium2 as pdfium
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


def _text(x: float, y: float, value: str, *, centered: bool = False) -> str:
    anchor = ' text-anchor="middle"' if centered else ""
    return f'<text x="{x}" y="{y}" {FONT}{anchor}>{value}</text>'


def _group(kind: str, *, wrong_color: bool = False, wrong_slice: bool = False,
           wrong_label: bool = False) -> str:
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
    parts = [_text(90, 85, declaration["title"]), _text(980, 135, "Revenue")]
    if kind == "bar":
        for i, (category, value) in enumerate(zip(CATEGORIES, values, strict=True)):
            y, width = 185 + 100 * i, value * 6
            color = "#AA0000" if wrong_color and i == 1 else COLORS[i]
            parts += [f'<rect x="240" y="{y}" width="{width}" height="40" fill="{color}"/>',
                      _text(165, y + 27, category, centered=True),
                      _text(240 + width + (30 if wrong_label and i == 0 else -36),
                            y + 27, str(value), centered=True)]
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
    published = [_publish(_group(kind), index) for index, kind in enumerate(kinds)]
    svgs, contents = map(list, zip(*published, strict=True))
    assert all(content["svgReceipt"]["nativeObjects"]["accepted"] == 1 for content in contents)
    deck, evidence = _export(tmp_path, svgs, contents)
    assert evidence["structuralReadback"]["nativeObjects"]["native"] == 4
    for index, kind in enumerate(kinds):
        (shape,) = [item for item in _frames(deck.slides[index]) if item.has_chart]
        chart = shape.chart
        assert list(chart.plots[0].categories) == CATEGORIES
        assert list(chart.plots[0].series[0].values) == VALUES[kind]
        assert chart.has_title and chart.chart_title.text_frame.text == f"{kind.title()} revenue"
        xml = chart._element
        assert len(xml.xpath(".//c:title/c:overlay")) == 1
        if kind != "line":
            fills = xml.xpath(".//c:ser/c:dPt/c:spPr/a:solidFill/a:srgbClr")
            assert ["#" + point.get("val") for point in fills] == COLORS
            assert len(xml.xpath(".//c:dLbls/c:showVal[@val='1']")) >= 1
        if kind == "bar":
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


def test_wrong_drawn_point_color_keeps_svg_and_drops_only_native_annotation():
    svg, content = _publish(_group("bar", wrong_color=True), 0)
    assert content["nativeObjects"] is None
    assert content["svgReceipt"]["nativeObjects"]["dropped"][0]["reason"] == "native_chart_point_color_mismatch"
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
