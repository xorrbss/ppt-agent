"""Page numbering is opt-in: a deck is unnumbered unless its request asked."""


from models.authored_header_contract import (
    AuthoredHeaderContract,
    DeckPageNumberSpec,
    default_header_contract,
)
from utils.authored_header_style_map import structural_changes
from utils.llm_calls.author_header_contract import (
    build_header_contract_messages,
    coerce_header_contract,
)


# --- the contract default ----------------------------------------------------


def test_a_deck_is_unnumbered_by_default():
    assert default_header_contract().page_number.enabled is False
    assert AuthoredHeaderContract().page_number.enabled is False


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


# --- changing it after the fact ----------------------------------------------


def test_toggling_numbering_is_reported_as_a_structural_change():
    off = AuthoredHeaderContract()
    on = AuthoredHeaderContract(page_number=DeckPageNumberSpec(enabled=True))

    # The patch engine can restyle existing elements but cannot add or remove
    # one, so the chat layer must tell the user re-authoring is required.
    assert "pageNumber.enabled" in structural_changes(off, on)
    assert "pageNumber.enabled" in structural_changes(on, off)
    assert structural_changes(off, off) == ()


