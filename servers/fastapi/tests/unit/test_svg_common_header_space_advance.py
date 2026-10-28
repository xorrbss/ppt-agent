"""Chromium's bundled-header space advance is a sealed layout contract."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from services.svg_common_header import (
    CHROMIUM_SPACE_ADVANCE_EM,
    prepare_common_header,
    render_common_header,
    validate_common_header_binding,
)
from services.svg_common_header_export_evidence import apply_common_header_v2_quality
from services.svg_common_header_v2 import prepare_common_header_v2, render_common_header_v2
from services.svg_renderer import render_svg_with_metrics
from services.svg_text_advance import text_advance
from utils.svg_template_fonts import trusted_font_marker
from utils.slide_capture import find_chrome

pytestmark = pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "svg_common_header_live4" / "failed_titles.json"


def _load_fixture_page(key: str) -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["pages"][key]


def _candidate(contract: dict) -> dict:
    fields = []
    for field in contract["fields"]:
        fields.append({
            "node_id": field["node_id"], "role": field["role"], "value_ref": field["value_ref"],
            "box": field["box"], "font_family": field["font"]["family"], "font_weight": field["font"]["weight"],
            "font_size_px": field["font"]["size_px"], "line_height_px": field["line_height_px"],
            "baseline_offset_px": field["baseline_offset_px"], "max_lines": field["max_lines"],
            "alignment": field["alignment"], "optional": field["optional"],
            "dependent_node_ids": field["dependent_node_ids"],
        })
    return {"candidate_id": contract["candidate_id"], "revision": contract["revision"],
            "svg": contract["svg_template"], "body_box": contract["body_box"],
            "design_intent": contract["design_intent"], "shared_style": contract["shared_style"], "fields": fields}


def _svg_for_group(group: ET.Element, width: int, height: int) -> str:
    root = ET.Element("{http://www.w3.org/2000/svg}svg", {
        "width": str(width), "height": str(height), "viewBox": f"0 0 {width} {height}",
    })
    root.append(group)
    return ET.tostring(root, encoding="unicode")


def _text_metrics(result) -> list[dict]:
    return [item for item in result.quality["browser_metrics"]["elements"] if item.get("has_owned_text")]


def test_chromium_space_advance_is_measured_at_both_header_weights():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="120" viewBox="0 0 400 120">'
        '<text x="0" y="50" font-family="Noto Sans KR" font-size="34" font-weight="400" fill="#000000">A A</text>'
        '<text x="0" y="100" font-family="Noto Sans KR" font-size="34" font-weight="700" fill="#000000">A A</text>'
        "</svg>"
    )
    result = asyncio.run(render_svg_with_metrics(
        svg, width=400, height=120, timeout=90, source_font_profile=trusted_font_marker(),
    ))
    metrics = _text_metrics(result)
    assert len(metrics) == 2
    for metric, weight in zip(metrics, (400, 700), strict=True):
        browser_width = metric["text_fragments"][0]["width"]
        face_width = text_advance("AA", 34, str(weight), "Noto Sans KR")
        assert browser_width - face_width == pytest.approx(CHROMIUM_SPACE_ADVANCE_EM * 34, abs=0.05)


@pytest.mark.parametrize("key", ("25", "29"))
def test_frozen_live4_v2_binding_keeps_legacy_face_measurement(key):
    page = _load_fixture_page(key)
    validate_common_header_binding(page["contract"], page["binding"], index=page["page_index"],
                                   count=page["binding"]["page_count"])


@pytest.mark.parametrize("key", ("25", "29"))
def test_live4_fresh_v2_header_wraps_and_passes_real_export_evidence(key):
    page = _load_fixture_page(key)
    contract = page["contract"]
    outline = {"slides": [
        {"page_id": f"page-{index}", "content": "fixture", "title": page["title"] if index == page["page_index"] else f"제목 {index}"}
        for index in range(page["binding"]["page_count"])
    ]}
    prepared, bindings = prepare_common_header_v2(_candidate(contract), outline, contract["canvas"], snapshot={})
    binding = bindings[page["page_index"]]
    title = next(field for field in binding["fields"] if field["role"] == "title")
    title_field = next(field for field in prepared["fields"] if field["role"] == "title")
    assert len(title["lines"]) == 2
    assert all(width <= title_field["box"]["width"] for width in title["line_widths"])
    validate_common_header_binding(prepared, binding, index=page["page_index"], count=binding["page_count"])
    svg = _svg_for_group(render_common_header_v2(prepared, binding, index=page["page_index"], count=binding["page_count"]), 1280, 720)
    quality = asyncio.run(render_svg_with_metrics(
        svg, width=1280, height=720, timeout=90, source_font_profile=trusted_font_marker(),
    )).quality
    apply_common_header_v2_quality(quality, svg, prepared, binding, None)
    assert quality["common_header"]["passed"] is True
    ink_widths = [fragment["width"] for metric in quality["browser_metrics"]["elements"]
                  if metric.get("has_owned_text")
                  if metric.get("owned_text") in title["lines"] for fragment in metric["text_fragments"]]
    assert len(ink_widths) == len(title["lines"])
    assert all(abs(preflight - browser) <= 1 for preflight, browser in zip(title["line_widths"], ink_widths, strict=True))


def test_twenty_page_v1_and_v2_headers_match_real_renderer_once_per_deck():
    fixture = _load_fixture_page("25")
    titles = [fixture["title"], "초기 10대를 우선 승인하고, 검증된 비용과 품질로 후속 투자를 결정한다"] * 10
    slides = [{"page_id": f"page-{index}", "content": "fixture", "title": title} for index, title in enumerate(titles)]
    canvas = {"width": 1280, "height": 720}
    v1_contract, v1_bindings = prepare_common_header(
        {"tokens": {"heading_font": "Noto Sans KR"}}, {"slides": slides}, canvas,
    )
    v2_contract, v2_bindings = prepare_common_header_v2(
        _candidate(fixture["contract"]), {"slides": slides}, canvas, snapshot={},
    )

    def deck_svg(rendered):
        root = ET.Element("{http://www.w3.org/2000/svg}svg", {
            "width": "2560", "height": "3600", "viewBox": "0 0 2560 3600",
        })
        for index, group in enumerate(rendered):
            group.set("transform", f"translate({index % 2 * 1280} {index // 2 * 720})")
            for node in group.iter():
                node.set("id", f"{node.get('id')}-p{index}")
            root.append(group)
        return ET.tostring(root, encoding="unicode")

    v1_svg = deck_svg([
        render_common_header(v1_contract, binding, index=index, count=20)
        for index, binding in enumerate(v1_bindings)
    ])
    v2_svg = deck_svg([
        render_common_header_v2(v2_contract, binding, index=index, count=20)
        for index, binding in enumerate(v2_bindings)
    ])
    v1_quality = asyncio.run(render_svg_with_metrics(
        v1_svg, width=2560, height=3600, timeout=90, source_font_profile=trusted_font_marker(),
    )).quality
    v2_quality = asyncio.run(render_svg_with_metrics(
        v2_svg, width=2560, height=3600, timeout=90, source_font_profile=trusted_font_marker(),
    )).quality
    for quality, bindings, marker in (
        (v1_quality, v1_bindings, "svg-system-common-header-title-line-"),
        (v2_quality, v2_bindings, "svg-system-common-header-a-title-line-"),
    ):
        assert not [failure for failure in quality["failures"] if failure["kind"].startswith("svg_common_header")]
        metrics = quality["browser_metrics"]["elements"]
        root = ET.fromstring(v1_svg if marker.startswith("svg-system-common-header-title") else v2_svg)
        title_nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "text" and marker in node.get("id", "")]
        expected_widths = []
        for binding in bindings:
            if "fields" in binding:
                title = next(field for field in binding["fields"] if field["role"] == "title")
                expected_widths.extend(title["line_widths"])
            else:
                expected_widths.extend(binding["line_widths"])
        metrics_by_qid = {item["qid"]: item for item in metrics}
        for node, expected in zip(title_nodes, expected_widths, strict=True):
            qid = f"svg-{list(root.iter()).index(node)}"
            metric = metrics_by_qid[qid]
            assert abs(metric["text_fragments"][0]["width"] - expected) <= 1
