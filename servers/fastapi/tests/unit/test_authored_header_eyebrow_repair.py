"""``repair_eyebrow_prefix``: re-inserting a dropped chapter prefix.

Byte-span editing only -- never a reserialised tree -- the same technique
``authored_header_repair`` uses, so nothing outside the eyebrow's own text can
move by even one byte.
"""

from utils.authored_header_eyebrow_repair import repair_eyebrow_prefix


_PREFIX = "Ⅱ. 제안개요 > "


def _slide(body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'></head>"
        f"<body>{body}</body></html>"
    )


def _with_eyebrow(text: str, *, qid: str | None = None) -> str:
    qid_attr = f' data-qid="{qid}"' if qid else ""
    return _slide(
        f'<div data-header-part="band">'
        f'<span data-header-part="eyebrow"{qid_attr}>{text}</span>'
        '<h1 data-header-part="title">단일 거래 흐름</h1></div>'
    )


def test_inserts_the_prefix_before_the_existing_text():
    html = _with_eyebrow("목표 아키텍처")

    repaired, changed = repair_eyebrow_prefix(html, chapter_prefix=_PREFIX)

    assert changed
    assert f'<span data-header-part="eyebrow">{_PREFIX}목표 아키텍처</span>' in repaired
    # Nothing else in the document moved.
    assert repaired.replace(_PREFIX, "", 1) == html


def test_inserts_the_prefix_alone_when_the_eyebrow_is_empty():
    html = _with_eyebrow("")

    repaired, changed = repair_eyebrow_prefix(html, chapter_prefix=_PREFIX)

    assert changed
    assert f'<span data-header-part="eyebrow">{_PREFIX}</span>' in repaired


def test_a_trailing_separator_space_is_not_doubled():
    """``chapter_prefix`` already ends in the chapter plan's own separator
    space; the repair must not add a second one."""
    html = _with_eyebrow("목표 아키텍처")

    repaired, changed = repair_eyebrow_prefix(html, chapter_prefix=_PREFIX)

    assert changed
    assert "  " not in repaired


def test_idempotent_once_the_prefix_is_present():
    html = _with_eyebrow("목표 아키텍처")
    once, changed_once = repair_eyebrow_prefix(html, chapter_prefix=_PREFIX)
    assert changed_once

    twice, changed_twice = repair_eyebrow_prefix(once, chapter_prefix=_PREFIX)

    assert not changed_twice
    assert twice == once


def test_leaves_the_document_untouched_when_no_eyebrow_exists():
    html = _slide('<h1 data-header-part="title">단일 거래 흐름</h1>')

    repaired, changed = repair_eyebrow_prefix(html, chapter_prefix=_PREFIX)

    assert not changed
    assert repaired == html


def test_leaves_the_document_untouched_when_the_eyebrow_is_ambiguous():
    """Two eyebrow elements with no locator to pick one: refuse rather than
    guess."""
    html = _slide(
        '<div data-header-part="band">'
        '<span data-header-part="eyebrow">첫번째</span>'
        '<span data-header-part="eyebrow">두번째</span>'
        "</div>"
    )

    repaired, changed = repair_eyebrow_prefix(html, chapter_prefix=_PREFIX)

    assert not changed
    assert repaired == html


def test_a_locator_disambiguates_which_eyebrow_to_edit():
    html = _slide(
        '<div data-header-part="band">'
        '<span data-header-part="eyebrow" data-qid="q-e1">첫번째</span>'
        '<span data-header-part="eyebrow" data-qid="q-e2">두번째</span>'
        "</div>"
    )

    repaired, changed = repair_eyebrow_prefix(
        html, chapter_prefix=_PREFIX, eyebrow_qid_or_locator="q-e2"
    )

    assert changed
    assert f'data-qid="q-e2">{_PREFIX}두번째' in repaired
    assert "q-e1\">첫번째" in repaired  # the other eyebrow is untouched


def test_a_locator_that_matches_nothing_leaves_the_document_untouched():
    html = _with_eyebrow("목표 아키텍처", qid="q-e1")

    repaired, changed = repair_eyebrow_prefix(
        html, chapter_prefix=_PREFIX, eyebrow_qid_or_locator="q-does-not-exist"
    )

    assert not changed
    assert repaired == html


def test_leaves_the_document_untouched_when_the_html_is_malformed():
    html = _slide('<div data-header-part="band"><span data-header-part="eyebrow">unterminated')

    repaired, changed = repair_eyebrow_prefix(html, chapter_prefix=_PREFIX)

    assert not changed
    assert repaired == html
