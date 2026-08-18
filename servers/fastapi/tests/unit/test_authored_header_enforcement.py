"""What one authored slide must reproduce, and what is reported when it does not.

The contract is only half the feature: a model that drifts on slide 14 produces
exactly the inconsistency the deck-wide brief exists to remove. These are the
checks that catch the drift — the parts a slide must mark, the band it derives
and declares for itself, and the rule that every requirement identifier it shows
came from that slide's own text.
"""

import math

from models.authored_header_contract import (
    AuthoredHeaderContract,
    HeaderEyebrowSpec,
    HeaderRequirementSpec,
    HeaderRightSlotSpec,
    HeaderSublineSpec,
    HeaderTickSpec,
    HeaderTitleSpec,
    default_header_contract,
)
from utils.authored_header_prompt import render_header_contract
from utils.authored_header_validation import validate_header_contract
from utils.llm_calls.author_slide_prompt import requirement_marker
from utils.renderer_isolation import admit_authored_html


def _numbered_contract() -> AuthoredHeaderContract:
    """A contract whose deck was judged to number its requirements.

    The accent tick is opt-in, so a deck that renders one has to say so. These
    fixtures render one, which is what keeps the tick rules under test.
    """
    return AuthoredHeaderContract(
        eyebrow=HeaderEyebrowSpec(tick=HeaderTickSpec(enabled=True)),
        requirements=HeaderRequirementSpec(enabled=True),
        right_slot=HeaderRightSlotSpec(mode="requirements"),
    )


# --- post-render enforcement --------------------------------------------------


def _slide_html(body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'></head><body>{body}</body></html>"


#: Line box of a one-line label, as a multiple of its font size. The brief hands
#: the model the rows this produces — an 18px eyebrow, a 21px subline, an 18px
#: note line — so a slide built here is built on the numbers the model was given.
#: ``test_the_brief_states_the_band_derivation`` is what keeps the two tied.
_LABEL_LINE_RATIO = 1.4


def _round_px(value: float) -> int:
    """Round half up, the way the brief's own arithmetic rounds."""
    return int(math.floor(value + 0.5))


def _left_block_px(
    contract: AuthoredHeaderContract, *, title_lines: int, subline_lines: int = 0
) -> int:
    block = 0
    if contract.eyebrow.enabled:
        block += (
            _round_px(contract.eyebrow.font_size_px * _LABEL_LINE_RATIO)
            + contract.eyebrow_gap_px
        )
    block += title_lines * _round_px(
        contract.title.font_size_px * contract.title.line_height
    )
    if subline_lines:
        block += contract.subline_gap_px + subline_lines * _round_px(
            contract.subline.font_size_px * _LABEL_LINE_RATIO
        )
    return block


def _band_px(
    contract: AuthoredHeaderContract,
    *,
    title_lines: int = 1,
    subline_lines: int = 0,
    right_block_px: int = 0,
) -> int:
    """Derive one slide's band the way the brief tells the model to derive it."""
    tallest = max(
        _left_block_px(contract, title_lines=title_lines, subline_lines=subline_lines),
        right_block_px,
    )
    band = contract.header_top_px + tallest + contract.title_gap_px
    return min(max(band, contract.band_min_px), contract.band_max_px)


def _right_slot_html(contract: AuthoredHeaderContract) -> str:
    if contract.right_slot.mode == "none":
        return ""
    inner = ""
    if contract.requirements.enabled:
        inner += (
            '<div data-header-part="requirements" data-requirement-ids="POR-005">'
            "<span>POR-005</span></div>"
        )
    if contract.right_slot.mode in {"summary", "requirementsAndSummary"}:
        inner += "<p>현행 대비 30% 단축, 오류 재처리 자동화, 감사 추적 확보</p>"
    return f'<div data-header-part="right">{inner}</div>'


def _conforming_slide(
    contract: AuthoredHeaderContract,
    *,
    title_lines: int = 1,
    subline_lines: int = 0,
    right_block_px: int = 0,
    declared: int | None = None,
    band_style: str | None = None,
    body_style: str | None = None,
) -> str:
    """One slide authored to the letter of this contract's brief.

    The overrides exist so a test can break exactly one of the three statements a
    slide makes about its band — the number it declared, the height it wrote, and
    where it started the body — and watch that one violation appear alone.
    """
    band = _band_px(
        contract,
        title_lines=title_lines,
        subline_lines=subline_lines,
        right_block_px=right_block_px,
    )
    number = band if declared is None else declared
    band_css = f"height:{band}px" if band_style is None else band_style
    body_css = (
        f"margin-top:{contract.content_gap_px}px" if body_style is None else body_style
    )
    subline = (
        '<p data-header-part="subline">현행 대비 개선점</p>' if subline_lines else ""
    )
    return _slide_html(
        f'<div data-header-part="band" data-header-band="{number}" '
        f'style="{band_css}">'
        '<span data-header-part="tick"></span>'
        '<span data-header-part="eyebrow">대외 연계</span>'
        '<h1 data-header-part="title">단일 거래 흐름</h1>'
        f"{subline}"
        f"{_right_slot_html(contract)}"
        '<div data-header-part="divider"></div></div>'
        f'<div data-header-part="body" style="{body_css}">본문</div>'
    )


#: One eyebrow row, one title line and a chip row, so the band derives to 135px:
#: 32 + max(18 + 12 + 45, 22) + 28.
_CONFORMING = _conforming_slide(_numbered_contract())


def test_conforming_slide_reports_no_violation():
    contract = _numbered_contract()
    assert (
        validate_header_contract(
            _CONFORMING,
            contract,
            role="PILLARS",
        )
        == ()
    )


def test_missing_title_is_reported():
    contract = _numbered_contract()
    html = _CONFORMING.replace('data-header-part="title"', 'data-header-part="band"')
    assert "header_title_missing" in validate_header_contract(
        html, contract, role="PILLARS"
    )


def test_a_tick_the_contract_forbids_is_reported():
    contract = _numbered_contract().model_copy(
        update={
            "eyebrow": HeaderEyebrowSpec(tick=HeaderTickSpec(enabled=False)),
        }
    )
    assert "header_tick_unexpected" in validate_header_contract(
        _CONFORMING, contract, role="PILLARS"
    )


def test_a_badge_on_a_deck_without_identifiers_is_reported():
    violations = validate_header_contract(
        _CONFORMING,
        default_header_contract(),
        role="PILLARS",
    )
    assert "header_requirements_unexpected" in violations


def test_a_second_requirement_group_is_reported():
    contract = _numbered_contract()
    html = _CONFORMING.replace(
        "</div></div>",
        '</div><div data-header-part="requirements"><span>SFR-012</span></div></div>',
    )
    assert "header_requirements_duplicated" in validate_header_contract(
        html, contract, role="PILLARS"
    )


def test_a_plus_n_overflow_chip_is_not_an_invention():
    contract = _numbered_contract()
    contract = contract.model_copy(
        update={"requirements": contract.requirements.model_copy(
            update={"max_visible": 1, "overflow": "plusN"}
        )}
    )
    html = _CONFORMING.replace("<span>POR-005</span>", "<span>POR-005</span><span>+2</span>")
    assert (
        validate_header_contract(
            html, contract, role="PILLARS"
        )
        == ()
    )


def test_excluded_roles_skip_the_band_but_still_may_not_invent_badges():
    contract = _numbered_contract()
    assert validate_header_contract(
        _slide_html("<h1>표지</h1>"), contract, role="COVER"
    ) == ()
    assert "header_requirements_unexpected" in validate_header_contract(
        _CONFORMING, contract, role="COVER"
    )


def test_a_subline_the_contract_forbids_is_reported():
    contract = _numbered_contract()
    html = _CONFORMING.replace(
        '<div data-header-part="divider"></div>',
        '<p data-header-part="subline">부제</p><div data-header-part="divider"></div>',
    )
    assert "header_subline_unexpected" in validate_header_contract(
        html, contract, role="PILLARS"
    )


def test_a_slide_with_nothing_for_the_right_slot_may_leave_it_out():
    """Reserving the slot deck-wide does not oblige every slide to fill it.

    The brief says so in as many words, and requiring the container anyway is
    the subline defect one field over: the model obeys, is re-authored for
    obeying, and the only way through is filler on every slide.
    """
    tick = HeaderEyebrowSpec(tick=HeaderTickSpec(enabled=True))
    for contract in (
        AuthoredHeaderContract(
            eyebrow=tick, right_slot=HeaderRightSlotSpec(mode="summary")
        ),
        _numbered_contract(),
        AuthoredHeaderContract(
            eyebrow=tick,
            requirements=HeaderRequirementSpec(enabled=True),
            right_slot=HeaderRightSlotSpec(mode="requirementsAndSummary"),
        ),
    ):
        html = _conforming_slide(contract).replace(
            _right_slot_html(contract), ""
        )
        assert 'data-header-part="right"' not in html
        assert validate_header_contract(html, contract, role="PILLARS") == ()


def test_a_right_slot_on_a_deck_that_has_none_is_reported():
    contract = default_header_contract()
    html = _conforming_slide(contract).replace(
        '<div data-header-part="divider"></div>',
        '<div data-header-part="right"><p>요약</p></div>'
        '<div data-header-part="divider"></div>',
    )
    assert "header_right_unexpected" in validate_header_contract(
        html, contract, role="PILLARS"
    )


def test_a_void_element_inside_a_chip_does_not_swallow_the_rest_of_the_header():
    """An unclosed <img> once left the requirement scope open to the end of the slide.

    The markers after it were then read as chip text rather than recorded, so a
    letter-perfect slide was reported for a missing divider and a missing body —
    a violation nothing in the brief forbids the model from causing.
    """
    contract = _numbered_contract()
    html = _CONFORMING.replace(
        "<span>POR-005</span>",
        '<span><img src="status.svg">POR-005</span>'
        '<span><br>SER-001</span>',
    )
    assert validate_header_contract(html, contract, role="PILLARS") == ()


def test_a_band_padded_outside_its_own_height_is_reported():
    """headerTopPx is exactly the number a model spells as padding-top.

    On a content-box band that padding renders below the height it declared, so
    the divider moves down while every number stays in agreement and the body is
    drawn over the last title line.
    """
    contract = _numbered_contract()
    band = _band_px(contract)
    padded = _conforming_slide(
        contract, band_style=f"padding-top:32px;height:{band}px"
    )
    assert validate_header_contract(padded, contract, role="PILLARS") == (
        "header_band_box_mismatch",
    )
    contained = _conforming_slide(
        contract,
        band_style=f"box-sizing:border-box;padding-top:32px;height:{band}px",
    )
    assert validate_header_contract(contained, contract, role="PILLARS") == ()


def test_a_band_taken_out_of_flow_needs_a_body_taken_out_of_flow_too():
    """A pinned band leaves no flow space for the body's margin to follow.

    The body then starts 20px down the canvas and the whole header renders
    underneath the slide's content, with the declaration, the height and the
    offset all agreeing with each other.
    """
    contract = _numbered_contract()
    band = _band_px(contract)
    flowing = _conforming_slide(
        contract, band_style=f"position:absolute;top:0;height:{band}px"
    )
    assert validate_header_contract(flowing, contract, role="PILLARS") == (
        "header_body_outside_band_flow",
    )
    pinned = _conforming_slide(
        contract,
        band_style=f"position:absolute;top:0;height:{band}px",
        body_style=f"position:absolute;top:{band + contract.content_gap_px}px",
    )
    assert validate_header_contract(pinned, contract, role="PILLARS") == ()


def test_a_body_whose_position_lives_in_a_class_is_still_read_as_pinned():
    """The deck's own idiom is a <style> block plus the offset inline.

    A ``top`` on an element in normal flow does nothing at all, so reading one
    as a flow margin reports a slide whose geometry is exactly right.
    """
    contract = _numbered_contract()
    band = _band_px(contract)
    html = _conforming_slide(
        contract, body_style=f"top:{band + contract.content_gap_px}px"
    )
    assert validate_header_contract(html, contract, role="PILLARS") == ()


def _subline_contract() -> AuthoredHeaderContract:
    return _numbered_contract().model_copy(
        update={"subline": HeaderSublineSpec(enabled=True)}
    )


def test_a_slide_that_omits_an_enabled_subline_is_conforming():
    """Enabling the subline reserves the row; it does not oblige a slide to fill it.

    Reporting the omission is what put a subline on 99 slides out of 99: the
    brief told the model to leave it out when the slide had nothing to add, and
    the slide was then re-authored for obeying.
    """
    contract = _subline_contract()
    assert (
        validate_header_contract(
            _conforming_slide(contract), contract, role="PILLARS"
        )
        == ()
    )


def test_a_rendered_subline_is_conforming_and_costs_its_own_rows():
    contract = _subline_contract()
    html = _conforming_slide(contract, subline_lines=1)

    assert validate_header_contract(html, contract, role="PILLARS") == ()
    # 8px gap + one 21px row, carried only by the slides that render one.
    assert _band_px(contract, subline_lines=1) - _band_px(contract) == 29


def test_a_second_subline_is_reported():
    contract = _subline_contract()
    html = _conforming_slide(contract, subline_lines=1).replace(
        '<div data-header-part="divider"></div>',
        '<p data-header-part="subline">또 다른 부제</p>'
        '<div data-header-part="divider"></div>',
    )
    assert "header_subline_duplicated" in validate_header_contract(
        html, contract, role="PILLARS"
    )


def test_header_markers_pass_renderer_admission():
    admitted = admit_authored_html(_CONFORMING)
    assert 'data-header-part="band"' in admitted.html
    # The renderer's attribute allowlist is outside this feature, and a slide
    # whose declaration it strips fails validation for a reason no header rule
    # explains — so the declaration surviving admission is checked here.
    assert 'data-header-band="135"' in admitted.html
    assert 'data-header-part="body"' in admitted.html
    assert 'data-requirement-ids="POR-005"' in admitted.html


# --- the band every slide derives for itself ----------------------------------


def _summary_contract() -> AuthoredHeaderContract:
    """A deck whose right slot stacks a chip row over a three-line note."""
    return AuthoredHeaderContract(
        eyebrow=HeaderEyebrowSpec(tick=HeaderTickSpec(enabled=True)),
        requirements=HeaderRequirementSpec(enabled=True),
        right_slot=HeaderRightSlotSpec(
            mode="requirementsAndSummary", summary_max_lines=3
        ),
    )


def test_the_brief_states_the_band_derivation():
    """The band is arithmetic the model performs, so the brief must show every term.

    These are also the numbers the slides in this file are built on: a term that
    silently changed here would leave the tests agreeing with themselves and with
    nothing the model was ever told.
    """
    text = render_header_contract(_numbered_contract())

    assert (
        "Left block = the eyebrow row (18px) + 12px + (number of title lines "
        "this slide actually renders x 45px)." in text
    )
    assert (
        "Right block = the requirement chip row (rendered chip rows x 22px, "
        "plus 6px between rows)." in text
    )
    assert "Band height = 32px + max(left block, right block) + 28px" in text
    assert "clamped to 96-280px" in text
    assert "SHORTEN THE TITLE" in text


def test_the_brief_makes_the_slide_declare_its_band_and_mark_its_body():
    text = render_header_contract(_numbered_contract())

    assert 'data-header-band="N"' in text
    assert 'data-header-part="body"' in text
    assert "margin-top:20px" in text


def test_the_brief_asks_for_one_line_whenever_the_title_fits():
    """The line budget reads as a target unless the brief says it is a ceiling.

    Observed live on a 100-slide deck: 54 of 99 headers carried a hand-written
    line break, and 52 of those titles measured under half the title column.
    Nothing told the model that the band it derives is charged to the slide's
    own content, so the second line looked free.
    """
    text = render_header_contract(_numbered_contract())

    assert "The line budget is a ceiling, never a target." in text
    assert "is written on ONE line" in text
    assert "never insert a manual line break" in text


def test_a_single_line_budget_leaves_the_preference_unsaid():
    """One allowed line already forbids the second; repeating it is noise."""
    contract = _numbered_contract().model_copy(
        update={"title": HeaderTitleSpec(max_lines=1)}
    )
    text = render_header_contract(contract)

    assert "at most 1 line(s)" in text
    assert "The line budget is a ceiling" not in text


def test_a_one_line_title_reclaims_a_whole_title_line_from_the_band():
    contract = _numbered_contract()
    one_line = _band_px(contract, title_lines=1)
    two_lines = _band_px(contract, title_lines=2)

    assert (one_line, two_lines) == (135, 180)
    # The 45px a fixed band charged to every one-line slide, on 51% of a real deck.
    assert two_lines - one_line == 45
    for html in (
        _conforming_slide(contract, title_lines=1),
        _conforming_slide(contract, title_lines=2),
    ):
        assert validate_header_contract(html, contract, role="PILLARS") == ()


def test_a_right_slot_taller_than_the_left_block_sizes_the_band():
    contract = _summary_contract()
    # Chip row + the gap the stack reuses + three note rows.
    right_block = 22 + contract.subline_gap_px + 3 * 18
    assert right_block > _left_block_px(contract, title_lines=1)

    band = _band_px(contract, title_lines=1, right_block_px=right_block)
    assert band == contract.header_top_px + right_block + contract.title_gap_px == 144
    assert (
        "+ 8px + (rendered note lines x 18px)" in render_header_contract(contract)
    )
    assert (
        validate_header_contract(
            _conforming_slide(contract, title_lines=1, right_block_px=right_block),
            contract,
            role="PILLARS",
        )
        == ()
    )


def test_the_floor_keeps_a_short_slide_from_collapsing_the_deck_chrome():
    contract = _numbered_contract().model_copy(update={"band_min_px": 160})
    assert _band_px(contract, title_lines=1) == 160

    assert (
        validate_header_contract(
            _conforming_slide(contract), contract, role="PILLARS"
        )
        == ()
    )
    # The band the derivation alone would have produced now sits under the floor.
    assert validate_header_contract(
        _conforming_slide(contract, declared=135, band_style="height:135px"),
        contract,
        role="PILLARS",
    ) == ("header_band_out_of_bounds",)


# --- what a slide may get wrong about its own band ----------------------------


def test_a_band_that_declares_no_height_is_reported():
    html = _CONFORMING.replace(' data-header-band="135"', "")
    assert validate_header_contract(
        html, _numbered_contract(), role="PILLARS"
    ) == ("header_band_not_declared",)


def test_a_band_declaration_that_is_not_a_whole_number_is_reported():
    contract = _numbered_contract()
    # A unit in the attribute is the mistake to expect, since the same number is
    # written with one three characters away.
    html = _conforming_slide(contract).replace(
        'data-header-band="135"', 'data-header-band="135px"'
    )
    assert validate_header_contract(html, contract, role="PILLARS") == (
        "header_band_declaration_invalid",
    )


def test_a_band_above_the_contract_ceiling_is_reported():
    contract = _numbered_contract()
    html = _conforming_slide(contract, declared=320, band_style="height:320px")
    assert validate_header_contract(html, contract, role="PILLARS") == (
        "header_band_out_of_bounds",
    )


def test_a_band_whose_css_contradicts_its_declaration_is_reported():
    contract = _numbered_contract()
    html = _conforming_slide(contract, band_style="height:200px")
    assert validate_header_contract(html, contract, role="PILLARS") == (
        "header_band_height_mismatch",
    )


def test_a_band_height_that_cannot_be_read_is_reported():
    contract = _numbered_contract()
    # A height in a class, or in a unit that needs rendering to resolve, leaves
    # the declaration unchecked — which is a violation, never a pass.
    for style in ("min-height:135px", "height:8.4em", "height:calc(100% - 20px)"):
        html = _conforming_slide(contract, band_style=style)
        assert validate_header_contract(html, contract, role="PILLARS") == (
            "header_band_height_unreadable",
        )


def test_a_body_that_does_not_start_one_content_gap_below_the_band_is_reported():
    contract = _numbered_contract()
    html = _conforming_slide(contract, body_style="margin-top:48px")
    assert validate_header_contract(html, contract, role="PILLARS") == (
        "header_body_top_mismatch",
    )


def test_a_pinned_body_is_measured_against_the_declared_band():
    contract = _numbered_contract()
    pinned = _conforming_slide(
        contract, body_style="position:absolute;top:155px;left:64px"
    )
    assert validate_header_contract(pinned, contract, role="PILLARS") == ()

    # 135 is the band itself: a body pinned there sits on the divider.
    crowding = _conforming_slide(
        contract, body_style="position:absolute;top:135px;left:64px"
    )
    assert validate_header_contract(crowding, contract, role="PILLARS") == (
        "header_body_top_mismatch",
    )


def test_a_body_offset_that_cannot_be_read_is_reported():
    contract = _numbered_contract()
    html = _conforming_slide(contract, body_style="margin-top:1.5em")
    assert validate_header_contract(html, contract, role="PILLARS") == (
        "header_body_top_unreadable",
    )


def test_a_slide_that_marks_no_body_root_is_reported():
    contract = _numbered_contract()
    html = _conforming_slide(contract).replace(
        '<div data-header-part="body" style="margin-top:20px">본문</div>',
        "<div>본문</div>",
    )
    assert validate_header_contract(html, contract, role="PILLARS") == (
        "header_body_missing",
    )



# --- identifiers must come from the slide's own text --------------------------


_NO_IDS_CONTENT = "폐쇄망 구성과 보안 통제. 전송구간은 SHA-256 해시로 검증합니다."
_WITH_IDS_CONTENT = "고유번호 PROP-011 | 연계: SER-001. 폐쇄망 구성과 보안 통제."


def test_an_identifier_absent_from_the_slide_text_is_reported():
    """Every slide showed the same PROP-001, lifted from the deck topic."""
    violations = validate_header_contract(
        _CONFORMING,
        _numbered_contract(),
        role="PILLARS",
        content=_NO_IDS_CONTENT,
    )
    assert "header_requirement_id_not_in_content" in violations


def test_an_identifier_written_in_the_slide_text_is_accepted():
    html = _CONFORMING.replace("POR-005", "PROP-011")
    assert (
        validate_header_contract(
            html,
            _numbered_contract(),
            role="PILLARS",
            content=_WITH_IDS_CONTENT,
        )
        == ()
    )


def test_a_slide_without_identifiers_may_leave_the_slot_empty():
    html = _conforming_slide(_numbered_contract()).replace(
        '<div data-header-part="requirements" data-requirement-ids="POR-005">'
        "<span>POR-005</span></div>",
        "",
    )
    assert '<div data-header-part="right"></div>' in html
    assert (
        validate_header_contract(
            html, _numbered_contract(), role="PILLARS", content=_NO_IDS_CONTENT
        )
        == ()
    )


def test_a_group_that_declares_nothing_cannot_be_checked_and_is_reported():
    html = _CONFORMING.replace(' data-requirement-ids="POR-005"', "")
    assert "header_requirement_ids_attribute_missing" in validate_header_contract(
        html, _numbered_contract(), role="PILLARS", content=_WITH_IDS_CONTENT
    )


def test_grounding_is_skipped_when_no_slide_text_is_supplied():
    # Callers that cannot supply the source text still get the placement checks.
    assert (
        validate_header_contract(_CONFORMING, _numbered_contract(), role="PILLARS")
        == ()
    )


def test_the_marker_forbids_borrowing_an_identifier_from_elsewhere():
    marker = requirement_marker(requirements_enabled=True)
    assert "ONLY place you may take an identifier from" in marker
    assert "Not the deck topic" in marker
