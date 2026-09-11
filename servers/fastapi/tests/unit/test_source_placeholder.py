"""Construction boundary, Unicode cover and existing ownership contracts."""

from types import SimpleNamespace

import pytest

from services.source_content.placeholder.block_table import build_block_table
from services.source_content.placeholder.static_checks import check_coverage
from services.source_content.placeholder.substitute import expand_placeholders
from services.source_content.placeholder.tokens import parse_text_tokens
from services.source_content.source_stamp_dom import BrowserSourceReadbackError, deterministic_source_qid
from services.source_content.text_stamping import stamp_source_blocks
from utils.authored_html_tree.parser_adapter import parse_authored_html
from utils.authored_html_tree.serializer import serialize_authored_html

CONTEXT = SimpleNamespace(normalization_profile="fragment")


def expand(html, bindings):
    return expand_placeholders(html, build_block_table(bindings), context=CONTEXT)


@pytest.mark.parametrize("text, expected", [
    ("{{q:source-slide:1:text:2}}", ("source-slide:1:text:2", None, None)),
    ("{{q:a@0-2}}", ("a", 0, 2)),
    ("x {{q:A_b.c/1-2@12-99}} y", ("A_b.c/1-2", 12, 99)),
])
def test_grammar(text, expected):
    token, = parse_text_tokens(text)
    assert (token.block_id, token.start, token.end) == expected


@pytest.mark.parametrize("text", [
    "{{q:}}", "{{q:a@01-2}}", "{{q:a@-1-2}}", "{{q:a@1-}}", "{{q:a@1.0-2}}",
    "{{q:a {{q:b}}}}", "{{{q:a}}}", "{{q:a@0-10000000}}", "{{q:a", "{{q:a b}}",
])
def test_malformed_grammar(text):
    with pytest.raises(BrowserSourceReadbackError, match="source_placeholder_context"):
        parse_text_tokens(text)


@pytest.mark.parametrize("html", [
    '<p title="{{q:a}}">x</p>', '<style>p { content:"{{q:a}}" }</style>',
    '<!-- {{q:a}} --><p>x</p>', '<script>{{q:a}}</script>', '<template>{{q:a}}</template>',
    '<p hidden>{{q:a}}</p>', '<section style="display:none"><p>{{q:a}}</p></section>',
    '<p style="visibility: hidden !important">{{q:a}}</p>',
    '<p style="opacity:0">{{q:a}}</p>', '<p aria-hidden="true">{{q:a}}</p>',
    '<p title="&#123;&#123;q:a&#125;&#125;">x</p>', '<p>{{q:<em>a}}</em></p>',
])
def test_forbidden_contexts(html):
    with pytest.raises(BrowserSourceReadbackError, match="source_placeholder_context"):
        expand(html, {"a": "안녕"})


def test_hidden_child_tail_is_visible():
    result = expand('<p><!-- furniture -->{{q:a}}</p>', {"a": "안녕"})
    assert "안녕" in result.html


@pytest.mark.parametrize("html, code", [
    ("<p>changed text</p>", "missing"),
    ("<p>{{q:a}}{{q:a}}</p>", "duplicate"),
    ("<p>{{q:a}}{{q:a@0-1}}</p>", "duplicate"),
    ("<p>{{q:a@0-2}}{{q:a@0-2}}</p>", "duplicate"),
    ("<p>{{q:invented}}</p>", "unknown"),
    ("<p>{{q:a@0-1}}{{q:a@2-3}}</p>", "cover"),
    ("<p>{{q:a@0-2}}{{q:a@1-3}}</p>", "cover"),
    ("<p>{{q:a@0-4}}</p>", "cover"),
    ("<p>{{q:a@0-0}}{{q:a@0-3}}</p>", "cover"),
])
def test_coded_rejections(html, code):
    with pytest.raises(BrowserSourceReadbackError, match="source_placeholder_" + code):
        expand(html, {"a": "가나다"})


def test_graphemes_korean_jamo_combining_and_zwj():
    text = "가가e\u0301👩\u200d💻끝"
    table = build_block_table({"a": text})
    assert table[0].boundaries == (0, 1, 3, 5, 8, 9)
    for boundary in (2, 4, 6, 7):
        with pytest.raises(BrowserSourceReadbackError, match="source_placeholder_cover"):
            check_coverage(parse_text_tokens(f"{{{{q:a@0-{boundary}}}}}{{{{q:a@{boundary}-9}}}}"), table)
    result = expand('<p>{{q:a@0-3}}<strong>{{q:a@3-8}}</strong>{{q:a@8-9}}</p>', {"a": text})
    root = parse_authored_html(result.html, profile="fragment").root
    assert "".join(root.itertext()) == text
    assert root[0].get("data-source-block-id") == "a"
    assert root[0][0].tag == "strong"


def test_nonrecursive_data_insertion_and_escaping():
    source = '<img src=x onerror="bad"> & {{q:secret}} {{q:a}}'
    result = expand("<p>{{q:a}}</p>", {"a": source})
    root = parse_authored_html(result.html, profile="fragment").root
    assert root[0].text == source
    assert not list(root.iter("img"))
    assert "&lt;img" in result.html and "&amp;" in result.html
    assert result.receipt["tokens"] == 1


@pytest.mark.parametrize("html, text", [
    ('<h1 data-qid="model">{{q:a}}</h1>', "제목"),
    ('<p>{{q:a@0-2}}<strong>{{q:a@2-4}}</strong></p>', "가나다라"),
    ('<p data-role="body">{{q:a}}</p>', "① 질문 | 답변"),
])
def test_stamping_parity(html, text):
    result = expand(html, {"a": text})
    root = parse_authored_html(result.html, profile="fragment").root
    owner = next(node for node in root.iter() if node.get("data-source-block-id"))
    assert owner.get("data-qid") == deterministic_source_qid("a")
    del owner.attrib["data-source-block-id"]
    legacy = stamp_source_blocks(serialize_authored_html(SimpleNamespace(root=root, profile="fragment")), [("a", text)])
    assert serialize_authored_html(parse_authored_html(legacy.html, profile="fragment")) == result.html


def test_two_equal_source_occurrences_and_furniture_are_separate():
    result = expand('<p>{{q:a}} / {{q:b}}</p><footer>01</footer>', {"a": "같음", "b": "같음"})
    root = parse_authored_html(result.html, profile="fragment").root
    assert {node.get("data-source-block-id") for node in root.iter() if node.get("data-source-block-id")} == {"a", "b"}
    assert root[-1].attrib == {}


def test_run_identity_and_dom_deviation():
    result = expand('<div><p>{{q:a@3-5}}</p><i>furniture</i><p>{{q:a@0-3}}</p></div>', {"a": "가나\n다라"})
    root = parse_authored_html(result.html, profile="fragment").root
    ids = [node.get("data-source-block-id") for node in root.iter() if node.get("data-source-block-id")]
    assert ids == ["a:run:1", "a:run:0"]
    assert result.deviations == ({"kind": "within_block_dom_order", "blockId": "a"},)
    assert check_coverage(parse_text_tokens("{{q:b}}{{q:a}}"), build_block_table({"a": "a", "b": "b"}))


def test_canonical_text_keeps_content_markers_drops_item_glyphs():
    # Regression for a defect where every run marker -- including a corporate
    # mark ("㈜") and enumerators ("①"/"②") -- was peeled off the canonical
    # token text, so token expansion silently dropped content even though
    # `accepts()` would have admitted the full (unpeeled) form. Only pure
    # item-glyph markers (bullets/checkboxes) are template furniture.
    table = build_block_table({"a": "㈜아이비즈소프트웨어"})
    block = table[0]
    assert block.text == "㈜아이비즈소프트웨어"
    assert block.projection.accepts(*block.projection.whole, block.text)

    table = build_block_table({"a": "① 질문\n② 답변"})
    block = table[0]
    assert "①" in block.text and "②" in block.text
    assert block.projection.accepts(*block.projection.whole, block.text)

    table = build_block_table({"a": "• 첫째 항목\n• 둘째 항목"})
    block = table[0]
    assert "•" not in block.text
    assert block.projection.accepts(*block.projection.whole, block.text)

    result = expand("<p>{{q:a}}</p>", {"a": "㈜아이비즈소프트웨어"})
    root = parse_authored_html(result.html, profile="fragment").root
    assert root[0].text == "㈜아이비즈소프트웨어"


def test_bounded_error_receipt():
    table = build_block_table({f"a{i}": "text" for i in range(100)})
    with pytest.raises(BrowserSourceReadbackError) as caught:
        check_coverage([], table)
    assert len(caught.value.block_ids) == 32
    assert len(caught.value.detail) <= 240
