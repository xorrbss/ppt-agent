"""Pure decision tests for the svg ``text_overlap`` basis (glyph ink, else line box)."""

from __future__ import annotations

import json
import subprocess

import pytest

from services.svg_text_overlap import comparison_fragments, text_overlaps


def _rect(x, y, width, height):
    return {"x": x, "y": y, "width": width, "height": height}


# Two stacked lines: the line boxes intersect by 15 px, the ink is 9 px apart.
LINE_A, INK_A = _rect(80, 144, 410, 70), _rect(81, 159, 406, 46)
LINE_B, INK_B = _rect(80, 199, 291, 70), _rect(81, 214, 286, 46)


def _item(qid, line, ink="absent", parent=None):
    item = {"qid": qid, "parent_qid": parent, "text_fragments": line}
    if ink != "absent":
        item["text_ink_fragments"] = ink
    return item


def test_ink_boxes_decide_when_present():
    overlaps, basis = text_overlaps([_item("a", [LINE_A], [INK_A]), _item("b", [LINE_B], [INK_B])])
    assert overlaps == []
    assert basis["version"] == "glyph-ink/v3"
    assert basis["fragments"] == {"ink": 2, "ink_cell": 0, "line_box": 0}
    assert basis["pairs"] == []


def test_missing_ink_keeps_the_line_box_verdict():
    overlaps, basis = text_overlaps([_item("a", [LINE_A]), _item("b", [LINE_B])])
    assert overlaps == [["a", "b"]]
    assert basis["fragments"] == {"ink": 0, "ink_cell": 0, "line_box": 2}
    assert basis["pairs"] == [{"pair": ["a", "b"], "basis": "line_box"}]


def test_cell_fallback_keeps_fragment_ink_geometry_and_reports_its_basis():
    with_cell = {**INK_A, "cellClusters": 1}
    overlaps, basis = text_overlaps([_item("a", [LINE_A], [with_cell]),
                                     _item("b", [LINE_B], [INK_B])])
    assert overlaps == []
    assert comparison_fragments(_item("a", [LINE_A], [with_cell])) == [(INK_A, "ink_cell")]
    assert basis["fragments"] == {"ink": 1, "ink_cell": 1, "line_box": 0}
    colliding = {**_rect(81, 190, 286, 46), "cellClusters": 1}
    overlaps, basis = text_overlaps([_item("a", [LINE_A], [with_cell]),
                                     _item("b", [LINE_B], [colliding])])
    assert overlaps == [["a", "b"]]
    assert basis["pairs"] == [{"pair": ["a", "b"], "basis": "ink_cell"}]


def test_null_ink_entry_falls_back_per_fragment():
    overlaps, basis = text_overlaps([_item("a", [LINE_A], [None]), _item("b", [LINE_B], [None])])
    assert overlaps == [["a", "b"]]
    assert basis["pairs"][0]["basis"] == "line_box"


def test_mixed_basis_compares_ink_against_line_box():
    # A's line box reaches y=214; B's ink starts at 214, so they only touch.
    overlaps, _ = text_overlaps([_item("a", [LINE_A], [None]), _item("b", [LINE_B], [INK_B])])
    assert overlaps == []
    deeper = _rect(81, 200, 286, 46)
    overlaps, basis = text_overlaps([_item("a", [LINE_A], [None]), _item("b", [LINE_B], [deeper])])
    assert overlaps == [["a", "b"]]
    assert basis["pairs"] == [{"pair": ["a", "b"], "basis": "mixed"}]


def test_true_ink_collision_is_still_reported_on_ink():
    colliding = _rect(81, 190, 286, 46)
    overlaps, basis = text_overlaps([_item("a", [LINE_A], [INK_A]), _item("b", [LINE_B], [colliding])])
    assert overlaps == [["a", "b"]]
    assert basis["pairs"] == [{"pair": ["a", "b"], "basis": "ink"}]


def test_ink_array_not_aligned_with_fragments_is_ignored():
    item = _item("a", [LINE_A], [INK_A, INK_A])
    assert comparison_fragments(item) == [(LINE_A, "line_box")]


def test_malformed_ink_entries_fall_back():
    for bad in ({"x": 1}, {"x": 1, "y": 2, "width": 0, "height": 3},
                {"x": float("nan"), "y": 0, "width": 1, "height": 1}, "box", 3):
        assert comparison_fragments(_item("a", [LINE_A], [bad])) == [(LINE_A, "line_box")]


def test_mixed_fragments_within_one_element():
    second_line, second_ink = _rect(80, 300, 100, 70), _rect(81, 315, 98, 46)
    item = _item("a", [LINE_A, second_line], [None, second_ink])
    assert comparison_fragments(item) == [(LINE_A, "line_box"), (second_ink, "ink")]


def test_direct_parent_child_pair_is_never_compared():
    parent = _item("t", [LINE_A])
    child = _item("s", [LINE_A], parent="t")
    overlaps, basis = text_overlaps([parent, child])
    assert overlaps == [] and basis["pairs"] == []


def test_sibling_tspans_are_compared():
    first = _item("s1", [LINE_A], [INK_A], parent="t")
    second = _item("s2", [LINE_A], [INK_A], parent="t")
    assert text_overlaps([first, second])[0] == [["s1", "s2"]]


def test_threshold_is_half_a_pixel_on_both_axes():
    a = _item("a", [_rect(0, 0, 10, 10)], [_rect(0, 0, 10, 10)])
    touching = _item("b", [_rect(0, 9.5, 10, 10)], [_rect(0, 9.5, 10, 10)])
    assert text_overlaps([a, touching])[0] == []
    deeper = _item("b", [_rect(0, 9.4, 10, 10)], [_rect(0, 9.4, 10, 10)])
    assert text_overlaps([a, deeper])[0] == [["a", "b"]]


@pytest.mark.parametrize("case,complete", [
    ("ancestor_hangul", True), ("explicit_ko", True),
    ("english_owner_hangul_body", False), ("mismatched_localized_advance", False),
    ("explicit_en", False), ("explicit_und", False), ("explicit_empty", False),
    ("pre_leading_spaces", True), ("pre_trailing_lf", True), ("pre_tab", True),
])
def test_source_text_context_requires_actual_font_advance_and_preserves_pair_witness(case, complete):
    from utils.slide_capture_text_ink import TEXT_INK_JS

    harness = r'''
const scenario = SCENARIO;
const leafContent = scenario==='pre_leading_spaces' ? '    L\u00b7' :
 scenario==='pre_trailing_lf' ? 'L\u00b7\n\n' : scenario==='pre_tab' ? '\tL\u00b7' : 'L\u00b7';
const starts = scenario==='pre_leading_spaces' ? [10,14,18,22,26,31] :
 scenario==='pre_trailing_lf' ? [10,15,23,27] : scenario==='pre_tab' ? [10,18,23] : [10,15];
const ends = scenario==='pre_leading_spaces' ? [14,18,22,26,31,39] :
 scenario==='pre_trailing_lf' ? [15,23,27,31] : scenario==='pre_tab' ? [18,23,31] : [15,23];
const style = {filter:'none',textShadow:'none',textDecorationLine:'none',writingMode:'horizontal-tb',
 direction:'ltr',textTransform:'none',fontVariant:'normal',fontFeatureSettings:'normal',
 fontVariationSettings:'normal',fontStretch:'normal',fontSizeAdjust:'none',fontWeight:'400',
 fontStyle:'normal',fontSize:'14px',fontFamily:'Noto Sans KR',fontKerning:'auto',
 letterSpacing:'normal',wordSpacing:'normal',stroke:'none',whiteSpace:scenario.startsWith('pre_')?'pre':'normal'};
const getComputedStyle = node => style;
class SVGTextContentElement {
 constructor(text,parent=null,attrs={}) { this.textContent=text; this.parentElement=parent;
  this.attrs=attrs; this.childElementCount=0; }
 getAttribute(key) { return this.attrs[key] ?? null; }
 getAttributeNS(ns,key) { return this.attrs['xml:'+key] ?? null; }
 hasAttribute(key) { return key in this.attrs; }
 getNumberOfChars() { return this.textContent.length; }
 getScreenCTM() { return {a:1,b:0,c:0,d:1}; }
 getStartPositionOfChar(i) { return {x:starts[i],y:20}; }
 getEndPositionOfChar(i) { return {x:ends[i],y:20}; }
}
class SVGTextElement extends SVGTextContentElement {}
class SVGTextPathElement extends SVGTextContentElement {}
class DOMPoint { constructor(x,y) {this.x=x;this.y=y;} matrixTransform(ctm) {return this;} }
const context = {lang:'und',font:'initial',fontKerning:'auto',letterSpacing:'0px',wordSpacing:'0px',
 measureText(value) {
  let width = value===' ' ? 4 : value==='L' ? 8 : value==='L\u00b7' ? (this.lang==='ko'?13:8) :
   this.lang==='ko' ? (scenario==='mismatched_localized_advance'?11:8) : 3;
  return {width,fontBoundingBoxAscent:16,fontBoundingBoxDescent:4,
   actualBoundingBoxLeft:0,actualBoundingBoxRight:width,actualBoundingBoxAscent:10,actualBoundingBoxDescent:2};
 }};
const document = {body:{classList:{contains:()=>true}},
 createElement:()=>({getContext:()=>context}),
 fonts:[{status:'loaded',family:'Noto Sans KR',style:'normal',weight:'100 900'}]};
const fontEvidence = () => ({loaded:true,source:'loaded-font-face',family:'Noto Sans KR'});
const px = value => parseFloat(value);
const body = {textContent:'\ud55c\uae00',parentElement:null,getAttribute:()=>null,getAttributeNS:()=>null};
const attrs = scenario==='explicit_empty' ? {lang:''} :
 scenario.startsWith('explicit_') ? {'xml:lang':scenario.slice(9)} : {};
const owner = new SVGTextElement(scenario==='english_owner_hangul_body' ? 'English L\u00b7' : '\ud55c\uae00 L\u00b7',body,attrs);
const leaf = new SVGTextContentElement(leafContent,owner);
const text = {textContent:leafContent,parentElement:leaf};
'''.replace("SCENARIO", json.dumps(case))
    script = harness + TEXT_INK_JS + r'''
const result = inkBox(leaf,text,{x:10,y:4,width:13,height:20});
const language = inkLanguageContext(leaf);
const font = context.font;
document.fonts=[];
const untrusted = inkBox(leaf,text,{x:10,y:4,width:13,height:20});
console.log(JSON.stringify({result,language,finalLanguage:context.lang,font,untrusted}));
'''
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                               encoding="utf-8", check=True)
    evidence = json.loads(completed.stdout)
    result = evidence["result"]
    assert result is not None
    assert result["glyphPaintCoverage"] == {"expected": 2, "isolated": int(complete),
        "pair": 1, "cell": int(not complete), "complete": complete}
    assert result["cellClusters"] == int(not complete)
    assert result["pairInkWitnesses"][0]["basis"] == "matching-pair-advance"
    first_cluster_index = 4 if case == "pre_leading_spaces" else 1 if case == "pre_tab" else 0
    assert result["pairInkWitnesses"][0]["index"] == first_cluster_index
    assert result["pairInkWitnesses"][0]["length"] == 1
    # The contextual pair proves bounded ink, not an isolated L glyph.
    assert result["glyphInkCoverage"]["complete"] is False
    assert all(glyph["index"] != first_cluster_index for glyph in result["glyphInkClusters"])
    assert evidence["untrusted"] is None
    if complete:
        assert result["glyphInkClusters"][0]["language"] == "ko"
    expected_language = "ko" if case == "explicit_ko" else "en" if case == "explicit_en" else "und"
    assert evidence["finalLanguage"] == expected_language
    assert evidence["language"]["explicit"] is case.startswith("explicit_")
    assert "Noto Sans KR" in evidence["font"]


@pytest.mark.parametrize("whitespace,source,count,expected", [
    ("pre", "    AB", 6, "    AB"), ("pre", "AB\n\n", 4, "AB  "),
    ("pre", "\tA\nB\r", 5, " A B "), ("pre", "    AB", 5, None),
    ("normal", "    AB\n\n", 4, " AB "), ("normal", "    AB\n\n", 2, "AB"),
    ("nowrap", "A\t \nB", 3, "A B"), ("normal", "    AB\n\n", 3, None),
    ("pre-wrap", "    AB", 6, None), ("break-spaces", "    AB", 6, None),
])
def test_svg_ink_content_respects_preserved_addresses_and_default_collapse(whitespace, source, count, expected):
    from utils.slide_capture_text_ink import TEXT_INK_JS

    values = json.dumps(dict(whitespace=whitespace, source=source, count=count), ensure_ascii=True)
    script = ("const values=" + values + "; const document={createElement:()=>({getContext:()=>null})};"
              "const getComputedStyle=()=>({whiteSpace:values.whitespace});" + TEXT_INK_JS +
              "const result=inkContent({getNumberOfChars:()=>values.count},{textContent:values.source});"
              "console.log(JSON.stringify(result));")
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                               encoding="utf-8", check=True)
    assert json.loads(completed.stdout) == expected
