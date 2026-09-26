"""Extraction and digest binding of model-declared native tables/charts."""
from __future__ import annotations

import asyncio
import contextlib
import json
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg import native_declarations as module
from services.generation_core.drivers.svg import terminal
from services.generation_core.drivers.svg.native_declarations import (
    PAYLOAD_KEY, bind_native_declarations, bound_declarations, extract_native_declarations,
    native_author_rules, native_binding_digest,
)
from services.svg_contract import finalize_svg, validate_svg
from utils.llm_calls import author_slide_svg as author_module

HEAD = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
TEXT = 'font-family="Inter" font-size="18" fill="#111111"'
TABLE = ('<g id="native-table-a" data-native-table=\'{"rows":[["항목","값"],["매출","1,200"]],"headerRows":1}\'>'
         '<rect x="100" y="100" width="400" height="80" fill="#FFFFFF" stroke="#333333"/>'
         '<line x1="300" y1="100" x2="300" y2="180" stroke="#333333"/>'
         '<line x1="100" y1="140" x2="500" y2="140" stroke="#333333"/>'
         f'<text x="110" y="126" {TEXT}>항목</text><text x="310" y="126" {TEXT}>값</text>'
         f'<text x="110" y="166" {TEXT}>매출</text><text x="310" y="166" {TEXT}>1,200</text></g>')


def _extract(body: str):
    root = ET.fromstring(HEAD + f'<text x="40" y="60" {TEXT}>제목</text>' + body + "</svg>")
    found = extract_native_declarations(root)
    return found, ET.tostring(root, encoding="unicode")


def test_table_declaration_is_extracted_and_the_attribute_removed():
    found, svg = _extract(TABLE)
    assert found == {"objects": [{"id": "native-table-a", "kind": "table", "raw": {
        "rows": [["항목", "값"], ["매출", "1,200"]], "headerRows": 1}}], "dropped": []}
    assert "data-native" not in svg
    validate_svg(svg)


def test_malformed_declarations_are_dropped_with_reasons():
    bodies = [
        ('<g id="t1" data-native-table=\'[1,2]\'><rect x="0" y="0" width="1" height="1"/></g>',
         "native_declaration_json_invalid"),
        ('<rect id="t2" x="0" y="0" width="1" height="1" data-native-table=\'{"rows":[["a"]]}\'/>',
         "native_declaration_not_group"),
        ('<g data-native-table=\'{"rows":[["a"]]}\'><rect x="0" y="0" width="1" height="1"/></g>',
         "native_declaration_id_missing"),
        ('<g id="t3" data-native-table=\'{}\' data-native-chart=\'{}\'><rect x="0" y="0" width="1" height="1"/></g>',
         "native_declaration_kind_ambiguous"),
    ]
    for body, reason in bodies:
        found, svg = _extract(body)
        assert found["objects"] == [] and found["dropped"][0]["reason"] == reason, body
        assert "data-native" not in svg


def test_a_declaration_nested_in_another_is_dropped():
    nested = TABLE.replace("</g>", '<g id="inner" data-native-table=\'{"rows":[["x"]]}\'></g></g>', 1)
    found, _ = _extract(nested)
    assert [item["id"] for item in found["objects"]] == ["native-table-a"]
    assert found["dropped"] == [{"id": "inner", "kind": "table", "reason": "native_declaration_nested"}]


def test_nothing_declared_returns_none():
    assert _extract(f'<text x="1" y="30" {TEXT}>a</text>')[0] is None


def test_chart_rule_is_offered_only_behind_the_flag(monkeypatch):
    assert "data-native-table" in native_author_rules()
    assert "data-native-chart" in native_author_rules()
    monkeypatch.setattr(module, "NATIVE_CHARTS_OFFERED", False)
    assert "data-native-chart" not in native_author_rules()
    found, svg = _extract('<g id="c1" data-native-chart=\'{"chartType":"bar"}\'>'
                          '<rect x="0" y="0" width="1" height="1"/></g>')
    assert found["dropped"][0]["reason"] == "native_chart_not_offered" and "data-native" not in svg


def test_binding_digest_survives_finalize_but_not_another_drawing():
    _, svg = _extract(TABLE)
    final = finalize_svg(svg, index=1, n=4)
    assert final != svg and native_binding_digest(final) == native_binding_digest(svg)
    _, other = _extract(TABLE.replace(">매출<", ">비용<"))
    assert native_binding_digest(other) != native_binding_digest(svg)
    assert native_binding_digest("<svg") is None


def test_every_path_that_changes_the_svg_loses_the_binding():
    found, first = _extract(TABLE)
    payload: dict = {}
    bind_native_declarations(payload, first, found)
    last_valid = finalize_svg(first, index=0, n=2)
    payload["svg_content"] = last_valid
    assert bound_declarations(payload, payload["svg_content"])[1] is True
    # A correction attempt with a different drawing rebinds the record ...
    corrected_found, corrected = _extract(TABLE.replace(">매출<", ">순매출<").replace('"매출"', '"순매출"'))
    bind_native_declarations(payload, corrected, corrected_found)
    # ... so restoring ``last_valid_svg`` afterwards cannot reuse it.
    payload["svg_content"] = last_valid
    assert bound_declarations(payload, last_valid) == (payload[PAYLOAD_KEY], False)
    # A correction attempt that declares nothing clears the record.
    bind_native_declarations(payload, corrected, None)
    assert PAYLOAD_KEY not in payload
    # The terminal fallback page is built without the record.
    assert PAYLOAD_KEY not in terminal._IDENTITY_KEYS


def _stub_provider(monkeypatch, response_svg: str, seen: dict):
    async def no_wait(*_a, **_k):
        return None

    def kwargs(**values):
        seen["messages"] = values["messages"]
        return {}

    for name, value in {
        "get_client": lambda **_: object(), "get_llm_config": lambda: None, "get_model": lambda: "m",
        "authoring_model": lambda model: model, "get_llm_provider": lambda: SimpleNamespace(value="p"),
        "_authoring_output_profile": lambda **_: (1000, None), "_authoring_extra_body": lambda *_a, **_k: None,
        "get_generate_kwargs": kwargs, "_wait_for_author_rate_slot": no_wait,
        "enforce_llm_egress_policy": lambda: None, "supports_provider_idempotency": lambda *_a, **_k: False,
        "provider_call_admission": lambda **_k: contextlib.nullcontext(),
        "provider_generate_call": lambda *_a, **_k: (lambda: SimpleNamespace(content=response_svg)),
        "record_provider_call": no_wait, "extract_text": lambda content: content,
    }.items():
        monkeypatch.setattr(author_module, name, value)
    monkeypatch.setattr("services.document_canvas_generation.generation_font_floor", lambda *_a: 18)


def test_author_slide_svg_strips_declarations_and_reports_them(monkeypatch):
    seen: dict = {}
    response = HEAD + f'<text x="40" y="60" {TEXT}>제목</text>' + TABLE + "</svg>"
    _stub_provider(monkeypatch, response, seen)
    style = {"contract_version": author_module.SVG_CONTRACT_VERSION, "digest": "d", "references": {}}
    result = asyncio.run(author_module.author_slide_svg("# 제목", style, "content", 1, 3))
    assert result.receipt["validation_error"] is None
    assert "data-native" not in result.svg_content
    validate_svg(result.svg_content)
    assert result.native_declarations["objects"][0]["id"] == "native-table-a"
    assert result.receipt["native_declarations"] == {
        "declared": 1, "extracted": [{"id": "native-table-a", "kind": "table"}], "dropped": []}
    prompt = json.dumps([str(message) for message in seen["messages"]], ensure_ascii=False)
    assert "data-native-table" in prompt and "data-native-chart" in prompt


def test_author_slide_svg_without_declarations_keeps_its_receipt_shape(monkeypatch):
    response = HEAD + f'<text x="40" y="60" {TEXT}>제목</text></svg>'
    _stub_provider(monkeypatch, response, {})
    style = {"contract_version": author_module.SVG_CONTRACT_VERSION, "digest": "d", "references": {}}
    result = asyncio.run(author_module.author_slide_svg("# 제목", style, "content", 1, 3))
    assert result.native_declarations is None and "native_declarations" not in result.receipt
