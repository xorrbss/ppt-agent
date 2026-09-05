"""Deterministic single-title repair for authored slide HTML."""

from __future__ import annotations

import pytest

from services.authored_composition_policy import (
    _AuthoredStartTagCollector,
    _attribute_map,
    ensure_authored_qids,
    ensure_authored_semantic_roles,
)
from utils.authored_semantic_role_repair import (
    TITLE_DEMOTED_RULE_ID,
    TITLE_PROMOTED_RULE_ID,
    canonicalize_authored_roles,
    ensure_title_role,
)


def _roles(html: str) -> dict[str, str]:
    parser = _AuthoredStartTagCollector()
    parser.feed(html)
    parser.close()
    return {
        _attribute_map(raw_attrs).get("data-qid", ""): _attribute_map(raw_attrs)
        .get("data-semantic-role", "")
        .strip()
        .lower()
        for _tag, raw_attrs, _raw in parser.tags
        if _attribute_map(raw_attrs).get("data-qid", "").strip()
    }


def test_first_text_owning_h1_is_promoted() -> None:
    html = (
        '<section data-qid="s" data-semantic-role="decoration">'
        '<h1 data-qid="t" data-semantic-role="body">제목</h1>'
        '<p data-qid="b" data-semantic-role="body">본문</p>'
        "</section>"
    )
    repaired, findings = ensure_title_role(html, role="ORDINARY")
    assert findings == [
        {"rule": TITLE_PROMOTED_RULE_ID, "detail": "title", "count": 1}
    ]
    assert _roles(repaired)["t"] == "title"
    assert _roles(repaired)["b"] == "body"


def test_decoration_labelled_h1_is_promoted() -> None:
    html = (
        '<h1 data-qid="t" data-semantic-role="decoration">제목</h1>'
        '<p data-qid="b" data-semantic-role="body">본문</p>'
    )
    repaired, findings = ensure_title_role(html, role="COVER")
    assert findings[0]["rule"] == TITLE_PROMOTED_RULE_ID
    assert 'data-qid="t" data-semantic-role="title"' in repaired


def test_largest_heading_is_promoted_when_no_h1_owns_text() -> None:
    html = (
        '<h3 data-qid="small" data-semantic-role="body" style="font-size:18px">작은</h3>'
        '<h2 data-qid="big" data-semantic-role="body" style="font-size:30px">큰</h2>'
        '<p data-qid="b" data-semantic-role="body" style="font-size:40px">본문</p>'
    )
    repaired, _findings = ensure_title_role(html)
    roles = _roles(repaired)
    assert roles["big"] == "title"
    assert roles["small"] == "body"
    assert roles["b"] == "body"


def test_largest_inline_font_owner_is_promoted_when_no_heading_owns_text() -> None:
    html = (
        '<p data-qid="a" data-semantic-role="body" style="font-size:18px">작은</p>'
        '<p data-qid="c" data-semantic-role="body" style="color:red;font-size:44px">큰</p>'
        '<p data-qid="d" data-semantic-role="body" style="font-size:24px">중간</p>'
    )
    repaired, _findings = ensure_title_role(html)
    assert _roles(repaired)["c"] == "title"


def test_inline_font_size_is_inherited_by_nested_owners() -> None:
    """The live cover defect: the h1's qid-bearing spans own the title text."""

    html = (
        '<main data-qid="m" data-semantic-role="decoration">'
        '<div data-qid="cover-author" data-semantic-role="body" style="font-size:18px">발표자</div>'
        '<h1 data-qid="cover-title" data-semantic-role="decoration" style="font-size:54px">'
        '<span data-qid="cover-title-line1" data-semantic-role="body" style="display:block">첫 줄</span>'
        '<span data-qid="cover-title-line2" data-semantic-role="body" style="display:block">둘째 줄</span>'
        "</h1>"
        '<p data-qid="cover-client" data-semantic-role="body" style="font-size:32px">고객사</p>'
        "</main>"
    )
    repaired, findings = ensure_title_role(html, role="COVER")
    assert findings[0]["rule"] == TITLE_PROMOTED_RULE_ID
    roles = _roles(repaired)
    assert roles["cover-title-line1"] == "title"
    assert roles["cover-title-line2"] == "body"
    assert roles["cover-client"] == "body"


def test_slide_with_a_title_owner_is_byte_identical() -> None:
    html = (
        '<h1 data-qid="t" data-semantic-role="title">제목</h1>'
        '<p data-qid="b" data-semantic-role="body">본문</p>'
    )
    repaired, findings = ensure_title_role(html)
    assert repaired == html
    assert findings == []


def test_repair_is_idempotent() -> None:
    html = (
        '<h1 data-qid="t" data-semantic-role="decoration">제목</h1>'
        '<p data-qid="b" data-semantic-role="body">본문</p>'
    )
    once, _ = ensure_title_role(html)
    twice, findings = ensure_title_role(once)
    assert twice == once
    assert findings == []


def test_extra_title_owners_are_demoted_to_supporting() -> None:
    html = (
        '<h1 data-qid="t" data-semantic-role="title" style="font-size:34px">목차</h1>'
        '<h2 data-qid="c1" data-semantic-role="title" style="font-size:24px">1장</h2>'
        '<h2 data-qid="c2" data-semantic-role="title" style="font-size:24px">2장</h2>'
        '<p data-qid="b" data-semantic-role="body">본문</p>'
    )
    repaired, findings = ensure_title_role(html, role="ORDINARY", demote=True)
    assert findings == [
        {"rule": TITLE_DEMOTED_RULE_ID, "detail": "title->supporting", "count": 2}
    ]
    assert ensure_title_role(html) == (html, []), "demotion is opt-in"
    roles = _roles(repaired)
    assert roles["t"] == "title"
    assert roles["c1"] == "supporting"
    assert roles["c2"] == "supporting"
    assert roles["b"] == "body"


def test_demotion_is_skipped_without_a_body_owner() -> None:
    """The gate requires a body owner too; demotion must never remove the last."""

    html = (
        '<h1 data-qid="t" data-semantic-role="title" style="font-size:34px">목차</h1>'
        '<h2 data-qid="c1" data-semantic-role="title" style="font-size:24px">1장</h2>'
    )
    repaired, findings = ensure_title_role(html, demote=True)
    assert repaired == html
    assert findings == []


def test_header_contract_title_is_never_demoted() -> None:
    html = (
        '<h2 data-qid="hdr" data-header-part="title" data-semantic-role="title"'
        ' style="font-size:20px">헤더 제목</h2>'
        '<h1 data-qid="big" data-semantic-role="title" style="font-size:60px">큰 글씨</h1>'
        '<p data-qid="b" data-semantic-role="body">본문</p>'
    )
    repaired, findings = ensure_title_role(html, demote=True)
    roles = _roles(repaired)
    assert roles["hdr"] == "title"
    assert roles["big"] == "supporting"
    assert findings[0]["count"] == 1


def test_document_without_qids_is_untouched() -> None:
    html = "<h1>제목</h1><p>본문</p>"
    assert ensure_title_role(html) == (html, [])


def test_non_string_html_is_rejected() -> None:
    with pytest.raises(TypeError):
        ensure_title_role(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "html",
    [
        '<section data-qid="s"><h1 data-qid="t">제목<span>강조</span></h1>'
        '<div data-qid="wrap"><p data-qid="p">본문</p></div>'
        '<div data-qid="empty"></div><style>.a{content:"x"}</style></section>',
        '<div data-qid="a">A&amp;B</div><div data-qid="b">&nbsp;</div>'
        '<p data-qid="c"><b>x</b></p>',
        '<ul data-qid="l"><li data-qid="i1">one</li><li>two</li></ul>',
        '<div data-qid="x">text<div data-qid="y">inner</div>tail</div>',
        '<h1 data-qid="t">A<br>B</h1>',
        '<div data-qid="u"><img data-qid="im" src="x"/>cap</div>',
    ],
)
def test_text_ownership_matches_the_composition_policy(html: str) -> None:
    """The repair must rank the same owners the role stamper and gate use."""

    parser = _AuthoredStartTagCollector()
    parser.feed(html)
    parser.close()
    policy_owners = {
        _attribute_map(raw_attrs).get("data-qid", "")
        for index, (_tag, raw_attrs, _raw) in enumerate(parser.tags)
        if parser.owns_text[index]
    }
    from utils.authored_semantic_role_repair import _collect

    assert {owner.attrs.get("data-qid", "") for owner in _collect(html)} == policy_owners


def test_canonicalize_authored_roles_repairs_the_cover_after_role_stamping() -> None:
    """End to end: qids + role stamping alone leave this cover without a title."""

    cover = (
        '<main><h1 data-qid="cover-title" style="font-size:54px">'
        '<span data-qid="cover-title-line1" style="display:block">첫 줄</span>'
        '<span data-qid="cover-title-line2" style="display:block">둘째 줄</span>'
        "</h1>"
        '<p data-qid="cover-summary" style="font-size:18px">요약 문장</p></main>'
    )
    stamped = ensure_authored_semantic_roles(ensure_authored_qids(cover))
    assert "title" not in set(_roles(stamped).values())

    repaired = canonicalize_authored_roles(cover, role="COVER")
    assert _roles(repaired)["cover-title-line1"] == "title"
    assert canonicalize_authored_roles(repaired, role="COVER") == repaired


def test_promotion_never_spends_the_slides_last_body_owner() -> None:
    """Trading a missing-title blocker for a missing-body one publishes nothing."""

    html = (
        '<p data-qid="eyebrow" data-semantic-role="supporting" style="font-size:40px">라벨</p>'
        '<p data-qid="only-body" data-semantic-role="body" style="font-size:60px">본문</p>'
    )
    repaired, findings = ensure_title_role(html)
    roles = _roles(repaired)
    assert findings[0]["rule"] == TITLE_PROMOTED_RULE_ID
    assert roles["eyebrow"] == "title"
    assert roles["only-body"] == "body"


def test_a_single_text_owner_is_still_promoted() -> None:
    html = '<p data-qid="only" data-semantic-role="body">본문</p>'
    repaired, findings = ensure_title_role(html)
    assert findings[0]["rule"] == TITLE_PROMOTED_RULE_ID
    assert _roles(repaired)["only"] == "title"
