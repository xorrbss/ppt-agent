"""Chrome-backed ``text_overlap`` checks: glyph ink decides, line boxes are the fallback.

Fixtures in ``tests/fixtures/svg_text_overlap``:

- ``fp_*``: the 12 round-1 canvas-bench pages (2026-09-29) that the line-box
  rule flagged although no glyph pixels touch (multi-line titles, label + big
  number, tspan lines of one ``<text>``);
- ``syn_TP*``: six true collisions; ``syn_N*``: four tight-but-clean near misses.

See docs/svg-text-overlap-false-positives-design-20260929.md.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from services.svg_renderer import render_svg_with_metrics
from services.svg_text_overlap import text_overlaps
from utils.slide_capture import find_chrome

pytestmark = pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "svg_text_overlap"
SVG_NS = "http://www.w3.org/2000/svg"

# Pairs the line-box rule reported for each bench page (recorded before the fix).
LINE_BOX_PAIRS = {
    "fp_1x1_638d2f1a_s00": [["svg-4", "svg-5"], ["svg-9", "svg-10"]],
    "fp_1x1_638d2f1a_s01": [["svg-29", "svg-30"], ["svg-30", "svg-31"], ["svg-33", "svg-34"]],
    "fp_1x1_638d2f1a_s02": [["svg-3", "svg-4"]],
    "fp_1x1_864bc673_s00": [["svg-4", "svg-5"]],
    "fp_1x1_864bc673_s01": [["svg-10", "svg-11"], ["svg-12", "svg-13"], ["svg-18", "svg-19"],
                            ["svg-20", "svg-21"]],
    "fp_1x1_864bc673_s02": [["svg-4", "svg-5"]],
    "fp_1x1_864bc673_s04": [["svg-3", "svg-4"]],
    "fp_21x9_2e861a9f_s00": [["svg-4", "svg-5"]],
    "fp_21x9_2e861a9f_s01": [["svg-13", "svg-14"]],
    "fp_21x9_92dfbce7_s00": [["svg-4", "svg-5"]],
    "fp_16x9_e314d3e8_s00": [["svg-4", "svg-5"], ["svg-5", "svg-6"]],
    "fp_16x9_e314d3e8_s05": [["svg-34", "svg-35"], ["svg-43", "svg-44"]],
}
TRUE_COLLISIONS = {
    "syn_TP1_title_pitch_075em": ["svg-2", "svg-3"],
    "syn_TP2_latin_desc_accent_pitch_1em": ["svg-2", "svg-3"],
    "syn_TP3_kpi_label_inside_number": ["svg-2", "svg-3"],
    "syn_TP4_horizontal_run_into_neighbor": ["svg-2", "svg-3"],
    "syn_TP5_same_text_tspans_collide": ["svg-3", "svg-4"],
    "syn_TP6_duplicate_ghost": ["svg-2", "svg-3"],
}
NEAR_MISSES = ["syn_N1_title_pitch_115em", "syn_N2_tspans_pitch_12em",
               "syn_N3_kpi_label_above_number", "syn_N4_latin_pitch_125em"]


def _measure(svg: str) -> dict:
    size = re.search(r'<svg[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"', svg)
    width, height = int(size.group(1)), int(size.group(2))
    return asyncio.run(render_svg_with_metrics(svg, width=width, height=height, timeout=90)).quality


def _fixture(name: str) -> dict:
    return _measure((FIXTURES / f"{name}.svg").read_text(encoding="utf-8"))


def _text(elements: list[dict]) -> list[dict]:
    return [item for item in elements if item.get("has_owned_text")]


def _line_box_only(elements: list[dict]) -> list[list]:
    stripped = [{k: v for k, v in item.items() if k != "text_ink_fragments"} for item in _text(elements)]
    return text_overlaps(stripped)[0]


def _pairs(quality: dict) -> list[list]:
    return [pair for failure in quality["failures"] if failure["kind"] == "text_overlap"
            for pair in failure["pairs"]]


@pytest.mark.parametrize("name", sorted(LINE_BOX_PAIRS))
def test_bench_false_positives_clear_on_glyph_ink(name):
    quality = _fixture(name)
    elements = quality["browser_metrics"]["elements"]
    # The fixture still reproduces the recorded line-box verdict ...
    assert _line_box_only(elements) == LINE_BOX_PAIRS[name]
    # ... and the ink basis no longer reports it.
    assert quality["failures"] == [], quality["failures"]
    assert quality["passed"] is True
    flagged = {qid for pair in LINE_BOX_PAIRS[name] for qid in pair}
    by_qid = {item["qid"]: item for item in _text(elements)}
    assert all(all(by_qid[qid]["text_ink_fragments"]) for qid in flagged)
    assert quality["overlap_basis"]["pairs"] == []


@pytest.mark.parametrize("name", sorted(TRUE_COLLISIONS))
def test_true_collisions_stay_text_overlap_on_ink(name):
    quality = _fixture(name)
    assert [failure["kind"] for failure in quality["failures"]] == ["text_overlap"]
    assert _pairs(quality) == [TRUE_COLLISIONS[name]]
    assert quality["overlap_basis"]["pairs"] == [{"pair": TRUE_COLLISIONS[name], "basis": "ink"}]
    assert quality["overlap_basis"]["fragments"]["line_box"] == 0


@pytest.mark.parametrize("name", NEAR_MISSES)
def test_near_misses_pass(name):
    quality = _fixture(name)
    assert quality["failures"] == []
    assert _line_box_only(quality["browser_metrics"]["elements"])  # the old rule flagged it


# --- fallback: ink is not trusted, so the line box decides exactly as before ---

_BOLD = 'font-family="Noto Sans KR" font-size="48" font-weight="700" fill="#0F172A"'


def _two_lines(first_attrs="", second_attrs="", wrap=("", ""), first="스마트팩토리 단계별",
               second="도입 계획 수립", font=_BOLD):
    # N1 geometry: 1.15 em pitch, ink 9 px apart, line boxes 15 px deep.
    return (f'<svg xmlns="{SVG_NS}" width="1280" height="720" viewBox="0 0 1280 720">{wrap[0]}'
            f'<text x="80" y="200" {font}{first_attrs}>{first}</text>'
            f'<text x="80" y="255" {font}{second_attrs}>{second}</text>{wrap[1]}</svg>')


def _probe(svg: str) -> list[dict]:
    """Run the product probe on SVG the svg contract would reject at admission."""
    from services.svg_default_fonts import font_marker
    from utils.slide_capture import render_html_to_png_with_metrics

    ET.register_namespace("", SVG_NS)
    root = ET.fromstring(svg)
    for index, node in enumerate(root.iter()):
        node.set("data-qid", f"svg-{index}")
    html = (f'<!DOCTYPE html><html title="{font_marker()}"><head><meta charset="utf-8"><style>'
            "html,body{margin:0;width:1280px;height:720px;overflow:hidden}body>svg{display:block}"
            "</style></head><body>" + ET.tostring(root, encoding="unicode") + "</body></html>")
    capture = asyncio.run(render_html_to_png_with_metrics(html, timeout=90, width_px=1280, height_px=720,
                                                          fit=False))
    return capture.browser_metrics["elements"]


def _assert_line_box_fallback(elements: list[dict], *, flagged: bool = True):
    text = _text(elements)
    assert len(text) == 2
    for item in text:
        assert item["text_ink_fragments"] and all(entry is None for entry in item["text_ink_fragments"])
    overlaps, basis = text_overlaps(text)
    # Exactly the verdict the line-box rule gave before glyph ink existed.
    assert overlaps == _line_box_only(elements)
    assert overlaps == ([[text[0]["qid"], text[1]["qid"]]] if flagged else [])
    assert basis["fragments"]["ink"] == 0
    assert [pair["basis"] for pair in basis["pairs"]] == (["line_box"] if flagged else [])


def test_control_without_fallback_uses_ink():
    text = _text(_probe(_two_lines()))
    assert all(item["text_ink_fragments"][0] for item in text)
    assert text_overlaps(text)[0] == []


@pytest.mark.parametrize(("case", "svg"), [
    ("multi-value dx (split into several client rects)", _two_lines(' dx="0 0"', ' dx="0 0"')),
    ("textLength", _two_lines(' textLength="400"', ' textLength="290"')),
    ("rotated group", _two_lines(wrap=('<g transform="rotate(2 80 200)">', "</g>"))),
    ("skewed group", _two_lines(wrap=('<g transform="skewX(8)">', "</g>"))),
])
def test_untrusted_layout_falls_back_to_line_boxes(case, svg):
    _assert_line_box_fallback(_probe(svg))


def test_unloaded_family_falls_back_and_stays_font_unresolved():
    font = 'font-family="Codex Missing Face" font-size="48" font-weight="700" fill="#0F172A"'
    quality = _measure(_two_lines(font=font))
    assert "font_unresolved" in {failure["kind"] for failure in quality["failures"]}
    # The substitute face has other vertical metrics, so the line boxes may not meet.
    elements = quality["browser_metrics"]["elements"]
    _assert_line_box_fallback(elements, flagged=bool(_line_box_only(elements)))


def test_advance_self_check_mismatch_falls_back():
    # Chromium's SVG layout sets U+00B7 after Hangul narrower than canvas does.
    quality = _measure(_two_lines(first="스마트·팩토리 단계별", second="도입·계획 수립"))
    assert _pairs(quality) == [["svg-1", "svg-2"]]
    _assert_line_box_fallback(quality["browser_metrics"]["elements"])


def test_html_payload_has_no_ink_field():
    from utils.slide_capture import render_html_to_png_with_metrics

    html = ('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body style="margin:0">'
            '<div data-qid="a" style="font:32px sans-serif">first line</div>'
            '<div data-qid="b" style="font:32px sans-serif">second line</div></body></html>')
    capture = asyncio.run(render_html_to_png_with_metrics(html, timeout=90, width_px=1280, height_px=720,
                                                          fit=False))
    text = _text(capture.browser_metrics["elements"])
    assert text and all("text_ink_fragments" not in item for item in text)
