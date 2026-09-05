"""Unit tests for utils.authored_header_repair (H2, authored-quality-speed
programme design section 6).

Covers each deterministic fix in isolation -- an unknown ``data-header-part``
dropped, a duplicated part deduplicated (attribute stripped when the
duplicate carries content, the whole element removed when it is empty), an
out-of-bounds band clamped on both its attribute and its CSS height, a body
wrapper's top offset re-pinned via the patch engine (both when it was already
wrong and when the band clamp itself moved the target) -- plus that a
conforming slide is returned unchanged and that a semantic violation this
module cannot fix is left in ``remaining``.

``validate_header_contract``'s band checks never re-derive the "correct"
band from title/eyebrow metrics -- they only check that the declared integer
sits in ``[band_min_px, band_max_px]``, that the CSS ``height`` on the band
root agrees with it, and that the body's offset agrees with it plus
``content_gap_px``. Fixtures below pick a plain in-bounds band number rather
than reproducing the deck's own derivation arithmetic.
"""

from models.authored_header_contract import default_header_contract
from utils.authored_dom_quality import ensure_authored_qids
from utils.authored_header_repair import repair_header_contract
from utils.authored_header_validation import validate_header_contract


_ROLE = "PILLARS"
_BAND = 150


def _slide(body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'></head><body>{body}</body></html>"


def _conforming(contract, *, band: int = _BAND, band_style: str | None = None, body_style: str | None = None) -> str:
    """A slide authored to the letter of ``contract``'s brief.

    The default contract enables the eyebrow and the divider, and disables
    the tick, the subline and the right slot -- see
    ``default_header_contract``'s own docstring.
    """
    band_css = band_style if band_style is not None else f"height:{band}px"
    body_css = body_style if body_style is not None else f"margin-top:{contract.content_gap_px}px"
    return _slide(
        f'<div data-header-part="band" data-header-band="{band}" style="{band_css}">'
        '<span data-header-part="eyebrow">대외 연계</span>'
        '<h1 data-header-part="title">단일 거래 흐름</h1>'
        '<div data-header-part="divider"></div></div>'
        f'<div data-header-part="body" style="{body_css}">본문</div>'
    )


def test_a_conforming_slide_is_returned_unchanged():
    contract = default_header_contract()
    html = _conforming(contract)
    assert validate_header_contract(html, contract, role=_ROLE) == ()

    result = repair_header_contract(html, contract, role=_ROLE)

    assert result.html == html
    assert result.fixed == ()
    assert result.remaining == ()
    assert result.changed is False


def test_a_semantic_violation_is_left_in_remaining_untouched():
    contract = default_header_contract()
    html = _conforming(contract).replace('data-header-part="title"', 'data-header-part="notatitle"')

    result = repair_header_contract(html, contract, role=_ROLE)

    # `notatitle` is an unknown part and is dropped like any other unknown
    # attribute -- but the title the deck requires is still missing, and
    # nothing in this module may invent one.
    assert "header_title_missing" in result.remaining
    assert "header_title_missing" not in result.fixed


def test_an_unknown_part_attribute_is_dropped():
    contract = default_header_contract()
    html = _conforming(contract).replace(
        '<div data-header-part="body"',
        '<div data-header-part="callout">여담</div><div data-header-part="body"',
    )

    result = repair_header_contract(html, contract, role=_ROLE)

    assert 'data-header-part="callout"' not in result.html
    assert "여담" in result.html
    assert "header_part_unknown" in result.fixed
    assert result.remaining == ()
    assert result.changed is True


def test_a_duplicated_part_with_content_keeps_the_first_and_strips_the_attribute():
    contract = default_header_contract()
    html = _conforming(contract).replace(
        '<div data-header-part="divider"></div></div>',
        '<div data-header-part="divider"></div>'
        '<h1 data-header-part="title">중복 제목</h1></div>',
    )

    result = repair_header_contract(html, contract, role=_ROLE)

    assert result.html.count('data-header-part="title"') == 1
    assert "단일 거래 흐름" in result.html
    assert "중복 제목" in result.html
    assert "header_title_duplicated" in result.fixed
    assert result.remaining == ()
    assert result.changed is True


def test_a_duplicated_empty_part_is_removed_entirely():
    contract = default_header_contract()
    html = _conforming(contract).replace(
        '<div data-header-part="divider"></div></div>',
        '<div data-header-part="divider"></div>'
        '<div data-header-part="divider"></div></div>',
    )

    result = repair_header_contract(html, contract, role=_ROLE)

    assert result.html.count('data-header-part="divider"') == 1
    assert "header_divider_duplicated" in result.fixed
    assert result.remaining == ()
    assert result.changed is True


def test_a_band_above_the_ceiling_is_clamped_on_attribute_and_css_height():
    contract = default_header_contract().model_copy(update={"band_max_px": 120})
    # Flow body: `margin-top` compliance does not depend on the band number,
    # so clamping the band alone must fully resolve this slide.
    html = _conforming(contract, band=320, band_style="height:320px")

    result = repair_header_contract(html, contract, role=_ROLE)

    assert 'data-header-band="120"' in result.html
    assert "height:120px" in result.html
    assert "header_band_out_of_bounds" in result.fixed
    assert result.remaining == ()
    assert result.changed is True


def test_a_band_below_the_floor_is_clamped():
    contract = default_header_contract().model_copy(update={"band_min_px": 160})
    html = _conforming(contract, band=100, band_style="height:100px")

    result = repair_header_contract(html, contract, role=_ROLE)

    assert 'data-header-band="160"' in result.html
    assert "height:160px" in result.html
    assert "header_band_out_of_bounds" in result.fixed
    assert result.remaining == ()


def test_a_body_top_mismatch_is_repinned_via_the_patch_engine():
    contract = default_header_contract()
    html = ensure_authored_qids(_conforming(contract, body_style="margin-top:999px"))

    result = repair_header_contract(html, contract, role=_ROLE)

    assert f"margin-top:{contract.content_gap_px}px" in result.html.replace(" ", "")
    assert "header_body_top_mismatch" in result.fixed
    assert result.remaining == ()
    assert result.changed is True


def test_a_body_top_that_cannot_be_read_is_repinned():
    contract = default_header_contract()
    html = ensure_authored_qids(_conforming(contract, body_style="margin-top:1.5em"))

    result = repair_header_contract(html, contract, role=_ROLE)

    assert "header_body_top_unreadable" in result.fixed
    assert result.remaining == ()


def test_a_pinned_body_top_is_repinned_against_the_clamped_band():
    contract = default_header_contract().model_copy(update={"band_max_px": 120})
    html = ensure_authored_qids(
        _conforming(
            contract,
            band=320,
            band_style="height:320px",
            body_style=f"position:absolute;top:{320 + contract.content_gap_px}px;left:64px",
        )
    )

    result = repair_header_contract(html, contract, role=_ROLE)

    # The body top matched the *original* out-of-bounds band, so no body-top
    # violation was present at the start -- the band clamp alone would have
    # moved the target and re-broken it, which is exactly what the re-pin
    # step (run only after the clamp) exists to catch before it becomes
    # `remaining`.
    assert 'data-header-band="120"' in result.html
    assert f"top:{120 + contract.content_gap_px}px" in result.html.replace(" ", "")
    assert "header_band_out_of_bounds" in result.fixed
    assert result.remaining == ()
