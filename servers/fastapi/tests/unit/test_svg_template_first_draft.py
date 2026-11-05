"""First-draft failures of template pages (synthetic, fictional data only).

Measured 2026-11-04 on 250 stored template pages (docs/svg-template-first-draft-20261104.md):
covers with only a bold face, whitespace spacer texts and zero-size shapes, a body edge given
only as an offset plus a long fractional height, a fractional text minimum, reformatted
figures, and hand-written arrowheads pointing backwards.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

NS = "http://www.w3.org/2000/svg"


# --- font weights: the face the browser draws ------------------------------------------

def test_css_weight_matching():
    from services.svg_templates.frame_fonts import css_matched_weight

    assert css_matched_weight(400, [700]) == 700
    assert css_matched_weight(400, [400, 700]) == 400
    assert css_matched_weight(500, [400, 700]) == 400
    assert css_matched_weight(450, [300, 500, 700]) == 500
    assert css_matched_weight(300, [400, 700]) == 400
    assert css_matched_weight(300, [200, 400]) == 200
    assert css_matched_weight(600, [400, 700]) == 700
    assert css_matched_weight(900, [400, 700]) == 700
    assert css_matched_weight(700, [400]) == 400
    assert css_matched_weight(400, []) is None


def _frame(tmp_path, weights):
    from services.svg_templates.frame_fonts import artifact_digest, build_font_manifest
    from tests.unit.test_svg_native_template_tables import _template_frame

    frame = _template_frame(tmp_path)
    manifest = build_font_manifest(tuple(asset for asset in frame.font_manifest.assets if asset.weight in weights))
    frame = frame.model_copy(update={"font_manifest": manifest, "digest": ""})
    return frame.model_copy(update={"digest": artifact_digest(frame)})


def _body(frame, *weights):
    region = frame.body_region
    texts = "".join(
        f'<text id="body-t{i}" x="{region.x + 10}" y="{region.y + 40 * (i + 1)}" font-family="Pretendard"'
        f' font-size="20" fill="#111111"{"" if weight is None else f" font-weight={chr(34)}{weight}{chr(34)}"}>'
        f"본문 문장 {i}</text>" for i, weight in enumerate(weights))
    return f'<svg xmlns="{NS}" width="1280" height="720" viewBox="0 0 1280 720">{texts}</svg>'


def _weights(svg):
    return [node.get("font-weight") for node in ET.fromstring(svg).iter(f"{{{NS}}}text")
            if (node.get("id") or "").startswith("body-t")]


def test_a_bold_only_page_draws_regular_text_with_its_bold_face(tmp_path):
    from services.svg_templates.frame_composition import compose_frame_svg

    frame = _frame(tmp_path, {700})
    composed = compose_frame_svg(frame, _body(frame, None, "normal", "400", "bold", "700"), dynamic_values={})
    assert _weights(composed) == ["700", "700", "700", "bold", "700"]


def test_heavier_and_lighter_weights_take_the_nearest_face(tmp_path):
    from services.svg_templates.frame_composition import compose_frame_svg

    frame = _frame(tmp_path, {400, 700})
    composed = compose_frame_svg(frame, _body(frame, "900", "600", "500", "300", "400"), dynamic_values={})
    assert _weights(composed) == ["700", "700", "400", "400", "400"]


def test_bold_on_a_regular_only_page_still_fails(tmp_path):
    # A browser synthesises bold from the regular face; writing 400 would drop the emphasis.
    from services.svg_templates.frame_composition import compose_frame_svg

    frame = _frame(tmp_path, {400})
    compose_frame_svg(frame, _body(frame, None, "normal"), dynamic_values={})
    with pytest.raises(ValueError, match="svg_template_font_unavailable: a text uses font-family 'Pretendard' weight 700"):
        compose_frame_svg(frame, _body(frame, "bold"), dynamic_values={})


def test_a_family_the_page_lacks_still_fails(tmp_path):
    from services.svg_templates.frame_composition import compose_frame_svg

    frame = _frame(tmp_path, {700})
    with pytest.raises(ValueError, match="this page has only the faces Pretendard 700"):
        compose_frame_svg(frame, _body(frame, None).replace("Pretendard", "Noto Serif"), dynamic_values={})


def test_a_tspan_follows_its_normalised_text(tmp_path):
    from services.svg_templates.frame_composition import compose_frame_svg

    frame = _frame(tmp_path, {700})
    region = frame.body_region
    body = (f'<svg xmlns="{NS}" width="1280" height="720" viewBox="0 0 1280 720">'
            f'<text id="body-a" x="{region.x + 10}" y="{region.y + 40}" font-family="Pretendard" font-size="20"'
            f' fill="#111111"><tspan x="{region.x + 10}" y="{region.y + 40}">첫 줄</tspan>'
            f'<tspan x="{region.x + 10}" y="{region.y + 70}">둘째 줄</tspan></text></svg>')
    root = ET.fromstring(compose_frame_svg(frame, body, dynamic_values={}))
    text = next(node for node in root.iter(f"{{{NS}}}text") if node.get("id") == "body-a")
    assert text.get("font-weight") == "700"
    assert all(span.get("font-weight") in (None, "700") for span in text)


# --- body paint: a whitespace text paints nothing ---------------------------------------

def _body_quality(monkeypatch, tmp_path, body: str, metrics: list[dict]):
    from services.svg_templates import generation

    frame = _frame(tmp_path, {400, 700})
    monkeypatch.setattr(generation, "page_frame", lambda snapshot, index, count, role=None, chapter=None: frame)
    svg = (f'<svg xmlns="{NS}" width="1280" height="720" viewBox="0 0 1280 720">'
           f'<g id="svg-system-template-body">{body}</g></svg>')
    quality = {"browser_metrics": {"elements": metrics}, "failures": [], "passed": True}
    context = {"page_index": 1, "page_count": 3, "digest": "d", "frame_digest": "f", "adaptation_digest": "a"}
    generation.apply_template_body_quality(quality, svg, {}, context)
    return frame, quality


def _rect_metric(qid, x, y, width, height):
    return {"qid": qid, "css_visible": True, "effective_opacity": 1, "has_owned_text": False,
            "bbox": {"x": x, "y": y, "width": width, "height": height}}


def test_a_whitespace_spacer_text_is_not_body_paint(monkeypatch, tmp_path):
    body = ('<rect id="body-card" x="100" y="200" width="300" height="80" fill="#EEEEEE"/>'
            '<text id="body-gap" x="100" y="300" font-family="Pretendard" font-size="1" fill="#FFFFFF"> </text>')
    # The browser measures the blank text as an empty box at the origin.
    _frame_, quality = _body_quality(monkeypatch, tmp_path, body, [
        _rect_metric("svg-2", 100, 200, 300, 80), _rect_metric("svg-3", 0, 0, 0, 0)])
    assert quality["failures"] == [] and quality["passed"] is True
    assert quality["template_preservation"]["body_paint_measured"] is True


def test_paint_outside_the_body_still_fails(monkeypatch, tmp_path):
    body = ('<rect id="body-card" x="100" y="200" width="300" height="80" fill="#EEEEEE"/>'
            '<text id="body-note" x="100" y="300" font-family="Pretendard" font-size="16" fill="#111111">메모</text>')
    frame, quality = _body_quality(monkeypatch, tmp_path, body, [
        _rect_metric("svg-2", 100, 200, 300, 80),
        _rect_metric("svg-3", 100, frame_bottom := 700, 40, 16)])
    assert frame.body_region.y + frame.body_region.height < frame_bottom
    assert [failure["kind"] for failure in quality["failures"]] == ["svg_template_body_outside_allowed_region"]
    assert quality["failures"][0]["elements"][0]["id"] == "body-note"


# --- what the author is told -------------------------------------------------------------

def test_body_edges_are_whole_pixels_inside_the_region():
    from services.svg_templates.generation import template_body_edges

    context = {"body_region": {"x": 68.0, "y": 187.00493150684932, "width": 1144.0, "height": 656.9950684931507}}
    assert template_body_edges(context) == {"left": 68, "top": 188, "right": 1212, "bottom": 844}
    context = {"body_region": {"x": 68.4, "y": 190.0, "width": 1143.2, "height": 600.6}}
    assert template_body_edges(context) == {"left": 69, "top": 190, "right": 1211, "bottom": 790}


def test_prompt_facts_carry_edges_and_the_checked_figures():
    from services.svg_presentation_checkpoint import _required_numeric_claims
    from services.svg_templates.generation import template_prompt_facts

    context = {"body_region": {"x": 68.0, "y": 187.0, "width": 1144.0, "height": 657.0}, "digest": "x"}
    content = "# 사업 개요\n- 기간: 2027년 3월 ~ 2028년 2월\n- 예산 12억 원"
    facts = template_prompt_facts(context, content)
    assert facts["template_page"] is context
    assert facts["template_body_edges"]["bottom"] == 844
    assert facts["required_literals"] == _required_numeric_claims(content)
    assert "2027년" in facts["required_literals"]
    assert "required_literals" not in template_prompt_facts(context, "# 개요\n- 숫자 없는 문장")
    assert "required_literals" not in template_prompt_facts(context, content, source_preserved=True)
    assert template_prompt_facts(None, content) == {}


def test_template_rules_explain_weights_edges_and_literals(monkeypatch):
    monkeypatch.setenv("SVG_TEMPLATE_BODY_EXEMPLARS", "off")  # composition guidance is tested elsewhere
    from services.svg_templates.generation import BODY_LIMIT_RULES, body_author_rules
    from utils.hashing import canonical_json_digest

    selection = {"schema_version": "svg-template-selection/v1", "mode": "template-redesign",
                 "template_pin": {"templateId": "t", "revision": 1, "digest": "sha256:" + "a" * 64}}
    selection["digest"] = canonical_json_digest(selection)
    rules = body_author_rules("rules", {"svg_template": selection, "composition_guidance": []})
    assert BODY_LIMIT_RULES in rules
    for phrase in ("without font-weight is weight 400", "template_body_edges", "required_literals"):
        assert phrase in rules
    assert body_author_rules("rules", {}) == "rules"


def test_a_fractional_text_minimum_names_the_half_pixel_above_it():
    from services.generation_core.authoring.author_slide_svg import _floor_rounding_clause

    assert "font-size 14.5 or larger" in _floor_rounding_clause(14.158054946990262)
    assert "font-size 15 or larger" in _floor_rounding_clause(14.6)
    assert _floor_rounding_clause(14.0) == "" and _floor_rounding_clause(14.5) == ""
    assert _floor_rounding_clause(float("nan")) == ""


# --- shapes SVG does not render ----------------------------------------------------------

def test_zero_size_shapes_are_dropped_and_others_kept():
    from services.svg_text_defaults import drop_zero_size_shapes

    root = ET.fromstring(
        f'<svg xmlns="{NS}" width="100" height="100" viewBox="0 0 100 100">'
        '<rect id="a" x="1" y="1" width="50" height="0" fill="none"/>'
        '<g><rect id="b" x="1" y="1" width="0" height="5" fill="#243B7C"/><circle id="c" cx="5" cy="5" r="0"/></g>'
        '<rect id="d" x="1" y="1" width="5" height="-1"/><rect id="e" x="1" y="1" width="5" height="5"/>'
        '<rect id="f" x="1" y="1" width="5" height="abc"/><circle id="g" cx="5" cy="5" r="2"/></svg>')
    assert drop_zero_size_shapes(root) == 3
    assert [node.get("id") for node in root.iter() if node.get("id")] == ["d", "e", "f", "g"]


def test_a_page_with_only_zero_size_spacers_now_validates_and_replays_the_same():
    from services.svg_author_output_view import parse_author_response
    from services.svg_contract import validate_svg
    from services.svg_text_defaults import drop_zero_size_shapes

    raw = (f'<svg xmlns="{NS}" width="1280" height="720" viewBox="0 0 1280 720">'
           '<rect x="68" y="780" width="1144" height="0" fill="none"/>'
           '<text x="68" y="700" font-family="Pretendard" font-size="20" fill="#111111">결론 문장</text></svg>')
    with pytest.raises(ValueError, match="svg_contract_positive_required"):
        validate_svg(raw, width=1280, height=720)
    root = ET.fromstring(raw)
    drop_zero_size_shapes(root)
    produced = ET.tostring(root, encoding="unicode")
    validate_svg(produced, width=1280, height=720)
    replayed, _receipt = parse_author_response(raw, view=None, source_facts=None)
    assert ET.tostring(replayed, encoding="unicode") == produced


# --- arrowheads that point back along a drawn sequence ------------------------------------

STAGES = ["현황 분석", "표준 설계", "구축", "통합 시험", "안정화"]
STAGE_SOURCE = "## 전환 단계\n1. 현황 분석\n2. 표준 설계\n3. 구축\n4. 통합 시험\n5. 안정화\n"


def _row(direction: int, *, count: int = 5, names=STAGES) -> str:
    """A row of boxes 180 wide with 30 px gaps and a triangle in every gap."""
    parts = []
    for i in range(count):
        x = 80 + 210 * i
        parts.append(f'<rect x="{x}" y="300" width="180" height="60" fill="#243B7C"/>'
                     f'<text x="{x + 90}" y="336" text-anchor="middle" font-family="Pretendard" font-size="18"'
                     f' fill="#FFFFFF">{names[i]}</text>')
        if i < count - 1:
            gap = x + 180
            points = (f"{gap + 22},330 {gap + 8},322 {gap + 8},338" if direction > 0
                      else f"{gap + 8},330 {gap + 22},322 {gap + 22},338")
            parts.append(f'<polygon id="body-arrow-{i}" points="{points}" fill="#005ED0"/>')
    return f'<svg xmlns="{NS}" width="1280" height="912" viewBox="0 0 1280 912">{"".join(parts)}</svg>'


def _column(direction: int, names, *, translate: bool = False) -> str:
    """Stacked layers 1120 wide, 120 high, 30 px apart, each in a translated group when asked."""
    parts = []
    for i, name in enumerate(names):
        y = 200 + 150 * i
        layer = (f'<rect x="80" y="{y}" width="1120" height="120" fill="#FFFFFF" stroke="#005ED0"/>'
                 f'<text x="104" y="{y + 60}" font-family="Pretendard" font-size="20" fill="#243B7C">{name}</text>')
        parts.append(f'<g transform="translate(0,-10)">{layer}</g>' if translate else layer)
        if i < len(names) - 1:
            gap = y + 120 - (10 if translate else 0)
            points = (f"640,{gap + 22} 628,{gap + 8} 652,{gap + 8}" if direction > 0
                      else f"640,{gap + 8} 628,{gap + 22} 652,{gap + 22}")
            parts.append(f'<polygon points="{points}" fill="#005ED0"/>')
    return f'<svg xmlns="{NS}" width="1280" height="912" viewBox="0 0 1280 912">{"".join(parts)}</svg>'


LAYERS = ["화면 계층", "업무 서비스 계층", "공통 플랫폼 계층", "인프라 계층"]
LAYER_SOURCE = "## 시스템 구조\n- 화면 계층\n- 업무 서비스 계층\n- 공통 플랫폼 계층\n- 인프라 계층\n위 계층은 바로 아래 계층만 호출한다."


def test_a_forward_row_passes():
    from services.generation_core.drivers.svg.connector_direction import reversed_arrowhead_chains

    assert reversed_arrowhead_chains(_row(+1), STAGE_SOURCE) == []


def test_a_reversed_row_is_flagged_with_its_arrowheads():
    from services.generation_core.drivers.svg.connector_direction import reversed_arrowhead_chains

    chains = reversed_arrowhead_chains(_row(-1), STAGE_SOURCE)
    assert len(chains) == 1 and chains[0]["axis"] == "horizontal" and chains[0]["count"] == 4
    first = chains[0]["arrowheads"][0]
    assert first["id"] == "body-arrow-0" and first["points_to"] == ["현황분석"] and first["points_from"] == ["표준설계"]


def test_a_reversed_stack_is_flagged_through_translated_groups():
    from services.generation_core.drivers.svg.connector_direction import reversed_arrowhead_chains

    assert reversed_arrowhead_chains(_column(+1, LAYERS, translate=True), LAYER_SOURCE) == []
    chains = reversed_arrowhead_chains(_column(-1, LAYERS, translate=True), LAYER_SOURCE)
    assert len(chains) == 1 and chains[0]["axis"] == "vertical" and chains[0]["count"] == 3


def test_an_upward_stack_the_source_lists_from_the_bottom_passes():
    # Data flows up: the source names the bottom layer first, so up arrows follow the source.
    from services.generation_core.drivers.svg.connector_direction import reversed_arrowhead_chains

    source = "## 데이터 흐름\n- 인프라 계층에서 수집\n- 공통 플랫폼 계층\n- 업무 서비스 계층\n- 화면 계층에 표시"
    assert reversed_arrowhead_chains(_column(-1, LAYERS), source) == []


def test_a_single_return_arrow_and_a_two_way_row_pass():
    from services.generation_core.drivers.svg.connector_direction import reversed_arrowhead_chains

    # Two boxes, one backward triangle: a loop back to the earlier step.
    assert reversed_arrowhead_chains(_row(-1, count=2), STAGE_SOURCE) == []
    # One forward triangle among backward ones: drawn both ways on purpose.
    svg = _row(-1).replace('points="268,330 282,322 282,338"', 'points="282,330 268,322 268,338"')
    assert svg != _row(-1)
    assert reversed_arrowhead_chains(svg, STAGE_SOURCE) == []


def test_unknown_source_order_and_loose_triangles_pass():
    from services.generation_core.drivers.svg.connector_direction import reversed_arrowhead_chains

    assert reversed_arrowhead_chains(_row(-1), "출처에 단계 이름이 없다") == []
    loose = (f'<svg xmlns="{NS}" width="1280" height="912" viewBox="0 0 1280 912">'
             '<polygon points="100,100 114,92 114,108" fill="#005ED0"/>'
             '<polygon points="300,100 314,92 314,108" fill="#005ED0"/></svg>')
    assert reversed_arrowhead_chains(loose, STAGE_SOURCE) == []


def test_template_furniture_is_not_read_but_the_body_is():
    from services.generation_core.drivers.svg.connector_direction import reversed_arrowhead_chains

    inner = _row(-1).split(">", 1)[1].rsplit("</svg>", 1)[0]
    head = f'<svg xmlns="{NS}" width="1280" height="912" viewBox="0 0 1280 912">'
    assert reversed_arrowhead_chains(head + f'<g id="svg-system-template-body">{inner}</g></svg>', STAGE_SOURCE)
    assert reversed_arrowhead_chains(head + f'<g id="svg-system-template-foreground">{inner}</g></svg>',
                                     STAGE_SOURCE) == []


def test_the_failure_is_a_layout_failure_with_a_repair_instruction():
    from services.generation_core.drivers.svg.connector_direction import (
        FAILURE_KIND, apply_connector_direction_quality)
    from services.generation_core.quality.page_failure_kind import LAYOUT_FAILURE_KINDS

    quality = {"failures": [], "passed": True, "geometry_passed": True}
    apply_connector_direction_quality(quality, _row(+1), STAGE_SOURCE)
    assert quality == {"failures": [], "passed": True, "geometry_passed": True}
    apply_connector_direction_quality(quality, _row(-1), STAGE_SOURCE)
    assert quality["passed"] is False and quality["geometry_passed"] is False
    failure = quality["failures"][0]
    assert failure["kind"] == FAILURE_KIND and "apex" in failure["instruction"]
    assert FAILURE_KIND in LAYOUT_FAILURE_KINDS
    apply_connector_direction_quality(quality, "<svg", STAGE_SOURCE)  # unparsable: no new failure
    assert len(quality["failures"]) == 1


def test_the_check_runs_only_with_the_connector_policy(monkeypatch):
    from services.generation_core.authoring import author_slide_svg

    monkeypatch.delenv("CONTENT_STRUCTURE_POLICY_SCOPE", raising=False)
    monkeypatch.delenv("CONTENT_STRUCTURE_POLICY_PARTS", raising=False)
    assert author_slide_svg.deck_structure_policy({})[0] is None
    monkeypatch.setenv("CONTENT_STRUCTURE_POLICY_SCOPE", "svg")
    monkeypatch.setenv("CONTENT_STRUCTURE_POLICY_PARTS", "connectors")
    policy, redesign = author_slide_svg.deck_structure_policy({})
    assert policy.connectors and not policy.tables and redesign is False
    monkeypatch.setenv("CONTENT_STRUCTURE_POLICY_PARTS", "tables")
    assert not author_slide_svg.deck_structure_policy({})[0].connectors


# --- generated body ids never repeat an id the drawing copied ---------------------------

def test_generated_body_ids_skip_ids_the_drawing_already_uses(tmp_path):
    from services.svg_templates.frame_composition import compose_frame_svg

    frame = _frame(tmp_path, {400, 700})
    region = frame.body_region
    # Node 1 has no id (it would become body-node-1); node 2 copied "body-node-1" from a previous page.
    body = (f'<svg xmlns="{NS}" width="1280" height="720" viewBox="0 0 1280 720">'
            f'<rect x="{region.x + 10}" y="{region.y + 10}" width="100" height="40" fill="#EEEEEE"/>'
            f'<rect id="body-node-1" x="{region.x + 10}" y="{region.y + 60}" width="100" height="40" fill="#DDDDDD"/>'
            f'<rect x="{region.x + 10}" y="{region.y + 110}" width="100" height="40" fill="#CCCCCC"/>'
            f'<text id="body-t" x="{region.x + 10}" y="{region.y + 200}" font-family="Pretendard" font-size="20"'
            ' fill="#111111">본문</text></svg>')
    composed = compose_frame_svg(frame, body, dynamic_values={})
    group = next(node for node in ET.fromstring(composed) if node.get("id") == "svg-system-template-body")
    ids = [node.get("id") for node in group]
    assert ids == ["body-node-1-1", "body-node-1", "body-node-3", "body-t"]
