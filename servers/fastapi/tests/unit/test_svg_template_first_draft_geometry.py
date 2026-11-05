"""Second round of template first-draft failures (synthetic, fictional data only).

Measured 2026-11-04 on 24 stored template decks (4 inputs x 2 templates x 3 policy arms):
placeholder marks the browser does not draw (zero-size icons, paint-less paths, an empty
``<style>``, ``.`` spacer texts), native table header labels drawn twice, texts wider than the
author had allowed, and repair hints that named overlapping texts only by measurement id.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

NS = "http://www.w3.org/2000/svg"


def _svg(body: str, *, width: int = 1280, height: int = 720) -> str:
    return f'<svg xmlns="{NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}">{body}</svg>'


def _ids(root) -> list[str]:
    return [node.get("id") for node in root.iter() if node.get("id")]


# --- marks SVG does not draw ---------------------------------------------------------------

def test_a_zero_size_icon_placeholder_is_dropped():
    from services.svg_text_defaults import drop_zero_size_shapes

    root = ET.fromstring(_svg(
        '<image id="a" href="source-slot-aaaa" x="72" y="560" width="40" height="40"/>'
        '<image id="b" href="source-slot-bbbb" x="72" y="728" width="0" height="0"/>'
        '<image id="c" href="source-slot-cccc" x="72" y="728" width="0" height="12"/>'))
    assert drop_zero_size_shapes(root) == 2
    assert _ids(root) == ["a"]


def test_paint_less_paths_and_empty_style_are_dropped_and_painted_ones_kept():
    from services.svg_text_defaults import drop_unrendered_marks

    root = ET.fromstring(_svg(
        '<style></style><defs/>'
        '<path id="arc" d="M940 312 A0 0 0 0 1 940 312" fill="none"/>'
        '<g fill="none"><path id="inherited-none" d="M1 1 L5 5"/></g>'
        '<g stroke="#243B7C"><path id="inherited-stroke" d="M1 1 L5 5" fill="none"/></g>'
        '<path id="filled" d="M1 1 L5 5 L1 5 Z"/>'
        '<path id="stroked" d="M1 1 L5 5" fill="none" stroke="#005ED0"/>'))
    assert drop_unrendered_marks(root) == 4
    assert _ids(root) == ["inherited-stroke", "filled", "stroked"]
    assert not [node for node in root.iter() if node.tag in (f"{{{NS}}}style", f"{{{NS}}}defs")]


def test_a_style_with_rules_is_left_for_the_validator():
    from services.svg_text_defaults import drop_unrendered_marks

    root = ET.fromstring(_svg("<style>text { fill: red }</style>"))
    assert drop_unrendered_marks(root) == 0


@pytest.mark.parametrize("attributes", [
    'font-size="1" fill="none"',          # svg_contract_invisible_text
    'font-size="14.5" fill="none"',
    'font-size="14.5" fill="#4E5B6E" opacity="0"',  # text_hidden
    'font-size="1" fill="#1A1F26"',       # small_body_text
])
def test_a_punctuation_placeholder_text_that_draws_nothing_is_dropped(attributes):
    from services.svg_text_defaults import drop_unrendered_marks

    root = ET.fromstring(_svg(f'<text id="dot" x="90" y="230" font-family="Pretendard" {attributes}>.</text>'
                              '<text id="keep" x="90" y="260" font-family="Pretendard" font-size="16" fill="#111111">본문</text>'))
    assert drop_unrendered_marks(root) == 1
    assert _ids(root) == ["keep"]


@pytest.mark.parametrize("body", [
    # A word or a number is never removed, however it is hidden: the existing checks report it.
    '<text x="0" y="0" font-family="Pretendard" font-size="14.5" fill="none">매출 비교</text>',
    '<text x="9" y="9" font-family="Pretendard" font-size="1" fill="#111111">12</text>',
    '<text x="9" y="9" font-family="Pretendard" font-size="16" fill="#111111" opacity="0">합계</text>',
    # Visible punctuation stays.
    '<text x="9" y="9" font-family="Pretendard" font-size="18" fill="#111111">→</text>',
    # A whitespace spacer with a fill already passes (it is not body paint); it is not touched.
    '<text x="9" y="9" font-family="Pretendard" font-size="1" fill="#0E1116"> </text>',
    # An outlined text paints its stroke.
    '<text x="9" y="9" font-family="Pretendard" font-size="18" fill="none" stroke="#111111">·</text>',
])
def test_texts_with_content_or_paint_are_kept(body):
    from services.svg_text_defaults import drop_unrendered_marks

    root = ET.fromstring(_svg(body))
    assert drop_unrendered_marks(root) == 0


def test_a_draft_failing_only_for_placeholders_now_validates_and_replays_the_same():
    from services.svg_author_output_view import parse_author_response
    from services.svg_contract import validate_svg
    from services.svg_text_defaults import drop_unrendered_marks, drop_zero_size_shapes

    raw = _svg('<style></style>'
               '<g><circle cx="940" cy="400" r="88" fill="none" stroke="#243B7C" stroke-width="44"/>'
               '<path d="M940 312 A0 0 0 0 1 940 312" fill="none"/></g>'
               '<text x="84" y="186" font-family="Pretendard" font-size="1" fill="none">.</text>'
               '<text x="68" y="600" font-family="Pretendard" font-size="20" fill="#111111">결론 문장</text>')
    with pytest.raises(ValueError, match="svg_contract_unsupported_element"):
        validate_svg(raw, width=1280, height=720)
    root = ET.fromstring(raw)
    drop_zero_size_shapes(root)
    assert drop_unrendered_marks(root) == 3
    produced = ET.tostring(root, encoding="unicode")
    validate_svg(produced, width=1280, height=720)
    replayed, _receipt = parse_author_response(raw, view=None, source_facts=None)
    assert ET.tostring(replayed, encoding="unicode") == produced


# --- native table header labels drawn twice -------------------------------------------------

def _table_page(outer_labels: str, *, group_transform: str = "") -> str:
    rows = [["항목", "현재", "목표"], ["처리 시간", "40분", "15분"], ["오류율", "3%", "1%"]]
    declaration = json.dumps({"rows": rows, "headerRows": 1}, ensure_ascii=False)
    transform = f' transform="{group_transform}"' if group_transform else ""
    inner = "".join(
        f'<text x="{90 + 300 * i}" y="223" font-family="Pretendard" font-size="18" font-weight="bold"'
        f' fill="#243B7C">{label}</text>' for i, label in enumerate(rows[0]))
    cells = '<text x="90" y="265" font-family="Pretendard" font-size="18" fill="#111111">처리 시간</text>'
    return _svg('<rect x="68" y="196" width="1144" height="40" fill="#E7F0FB"/>' + outer_labels
                + f"<g id=\"body-native-table-plan\" data-native-table='{declaration}'{transform}>"
                + '<rect x="68" y="196" width="1144" height="132" fill="#FFFFFF" stroke="#D5DBE1"/>'
                + inner + cells + "</g>")


def _texts(root) -> list[tuple[str, str]]:
    return [(node.get("x"), "".join(node.itertext())) for node in root.iter(f"{{{NS}}}text")]


def test_header_labels_written_again_outside_the_table_on_its_baseline_are_dropped():
    from services.svg_text_defaults import drop_duplicate_native_table_headers

    outer = "".join(f'<text x="{x}" y="223" font-family="Pretendard" font-size="18" fill="#8A857E">{label}</text>'
                    for x, label in ((94, "항목"), (130, "현재"), (690, "목표")))
    root = ET.fromstring(_table_page(outer))
    assert drop_duplicate_native_table_headers(root) == 3
    assert _texts(root) == [("90", "항목"), ("390", "현재"), ("690", "목표"), ("90", "처리 시간")]


def test_other_texts_and_other_baselines_are_kept():
    from services.svg_text_defaults import drop_duplicate_native_table_headers

    outer = ('<text x="68" y="180" font-family="Pretendard" font-size="18" fill="#111111">항목</text>'  # other baseline
             '<text x="400" y="223" font-family="Pretendard" font-size="18" fill="#111111">현재 기준</text>'  # other words
             '<text x="90" y="265" font-family="Pretendard" font-size="18" fill="#111111">처리 시간</text>')  # body cell
    root = ET.fromstring(_table_page(outer))
    assert drop_duplicate_native_table_headers(root) == 0
    root = ET.fromstring(_svg('<text x="1" y="9" font-family="Pretendard" font-size="18" fill="#111111">항목</text>'))
    assert drop_duplicate_native_table_headers(root) == 0


def test_a_translated_table_is_compared_on_its_drawn_baseline():
    from services.svg_text_defaults import drop_duplicate_native_table_headers

    on_drawn = '<text x="90" y="233" font-family="Pretendard" font-size="18" fill="#111111">항목</text>'
    root = ET.fromstring(_table_page(on_drawn, group_transform="translate(0,10)"))
    assert drop_duplicate_native_table_headers(root) == 1
    on_written = '<text x="90" y="223" font-family="Pretendard" font-size="18" fill="#111111">항목</text>'
    root = ET.fromstring(_table_page(on_written, group_transform="translate(0,10)"))
    assert drop_duplicate_native_table_headers(root) == 0


def test_a_malformed_declaration_drops_nothing():
    from services.svg_text_defaults import drop_duplicate_native_table_headers

    root = ET.fromstring(_svg(
        '<text x="90" y="223" font-family="Pretendard" font-size="18" fill="#111111">항목</text>'
        "<g id=\"body-native-table-x\" data-native-table='not json'>"
        '<text x="90" y="223" font-family="Pretendard" font-size="18" fill="#111111">항목</text></g>'))
    assert drop_duplicate_native_table_headers(root) == 0


def test_the_replay_drops_the_same_header_labels():
    from services.svg_author_output_view import parse_author_response
    from services.svg_text_defaults import drop_duplicate_native_table_headers

    raw = _table_page('<text x="90" y="223" font-family="Pretendard" font-size="18" fill="#243B7C">항목</text>')
    root = ET.fromstring(raw)
    drop_duplicate_native_table_headers(root)
    replayed, _receipt = parse_author_response(raw, view=None, source_facts=None)
    assert ET.tostring(replayed, encoding="unicode") == ET.tostring(root, encoding="unicode")


# --- what the author is told about text widths ----------------------------------------------

def test_face_metrics_are_the_fonts_own_advances():
    from services.svg_templates.frame_fonts import bundled_font_assets, face_text_metrics, measure_text

    asset = next(asset for asset in bundled_font_assets() if asset.weight == 700)
    metrics = face_text_metrics(asset)
    assert metrics["family"] == asset.family and metrics["weight"] == 700
    advance = metrics["advance_em"]
    assert set(advance) == {"hangul_max", "digit_max", "latin_lower_average", "latin_upper_average", "space"}
    assert advance["hangul_max"] == pytest.approx(measure_text(asset, "한", 1000)[0] / 1000, abs=0.002)
    assert advance["digit_max"] >= measure_text(asset, "1", 1000)[0] / 1000
    assert "%" in metrics["symbol_advance_em"]
    assert 0.6 < metrics["ink_above_baseline_em"] < 1.0 and 0.1 < metrics["ink_below_baseline_em"] < 0.35
    # Summing the listed advances bounds a figure from above, within a few percent.
    label = "52일 → 44일"
    estimate = (4 * advance["digit_max"] + 2 * advance["hangul_max"] + 2 * advance["space"]
                + metrics["symbol_advance_em"]["→"]) * 38
    actual = measure_text(asset, label, 38)[0]
    assert actual <= estimate <= actual * 1.05
    assert face_text_metrics(asset) is metrics  # cached per face


def test_prompt_facts_carry_the_page_faces_metrics(monkeypatch, tmp_path):
    from services.svg_templates import generation
    from tests.unit.test_svg_template_first_draft import _frame

    frame = _frame(tmp_path, {400, 700})
    monkeypatch.setattr(generation, "page_frame", lambda snapshot, index, count, role=None: frame)
    context = {"body_region": {"x": 68.0, "y": 187.0, "width": 1144.0, "height": 657.0},
               "page_index": 2, "page_count": 10}
    facts = generation.template_prompt_facts(context, "# 개요\n- 문장", snapshot={"svg_template": {}})
    assert [(item["family"], item["weight"]) for item in facts["template_text_metrics"]] == [
        (asset.family, asset.weight) for asset in frame.font_manifest.assets]
    assert "template_text_metrics" not in generation.template_prompt_facts(context, "# 개요\n- 문장")


def test_template_rules_explain_widths_stacking_and_units():
    from services.svg_templates.generation import BODY_LIMIT_RULES

    for phrase in ("template_text_metrics", "text-anchor middle", "ink_below_baseline_em",
                   "axis or column caption"):
        assert phrase in BODY_LIMIT_RULES


# --- repair hints that name what failed -----------------------------------------------------

def _text_metric(qid, text, size, box):
    return {"qid": qid, "has_owned_text": True, "owned_text": text, "font_size": size,
            "css_visible": True, "effective_opacity": 1,
            "text_fragments": [box], "text_ink_fragments": [box]}


def test_overlap_witnesses_name_the_texts_and_the_overlap():
    from services.generation_core.drivers.svg.render import TEXT_OVERLAP_INSTRUCTION, text_overlap_witnesses

    quality = {"failures": [{"kind": "text_overlap", "pairs": [["svg-8", "svg-9"], ["svg-5", "svg-17"]]}],
               "browser_metrics": {"elements": [
                   _text_metric("svg-8", "약 46%", 60, {"x": 92, "y": 214, "width": 201.3, "height": 54}),
                   _text_metric("svg-9", "3시간 10분", 22, {"x": 290, "y": 244, "width": 101, "height": 21}),
                   _text_metric("svg-5", "지표", 18, {"x": 90, "y": 208, "width": 31.6, "height": 17}),
                   _text_metric("svg-17", "지표", 18, {"x": 90, "y": 208, "width": 31.6, "height": 17})]}}
    witnesses = text_overlap_witnesses(quality)
    assert witnesses[0]["texts"] == ["약 46%", "3시간 10분"]
    assert witnesses[0]["overlap_px"] == {"x": 3.3, "y": 21.0}
    assert witnesses[0]["same_text"] is False and witnesses[1]["same_text"] is True
    assert "same_text" in TEXT_OVERLAP_INSTRUCTION
    assert text_overlap_witnesses({"failures": [], "browser_metrics": {"elements": []}}) == []
    # A pair whose measurement is missing is skipped, never invented.
    assert text_overlap_witnesses({"failures": quality["failures"], "browser_metrics": {}}) == []


def test_body_outside_failures_say_which_text_and_how_far(monkeypatch, tmp_path):
    from services.generation_core.drivers.svg.render import template_region_hint
    from tests.unit.test_svg_template_first_draft import _body_quality, _rect_metric

    body = ('<rect id="body-card" x="100" y="200" width="300" height="80" fill="#EEEEEE"/>'
            '<text id="body-figure" x="1060" y="300" font-family="Pretendard" font-size="38" fill="#111111">52일 → 44일</text>')
    frame, _ = _body_quality(monkeypatch, tmp_path, body, [])
    right = frame.body_region.x + frame.body_region.width
    _, quality = _body_quality(monkeypatch, tmp_path, body, [
        _rect_metric("svg-2", 100, 200, 300, 80),
        _text_metric("svg-3", "52일 → 44일", 38, {"x": right - 156, "y": 270, "width": 215.2, "height": 35})])
    element = quality["failures"][0]["elements"][0]
    assert element["text"] == "52일 → 44일" and element["tag"] == "text"
    assert element["past_edge_px"] == {"right": 59.2}
    hint = json.loads(template_region_hint(quality))
    assert hint["failures"][0]["elements"][0]["past_edge_px"] == {"right": 59.2}
    assert "past_edge_px" in hint["instruction"]


def test_witnesses_keep_a_json_hint_one_json_object():
    from services.generation_core.drivers.svg.render import with_text_overlap_witnesses

    witness = {"texts": ["가", "가"], "font_sizes": [16, 16], "ink_boxes": [], "overlap_px": {"x": 9, "y": 9},
               "same_text": True}
    hint = json.dumps({"kind": "svg_readability_repair", "failures": []})
    merged = json.loads(with_text_overlap_witnesses(hint, [witness] * 3))
    assert merged["kind"] == "svg_readability_repair" and len(merged["text_overlap_witnesses"]) == 3
    assert len(json.loads(with_text_overlap_witnesses(hint, [witness] * 3, limit=800))["text_overlap_witnesses"]) < 3
    assert with_text_overlap_witnesses(hint, []) == hint
    plain = with_text_overlap_witnesses("not json", [witness])
    assert plain.splitlines()[1] == "not json"
    assert json.loads(plain.splitlines()[0])["witnesses"] == [witness]
