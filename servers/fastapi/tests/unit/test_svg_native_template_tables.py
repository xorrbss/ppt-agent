"""Native tables and charts on template-redesign pages (synthetic, fictional data only).

Two causes measured on a template deck (2026-11-04) made every outline table fall back:
the frame composition left every declaration stale, and a wrapped cell drawn as one
``<text>`` per line failed the table's text multiset check.
"""
from __future__ import annotations

import json
from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg.native_declarations import (
    PAYLOAD_KEY, bind_native_declarations, bound_declarations, extract_native_declarations,
    native_author_rules, rebind_native_declarations,
)
from services.generation_core.drivers.svg.native_geometry import NativeRejection, collect_primitives, find_group
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.generation_core.drivers.svg.native_requirements import (
    apply_requested_native_quality, requested_native_data,
)
from services.generation_core.drivers.svg.native_table import TABLE_ELEMENTS, fold_wrapped_cell_lines, table_object
from services.svg_contract import validate_svg

NS = "http://www.w3.org/2000/svg"
HEAD = f'<svg xmlns="{NS}" width="1280" height="720" viewBox="0 0 1280 720">'
FONT = 'font-family="Noto Sans KR" font-size="18" fill="#222222"'
ROWS = [["구분", "내용"], ["범위", "주문, 재고, 출하 모듈"], ["기간", "6개월"]]
SOURCE = "범위와 기간\n| 구분 | 내용 |\n| --- | --- |\n| 범위 | 주문, 재고, 출하 모듈 |\n| 기간 | 6개월 |\n"


def _attr(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False).replace("&", "&amp;").replace("'", "&apos;")


def _text(x, y, text, font=FONT):
    return f'<text x="{x}" y="{y}" {font}>{text}</text>'


GRID = ('<rect x="100" y="200" width="600" height="180" fill="none" stroke="#333333"/>'
        '<line x1="300" y1="200" x2="300" y2="380" stroke="#CCCCCC"/>'
        + "".join(f'<line x1="100" y1="{y}" x2="700" y2="{y}" stroke="#CCCCCC"/>' for y in (260, 320)))
# The long cell is drawn as two texts, one per wrapped line, 22 px apart (1.2 font sizes).
WRAPPED = (_text(112, 238, "구분") + _text(312, 238, "내용") + _text(112, 298, "범위")
           + _text(312, 284, "주문, 재고,") + _text(312, 306, "출하 모듈")
           + _text(112, 358, "기간") + _text(312, 358, "6개월"))


def _group(cells: str, rows=ROWS, group_id="body-native-table-scope") -> str:
    return (f'<g id="{group_id}" data-native-table=\'{_attr({"rows": rows, "headerRows": 1})}\'>'
            f"{GRID}{cells}</g>")


def _authored(body: str) -> tuple[dict, str, dict | None]:
    """(payload, drawn svg, declarations) as the author step leaves them."""
    root = ET.fromstring(HEAD + body + "</svg>")
    declarations = extract_native_declarations(root)
    drawn = ET.tostring(root, encoding="unicode")
    payload: dict = {}
    bind_native_declarations(payload, drawn, declarations)
    payload["svg_content"] = drawn
    return payload, drawn, declarations


def _compose(drawn: str) -> str:
    """What ``compose_frame_svg`` does to a body: ``body-node-N`` ids on id-less nodes, the
    body inside ``svg-system-template-body`` between the frame's background and foreground."""
    body = ET.fromstring(drawn)
    for index, node in enumerate(body.iter()):
        node.set("id", node.get("id") or f"body-node-{index}")
    root = ET.Element(f"{{{NS}}}svg", {"width": "1280", "height": "720", "viewBox": "0 0 1280 720"})
    background = ET.SubElement(root, f"{{{NS}}}g", {"id": "svg-system-template-background"})
    ET.SubElement(background, f"{{{NS}}}rect", {"x": "0", "y": "0", "width": "1280", "height": "120",
                                                "fill": "#0B2D5B"})
    content = ET.SubElement(root, f"{{{NS}}}g", {"id": "svg-system-template-body"})
    content.extend(deepcopy(list(body)))
    ET.SubElement(root, f"{{{NS}}}g", {"id": "svg-system-template-foreground"})
    return ET.tostring(root, encoding="unicode")


# --- wrapped cell lines ---------------------------------------------------------------

def test_a_cell_drawn_one_text_per_line_is_folded_and_accepted():
    payload, drawn, declarations = _authored(_group(WRAPPED))
    [declared] = declarations["objects"]
    assert declared["raw"]["rows"][1][1] == "주문, 재고,\n출하 모듈"
    validate_svg(drawn)
    root = ET.fromstring(drawn)
    [folded] = [node for node in root.iter(f"{{{NS}}}text") if node.text == "주문, 재고,"]
    assert [(tspan.get("x"), tspan.get("y"), tspan.text) for tspan in folded] == [("312", "306", "출하 모듈")]
    assert len(list(root.iter(f"{{{NS}}}text"))) == 6
    wire, summary = page_native_objects(payload, pt_per_px=0.75)
    assert summary == {"declared": 1, "accepted": 1, "dropped": []}
    [entry] = wire["objects"]
    assert [cell["text"] for cell in entry["payload"]["rows"][1]] == ["범위", "주문, 재고,\n출하 모듈"]
    # The outline's table (the source cell without the visual wrap) is satisfied.
    quality = {"passed": True}
    apply_requested_native_quality(quality, payload, drawn, SOURCE, pt_per_px=0.75)
    assert quality == {"passed": True, "requestedNativeObjects": quality["requestedNativeObjects"]}
    assert quality["requestedNativeObjects"]["passed"] is True


def test_without_the_fold_the_same_drawing_was_rejected():
    root = ET.fromstring(HEAD + _group(WRAPPED) + "</svg>")
    next(node for node in root.iter() if node.get("data-native-table")).attrib.pop("data-native-table")
    found = find_group(root, "body-native-table-scope")
    with pytest.raises(NativeRejection, match="native_table_text_unaccounted"):
        table_object({"rows": deepcopy(ROWS), "headerRows": 1},
                     collect_primitives(*found, allowed=TABLE_ELEMENTS), pt_per_px=0.75, layers=[])


def test_a_table_the_text_check_already_accepts_is_not_changed():
    cells = WRAPPED.replace(_text(312, 284, "주문, 재고,") + _text(312, 306, "출하 모듈"),
                            _text(312, 298, "주문, 재고, 출하 모듈"))
    group = ET.fromstring(f'<g xmlns="{NS}">{GRID}{cells}</g>')
    before = ET.tostring(group, encoding="unicode")
    raw = {"rows": deepcopy(ROWS), "headerRows": 1}
    assert fold_wrapped_cell_lines(group, raw) == 0
    assert ET.tostring(group, encoding="unicode") == before and raw["rows"] == ROWS


def test_cells_of_different_rows_are_not_joined():
    # "범위" and "기간" are a row apart (60 px, 3.3 font sizes); a declared cell spelling
    # both must not be assembled from them.
    rows = [["구분", "내용"], ["범위 기간", "주문, 재고, 출하 모듈"], ["", "6개월"]]
    group = ET.fromstring(f'<g xmlns="{NS}">{GRID}{WRAPPED}</g>')
    raw = {"rows": deepcopy(rows), "headerRows": 1}
    assert fold_wrapped_cell_lines(group, raw) == 1  # only the wrapped content cell
    assert raw["rows"][1] == ["범위 기간", "주문, 재고,\n출하 모듈"]


def test_lines_with_a_different_style_or_spelling_are_not_joined():
    other = 'font-family="Noto Sans KR" font-size="18" fill="#C00000"'
    restyled = WRAPPED.replace(_text(312, 306, "출하 모듈"), _text(312, 306, "출하 모듈", font=other))
    respelled = WRAPPED.replace(">출하 모듈<", ">출고 모듈<")
    for cells in (restyled, respelled):
        group = ET.fromstring(f'<g xmlns="{NS}">{GRID}{cells}</g>')
        raw = {"rows": deepcopy(ROWS), "headerRows": 1}
        assert fold_wrapped_cell_lines(group, raw) == 0 and raw["rows"] == ROWS


# --- template composition --------------------------------------------------------------

def test_declarations_follow_the_drawing_into_its_template_composition():
    payload, drawn, _ = _authored(_group(WRAPPED))
    composed = _compose(drawn)
    validate_svg(composed)
    payload["svg_content"] = composed
    # Before: the composed page never had the drawing's digest.
    wire, summary = page_native_objects(payload, pt_per_px=0.75)
    assert wire is None and summary["dropped"][0]["reason"] == "native_declaration_stale_svg"
    quality = {"passed": True}
    apply_requested_native_quality(quality, payload, composed, SOURCE, pt_per_px=0.75)
    assert [failure["kind"] for failure in quality["failures"]] == ["native_requested_table_missing"]

    assert rebind_native_declarations(payload, drawn, composed) is True
    assert bound_declarations(payload, composed)[1] is True
    wire, summary = page_native_objects(payload, pt_per_px=0.75)
    assert summary == {"declared": 1, "accepted": 1, "dropped": []}
    assert wire["objects"][0]["id"] == "body-native-table-scope"
    quality = {"passed": True}
    apply_requested_native_quality(quality, payload, composed, SOURCE, pt_per_px=0.75)
    assert quality["passed"] is True and "failures" not in quality


def test_only_the_bound_drawing_can_hand_its_declarations_on():
    payload, drawn, _ = _authored(_group(WRAPPED))
    corrected = drawn.replace(">6개월<", ">7개월<")
    record = deepcopy(payload[PAYLOAD_KEY])
    assert rebind_native_declarations(payload, corrected, _compose(corrected)) is False
    assert payload[PAYLOAD_KEY] == record
    # A page without declarations stays without them.
    empty: dict = {}
    assert rebind_native_declarations(empty, drawn, _compose(drawn)) is False and PAYLOAD_KEY not in empty


# --- prompt text -----------------------------------------------------------------------

def test_template_rules_name_native_groups_in_the_body_namespace(monkeypatch):
    monkeypatch.setenv("SVG_TEMPLATE_BODY_EXEMPLARS", "off")  # composition guidance is tested elsewhere
    from services.svg_templates.generation import body_author_rules
    from utils.hashing import canonical_json_digest

    selection = {"schema_version": "svg-template-selection/v1", "mode": "template-redesign",
                 "template_pin": {"templateId": "t", "revision": 1, "digest": "sha256:" + "a" * 64}}
    selection["digest"] = canonical_json_digest(selection)
    rules = body_author_rules(native_author_rules(), {"svg_template": selection, "composition_guidance": []})
    assert 'id="body-native-table-SLUG"' in rules and 'id="body-native-chart-SLUG"' in rules
    assert 'id="native-table-SLUG"' not in rules and 'id="native-chart-SLUG"' not in rules
    # Outside template-redesign the rule text is unchanged.
    assert body_author_rules(native_author_rules(), {}) == native_author_rules()


def test_the_outline_chart_form_is_what_the_native_requirements_read():
    from utils.content_structure_policy import StructurePolicy, outline_table_clause

    clause = outline_table_clause(StructurePolicy())
    form = clause.split("in the form ", 1)[1].split(".", 1)[0]
    assert form == "chart: Label value unit, Label value unit"
    slide = "투입 인력\nchart: 관리자 1명, 분석가 4명, 개발자 10명\n"
    [chart] = requested_native_data(slide)["charts"]
    assert chart["categories"] == ["관리자", "분석가", "개발자"] and chart["values"] == ["1", "4", "10"]
    assert chart["requiredUnitLabels"] == [{"text": "명", "unit": "명"}] and "unsupportedReason" not in chart


# --- native chart on a template page ---------------------------------------------------

CHART_SOURCE = "투입 인력\nchart: 관리자 1명, 분석가 4명, 개발자 10명\n"


def _bar_chart(series_name: str) -> str:
    spec = {"chartType": "bar", "categories": ["관리자", "분석가", "개발자"],
            "series": [{"name": series_name, "values": [1, 4, 10]}], "categoryOrder": "topToBottom",
            "valueLabels": {"shown": True, "position": "outsideEnd"}, "title": "역할별 인원 (명)"}
    text = 'font-family="Noto Sans KR" font-size="15" fill="#1A1F2B"'
    bars = "".join(
        f'<text x="220" y="{334 + 31 * i}" text-anchor="end" {text}>{label}</text>'
        f'<rect x="230" y="{318 + 31 * i}" width="{40 * value}" height="22" fill="#005ED0"/>'
        f'<text x="{238 + 40 * value}" y="{335 + 31 * i}" {text}>{value}명</text>'
        for i, (label, value) in enumerate(zip(spec["categories"], [1, 4, 10], strict=True)))
    return (f'<g id="body-native-chart-roles" data-native-chart=\'{_attr(spec)}\'>'
            f'<text x="68" y="296" font-family="Noto Sans KR" font-size="16" font-weight="bold" fill="#243B7C">'
            f'역할별 인원 (명)</text><line x1="230" y1="310" x2="230" y2="410" stroke="#D5DBE1"/>{bars}</g>')


def test_a_single_series_chart_named_by_its_drawn_title_passes_on_a_template_page():
    payload, drawn, _ = _authored(_bar_chart("역할별 인원 (명)"))
    composed = _compose(drawn)
    assert rebind_native_declarations(payload, drawn, composed)
    payload["svg_content"] = composed
    wire, summary = page_native_objects(payload, pt_per_px=0.75)
    assert summary == {"declared": 1, "accepted": 1, "dropped": []}
    assert wire["objects"][0]["kind"] == "chart"
    quality = {"passed": True}
    apply_requested_native_quality(quality, payload, composed, CHART_SOURCE, pt_per_px=0.75)
    assert quality["passed"] is True and quality["requestedNativeObjects"]["passed"] is True


def test_a_series_name_that_is_not_drawn_drops_the_chart():
    # The measured failure the author clause addresses: the name only lives in the JSON.
    payload, drawn, _ = _authored(_bar_chart("인원(명)"))
    wire, summary = page_native_objects(payload, pt_per_px=0.75)
    assert wire is None and summary["dropped"][0]["reason"] == "native_chart_label_not_drawn"


def test_a_chart_title_drawn_outside_the_group_drops_the_chart():
    chart = _bar_chart("역할별 인원 (명)")
    title = ('<text x="68" y="296" font-family="Noto Sans KR" font-size="16" font-weight="bold" fill="#243B7C">'
             '역할별 인원 (명)</text>')
    payload, _, _ = _authored(title + chart.replace(title, ""))
    wire, summary = page_native_objects(payload, pt_per_px=0.75)
    assert wire is None and summary["dropped"][0]["reason"] == "native_chart_label_not_drawn"


def test_the_policy_author_clause_names_the_chart_series_rule():
    from utils.content_structure_policy import StructurePolicy, author_rules

    rules = author_rules("rules", StructurePolicy(), template_ids=True)
    assert "declare the exact drawn chart title as the" in rules and "inside the" in rules
    assert author_rules("rules", None) == "rules"


# --- composition errors the next attempt can act on ------------------------------------

def _template_frame(tmp_path):
    """A one-slide template (header band, footer bars) through the real extraction."""
    from services.svg_templates.frame import extract_template_package
    from tests.unit.test_svg_template_raster_preservation import _FakeRenderer, _band_and_footer, _deck

    return extract_template_package(_deck(tmp_path / "deck.pptx", _band_and_footer),
                                    raster_renderer=_FakeRenderer()).frames[0]


def test_composition_errors_name_what_to_change(tmp_path):
    from services.svg_templates.frame_composition import compose_frame_svg

    frame = _template_frame(tmp_path)
    region = frame.body_region

    def body(attributes: str) -> str:
        return (f'<svg xmlns="{NS}" width="1280" height="720" viewBox="0 0 1280 720">'
                f'<text {attributes} x="{region.x + 10}" y="{region.y + 40}" font-family="Pretendard" '
                'font-size="20" fill="#111111">본문 문장</text></svg>')

    compose_frame_svg(frame, body('id="body-claim" font-weight="400"'), dynamic_values={})
    # A missing weight of a face the page has is drawn as the browser would (test_svg_template_first_draft);
    # a family the page does not have still fails, naming the faces it has.
    with pytest.raises(ValueError) as error:
        compose_frame_svg(frame, body('id="body-claim" font-weight="900"').replace("Pretendard", "Noto Serif"),
                          dynamic_values={})
    message = str(error.value)
    assert message.startswith("svg_template_font_unavailable: a text uses font-family 'Noto Serif' weight 900;")
    assert "Pretendard 400" in message and "explicit font-family and font-weight" in message
    with pytest.raises(ValueError) as error:
        compose_frame_svg(frame, body('id="native-table-x" font-weight="400"'), dynamic_values={})
    assert str(error.value) == "svg_template_body_namespace_required: id 'native-table-x' must start with body-"
    from services.generation_core.drivers.svg.attempt_cause import static_cause
    assert static_cause(validation_error=message)["code"] == "static_quality:svg_template_font_unavailable"


# --- the writing policy's symbol substitution -------------------------------------------

def test_declarations_follow_the_visible_symbol_substitution():
    from services.ppt_writing_visible import substitute_svg_visible_symbols
    from services.generation_core.drivers.svg.native_declarations import follow_visible_text_edits

    rows = [["구분", "내용"], ["범위", "주문·재고·출하 모듈"], ["기간", "6개월"]]
    cells = WRAPPED.replace(_text(312, 284, "주문, 재고,") + _text(312, 306, "출하 모듈"),
                            _text(312, 298, "주문·재고·출하 모듈"))
    payload, drawn, _ = _authored(_group(cells, rows=rows))
    composed = _compose(drawn)
    assert rebind_native_declarations(payload, drawn, composed)
    final, substitutions = substitute_svg_visible_symbols(composed)
    assert substitutions and "주문, 재고, 출하 모듈" in final
    payload["svg_content"] = final
    assert page_native_objects(payload, pt_per_px=0.75)[1]["dropped"][0]["reason"] == "native_declaration_stale_svg"

    assert follow_visible_text_edits(payload, composed, final) is True
    wire, summary = page_native_objects(payload, pt_per_px=0.75)
    assert summary == {"declared": 1, "accepted": 1, "dropped": []}
    assert wire["objects"][0]["payload"]["rows"][1][1]["text"] == "주문, 재고, 출하 모듈"
    quality = {"passed": True}
    # The outline's cell (without the symbol) now matches the drawing and its declaration.
    apply_requested_native_quality(quality, payload, final, SOURCE, pt_per_px=0.75)
    assert quality["passed"] is True


def test_a_substitution_that_is_not_text_only_is_not_followed():
    from services.generation_core.drivers.svg.native_declarations import follow_visible_text_edits

    payload, drawn, _ = _authored(_group(WRAPPED))
    record = deepcopy(payload[PAYLOAD_KEY])
    moved = drawn.replace('x="112" y="238"', 'x="114" y="238"')
    assert follow_visible_text_edits(payload, drawn, moved) is False and payload[PAYLOAD_KEY] == record
    assert follow_visible_text_edits(payload, moved, moved) is False  # not the bound drawing
