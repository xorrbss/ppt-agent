"""The bundled-header space advance is measured on the renderer and sealed in the contract."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from services import svg_common_header_space_advance as space_advance
from services.svg_common_header import (
    MEASUREMENT_VERSION,
    _advance,
    _font,
    prepare_common_header,
    render_common_header,
    validate_common_header_binding,
)
from services.svg_common_header_export_evidence import apply_common_header_v2_quality
from services.svg_common_header_v2 import (
    MEASUREMENT_VERSION as V2_MEASUREMENT_VERSION,
    prepare_common_header_v2,
    render_common_header_v2,
)
from services.svg_renderer import render_svg_with_metrics
from utils.svg_template_fonts import trusted_font_marker
from utils.slide_capture import find_chrome

pytestmark = pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURE = FIXTURES / "svg_common_header_live4" / "failed_titles.json"
FROZEN = FIXTURES / "svg_common_header_frozen_measurements"


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


def _render_texts(texts: list[tuple[str, str, int, int]], width=1280, height=720) -> list[dict]:
    nodes = "".join(
        f'<text x="20" y="{60 + index * 60}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="#000000">{text}</text>'
        for index, (text, family, weight, size) in enumerate(texts))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}">{nodes}</svg>')
    return _text_metrics(asyncio.run(render_svg_with_metrics(svg, width=width, height=height, timeout=90)))


@pytest.mark.parametrize(("family", "weight"), space_advance.PROBE_FACES)
def test_measured_space_advance_matches_the_renderer_at_every_header_face(family, weight):
    font = _font(family, weight, 34)
    evidence = space_advance.space_advance_evidence([font])
    # A space in a Latin run and one in a Hangul run: Noto Sans KR's Korean
    # locl draws the second wider in Chromium, which the font alone omits.
    texts = ["A A", "가 가"]
    metrics = _render_texts([(text, family, weight, 34) for text in texts])
    assert len(metrics) == len(texts)
    for metric, text in zip(metrics, texts, strict=True):
        browser_width = metric["text_fragments"][0]["width"]
        assert browser_width == pytest.approx(_advance(text, font, MEASUREMENT_VERSION, evidence), abs=0.05)


def test_measurement_provenance_names_the_renderer_and_is_sealed():
    slides = [{"page_id": "p", "content": "x", "title": "가격대별 판매 비중과 향후 전망"}]
    contract, bindings = prepare_common_header({"tokens": {"heading_font": "Noto Sans KR"}},
                                               {"slides": slides}, {"width": 1280, "height": 720})
    assert contract["measurement_version"] == MEASUREMENT_VERSION
    evidence = contract["space_advance"]
    assert evidence["version"] == space_advance.PROBE_VERSION
    assert evidence["renderer"] == space_advance.renderer_identity()
    assert [(face["family"], face["weight"]) for face in evidence["faces"]] == [("Noto Sans KR", 800)]
    validate_common_header_binding(contract, bindings[0])
    face = evidence["faces"][0]
    assert set(face["contexts"]) == set(space_advance.SPACE_CONTEXTS)
    # This title's spaces all sit between Hangul syllables.
    assert space_advance.space_contexts(slides[0]["title"]) == ["hangul|hangul"] * 4
    hangul = face["contexts"]["hangul|hangul"][0]
    tampered = {**contract, "space_advance": {**evidence, "faces": [{**face, "contexts": {
        **face["contexts"], "hangul|hangul": [{**hangul, "space_delta_em": hangul["space_delta_em"] + 1e-6}]}}]}}
    with pytest.raises(ValueError, match="svg_common_header_digest_mismatch"):
        validate_common_header_binding(tampered, bindings[0])


@pytest.mark.parametrize("alignment", ("left", "center", "right"))
def test_fresh_v1_header_lines_land_where_the_renderer_draws_them(alignment):
    titles = ["2026년 국내 전기차 시장 분석", "Market share and pricing trend", "AI 전략 (2026) 수립과 실행 계획"]
    slides = [{"page_id": f"p{index}", "content": "x", "title": title} for index, title in enumerate(titles)]
    contract, bindings = prepare_common_header(
        {"tokens": {"heading_font": "Noto Sans KR"}, "common_header": {"alignment": alignment}},
        {"slides": slides}, {"width": 1280, "height": 720})
    box = contract["title_box"]
    for index, binding in enumerate(bindings):
        svg = _svg_for_group(render_common_header(contract, binding, index=index, count=len(slides)), 1280, 720)
        result = asyncio.run(render_svg_with_metrics(svg, width=1280, height=720, timeout=90))
        fragments = [metric["text_fragments"][0] for metric in _text_metrics(result)]
        assert len(fragments) == len(binding["lines"])
        for fragment, width in zip(fragments, binding["line_widths"], strict=True):
            expected_x = box["x"] + {"left": 0, "center": (box["width"] - width) / 2,
                                     "right": box["width"] - width}[alignment]
            assert abs(fragment["width"] - width) <= 1
            assert abs(fragment["x"] - expected_x) <= 1


@pytest.mark.parametrize("name", ("v1_glyph_v2.json", "v2_glyph_paint_v3.json"))
def test_frozen_sealed_constant_contracts_still_validate_and_render(name):
    frozen = json.loads((FROZEN / name).read_text(encoding="utf-8"))
    contract, bindings, slides = frozen["contract"], frozen["bindings"], frozen["slides"]
    assert contract["measurement_version"] in ("svg-common-header-glyph/v2", "svg-common-header-glyph-paint/v3")
    assert "space_advance" not in contract
    for index, (binding, slide) in enumerate(zip(bindings, slides, strict=True)):
        validate_common_header_binding(contract, binding, index=index, count=len(slides),
                                       content=slide["content"], title=slide["title"])
        assert render_common_header(contract, binding, index=index, count=len(slides)) is not None
    # The sealed constant cannot be swapped for this host's measurement.
    resealed = {**contract, "space_advance": space_advance.space_advance_evidence(
        [contract["font"]] if "font" in contract else [field["font"] for field in contract["fields"]])}
    with pytest.raises(ValueError, match="svg_common_header_digest_mismatch"):
        validate_common_header_binding(resealed, bindings[0], index=0, count=len(slides))


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
    assert prepared["measurement_version"] == V2_MEASUREMENT_VERSION
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
