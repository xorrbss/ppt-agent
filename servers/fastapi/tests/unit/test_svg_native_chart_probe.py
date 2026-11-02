"""Probe: can vendor/slide_master's SVG->PPTX converter emit NATIVE charts?

This replicates the exact call shape used by
``workers/public_svg_export_worker.py::convert_pptx`` (sys.path insertion,
``ConversionContext``, ``create_pptx_with_native_svg(..., native_objects=True)``)
without importing that worker module (it is being refactored concurrently).

It answers, with evidence:
  1. which authored V1 chartType values ("column"/"bar"/"line"/"area"/
     "pie"/"donut") convert to a real python-pptx ``chart.chart_type``;
  2. which JSON payload keys the converter actually reads (some V1-shaped
     keys, e.g. a nested ``legend``/``palette``/``categoryAxis`` object, are
     silently ignored -- the converter wants ``show_legend``,
     ``legend_position``, ``style.colors`` and ``axis_titles``);
  3. that the drawn SVG fallback children are dropped once native_objects
     converts the marker group;
  4. that ``validate_native_object_marker`` and the full conversion both
     raise (never silently drop) on a structurally invalid chart payload.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from pptx import Presentation
from pptx.enum.chart import XL_CHART_TYPE

from services.authored_canvas import canvas_metadata_for_aspect_ratio
from services.document_canvas import canvas_physical_size

VENDOR_ROOT = Path(__file__).resolve().parents[2] / "vendor" / "slide_master"
SVG_NS = "http://www.w3.org/2000/svg"
#: Where each test writes its probe files; ``_scratch_dir`` points it at the
#: test's own temporary directory. (It used to be one developer's Windows
#: scratchpad path, which on other machines created that literal path as a
#: directory inside the repository.)
SCRATCH_DIR = Path()


@pytest.fixture(autouse=True)
def _scratch_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "SCRATCH_DIR", tmp_path / "probe")
CATEGORIES = ["1분기", "2분기", "3분기", "4분기"]


def _fallback_marks(categories: list[str], values: list[float], *, horizontal: bool) -> str:
    """A minimal drawn fallback: one rect bar + one text label per category."""
    parts: list[str] = []
    for index, (category, value) in enumerate(zip(categories, values, strict=True)):
        if horizontal:
            x, y, width, height = 150, 60 + index * 60, 20 + value, 40
        else:
            x, y, width, height = 60 + index * 120, 400 - value, 80, value
        parts.append(f'<rect x="{x}" y="{y}" width="{max(width, 1)}" height="{max(height, 1)}" fill="#4472C4"/>')
        parts.append(f'<text x="{x}" y="{y + height + 14}" font-size="12">{category}</text>')
    return "".join(parts)


def _text(value: str, *, x: int = 60, y: int = 20, size: int = 14) -> str:
    return f'<text x="{x}" y="{y}" font-size="{size}">{value}</text>'


def _marker_group(group_id: str, payload: dict, fallback_xml: str) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    escaped = raw.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<g id="{group_id}" data-pptx-native="chart" data-pptx-json="{escaped}">{fallback_xml}</g>'


def _svg_document(marker_xml: str) -> str:
    return f'<svg xmlns="{SVG_NS}" width="1280" height="720" viewBox="0 0 1280 720">{marker_xml}</svg>'


def _write_svg(name: str, xml: str) -> Path:
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    path = SCRATCH_DIR / name
    path.write_text(xml, encoding="utf-8")
    return path


def _convert(paths: list[Path], target: Path, *, native_objects: bool = True) -> bool:
    """Reproduces workers/public_svg_export_worker.py::convert_pptx's call shape."""
    canvas = canvas_metadata_for_aspect_ratio("16:9")
    width_emu, height_emu = canvas_physical_size(canvas)
    sys.path.insert(0, str(VENDOR_ROOT))
    try:
        from svg_to_pptx import create_pptx_with_native_svg
        from svg_to_pptx.conversion_context import ConversionContext

        context = ConversionContext(canvas.width_px, canvas.height_px, width_emu, height_emu, legacy=True)
        return create_pptx_with_native_svg(
            paths, target, verbose=False, transition=None, use_native_shapes=True,
            notes={path.stem: "" for path in paths}, workers=1, merge_paragraphs=False,
            image_optimize=False, pptx_structure="flat", use_compat_mode=False,
            conversion_context=context, native_objects=native_objects,
        )
    finally:
        sys.path.remove(str(VENDOR_ROOT))


def _base_payload(chart_type: str, series: list[dict], **extra) -> dict:
    payload = {
        "type": chart_type, "categories": CATEGORIES, "series": series,
        "x": 60, "y": 60, "width": 1160, "height": 560,
    }
    payload.update(extra)
    return payload


CHART_CASES = [
    pytest.param("column-1series", "column", [{"name": "매출", "values": [10, 20, 15, 30]}],
                 XL_CHART_TYPE.COLUMN_CLUSTERED, id="column-1series"),
    pytest.param(
        "column-2series", "column",
        [{"name": "매출", "values": [10, 20, 15, 30]}, {"name": "비용", "values": [5, 10, 8, 12]}],
        XL_CHART_TYPE.COLUMN_CLUSTERED, id="column-2series",
    ),
    pytest.param("bar", "bar", [{"name": "매출", "values": [10, 20, 15, 30]}],
                 XL_CHART_TYPE.BAR_CLUSTERED, id="bar"),
    pytest.param("line", "line", [{"name": "매출", "values": [10, 20, 15, 30]}],
                 XL_CHART_TYPE.LINE, id="line"),
    pytest.param("area", "area", [{"name": "매출", "values": [10, 20, 15, 30]}],
                 XL_CHART_TYPE.AREA, id="area"),
    pytest.param("pie", "pie", [{"name": "매출", "values": [10, 20, 15, 30]}],
                 XL_CHART_TYPE.PIE, id="pie"),
    pytest.param("doughnut", "donut", [{"name": "매출", "values": [10, 20, 15, 30]}],
                 XL_CHART_TYPE.DOUGHNUT, id="doughnut-alias-donut"),
]


@pytest.mark.parametrize("name, chart_type, series, expected_xl_type", CHART_CASES)
def test_native_chart_types_convert(name, chart_type, series, expected_xl_type):
    values = series[0]["values"]
    fallback = _fallback_marks(CATEGORIES, values, horizontal=(chart_type == "bar"))
    payload = _base_payload(chart_type, series)
    svg_path = _write_svg(f"{name}.svg", _svg_document(_marker_group(f"c-{name}", payload, fallback)))
    target = SCRATCH_DIR / f"{name}.pptx"

    assert _convert([svg_path], target), f"conversion reported failure for {name}"

    deck = Presentation(str(target))
    assert len(deck.slides) == 1
    slide = deck.slides[0]
    shapes = list(slide.shapes)
    frames = [shape for shape in shapes if shape.has_chart]
    assert len(frames) == 1, "expected exactly one native chart graphicFrame"
    assert len(shapes) == 1, (
        f"drawn SVG fallback (rects/text) leaked as extra shapes: {len(shapes)} total"
    )

    chart = frames[0].chart
    assert chart.chart_type == expected_xl_type
    plot = chart.plots[0]
    assert list(plot.categories) == CATEGORIES
    assert [chart_series.name for chart_series in chart.series] == [item["name"] for item in series]
    for chart_series, item in zip(chart.series, series, strict=True):
        assert list(chart_series.values) == item["values"]
    assert chart.part.chart_workbook.xlsx_part is not None, "no embedded workbook part"


def test_recognized_style_keys_render_legend_axis_titles_and_palette():
    """The converter's *actual* chart-chrome keys: show_legend/legend_position,
    axis_titles.category/value, fontFamily, textColor, style.colors, note.
    """
    series = [{"name": "매출", "values": [10, 20, 15, 30]}, {"name": "비용", "values": [5, 10, 8, 12]}]
    fallback = "".join([
        _fallback_marks(CATEGORIES, series[0]["values"], horizontal=False),
        _text("분기별 실적", y=20),
        _text("매출"), _text("비용"),
        _text("분기"), _text("매출(억원)"),
        _text("출처: 내부 자료"),
    ])
    payload = _base_payload(
        "column", series,
        title="분기별 실적",
        show_legend=True, legend_position="right",
        axis_titles={"category": "분기", "value": "매출(억원)"},
        fontFamily="Malgun Gothic", textColor="1F1F1F",
        style={"colors": ["FF0000", "00B050"]},
        note="출처: 내부 자료",
    )
    svg_path = _write_svg("column-style.svg", _svg_document(_marker_group("c-style", payload, fallback)))
    target = SCRATCH_DIR / "column-style.pptx"
    assert _convert([svg_path], target)

    slide = Presentation(str(target)).slides[0]
    shapes = list(slide.shapes)
    frame = next(shape for shape in shapes if shape.has_chart)
    chart = frame.chart

    assert chart.has_title
    assert chart.chart_title.text_frame.text == "분기별 실적"
    assert chart.has_legend is True
    assert chart.category_axis.has_title
    assert chart.category_axis.axis_title.text_frame.text == "분기"
    assert chart.value_axis.has_title
    assert chart.value_axis.axis_title.text_frame.text == "매출(억원)"
    first_series_rgb = str(chart.series[0].format.fill.fore_color.rgb)
    second_series_rgb = str(chart.series[1].format.fill.fore_color.rgb)
    assert first_series_rgb == "FF0000"
    assert second_series_rgb == "00B050"
    # A companion text box (note) is added alongside the chart graphicFrame.
    assert len(shapes) == 2
    note_box = next(shape for shape in shapes if not shape.has_chart)
    assert note_box.text_frame.text == "출처: 내부 자료"


def test_v1_literal_legend_palette_axis_keys_are_ignored():
    """Documents the mapping mismatch: authored V1's own field spellings for
    legend/palette/axis titles/font size are NOT understood by the converter,
    so a payload built with only those V1-shaped keys renders with defaults.
    """
    series = [{"name": "매출", "values": [10, 20, 15, 30]}, {"name": "비용", "values": [5, 10, 8, 12]}]
    fallback = _fallback_marks(CATEGORIES, series[0]["values"], horizontal=False)
    payload = _base_payload(
        "column", series,
        legend={"visible": True, "position": "right"},
        palette=["FF0000", "00B050"],
        categoryAxis={"title": "분기"},
        valueAxis={"title": "매출(억원)"},
        fontSizePt=20,
    )
    svg_path = _write_svg("column-v1-literal.svg", _svg_document(_marker_group("c-v1", payload, fallback)))
    target = SCRATCH_DIR / "column-v1-literal.pptx"
    assert _convert([svg_path], target)

    slide = Presentation(str(target)).slides[0]
    frame = next(shape for shape in slide.shapes if shape.has_chart)
    chart = frame.chart

    assert chart.has_legend is False, "unrecognized 'legend' object must not turn on show_legend"
    assert chart.category_axis.has_title is False, "unrecognized 'categoryAxis' must not set an axis title"
    assert chart.value_axis.has_title is False, "unrecognized 'valueAxis' must not set an axis title"
    # 'palette' is not read; series fall back to the built-in default colors.
    assert str(chart.series[0].format.fill.fore_color.rgb) == "4472C4"
    assert str(chart.series[1].format.fill.fore_color.rgb) == "ED7D31"


def test_validate_native_object_marker_accepts_clean_chart_fixture():
    payload = _base_payload("column", [{"name": "매출", "values": [10, 20, 15, 30]}])
    fallback = _fallback_marks(CATEGORIES, payload["series"][0]["values"], horizontal=False)
    root = ET.fromstring(_svg_document(_marker_group("c-validate", payload, fallback)))
    group = root.find(f"{{{SVG_NS}}}g")
    assert group is not None

    sys.path.insert(0, str(VENDOR_ROOT))
    try:
        from svg_to_pptx.native_objects import validate_native_object_marker

        validate_native_object_marker(group)  # must not raise
    finally:
        sys.path.remove(str(VENDOR_ROOT))


def test_invalid_chart_payload_raises_instead_of_silently_dropping():
    """series length mismatched with categories is a structural error: the
    marker validator must raise, and native_objects=True conversion must
    propagate that failure rather than silently falling back to a picture.
    """
    payload = _base_payload("column", [{"name": "매출", "values": [10, 20, 15]}])  # 3 values, 4 categories
    fallback = _fallback_marks(CATEGORIES[:3], [10, 20, 15], horizontal=False)
    svg_path = _write_svg("column-invalid.svg", _svg_document(_marker_group("c-invalid", payload, fallback)))
    target = SCRATCH_DIR / "column-invalid.pptx"
    target.unlink(missing_ok=True)

    with pytest.raises(RuntimeError, match="categories length"):
        _convert([svg_path], target)
    assert not target.is_file(), "a failed native conversion must not leave a partial PPTX"
