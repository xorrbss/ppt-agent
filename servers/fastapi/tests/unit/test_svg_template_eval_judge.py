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


EVIDENCE = {"component_kinds": ["table", "card grid"], "sections": 2, "key_message_bar": True, "text_items": 34,
            "empty_share": 15, "template_components": ["numbered sub-headings"], "defects": []}


def _answer(**scores):
    return {"evidence": EVIDENCE,
            **{axis: {"reason": f"{axis} reason", "score": scores.get(axis, 3)} for axis in AXES}}


def _png(color="white", size=(128, 91)):
    from PIL import Image

    out = BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


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


def test_request_puts_context_and_anchors_first_and_the_generated_page_last():
    context = [("8", b"sheet")]
    high = [("ref-009.png, measured blank share 30%", b"high one"), ("ref-066.png", b"high two")]
    low = [("low.png", b"low")]
    request = judge.build_request(b"generated", high, low, context, sheet=True, generated_blank=0.512)
    content = request["messages"][0]["content"]
    texts = [block["text"] for block in content if block["type"] == "text"]
    images = [block["source"]["data"] for block in content if block["type"] == "image"]
    assert len(images) == 5 and images[-1] == "Z2VuZXJhdGVk"  # sheet, two high, one low, the generated page
    assert "contact sheet of 8 pages" in texts[0]
    assert texts[1].startswith("SCORE 5 ANCHOR 1 (ref-009.png, measured blank share 30%)")
    assert texts[2].startswith("SCORE 5 ANCHOR 2 (ref-066.png)") and texts[3].startswith("SCORE 1 ANCHOR 1 (low.png)")
    assert texts[4] == "Generated page to score (measured blank share 51%):"
    cached = [index for index, block in enumerate(content) if "cache_control" in block]
    assert cached == [7] and content[7]["type"] == "image"  # the last anchor: the prefix shared by every page
    assert all(axis in texts[-1] for axis in AXES) and "evidence" in texts[-1].lower()
    assert all(axis in request["system"] for axis in AXES) and "SCORE 5 ANCHOR" in request["system"]
    # the same anchors give the same prefix for every page
    other = judge.build_request(b"other", high, low, context, sheet=True, generated_blank=0.9)
    assert other["messages"][0]["content"][:8] == content[:8]
    assert other["system"] == request["system"]


def test_request_without_context_or_measure_and_needs_both_anchor_kinds():
    content = judge.build_request(b"generated", [("a.png", b"a")], [("low.png", b"b")])["messages"][0]["content"]
    assert [block["type"] for block in content] == ["text", "image", "text", "image", "text", "image", "text"]
    assert content[0]["text"].startswith("SCORE 5 ANCHOR 1") and content[4]["text"] == "Generated page to score:"
    individual = judge.build_request(b"g", [("a.png", b"a")], [("l.png", b"l")], [("r1.png", b"1"), ("r2.png", b"2")])
    assert individual["messages"][0]["content"][2]["text"] == "Context page 2 of the template deck (r2.png):"
    for high, low in (([], [("l.png", b"l")]), ([("a.png", b"a")], [])):
        with pytest.raises(ValueError):
            judge.build_request(b"g", high, low)


def test_anchor_resolves_a_path_or_a_name_in_the_references_dir(tmp_path):
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "ref-009.png").write_bytes(b"x")
    other = tmp_path / "low.png"
    other.write_bytes(b"y")
    assert judge.resolve_anchor("ref-009.png", refs) == refs / "ref-009.png"
    assert judge.resolve_anchor(str(other), refs) == other
    with pytest.raises(ValueError):
        judge.resolve_anchor("missing.png", refs)


def test_schema_asks_for_evidence_before_every_axis_and_reasons_before_scores():
    schema = judge.RESPONSE_SCHEMA
    assert schema["required"] == ["evidence", *AXES] and schema["additionalProperties"] is False
    assert list(schema["properties"]) == ["evidence", *AXES]
    assert list(schema["properties"]["tone"]["properties"]) == ["reason", "score"]
    assert schema["properties"]["tone"]["properties"]["score"]["enum"] == [1, 2, 3, 4, 5]
    evidence = schema["properties"]["evidence"]
    assert set(evidence["required"]) == set(EVIDENCE) and evidence["additionalProperties"] is False


def test_parse_strict_fenced_and_embedded_json():
    strict = judge.parse_judgement(json.dumps(_answer(structure=5, tone=2)))
    assert strict["valid"] and strict["scores"]["structure"] == 5 and strict["scores"]["tone"] == 2
    assert strict["reasons"]["density"] == "density reason" and strict["evidence"] == EVIDENCE
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


def test_parse_evidence_shapes_and_truncated_answers():
    loose = {**_answer(), "evidence": {"component_kinds": "table, chart", "sections": "3", "key_message_bar": "yes",
                                       "text_items": 41.6, "empty_share": "10%", "defects": "raw markup"}}
    parsed = judge.parse_judgement(json.dumps(loose))
    assert parsed["valid"] and parsed["evidence"] == {
        "component_kinds": ["table", "chart"], "sections": 3, "key_message_bar": None, "text_items": 42,
        "empty_share": 10, "template_components": [], "defects": ["raw markup"]}
    no_evidence = judge.parse_judgement(json.dumps({axis: {"score": 2, "reason": "r"} for axis in AXES}))
    assert no_evidence["valid"] and no_evidence["evidence"] is None  # the evidence never decides validity
    cut = json.dumps(_answer(structure=2, density=4))
    cut = cut[:cut.index('"hierarchy"') + 30]  # cut off inside the hierarchy reason
    truncated = judge.parse_judgement(cut)
    assert truncated["evidence"] == EVIDENCE and not truncated["valid"]
    assert truncated["scores"]["structure"] == 2 and truncated["scores"]["density"] == 4
    assert truncated["missing"] == ["hierarchy", "tone", "visuals"]
    reason_first = judge.parse_judgement('{"tone": {"reason": "a \\"quoted\\" 9 word", "score": 4}, "visuals": {"re')
    assert reason_first["scores"]["tone"] == 4 and reason_first["scores"]["visuals"] is None


def test_overall_is_the_documented_weighted_mean():
    assert abs(sum(judge.WEIGHTS.values()) - 1) < 1e-9 and set(judge.WEIGHTS) == set(AXES)
    scores = {"structure": 1.0, "density": 2.0, "hierarchy": 3.0, "tone": 4.0, "visuals": 5.0}
    expected = round(sum(judge.WEIGHTS[axis] * scores[axis] for axis in AXES), 3)
    assert judge.combine(scores) == expected == judge.page_overall(scores)
    heavy = {"structure": 0.5, "density": 0.5, "hierarchy": 0.0, "tone": 0.0, "visuals": 0.0}
    assert judge.combine(scores, heavy) == 1.5
    assert judge.combine({**scores, "tone": None}) is None and judge.combine({}) is None


def test_aggregate_skips_missing_axes_and_overall_needs_every_axis():
    pages = [{"scores": {axis: 4.0 for axis in AXES}, "valid": True},
             {"scores": {**{axis: 2.0 for axis in AXES}, "tone": None}, "valid": False},
             {"scores": {axis: None for axis in AXES}, "valid": False, "error": "boom"}]
    deck = judge.aggregate(pages)
    assert deck["axes"]["structure"] == 3.0 and deck["axes"]["tone"] == 4.0
    assert deck["overall"] == judge.combine(deck["axes"]) and deck["pages"] == 3 and deck["pages_valid"] == 1
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
    assert fields == {**{f"judge_{axis}": 3.0 for axis in AXES if axis != "visuals"},
                      "judge_version": judge.JUDGE_VERSION}
    assert judge.run_fields({"deck": {"axes": {axis: None for axis in AXES}, "overall": None}}) == {}
    assert judge.run_fields({"judge_version": 7, "deck": {"axes": a["axes"], "overall": 3.0}})["judge_version"] == 7
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


def test_blank_share_counts_flat_tiles_of_the_body_only():
    from PIL import Image, ImageDraw

    assert judge.blank_share(_png("white", (240, 240)), 0.0, 1.0, tile=24) == 1.0
    image = Image.new("RGB", (240, 240), "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, 240, 4):  # stripes in the left half: not flat
        draw.line((x, 0, x, 240), fill="black")
    draw.rectangle((120, 0, 240, 240), fill=(230, 240, 250))  # a flat card fill counts as blank
    out = BytesIO()
    image.save(out, format="PNG")
    assert judge.blank_share(out.getvalue(), 0.0, 1.0, tile=24) == 0.5
    image = Image.new("RGB", (240, 240), "white")
    ImageDraw.Draw(image).rectangle((0, 0, 239, 47), fill="navy")
    for y in range(0, 48, 4):  # a busy header band above the body is left out
        ImageDraw.Draw(image).line((0, y, 240, y), fill="yellow")
    out = BytesIO()
    image.save(out, format="PNG")
    assert judge.blank_share(out.getvalue(), 0.2, 1.0, tile=24) == 1.0
    assert judge.blank_share(out.getvalue(), 0.0, 1.0, tile=24) == 0.8


def test_low_anchor_keeps_the_frame_and_writes_bullets_into_a_cleared_body():
    from PIL import Image

    frame = Image.new("RGB", (640, 456), (0, 60, 150))
    out = BytesIO()
    frame.save(out, format="PNG")
    anchor = Image.open(BytesIO(judge.low_anchor(out.getvalue(), None, ["first line", "second line"]))).convert("RGB")
    assert anchor.size == (640, 456)
    assert anchor.getpixel((5, 5)) == (0, 60, 150) and anchor.getpixel((5, 450)) == (0, 60, 150)  # header, footer
    assert anchor.getpixel((600, 300)) == (255, 255, 255)  # cleared body
    body = anchor.crop((0, round(456 * 0.19), 640, round(456 * 0.94)))
    assert min(body.convert("L").tobytes()) < 100  # bullet text drawn
