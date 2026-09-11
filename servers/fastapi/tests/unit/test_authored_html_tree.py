from __future__ import annotations

import hashlib

import pytest

from utils.authored_html_normalize import normalize_authored_html
from utils.authored_html_tree import RawAuthoredHtmlViolation, normalize_authored_html_tree
from utils.authored_html_tree.parser_adapter import HTML5LIB_VERSION, parse_authored_html
from utils.authored_html_tree.receipts import MAX_RECEIPT_DETAIL_CHARS, ReceiptCollector
from utils.authored_html_tree.serializer import serialize_authored_html
from utils.get_env_programme import get_authored_html_normalization
from utils.renderer_isolation import admit_authored_html
from utils.runtime_settings import RuntimeSettings


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_parser_adapter_pins_document_fragment_and_namespace_behavior() -> None:
    assert HTML5LIB_VERSION == "1.1"
    document = parse_authored_html("<svg><path></path></svg>", profile="document")
    fragment = parse_authored_html("<svg><path></path></svg>", profile="fragment")

    assert document.root.tag == "html"
    assert fragment.root.tag == "DOCUMENT_FRAGMENT"
    svg = next(element for element in fragment.root.iter() if str(element.tag).endswith("}svg"))
    assert str(svg.tag).startswith("{http://www.w3.org/2000/svg}")


def test_serializer_is_explicit_and_deterministic() -> None:
    parsed = parse_authored_html('<html><body><p z="2" a="1">x</p></body></html>', profile="document")

    assert serialize_authored_html(parsed) == (
        '<!DOCTYPE html><html><head></head><body><p a="1" z="2">x</p></body></html>'
    )


def test_tree_rewrite_enumerates_today_semantic_rules_and_admits() -> None:
    source = """<!doctype html><html><head><style>
    .number::before { content: "1"; break-inside: avoid; color: red }
    @media screen and (min-width: 10px) {.card {will-change:auto;color:blue}}
    </style></head><body>
    <h1 name="heading">Title <br> next</h1>
    <div hidden><span name="discarded">gone</span></div>
    <div hidden data-qid="text.body" style="content: attr(data-x); color: black">kept</div>
    </body></html>"""

    normalized, receipts = normalize_authored_html_tree(source, profile="document")

    assert "content:" not in normalized
    assert "break-inside:" not in normalized
    assert "will-change:" not in normalized
    assert " hidden" not in normalized
    assert " name=" not in normalized
    assert "<br>" not in normalized
    assert "gone" not in normalized
    assert "Title next" in normalized
    rules = {entry["rule"] for entry in receipts}
    assert rules == {
        "css_break_control_property_stripped",
        "hidden_element_without_bound_text_removed",
        "hidden_attribute_stripped_bound_text",
        "title_role_line_break_replaced",
        "html_attribute_stripped",
        "css_property_stripped",
        "css_at_rule_hoisted",
    }
    assert all(set(entry) == {"rule", "count", "detail"} for entry in receipts)
    admit_authored_html(normalized)


def test_tree_parser_admits_nested_void_end_tags_with_receipts() -> None:
    normalized, receipts = normalize_authored_html_tree(
        "<html><body><div><img><br></img></div></body></html>",
        profile="document",
    )

    assert normalized.count("<img>") == 1
    assert normalized.count("<br>") == 1
    assert {entry["rule"] for entry in receipts} == {"html_void_end_tag_dropped"}
    admit_authored_html(normalized)


def test_tree_parser_uses_browser_semantics_for_br_end_tag() -> None:
    normalized, receipts = normalize_authored_html_tree(
        "<html><body>one<br></br>two</body></html>",
        profile="document",
    )

    assert normalized.count("<br>") == 2
    assert receipts == [{"rule": "html_void_end_tag_reparsed", "count": 1, "detail": "br"}]


def test_tree_normalization_rejects_raw_findings_the_tree_would_erase() -> None:
    for source in ('<p id="a" id="b">x</p>', "<div></span></div>"):
        with pytest.raises(RawAuthoredHtmlViolation):
            normalize_authored_html_tree(source, profile="document")


def test_tree_normalization_does_not_rewrite_comments_as_markup() -> None:
    source = '<!-- <p name="x" style="content: \'bad\'"> --><p>safe</p>'

    normalized, receipts = normalize_authored_html_tree(source, profile="fragment")

    assert '<p name="x" style="content: \'bad\'">' in normalized
    assert receipts == []


def test_already_canonical_document_has_stable_digest_and_is_idempotent() -> None:
    canonical = "<!DOCTYPE html><html><head></head><body><p>stable</p></body></html>"

    first, first_receipts = normalize_authored_html_tree(canonical, profile="document")
    second, second_receipts = normalize_authored_html_tree(first, profile="document")

    assert first == canonical
    assert second == first
    assert _digest(second) == _digest(first)
    assert first_receipts == second_receipts == []


def test_switch_defaults_legacy_and_both_paths_are_callable() -> None:
    settings = RuntimeSettings.from_sources(environ={}, user_config={})
    assert get_authored_html_normalization(settings) == "legacy"
    source = '<main class="x">safe</main>'

    legacy = normalize_authored_html(source, mode="legacy")
    tree = normalize_authored_html(source, mode="tree", profile="fragment")

    assert legacy.html == source
    assert tree.html == source


def test_receipt_detail_is_bounded() -> None:
    receipts = ReceiptCollector()
    receipts.add("rule", 1, "x" * (MAX_RECEIPT_DETAIL_CHARS + 10))

    assert len(receipts.entries()[0]["detail"]) == MAX_RECEIPT_DETAIL_CHARS
