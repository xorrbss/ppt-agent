from __future__ import annotations

from utils.authored_html_tree.raw_diagnostics import diagnose_raw_authored_html
from utils.renderer_isolation import (
    MAX_ATTRIBUTES_PER_NODE,
    MAX_DOM_DEPTH,
    MAX_DOM_NODES,
    MAX_HTML_BYTES,
    MAX_TEXT_BYTES,
)


def test_raw_diagnostics_report_duplicate_attributes_before_tree_parsing() -> None:
    diagnostics = diagnose_raw_authored_html('<div ID="first" id="second">ok</div>')

    assert diagnostics.byte_size == len('<div ID="first" id="second">ok</div>'.encode("utf-8"))
    assert diagnostics.text_bytes_estimate == 2
    assert diagnostics.has_nul is False
    assert diagnostics.duplicate_attribute_findings == [("div", "id", 2)]
    assert [finding.code for finding in diagnostics.violations] == ["duplicate_html_attribute"]


def test_raw_diagnostics_ignore_markup_inside_comments() -> None:
    diagnostics = diagnose_raw_authored_html('<!-- <p id="a" id="b"> --><p>text</p>')

    assert diagnostics.duplicate_attribute_findings == []
    assert diagnostics.text_bytes_estimate == 4
    assert diagnostics.violations == []


def test_raw_diagnostics_keep_the_existing_stray_nonvoid_rejection() -> None:
    diagnostics = diagnose_raw_authored_html("<div></span></div>")

    assert [finding.code for finding in diagnostics.violations] == [
        "html_closing_tag_unbalanced"
    ]


def test_raw_diagnostics_use_existing_size_and_nul_limits() -> None:
    oversized = diagnose_raw_authored_html("x" * (MAX_HTML_BYTES + 1))
    nul = diagnose_raw_authored_html("<p>bad\x00text</p>")

    assert oversized.byte_size == MAX_HTML_BYTES + 1
    assert [finding.code for finding in oversized.violations] == [
        "authored_html_source_size_limit_exceeded"
    ]
    assert nul.has_nul is True
    assert [finding.code for finding in nul.violations] == ["authored_html_contains_nul"]


def test_raw_diagnostics_use_existing_text_limit() -> None:
    diagnostics = diagnose_raw_authored_html(f"<p>{'x' * (MAX_TEXT_BYTES + 1)}</p>")

    assert diagnostics.text_bytes_estimate == MAX_TEXT_BYTES + 1
    assert [finding.code for finding in diagnostics.violations] == ["html_text_limit_exceeded"]


def test_raw_diagnostics_use_existing_tree_shape_limits() -> None:
    nodes = diagnose_raw_authored_html("<br>" * (MAX_DOM_NODES + 1))
    depth = diagnose_raw_authored_html(
        "<div>" * (MAX_DOM_DEPTH + 1) + "</div>" * (MAX_DOM_DEPTH + 1)
    )
    attrs = " ".join(f'data-x-{index}="x"' for index in range(MAX_ATTRIBUTES_PER_NODE + 1))
    attributes = diagnose_raw_authored_html(f"<div {attrs}></div>")

    assert [finding.code for finding in nodes.violations] == ["dom_node_limit_exceeded"]
    assert [finding.code for finding in depth.violations] == ["dom_depth_limit_exceeded"]
    assert [finding.code for finding in attributes.violations] == [
        "attribute_per_node_limit_exceeded"
    ]
