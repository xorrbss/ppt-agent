"""Unit tests for utils.authored_css_strip (H1, authored-quality-speed
programme design section 6).

Covers: one property class stripped in an inline ``style=""`` attribute and
in a ``<style>`` block, ``@media print`` removed whole, a plain ``@media
screen``/feature query hoisted, ``@keyframes`` removed whole, non-rendering
``content`` values stripped while rendering values and the other measurement-
exempt families stay untouched, a clean document coming back byte-identical,
and that a document which previously failed
``admit_authored_html`` on a stripped property is admitted afterwards.
"""

import pytest

from utils.authored_css_strip import strip_disallowed_css
from utils.renderer_isolation import RendererPolicyError, admit_authored_html


SAFE_HTML = (
    '<!doctype html><html><head><meta charset="utf-8"><style>\n'
    "html,body{margin:0;width:1280px;height:720px;background:#fff}"
    ".card{position:absolute;left:20px;top:20px;width:240px;height:80px;"
    "background:#2563eb;color:white;padding:12px}\n"
    "</style></head><body><main class=\"card\"><h1>Safe authored</h1></main>"
    "</body></html>"
)


def test_clean_document_is_byte_identical():
    html, records = strip_disallowed_css(SAFE_HTML)

    assert html == SAFE_HTML
    assert records == []


def test_strips_disallowed_property_in_inline_style_attribute():
    html = '<div style="color: red; will-change: transform">t</div>'

    result, records = strip_disallowed_css(html)

    assert "will-change" not in result
    assert "color: red" in result
    assert records == [{"rule": "css_property_stripped", "property": "will-change", "count": 1}]


def test_strips_disallowed_property_in_style_block():
    html = "<style>.x { scroll-behavior: smooth; color: blue; }</style>"

    result, records = strip_disallowed_css(html)

    assert "scroll-behavior" not in result
    assert "color: blue" in result
    assert ".x {" in result
    assert records == [
        {"rule": "css_property_stripped", "property": "scroll-behavior", "count": 1}
    ]


def test_strips_multiple_occurrences_of_the_same_disallowed_property():
    html = (
        "<style>.a { appearance: none; } .b { appearance: none; color: red; }</style>"
    )

    result, records = strip_disallowed_css(html)

    assert "appearance" not in result
    assert "color: red" in result
    assert records == [{"rule": "css_property_stripped", "property": "appearance", "count": 2}]


def test_media_print_block_is_removed_whole():
    html = (
        "<style>.card{color:red}"
        "@media print{.card{display:none}.other{color:blue}}"
        "</style>"
    )

    result, records = strip_disallowed_css(html)

    assert "@media" not in result
    assert "display:none" not in result
    assert ".card{color:red}" in result
    assert records == [{"rule": "css_at_rule_removed", "at_rule": "media", "count": 1}]


def test_media_screen_query_is_hoisted():
    html = "<style>@media screen{.card{color:red}}</style>"

    result, records = strip_disallowed_css(html)

    assert "@media" not in result
    assert ".card{color:red}" in result
    assert records == [{"rule": "css_at_rule_hoisted", "at_rule": "media", "count": 1}]


def test_media_min_width_feature_query_is_hoisted():
    html = "<style>@media (min-width: 768px){.card{color:red}}</style>"

    result, records = strip_disallowed_css(html)

    assert "@media" not in result
    assert ".card{color:red}" in result
    assert records == [{"rule": "css_at_rule_hoisted", "at_rule": "media", "count": 1}]


def test_keyframes_block_is_removed_whole():
    html = (
        "<style>.card{color:red}"
        "@keyframes spin{from{transform:rotate(0)}to{transform:rotate(1turn)}}"
        "</style>"
    )

    result, records = strip_disallowed_css(html)

    assert "@keyframes" not in result
    assert "spin" not in result
    assert ".card{color:red}" in result
    assert records == [{"rule": "css_at_rule_removed", "at_rule": "keyframes", "count": 1}]


def test_content_and_custom_property_are_not_stripped():
    html = (
        '<div style="content: \'x\'; --brand-color: #111; color: red">t</div>'
    )

    result, records = strip_disallowed_css(html)

    assert result == html
    assert records == []


@pytest.mark.parametrize(
    "value",
    ["''", '\"\"', "none", "NoRmAl", '\"\" !IMPORTANT'],
)
def test_strips_only_non_rendering_content_values_in_blocks_and_attributes(value):
    attribute = (
        f'<div style="content: {value}; color: red">t</div>'
        if "'" in value
        else f"<div style='content: {value}; color: red'>t</div>"
    )
    html = f"<style>.x {{ content: {value}; color: blue; }}</style>{attribute}"

    result, records = strip_disallowed_css(html)

    assert "content:" not in result
    assert "color: blue" in result
    assert "color: red" in result
    assert records == [
        {"rule": "css_property_stripped", "property": "content", "count": 2}
    ]


@pytest.mark.parametrize("value", ['\"x\"', "attr(data-x)", '\"\" \"y\"', '\"'])
def test_content_values_that_can_render_or_are_malformed_stay_byte_identical(value):
    html = f"<style>.x {{ content: {value}; color: blue; }}</style>"

    result, records = strip_disallowed_css(html)

    assert result == html
    assert records == []


def test_empty_content_document_admits_after_stripping():
    hostile = SAFE_HTML.replace(
        ".card{position:absolute",
        '.card::before{content:"";display:block}.card{position:absolute',
    )

    with pytest.raises(RendererPolicyError, match="content"):
        admit_authored_html(hostile)

    stripped, records = strip_disallowed_css(hostile)

    assert records == [
        {"rule": "css_property_stripped", "property": "content", "count": 1}
    ]
    assert admit_authored_html(stripped).source_digest.startswith("sha256:")


def test_default_author_normalization_path_strips_empty_content(monkeypatch):
    from utils.llm_calls.author_slide import validate_authored_slide_html

    monkeypatch.delenv("AUTHORED_HTML_NORMALIZE_ENABLED", raising=False)
    hostile = SAFE_HTML.replace(
        ".card{position:absolute",
        '.card::before{content:"";display:block}.card{position:absolute',
    )
    receipt = []

    admitted = validate_authored_slide_html(hostile, normalization_receipt=receipt)

    assert "content:" not in admitted
    assert receipt == [
        {"rule": "css_property_stripped", "property": "content", "count": 1}
    ]


def test_admits_after_stripping_a_document_that_previously_failed_admission():
    hostile = SAFE_HTML.replace(
        "background:#2563eb;color:white;padding:12px}",
        "background:#2563eb;color:white;padding:12px;will-change:transform}",
    )

    try:
        admit_authored_html(hostile)
    except RendererPolicyError as exc:
        assert "will-change" in str(exc)
    else:  # pragma: no cover - defensive, the fixture must reproduce the failure
        raise AssertionError("expected the un-stripped fixture to fail admission")

    stripped, records = strip_disallowed_css(hostile)

    assert records == [{"rule": "css_property_stripped", "property": "will-change", "count": 1}]
    admitted = admit_authored_html(stripped)
    assert admitted.source_digest.startswith("sha256:")
