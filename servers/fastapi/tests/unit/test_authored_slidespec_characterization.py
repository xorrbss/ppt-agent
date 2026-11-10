"""Characterization contracts for SlideSpec-era authored repair boundaries."""

import re

from utils.authored_dom_quality import (
    build_compact_dom_map,
    ensure_authored_qids,
)
from utils.authored_patch_engine import apply_authored_patch


def _plan(*actions):
    return {"strategy": "patch", "actions": list(actions), "rationale": "test"}


def test_d1_d2_void_elements_never_receive_generated_qids_or_capture_later_text():
    source = '<p data-qid="copy">Before<br>After<img src="hero.png">Tail</p>'

    enriched = ensure_authored_qids(source)
    nodes = build_compact_dom_map(enriched)

    assert enriched == ensure_authored_qids(enriched)
    assert re.search(r"<br(?:\s[^>]*)?\s+data-qid=", enriched) is None
    assert re.search(r"<img(?:\s[^>]*)?\s+data-qid=", enriched) is None
    assert [(node.tag, node.qid, node.text) for node in nodes] == [
        ("p", "copy", "Before After Tail")
    ]


def test_d2_set_text_rejects_a_hard_break_instead_of_flattening_it():
    source = '<p data-qid="copy">First<br>Second</p>'

    result = apply_authored_patch(
        source,
        _plan({"action": "setText", "qid": "copy", "text": "Flattened"}),
    )

    assert not result.ok
    assert result.html == source
    assert result.fallback_reason == "unsafe_patch"
    assert "hard line breaks" in (result.failure_reason or "")


def test_d3_browser_camel_case_styles_normalize_to_allowlisted_kebab_case():
    source = '<p data-qid="copy" style="font-size: 20px">Copy</p>'

    result = apply_authored_patch(
        source,
        _plan(
            {
                "action": "setStyle",
                "qid": "copy",
                "styles": {"fontSize": "24px", "marginTop": "8px"},
            }
        ),
    )

    assert result.ok
    assert 'style="font-size: 24px; margin-top: 8px"' in result.html
    assert result.changes[0].after == {
        "styles": {"font-size": "24px", "margin-top": "8px"}
    }


def test_d3_camel_and_kebab_aliases_are_duplicate_properties():
    result = apply_authored_patch(
        '<p data-qid="copy">Copy</p>',
        _plan(
            {
                "action": "setStyle",
                "qid": "copy",
                "styles": {"font-size": "24px", "fontSize": "22px"},
            }
        ),
    )

    assert not result.ok
    assert result.fallback_reason == "invalid_patch_schema"
    assert "duplicate CSS property 'font-size'" in (result.failure_reason or "")
