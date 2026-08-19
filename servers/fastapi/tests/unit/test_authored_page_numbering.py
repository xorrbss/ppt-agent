"""Page numbering is opt-in: a deck is unnumbered unless its request asked."""

import pytest

from models.authored_header_contract import (
    AuthoredHeaderContract,
    DeckPageNumberSpec,
    default_header_contract,
)
from services.authored_header_contract_service import page_number_mode_for
from utils.authored_header_style_map import structural_changes
from utils.llm_calls.author_header_contract import (
    build_header_contract_messages,
    coerce_header_contract,
)
from utils.llm_calls.author_slide import (
    COMPANY_STANDARD_PAGE_NUMBER_MODE,
    NO_PAGE_NUMBER_MODE,
    TEMPLATE_PAGE_NUMBER_MODE,
    Brand,
    build_design_system,
    fallback_slide_html,
    validate_page_number_mode,
)
from utils.llm_calls.author_slide_prompt import author_prompt


def _brand() -> Brand:
    return Brand(topic="차세대 VOC 통합", language="Korean", primary="#0B4EA2")


# --- the contract default ----------------------------------------------------


def test_a_deck_is_unnumbered_by_default():
    assert default_header_contract().page_number.enabled is False
    assert AuthoredHeaderContract().page_number.enabled is False


def test_mode_follows_the_contract():
    numbered = AuthoredHeaderContract(page_number=DeckPageNumberSpec(enabled=True))

    assert page_number_mode_for(numbered) == TEMPLATE_PAGE_NUMBER_MODE
    assert page_number_mode_for(AuthoredHeaderContract()) == NO_PAGE_NUMBER_MODE
    # A deck with no contract at all predates numbering being a decision, and is
    # treated as unnumbered rather than inheriting the old always-on default.
    assert page_number_mode_for(None) == NO_PAGE_NUMBER_MODE


# --- the hard floor over the model -------------------------------------------


def test_numbering_is_forced_off_when_nothing_could_have_asked_for_it():
    contract = coerce_header_contract(
        {"page_number": {"enabled": True}},
    )

    assert contract.page_number.enabled is False


def test_numbering_is_accepted_when_the_request_could_have_asked_for_it():
    contract = coerce_header_contract(
        {"pageNumber": {"enabled": True}},
        allow_page_numbers=True,
    )

    assert contract.page_number.enabled is True


def test_the_model_is_not_offered_numbering_without_a_request():
    without = build_header_contract_messages(
        topic="VOC",
        language="Korean",
        primary="#0B4EA2",
        fonts="Noto Sans KR",
        style_brief=None,
        instructions=None,
    )
    with_request = build_header_contract_messages(
        topic="VOC",
        language="Korean",
        primary="#0B4EA2",
        fonts="Noto Sans KR",
        style_brief=None,
        instructions="페이지 번호를 넣어주세요",
        allow_page_numbers=True,
    )

    assert "This deck is NOT numbered" in without[1].content
    assert "Set pageNumber.enabled=true ONLY if" in with_request[1].content


# --- what the slide prompt says ----------------------------------------------


def test_the_unnumbered_design_system_forbids_footer_copy_and_targets_32px():
    design_system = build_design_system(
        _brand(), page_number_mode=NO_PAGE_NUMBER_MODE
    )

    assert "NO PAGE NUMBER" in design_system
    assert "y=688px" in design_system
    # The boundary is stated as a hard limit. Told to reserve "at least" 72px the
    # model padded to a 92px median band; told to fill down to the line it clipped
    # panels on 3 of 8 slides. Overflow has to read as the worse failure.
    assert "NOTHING may cross that line" in design_system
    assert "bottom:14px" not in design_system
    assert "bottom 72px" not in design_system
    assert "#A7ACB3" not in design_system


def test_the_unnumbered_slide_prompt_omits_the_marker():
    prompt = author_prompt(
        "content",
        "DESIGN",
        _brand(),
        "PROBLEM",
        1,
        4,
        page_number_mode=NO_PAGE_NUMBER_MODE,
    )

    assert "PAGE MARKER FOR THIS SLIDE: OMIT IT" in prompt
    assert "This deck is not numbered" in prompt
    assert 'render exactly "01" once' not in prompt


def test_the_unnumbered_fallback_slide_has_no_footer():
    html = fallback_slide_html(
        "content", _brand(), "PROBLEM", 1, 4, page_number_mode=NO_PAGE_NUMBER_MODE
    )

    assert 'class="foot"' not in html
    assert "padding:64px 64px 32px" in html


def test_the_numbered_fallback_slide_keeps_its_72px_footer_zone():
    html = fallback_slide_html(
        "content", _brand(), "PROBLEM", 1, 4, page_number_mode=TEMPLATE_PAGE_NUMBER_MODE
    )

    assert '<div class="foot">01</div>' in html
    assert "padding:64px 64px 72px" in html


def test_the_mode_vocabulary_stays_closed():
    for mode in (
        TEMPLATE_PAGE_NUMBER_MODE,
        COMPANY_STANDARD_PAGE_NUMBER_MODE,
        NO_PAGE_NUMBER_MODE,
    ):
        assert validate_page_number_mode(mode) == mode
    with pytest.raises(ValueError, match="unsupported page number mode"):
        validate_page_number_mode("footer")


# --- changing it after the fact ----------------------------------------------


def test_toggling_numbering_is_reported_as_a_structural_change():
    off = AuthoredHeaderContract()
    on = AuthoredHeaderContract(page_number=DeckPageNumberSpec(enabled=True))

    # The patch engine can restyle existing elements but cannot add or remove
    # one, so the chat layer must tell the user re-authoring is required.
    assert "pageNumber.enabled" in structural_changes(off, on)
    assert "pageNumber.enabled" in structural_changes(on, off)
    assert structural_changes(off, off) == ()
