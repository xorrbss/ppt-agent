"""Unit tests for utils.authored_html_normalize (A2, generation programme
master design section 4).

Covers the normaliser in isolation: the whitelist it strips (forbidden
break/page-break CSS declarations, a `hidden` attribute with or without
bound source text), the byte-identical no-op guarantee when nothing in a
document matches, and that anything outside the whitelist is left alone."""

import pytest

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


def test_normalize_authored_html_drops_only_void_element_end_tags():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        "<main><img data-qid='hero'></IMG><br></br><hr></hr></main>"
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert "</img>" not in result.html.lower()
    assert "</br>" not in result.html.lower()
    assert "</hr>" not in result.html.lower()
    assert result.normalizations == [
        {"rule": "html_void_end_tag_dropped", "detail": "br", "count": 1},
        {"rule": "html_void_end_tag_dropped", "detail": "hr", "count": 1},
        {"rule": "html_void_end_tag_dropped", "detail": "img", "count": 1},
    ]


def test_non_void_unbalanced_end_tag_still_fails_renderer_admission():
    from utils.renderer_isolation import RendererPolicyError, admit_authored_html

    html = "<html><head></head><body><main>kept</span></main></body></html>"
    result = normalize_authored_html(html)

    assert result.html == html
    assert result.normalizations == []
    with pytest.raises(RendererPolicyError, match="unbalanced: span"):
        admit_authored_html(result.html)


def test_author_intake_records_void_end_tag_normalization(monkeypatch):
    from services.generation_core.authoring.author_slide import validate_authored_slide_html

    monkeypatch.delenv("AUTHORED_HTML_NORMALIZE_ENABLED", raising=False)
    html = "<html><head></head><body><img data-qid='hero'></img></body></html>"
    receipt = []

    admitted = validate_authored_slide_html(html, normalization_receipt=receipt)

    assert "</img>" not in admitted.lower()
    assert receipt == [
        {"rule": "html_void_end_tag_dropped", "detail": "img", "count": 1}
    ]


def test_author_intake_stays_byte_identical_in_legacy_mode_when_nothing_fires(monkeypatch):
    """Regression: adoption must not depend on the receipt count. `legacy` only
    ever rewrites bytes when a whitelisted rule fires, so a document with no
    findings comes back unchanged either way -- this pins that invariant."""
    from services.generation_core.authoring.author_slide import validate_authored_slide_html

    monkeypatch.delenv("AUTHORED_HTML_NORMALIZE_ENABLED", raising=False)
    monkeypatch.setenv("AUTHORED_HTML_NORMALIZATION", "legacy")
    html = "<html><head></head><body><p data-qid='x'>hello</p></body></html>"
    receipt = []

    admitted = validate_authored_slide_html(html, normalization_receipt=receipt)

    assert admitted == html
    assert receipt == []


def test_author_intake_adopts_tree_canonicalization_even_when_no_rule_fires(monkeypatch):
    """Regression for the defect where `validate_authored_slide_html` gated
    adoption of the normalized HTML on `normalized.normalizations` being
    non-empty. In `tree` mode the whole point is a canonical HTML5
    reserialization, which the html5lib round trip changes on virtually every
    real document (here: single-quoted attributes canonicalize to double
    quotes) even when zero rewrite rules fire. Gating on receipt count meant
    the reserialized tree never reached the renderer in tree mode."""
    from services.generation_core.authoring.author_slide import validate_authored_slide_html

    monkeypatch.delenv("AUTHORED_HTML_NORMALIZE_ENABLED", raising=False)
    monkeypatch.setenv("AUTHORED_HTML_NORMALIZATION", "tree")
    html = "<html><head></head><body><div class='a'><p data-qid=\"x\">hello</p></div></body></html>"
    receipt = []

    admitted = validate_authored_slide_html(html, normalization_receipt=receipt)

    # No whitelisted rewrite rule fired -- the receipt stays empty --
    # but the canonical (double-quoted) reserialization must still be adopted.
    assert receipt == []
    assert "class=\"a\"" in admitted
    assert "class='a'" not in admitted


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


# --- title-role <br> (QUAL-12, utils/authored_html_normalize_rules_title.py) ---


def test_normalize_authored_html_replaces_br_inside_header_contract_title():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<div data-header-part="title" data-qid="header.title">'
        "디지털 전환<br>추진 전략<br/>2026</div>"
        '<p data-qid="body">본문<br>줄바꿈은 유지</p>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert "디지털 전환 추진 전략 2026</div>" in result.html
    assert "본문<br>줄바꿈은 유지" in result.html
    assert result.normalizations == [
        {"rule": "title_role_line_break_replaced", "detail": "div", "count": 2}
    ]


def test_normalize_authored_html_recognises_every_title_marker():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        "<h1>커버<br>제목</h1>"
        '<span data-semantic-role="title">시맨틱<br />제목</span>'
        '<div data-qid="cover.title.text">큐아이디<br>제목</div>'
        '<div data-qid="slide.title">슬라이드<br>제목</div>'
        '<div data-qid="card.title">카드<br>제목</div>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert "<h1>커버 제목</h1>" in result.html
    assert "시맨틱 제목</span>" in result.html
    assert "큐아이디 제목</div>" in result.html
    assert "슬라이드 제목</div>" in result.html
    # `card.title` is not in the primary title vocabulary: untouched.
    assert "카드<br>제목" in result.html
    assert result.normalizations == [
        {
            "rule": "title_role_line_break_replaced",
            "detail": "div, h1, span",
            "count": 4,
        }
    ]


def test_normalize_authored_html_title_br_swallows_surrounding_whitespace_once():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<h2 data-qid="header.title">\n  첫 줄 <br>\n  둘째 줄\n</h2>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert '<h2 data-qid="header.title">\n  첫 줄 둘째 줄\n</h2>' in result.html


def test_normalize_authored_html_title_br_handles_nested_title_once():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<div data-header-part="title"><h1 data-qid="header.title">A<br>B</h1></div>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert '<h1 data-qid="header.title">A B</h1>' in result.html
    assert result.normalizations == [
        {"rule": "title_role_line_break_replaced", "detail": "div", "count": 1}
    ]


def test_normalize_authored_html_title_br_is_a_no_op_outside_titles():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<p data-qid="body.text">첫 줄<br>둘째 줄</p><h1>제목</h1>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert result.html == html
    assert result.normalizations == []


def test_normalize_authored_html_title_br_leaves_unbalanced_documents_alone():
    html = "<html><body><h1>A<br>B</div></body></html>"

    result = normalize_authored_html(html)

    assert result.html == html
    assert result.normalizations == []


def test_normalize_authored_html_strips_name_attribute_from_a_tag():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<a name="section-2" data-qid="a">앵커</a>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert 'name="section-2"' not in result.html
    assert "<a data-qid=\"a\">앵커</a>" in result.html
    assert result.normalizations == [
        {"rule": "html_attribute_stripped", "detail": "name", "count": 1}
    ]


def test_normalize_authored_html_strips_name_attribute_from_multiple_tags():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<div name="one">a</div><div name="two">b</div>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert "name=" not in result.html
    assert "<div>a</div><div>b</div>" in result.html
    assert result.normalizations == [
        {"rule": "html_attribute_stripped", "detail": "name", "count": 2}
    ]


def test_normalize_authored_html_does_not_strip_data_name_attribute():
    """`data-name` must never be mistaken for `name`."""
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<div data-name="one">t</div>'
        "</body></html>"
    )

    result = normalize_authored_html(html)

    assert result.html == html
    assert result.normalizations == []


def test_admits_after_stripping_a_document_that_previously_failed_admission_on_name():
    from utils.renderer_isolation import RendererPolicyError, admit_authored_html

    hostile = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;width:1280px;height:720px;background:#fff}</style>"
        '</head><body><div name="section-2">앵커</div></body></html>'
    )

    with pytest.raises(RendererPolicyError, match="name"):
        admit_authored_html(hostile)

    result = normalize_authored_html(hostile)
    assert result.normalizations == [
        {"rule": "html_attribute_stripped", "detail": "name", "count": 1}
    ]
    admitted = admit_authored_html(result.html)
    assert admitted.source_digest.startswith("sha256:")
