"""Unit tests for utils.authored_html_normalize (A2, generation programme
master design section 4).

Covers the normaliser in isolation: the whitelist it strips (forbidden
break/page-break CSS declarations, a `hidden` attribute with or without
bound source text), the byte-identical no-op guarantee when nothing in a
document matches, and that anything outside the whitelist is left alone."""

from utils.authored_html_normalize import normalize_authored_html


def test_normalize_authored_html_is_a_no_op_when_nothing_matches():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<p data-qid="a" style="color:#111; font-size: 12px">Hello</p>'
        "<style>.x { color: red; }</style>"
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert result.html == html
    assert result.normalizations == []


def test_normalize_authored_html_strips_forbidden_style_attribute_declarations():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<div style="break-inside: avoid; color: red; orphans: 3">t</div>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert "break-inside" not in result.html
    assert "orphans" not in result.html
    assert "color: red" in result.html
    assert result.normalizations == [
        {
            "rule": "css_break_control_property_stripped",
            "detail": "break-inside, orphans",
            "count": 2,
        }
    ]


def test_normalize_authored_html_strips_forbidden_style_block_declarations():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>.card { page-break-before: always; widows: 2; color: blue; }</style>"
        "</head><body><div class='card'>t</div></body></html>"
    )

    result = normalize_authored_html(html)

    assert "page-break-before" not in result.html
    assert "widows" not in result.html
    assert "color: blue" in result.html
    assert ".card {" in result.html


def test_normalize_authored_html_does_not_touch_a_property_value_that_merely_contains_the_word():
    """`content` isn't in the whitelist; a value that happens to contain one of
    the forbidden property names as text must not be treated as a declaration."""
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<div style="color: red">note: orphans are not a real CSS token here</div>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert result.html == html
    assert result.normalizations == []


def test_normalize_authored_html_removes_hidden_element_without_bound_text():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<div hidden class="ghost">decor</div><p data-qid="a">kept</p>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert "ghost" not in result.html
    assert "decor" not in result.html
    assert 'data-qid="a"' in result.html
    assert "kept" in result.html
    assert result.normalizations == [
        {
            "rule": "hidden_element_without_bound_text_removed",
            "detail": "div",
            "count": 1,
        }
    ]


def test_normalize_authored_html_strips_hidden_attribute_when_element_has_bound_text():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<p hidden data-source-block-id="source-slide:1:text:1">원문</p>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert "hidden" not in result.html
    assert "원문" in result.html
    assert 'data-source-block-id="source-slide:1:text:1"' in result.html
    assert result.normalizations == [
        {"rule": "hidden_attribute_stripped_bound_text", "detail": "p", "count": 1}
    ]


def test_normalize_authored_html_strips_hidden_attribute_when_descendant_has_bound_text():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<div hidden><span data-qid="child">t</span></div>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert result.html.count("hidden") == 0
    assert '<span data-qid="child">t</span>' in result.html
    assert result.normalizations == [
        {"rule": "hidden_attribute_stripped_bound_text", "detail": "div", "count": 1}
    ]


def test_normalize_authored_html_removes_nested_hidden_elements_only_once():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<div hidden class="outer"><span hidden class="inner">x</span></div>'
        '<p data-qid="k">kept</p>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert "outer" not in result.html
    assert "inner" not in result.html
    assert "kept" in result.html
    assert result.normalizations == [
        {
            "rule": "hidden_element_without_bound_text_removed",
            "detail": "div",
            "count": 1,
        }
    ]


def test_normalize_authored_html_removes_void_hidden_element_without_bound_text():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<p data-qid="a">kept</p><hr hidden>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert "<hr" not in result.html
    assert "kept" in result.html


def test_normalize_authored_html_strips_hidden_from_void_element_with_bound_text():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<img hidden data-qid="pic" src="data:image/png;base64,AAAA">'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert "<img" in result.html
    assert "hidden" not in result.html
    assert 'data-qid="pic"' in result.html


def test_normalize_authored_html_leaves_non_whitelisted_findings_untouched():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<div style="content-visibility: hidden; color: red">t</div>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert result.html == html
    assert result.normalizations == []


def test_normalize_authored_html_never_matches_aria_hidden():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<span aria-hidden="true" data-qid="deco">*</span>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert result.html == html
    assert result.normalizations == []


def test_normalize_authored_html_handles_empty_and_non_string_input():
    assert normalize_authored_html("").normalizations == []
    assert normalize_authored_html("").html == ""
