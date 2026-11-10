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
from utils.authored_header_prompt import render_header_contract
from utils.llm_calls.author_header_contract import coerce_header_contract
from services.generation_core.authoring.author_slide_prompt import (
    author_prompt,
    chapter_marker,
)
from services.generation_core.authoring.author_slide import Brand


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


def test_the_deck_brief_states_the_breadcrumb_rule_only_for_a_breadcrumb_deck():
    plain = render_header_contract(default_header_contract())
    assert "prefix supplied" not in plain
    breadcrumb = render_header_contract(
        default_header_contract(chapter_breadcrumb=True)
    )
    assert "prefix supplied" in breadcrumb
    assert "Copy the supplied prefix" in breadcrumb


# --- what one slide is handed ---------------------------------------------------


def test_a_slide_is_handed_its_prefix_verbatim():
    marker = chapter_marker(chapter_breadcrumb=True, chapter_prefix="Ⅱ. 제안개요 > ")
    assert '"Ⅱ. 제안개요 > "' in marker
    assert "Never renumber it" in marker


def test_a_front_matter_slide_is_told_it_has_no_prefix():
    marker = chapter_marker(chapter_breadcrumb=True, chapter_prefix=None)
    assert "none" in marker
    assert "no chapter number" in marker


def test_an_unchaptered_deck_reads_the_prompt_it_always_read():
    assert chapter_marker(chapter_breadcrumb=False, chapter_prefix="Ⅱ. 개요 > ") == ""
    brand = Brand(topic="주제", language="Korean")
    plain = author_prompt("CONTENT", "DESIGN", brand, "PILLARS", 2, 9)
    assert "HEADER EYEBROW PREFIX" not in plain
    chaptered = author_prompt(
        "CONTENT",
        "DESIGN",
        brand,
        "PILLARS",
        2,
        9,
        chapter_breadcrumb=True,
        chapter_prefix="Ⅱ. 제안개요 > ",
    )
    assert "Ⅱ. 제안개요 > " in chaptered


# --- reference images for the two new roles -------------------------------------


class _Style:
    def __init__(self, **images):
        self.reference_images = images


# --- coercion drops what the deck cannot carry ----------------------------------


# --- when the deck is left alone ------------------------------------------------
