"""Deck-scoped authored chapter plan: what a chaptered deck decides once.

Two things come out of the plan and they have to describe one deck: the outline
the slides are authored from, and the indices the plan reports. Most of what is
checked here is that pairing — a divider page and the plan entry that claims it
must land on the same slide, and a page the plan did not insert must keep the
role its own text derives.
"""


from models.authored_header_contract import (
    BREADCRUMB_MIN_EYEBROW_CHARS,
    AuthoredHeaderContract,
    HeaderEyebrowSpec,
    default_header_contract,
)
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from utils.llm_calls.author_header_contract import coerce_header_contract


def _outline(count: int) -> PresentationOutlineModel:
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"## 슬라이드 {i}") for i in range(count)]
    )


# --- the outline and the plan describe one deck --------------------------------


# --- what the plan reports per slide -------------------------------------------


# --- roles the plan forces ------------------------------------------------------


# --- the header contract's side of the breadcrumb -------------------------------


def test_a_breadcrumb_deck_never_carries_a_section_only_eyebrow_budget():
    contract = AuthoredHeaderContract(
        eyebrow=HeaderEyebrowSpec(form="chapterBreadcrumb", max_chars=18)
    )
    assert contract.eyebrow.max_chars == BREADCRUMB_MIN_EYEBROW_CHARS


def test_the_eyebrow_form_is_forced_rather_than_taken_from_the_model():
    payload = {"eyebrow": {"form": "chapterBreadcrumb"}}
    assert coerce_header_contract(payload).eyebrow.form == "sectionOnly"
    assert (
        coerce_header_contract(
            {"eyebrow": {"form": "sectionOnly"}}, eyebrow_form="chapterBreadcrumb"
        ).eyebrow.form
        == "chapterBreadcrumb"
    )


def test_the_fallback_contract_honours_the_form_the_deck_already_decided():
    assert default_header_contract().eyebrow.form == "sectionOnly"
    assert (
        default_header_contract(chapter_breadcrumb=True).eyebrow.form
        == "chapterBreadcrumb"
    )


# --- what one slide is handed ---------------------------------------------------


# --- reference images for the two new roles -------------------------------------


class _Style:
    def __init__(self, **images):
        self.reference_images = images


# --- coercion drops what the deck cannot carry ----------------------------------


# --- when the deck is left alone ------------------------------------------------
