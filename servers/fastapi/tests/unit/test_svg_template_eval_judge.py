"""The pure parts of scripts/svg_template_eval/judge.py: reference choice, prompt, parsing, aggregation."""
import importlib.util
import json
import sys
from io import BytesIO
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[2] / "scripts" / "svg_template_eval"
sys.path.insert(0, str(_DIR))
_spec = importlib.util.spec_from_file_location("svg_template_eval_judge", _DIR / "judge.py")
judge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(judge)

AXES = judge.AXES


def _answer(**scores):
    return {axis: {"score": scores.get(axis, 3), "reason": f"{axis} reason"} for axis in AXES}


def test_reference_choice_is_one_rule_for_every_page():
    assert judge.select_references(["b.png", "a.png"]) == {"mode": "individual", "pages": ["a.png", "b.png"]}
    many = [f"ref-{n:03d}.png" for n in (9, 4, 19, 25)]
    assert judge.select_references(many) == {"mode": "sheet", "pages": sorted(many)}
    assert judge.select_references(many, ["ref-019.png", "ref-004.png"]) == {
        "mode": "individual", "pages": ["ref-019.png", "ref-004.png"]}
    with pytest.raises(ValueError):
        judge.select_references(many, ["nope.png"])
    with pytest.raises(ValueError):
        judge.select_references([])


def test_contact_sheet_grid_and_image():
    assert judge.sheet_grid(2) == (2, 1)
    assert judge.sheet_grid(8) == (3, 3)
    assert judge.sheet_grid(12) == (4, 3)
    from PIL import Image

    def png(color):
        out = BytesIO()
        Image.new("RGB", (128, 91), color).save(out, format="PNG")
        return out.getvalue()

    sheet = Image.open(BytesIO(judge.contact_sheet([png("red"), png("blue"), png("green"), png("black")], 400)))
    assert sheet.width == 400 and sheet.height > 91


def test_request_puts_cached_references_first_and_the_generated_page_last():
    refs = [("ref-004.png", b"one"), ("ref-009.png", b"two")]
    request = judge.build_request(refs, b"generated", sheet=False)
    content = request["messages"][0]["content"]
    images = [block for block in content if block["type"] == "image"]
    assert len(images) == 3 and images[-1]["source"]["data"] == "Z2VuZXJhdGVk"
    assert "ref-009.png" in content[2]["text"]
    cached = [index for index, block in enumerate(content) if "cache_control" in block]
    assert cached == [3]  # the last reference image: the prefix shared by every page
    assert content[-1]["type"] == "text" and all(axis in content[-1]["text"] for axis in AXES)
    assert all(axis in request["system"] for axis in AXES)
    sheet = judge.build_request([("8", b"sheet")], b"generated", sheet=True)["messages"][0]["content"]
    assert "contact sheet of 8 pages" in sheet[0]["text"] and sum(b["type"] == "image" for b in sheet) == 2
    # the same references give the same prefix for every page
    other = judge.build_request(refs, b"other page", sheet=False)["messages"][0]["content"]
    assert other[:4] == content[:4]


def test_schema_requires_every_axis():
    schema = judge.RESPONSE_SCHEMA
    assert schema["required"] == list(AXES) and schema["additionalProperties"] is False
    assert schema["properties"]["tone"]["properties"]["score"]["enum"] == [1, 2, 3, 4, 5]


def test_parse_strict_fenced_and_embedded_json():
    strict = judge.parse_judgement(json.dumps(_answer(structure=5, tone=2)))
    assert strict["valid"] and strict["scores"]["structure"] == 5 and strict["scores"]["tone"] == 2
    assert strict["reasons"]["density"] == "density reason"
    fenced = judge.parse_judgement("Here you go:\n```json\n" + json.dumps(_answer(visuals=4)) + "\n```\nDone.")
    assert fenced["valid"] and fenced["scores"]["visuals"] == 4
    prose = judge.parse_judgement("My scores {\"note\": \"a } in a string\"} then " + json.dumps(_answer(density=1)))
    assert prose["valid"] and prose["scores"]["density"] == 1


def test_parse_tolerates_other_shapes_and_malformed_json():
    flat = judge.parse_judgement('{"scores": {"Structure": 4, "density": "3/5", "hierarchy": 5, "tone": 2,'
                                 ' "visuals": 1}, "reasons": {"tone": "blue is off"},}')
    assert flat["valid"] and flat["scores"]["density"] == 3 and flat["reasons"]["tone"] == "blue is off"
    lines = judge.parse_judgement("structure: 4\ndensity - 2\nhierarchy = 5\ntone: 3\nvisuals: 4")
    assert lines["valid"] and lines["scores"]["density"] == 2
    truncated = judge.parse_judgement('{"structure": {"score": 4, "reason": "cards"}, "density": {"sco')
    assert truncated["scores"]["structure"] == 4 and not truncated["valid"]
    assert "density" in truncated["missing"]
    out_of_range = judge.parse_judgement(json.dumps(_answer(tone=7, visuals=True)))
    assert out_of_range["scores"]["tone"] is None and out_of_range["scores"]["visuals"] is None
    assert not out_of_range["valid"]
    nothing = judge.parse_judgement("I cannot judge this page.")
    assert not nothing["valid"] and nothing["missing"] == list(AXES)
    assert not judge.parse_judgement("")["valid"]


def test_aggregate_skips_missing_axes_and_overall_needs_every_axis():
    pages = [{"scores": {axis: 4.0 for axis in AXES}, "valid": True},
             {"scores": {**{axis: 2.0 for axis in AXES}, "tone": None}, "valid": False},
             {"scores": {axis: None for axis in AXES}, "valid": False, "error": "boom"}]
    deck = judge.aggregate(pages)
    assert deck["axes"]["structure"] == 3.0 and deck["axes"]["tone"] == 4.0
    assert deck["overall"] == 3.2 and deck["pages"] == 3 and deck["pages_valid"] == 1
    assert deck["per_axis_n"]["tone"] == 1 and deck["per_axis_n"]["structure"] == 2
    assert judge.aggregate([pages[2]])["overall"] is None
    assert judge.page_overall(pages[0]["scores"]) == 4.0 and judge.page_overall(pages[1]["scores"]) is None


def test_spread_run_fields_and_cost():
    a = {"axes": {axis: 3.0 for axis in AXES}, "overall": 3.0}
    b = {"axes": {**{axis: 3.4 for axis in AXES}, "tone": 2.5}, "overall": 3.22}
    result = judge.spread([a, b])
    assert result["structure"] == 0.4 and result["tone"] == 0.5 and result["overall"] == 0.22
    assert judge.spread([a])["tone"] is None
    fields = judge.run_fields({"deck": {"axes": {**a["axes"], "visuals": None}, "overall": None}})
    assert fields == {f"judge_{axis}": 3.0 for axis in AXES if axis != "visuals"}
    usage = judge.add_usage({}, {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": None})
    usage = judge.add_usage(usage, {"input_tokens": 1000, "cache_creation_input_tokens": 2000})
    assert usage == {"input_tokens": 2000, "output_tokens": 100, "cache_read_input_tokens": 0,
                     "cache_creation_input_tokens": 2000}
    model = judge.DEFAULT_MODEL
    fresh, output, _, write = judge.PRICES[model]
    assert judge.cost_estimate(model, usage) == round((2000 * fresh + 100 * output + 2000 * write) / 1e6, 4)
    assert judge.cost_estimate("unknown-model", usage) is None


def test_svg_size_from_attributes_or_viewbox():
    assert judge.svg_size('<svg xmlns="x" width="1280" height="912" viewBox="0 0 1280 912">') == (1280, 912)
    assert judge.svg_size('<svg viewBox="0 0 960 540">') == (960, 540)
