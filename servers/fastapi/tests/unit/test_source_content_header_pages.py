"""A restyle requires a header only from pages whose source has one.

The deck's header contract is one design every content page reproduces. Front
and back matter are not content pages: a cover and a closing page carry a
title, a statement and a footer, and no running header or page number. Asking
them for a band rejects a composition that is faithful to the source, which is
what ended operation ce48036c after slide 35 spent both of its provider calls
on ``header_band_missing``.
"""

from __future__ import annotations

from services.source_content.header_pages import (
    header_absent_pages,
    source_pages_are_numbered,
)
from utils.llm_calls.author_deck import AuthoredDeckPlan


RUNNING_HEADER = "꿀벌쌤 | 숏폼대행 1인사업자 홀로서기"


def _deck(page_count: int = 8) -> list[list[tuple[str, str]]]:
    """A deck shaped like the real one: matter, content pages, closing."""

    pages: list[list[tuple[str, str]]] = [
        [("b:1:1", "숏폼대행 1인사업자 홀로서기"), ("b:1:2", "4주 8시간")],
    ]
    for index in range(2, page_count):
        pages.append([
            (f"b:{index}:1", f"{index}주차 핵심"),
            (f"b:{index}:2", RUNNING_HEADER),
            (f"b:{index}:3", str(index)),
        ])
    pages.append([
        (f"b:{page_count}:1", "🐝 꿀벌쌤"),
        (f"b:{page_count}:2", "이제,\n홀로서기 시작입니다."),
        (f"b:{page_count}:3", "꿀벌쌤 강의용 마스터 PPT"),
    ])
    return pages


def test_front_and_back_matter_are_the_pages_without_header_furniture() -> None:
    pages = _deck()
    assert header_absent_pages(pages) == frozenset({0, len(pages) - 1})


def test_either_a_running_header_or_a_page_number_is_enough() -> None:
    numbered_only = [
        [("a", "표지")],
        [("b", "본문"), ("c", "2")],
        [("d", "본문"), ("e", "3")],
        [("f", "본문"), ("g", "4")],
    ]
    assert header_absent_pages(numbered_only) == frozenset({0})
    header_only = [
        [("a", "표지")],
        [("b", "본문"), ("c", RUNNING_HEADER)],
        [("d", "본문"), ("e", RUNNING_HEADER)],
        [("f", "본문"), ("g", RUNNING_HEADER)],
    ]
    assert header_absent_pages(header_only) == frozenset({0})


def test_a_number_that_is_not_this_page_is_not_a_page_number() -> None:
    # A stale or content number ("10곳 찾기" reduced to a metric) is not this
    # page's number, so a page carrying only that has no header furniture.
    pages = [
        [("a", "표지")],
        [("b", "첫 번째 주제"), ("c", RUNNING_HEADER)],
        [("d", "두 번째 주제"), ("e", RUNNING_HEADER)],
        [("f", "세 번째 주제"), ("g", "99")],
    ]
    assert header_absent_pages(pages) == frozenset({0, 3})


def test_repeated_body_text_is_not_mistaken_for_furniture_on_a_real_deck() -> None:
    # The rule only ever removes a requirement, so a false negative keeps
    # today's behaviour; this pins the shape a real deck has.
    pages = _deck(12)
    absent = header_absent_pages(pages)
    assert absent == frozenset({0, len(pages) - 1})
    assert all(index not in absent for index in range(1, len(pages) - 1))


def test_no_source_means_no_exemption() -> None:
    assert header_absent_pages(None) == frozenset()
    assert header_absent_pages([]) == frozenset()


def test_the_plan_hands_an_exempt_slide_no_contract(monkeypatch) -> None:
    from models.authored_header_contract import AuthoredHeaderContract

    contract = AuthoredHeaderContract()
    plan = AuthoredDeckPlan(
        contents=("a", "b"),
        roles=("COVER", "CLOSING"),
        reference_images=((), ()),
        reference_mode="style",
        brand=None,
        design_system="",
        structured_contexts=(None, None),
        page_number_mode="template",
        header_contract=contract,
        header_exempt_slides=frozenset({1}),
    )
    assert plan.header_contract_at(0) is contract
    assert plan.header_contract_at(1) is None


def test_the_source_decides_whether_a_restyled_deck_is_numbered() -> None:
    """Page numbers are source text here, not a design decision.

    The header-contract call reads a content sample and judges whether the deck
    "looks numbered". On identical input it answered True on one run and False
    on the next, and the False run told every slide to render no page number
    and no footer text -- then failed those slides for the missing source text.
    """

    assert source_pages_are_numbered(_deck()) is True
    unnumbered = [
        [("a", "표지")],
        [("b", "첫 주제"), ("c", RUNNING_HEADER)],
        [("d", "둘째 주제"), ("e", RUNNING_HEADER)],
        [("f", "셋째 주제"), ("g", RUNNING_HEADER)],
    ]
    assert source_pages_are_numbered(unnumbered) is False
    # A number that is not this page's own is content, not pagination.
    stale = [
        [("a", "표지")],
        [("b", "10곳 찾기"), ("c", "10")],
        [("d", "둘째"), ("e", "10")],
        [("f", "셋째"), ("g", "10")],
    ]
    assert source_pages_are_numbered(stale) is False
    assert source_pages_are_numbered(None) is False
    assert source_pages_are_numbered([]) is False
