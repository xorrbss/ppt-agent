"""``validate_header_contract``'s ``chapter_prefix`` keyword.

The bench found a patched slide that lost its chapter numeral from the header
eyebrow after a repair round: the eyebrow kept its section name but the
deck's frozen chapter opener was gone. Nothing checked for that before this
module's ``header_eyebrow_prefix_missing`` code existed.
"""

from models.authored_header_contract import default_header_contract
from utils.authored_header_validation import validate_header_contract


_ROLE = "PILLARS"
_BAND = 150
_PREFIX = "Ⅱ. 제안개요 > "


def _slide(body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'></head>"
        f"<body>{body}</body></html>"
    )


def _with_eyebrow(contract, eyebrow_html: str, *, band: int = _BAND) -> str:
    return _slide(
        f'<div data-header-part="band" data-header-band="{band}" '
        f'style="height:{band}px">'
        f"{eyebrow_html}"
        '<h1 data-header-part="title">단일 거래 흐름</h1>'
        '<div data-header-part="divider"></div></div>'
        f'<div data-header-part="body" '
        f'style="margin-top:{contract.content_gap_px}px">본문</div>'
    )


def _slide_with_eyebrow_text(contract, text: str, *, band: int = _BAND) -> str:
    return _with_eyebrow(
        contract, f'<span data-header-part="eyebrow">{text}</span>', band=band
    )


def test_missing_prefix_is_flagged():
    contract = default_header_contract(chapter_breadcrumb=True)
    html = _slide_with_eyebrow_text(contract, "목표 아키텍처")

    violations = validate_header_contract(
        html, contract, role=_ROLE, content="본문", chapter_prefix=_PREFIX
    )

    assert "header_eyebrow_prefix_missing" in violations


def test_present_prefix_is_not_flagged():
    contract = default_header_contract(chapter_breadcrumb=True)
    html = _slide_with_eyebrow_text(contract, f"{_PREFIX}목표 아키텍처")

    violations = validate_header_contract(
        html, contract, role=_ROLE, content="본문", chapter_prefix=_PREFIX
    )

    assert "header_eyebrow_prefix_missing" not in violations


def test_present_prefix_survives_whitespace_collapsing():
    """The check compares after collapsing whitespace, not byte for byte."""
    contract = default_header_contract(chapter_breadcrumb=True)
    # A line break and doubled internal spaces where the deck wrote one.
    html = _slide_with_eyebrow_text(
        contract, "Ⅱ.  제안개요  >\n목표 아키텍처"
    )

    violations = validate_header_contract(
        html, contract, role=_ROLE, content="본문", chapter_prefix=_PREFIX
    )

    assert "header_eyebrow_prefix_missing" not in violations


def test_front_matter_slide_skips_the_check_entirely():
    """``chapter_prefix=None`` means front matter: never flagged, whatever
    the eyebrow says."""
    contract = default_header_contract(chapter_breadcrumb=True)
    html = _slide_with_eyebrow_text(contract, "표지")

    violations = validate_header_contract(
        html, contract, role=_ROLE, content="본문", chapter_prefix=None
    )

    assert "header_eyebrow_prefix_missing" not in violations


def test_omitting_the_keyword_is_byte_identical_to_existing_callers():
    """Every existing caller (no ``chapter_prefix`` argument at all) must see
    exactly the result it always has, whether or not the slide happens to
    carry a mismatched eyebrow."""
    contract = default_header_contract(chapter_breadcrumb=True)
    html = _slide_with_eyebrow_text(contract, "목표 아키텍처")

    with_keyword_none = validate_header_contract(
        html, contract, role=_ROLE, content="본문", chapter_prefix=None
    )
    without_keyword = validate_header_contract(
        html, contract, role=_ROLE, content="본문"
    )

    assert without_keyword == with_keyword_none
    assert "header_eyebrow_prefix_missing" not in without_keyword


def test_a_duplicated_eyebrow_is_not_also_flagged_for_a_missing_prefix():
    """``header_eyebrow_duplicated`` already reports the real problem; a
    second, misleading prefix code on top of it would only confuse the
    correction call about what to fix."""
    contract = default_header_contract(chapter_breadcrumb=True)
    html = _with_eyebrow(
        contract,
        '<span data-header-part="eyebrow">' + _PREFIX + "목표 아키텍처</span>"
        '<span data-header-part="eyebrow">또 다른 것</span>',
    )

    violations = validate_header_contract(
        html, contract, role=_ROLE, content="본문", chapter_prefix=_PREFIX
    )

    assert "header_eyebrow_duplicated" in violations
    assert "header_eyebrow_prefix_missing" not in violations


def test_a_missing_eyebrow_is_not_also_flagged_for_a_missing_prefix():
    contract = default_header_contract(chapter_breadcrumb=True)
    html = _slide(
        f'<div data-header-part="band" data-header-band="{_BAND}" '
        f'style="height:{_BAND}px">'
        '<h1 data-header-part="title">단일 거래 흐름</h1>'
        '<div data-header-part="divider"></div></div>'
        f'<div data-header-part="body" '
        f'style="margin-top:{contract.content_gap_px}px">본문</div>'
    )

    violations = validate_header_contract(
        html, contract, role=_ROLE, content="본문", chapter_prefix=_PREFIX
    )

    assert "header_eyebrow_missing" in violations
    assert "header_eyebrow_prefix_missing" not in violations
