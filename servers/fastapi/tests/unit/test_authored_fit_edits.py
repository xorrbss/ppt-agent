"""Replaying the in-page fitter's style appends onto the source HTML.

The whole point of this module is byte-level agreement with what the fitter did
in the page, so these tests pin the append rule itself rather than "some style
ends up on the element".
"""

from __future__ import annotations

import pathlib

import pytest

from utils.authored_fit_edits import FitEditError, apply_fit_edits, fitted_source
from utils.authored_qid_namespace import ensure_authored_qids

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "authored_fit"


def test_append_creates_the_attribute_when_the_element_has_no_style():
    html = '<div data-qid="card">x</div>'
    assert apply_fit_edits(html, [{"qid": "card", "declarations": "height:471px"}]) == (
        '<div data-qid="card" style="height:471px">x</div>'
    )


def test_append_joins_an_existing_declaration_with_one_semicolon():
    html = '<div data-qid="card" style="color:red">x</div>'
    assert apply_fit_edits(html, [{"qid": "card", "declarations": "height:471px"}]) == (
        '<div data-qid="card" style="color:red;height:471px">x</div>'
    )


def test_append_keeps_a_trailing_semicolon_and_adds_its_own():
    # ``setAttribute('style', original + ';' + declarations)`` does not normalise
    # the original, so a source that already ends in ``;`` yields ``;;``.  The
    # reconstruction must produce exactly the same bytes, not a tidier string.
    html = '<div data-qid="card" style="color:red;">x</div>'
    assert apply_fit_edits(html, [{"qid": "card", "declarations": "height:471px"}]) == (
        '<div data-qid="card" style="color:red;;height:471px">x</div>'
    )


def test_append_treats_an_empty_style_attribute_as_no_original():
    html = '<div data-qid="card" style="">x</div>'
    assert apply_fit_edits(html, [{"qid": "card", "declarations": "height:471px"}]) == (
        '<div data-qid="card" style="height:471px">x</div>'
    )


def test_single_quoted_and_valueless_style_attributes_are_handled():
    single = '<div data-qid="card" style=\'color:red\'>x</div>'
    # A quoted style keeps its quoting: the append is byte-preserving.
    assert apply_fit_edits(single, [{"qid": "card", "declarations": "top:1px"}]) == (
        '<div data-qid="card" style=\'color:red;top:1px\'>x</div>'
    )
    unquoted = '<div data-qid="card" style=color:red>x</div>'
    assert apply_fit_edits(unquoted, [{"qid": "card", "declarations": 'font-family:"A B"'}]) == (
        '<div data-qid="card" style="color:red;font-family:&quot;A B&quot;">x</div>'
    )
    valueless = '<div data-qid="card" style>x</div>'
    assert apply_fit_edits(valueless, [{"qid": "card", "declarations": "top:1px"}]) == (
        '<div data-qid="card" style="top:1px">x</div>'
    )


def test_the_rest_of_the_document_is_left_byte_identical():
    html = (
        "<!doctype html><html><body>\n"
        '  <section class="a"  id=b >\n'
        '    <div data-qid="card" style="color:red">Ω &amp; x</div>\n'
        "  </section>\n"
        "</body></html>"
    )
    fitted = apply_fit_edits(html, [{"qid": "card", "declarations": "height:1px"}])
    assert fitted == html.replace(
        'style="color:red"', 'style="color:red;height:1px"'
    )


def test_application_is_idempotent():
    html = '<div data-qid="card" style="color:red">x</div>'
    edits = [{"qid": "card", "declarations": "height:471px"}]
    once = apply_fit_edits(html, edits)
    assert apply_fit_edits(once, edits) == once
    # And on an element whose whole style is the declarations.
    bare = apply_fit_edits('<div data-qid="c">x</div>', [{"qid": "c", "declarations": "a:b"}])
    assert apply_fit_edits(bare, [{"qid": "c", "declarations": "a:b"}]) == bare


def test_edits_are_applied_in_report_order():
    html = '<div data-qid="a">x</div><div data-qid="b" style="color:red">y</div>'
    fitted = apply_fit_edits(
        html,
        [
            {"qid": "a", "declarations": "height:10px"},
            {"qid": "a", "declarations": "width:20px"},
            {"qid": "b", "declarations": "font-size:15px;line-height:22.5px"},
        ],
    )
    assert fitted == (
        '<div data-qid="a" style="height:10px;width:20px">x</div>'
        '<div data-qid="b" style="color:red;font-size:15px;line-height:22.5px">y</div>'
    )


def test_a_missing_qid_is_refused():
    with pytest.raises(FitEditError):
        apply_fit_edits('<div data-qid="a">x</div>', [{"qid": "b", "declarations": "a:b"}])


def test_a_duplicated_qid_is_refused():
    html = '<div data-qid="a">x</div><span data-qid="a">y</span>'
    with pytest.raises(FitEditError):
        apply_fit_edits(html, [{"qid": "a", "declarations": "a:b"}])


def test_a_malformed_edit_is_refused():
    html = '<div data-qid="a">x</div>'
    for edit in ({"declarations": "a:b"}, {"qid": "a"}, {"qid": "a", "declarations": " "}, "nope"):
        with pytest.raises(FitEditError):
            apply_fit_edits(html, [edit])


def test_a_quote_in_the_declarations_stays_inside_the_attribute():
    html = '<div data-qid="a">x</div>'
    fitted = apply_fit_edits(html, [{"qid": "a", "declarations": 'font-family:"Noto"'}])
    assert fitted == '<div data-qid="a" style="font-family:&quot;Noto&quot;">x</div>'


@pytest.mark.parametrize("slide", ("run42-s12", "run42-s20"))
def test_a_real_authored_slide_is_edited_at_the_addressed_element_only(slide):
    # The two slides bench run 42 published flagged ``text_escapes_card``.  They
    # are the shapes the fitter actually meets: many qids, nested cards, inline
    # styles on some elements and none on others.
    source = FIXTURES / f"{slide}.html"
    if not source.exists():
        pytest.skip("run-42 fit fixtures are not checked out")
    html = source.read_text(encoding="utf-8")
    card = f"s{slide.split('-s')[1]}-map"
    assert f'data-qid="{card}"' in html
    fitted = apply_fit_edits(html, [{"qid": card, "declarations": "height:471px"}])
    assert fitted != html
    assert len(fitted) == len(html) + len(' style="height:471px"')
    # Nothing else moved, and the document stays qid-complete.
    assert ensure_authored_qids(fitted) == fitted
    assert fitted.replace(' style="height:471px"', "", 1) == html
    assert apply_fit_edits(fitted, [{"qid": card, "declarations": "height:471px"}]) == fitted


def test_fitted_source_returns_the_input_when_the_report_has_no_edits():
    html = '<div data-qid="a">x</div>'
    assert fitted_source(html, {"edits": []}) is html
    assert fitted_source(html, {}) is html
    assert fitted_source(html, {"edits": None}) is html
    assert fitted_source(html, {"edits": [{"qid": "a", "declarations": "a:b"}]}) == (
        '<div data-qid="a" style="a:b">x</div>'
    )
